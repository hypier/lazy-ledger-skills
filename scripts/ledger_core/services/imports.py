"""Source-preserving statement plans. Parsing never writes financial records."""
import csv
import hashlib
import io
import re
from datetime import datetime
from pathlib import Path
from ..domain.money import decimal_amount, money_sum
from ..storage import repository
from ..storage.documents import atomic_write_json
from .profile import runtime_dir
from . import proposals

ALIASES = {
    "date": ("occurred_at", "交易时间", "交易创建时间", "付款时间", "交易日期"),
    "amount": ("amount", "金额(元)", "金额（元）", "金额", "交易金额"),
    "direction": ("type", "收/支", "收支", "资金流向"),
    "merchant": ("merchant", "交易对方", "对方户名", "商户"),
    "note": ("note", "商品", "商品名称", "商品说明", "摘要", "备注"),
    "status": ("当前状态", "交易状态", "status"),
    "id": ("交易单号", "交易订单号", "交易号", "流水号", "source_transaction_id"),
    "method": ("支付方式", "收/付款方式", "付款方式", "method"),
}


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def cell(row, name):
    return next((str(row[k]).strip().strip("\t") for k in ALIASES[name] if row.get(k) not in (None, "")), "")


def source_key(source):
    if source.get("provider") and source.get("transaction_id"):
        return (source["provider"], source["transaction_id"])
    return (source.get("file_hash"), source.get("row"))


def parse_file(filename, *, provider=None, currency="CNY"):
    raw = Path(filename).read_bytes()
    if len(raw) > 20 * 1024 * 1024:
        raise ValueError("Statement exceeds 20 MiB; split at source boundaries")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Unsupported statement encoding")
    lines = text.splitlines()
    start, delimiter = None, None
    for i, line in enumerate(lines[:100]):
        for sep in ("\t", ","):
            fields = [f.strip() for f in next(csv.reader([line], delimiter=sep))]
            if any(k in fields for k in ALIASES["amount"]) and any(k in fields for k in ALIASES["date"]):
                start, delimiter = i, sep
                break
        if start is not None:
            break
    if start is None:
        raise ValueError("Unsupported statement header; provide normalized TSV or a verified converter")
    inferred = "wechat" if "微信" in "\n".join(lines[:start + 1]) or "交易单号" in lines[start] else "alipay" if "支付宝" in text[:2000] or "交易号" in lines[start] or "交易订单号" in lines[start] else "normalized"
    provider = provider or inferred
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])), delimiter=delimiter)
    reader.fieldnames = [x.strip() for x in reader.fieldnames]
    rows, errors = [], []
    file_hash = digest_bytes(raw)
    for row in reader:
        line_number = start + reader.line_num
        if not any(str(v or "").strip() for v in row.values()):
            continue
        evidence = {"file_hash": file_hash, "row": line_number, "provider": provider,
                    "transaction_id": cell(row, "id") or None, "raw": row}
        try:
            if None in row:
                raise ValueError("Unexpected extra columns")
            date_text = cell(row, "date").replace("/", "-")
            occurred = datetime.fromisoformat(date_text).astimezone().isoformat(timespec="seconds")
            amount = decimal_amount(re.sub(r"[¥￥,\s]", "", cell(row, "amount")))
            if amount <= 0:
                raise ValueError("Amount must be positive; use direction for sign")
            direction = cell(row, "direction")
            status = cell(row, "status")
            kind = {"支出": "expense", "收入": "income", "退款": "refund", "expense": "expense", "income": "income", "refund": "refund", "transfer": "transfer"}.get(direction)
            refund_original = kind == "expense" and "退款" in status
            if "退款成功" in status and kind != "expense":
                kind = "refund"
            excluded = any(word in status for word in ("交易关闭", "已关闭", "交易失败", "已撤销", "交易取消", "已取消"))
            successes = {"支付成功", "交易成功", "退款成功", "对方已收钱", "已存入零钱", "转账成功", "收款成功", "已转账", "已收款", "还款成功", "提现已到账", "成功", "success", "completed"}
            pending = not excluded and (kind is None or refund_original or bool(status and status not in successes))
            candidate = {"type": kind or "expense", "amount": float(amount), "currency": row.get("currency") or currency,
                         "occurred_at": occurred, "merchant": cell(row, "merchant"), "note": cell(row, "note"),
                         "category": row.get("category") or ("收入" if kind == "income" else "其他"),
                         "source": "import", "sources": [evidence],
                         "field_evidence": {key: {"kind": "source", **evidence, "original": cell(row, name)}
                                            for key, name in (("amount", "amount"), ("occurred_at", "date"), ("type", "direction"), ("merchant", "merchant"))}}
            if cell(row, "method"):
                candidate["method"] = cell(row, "method")
            if provider in {"wechat", "alipay"}:
                candidate["payment_channel"] = provider
            if row.get("account"):
                candidate["account"] = row["account"]
            if row.get("to_account"):
                candidate["to_account"] = row["to_account"]
            for key in row:
                if key.startswith("x_") and row[key]:
                    candidate[key] = row[key]
            rows.append({"line": line_number, "candidate": candidate, "state": "excluded" if excluded else "review" if pending else "ready", "status": status})
        except (ValueError, TypeError, KeyError) as exc:
            errors.append({"line": line_number, "error": str(exc), "source": evidence})
    header = "\n".join(lines[:start])
    declared_count = re.search(r"共\s*(\d+)\s*笔", header)
    declared_totals = {}
    for label, kind in (("支出", "expense"), ("收入", "income"), ("退款", "refund")):
        match = re.search(label + r"[：:]\s*\d+\s*笔\s*[，,]?\s*([\d,.]+)\s*元", header)
        if match:
            declared_totals[kind] = float(decimal_amount(match.group(1).replace(",", "")))
    return {"version": 1, "file_hash": file_hash, "filename": Path(filename).name, "provider": provider,
            "rows": rows, "errors": errors, "declared_count": int(declared_count.group(1)) if declared_count else None,
            "declared_totals": declared_totals}


