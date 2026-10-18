"""Thin adapters: no financial calculations and no captured global stdout."""
import json
from pathlib import Path
from ..services import proposals, imports, observations, reports, migrations


def read(path):
    target = Path(path)
    if target.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Input exceeds 20 MiB")
    return json.loads(target.read_text(encoding="utf-8"))


def dispatch(args):
    try:
        if args.command == "metadata-migrate":
            result = migrations.apply(args.ledger, read(args.input)) if args.apply else migrations.prepare(args.ledger, read(args.input))
        elif args.command == "proposal":
            if args.action == "prepare":
                result = proposals.prepare(args.ledger, read(args.input))
            elif args.action == "commit":
                result = proposals.commit(args.ledger, read(args.input))
            elif args.action == "undo":
                result = proposals.undo(args.ledger, args.operation, apply=args.apply)
            else:
                from ..storage.repository import load
                result = load(args.ledger, create=False).get("changes", [])[-30:]
        elif args.command == "import":
            if args.action == "prepare":
                result = imports.prepare(args.ledger, args.input, provider=args.provider, account=args.account,
                                         expected_count=args.expected_count, expected_total=args.expected_total,
                                         links=read(args.links) if args.links else None, distinct=args.distinct)
            else:
                plan = read(args.input)
                if not plan.get("ready"):
                    raise ValueError("Import plan has unresolved rows or source discrepancies")
                result = proposals.commit(args.ledger, plan["proposal"])
        elif args.command == "observations":
            result = observations.get(args.ledger, refresh=args.refresh)
        else:
            report = reports.read_archive(args.ledger, args.id) if args.action == "read" else reports.build(args.ledger, start=args.start, end=args.end)
            if getattr(args, "archive", False):
                result = reports.archive(args.ledger, report, format=args.format)
            else:
                print(reports.render(report, args.format), end="")
                return
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise SystemExit(str(exc)) from exc


def add_commands(sub):
    def leaf(parent, name, help):
        parser = parent.add_parser(name, help=help)
        parser.add_argument("--ledger", required=True)
        parser.set_defaults(func=dispatch)
        return parser
    group = sub.add_parser("proposal", help="Prepare, commit and undo versioned proposals").add_subparsers(dest="action", required=True)
    for name in ("prepare", "commit"):
        leaf(group, name, name + " a proposal").add_argument("--input", required=True)
    p = leaf(group, "undo", "Preview or apply a targeted undo")
    p.add_argument("--operation", required=True)
    p.add_argument("--apply", action="store_true")
    leaf(group, "history", "Read operation change records")
    group = sub.add_parser("import", help="Prepare reconciled statement imports").add_subparsers(dest="action", required=True)
    p = leaf(group, "prepare", "Parse CSV/TSV and reconcile source identities")
    p.add_argument("--input", required=True)
    p.add_argument("--provider", default=None)
    p.add_argument("--account", default=None)
    p.add_argument("--expected-count", type=int)
    p.add_argument("--expected-total")
    p.add_argument("--links", help="JSON mapping source line to existing transaction ID")
    p.add_argument("--distinct", action="append", default=[], help="Source line confirmed as a distinct purchase")
    leaf(group, "commit", "Commit a ready import plan once").add_argument("--input", required=True)
    p = leaf(sub, "observations", "Read current observations, never executable user rules")
    p.add_argument("--refresh", action="store_true")
    p = leaf(sub, "metadata-migrate", "Preview optional-field migrations; apply the saved plan explicitly")
    p.add_argument("--input", required=True)
    p.add_argument("--apply", action="store_true")
    group = sub.add_parser("report", help="Traceable data and optional archived reports").add_subparsers(dest="action", required=True)
    p = leaf(group, "build", "Build a versioned report from core metrics")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--format", choices=["markdown", "json", "html"], default="markdown")
    p.add_argument("--archive", action="store_true")
    p = leaf(group, "read", "Render an immutable archived data snapshot")
    p.add_argument("--id", required=True)
    p.add_argument("--format", choices=["markdown", "json", "html"], default="markdown")
