"""Confirmed habit application and derived observations."""
import re
import uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from ledger_core.storage.documents import write_text_secure
from ledger_core.domain.model import now_iso, ledger_preferences
from ledger_core.domain.metrics import account_balances
from ledger_core.domain.semantics import counts_as
from ledger_core.storage import repository
from ledger_core.services.profile import active_rule

from ledger_core.domain.vocabulary import (ACCOUNT_TYPE_TO_METHOD, HABIT_PHRASES, MAX_LEARNABLE_PHRASE, MEMORY_MAX_AGE_DAYS, MEMORY_TX_GAP, METHOD_LABELS, NOISY_MERCHANT_RE)
from ledger_core.services.common import (default_account)


def habit_id_for(phrase):
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "_", str(phrase or "").strip()).strip("_")
    if not slug:
        raise SystemExit("Habit phrase is required")
    return f"hab_{slug}"[:48]


def ensure_habits(ledger):
    rows = ledger.setdefault("habits", [])
    if not isinstance(rows, list):
        rows = []
        ledger["habits"] = rows
    return rows


def find_habit(ledger, value):
    if not value:
        return None
    needle = str(value).strip()
    for habit in ensure_habits(ledger):
        if not isinstance(habit, dict):
            continue
        if habit.get("id") == needle or habit.get("phrase") == needle:
            return habit
    return None


def typical_amount(recent, *, locked_amount=None, amount_locked=False):
    if amount_locked and locked_amount is not None:
        return round(float(locked_amount), 2)
    values = [round(float(value), 2) for value in recent if value is not None]
    if len(values) < 2:
        return None
    value, count = Counter(values).most_common(1)[0]
    if count >= 2 and count >= (len(values) + 1) // 2:
        return value
    return None


def is_learnable_phrase(phrase):
    text = str(phrase or "").strip()
    if not text:
        return False
    if text in HABIT_PHRASES:
        return True
    if NOISY_MERCHANT_RE.search(text):
        return False
    if len(text) > MAX_LEARNABLE_PHRASE:
        return False
    return True


def habit_phrases_from_tx(tx):
    phrases = []
    merchant = str(tx.get("merchant") or "").strip()
    if is_learnable_phrase(merchant):
        phrases.append(merchant)
    blob = " ".join(str(part) for part in (tx.get("note"), tx.get("merchant"), tx.get("category")) if part)
    for phrase in HABIT_PHRASES:
        if phrase in blob and phrase not in phrases:
            phrases.append(phrase)
    return phrases


def habit_match_score(habit, text, merchant=None):
    phrase = str(habit.get("phrase") or "").strip()
    if not phrase:
        return None
    in_text = phrase in (text or "")
    merchant_hit = bool(merchant) and (
        habit.get("merchant") == merchant or phrase == merchant
    )
    if not in_text and not merchant_hit:
        return None
    return (
        1 if habit.get("pinned") else 0,
        len(phrase) if in_text else 0,
        habit.get("count") or 0,
    )


def match_habit(ledger, text, merchant=None):
    ranked = []
    for habit in ensure_habits(ledger or {}):
        if not isinstance(habit, dict) or not active_rule(habit):
            continue
        score = habit_match_score(habit, text, merchant)
        if score is not None:
            ranked.append((score, habit))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def apply_habit_to_proposal(proposal, habit, *, amount_from_habit=False):
    if not habit or habit.get("source") != "manual":
        return proposal
    phrase = habit.get("phrase")
    if amount_from_habit and not is_learnable_phrase(phrase) and habit.get("source") != "manual":
        amount_from_habit = False
    proposal["_habit"] = {
        "id": habit.get("id"),
        "phrase": phrase,
        "amount_from_habit": bool(amount_from_habit),
    }
    if amount_from_habit and habit.get("amount") is not None:
        proposal["amount"] = round(float(habit["amount"]), 2)
        confidence = proposal.get("confidence")
        if confidence is None or confidence > 0.9:
            proposal["confidence"] = 0.9
    if not proposal.get("merchant") and habit.get("merchant") and is_learnable_phrase(habit.get("merchant")):
        proposal["merchant"] = habit["merchant"]
    if (not proposal.get("category") or proposal.get("category") == "其他") and habit.get("category"):
        proposal["category"] = habit["category"]
    if not proposal.get("method") and habit.get("method"):
        proposal["method"] = habit["method"]
    if not proposal.get("account") and not proposal.get("account_id"):
        if habit.get("account") or habit.get("account_id"):
            proposal["account"] = habit.get("account")
            if habit.get("account_id"):
                proposal["account_id"] = habit["account_id"]
    return proposal


