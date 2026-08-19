#!/usr/bin/env python3
"""JSON document store for Lazy Ledger.

One local file holds named collections of JSON documents. This is the
bookkeeping database: no SQL, no extra packages, no network.
"""
from __future__ import annotations

import copy
import json
import threading
from contextlib import contextmanager
from pathlib import Path


COLLECTIONS = ("transactions", "accounts", "budgets", "categories", "habits")
STORE_KIND = "lazy-ledger-docs"
STORE_VERSION = 1

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

_PATH_LOCKS = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path):
    key = str(Path(path).resolve())
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


@contextmanager
def _flock(handle, exclusive=True):
    if fcntl is None:
        yield
        return
    flag = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    fcntl.flock(handle.fileno(), flag)
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write_json(path, data):
    ledger_path = Path(path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp = ledger_path.with_suffix(ledger_path.suffix + ".tmp")
    with _path_lock(ledger_path):
        with tmp.open("w", encoding="utf-8") as handle:
            with _flock(handle, exclusive=True):
                handle.write(payload)
                handle.flush()
        tmp.replace(ledger_path)


def read_json(path):
    ledger_path = Path(path)
    with _path_lock(ledger_path):
        with ledger_path.open("r", encoding="utf-8") as handle:
            with _flock(handle, exclusive=False):
                return json.load(handle)


class DocumentNotFound(KeyError):
    pass


class Collection:
    def __init__(self, store, name):
        self.store = store
        self.name = name

    def _rows(self):
        rows = self.store.data.setdefault(self.name, [])
        if not isinstance(rows, list):
            rows = []
            self.store.data[self.name] = rows
        return rows

    def all(self):
        return [copy.deepcopy(doc) for doc in self._rows() if isinstance(doc, dict)]

    def get(self, doc_id):
        for doc in self._rows():
            if isinstance(doc, dict) and doc.get("id") == doc_id:
                return copy.deepcopy(doc)
        raise DocumentNotFound(doc_id)

    def find(self, **equals):
        wanted = {key: value for key, value in equals.items() if value is not None}

        def matches(doc):
            return all(doc.get(key) == value for key, value in wanted.items())

        return [copy.deepcopy(doc) for doc in self._rows() if isinstance(doc, dict) and matches(doc)]

    def insert(self, doc, *, commit=True):
        if not isinstance(doc, dict) or not doc.get("id"):
            raise ValueError("Document must be an object with an id")
        rows = self._rows()
        if any(existing.get("id") == doc["id"] for existing in rows if isinstance(existing, dict)):
            raise ValueError(f"Document already exists: {doc['id']}")
        rows.append(copy.deepcopy(doc))
        if commit:
            self.store.commit()
        return copy.deepcopy(doc)

    def update(self, doc_id, fields, *, commit=True):
        rows = self._rows()
        for index, doc in enumerate(rows):
            if isinstance(doc, dict) and doc.get("id") == doc_id:
                updated = copy.deepcopy(doc)
                updated.update(fields)
                updated["id"] = doc_id
                rows[index] = updated
                if commit:
                    self.store.commit()
                return copy.deepcopy(updated)
        raise DocumentNotFound(doc_id)

    def remove(self, doc_id, *, commit=True):
        rows = self._rows()
        for index, doc in enumerate(rows):
            if isinstance(doc, dict) and doc.get("id") == doc_id:
                removed = rows.pop(index)
                if commit:
                    self.store.commit()
                return copy.deepcopy(removed)
        raise DocumentNotFound(doc_id)

    def replace_all(self, docs, *, commit=True):
        self.store.data[self.name] = [copy.deepcopy(doc) for doc in docs if isinstance(doc, dict)]
        if commit:
            self.store.commit()


class DocumentStore:
    """Local JSON document database used by the CLI and the live page."""

    def __init__(self, path, *, create=True, currency="CNY"):
        self.path = Path(path)
        self.currency = currency
        self.create = create
        self.data = self._load()

    def _tool(self):
        import ledger_tool

        return ledger_tool

    def _load(self):
        tool = self._tool()
        if not self.path.exists():
            if not self.create:
                raise FileNotFoundError(self.path)
            data = tool.empty_ledger(self.currency)
        else:
            data = tool.normalize_ledger(read_json(self.path), self.currency)
        data["store"] = STORE_KIND
        data.setdefault("store_version", STORE_VERSION)
        return data

    def reload(self):
        self.data = self._load()
        return self.data

    def commit(self):
        tool = self._tool()
        self.data["store"] = STORE_KIND
        self.data["store_version"] = STORE_VERSION
        self.data["updated_at"] = tool.now_iso()
        atomic_write_json(self.path, self.data)

    def snapshot(self):
        self.reload()
        return copy.deepcopy(self.data)

    def collection(self, name):
        if name not in COLLECTIONS:
            raise ValueError(f"Unknown collection: {name}")
        return Collection(self, name)

    def meta(self):
        skip = set(COLLECTIONS)
        return {key: copy.deepcopy(value) for key, value in self.data.items() if key not in skip}

    def export_json(self, path):
        atomic_write_json(path, self.snapshot())
        return str(Path(path).resolve())