def prepare(path, filename, *, provider=None, account=None, expected_count=None, expected_total=None, links=None, distinct=None):
    ledger = repository.load(path, create=False)
    source = parse_file(filename, provider=provider, currency=ledger["currency"])
    links, distinct = links or {}, set(distinct or [])
    seen = {}
    for row in ledger["transactions"]:
        for evidence in row.get("sources", []):
            seen[source_key(evidence)] = row
        for field, family in (("wechat_transaction_id", "wechat"), ("bank_statement_ref", "bank"), ("credit_statement_ref", "credit")):
            if row.get(field):
                seen[(family, row[field])] = row
    operations, dispositions, review = [], [], []
    updates = {}
    for item in source["rows"]:
        row, line = item["candidate"], item["line"]
        if item["state"] != "ready":
            dispositions.append({"line": line, "state": item["state"], "status": item["status"]})
            if item["state"] == "review":
                review.append({"line": line, "reason": "Unsupported or unfinished source event"})
            continue
        if account:
            row["account"] = account
            row["field_evidence"]["account"] = {"kind": "user", "value": account}
        evidence = row["sources"][0]
        existing = seen.get(source_key(evidence))
        linked = links.get(str(line))
        if linked:
            existing = next((t for t in ledger["transactions"] if t["id"] == linked), None)
            if not existing:
                raise ValueError("Explicit source link points to a missing transaction")
        if existing:
            same = all(existing.get(k, ledger["currency"] if k == "currency" else None) == row.get(k) for k in ("amount", "currency", "type"))
            if not same:
                review.append({"line": line, "reason": "Source identity conflicts with stored financial values", "id": existing["id"]})
                continue
            if linked and evidence not in existing.get("sources", []):
                update = updates.setdefault(existing["id"], {**existing, "sources": list(existing.get("sources", []))})
                update["sources"].append(evidence)
                dispositions.append({"line": line, "state": "linked", "id": existing["id"]})
            else:
                dispositions.append({"line": line, "state": "existing", "id": existing["id"]})
            continue
        candidates = []
        for tx in ledger["transactions"]:
            same_provider = any(s.get("provider") == evidence["provider"] and s.get("transaction_id") for s in tx.get("sources", []))
            if same_provider and evidence.get("transaction_id"):
                continue  # different authoritative source IDs may be genuine repeat purchases
            if tx.get("type") == row["type"] and tx.get("amount") == row["amount"] and tx.get("occurred_at", "")[:10] == row["occurred_at"][:10] and tx.get("merchant") == row["merchant"]:
                candidates.append(tx["id"])
        if candidates and str(line) not in distinct:
            review.append({"line": line, "reason": "Possible overlapping source; link or mark as a distinct purchase", "candidates": candidates})
            continue
        row = proposals.normalize_row(ledger, row)
        operations.append({"action": "add", "after": row})
        seen[source_key(evidence)] = row
        dispositions.append({"line": line, "state": "new", "id": row["id"]})
    operations.extend({"action": "update", "id": key, "after": row} for key, row in updates.items())
    count = len(source["rows"]) + len(source["errors"])
    total = money_sum(item["candidate"]["amount"] for item in source["rows"])
    mismatches = []
    expected_count = expected_count if expected_count is not None else source["declared_count"]
    if expected_count is not None and count != expected_count:
        mismatches.append({"kind": "count", "expected": expected_count, "actual": count})
    if expected_total is not None and decimal_amount(expected_total) != decimal_amount(total):
        mismatches.append({"kind": "total", "expected": expected_total, "actual": total})
    for kind, expected in source["declared_totals"].items():
        actual = money_sum(item["candidate"]["amount"] for item in source["rows"] if item["candidate"]["type"] == kind and item["state"] != "excluded")
        if decimal_amount(expected) != decimal_amount(actual):
            mismatches.append({"kind": kind, "expected": expected, "actual": actual})
    report = {"source": {k: v for k, v in source.items() if k != "rows"}, "count": count, "source_total": total,
              "dispositions": dispositions, "review": review, "mismatches": mismatches,
              "ready": not source["errors"] and not review and not mismatches}
    if report["ready"]:
        record_id = "import_" + source["file_hash"]
        if not any(r.get("id") == record_id for r in ledger.get("imports", [])):
            operations.append({"action": "add", "collection": "imports", "after": {"id": record_id,
                "file_hash": source["file_hash"], "provider": source["provider"], "count": count,
                "source_total": total, "coverage": {"min": min((r["candidate"]["occurred_at"] for r in source["rows"]), default=None), "max": max((r["candidate"]["occurred_at"] for r in source["rows"]), default=None)},
                "completeness": "source_totals_checked" if expected_count is not None or source["declared_totals"] or expected_total is not None else "rows_only"}})
        report["proposal"] = proposals.prepare(path, {"reason": "Import " + source["filename"], "operations": operations,
                                                       "evidence": {"file_hash": source["file_hash"], "provider": source["provider"]}},
                                                expected_context=proposals.context_token(ledger))
    target = runtime_dir(path) / "work" / "imports" / (source["file_hash"] + ".json")
    atomic_write_json(target, report)
    return report