def affordable_account(ledger, amount):
    """The visible account with the most headroom, if any can cover ``amount``.

    Legacy query helper only. It is never used to infer a transaction account.
    Computed balances may be incomplete and do not establish purchasing power.
    """
    if not ledger:
        return None
    try:
        amount = float(amount or 0)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    candidates = [
        row
        for row in account_balances(ledger)
        if not row.get("archived")
        and row.get("balance", 0) >= amount
        # A credit card has no balance to overdraw; it is a valid payer.
        and row.get("type") in {"wechat", "alipay", "cash", "bank", "card", "other"}
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda row: row.get("balance", 0))


def apply_usage_defaults(proposal, ledger):
    # Only an explicitly configured default is eligible. Balances are not evidence
    # of which account actually paid, even for a large purchase.
    configured = default_account(ledger) if ledger is not None else None
    if configured and not proposal.get("account") and not proposal.get("account_id") and not proposal.get("method"):
        proposal["account"] = configured["name"]
        proposal["account_id"] = configured["id"]
        proposal["account_reason"] = "使用已确认的默认账户"
    return proposal


def is_shortcut_habit(habit):
    if not isinstance(habit, dict) or not habit.get("phrase"):
        return False
    if habit.get("pinned") or habit.get("source") == "manual":
        return True
    return (habit.get("count") or 0) >= 2


def sort_habits(habits):
    return sorted(
        [habit for habit in habits if isinstance(habit, dict)],
        key=lambda habit: (
            not habit.get("pinned"),
            -(habit.get("count") or 0),
            0 if habit.get("amount") is None else -1,
            habit.get("phrase") or "",
        ),
    )


def _mode(values):
    counted = Counter(value for value in values if value)
    if not counted:
        return None, 0
    value, count = counted.most_common(1)[0]
    return value, count


