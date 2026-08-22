#!/usr/bin/env python3
"""Prepare a confirmation table from a WeChat/bank bill screenshot. Does not write the ledger."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SWIFT_SRC = SCRIPT_DIR / "ocr_bill.swift"
OCR_BIN = Path(os.environ.get("LAZY_LEDGER_OCR_BIN", "/tmp/lazy-ledger-ocr-bill"))
sys.path.insert(0, str(SCRIPT_DIR))
from ledger_tool import infer_category  # noqa: E402

DATE_RE = re.compile(r"^(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})$")
DATE_COMPACT_RE = re.compile(r"^(\d{1,2})月(\d{1,2})日(\d{1,2}):(\d{2})$")
AMOUNT_RE = re.compile(r"^([+-])\s*(\d+\.\d{2})$")
HEADER_MONTH_RE = re.compile(r"(\d{4})年(\d{1,2})月")
HEADER_TOTALS_RE = re.compile(r"支出\s*¥?\s*([0-9,]+(?:\.\d+)?)\s*收入\s*¥?\s*([0-9,]+(?:\.\d+)?)")
TIMESTAMP_RE = re.compile(r"(20\d{6}\d{6})")
TRUNC_RE = re.compile(r"[.…⋯]{1,}$|。{2,}$|\.{2,}$")

CHROME = {
    "账单",
    "全部账单",
    "全部账单、",
    "查找交易",
    "Q 查找交易",
    "Q查找交易",
    "收支统计〉",
    "收支统计＞",
    "收支统计>",
    "美团",
    "X",
    "×",
    "•••",
    "已全额退款",
    "对方已退还",
    "对方已收钱",
}

SCREENSHOT_CATEGORY_EXTRA = {
    "餐饮": ("喜茶", "奈雪", "烧腊", "清补凉", "嗨小鸭", "小吃", "酸辣"),
    "购物": ("零食", "百货", "洋卡", "蔬菜", "卖菜"),
    "居住": ("物业",),
    "人情": ("转账", "红包"),
}

TSV_FIELDS = (
    "occurred_at",
    "type",
    "amount",
    "category",
    "merchant",
    "note",
    "source",
    "confidence",
    "method",
    "account",
)


def image_size(path):
    result = subprocess.run(
        ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    width = height = None
    for line in result.stdout.splitlines():
        if "pixelWidth:" in line:
            width = int(line.split(":")[-1].strip())
        if "pixelHeight:" in line:
            height = int(line.split(":")[-1].strip())
    return width, height


def find_original_image(path):
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"Image not found: {path}")
    width, height = image_size(path)
    info = {"path": path, "width": width, "height": height, "replaced": False, "reason": None}
    if width and width >= 400:
        return info
    needle = TIMESTAMP_RE.search(path.name)
    names = [path.name]
    if needle:
        names.append(needle.group(1))
    stem = re.sub(r"-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "", path.stem)
    search_dirs = [
        Path.home() / "Downloads",
        Path.home() / "Desktop",
        Path.home() / "Pictures",
        path.parent,
    ]
    candidates = []
    for folder in search_dirs:
        if not folder.is_dir():
            continue
        for child in folder.iterdir():
            if not child.is_file() or child.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".heic"}:
                continue
            if child.resolve() == path:
                continue
            if any(token in child.name for token in names) or stem and stem in child.name:
                try:
                    cw, ch = image_size(child)
                except (subprocess.CalledProcessError, ValueError):
                    continue
                if cw and cw > (width or 0):
                    candidates.append((cw * (ch or 1), child, cw, ch))
    if candidates:
        candidates.sort(reverse=True)
        _, better, cw, ch = candidates[0]
        info.update(
            {
                "path": better.resolve(),
                "width": cw,
                "height": ch,
                "replaced": True,
                "reason": f"chat thumbnail {width}x{height} replaced with {cw}x{ch}",
            }
        )
        return info
    info["reason"] = f"image is only {width}x{height}; OCR will likely fail"
    return info


def ensure_ocr_bin():
    if sys.platform != "darwin":
        raise SystemExit("Screenshot OCR uses macOS Vision. Run this on a Mac.")
    src_mtime = SWIFT_SRC.stat().st_mtime
    if OCR_BIN.exists() and OCR_BIN.stat().st_mtime >= src_mtime:
        return OCR_BIN
    OCR_BIN.parent.mkdir(parents=True, exist_ok=True)
    compile_result = subprocess.run(
        ["swiftc", "-O", "-o", str(OCR_BIN), str(SWIFT_SRC)],
        capture_output=True,
        text=True,
    )
    if compile_result.returncode != 0:
        raise SystemExit(compile_result.stderr.strip() or "swiftc failed")
    return OCR_BIN


def run_ocr(image_path):
    binary = ensure_ocr_bin()
    result = subprocess.run([str(binary), str(image_path)], capture_output=True)
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace").strip()
        raise SystemExit(err or "ocr failed")
    return json.loads(result.stdout.decode("utf-8"))


def parse_date(text, year):
    text = re.sub(r"\s+", " ", (text or "").strip())
    match = DATE_RE.match(text) or DATE_COMPACT_RE.match(text.replace(" ", ""))
    if not match:
        return None
    month = int(match.group(1))
    day = int(match.group(2))
    hour = int(match.group(3))
    minute = match.group(4)
    return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute}"


def parse_amount(text):
    match = AMOUNT_RE.match((text or "").replace(" ", ""))
    if not match:
        return None
    return match.group(1), float(match.group(2))


def extract_header(items):
    header = {"year": datetime.now().year, "month": None, "expense": None, "income": None, "label": None}
    month_hits = []
    total_hits = []
    for item in items:
        text = item.get("text") or ""
        month_match = HEADER_MONTH_RE.search(text)
        if month_match:
            month_hits.append((item.get("global_y", 0), month_match))
        totals = HEADER_TOTALS_RE.search(text.replace(",", "").replace(" ", ""))
        if totals:
            total_hits.append((item.get("global_y", 0), totals))
    if month_hits:
        month_hits.sort()
        match = month_hits[0][1]
        header["year"] = int(match.group(1))
        header["month"] = f"{match.group(1)}-{int(match.group(2)):02d}"
        header["label"] = match.group(0).rstrip("～<")
    if total_hits:
        total_hits.sort()
        totals = total_hits[0][1]
        header["expense"] = float(totals.group(1))
        header["income"] = float(totals.group(2))
    return header


def bucket_items(items):
    merchants, dates, amounts, statuses = [], [], [], []
    for item in items:
        text = (item.get("text") or "").strip()
        if not text or item.get("confidence", 1) < 0.3:
            continue
        if text in CHROME:
            if text in {"已全额退款", "对方已退还", "对方已收钱"}:
                statuses.append({**item, "text": text})
            continue
        if text.startswith("2026年") or text.startswith("2025年") or text.startswith("2024年"):
            continue
        if text.startswith("支出") or "收入¥" in text.replace(" ", ""):
            continue
        amount = parse_amount(text)
        if amount and item.get("x", 0) > 0.70:
            amounts.append({**item, "sign": amount[0], "amount": amount[1]})
            continue
        dated = parse_date(text, 2026)
        if dated and item.get("x", 0) < 0.55:
            dates.append({**item, "date_raw": text})
            continue
        if 0.12 < item.get("x", 0) < 0.55 and len(text) >= 2:
            merchants.append({**item, "text": text})
    return merchants, dates, amounts, statuses


def pair_rows(items, year):
    header = extract_header(items)
    year = int((header.get("month") or f"{year}-01")[:4])
    merchants, dates, amounts, statuses = bucket_items(items)
    used_dates = set()
    rows = []
    for amount in sorted(amounts, key=lambda item: item.get("global_y", 0)):
        merchant_cands = [
            merchant
            for merchant in merchants
            if abs(merchant.get("global_y", 0) - amount.get("global_y", 0)) < 35 and merchant.get("x", 0) < 0.55
        ]
        merchant_cands.sort(key=lambda item: abs(item.get("global_y", 0) - amount.get("global_y", 0)))
        merchant = merchant_cands[0] if merchant_cands else None
        base_y = merchant.get("global_y", 0) if merchant else amount.get("global_y", 0)
        date_cands = [
            dated
            for dated in dates
            if 8 < (dated.get("global_y", 0) - base_y) < 110 and id(dated) not in used_dates
        ]
        date_cands.sort(key=lambda item: abs(item.get("global_y", 0) - (base_y + 45)))
        dated = date_cands[0] if date_cands else None
        if dated is None:
            fallback = [
                item
                for item in dates
                if id(item) not in used_dates and abs(item.get("global_y", 0) - base_y) < 140
            ]
            fallback.sort(key=lambda item: abs(item.get("global_y", 0) - (base_y + 45)))
            dated = fallback[0] if fallback else None
        status_cands = [
            status
            for status in statuses
            if abs(status.get("global_y", 0) - amount.get("global_y", 0)) < 90
        ]
        status_cands.sort(key=lambda item: abs(item.get("global_y", 0) - amount.get("global_y", 0)))
        status = status_cands[0]["text"] if status_cands else None
        if merchant is None or dated is None:
            continue
        used_dates.add(id(dated))
        occurred = parse_date(dated.get("date_raw") or dated.get("text"), year)
        if not occurred:
            continue
        rows.append(
            {
                "occurred_at": occurred,
                "sign": amount["sign"],
                "amount": amount["amount"],
                "merchant": merchant["text"],
                "status": status,
                "global_y": amount.get("global_y", 0),
                "confidence": min(
                    amount.get("confidence", 1),
                    merchant.get("confidence", 1),
                    dated.get("confidence", 1),
                ),
            }
        )
    uniq = []
    seen = set()
    for row in rows:
        key = (row["occurred_at"], row["amount"], row["sign"], (row["merchant"] or "")[:12])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(row)
    uniq.sort(key=lambda item: (item["occurred_at"], item["global_y"]))
    return header, uniq


def infer_screenshot_category(merchant, tx_type):
    text = merchant or ""
    for category, keywords in SCREENSHOT_CATEGORY_EXTRA.items():
        if any(keyword in text for keyword in keywords):
            if tx_type == "income":
                return "收入" if category != "人情" else "人情"
            return category
    return infer_category(text, tx_type=tx_type)


def classify_row(row):
    merchant = row["merchant"]
    sign = row["sign"]
    status = row.get("status") or ""
    truncated = bool(TRUNC_RE.search(merchant))
    note_bits = ["微信账单截图"]
    if truncated:
        note_bits.append("商户名被截断")
    if status:
        note_bits.append(status)
    if sign == "+":
        if "退款" in merchant or "退还" in status:
            tx_type = "refund"
            merchant_out = re.sub(r"-退款$", "", merchant)
        else:
            tx_type = "income"
            merchant_out = merchant
    else:
        tx_type = "expense"
        merchant_out = merchant
        if "提现" in merchant:
            tx_type = "transfer"
    if tx_type == "expense" and merchant_out.startswith("转账-转给"):
        category = "人情"
    elif tx_type == "income" and ("转账-来自" in merchant_out or "红包" in merchant_out):
        category = "人情"
    else:
        category = infer_screenshot_category(merchant_out, tx_type)
    confidence = 0.7 if truncated else round(min(0.95, float(row.get("confidence") or 0.95)), 2)
    iso = row["occurred_at"].replace(" ", "T") + ":00+08:00"
    return {
        "occurred_at": iso,
        "type": tx_type,
        "amount": f"{row['amount']:.2f}",
        "category": category,
        "merchant": merchant_out,
        "note": "；".join(note_bits),
        "source": "image",
        "confidence": f"{confidence:.2f}",
        "method": "wechat",
        "account": "微信零钱",
        "sign": sign,
        "status": status or "",
        "display_time": row["occurred_at"],
        "display_amount": row["amount"],
    }


def render_markdown(header, rows):
    month = header.get("month") or "未知月份"
    lines = [f"识别结果（{month}，共 {len(rows)} 笔）。请核对后回复「可以导入」。", ""]
    if header.get("expense") is not None:
        lines.append(
            f"页眉合计：支出 ¥{header['expense']:.2f} / 收入 ¥{header['income']:.2f}（整月，不一定等于这一屏）。"
        )
        lines.append("")
    lines.append("| # | 日期时间 | 商户 | 收支 | 金额 | 备注 |")
    lines.append("|---|---|---|---|---|---|")
    labels = {"expense": "支出", "income": "收入", "refund": "退款", "transfer": "转账"}
    for index, row in enumerate(rows, start=1):
        lines.append(
            f"| {index} | {row['display_time']} | {row['merchant']} | {labels.get(row['type'], row['type'])} | "
            f"{row['display_amount']:.2f} | {row.get('status') or ''} |"
        )
    lines.append("")
    lines.append("截断的商户名按原样保留。已全额退款的会记成「支出 + 退款」两笔。")
    return "\n".join(lines).rstrip() + "\n"


def write_tsv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["\t".join(TSV_FIELDS)]
    for row in rows:
        lines.append("\t".join(str(row.get(field) or "") for field in TSV_FIELDS))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path.resolve()


def prepare(image, output=None, json_out=False):
    info = find_original_image(image)
    ocr = run_ocr(info["path"])
    items = ocr.get("items") or []
    if len(items) < 8:
        payload = {
            "error": "ocr_too_sparse",
            "message": "识别结果太少，多半是缩略图。请发相册原图或从下载里打开原文件。",
            "image": str(info["path"]),
            "width": info["width"],
            "height": info["height"],
            "item_count": len(items),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        raise SystemExit(2)
    header, raw_rows = pair_rows(items, datetime.now().year)
    classified = [classify_row(row) for row in raw_rows]
    if not classified:
        raise SystemExit("No complete bill rows (merchant + date + amount) were recognized.")
    tsv_path = Path(output) if output else Path.cwd() / "wechat-bill-rows.tsv"
    write_tsv(tsv_path, classified)
    markdown = render_markdown(header, classified)
    payload = {
        "image": str(Path(image).expanduser().resolve()),
        "resolved_image": str(info["path"]),
        "thumbnail_replaced": info["replaced"],
        "width": ocr.get("width") or info["width"],
        "height": ocr.get("height") or info["height"],
        "header": header,
        "count": len(classified),
        "tsv": str(tsv_path),
        "rows": classified,
        "markdown": markdown,
    }
    if json_out:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if info["replaced"]:
            print(f"已改用更清晰的原图：{info['path']}（{info['width']}x{info['height']}）\n")
        print(markdown)
        print(f"TSV: {tsv_path}")
        print("确认前不要 import-tsv。用户说「可以导入」后再写入账本。")
    return payload


def build_parser():
    parser = argparse.ArgumentParser(description="Slice + OCR a bill screenshot into a confirmation table")
    sub = parser.add_subparsers(dest="command", required=True)
    p_prepare = sub.add_parser("prepare", help="Recognize rows and write TSV; do not import")
    p_prepare.add_argument("--image", required=True)
    p_prepare.add_argument("--output", default=None, help="TSV path; default ./wechat-bill-rows.tsv")
    p_prepare.add_argument("--json", action="store_true")
    p_prepare.set_defaults(func=lambda args: prepare(args.image, args.output, args.json))
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
