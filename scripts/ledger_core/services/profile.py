"""A human editable document is the authority for confirmed user rules.

The fenced JSON section has a versioned contract; prose outside it is preserved.
Derived transaction statistics never rewrite this document.
"""
import copy
import hashlib
import json
import re
from pathlib import Path
from datetime import date

START = "<!-- lazy-ledger-profile:start -->"
END = "<!-- lazy-ledger-profile:end -->"
BLOCK = re.compile(re.escape(START) + r"\s*```json\s*\n(.*?)\n```\s*" + re.escape(END), re.S)


def runtime_dir(ledger_path):
    path = Path(ledger_path)
    return path.parent / (path.stem + "-runtime")


def profile_path(ledger_path):
    return runtime_dir(ledger_path) / "user-profile.md"


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def empty_profile():
    return {"version": 1, "defaults": {"account_id": None}, "merchant_categories": {},
            "merchant_aliases": {}, "habits": [], "communication": {"advice": False}, "goals": []}


def validate(profile):
    if not isinstance(profile, dict) or profile.get("version") != 1:
        raise ValueError("Unsupported user profile version")
    for name in ("defaults", "merchant_categories", "merchant_aliases", "communication"):
        if not isinstance(profile.get(name), dict):
            raise ValueError(f"Profile {name} must be an object")
    if profile["defaults"].get("account_id") is not None and not isinstance(profile["defaults"]["account_id"], str):
        raise ValueError("Default account must be an account ID or null")
    if not isinstance(profile.get("habits"), list) or not isinstance(profile.get("goals"), list):
        raise ValueError("Profile habits and goals must be lists")
    if type(profile["communication"].get("advice", False)) is not bool:
        raise ValueError("Advice preference must be a boolean")
    if 'presentation' in profile:
        presentation = profile['presentation']
        if not isinstance(presentation,dict) or presentation.get('density','normal') not in {'compact','normal','detailed'}:
            raise ValueError('Presentation density must be compact, normal or detailed')
    from ..domain.money import decimal_amount
    phrases = set()
    for habit in profile["habits"]:
        if not isinstance(habit, dict) or not isinstance(habit.get("phrase"), str) or not habit["phrase"].strip():
            raise ValueError("A confirmed habit needs a phrase")
        if habit["phrase"] in phrases:
            raise ValueError("Duplicate profile phrase")
        phrases.add(habit["phrase"])
        if "enabled" in habit and type(habit["enabled"]) is not bool:
            raise ValueError("Rule enabled must be a boolean")
        if habit.get("expires_on"):
            date.fromisoformat(habit["expires_on"])
        if habit.get("amount") is not None and decimal_amount(habit["amount"]) <= 0:
            raise ValueError("Fixed amount must be positive")
    return profile


def read(path):
    target = profile_path(path)
    if not target.exists():
        return empty_profile(), None, ""
    text = target.read_text(encoding="utf-8")
    matches = list(BLOCK.finditer(text))
    if len(matches) != 1:
        raise ValueError("User profile needs exactly one marked JSON block")
    return validate(json.loads(matches[0].group(1))), digest(text), text


def render(profile, previous=""):
    validate(profile)
    block = START + "\n```json\n" + json.dumps(profile, ensure_ascii=False, indent=2) + "\n```\n" + END
    if previous:
        return BLOCK.sub(lambda _: block, previous, count=1)
    return "# 用户记账习惯\n\n以下规则由用户明确设置；统计观察不自动成为规则。\n\n" + block + "\n\n## 用户补充\n\n"


def attach(ledger, path):
    profile, token, text = read(path)
    ledger.profile = profile
    ledger.profile_token = token
    ledger.profile_text = text
    if token is None:
        return
    prefs = ledger["preferences"]
    prefs["default_account_id"] = profile["defaults"].get("account_id")
    for key in ("merchant_categories", "merchant_aliases"):
        prefs[key] = copy.deepcopy(profile[key])
    prefs["communication"] = copy.deepcopy(profile["communication"])
    observed = [h for h in ledger["habits"] if isinstance(h, dict) and h.get("source") != "manual"]
    confirmed = []
    for rule in profile["habits"]:
        item = copy.deepcopy(rule)
        item.update(source="manual", id=item.get("id") or "hab_" + digest(item["phrase"])[:12],
                    amount_locked=item.get("amount") is not None)
        confirmed.append(item)
    ledger["habits"] = observed + confirmed
    ledger.profile_cache = confirmed_fields(ledger)


def confirmed_fields(ledger):
    prefs = ledger["preferences"]
    return {"account_id": prefs.get("default_account_id"),
            "merchant_categories": copy.deepcopy(prefs.get("merchant_categories", {})),
            "merchant_aliases": copy.deepcopy(prefs.get("merchant_aliases", {})),
            "habits": [{k: copy.deepcopy(v) for k,v in h.items() if k in
                        {"id", "phrase", "amount", "amount_locked", "category", "merchant", "account", "account_id", "method", "type", "pinned", "enabled", "expires_on"}}
                       for h in ledger["habits"] if isinstance(h, dict) and h.get("source") == "manual"]}


def updated_document(ledger, path):
    current, token, text = read(path)
    if token != getattr(ledger, "profile_token", token):
        raise ValueError("User profile changed; reload the context before writing")
    if token is None:
        return None
    after = confirmed_fields(ledger)
    if after == getattr(ledger, "profile_cache", after):
        return None
    current["defaults"]["account_id"] = after["account_id"]
    for key in ("merchant_categories", "merchant_aliases", "habits"):
        current[key] = after[key]
    return render(current, text)


def active_rule(rule):
    return rule.get("enabled", True) and (not rule.get("expires_on") or rule["expires_on"] >= date.today().isoformat())
