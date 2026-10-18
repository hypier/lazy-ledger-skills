"""Version-bound, inspectable changes; only this service applies proposals."""
import copy
import json
import uuid
from pathlib import Path
from ..domain.model import now_iso
from ..domain.money import decimal_amount
from ..domain.validation import validate
from ..storage import repository
from ..storage.documents import ledger_lock, atomic_write_json
from .profile import runtime_dir

COLLECTIONS = {"transactions", "accounts", "budgets", "categories", "bills", "review_queue", "imports"}


def context_token(ledger):
    return {"ledger_id": ledger["ledger_id"], "revision": ledger["revision"], "data": ledger.read_token,
            "profile": ledger.profile_token, "runtime": ledger.runtime_token}


def normalize_row(ledger, row):
    value = copy.deepcopy(row)
    value.setdefault("id", "tx_" + uuid.uuid4().hex)
    value.setdefault("type", "expense")
    value["amount"] = float(decimal_amount(value.get("amount")))
    value.setdefault("currency", ledger["currency"])
    value.setdefault("category", "收入" if value["type"] == "income" else "其他")
    value.setdefault("source", "manual")
    value.setdefault("confidence", 1.0)
    value.setdefault("tags", [])
    value.setdefault("created_at", now_iso())
    value.setdefault("updated_at", value["created_at"])
    if not value.get("occurred_at"):
        raise ValueError("A proposal must supply the transaction date")
    for id_key, name_key in (("account_id", "account"), ("to_account_id", "to_account")):
        if value.get(name_key) and not value.get(id_key):
            matches = [a for a in ledger["accounts"] if value[name_key] in (a.get("id"), a.get("name"))]
            if len(matches) != 1:
                raise ValueError("Unknown or ambiguous account: " + value[name_key])
            value[id_key] = matches[0]["id"]
        if value.get(id_key):
            account = next((a for a in ledger["accounts"] if a.get("id") == value[id_key]), None)
            if account:
                value[name_key] = account["name"]
    for field, val in value.items():
        if field.startswith("x_"):
            spec = ledger.runtime["custom_fields"].get(field)
            types = {"string": str, "number": (int, float), "boolean": bool, "object": dict, "array": list}
            if not spec or not isinstance(val, types[spec["type"]]) or (spec["type"] == "number" and type(val) is bool):
                raise ValueError("Unregistered or invalid custom field: " + field)
    return value


def apply_to_copy(ledger, operations):
    result = copy.deepcopy(ledger)
    touched = set()
    for op in operations:
        collection = op.get("collection", "transactions")
        if collection not in COLLECTIONS or op.get("action") not in {"add", "update", "delete"}:
            raise ValueError("Unsupported proposal operation")
        rows = result.setdefault(collection, [])
        row_id = op.get("id") or (op.get("after") or {}).get("id")
        if not isinstance(row_id, str) or not row_id or (collection, row_id) in touched:
            raise ValueError("Each row may occur only once in a proposal")
        touched.add((collection, row_id))
        before = next((r for r in rows if r.get("id") == row_id), None)
        if op["action"] == "add":
            if before is not None:
                raise ValueError("Proposed ID already exists")
        elif before is None or before != op.get("before"):
            raise ValueError("Row changed since the proposal was prepared")
        if before is not None:
            rows.remove(before)
        if op["action"] != "delete":
            after = copy.deepcopy(op["after"])
            if after.get("id") != row_id:
                raise ValueError("An operation cannot change row identity")
            if collection == "transactions":
                after = normalize_row(result, after)
            rows.append(after)
    validate(result)
    return result


