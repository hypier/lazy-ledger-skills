"""CLI adapter for context, profile, migration and optional Git history."""
import json
import uuid
from pathlib import Path

from ..services import profile, runtime, history
from ..storage import repository
from ..storage.documents import ledger_lock, write_text_secure
from ..domain.model import SCHEMA_VERSION


def context(path, task):
    ledger = repository.load(path, create=False)
    from ..services.observations import get
    observations = get(path)
    return {"ledger_id": ledger.get("ledger_id"), "revision": ledger["revision"],
            "schema_version": ledger["schema_version"], "task": task,
            "profile": ledger.profile, "profile_path": str(profile.profile_path(path).resolve()),
            "profile_present": ledger.profile_token is not None, "profile_token": ledger.profile_token,
            "capabilities": {**ledger.runtime, "extensions": [
                {**{k:v for k,v in item.items() if k != "bundle"},
                 "permissions": item["bundle"]["permissions"], "core_schema": item["bundle"]["core_schema"]}
                for item in ledger.runtime["extensions"]]}, "runtime_token": ledger.runtime_token,
            "coverage": {"transactions": len(ledger["transactions"]),
                         "unassigned_accounts": sum(not tx.get("account_id") for tx in ledger["transactions"] if isinstance(tx, dict)),
                         "unknown_openings": [a["id"] for a in ledger["accounts"] if not a.get("opening_confirmed")]},
            "observations": {"authoritative": False, "data": observations},
            "git": {"preference": ledger.get("git_preference", "undecided"), **history.settings(path), "status": history.sync_status(path)},
            "needs_setup": ["git_preference"] if ledger.get("git_preference", "undecided") == "undecided" else []}


def migrate(path, apply=False):
    with ledger_lock(path):
        ledger = repository.load(path, create=False)
        result = {"from": ledger["schema_version"], "to": SCHEMA_VERSION, "applied": False,
                  "notes": ["Unknown fields are preserved", "Legacy default account requires user confirmation",
                            "Old inferred prices are observations only", "Historical written-off rows retain legacy date semantics"]}
        if ledger["schema_version"] == SCHEMA_VERSION and ledger.profile_token is not None:
            result["needed"] = False
            return result
        if not apply:
            return result
        backup = profile.runtime_dir(path) / "backups" / ("migration-" + uuid.uuid4().hex)
        backup.mkdir(parents=True)
        for name, source in {"ledger.json": Path(path), "user-profile.md": profile.profile_path(path),
                             "runtime.json": profile.runtime_dir(path) / "runtime.json"}.items():
            if source.exists():
                write_text_secure(backup / name, source.read_text(encoding="utf-8"))
        if ledger.profile_token is None:
            rules = profile.empty_profile()
            fields = profile.confirmed_fields(ledger)
            fields.pop("account_id")  # old defaults have no evidence of user confirmation
            rules.update(fields)
            ledger.profile_override = rules
            ledger["preferences"]["default_account_id"] = None
        ledger["schema_version"] = SCHEMA_VERSION
        ledger.runtime["schema_version"] = SCHEMA_VERSION
        repository.save(path, ledger)
        result.update(applied=True, backup=str(backup), revision=ledger["revision"])
        return result


