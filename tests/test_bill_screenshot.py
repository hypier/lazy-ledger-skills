import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bill_screenshot  # noqa: E402


def box(text, x, global_y, confidence=1.0):
    return {"text": text, "x": x, "y": 0.5, "w": 0.2, "h": 0.03, "confidence": confidence, "global_y": global_y}


class BillScreenshotTest(unittest.TestCase):
    def test_pair_rows_builds_table_rows_from_ocr_boxes(self):
        items = [
            box("2026年8月", 0.04, 40),
            box("支出¥1794.12 收入¥226.73", 0.53, 40),
            box("零食有鸣", 0.20, 100),
            box("-13.80", 0.83, 100),
            box("8月22日 10:48", 0.20, 145),
            box("洋卡优选-退款", 0.20, 220),
            box("+32.61", 0.83, 220),
            box("8月18日 16:33", 0.20, 265),
            box("洋卡优选", 0.20, 340),
            box("-32.61", 0.83, 340),
            box("已全额退款", 0.79, 370),
            box("8月18日 16:11", 0.20, 385),
            box("2026年7月", 0.04, 9000),
            box("支出¥8370.30 收入¥5169.07", 0.53, 9000),
        ]
        header, rows = bill_screenshot.pair_rows(items, 2026)
        self.assertEqual(header["month"], "2026-08")
        self.assertEqual(header["expense"], 1794.12)
        self.assertEqual(len(rows), 3)
        classified = [bill_screenshot.classify_row(row) for row in rows]
        merchants = [row["merchant"] for row in classified]
        types = [row["type"] for row in classified]
        self.assertIn("零食有鸣", merchants)
        self.assertIn("refund", types)
        self.assertEqual(classified[types.index("refund")]["merchant"], "洋卡优选")
        markdown = bill_screenshot.render_markdown(header, classified)
        self.assertIn("请核对后回复「可以导入」", markdown)
        self.assertIn("零食有鸣", markdown)

    def test_write_tsv_uses_import_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [
                bill_screenshot.classify_row(
                    {
                        "occurred_at": "2026-08-22 10:48",
                        "sign": "-",
                        "amount": 13.8,
                        "merchant": "零食有鸣",
                        "status": None,
                        "confidence": 1,
                    }
                )
            ]
            path = bill_screenshot.write_tsv(Path(tmp) / "rows.tsv", rows)
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("occurred_at\ttype\tamount"))
            self.assertIn("零食有鸣", text)
            self.assertIn("image", text)

    def test_prepare_refund_does_not_invent_an_expense_or_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "screenshot.png"
            source.write_bytes(b"synthetic fixture")
            row = {"occurred_at": "2026-08-18 16:33", "sign": "+", "amount": 32.61,
                   "merchant": "商户-退款", "status": "退款成功", "confidence": 1}
            with patch.object(bill_screenshot, "find_original_image", return_value={"path":source,"width":600,"height":900,"replaced":False}), patch.object(bill_screenshot, "run_ocr", return_value={"items":[{}]*8}), patch.object(bill_screenshot,"pair_rows",return_value=({},[row])), patch("builtins.print"):
                result = bill_screenshot.prepare(source, Path(tmp)/"rows.tsv", True)
            self.assertEqual(result["count"], 1)
            self.assertEqual(result["rows"][0]["type"], "refund")
            self.assertEqual(result["rows"][0]["account"], "")
            self.assertNotIn("由退款行反推", result["markdown"])


if __name__ == "__main__":
    unittest.main()