def prepare(path, request, *, persist=True, expected_context=None):
    ledger = repository.load(path, create=False)
    if expected_context is not None and context_token(ledger) != expected_context:
        raise ValueError("Context changed while preparing the proposal")
    if not isinstance(request.get("reason"), str) or not request["reason"].strip():
        raise ValueError("A proposal needs a reason")
    operations = []
    for item in request.get("operations", []):
        op = copy.deepcopy(item)
        collection = op.setdefault("collection", "transactions")
        if op.get("action") == "add":
            if collection == "transactions":
                op["after"] = normalize_row(ledger, op.get("after", {}))
            else:
                op.setdefault("after", {}).setdefault("id", "row_" + uuid.uuid4().hex)
            op["id"] = op["after"]["id"]
        else:
            row = next((r for r in ledger.get(collection, []) if r.get("id") == op.get("id")), None)
            if row is None:
                raise ValueError("Proposal target does not exist")
            if "before" in op and op["before"] != row:
                raise ValueError("Proposal target changed")
            op["before"] = copy.deepcopy(row)
            if op.get("action") == "update":
                edits = {**op.pop("set", {}), **op.get("after", {})}
                op["after"] = {**row, **edits}
                for name, identity in (("account", "account_id"), ("to_account", "to_account_id")):
                    if name in edits and identity not in edits:
                        op["after"].pop(identity, None)
                if not request.get("undo_of"):
                    op["after"]["updated_at"] = now_iso()
                for field in op.pop("unset", []):
                    if field in {"id", "amount", "type", "occurred_at", "currency"}:
                        raise ValueError("Cannot remove a required financial field")
                    op["after"].pop(field, None)
                if collection == "transactions":
                    op["after"] = normalize_row(ledger, op["after"])
        operations.append(op)
    candidate = apply_to_copy(ledger, operations)
    proposal = {"version": 1, "id": "proposal_" + uuid.uuid4().hex, "context": context_token(ledger),
                "reason": request["reason"], "operations": operations, "evidence": request.get("evidence", {}),
                "undo_of": request.get("undo_of"), "created_at": now_iso(),
                "preview": {"before_count": len(ledger["transactions"]), "after_count": len(candidate["transactions"])}}
    proposal["digest"] = repository.fingerprint(proposal)
    if persist:
        target = runtime_dir(path) / "work" / "proposals" / (proposal["id"] + ".json")
        atomic_write_json(target, proposal)
    return proposal


def commit(path, proposal):
    if proposal.get("version") != 1:
        raise ValueError("Unsupported proposal version")
    digest = repository.fingerprint({k: v for k, v in proposal.items() if k != "digest"})
    if digest != proposal.get("digest"):
        raise ValueError("Proposal changed; prepare a new preview")
    with ledger_lock(path):
        ledger = repository.load(path, create=False)
        previous = ledger.get("operations", {}).get(proposal["id"])
        if previous:
            if previous.get("request_hash") != digest:
                raise ValueError("Proposal identity reused with different content")
            return previous["result"]
        if proposal["context"] != context_token(ledger):
            raise ValueError("Proposal context is stale; regenerate it")
        if not proposal["operations"]:
            return {"operation_id": proposal["id"], "revision": ledger["revision"], "changed": 0, "saved": False}
        result = apply_to_copy(ledger, proposal["operations"])
        receipt = {"operation_id": proposal["id"], "revision": ledger["revision"] + 1,
                   "changed": len(proposal["operations"]), "saved": True}
        result.setdefault("operations", {})[proposal["id"]] = {"request_hash": digest, "result": receipt}
        result.operation = {"id": proposal["id"], "reason": proposal["reason"],
                            "evidence": proposal["evidence"], "undo_of": proposal.get("undo_of")}
        repository.save(path, result)
        return {**receipt, "history": getattr(result, "history_status", None)}


def undo(path, operation_id, *, apply=False):
    ledger = repository.load(path, create=False)
    change = next((c for c in ledger.get("changes", []) if c.get("id") == operation_id), None)
    if not change or not change.get("changes"):
        raise ValueError("No row change to undo for that operation")
    if any(c.get("undo_of") == operation_id for c in ledger.get("changes", [])):
        raise ValueError("Operation was already undone")
    operations = []
    for delta in change["changes"]:
        current = next((r for r in ledger.get(delta["collection"], []) if r.get("id") == delta["id"]), None)
        if current != delta["after"]:
            raise ValueError("Affected row changed later; prepare a coordinated correction")
        action = "delete" if delta["before"] is None else "add" if delta["after"] is None else "update"
        operations.append({"action": action, "collection": delta["collection"], "id": delta["id"],
                           "before": current, "after": delta["before"]})
    proposal = prepare(path, {"reason": "Undo operation " + operation_id, "undo_of": operation_id, "operations": operations})
    return commit(path, proposal) if apply else proposal
