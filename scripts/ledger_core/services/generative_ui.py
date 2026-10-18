"""Minimal financial projections and a universal offline presentation host."""
import base64
import copy
import hashlib
import html
import json
from pathlib import Path

from ..domain.money import decimal_amount

ASSETS = Path(__file__).resolve().parents[3] / "assets" / "openui"
LABELS = {"totals.income": "已记录收入", "totals.expense": "已记录支出",
          "totals.refund": "已记录退款", "totals.net": "已记录净额",
          "by_category": "支出分类", "by_day": "每日已记录支出",
          "budgets": "预算执行", "transactions": "交易依据"}


def catalog():
    return json.loads((ASSETS / "catalog.json").read_text(encoding="utf-8"))


def capabilities():
    value = catalog()
    try:
        build, _ = asset_bytes()
        value.update(available=True, dependencies=build["dependencies"], asset_digests=build["sha256"])
    except (OSError, ValueError, KeyError):
        value["available"] = False
    value["formats"] = ["openui", "view-json"]
    value["generation"] = catalog()["generation"]
    import shutil
    value["generation"]["node_available"] = bool(shutil.which("node"))
    return value


def money(value):
    return f"{decimal_amount(value):.2f}"


def transaction(row):
    """Allowlist: no source cells, paths, profile text or extension metadata."""
    return {**{key: row.get(key, "") for key in ("id", "occurred_at", "type", "category", "merchant", "currency")},
            "amount": money(row["amount"]), "excluded": bool(row.get("excluded_from_totals", row.get("excluded", False)))}


def import_data(plan):
    if plan.get("preview_version") != 1:
        raise ValueError("This import plan predates visual previews; run import prepare again")
    dispositions = {item["line"]: item for item in plan["dispositions"]}
    reviews = {item["line"]: item for item in plan["review"]}
    rows = []
    for item in plan["preview_rows"]:
        disposition = dispositions.get(item["line"], {})
        rows.append({**transaction(item), "line": item["line"],
                     "state": "review" if item["line"] in reviews else disposition.get("state", "review"),
                     "id": disposition.get("id", "")})
    issues = [{"line": r.get("line"), "reason": str(r.get("reason", "")),
               "related_ids": r.get("candidates", []) or ([r["id"]] if r.get("id") else [])}
              for r in plan["review"]]
    issues.extend({"line": r.get("line"), "reason": str(r["error"]), "related_ids": []}
                  for r in plan["source"].get("errors", []))
    issues.extend({"line": None, "reason": f"来源核对不一致：{r['kind']}，预期 {r['expected']}，实际 {r['actual']}",
                   "related_ids": []} for r in plan["mismatches"])
    summary = {"count": plan["count"], "ready": bool(plan["ready"]),
               "new": sum(r["state"] == "new" for r in rows),
               "existing": sum(r["state"] in {"existing", "linked"} for r in rows),
               "excluded": sum(r["state"] == "excluded" for r in rows),
               "review": sum(r["state"] == "review" for r in rows),
               "errors": len(plan["source"].get("errors", []))}
    return {"import_summary": summary, "import_rows": rows, "import_issues": issues}


def default_layout(report):
    kind = report.get("kind", "report")
    if kind == "import":
        pairs = [("ImportSummary", "import_summary"), ("Issues", "import_issues"), ("ImportRows", "import_rows")]
    else:
        pairs = [("Metric", "totals." + key) for key in ("income", "expense", "refund", "net")]
        if report["metrics"]["by_category"]:
            pairs.append(("Breakdown", "by_category"))
        if len(report["metrics"]["by_day"]) > 1:
            pairs.append(("Breakdown", "by_day"))
        if report["metrics"]["budgets"]:
            pairs.append(("Budget", "budgets"))
        if report.get("presentation", {}).get("density") != "compact":
            pairs.append(("Transactions", "transactions"))
    return {"version": 1, "blocks": [{"component": c, "ref": r} for c, r in pairs]}


def validate_layout(layout, kind):
    if not isinstance(layout, dict) or set(layout) != {"version", "blocks"} or type(layout["version"]) is not int or layout["version"] != 1:
        raise ValueError("Layout requires version 1 and blocks only")
    blocks = layout["blocks"]
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= 24:
        raise ValueError("Layout needs 1 to 24 blocks")
    components = catalog()["components"]
    seen = set()
    for block in blocks:
        if not isinstance(block, dict) or set(block) != {"component", "ref"}:
            raise ValueError("Each block requires component and ref only")
        component, ref = block["component"], block["ref"]
        if not isinstance(component, str) or not isinstance(ref, str):
            raise ValueError("Component and ref must be strings")
        spec = components.get(component)
        if not spec or kind not in spec["kinds"] or ref not in spec["refs"]:
            raise ValueError("Unknown component or data reference for this snapshot")
        if (component, ref) in seen:
            raise ValueError("Repeated layout block")
        seen.add((component, ref))
    return copy.deepcopy(layout)


