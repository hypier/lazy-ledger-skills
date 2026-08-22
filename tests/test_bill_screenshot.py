import sys
import tempfile
import unittest
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

    def test_find_original_prefers_wider_download(self):
        src = Path("/Users/barry/Downloads/微信图片_20260822154929_198_5.jpg")
        thumb = Path(
            "/Users/barry/.cursor/projects/Users-barry-code-skills-lazy-ledger/assets/"
            "_____20260822154929_198_5-fc31f3c3-d4f8-4d59-bbf7-ac4dd9737b26.jpg"
        )
        if not src.exists() or not thumb.exists():
            self.skipTest("sample WeChat screenshot not on disk")
        info = bill_screenshot.find_original_image(thumb)
        self.assertTrue(info["replaced"])
        self.assertGreaterEqual(info["width"], 400)


if __name__ == "__main__":
    unittest.main()
