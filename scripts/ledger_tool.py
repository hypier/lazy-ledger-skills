#!/usr/bin/env python3
import argparse
import csv
import json
import re
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_TEMPLATE = SKILL_DIR / "assets" / "ledger-viewer-template.html"
VALID_TYPES = {"expense", "income", "refund", "transfer"}
VALID_SOURCES = {"text", "image", "voice", "manual", "import"}
REQUIRED_TX_FIELDS = {
    "id",
    "type",
    "amount",
    "currency",
    "category",
    "occurred_at",
    "source",
    "created_at",
    "updated_at",
}
CATEGORY_KEYWORDS = {
    "咖啡": ["星巴克", "瑞幸", "咖啡", "拿铁", "美式"],
    "餐饮": ["饭", "午饭", "晚饭", "早餐", "外卖", "火锅", "奶茶", "餐厅", "美团", "饿了么"],
    "交通": ["地铁", "公交", "打车", "滴滴", "高铁", "火车", "机票", "停车", "加油"],
    "购物": ["淘宝", "京东", "拼多多", "超市", "便利店", "衣服", "数码"],
    "居住": ["房租", "水电", "燃气", "物业", "宽带"],
    "娱乐": ["电影", "游戏", "演出", "KTV", "会员"],
    "医疗": ["医院", "药", "挂号", "体检"],
    "教育": ["课程", "书", "学费", "培训"],
    "人情": ["红包", "礼物", "请客"],
    "收入": ["工资", "奖金", "报销", "利息"],
}
KNOWN_MERCHANTS = ["星巴克", "瑞幸"]
TYPE_KEYWORDS = {
    "income": ["工资", "奖金", "收入", "利息", "报销到账"],
    "refund": ["退款", "退回", "返现"],
    "transfer": ["转账", "转入", "转出", "还信用卡"],
}
DATE_TOKEN_RE = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|今天|昨天|前天|上周[一二三四五六日天]")
AMOUNT_RE = re.compile(r"(?<![\d.])(?:¥|￥)?(\d+(?:\.\d+)?)(?:元|块|人民币)?(?![\d.])")


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_currency(currency=None):
    return currency or "CNY"