def project(report, layout=None, *, refs=None):
    from .reports import check
    check(report)
    kind = report.get("kind", "report")
    if refs is None:
        layout = validate_layout(default_layout(report) if layout is None else layout, kind)
    else:
        if not isinstance(refs, list) or any(not isinstance(r, str) for r in refs):
            raise ValueError("Data refs must be an array of strings")
        available = {ref: name for name, spec in catalog()["components"].items() if kind in spec["kinds"] for ref in spec["refs"]}
        if any(ref not in available for ref in refs):
            raise ValueError("Unknown data reference for this snapshot")
        layout = {"version": 1, "blocks": [{"component": available[r], "ref": r} for r in dict.fromkeys(refs)]}
    datasets = {}
    for block in layout["blocks"]:
        ref = block["ref"]
        if kind == "import":
            datasets[ref] = copy.deepcopy(report["import"][ref])
        elif ref.startswith("totals."):
            key = ref.split(".")[1]
            evidence = report["evidence"].get(key)
            if evidence is None:
                evidence = list(dict.fromkeys(i for ids in report["evidence"].values() for i in ids))
            datasets[ref] = {"label": LABELS[ref], "value": money(report["metrics"]["totals"][key]), "evidence": evidence}
        elif ref in {"by_category", "by_day"}:
            datasets[ref] = {"label": LABELS[ref], "rows": [{"label": k, "amount": money(v)} for k, v in report["metrics"][ref].items()]}
        elif ref == "budgets":
            datasets[ref] = [{**{k: r.get(k) for k in ("id", "month", "category", "pct", "over")},
                              **{k: money(r[k]) for k in ("limit", "spent", "remaining")}}
                             for r in report["metrics"][ref]]
        elif ref == "transactions":
            datasets[ref] = [transaction(r) for r in report["transactions"]]
    if kind == "import":
        coverage = {"ready_at_prepare": report["import"]["import_summary"]["ready"],
                    "notice": "导入计划快照，尚未执行；可提交状态仅代表准备时，提交前仍需检查版本与待复核项。"}
    else:
        coverage = {key: report["coverage"][key] for key in ("recorded_rows", "unassigned_rows", "complete")}
        coverage["unknown_openings"] = len(report["coverage"]["unknown_openings"])
        coverage["notice"] = "仅包含已记录数据，不代表全部实际收支。图表与预算使用所选期间内的支出，缺少记录的日期不代表零支出。"
    result = {"version": 1, "renderer": catalog()["renderer"], "kind": kind, "snapshot_id": report["id"],
              "snapshot_digest": report["digest"], "generated_at": report["generated_at"],
              "revision": report["context"]["revision"], "currency": report["currency"],
              "period": report["period"], "coverage": coverage, "layout": layout, "datasets": datasets}
    result["lang"] = compile_layout(layout)
    return result


def compile_layout(layout):
    # Called only after validation. Values are allowlisted names, never user code.
    names = [f"b{i}" for i in range(len(layout["blocks"]))]
    lines = ["root = LedgerView([" + ", ".join(names) + "])"]
    lines.extend(f'{name} = {block["component"]}({json.dumps(block["ref"])})'
                 for name, block in zip(names, layout["blocks"]))
    return "\n".join(lines)


def fallback(view):
    """Fallback uses the SAME minimal projection, never the full private report."""
    lines = [f"# {view['period']['label']}", "",
             f"币种：{view['currency']} · 快照：{view['snapshot_id']} · 修订：{view['revision']}",
             view["coverage"]["notice"],
             "覆盖情况：" + json.dumps(view["coverage"], ensure_ascii=False), ""]
    for block in view["layout"]["blocks"]:
        ref = block["ref"]
        lines.extend(["## " + LABELS.get(ref, {"import_summary": "导入概览", "import_rows": "来源行", "import_issues": "待核对"}.get(ref, ref)),
                      json.dumps(view["datasets"][ref], ensure_ascii=False, indent=2), ""])
    return "\n".join(lines)


def asset_bytes():
    manifest = json.loads((ASSETS / "build.json").read_text())
    if manifest["renderer"] != catalog()["renderer"]:
        raise ValueError("OpenUI assets version mismatch")
    content = {}
    for name in ("renderer.js", "renderer.css", "catalog.json", "generation.json", "validate.cjs"):
        raw = (ASSETS / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest["sha256"][name]:
            raise ValueError("OpenUI assets failed integrity check")
        content[name] = raw.decode("utf-8")
    return manifest, content


def document(view):
    """A single file: no CDN, fetch, model endpoint, tool provider or ledger API."""
    text = fallback(view)
    fallback_html = '<pre id="fallback">' + html.escape(text) + '</pre>'
    status = "交互视图加载中；下方保留同一快照的文本。"
    script, css = "", ""
    try:
        _, assets = asset_bytes()
        script, css = assets["renderer.js"], assets["renderer.css"]
    except (OSError, ValueError, KeyError):
        status = "交互资源不可用，已显示文本快照。"
    # Base64 prevents any ledger string from closing a script tag. The CSP allows
    # the trusted bundle plus inspected child scripts by hash. Child scripts
    # run only in the opaque iframe; resource loads remain CSP-restricted.
    payload = base64.b64encode(json.dumps(view, ensure_ascii=False).encode()).decode()
    script_hash = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    script_sources = "'sha256-" + script_hash + "'"
    for child_script in view.get("html_scripts", []):
        script_sources += " 'sha256-" + base64.b64encode(hashlib.sha256(child_script.encode()).digest()).decode() + "'"
    frame_policy = "; frame-src 'self' about:" if view.get("mode") == "html" else "; frame-src 'none'"
    image_policy = "data:" if view.get("mode") == "html" else "'none'"
    policy = f"default-src 'none'; script-src {script_sources}; style-src 'unsafe-inline'; connect-src 'none'; img-src {image_policy}; font-src 'none'; base-uri 'none'; form-action 'none'" + frame_policy
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta http-equiv="Content-Security-Policy" content="{policy}">'
            '<title>账本快照</title><style>body{margin:0;padding:24px;background:#f4f6f2;color:#243b32;font:16px/1.6 system-ui}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere;max-width:1100px;margin:auto}'
            + css + '</style></head><body>'
            f'<p id="status" role="status">{status}</p><main id="app"></main>'
            + fallback_html + f'<script id="ledger-view" type="application/octet-stream">{payload}</script>'
            + ('<script>' + script + '</script>' if script else '') + '</body></html>')
