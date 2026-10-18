"""Additional ledger workflows; outputs are returned to adapters."""
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from ledger_db import write_text_secure
from ledger_core.domain.model import now_iso, empty_ledger, ledger_preferences
from ledger_core.domain.metrics import compact_candidate
from ledger_core.domain.semantics import remaining_amount

from ledger_core.domain.vocabulary import (VALID_ADVANCE_STATES)
from ledger_core.services.common import advance_remaining, apply_advance_settlement, find_transaction, load_ledger, mutating, parse_when, save_ledger
from ledger_core.services.habits import (habits_payload, rebuild_learned_habits, refresh_usage_profile, seed_habits_if_needed, upsert_habit, write_habit_memory)
from ledger_core.services.inspections import build_review_queue


@mutating
def prefer_command(args):
    ledger = load_ledger(args.ledger, create=True)
    prefs = ledger_preferences(ledger)
    if args.merchant and args.category:
        prefs["merchant_categories"][args.merchant] = args.category
        upsert_habit(
            ledger,
            args.merchant,
            source="manual",
            merchant=args.merchant,
            category=args.category,
        )
    if args.alias and args.merchant:
        prefs["merchant_aliases"][args.alias] = args.merchant
    if not ((args.merchant and args.category) or (args.alias and args.merchant)):
        raise SystemExit("Provide --merchant with --category, and/or --alias with --merchant")
    save_ledger(args.ledger, ledger)
    return prefs


@mutating
def review_resolve_command(args):
    ledger = load_ledger(args.ledger, create=True)
    queue = build_review_queue(ledger, include_handled=True)
    if any(item.get('key') == args.id for item in queue):
        resolved_key = args.id
        tx = {'id':None}
    else:
        if not args.code: raise ValueError("Provide --code or use the complete finding key as --id")
        _index, tx = find_transaction(ledger, args.id)
        resolved_key = f"{args.code}:{tx.get('id')}"
    if not any(item.get("key") == resolved_key for item in queue):
        queue.append(
            {
                "key": resolved_key,
                "code": args.code,
                "transaction_id": tx.get("id"),
                "status": "open",
            }
        )
    for item in queue:
        if item.get("key") == resolved_key:
            from ledger_core.services.findings import transition
            transition(item, getattr(args, "status", "resolved"), args.note or "", getattr(args, "until", None))
    ledger["review_queue"] = queue
    save_ledger(args.ledger, ledger)
    return {"resolved": resolved_key, "note": args.note or ""}


@mutating
def advance_settle_command(args):
    ledger = load_ledger(args.ledger, create=True)
    _index, advance = find_transaction(ledger, args.id)
    if advance.get("advance") not in VALID_ADVANCE_STATES:
        raise SystemExit(f"Transaction {args.id} is not an advance")
    by_id = args.by
    if by_id:
        _income_index, income = find_transaction(ledger, by_id)
        if income.get("type") != "income":
            raise SystemExit(f"Transaction {by_id} is not income")
        if apply_advance_settlement(advance, income) is None:
            raise SystemExit(f"Transaction {by_id} is already recorded as settling this advance")
    else:
        raise SystemExit("Settlement needs --by with the actual repayment transaction")
    advance["updated_at"] = now_iso()
    save_ledger(args.ledger, ledger)
    payload = compact_candidate(advance)
    payload["advance"] = advance.get("advance")
    payload["remaining"] = advance_remaining(advance)
    return payload


@mutating
def advance_writeoff_command(args):
    ledger = load_ledger(args.ledger, create=True)
    _index, advance = find_transaction(ledger, args.id)
    if advance.get("advance") not in VALID_ADVANCE_STATES:
        raise SystemExit(f"Transaction {args.id} is not an advance")
    if advance.get("advance") != "open":
        raise SystemExit("Only an open advance can be written off")
    loss = float(remaining_amount(advance))
    if loss <= 0:
        raise SystemExit("No outstanding amount to write off")
    occurred_at = parse_when(args.date) if getattr(args, "date", None) else now_iso()
    if occurred_at[:10] < advance.get("occurred_at", "")[:10]:
        raise SystemExit("Writeoff cannot precede the advance")
    row = {"id": "tx_" + uuid.uuid4().hex, "type": "expense", "amount": loss,
           "currency": advance.get("currency"), "category": "坏账损失", "occurred_at": occurred_at,
           "created_at": now_iso(), "updated_at": now_iso(), "balance_effect": False,
           "relation": "writeoff", "related_transaction_id": advance["id"],
           "source": "manual", "note": "垫付核销", "confidence": 1.0, "tags": []}
    advance.update(advance="written_off", written_off_amount=loss,
                   writeoff_transaction_id=row["id"], updated_at=now_iso())
    ledger["transactions"].append(row)
    save_ledger(args.ledger, ledger)
    return {"advance": compact_candidate(advance), "loss": row}