def dispatch(args):
    try:
        if args.command == "history" and args.history_command == "sync":
            resolution = json.loads(Path(args.resolution).read_text()) if args.resolution else None
            print(json.dumps(history.sync(args.ledger, resolution), ensure_ascii=False, indent=2))
            return
        with ledger_lock(args.ledger):
            if args.command == "context":
                result = context(args.ledger, args.task)
            elif args.command == "migrate":
                result = migrate(args.ledger, args.apply)
            elif args.command == "profile":
                ledger = repository.load(args.ledger, create=False)
                if args.profile_command == "show":
                    result = {"path": str(profile.profile_path(args.ledger)), "rules": ledger.profile, "token": ledger.profile_token}
                else:
                    if ledger.profile_token != args.expected_token:
                        raise ValueError("Profile changed; show it again before applying")
                    rules = profile.validate(json.loads(Path(args.input).read_text(encoding="utf-8")))
                    account_id = rules["defaults"].get("account_id")
                    if account_id and not any(a.get("id") == account_id and not a.get("archived") for a in ledger["accounts"]):
                        raise ValueError("Default account is unknown or archived")
                    ledger.profile_override = rules
                    repository.save(args.ledger, ledger)
                    result = {"saved": True, "revision": ledger["revision"], "path": str(profile.profile_path(args.ledger))}
            elif args.command == "schema":
                ledger = repository.load(args.ledger, create=False)
                if args.schema_command == "show":
                    result = ledger.runtime
                else:
                    spec = {"type": args.type, "description": args.description, "affects_totals": False}
                    old = ledger.runtime["custom_fields"].get(args.name)
                    if old and old != spec:
                        raise ValueError("Changing a registered field needs an explicit migration")
                    ledger.runtime["custom_fields"][args.name] = spec
                    runtime.validate(ledger.runtime, ledger["schema_version"])
                    repository.save(args.ledger, ledger)
                    result = {"registered": args.name, "revision": ledger["revision"]}
            else:
                action = args.history_command
                if action == "configure":
                    ledger = repository.load(args.ledger, create=False)
                    result = history.configure(args.ledger, args.mode, args.remote, args.private_remote_confirmed)
                    ledger["git_preference"] = args.mode
                    repository.save(args.ledger, ledger)
                    result["history"] = getattr(ledger, "history_status", None)
                elif action == "list":
                    result = history.list_history(args.ledger)
                elif action == "save":
                    result = history.record(args.ledger)
                elif action == "restore":
                    result = history.restore(args.ledger, args.commit, args.apply)
                else:
                    result = history.sync(args.ledger)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from exc


def add_commands(sub):
    def leaf(parent, name, help):
        parser = parent.add_parser(name, help=help)
        parser.add_argument("--ledger", required=True)
        parser.set_defaults(func=dispatch)
        return parser
    p = leaf(sub, "context", "Load the shared profile and capability context")
    p.add_argument("--task", choices=["record", "import", "budget", "analyze", "report", "audit"], default="record")
    p = leaf(sub, "migrate", "Preview a version migration; apply explicitly")
    p.add_argument("--apply", action="store_true")
    group = sub.add_parser("profile", help="Confirmed user rules").add_subparsers(dest="profile_command", required=True)
    leaf(group, "show", "Read the authoritative profile")
    p = leaf(group, "apply", "Apply a validated JSON profile while preserving document prose")
    p.add_argument("--input", required=True)
    p.add_argument("--expected-token", default=None)
    group = sub.add_parser("schema", help="Shared optional field registry").add_subparsers(dest="schema_command", required=True)
    leaf(group, "show", "Read the capability contract")
    p = leaf(group, "register", "Register a nonfinancial x_ metadata field")
    p.add_argument("--name", required=True)
    p.add_argument("--type", choices=["string", "number", "boolean", "object", "array"], required=True)
    p.add_argument("--description", required=True)
    group = sub.add_parser("history", help="Optional isolated Git history").add_subparsers(dest="history_command", required=True)
    p = leaf(group, "configure", "Choose local history or a confirmed private remote")
    p.add_argument("--mode", choices=["off", "local", "remote"], required=True)
    p.add_argument("--remote", default=None)
    p.add_argument("--private-remote-confirmed", action="store_true")
    leaf(group, "list", "List recent snapshots")
    leaf(group, "save", "Retry recording the current saved state")
    p = leaf(group, "sync", "Merge independent records; stop on conflicting edits")
    p.add_argument("--resolution", help="Conflict plan with explicit local/remote choices")
    p = leaf(group, "restore", "Preview restoration; applying creates a new commit")
    p.add_argument("--commit", required=True)
    p.add_argument("--apply", action="store_true")
