#!/usr/bin/env python3
"""Local localhost server for the live ledger app."""
from __future__ import annotations

import json
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent



def _tool():
    from ledger_core.adapters import api
    return api


MAX_BODY_BYTES = 5_000_000


def _read_json(handler):
    content_type = (handler.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type and content_type != "application/json":
        raise ValueError("unsupported_content_type")
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_content_length") from exc
    if length <= 0:
        return {}
    if length > MAX_BODY_BYTES:
        raise ValueError("request_too_large")
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("invalid_json") from exc


def _send(handler, status, payload=None, body=None, content_type="application/json; charset=utf-8"):
    if body is None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _invoke(func, args):
    try:
        result = func.result(args)
        from ledger_core.services.history import flush_auto_sync
        flush_auto_sync(args.ledger)
        return 200, result if result is not None else {"ok": True}
    except SystemExit as exc:
        return (409 if exc.code == 2 else 400), {"error": str(exc.code)}
    except ValueError as exc:
        return 400, {"error": str(exc)}


def _add_args(ledger_path, body):
    return SimpleNamespace(
        ledger=str(ledger_path),
        operation_id=body.get("operation_id"),
        expected_revision=body.get("expected_revision"),
        text=body.get("text"),
        amount=body.get("amount"),
        type=body.get("type"),
        currency=body.get("currency"),
        category=body.get("category"),
        merchant=body.get("merchant"),
        note=body.get("note"),
        date=body.get("date"),
        occurred_at=body.get("occurred_at"),
        source=body.get("source") or ("text" if body.get("text") else "manual"),
        confidence=body.get("confidence"),
        tags=body.get("tags"),
        attachment=body.get("attachment"),
        method=body.get("method"),
        account=body.get("account"),
        to_account=body.get("to_account"),
        related_transaction_id=body.get("related_transaction_id") or body.get("related_id"),
        relation=body.get("relation"),
        remember=bool(body.get("remember")),
        exclude_from_totals=bool(body.get("exclude_from_totals") or body.get("excluded_from_totals")),
        include_in_totals=bool(body.get("include_in_totals")),
        id=body.get("id"),
        allow_duplicate=bool(body.get("allow_duplicate")),
    )


def _update_args(ledger_path, tx_id, body):
    return SimpleNamespace(
        ledger=str(ledger_path),
        id=tx_id,
        amount=body.get("amount"),
        type=body.get("type"),
        currency=body.get("currency"),
        category=body.get("category"),
        merchant=body.get("merchant"),
        note=body.get("note"),
        date=body.get("date"),
        occurred_at=body.get("occurred_at"),
        source=body.get("source"),
        confidence=body.get("confidence"),
        tags=body.get("tags"),
        attachment=body.get("attachment"),
        method=body.get("method"),
        account=body.get("account"),
        to_account=body.get("to_account"),
        related_transaction_id=body.get("related_transaction_id") or body.get("related_id"),
        relation=body.get("relation"),
        remember=bool(body.get("remember")),
        exclude_from_totals=bool(body.get("exclude_from_totals"))
        or ("excluded_from_totals" in body and bool(body.get("excluded_from_totals"))),
        include_in_totals=bool(body.get("include_in_totals"))
        or ("excluded_from_totals" in body and not bool(body.get("excluded_from_totals"))),
        excluded_from_totals=body.get("excluded_from_totals"),
    )


def make_handler(ledger_path):
    ledger_path = Path(ledger_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

        def _mutation_allowed(self):
            host = urlparse("//" + (self.headers.get("Host") or "")).hostname
            if host not in {"127.0.0.1", "localhost", "::1"}:
                return False
            origin = self.headers.get("Origin")
            if origin:
                parsed = urlparse(origin)
                if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                    return False
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                return False
            return True

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
            tool = _tool()
            if path in {"/", "/settings"}:
                from ledger_core.services import reports
                from datetime import date
                ledger = tool.load_ledger(ledger_path, create=True)
                days = [tx["occurred_at"][:10] for tx in ledger["transactions"]]
                start, end = (min(days), max(days)) if days else (date.today().isoformat(), date.today().isoformat())
                report = reports.from_ledger(ledger, start=start, end=end)
                _send(self, 200, body=reports.render(report, "html").encode(), content_type="text/html; charset=utf-8")
                return
            if path == "/bill" or path.startswith("/bill/"):
                month = None if path == "/bill" else path.rsplit("/", 1)[-1]
                if month:
                    try:
                        tool.validate_month(month)
                    except SystemExit:
                        _send(self, 404, {"error": "not_found"})
                        return
                ledger = tool.load_ledger(ledger_path, create=True)
                _pack, html_path = tool.render_bill_html(ledger, ledger_path, month)
                _send(self, 200, body=html_path.read_bytes(), content_type="text/html; charset=utf-8")
                return
            if path == "/api/health":
                _send(self, 200, {"ok": True, "ledger": str(ledger_path.resolve())})
                return
            if path == "/api/ledger":
                ledger = tool.load_ledger(ledger_path, create=True)
                _send(self, 200, ledger)
                return
            if path == "/api/habits":
                ledger = tool.load_ledger(ledger_path, create=True)
                _send(self, 200, tool.habits_payload(ledger))
                return
            if path == "/api/summary":
                ledger = tool.load_ledger(ledger_path, create=True)
                args = SimpleNamespace(
                    month=query.get("month"),
                    range=query.get("range") or None,
                    start=query.get("start"),
                    end=query.get("end"),
                    query=query.get("query"),
                    type=query.get("type"),
                    category=query.get("category"),
                    method=query.get("method"),
                    account=query.get("account"),
                    compare=query.get("compare") in {"1", "true", "yes"},
                )
                default_range = None if (args.month or args.start or args.end or args.range) else "this-month"
                _send(self, 200, tool.build_summary(ledger, args, default_range=default_range))
                return
            if path == "/api/bill":
                ledger = tool.load_ledger(ledger_path, create=True)
                try:
                    payload = tool.build_bill_pack(ledger, query.get("month"))
                except SystemExit as exc:
                    _send(self, 400, {"error": exc.code or "invalid_month"})
                    return
                _send(self, 200, payload)
                return
            _send(self, 404, {"error": "not_found"})

        def do_POST(self):
            if not self._mutation_allowed():
                _send(self, 403, {"error": "forbidden_origin"})
                return
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            tool = _tool()
            try:
                body = _read_json(self)
            except (json.JSONDecodeError, ValueError):
                _send(self, 400, {"error": "invalid_json"})
                return
            if path == "/api/parse":
                text = (body.get("text") or "").strip()
                if not text:
                    _send(self, 400, {"error": "Text is required"})
                    return
                ledger = tool.load_optional_ledger(ledger_path)
                proposals = tool.parse_text_transactions(text, ledger=ledger)
                payload = proposals[0] if len(proposals) == 1 else {"count": len(proposals), "proposals": proposals}
                _send(self, 200, payload)
                return
            if path == "/api/transactions":
                status, payload = _invoke(tool.add_transaction, _add_args(ledger_path, body))
                _send(self, status, payload)
                return
            if path == "/api/accounts":
                args = SimpleNamespace(
                    ledger=str(ledger_path),
                    name=body.get("name"),
                    type=body.get("type"),
                    opening=body.get("opening", "0"),
                    id=body.get("id"),
                    default=bool(body.get("default")),
                )
                status, payload = _invoke(tool.account_add_command, args)
                _send(self, status, payload)
                return
            if path == "/api/budgets":
                args = SimpleNamespace(
                    ledger=str(ledger_path),
                    amount=body.get("amount"),
                    month=body.get("month"),
                    category=body.get("category"),
                )
                status, payload = _invoke(tool.budget_set_command, args)
                _send(self, status, payload)
                return
            if path == "/api/habits":
                args = SimpleNamespace(
                    ledger=str(ledger_path),
                    phrase=body.get("phrase"),
                    id=body.get("id"),
                    amount=body.get("amount"),
                    clear_amount=bool(body.get("clear_amount")),
                    category=body.get("category"),
                    merchant=body.get("merchant"),
                    account=body.get("account"),
                    type=body.get("type"),
                    method=body.get("method"),
                    pin=bool(body.get("pin")),
                    unpin=bool(body.get("unpin")),
                )
                status, payload = _invoke(tool.habit_set_command, args)
                _send(self, status, payload)
                return
            if path == "/api/bills":
                args = SimpleNamespace(
                    ledger=str(ledger_path),
                    month=body.get("month"),
                    title=body.get("title"),
                    body=body.get("body"),
                )
                status, payload = _invoke(tool.bill_save_command, args)
                _send(self, status, payload)
                return
            if path == "/api/categories":
                args = SimpleNamespace(
                    ledger=str(ledger_path),
                    name=body.get("name"),
                    icon=body.get("icon"),
                    parent=body.get("parent"),
                    old_name=None,
                )
                status, payload = _invoke(tool.category_set_command, args)
                _send(self, status, payload)
                return
            _send(self, 404, {"error": "not_found"})

        def do_PATCH(self):
            if not self._mutation_allowed():
                _send(self, 403, {"error": "forbidden_origin"})
                return
            parsed = urlparse(self.path)
            parts = [part for part in parsed.path.split("/") if part]
            tool = _tool()
            try:
                body = _read_json(self)
            except (json.JSONDecodeError, ValueError):
                _send(self, 400, {"error": "invalid_json"})
                return
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "transactions":
                status, payload = _invoke(tool.update_command, _update_args(ledger_path, parts[2], body))
                _send(self, status, payload)
                return
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "accounts":
                args = SimpleNamespace(
                    ledger=str(ledger_path),
                    id=parts[2],
                    name=body.get("name"),
                    type=body.get("type"),
                    opening=body.get("opening"),
                    archive=bool(body.get("archive")),
                    unarchive=bool(body.get("unarchive")),
                    default=bool(body.get("default")),
                )
                status, payload = _invoke(tool.account_update_command, args)
                _send(self, status, payload)
                return
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "habits":
                args = SimpleNamespace(
                    ledger=str(ledger_path),
                    id=parts[2],
                    phrase=body.get("phrase"),
                    amount=body.get("amount"),
                    clear_amount=bool(body.get("clear_amount")),
                    category=body.get("category"),
                    merchant=body.get("merchant"),
                    account=body.get("account"),
                    type=body.get("type"),
                    method=body.get("method"),
                    pin=bool(body.get("pin")),
                    unpin=bool(body.get("unpin")),
                )
                status, payload = _invoke(tool.habit_set_command, args)
                _send(self, status, payload)
                return
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "categories":
                args = SimpleNamespace(
                    ledger=str(ledger_path),
                    old_name=unquote(parts[2]),
                    name=body.get("name"),
                    icon=body.get("icon"),
                    parent=body.get("parent"),
                )
                status, payload = _invoke(tool.category_set_command, args)
                _send(self, status, payload)
                return
            _send(self, 404, {"error": "not_found"})

        def do_DELETE(self):
            if not self._mutation_allowed():
                _send(self, 403, {"error": "forbidden_origin"})
                return
            parsed = urlparse(self.path)
            parts = [part for part in parsed.path.split("/") if part]
            tool = _tool()
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "transactions":
                args = SimpleNamespace(ledger=str(ledger_path), id=parts[2], yes=True)
                status, payload = _invoke(tool.delete_command, args)
                _send(self, status, payload)
                return
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "budgets":
                args = SimpleNamespace(ledger=str(ledger_path), id=parts[2])
                status, payload = _invoke(tool.budget_delete_command, args)
                _send(self, status, payload)
                return
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "habits":
                args = SimpleNamespace(ledger=str(ledger_path), id=parts[2], phrase=None)
                status, payload = _invoke(tool.habit_delete_command, args)
                _send(self, status, payload)
                return
            _send(self, 404, {"error": "not_found"})

    return Handler


def run_server(ledger_path, host="127.0.0.1", port=8765, open_browser=False, block=True):
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("Serve only on localhost. Pass 127.0.0.1.")

    class Server(ThreadingHTTPServer):
        allow_reuse_address = True

    last_error = None
    server = None
    chosen = port
    for candidate in range(port, port + 12):
        try:
            server = Server((host, candidate), make_handler(ledger_path))
            chosen = candidate
            break
        except OSError as exc:
            last_error = exc
    if server is None:
        raise OSError(last_error or f"No free port near {port}")
    display_host = host if host not in {"localhost", "::1"} else host
    if ":" in display_host and not display_host.startswith("["):
        display_host = f"[{display_host}]"
    url = f"http://{display_host}:{chosen}/"
    print(json.dumps({"url": url, "ledger": str(Path(ledger_path).resolve())}, ensure_ascii=False), flush=True)
    if open_browser:
        webbrowser.open(url)
    if not block:
        import threading

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return server