def build_usage_profile(ledger, summary=None):
    txs = [tx for tx in ledger.get("transactions", []) if isinstance(tx, dict)]
    expenses = [tx for tx in txs if counts_as(tx, "expense")]
    account, _ = _mode([tx.get("account") for tx in expenses])
    account_id, _ = _mode([tx.get("account_id") for tx in expenses if tx.get("account") == account])
    method, method_n = _mode([tx.get("method") for tx in expenses])
    configured_default = default_account(ledger)
    default_name = configured_default.get("name") if configured_default else None
    default_id = configured_default.get("id") if configured_default else None
    default_method = (
        ACCOUNT_TYPE_TO_METHOD.get(configured_default.get("type"))
        if configured_default
        else None
    )
    top_categories = [name for name, _count in Counter((tx.get("category") or "其他") for tx in expenses).most_common(4)]
    stable = []
    variable = []
    for habit in sort_habits(ensure_habits(ledger)):
        phrase = habit.get("phrase")
        if not is_learnable_phrase(phrase) and habit.get("source") != "manual":
            continue
        if (habit.get("count") or 0) < 2 and habit.get("source") != "manual":
            continue
        row = {
            "phrase": phrase,
            "category": habit.get("category"),
            "merchant": habit.get("merchant"),
            "amount": habit.get("amount"),
            "count": habit.get("count") or 0,
        }
        if habit.get("amount") is not None and habit.get("source") == "manual" and habit.get("amount_locked"):
            stable.append(row)
        else:
            variable.append(row)
    lunch_hours = 0
    for tx in expenses:
        try:
            hour = datetime.fromisoformat(tx.get("occurred_at") or "").hour
        except ValueError:
            continue
        if 11 <= hour <= 14 and (tx.get("category") in {"餐饮", "外食", "咖啡茶饮", "买菜", "零食"} or "饭" in str(tx.get("note") or "")):
            lunch_hours += 1
    parts = []
    if default_name:
        method_label = METHOD_LABELS.get(default_method) if default_method else None
        extra = f"（{method_label}）" if method_label else ""
        parts.append(f"记账默认走{default_name}{extra}，缺账户时不必再问。")
    if top_categories:
        parts.append("支出主要记在" + "、".join(top_categories[:3]) + "。")
    if lunch_hours >= 3:
        parts.append("已记录的午间交易中餐饮较多；这是观察，不作为自动分类规则。")
    for item in stable[:3]:
        category = item.get("category") or "支出"
        parts.append(f"{item['phrase']}常记{category}，金额大约 ¥{item['amount']:.0f}。")
    if variable:
        names = "、".join(item["phrase"] for item in variable[:3] if item.get("phrase"))
        if names:
            parts.append(f"{names}去得勤但金额不固定，缺金额时要问，不要猜。")
    if not parts:
        parts.append("按原始信息记账；没有确认的默认账户时保留未分配，缺金额时询问。")
    portrait = summary if summary else "".join(parts)
    return {
        "updated_at": now_iso(),
        "tx_count": len(txs),
        "summary": portrait,
        "defaults": {
            "account": default_name,
            "account_id": default_id,
            "method": default_method,
        },
        "top_categories": top_categories,
        "stable_amounts": stable[:8],
        "variable_merchants": variable[:8],
        "do_not": [
            "不要把长商户名、支付公司名做成快捷按钮",
            "金额不稳定时不要自动填金额",
            "不要向用户展示常用按钮或要求存为常用",
        ],
    }


def refresh_usage_profile(ledger, *, force=False, summary=None):
    prefs = ledger_preferences(ledger or {})
    current = prefs.get("usage_profile")
    tx_count = len(ledger.get("transactions") or [])
    token = repository.fingerprint({"transactions": ledger.get("transactions"), "habits": ledger.get("habits"), "default": prefs.get("default_account_id")})
    if (
        not force
        and not summary
        and isinstance(current, dict)
        and current.get("input_token") == token
        and current.get("summary")
    ):
        return False
    kept_summary = summary if summary else (current.get("summary") if isinstance(current, dict) and current.get("tx_count") == tx_count else None)
    prefs["usage_profile"] = build_usage_profile(ledger, summary=kept_summary if summary else None)
    prefs["usage_profile"]["input_token"] = token
    if summary:
        prefs["usage_profile"]["summary"] = summary
    return True


def ledger_stem(ledger_path):
    """Stable document stem: ledger.json -> ledger, lazy-ledger.json -> lazy-ledger."""
    return Path(ledger_path).stem or "ledger"


def habit_memory_path(ledger_path):
    path = Path(ledger_path)
    target = path.parent / f"{ledger_stem(path)}-memory.md"
    return target


def parse_habit_memory_meta(text):
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text or "", re.DOTALL)
    if not match:
        return {}
    meta = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"')
    return meta


def habit_memory_is_stale(memory_path, ledger):
    path = Path(memory_path)
    if not path.exists():
        return True
    try:
        meta = parse_habit_memory_meta(path.read_text(encoding="utf-8"))
    except OSError:
        return True
    tx_count = len(ledger.get("transactions") or [])
    remembered = int(float(meta["tx_count"])) if meta.get("tx_count") not in (None, "") else -1
    if remembered < 0 or tx_count - remembered >= MEMORY_TX_GAP:
        return True
    updated = meta.get("updated_at") or ""
    try:
        written = datetime.fromisoformat(updated)
        if written.tzinfo is None:
            written = written.astimezone()
        if datetime.now().astimezone() - written >= timedelta(days=MEMORY_MAX_AGE_DAYS):
            return True
    except ValueError:
        return True
    return False