@mutating
def habit_rebuild_command(args):
    ledger = load_ledger(args.ledger, create=True)
    rebuild_learned_habits(ledger)
    ledger_preferences(ledger)["habits_seeded"] = True
    save_ledger(args.ledger, ledger)
    write_habit_memory(args.ledger, ledger, force=True)
    return habits_payload(ledger)


@mutating
def review_refresh_command(args):
    ledger = load_ledger(args.ledger, create=True)
    queue = build_review_queue(ledger, include_handled=True)
    known = {item.get("key"): item.get("evidence") for item in ledger.get("review_queue") or [] if isinstance(item, dict)}
    items = [item for item in queue if known.get(item["key"]) != item.get("evidence")]
    ledger["review_queue"] = queue
    save_ledger(args.ledger, ledger)
    open_items = [item for item in queue if item.get("status") == "open"]
    return {"added": len(items), "open": open_items, "open_count": len(open_items)}


@mutating
def habit_profile_command(args):
    ledger = load_ledger(args.ledger, create=True)
    seed_habits_if_needed(ledger)
    summary = (args.summary or "").strip() or None
    refresh_usage_profile(ledger, force=True, summary=summary)
    save_ledger(args.ledger, ledger)
    write_habit_memory(args.ledger, ledger, force=True, summary=summary)
    payload = habits_payload(ledger)
    return payload


@mutating
def habit_memory_command(args):
    ledger = load_ledger(args.ledger, create=True)
    seed_habits_if_needed(ledger)
    summary = (getattr(args, "summary", None) or "").strip() or None
    refresh_usage_profile(ledger, force=True, summary=summary)
    save_ledger(args.ledger, ledger)
    path, written = write_habit_memory(args.ledger, ledger, force=True, summary=summary)
    return {
                "path": str(path.resolve()),
                "written": written,
                "tx_count": len(ledger.get("transactions") or []),
                "summary": (ledger_preferences(ledger).get("usage_profile") or {}).get("summary"),
            }


def backup_command(args):
    src = Path(args.ledger)
    if not src.is_file():
        raise SystemExit(f"Ledger not found: {src}")
    try:
        text = src.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"Could not read ledger {src}: {exc}") from exc
    if args.output:
        dest = Path(args.output)
        if dest.exists() and not getattr(args, "force", False):
            raise SystemExit(f"Backup already exists: {dest}. Pass --force to overwrite it.")
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = src.parent / "backups" / f"{src.stem}-{stamp}{src.suffix or '.json'}"
        counter = 1
        while dest.exists():
            dest = dest.with_name(f"{src.stem}-{stamp}-{counter}{src.suffix or '.json'}")
            counter += 1
    write_text_secure(dest, text)
    return {"ok": True, "ledger": str(src.resolve()), "backup": str(dest.resolve())}


@mutating
def init_command(args):
    path = Path(args.ledger)
    if path.exists() and not args.force:
        return {"path":str(path.resolve()), "exists":True}
    if path.exists():
        backup = path.parent / "backups" / f"{path.stem}-preinit-{datetime.now().strftime('%Y%m%d-%H%M%S')}{path.suffix or '.json'}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        counter = 1
        while backup.exists():
            backup = backup.with_name(f"{path.stem}-preinit-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{counter}{path.suffix or '.json'}")
            counter += 1
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)
    from ledger_core.services import profile, runtime, history
    if path.exists():
        fresh = load_ledger(path)
        identity, revision, git_mode = fresh.get("ledger_id"), fresh["revision"], fresh.get("git_preference", "undecided")
        for name, source in {"user-profile.md": profile.profile_path(path), "runtime.json": profile.runtime_dir(path) / "runtime.json"}.items():
            if source.exists():
                write_text_secure(backup.with_name(backup.name + "." + name), source.read_text(encoding="utf-8"))
        fresh.clear()
        fresh.update(empty_ledger(args.currency))
        fresh.update(revision=revision, git_preference=git_mode)
        if identity:
            fresh["ledger_id"] = identity
        fresh.profile_override = profile.empty_profile()
        fresh.runtime = runtime.initial(fresh["schema_version"])
    else:
        fresh = empty_ledger(args.currency)
    chosen_mode = getattr(args, "git_mode", "undecided")
    if chosen_mode != "undecided":
        fresh["git_preference"] = chosen_mode
        if chosen_mode == "off":
            history.configure(path, "off")
    save_ledger(path, fresh)
    if chosen_mode == "local":
        history.configure(path, "local")
        history.record(path)
    return {"path":str(path.resolve()), "backup":str(backup) if "backup" in locals() else None}
