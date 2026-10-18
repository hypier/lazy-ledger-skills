"""Revision checked JSON persistence. Reads never initialize files."""
import hashlib
import json
import uuid
import copy
import subprocess
from pathlib import Path

from .documents import ledger_lock, read_json
from ..domain.model import empty_ledger, normalize_ledger, now_iso
from ..services import profile, runtime
from . import transaction


class ConflictError(ValueError):
    pass


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Ledger(dict):
    """Transient read token is an attribute and never persisted as user data."""


def load(path, currency="CNY", create=True):
    path = Path(path)
    if not path.exists() and not create:
        raise FileNotFoundError(path)
    def read():
        raw = read_json(path) if path.exists() else None
        ledger = Ledger(normalize_ledger(copy.deepcopy(raw), currency) if raw is not None else empty_ledger(currency))
        ledger.read_token = fingerprint(raw) if raw is not None else None
        profile.attach(ledger, path)
        runtime.attach(ledger, path)
        return ledger
    if path.exists() or (profile.runtime_dir(path) / "pending.json").exists():
        with ledger_lock(path):
            transaction.recover(path)
            return read()
    ledger = read()
    return ledger


def save(path, ledger):
    path = Path(path)
    with ledger_lock(path):
        transaction.recover(path)
        current = read_json(path) if path.exists() else None
        if hasattr(ledger, "read_token"):
            actual = fingerprint(current) if current is not None else None
            if actual != ledger.read_token:
                raise ConflictError("Ledger changed since it was read; reload before writing")
        elif current is not None and ledger.get("revision", 0) != current.get("revision", 0):
            raise ConflictError("Stale ledger revision")
        normalize_ledger(ledger)
        ledger.setdefault("ledger_id", (current or {}).get("ledger_id") or "ledger_" + uuid.uuid4().hex)
        ledger["revision"] = (current or {}).get("revision", 0) + 1
        ledger["updated_at"] = now_iso()
        document = profile.updated_document(ledger, path)
        if hasattr(ledger, "profile_override"):
            document = profile.render(ledger.profile_override, getattr(ledger, "profile_text", ""))
        runtime_document = runtime.document(ledger, path)
        if current is None and document is None and not profile.profile_path(path).exists():
            initial = profile.empty_profile()
            fields = profile.confirmed_fields(ledger)
            initial["defaults"]["account_id"] = fields.pop("account_id")
            initial.update(fields)
            document = profile.render(initial)
        changes = []
        for collection in ("transactions", "accounts", "budgets", "categories", "bills", "review_queue", "imports"):
            before = {r.get("id"): r for r in (current or {}).get(collection, []) if isinstance(r, dict) and r.get("id")}
            after = {r.get("id"): r for r in ledger.get(collection, []) if isinstance(r, dict) and r.get("id")}
            for key in sorted(before.keys() | after.keys()):
                if before.get(key) != after.get(key):
                    changes.append({"collection": collection, "id": key, "before": copy.deepcopy(before.get(key)), "after": copy.deepcopy(after.get(key))})
        ledger.setdefault("changes", []).append({**getattr(ledger, "operation", {}),
            "id": getattr(ledger, "operation", {}).get("id") or "op_" + uuid.uuid4().hex,
            "revision": ledger["revision"], "at": ledger["updated_at"], "changes": changes})
        bundle = {"ledger": json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", "runtime": runtime_document}
        if document is not None:
            bundle["profile"] = document
        transaction.commit(path, bundle)
        if hasattr(ledger, "read_token"):
            ledger.read_token = fingerprint(ledger)
            profile.attach(ledger, path)
            runtime.attach(ledger, path)
        from ..services import history
        try:
            status = history.record(path)
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            status = {"state": "pending", "message": str(exc)}
        if isinstance(ledger, Ledger):
            ledger.history_status = status