def render_habit_memory(ledger, profile=None):
    prefs = ledger_preferences(ledger or {})
    profile = profile or prefs.get("usage_profile") or build_usage_profile(ledger)
    defaults = profile.get("defaults") or {}
    method = defaults.get("method")
    method_label = METHOD_LABELS.get(method, method) if method else ""
    lines = [
        "---",
        f"updated_at: {profile.get('updated_at') or now_iso()}",
        f"tx_count: {profile.get('tx_count') if profile.get('tx_count') is not None else len(ledger.get('transactions') or [])}",
        "kind: lazy-ledger-memory",
        "---",
        "",
        "# 记账习惯",
        "",
        "给记账助手读的定期总结。流水仍以账本 JSON 为准；这里只写默认怎么记。",
        "",
        "## 画像",
        "",
        (profile.get("summary") or "按原始信息记账；没有确认的默认账户时保留未分配，缺金额时询问。").strip(),
        "",
        "## 默认",
        "",
        f"- 账户：{defaults.get('account') or '微信零钱'}",
    ]
    if method_label:
        lines.append(f"- 支付：{method_label}")
    categories = profile.get("top_categories") or []
    if categories:
        lines.append(f"- 常见分类：{'、'.join(categories[:4])}")
    lines.extend(["", "## 稳定金额", "", "仅下列短说法在用户没写金额时可以默认。不要猜这里没有的金额。", ""])
    stable = profile.get("stable_amounts") or []
    if stable:
        for item in stable:
            amount = item.get("amount")
            price = f"¥{amount:.0f}" if isinstance(amount, (int, float)) else ""
            bits = [item.get("phrase"), item.get("category"), price]
            lines.append("- " + " · ".join(str(bit) for bit in bits if bit))
    else:
        lines.append("- （还没有稳定金额）")
    lines.extend(["", "## 不要猜金额", ""])
    variable = profile.get("variable_merchants") or []
    if variable:
        for item in variable:
            lines.append(f"- {item.get('phrase')}：常去但金额不固定")
    else:
        lines.append("- （暂无）")
    mapping = prefs.get("merchant_categories") or {}
    lines.extend(["", "## 商户分类", ""])
    if mapping:
        for merchant, category in list(mapping.items())[:20]:
            lines.append(f"- {merchant} → {category}")
    else:
        lines.append("- （还没有显式规则）")
    lines.extend(
        [
            "",
            "## 助手怎么用",
            "",
            "- 缺账户、缺支付方式时用「默认」",
            "- 只有「稳定金额」里的短说法可以补金额",
            "- 「不要猜金额」和支付公司全称一律问金额",
            "- 不要向用户展示常用按钮，也不要要求存为常用",
            "",
        ]
    )
    return "\n".join(lines)


def write_habit_memory(ledger_path, ledger, *, force=False, summary=None):
    path = habit_memory_path(ledger_path)
    refresh_usage_profile(ledger, force=force or bool(summary), summary=summary)
    if not force and not habit_memory_is_stale(path, ledger):
        return path, False
    profile = ledger_preferences(ledger).get("usage_profile") or build_usage_profile(ledger)
    write_text_secure(path, render_habit_memory(ledger, profile))
    return path, True


def habits_payload(ledger):
    refresh_usage_profile(ledger)
    rows = sort_habits(ensure_habits(ledger))
    profile = ledger_preferences(ledger).get("usage_profile") or {}
    return {"profile": profile, "habits": rows}


def prune_noisy_learned_habits(ledger):
    kept = []
    changed = False
    for habit in ensure_habits(ledger):
        if not isinstance(habit, dict):
            changed = True
            continue
        if habit.get("source") == "manual" or is_learnable_phrase(habit.get("phrase")):
            kept.append(habit)
        else:
            changed = True
    ledger["habits"] = kept
    return changed


