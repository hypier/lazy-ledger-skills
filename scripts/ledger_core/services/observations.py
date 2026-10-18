"""Derived patterns have their own cache and can never grant user permissions."""
from collections import Counter
from ..domain.semantics import counts_as
from ..storage import repository
from ..storage.documents import read_json, atomic_write_json
from .profile import runtime_dir


def token(ledger):
    return repository.fingerprint({"transactions": ledger["transactions"], "profile": ledger.profile_token,
                                   "runtime": ledger.runtime_token})


def build(ledger):
    expenses = [r for r in ledger["transactions"] if counts_as(r, "expense")]
    merchants = Counter(r.get("merchant") for r in expenses if r.get("merchant"))
    return {"version": 1, "input_token": token(ledger), "revision": ledger["revision"], "authoritative": False,
            "transaction_count": len(ledger["transactions"]),
            "category_counts": dict(Counter(r.get("category", "其他") for r in expenses)),
            "frequent_merchants": [{"merchant": name, "count": count} for name, count in merchants.most_common(12)],
            "unassigned_count": sum(not r.get("account_id") for r in ledger["transactions"]),
            "rule_suggestions": []}


def get(path, *, refresh=False):
    ledger = repository.load(path, create=False)
    target = runtime_dir(path) / "observations.json"
    if target.exists() and not refresh:
        try:
            cached = read_json(target)
            if cached.get("version") == 1 and cached.get("input_token") == token(ledger):
                return cached
        except (ValueError, OSError, AttributeError):
            pass  # a corrupt derived cache does not corrupt authoritative data
    result = build(ledger)
    if refresh:
        atomic_write_json(target, result)
    return result
