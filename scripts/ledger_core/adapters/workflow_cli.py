"""Thin adapters: no financial calculations and no captured global stdout."""
import json
from pathlib import Path
from ..services import proposals, imports, observations, reports, migrations


def read(path):
    target = Path(path)
    if target.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Input exceeds 20 MiB")
    return json.loads(target.read_text(encoding="utf-8"))


def generation_dispatch(args):
    """Generation uses saved snapshots, so a layout revision cannot change facts."""
    import copy
    import uuid
    from ..services import generative_ui as ui, ui_generation as generation
    from ..storage import repository
    from ..storage.documents import write_text_secure
    from ..services.profile import runtime_dir
    if args.action == "ui-spec":
        print(json.dumps(generation.specification(), ensure_ascii=False, indent=2))
        return
    if args.action == "watch":
        from ..services import preview
        preview.run(args.ledger, args.id, args.openui or args.html_source, args.session_dir,
                    mode="openui" if args.openui else "html", refs=args.refs, port=args.port, interval=args.interval)
        return
    report = reports.read_archive(args.ledger, args.id)
    if args.action == "data":
        kind = report.get("kind", "report")
        refs = args.refs if args.refs is not None else [ref for spec in ui.catalog()["components"].values() if kind in spec["kinds"] for ref in spec["refs"]]
        view = ui.project(report, refs=refs)
        view.pop("lang")
        view.pop("layout")
        print(json.dumps(view, ensure_ascii=False, indent=2))
        return
    if getattr(args, "archive", False):
        report = copy.deepcopy(report)
        report["source_snapshot_id"] = report["id"]
        report["id"] = "report_" + uuid.uuid4().hex
        report["digest"] = repository.fingerprint({k: v for k, v in report.items() if k != "digest"})
    path = Path(args.openui or args.html_source)
    if path.stat().st_size > 500000:
        raise ValueError("Generated source exceeds 500000 bytes")
    source = path.read_text(encoding="utf-8")
    view = generation.compose(report, lang=source if args.openui else None,
                              html_source=source if args.html_source else None, refs=args.refs)
    if args.action == "validate":
        print(json.dumps({"valid": True, "mode": view["mode"], "refs": list(view["datasets"]),
                          "snapshot_id": report["id"], "semantic_review_required": view["mode"] == "html"}, ensure_ascii=False))
        return
    if args.archive:
        if args.output or args.replace:
            raise ValueError("Use either --archive or --output; archives cannot be replaced")
        print(json.dumps(reports.archive(args.ledger, report, format=args.format, view=view), ensure_ascii=False))
        return
    output = ui.document(view) if args.format == "openui" else json.dumps(view, ensure_ascii=False, indent=2) + "\n"
    if args.replace and (not args.output or args.format != "openui"):
        raise ValueError("--replace requires --output and openui format")
    if args.output:
        target = Path(args.output)
        resolved = target.resolve()
        protected = [Path(args.ledger).resolve(), runtime_dir(args.ledger).resolve(), Path(__file__).resolve().parents[3]]
        if any(resolved == p or p in resolved.parents for p in protected):
            raise ValueError("Write generated previews outside the ledger, runtime and installed skill")
        if target.exists():
            if not args.replace:
                raise ValueError("Output already exists; use a new path or --replace for an existing preview")
            # Only replace our preview container, never arbitrary user files.
            import re
            import base64
            match = re.search(r'<script id="ledger-view"[^>]*>([^<]+)</script>', target.read_text(encoding="utf-8"))
            try:
                prior = json.loads(base64.b64decode(match[1])) if match else {}
            except (ValueError, TypeError):
                prior = {}
            if not str(prior.get("renderer", "")).startswith("ledger-openui-"):
                raise ValueError("--replace only accepts an existing Lazy Ledger preview")
        write_text_secure(target, output)
        print(json.dumps({"artifact": str(resolved), "mode": view["mode"], "snapshot_id": report["id"]}, ensure_ascii=False))
    else:
        print(output, end="")