def parse_when(value):
    if not value:
        return now_iso()
    value = value.strip()
    today = datetime.now().astimezone()
    aliases = {
        "今天": today,
        "昨天": today - timedelta(days=1),
        "前天": today - timedelta(days=2),
    }
    if value in aliases:
        return aliases[value].replace(hour=12, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    weekday_aliases = {
        "一": 0,
        "二": 1,
        "三": 2,
        "四": 3,
        "五": 4,
        "六": 5,
        "日": 6,
        "天": 6,
    }
    if value.startswith("上周") and len(value) == 3 and value[-1] in weekday_aliases:
        current_week_start = today - timedelta(days=today.weekday())
        target = current_week_start - timedelta(days=7) + timedelta(days=weekday_aliases[value[-1]])
        return target.replace(hour=12, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone().isoformat(timespec="seconds")
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            parsed = datetime.strptime(value, fmt)
            if "%H" not in fmt:
                parsed = parsed.replace(hour=12)
            return parsed.astimezone().isoformat(timespec="seconds")
        except ValueError:
            pass
    raise SystemExit(f"Unsupported date format: {value}")


def empty_ledger(currency="CNY"):
    ts = now_iso()
    return {
        "schema_version": 1,
        "currency": default_currency(currency),
        "created_at": ts,
        "updated_at": ts,
        "transactions": [],
        "categories": [],
    }


def normalize_ledger(data, currency="CNY"):
    if not isinstance(data, dict):
        raise SystemExit("Ledger JSON root must be an object")
    data.setdefault("schema_version", 1)
    if not data.get("currency"):
        data["currency"] = default_currency(currency)
    data.setdefault("created_at", now_iso())
    data.setdefault("updated_at", now_iso())
    data.setdefault("transactions", [])
    data.setdefault("categories", [])
    if not isinstance(data["transactions"], list):
        raise SystemExit("Ledger transactions must be a list")
    if not isinstance(data["categories"], list):
        data["categories"] = []
    return data


def load_ledger(path, currency="CNY", create=True):
    ledger_path = Path(path)
    if not ledger_path.exists():
        if not create:
            raise SystemExit(f"Ledger not found: {ledger_path}")
        return empty_ledger(currency)
    with ledger_path.open("r", encoding="utf-8") as f:
        return normalize_ledger(json.load(f), currency)


def save_ledger(path, ledger):
    ledger_path = Path(path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger["updated_at"] = now_iso()
    tmp = ledger_path.with_suffix(ledger_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(ledger_path)


def make_id(occurred_at):
    stamp = occurred_at[:10].replace("-", "")
    return f"tx_{stamp}_{uuid.uuid4().hex[:8]}"


def coerce_tags(value):
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def load_optional_ledger(path, currency="CNY"):
    if not path:
        return empty_ledger(currency)
    ledger_path = Path(path)
    if not ledger_path.exists():
        return empty_ledger(currency)
    return load_ledger(ledger_path, currency=currency, create=False)


def validate_confidence(value):
    if value is None:
        return None
    if value < 0 or value > 1:
        raise SystemExit("Confidence must be between 0 and 1")
    return value


def infer_type(text):
    for tx_type, keywords in TYPE_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return tx_type
    return "expense"


def infer_category(text, tx_type="expense"):
    if tx_type == "income":
        return "收入"
    for category in CATEGORY_KEYWORDS:
        if category in text:
            return category
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return category
    return "其他"


def extract_date_token(text):
    match = DATE_TOKEN_RE.search(text)
    return match.group(0) if match else None


def extract_single_amount(text):
    matches = list(AMOUNT_RE.finditer(text))
    if not matches:
        return None, text
    values = [match.group(1) for match in matches]
    if len(values) > 1:
        raise SystemExit(f"Multiple amounts found: {', '.join(values)}")
    match = matches[0]
    cleaned = f"{text[:match.start()]} {text[match.end():]}"
    return round(float(match.group(1)), 2), cleaned


def infer_merchant(cleaned_text, category):
    for merchant in KNOWN_MERCHANTS:
        if merchant in cleaned_text:
            return merchant
    text = cleaned_text
    for category_name, keywords in CATEGORY_KEYWORDS.items():
        text = text.replace(category_name, " ")
        for keyword in keywords:
            text = text.replace(keyword, " " if keyword != "星巴克" else keyword)
    for keywords in TYPE_KEYWORDS.values():
        for keyword in keywords:
            text = text.replace(keyword, " ")
    text = re.sub(r"[，,。；;:：()（）\[\]【】]", " ", text)
    parts = [part.strip() for part in text.split() if part.strip()]
    parts = [part for part in parts if part != category]
    if not parts:
        return None
    return parts[0]


def parse_text_transaction(text, ledger=None, currency="CNY", source="text"):
    raw_text = text.strip()
    if not raw_text:
        raise SystemExit("Text is required")
    date_token = extract_date_token(raw_text)
    date_text_removed = raw_text.replace(date_token, " ", 1) if date_token else raw_text
    amount, amount_removed = extract_single_amount(date_text_removed)
    if amount is None:
        raise SystemExit("Amount is required")
    tx_type = infer_type(raw_text)
    category = infer_category(raw_text, tx_type)
    merchant = infer_merchant(amount_removed, category)
    proposal = {
        "type": tx_type,
        "amount": amount,
        "currency": default_currency(currency or (ledger or {}).get("currency")),
        "category": category,
        "occurred_at": parse_when(date_token),
        "source": source,
        "confidence": 1.0,
        "tags": [],
        "note": raw_text,
    }
    if merchant:
        proposal["merchant"] = merchant
    return proposal


def tx_day(tx):
    return (tx.get("occurred_at") or "")[:10]


def normalized_text(value):
    return re.sub(r"\s+", "", str(value or "")).lower()


def compact_candidate(tx):
    return {
        "id": tx.get("id"),
        "type": tx.get("type"),
        "amount": tx.get("amount"),
        "category": tx.get("category"),
        "merchant": tx.get("merchant"),
        "note": tx.get("note"),
        "occurred_at": tx.get("occurred_at"),
    }


def likely_duplicate_candidates(transactions, tx):
    candidates = []
    tx_amount = round(float(tx.get("amount", 0) or 0), 2)
    tx_merchant = normalized_text(tx.get("merchant"))
    tx_note = normalized_text(tx.get("note"))
    for existing in transactions:
        try:
            existing_amount = round(float(existing.get("amount", 0) or 0), 2)
        except (TypeError, ValueError):
            continue
        if existing_amount != tx_amount:
            continue
        if tx_day(existing) != tx_day(tx):
            continue
        existing_merchant = normalized_text(existing.get("merchant"))
        existing_note = normalized_text(existing.get("note"))
        same_merchant = tx_merchant and existing_merchant and tx_merchant == existing_merchant
        same_note = tx_note and existing_note and (tx_note == existing_note or tx_note in existing_note or existing_note in tx_note)
        same_attachment = tx.get("attachment") and tx.get("attachment") == existing.get("attachment")
        if same_merchant or same_note or same_attachment:
            candidates.append(compact_candidate(existing))
    return candidates


def merge_text_proposal(args, ledger):
    if not args.text:
        return {}
    currency = default_currency(args.currency or ledger.get("currency"))
    return parse_text_transaction(args.text, ledger=ledger, currency=currency, source=args.source)


def build_transaction_payload(
    ledger,
    *,
    amount_value,
    tx_type,
    currency,
    category,
    occurred_at,
    source,
    confidence,
    tags,
    merchant=None,
    note=None,
    attachment=None,
    tx_id=None,
):
    if amount_value is None:
        raise SystemExit("Amount is required unless --text includes one")
    amount = float(amount_value)
    if amount <= 0:
        raise SystemExit("Amount must be positive")
    if tx_type not in VALID_TYPES:
        raise SystemExit(f"Type must be one of: {', '.join(sorted(VALID_TYPES))}")
    validate_confidence(confidence)
    ts = now_iso()
    tx = {
        "id": tx_id or make_id(occurred_at),
        "type": tx_type,
        "amount": round(amount, 2),
        "currency": default_currency(currency or ledger.get("currency")),
        "category": category or ("收入" if tx_type == "income" else "其他"),
        "occurred_at": occurred_at,
        "source": source,
        "confidence": confidence,
        "tags": coerce_tags(tags),
        "created_at": ts,
        "updated_at": ts,
    }
    if merchant:
        tx["merchant"] = merchant
    if note:
        tx["note"] = note
    if attachment:
        tx["attachment"] = attachment
    return tx


def add_transaction(args):
    ledger = load_ledger(args.ledger, args.currency)
    parsed = merge_text_proposal(args, ledger)
    amount_value = args.amount if args.amount is not None else parsed.get("amount")
    tx_type = args.type or parsed.get("type", "expense")
    occurred_at = parse_when(args.date or args.occurred_at) if (args.date or args.occurred_at) else parsed.get("occurred_at", now_iso())
    merchant = args.merchant if args.merchant is not None else parsed.get("merchant")
    note = args.note if args.note is not None else parsed.get("note")
    tx = build_transaction_payload(
        ledger,
        amount_value=amount_value,
        tx_type=tx_type,
        currency=args.currency,
        category=args.category or parsed.get("category"),
        occurred_at=occurred_at,
        source=args.source,
        confidence=args.confidence,
        tags=args.tags,
        merchant=merchant,
        note=note,
        attachment=args.attachment,
        tx_id=args.id,
    )
    duplicates = likely_duplicate_candidates(ledger["transactions"], tx)
    if duplicates and not args.allow_duplicate:
        print(
            json.dumps(
                {
                    "error": "likely_duplicate",
                    "message": "Likely duplicate transaction. Pass --allow-duplicate to add anyway.",
                    "candidates": duplicates,
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2)
    ledger["transactions"].append(tx)
    ledger["transactions"].sort(key=lambda item: item.get("occurred_at", ""), reverse=True)
    save_ledger(args.ledger, ledger)
    print(json.dumps(tx, ensure_ascii=False, indent=2))


def month_key(iso_value):
    return (iso_value or "")[:7]


def filter_transactions(transactions, month=None, query=None, tx_type=None, category=None):
    rows = []
    q = (query or "").lower()
    for tx in transactions:
        if month and month_key(tx.get("occurred_at")) != month:
            continue
        if tx_type and tx.get("type") != tx_type:
            continue
        if category and tx.get("category") != category:
            continue
        if q:
            haystack = " ".join(
                str(tx.get(k, "")) for k in ("category", "merchant", "note", "amount", "type")
            ).lower()
            if q not in haystack:
                continue
        rows.append(tx)
    return rows


def summarize(transactions):
    totals = {"expense": 0.0, "income": 0.0, "refund": 0.0, "transfer": 0.0}
    by_category = defaultdict(float)
    for tx in transactions:
        tx_type = tx.get("type", "expense")
        amount = float(tx.get("amount", 0) or 0)
        if tx_type in totals:
            totals[tx_type] += amount
        if tx_type == "expense":
            by_category[tx.get("category") or "其他"] += amount
    totals = {k: round(v, 2) for k, v in totals.items()}
    totals["net"] = round(totals["income"] + totals["refund"] - totals["expense"], 2)
    return {
        "totals": totals,
        "by_category": dict(sorted(by_category.items(), key=lambda item: item[1], reverse=True)),
        "count": len(transactions),
    }


def summary_command(args):
    ledger = load_ledger(args.ledger, create=False)
    rows = filter_transactions(
        ledger["transactions"],
        month=args.month,
        query=args.query,
        tx_type=args.type,
        category=args.category,
    )
    data = summarize(rows)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    label = args.month or "全部"
    print(f"范围: {label}")
    print(f"笔数: {data['count']}")
    print(f"支出: {data['totals']['expense']:.2f}")
    print(f"收入: {data['totals']['income']:.2f}")
    print(f"退款: {data['totals']['refund']:.2f}")
    print(f"净额: {data['totals']['net']:.2f}")
    if data["by_category"]:
        print("分类支出:")
        for category, amount in data["by_category"].items():
            print(f"- {category}: {amount:.2f}")


def list_command(args):
    ledger = load_ledger(args.ledger, create=False)
    rows = filter_transactions(
        ledger["transactions"],
        month=args.month,
        query=args.query,
        tx_type=args.type,
        category=args.category,
    )
    rows.sort(key=lambda item: item.get("occurred_at", ""), reverse=True)
    for tx in rows[: args.limit]:
        date = (tx.get("occurred_at") or "")[:10]
        merchant = tx.get("merchant") or "-"
        note = tx.get("note") or ""
        amount = float(tx.get("amount", 0) or 0)
        print(f"{tx.get('id')} | {date} | {tx.get('type')} | {tx.get('category')} | {amount:.2f} | {merchant} | {note}")


def find_transaction(ledger, tx_id):
    for index, tx in enumerate(ledger["transactions"]):
        if tx.get("id") == tx_id:
            return index, tx
    raise SystemExit(f"Transaction not found: {tx_id}")


def update_command(args):
    ledger = load_ledger(args.ledger, create=False)
    _, tx = find_transaction(ledger, args.id)
    if args.amount is not None:
        amount = float(args.amount)
        if amount <= 0:
            raise SystemExit("Amount must be positive")
        tx["amount"] = round(amount, 2)
    if args.type is not None:
        tx["type"] = args.type
    if args.currency is not None:
        tx["currency"] = args.currency
    if args.category is not None:
        tx["category"] = args.category
    if args.merchant is not None:
        if args.merchant == "":
            tx.pop("merchant", None)
        else:
            tx["merchant"] = args.merchant
    if args.note is not None:
        if args.note == "":
            tx.pop("note", None)
        else:
            tx["note"] = args.note
    if args.date or args.occurred_at:
        tx["occurred_at"] = parse_when(args.date or args.occurred_at)
    if args.source is not None:
        tx["source"] = args.source
    if args.confidence is not None:
        validate_confidence(args.confidence)
        tx["confidence"] = args.confidence
    if args.tags is not None:
        tx["tags"] = coerce_tags(args.tags)
    if args.attachment is not None:
        if args.attachment == "":
            tx.pop("attachment", None)
        else:
            tx["attachment"] = args.attachment
    tx["updated_at"] = now_iso()
    ledger["transactions"].sort(key=lambda item: item.get("occurred_at", ""), reverse=True)
    save_ledger(args.ledger, ledger)
    print(json.dumps(tx, ensure_ascii=False, indent=2))


def delete_command(args):
    ledger = load_ledger(args.ledger, create=False)
    index, tx = find_transaction(ledger, args.id)
    if not args.yes:
        date = (tx.get("occurred_at") or "")[:10]
        print(f"Refusing to delete without --yes: {tx.get('id')} {date} {tx.get('category')} {tx.get('amount')}")
        return
    removed = ledger["transactions"].pop(index)
    save_ledger(args.ledger, ledger)
    print(json.dumps(removed, ensure_ascii=False, indent=2))


def render_command(args):
    ledger = load_ledger(args.ledger, create=False)
    template_path = Path(args.template) if args.template else DEFAULT_TEMPLATE
    html = template_path.read_text(encoding="utf-8")
    payload = json.dumps(ledger, ensure_ascii=False)
    payload = payload.replace("</", "<\\/")
    html = html.replace("__LEDGER_DATA__", payload)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    print(str(output.resolve()))


def parse_command(args):
    ledger = load_optional_ledger(args.ledger, args.currency)
    proposal = parse_text_transaction(
        args.text,
        ledger=ledger,
        currency=args.currency or ledger.get("currency", "CNY"),
        source=args.source,
    )
    duplicates = likely_duplicate_candidates(ledger.get("transactions", []), proposal)
    if duplicates:
        proposal["possible_duplicates"] = duplicates
    print(json.dumps(proposal, ensure_ascii=False, indent=2))


def recent_command(args):
    list_command(args)


def add_issue(issues, code, message, tx=None, **extra):
    issue = {"code": code, "message": message}
    if tx and isinstance(tx, dict):
        issue["id"] = tx.get("id")
    issue.update(extra)
    issues.append(issue)


def doctor_report(ledger):
    issues = []
    transactions = ledger.get("transactions", [])
    for index, tx in enumerate(transactions):
        if not isinstance(tx, dict):
            add_issue(issues, "invalid_transaction", "Transaction must be an object", index=index)
            continue
        for field in sorted(REQUIRED_TX_FIELDS):
            if field not in tx:
                add_issue(issues, "missing_field", f"Missing required field: {field}", tx, field=field)
        if tx.get("type") not in VALID_TYPES:
            add_issue(issues, "invalid_type", "Invalid transaction type", tx, value=tx.get("type"))
        try:
            amount = float(tx.get("amount", 0))
            if amount <= 0:
                add_issue(issues, "invalid_amount", "Amount must be positive", tx, value=tx.get("amount"))
        except (TypeError, ValueError):
            add_issue(issues, "invalid_amount", "Amount must be numeric", tx, value=tx.get("amount"))
        confidence = tx.get("confidence")
        if confidence is not None:
            try:
                confidence_value = float(confidence)
                if confidence_value < 0 or confidence_value > 1:
                    add_issue(issues, "invalid_confidence", "Confidence must be between 0 and 1", tx, value=confidence)
            except (TypeError, ValueError):
                add_issue(issues, "invalid_confidence", "Confidence must be numeric", tx, value=confidence)
    for index, tx in enumerate(transactions):
        if not isinstance(tx, dict):
            continue
        duplicates = likely_duplicate_candidates(transactions[:index], tx)
        for duplicate in duplicates:
            add_issue(
                issues,
                "likely_duplicate",
                "Likely duplicate transaction",
                tx,
                duplicate_id=duplicate.get("id"),
            )
    return {"ok": not issues, "issue_count": len(issues), "issues": issues}


def doctor_command(args):
    ledger = load_ledger(args.ledger, create=False)
    report = doctor_report(ledger)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print("账本体检: 通过" if report["ok"] else f"账本体检: {report['issue_count']} 个问题")
    for issue in report["issues"]:
        tx_id = issue.get("id") or "-"
        print(f"- {issue['code']} | {tx_id} | {issue['message']}")


def import_tsv_command(args):
    ledger = load_ledger(args.ledger, args.currency)
    input_path = Path(args.input)
    added_ids = []
    with input_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames:
            raise SystemExit("TSV header is required")
        for line_number, row in enumerate(reader, start=2):
            if not any((value or "").strip() for value in row.values()):
                continue
            amount_value = row.get("amount")
            occurred_at = row.get("occurred_at")
            if not amount_value:
                raise SystemExit(f"Missing amount on line {line_number}")
            if not occurred_at:
                raise SystemExit(f"Missing occurred_at on line {line_number}")
            confidence_value = row.get("confidence")
            confidence = float(confidence_value) if confidence_value not in (None, "") else 1.0
            tx = build_transaction_payload(
                ledger,
                amount_value=amount_value,
                tx_type=row.get("type") or "expense",
                currency=row.get("currency") or args.currency,
                category=row.get("category"),
                occurred_at=parse_when(occurred_at),
                source=row.get("source") or "import",
                confidence=confidence,
                tags=row.get("tags"),
                merchant=row.get("merchant"),
                note=row.get("note"),
                attachment=row.get("attachment"),
                tx_id=row.get("id"),
            )
            duplicates = likely_duplicate_candidates(ledger["transactions"], tx)
            if duplicates and not args.allow_duplicate:
                print(
                    json.dumps(
                        {
                            "error": "likely_duplicate",
                            "line": line_number,
                            "message": "Likely duplicate transaction in import. Pass --allow-duplicate to import anyway.",
                            "candidates": duplicates,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    file=sys.stderr,
                )
                raise SystemExit(2)
            ledger["transactions"].append(tx)
            added_ids.append(tx["id"])
    ledger["transactions"].sort(key=lambda item: item.get("occurred_at", ""), reverse=True)
    save_ledger(args.ledger, ledger)
    print(json.dumps({"input": str(input_path), "added": len(added_ids), "ids": added_ids}, ensure_ascii=False, indent=2))


def init_command(args):
    path = Path(args.ledger)
    if path.exists() and not args.force:
        print(f"Ledger already exists: {path}")
        return
    save_ledger(path, empty_ledger(args.currency))
    print(str(path.resolve()))


def build_parser():
    parser = argparse.ArgumentParser(description="Lazy Ledger JSON bookkeeping tool")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create an empty ledger")
    p_init.add_argument("--ledger", required=True)
    p_init.add_argument("--currency", default="CNY")
    p_init.add_argument("--force", action="store_true")
    p_init.set_defaults(func=init_command)

    p_add = sub.add_parser("add", help="Add one transaction")
    p_add.add_argument("--ledger", required=True)
    p_add.add_argument("--amount", default=None)
    p_add.add_argument("--text", default=None, help="Parse a casual text transaction before adding")
    p_add.add_argument("--type", default=None, choices=sorted(VALID_TYPES))
    p_add.add_argument("--currency", default=None)
    p_add.add_argument("--category", default=None)
    p_add.add_argument("--merchant", default=None)
    p_add.add_argument("--note", default=None)
    p_add.add_argument("--date", default=None)
    p_add.add_argument("--occurred-at", default=None)
    p_add.add_argument("--source", default="text", choices=sorted(VALID_SOURCES))
    p_add.add_argument("--confidence", type=float, default=1.0)
    p_add.add_argument("--tags", default=None)
    p_add.add_argument("--attachment", default=None)
    p_add.add_argument("--id", default=None)
    p_add.add_argument("--allow-duplicate", action="store_true")
    p_add.set_defaults(func=add_transaction)

    p_parse = sub.add_parser("parse", help="Preview a casual text transaction without writing")
    p_parse.add_argument("--text", required=True)
    p_parse.add_argument("--ledger", default=None)
    p_parse.add_argument("--currency", default="CNY")
    p_parse.add_argument("--source", default="text", choices=sorted(VALID_SOURCES))
    p_parse.set_defaults(func=parse_command)

    p_update = sub.add_parser("update", help="Update one transaction by id")
    p_update.add_argument("--ledger", required=True)
    p_update.add_argument("--id", required=True)
    p_update.add_argument("--amount", default=None)
    p_update.add_argument("--type", default=None, choices=sorted(VALID_TYPES))
    p_update.add_argument("--currency", default=None)
    p_update.add_argument("--category", default=None)
    p_update.add_argument("--merchant", default=None)
    p_update.add_argument("--note", default=None)
    p_update.add_argument("--date", default=None)
    p_update.add_argument("--occurred-at", default=None)
    p_update.add_argument("--source", default=None, choices=sorted(VALID_SOURCES))
    p_update.add_argument("--confidence", type=float, default=None)
    p_update.add_argument("--tags", default=None)
    p_update.add_argument("--attachment", default=None)
    p_update.set_defaults(func=update_command)

    p_delete = sub.add_parser("delete", help="Delete one transaction by id")
    p_delete.add_argument("--ledger", required=True)
    p_delete.add_argument("--id", required=True)
    p_delete.add_argument("--yes", action="store_true")
    p_delete.set_defaults(func=delete_command)

    for name, func, help_text in [
        ("summary", summary_command, "Summarize transactions"),
        ("list", list_command, "List transactions"),
        ("recent", recent_command, "List recent transactions"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--ledger", required=True)
        p.add_argument("--month", default=None, help="YYYY-MM")
        p.add_argument("--query", default=None)
        p.add_argument("--type", default=None, choices=sorted(VALID_TYPES))
        p.add_argument("--category", default=None)
        if name == "summary":
            p.add_argument("--json", action="store_true")
        else:
            p.add_argument("--limit", type=int, default=20)
        p.set_defaults(func=func)

    p_doctor = sub.add_parser("doctor", help="Validate ledger data and report likely duplicates")
    p_doctor.add_argument("--ledger", required=True)
    p_doctor.add_argument("--json", action="store_true")
    p_doctor.set_defaults(func=doctor_command)

    p_import_tsv = sub.add_parser("import-tsv", help="Import multiple transactions from a TSV file")
    p_import_tsv.add_argument("--ledger", required=True)
    p_import_tsv.add_argument("--input", required=True)
    p_import_tsv.add_argument("--currency", default=None)
    p_import_tsv.add_argument("--allow-duplicate", action="store_true")
    p_import_tsv.set_defaults(func=import_tsv_command)

    p_render = sub.add_parser("render", help="Generate a self-contained HTML dashboard")
    p_render.add_argument("--ledger", required=True)
    p_render.add_argument("--output", required=True)
    p_render.add_argument("--template", default=None)
    p_render.set_defaults(func=render_command)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
