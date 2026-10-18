import json
from ..extensions import registry
from .workflow_cli import read


def dispatch(args):
    try:
        if args.extension_action == "register":
            result = registry.register(args.ledger, args.manifest)
        elif args.extension_action == "validate":
            result = registry.validate_version(args.ledger, args.id, args.version)
        elif args.extension_action in {"enable", "disable"}:
            result = registry.set_enabled(args.ledger, args.id, args.version, args.extension_action == "enable")
        elif args.extension_action == "run":
            result = registry.run(args.ledger, args.id, args.version, read(args.input))
        else:
            from ..storage.repository import load
            result = [{k:v for k,v in item.items() if k != "bundle"} for item in load(args.ledger, create=False).runtime["extensions"]]
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError, SyntaxError) as exc:
        raise SystemExit(str(exc)) from exc


def add_commands(sub):
    group = sub.add_parser("extension", help="Register and verify isolated converters").add_subparsers(dest="extension_action", required=True)
    for name in ("register", "validate", "enable", "disable", "run", "list"):
        parser = group.add_parser(name)
        parser.add_argument("--ledger", required=True)
        parser.set_defaults(func=dispatch)
        if name == "register":
            parser.add_argument("--manifest", required=True)
        elif name != "list":
            parser.add_argument("--id", required=True)
            parser.add_argument("--version", required=True)
        if name == "run":
            parser.add_argument("--input", required=True)