def dispatch(args):
    try:
        if args.command == "report" and args.action in {"ui-spec", "data", "compose", "validate", "watch"}:
            generation_dispatch(args)
            return
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
                                         links=read(args.links) if args.links else None, distinct=args.distinct, confirmed_image=args.confirmed_image)
            else:
                plan = read(args.input)
                if not plan.get("ready"):
                    raise ValueError("Import plan has unresolved rows or source discrepancies")
                result = proposals.commit(args.ledger, plan["proposal"])
        elif args.command == "observations":
            result = observations.get(args.ledger, refresh=args.refresh)
        else:
            layout = read(args.layout) if getattr(args, "layout", None) else None
            if args.action == "read":
                output = reports.render_archive(args.ledger, args.id, args.format)
                report = None
            else:
                report = reports.from_import(read(args.input)) if args.action == "import-preview" else reports.build(args.ledger, start=args.start, end=args.end)
            if getattr(args, "archive", False):
                if args.output:
                    raise ValueError("Use either --archive or --output")
                result = reports.archive(args.ledger, report, format=args.format, layout=layout)
            else:
                if report is not None:
                    output = reports.render(report, args.format, layout=layout)
                if args.output:
                    from ..storage.documents import write_text_secure
                    target = Path(args.output)
                    if target.exists():
                        raise ValueError("Output already exists; choose a new artifact path")
                    write_text_secure(target, output)
                    print(json.dumps({"artifact": str(target.resolve())}, ensure_ascii=False))
                else:
                    print(output, end="")
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
    p.add_argument("--confirmed-image", action="store_true", help="User has checked the OCR rows")
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
    p.add_argument("--format", choices=["markdown", "json", "html", "openui", "view-json"], default="markdown")
    p.add_argument("--layout", help="Validated JSON component/ref composition for OpenUI")
    p.add_argument("--output", help="Write to a new artifact file")
    p.add_argument("--archive", action="store_true")
    p = leaf(group, "import-preview", "View a saved import plan without committing it")
    p.add_argument("--input", required=True)
    p.add_argument("--format", choices=["markdown", "json", "html", "openui", "view-json"], default="markdown")
    p.add_argument("--layout")
    p.add_argument("--output")
    p.add_argument("--archive", action="store_true")
    p = leaf(group, "read", "Render an immutable archived data snapshot")
    p.add_argument("--id", required=True)
    p.add_argument("--format", choices=["markdown", "json", "html", "openui", "view-json"], default="markdown")
    p.add_argument("--output", help="Write to a new artifact file")

    p = group.add_parser("ui-spec", help="Read the installed component schema and AI generation instructions")
    p.add_argument("--ledger", help="Accepted for wrapper compatibility; no ledger is read")
    p.set_defaults(func=dispatch)
    p = leaf(group, "data", "Export selected public datasets from a saved snapshot")
    p.add_argument("--id", required=True)
    p.add_argument("--refs", nargs="+")
    for name in ("compose", "validate", "watch"):
        p = leaf(group, name, {"validate": "Validate AI-authored source against an archived snapshot",
                               "compose": "Render an AI-authored page from a frozen snapshot",
                               "watch": "Watch authored source and automatically update a local preview"}[name])
        p.add_argument("--id", required=True, help="Source report snapshot ID")
        sources = p.add_mutually_exclusive_group(required=True)
        sources.add_argument("--openui", help="AI-authored OpenUI Lang file")
        sources.add_argument("--html-source", help="AI-authored self-contained HTML file")
        p.add_argument("--refs", nargs="+", help="HTML only: explicitly selected data references")
        if name == "compose":
            p.add_argument("--format", choices=["openui", "view-json"], default="openui")
            p.add_argument("--output")
            p.add_argument("--archive", action="store_true")
            p.add_argument("--replace", action="store_true", help="Update an existing preview outside authoritative storage")

        if name == "watch":
            p.add_argument("--session-dir", required=True, help="New private directory for preview and diagnostics")
            p.add_argument("--port", type=int, default=0, help="Loopback port; 0 selects an available port")
            p.add_argument("--interval", type=float, default=0.5, help="File sampling interval in seconds")
