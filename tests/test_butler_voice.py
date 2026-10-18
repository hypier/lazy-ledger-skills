"""Tests for the butler's voice: advice, briefing, and the forwardable digest.

These are about *what the butler says*, not what it stores. The ledger-side
behaviour is covered elsewhere; here the question is whether the reply tells the
user what they asked for -- what happened, why, and what it means -- or dumps
machinery at them.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
WRAPPER = SCRIPTS / "ledger"

sys.path.insert(0, str(SCRIPTS))
import ledger_voice  # noqa: E402
from ledger_advice import build_advice  # noqa: E402


def ledger_path(cwd):
    return Path(cwd) / "data" / "ledgers" / "default" / "ledger.json"


def run(*args, cwd, check=True):
    result = subprocess.run([str(WRAPPER), *args], cwd=str(cwd), text=True, capture_output=True)
    if check and result.returncode != 0:
        raise AssertionError(f"command failed: {result.args}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result


class VoiceFormattingTest(unittest.TestCase):
    """The reply composer is pure, so it can be driven with plain dicts."""

    def test_row_reads_as_a_sentence_not_a_record(self):
        tx = {
            "nature": "spending",
            "amount": 38,
            "category": "咖啡茶饮",
            "merchant": "星巴克",
            "account": "微信零钱",
            "note": "星巴克38",
        }
        line = ledger_voice.describe_row(tx, "CNY")
        self.assertIn("支出", line)
        self.assertIn("¥38.00", line)
        self.assertIn("咖啡茶饮", line)
        # The note already says 星巴克; repeating it adds nothing.
        self.assertEqual(line.count("星巴克"), 0, line)

    def test_advance_is_named_an_advance_not_an_expense(self):
        tx = {"nature": "advance", "amount": 26, "category": "其他", "merchant": "公司", "account": "微信零钱"}
        line = ledger_voice.describe_row(tx, "CNY")
        self.assertIn("垫付", line)
        self.assertNotIn("支出", line)

    def test_reason_quotes_the_word_not_the_rule_name(self):
        tx = {"nature": "advance", "amount": 26, "category": "其他"}
        event = {"reasons": [{"rule": "REIMBURSE_EXPECTED_KEYWORDS", "match": "报销"}]}
        line = ledger_voice.why_line(tx, event)
        self.assertIn("报销", line)
        self.assertNotIn("KEYWORDS", line)
        self.assertNotIn("{", line)

    def test_reason_accepts_the_older_string_shape(self):
        tx = {"nature": "advance", "amount": 26}
        line = ledger_voice.why_line(tx, {"reasons": ["ADVANCE_KEYWORDS: 垫付"]})
        self.assertIn("垫付", line)

    def test_no_change_in_spending_is_stated_plainly(self):
        before = {"expense": 100.0}
        after = {"expense": 100.0, "count": 3, "label": "本月"}
        line = ledger_voice.impact_line(before, after, "CNY")
        self.assertIn("没有变化", line)

    def test_impact_names_the_new_total(self):
        line = ledger_voice.impact_line({"expense": 100.0}, {"expense": 138.0, "count": 3, "label": "本月"}, "CNY")
        self.assertIn("¥138.00", line)
        self.assertIn("¥38.00", line)

    def test_no_empty_section_header(self):
        payload = {
            "added": [{"nature": "spending", "amount": 38, "category": "咖啡茶饮"}],
            "before": {"expense": 0},
            "after": {"expense": 38, "count": 1, "label": "本月"},
        }
        lines = ledger_voice.compose_record(payload, "CNY", findings=[])
        self.assertNotIn("顺带一提：", "\n".join(lines))


class AdviceTest(unittest.TestCase):
    """Advice must be anchored to numbers, and must stay quiet without history."""

    def _expenses(self, category, merchant, count, amount):
        return [
            {
                "type": "expense",
                "amount": amount,
                "category": category,
                "merchant": merchant,
                "occurred_at": f"2026-10-{index + 1:02d}T12:00:00+08:00",
            }
            for index in range(count)
        ]

    def test_no_advice_on_a_thin_ledger(self):
        ledger = {"transactions": self._expenses("外食", "美团", 3, 45)}
        self.assertEqual(build_advice(ledger, self._expenses("外食", "美团", 3, 45), "2026-10", "CNY"), [])

    def test_frequent_category_earns_a_cap_offer(self):
        expenses = self._expenses("外食", "美团", 8, 45)
        ledger = {"transactions": expenses}
        advice = build_advice(ledger, expenses, "2026-10", "CNY")
        self.assertTrue(advice)
        cap = next(item for item in advice if item["kind"] == "cap_suggestion")
        # A cap well under current behaviour reads as scolding, so it is rounded
        # down gently: 315 -> 300, not 315 -> 100.
        self.assertGreaterEqual(cap["evidence"]["cap"], cap["evidence"]["amount"] * 0.5)
        self.assertLessEqual(cap["evidence"]["cap"], cap["evidence"]["amount"])
        self.assertIn("外食", cap["message"])

    def test_cap_command_is_a_command_that_exists(self):
        expenses = self._expenses("外食", "美团", 8, 45)
        advice = build_advice({"transactions": expenses}, expenses, "2026-10", "CNY")
        for item in advice:
            command = item.get("command")
            if not command:
                continue
            # budget set takes --amount; the suggestion must not invent a flag.
            self.assertIn("--amount", command)
            self.assertNotIn("--overall", command)

    def test_advice_does_not_repeat_a_finding(self):
        expenses = self._expenses("外食", "美团", 8, 45)
        findings = [{"kind": "habit_merchant", "merchant": "美团"}]
        advice = build_advice({"transactions": expenses}, expenses, "2026-10", "CNY", findings=findings)
        for item in advice:
            evidence = item.get("evidence") or {}
            self.assertNotEqual(str(evidence.get("merchant")), "美团")


class BriefAndDigestTest(unittest.TestCase):
    def _seed(self, tmp, rows, opening=3000):
        run("init", cwd=tmp)
        run("account", "update", "--id", "acc_wechat", "--opening", str(opening), cwd=tmp)
        for text in rows:
            run("add", "--text", text, "--allow-duplicate", cwd=tmp)

    def test_brief_reads_as_prose_with_an_opinion(self):
        rows = ["美团外卖45", "美团外卖52", "美团外卖48", "美团外卖40", "星巴克38", "地铁6", "超市120"]
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp, rows)
            out = run("brief", cwd=tmp).stdout
            self.assertIn("本月支出", out)
            # A table would not say what the month was mostly about.
            self.assertTrue("外卖" in out or "餐饮" in out, out)

    def test_digest_has_no_commands_or_schema_words(self):
        rows = ["星巴克38", "美团外卖45", "打车26，公司报销"]
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp, rows)
            out = run("digest", cwd=tmp).stdout
            for banned in ("ledger ", "--id", "transaction", "tx_"):
                self.assertNotIn(banned, out, f"digest leaked {banned!r}:\n{out}")
            self.assertIn("账单", out)

    def test_digest_stands_alone_when_period_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            out = run("digest", "--range", "last-month", cwd=tmp).stdout
            self.assertIn("没有记录", out)
            # The empty message must not appear twice.
            self.assertEqual(out.count("没有记录"), 1, out)

    def test_digest_json_lists_its_lines(self):
        rows = ["星巴克38"]
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp, rows)
            payload = json.loads(run("digest", "--json", cwd=tmp).stdout)
            self.assertTrue(payload["lines"])
            self.assertIn("period", payload)

    def test_brief_json_exposes_advice(self):
        rows = ["美团外卖45"] * 8 + ["星巴克38"]
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp, rows)
            payload = json.loads(run("brief", "--json", cwd=tmp).stdout)
            self.assertIn("advice", payload)


if __name__ == "__main__":
    unittest.main()
