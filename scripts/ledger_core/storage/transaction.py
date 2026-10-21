"""Recoverable bundle writes for the ledger and its profile.

The fixed target allowlist prevents a tampered journal from choosing write paths.
All callers hold the ledger lock. Incomplete commits finish before the next read.
"""
from pathlib import Path
from .documents import read_json, atomic_write_json, write_text_secure
from ..services.profile import runtime_dir, profile_path


def targets(path):
    return {"ledger": Path(path), "profile": profile_path(path), "runtime": runtime_dir(path) / "runtime.json"}


def recover(path):
    journal = runtime_dir(path) / "pending.json"
    if not journal.exists():
        return
    bundle = read_json(journal)
    allowed = targets(path)
    if not isinstance(bundle, dict) or not bundle or not set(bundle).issubset(allowed):
        raise ValueError("Invalid pending transaction; manual recovery required")
    for key, value in bundle.items():
        if not isinstance(value, str):
            raise ValueError("Invalid pending transaction payload")
        if key != "profile":
            import json
            json.loads(value)
    for key, value in bundle.items():
        write_text_secure(allowed[key], value)
    journal.unlink()


def commit(path, bundle):
    if not set(bundle).issubset(targets(path)):
        raise ValueError("Unknown bundle target")
    journal = runtime_dir(path) / "pending.json"
    if journal.exists():
        raise ValueError("Recover the previous transaction before committing")
    atomic_write_json(journal, bundle)
    recover(path)