def rebuild_learned_habits(ledger):
    kept = [
        habit
        for habit in ensure_habits(ledger)
        if isinstance(habit, dict) and habit.get("source") == "manual"
    ]
    ledger["habits"] = kept
    rows = [tx for tx in ledger.get("transactions", []) if isinstance(tx, dict)]
    rows.sort(key=lambda tx: tx.get("occurred_at") or "")
    for tx in rows:
        if tx.get("source") == "import" or tx.get("type") == "transfer":
            continue
        learn_habits_from_transaction(ledger, tx)
    refresh_usage_profile(ledger, force=True)
    return ensure_habits(ledger)


def seed_habits_if_needed(ledger):
    prefs = ledger_preferences(ledger or {})
    pruned = prune_noisy_learned_habits(ledger)
    if prefs.get("habits_seeded") and not pruned:
        return refresh_usage_profile(ledger)
    if ledger.get("transactions"):
        rebuild_learned_habits(ledger)
    prefs["habits_seeded"] = True
    refresh_usage_profile(ledger, force=True)
    return True


def upsert_habit(ledger, phrase, tx=None, **fields):
    phrase = str(phrase or "").strip()
    if not phrase:
        return None
    habit = find_habit(ledger, phrase)
    if habit and habit.get("source") == "manual" and fields.get("source") == "learned":
        return habit
    ts = now_iso()
    created = False
    if habit is None:
        habit = {
            "id": fields.pop("id", None) or habit_id_for(phrase),
            "phrase": phrase,
            "count": 0,
            "recent_amounts": [],
            "pinned": False,
            "amount_locked": False,
            "source": fields.pop("source", "learned"),
            "created_at": ts,
        }
        if any(existing.get("id") == habit["id"] for existing in ensure_habits(ledger) if isinstance(existing, dict)):
            habit["id"] = f"hab_{uuid.uuid4().hex[:8]}"
        ensure_habits(ledger).append(habit)
        created = True
    overwrite = created or fields.get("source") == "manual"
    for key in ("merchant", "category", "type", "method", "account", "account_id"):
        value = fields.get(key)
        if value in (None, ""):
            continue
        if overwrite or not habit.get(key):
            habit[key] = value
    if fields.get("pinned") is True:
        habit["pinned"] = True
    if fields.get("pinned") is False:
        habit["pinned"] = False
    if fields.get("source") == "manual":
        habit["source"] = "manual"
    if tx:
        amount = tx.get("amount")
        habit["count"] = int(habit.get("count") or 0) + 1
        habit["last_used_at"] = tx.get("occurred_at") or ts
        if amount is not None:
            recent = [round(float(value), 2) for value in (habit.get("recent_amounts") or []) if value is not None]
            recent.append(round(float(amount), 2))
            habit["recent_amounts"] = recent[-8:]
        if not habit.get("merchant") and tx.get("merchant"):
            habit["merchant"] = tx.get("merchant")
        if (not habit.get("category") or habit.get("category") == "其他") and tx.get("category") and tx.get("category") != "其他":
            habit["category"] = tx.get("category")
        if not habit.get("method") and tx.get("method"):
            habit["method"] = tx.get("method")
        if not habit.get("account") and tx.get("account"):
            habit["account"] = tx.get("account")
            if tx.get("account_id"):
                habit["account_id"] = tx.get("account_id")
        if not habit.get("type") and tx.get("type") and tx.get("type") != "transfer":
            habit["type"] = tx.get("type")
    if "amount" in fields:
        if fields["amount"] is None:
            habit["amount"] = None
            habit["amount_locked"] = False
        else:
            habit["amount"] = round(float(fields["amount"]), 2)
            if fields.get("amount_locked") or habit.get("source") == "manual":
                habit["amount_locked"] = True
    elif not habit.get("amount_locked"):
        habit["amount"] = typical_amount(
            habit.get("recent_amounts") or [],
            locked_amount=habit.get("amount"),
            amount_locked=False,
        )
    habit["updated_at"] = ts
    return habit


def learn_habits_from_transaction(ledger, tx):
    if not isinstance(tx, dict):
        return []
    if tx.get("source") == "import" or tx.get("type") == "transfer":
        return []
    updated = []
    for phrase in habit_phrases_from_tx(tx):
        updated.append(upsert_habit(ledger, phrase, tx=tx, source="learned"))
    return updated
