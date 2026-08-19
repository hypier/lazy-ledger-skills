#!/usr/bin/env python3
"""Local localhost server for the live ledger app."""
from __future__ import annotations

import json
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
APP_HTML = SKILL_DIR / "assets" / "ledger-app.html"


def _tool():
    import ledger_tool

    return ledger_tool


def _store(path):
    from ledger_db import DocumentStore

    return DocumentStore(path, create=True)


def _read_json(handler):
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _send(handler, status, payload=None, body=None, content_type="application/json; charset=utf-8"):
    if body is None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _invoke(func, args):
    import io
    from contextlib import redirect_stderr, redirect_stdout

    out = io.StringIO()
    err = io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            func(args)
    except SystemExit as exc:
        code = exc.code
        stderr = err.getvalue().strip()
        stdout = out.getvalue().strip()
        if code == 2:
            try:
                return 409, json.loads(stderr)
            except json.JSONDecodeError:
                return 409, {"error": stderr or "likely_duplicate"}
        message = code if isinstance(code, str) else (stderr or stdout or "request_failed")
        return 400, {"error": message}
    text = out.getvalue().strip()
    if not text:
        return 200, {"ok": True}
    try:
        return 200, json.loads(text)
    except json.JSONDecodeError:
        return 200, {"ok": True, "output": text}


def _add_args(ledger_path, body):
    return SimpleNamespace(
        ledger=str(ledger_path),
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
    )


def make_handler(ledger_path):
    ledger_path = Path(ledger_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
            tool = _tool()
            if path == "/":
                _send(self, 200, body=APP_HTML.read_bytes(), content_type="text/html; charset=utf-8")
                return
            if path == "/api/health":
                _send(self, 200, {"ok": True, "ledger": str(ledger_path.resolve())})
                return
            if path == "/api/ledger":
                _send(self, 200, _store(ledger_path).snapshot())
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
            _send(self, 404, {"error": "not_found"})

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            tool = _tool()
            try:
                body = _read_json(self)
            except json.JSONDecodeError:
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
            _send(self, 404, {"error": "not_found"})

        def do_PATCH(self):
            parsed = urlparse(self.path)
            parts = [part for part in parsed.path.split("/") if part]
            tool = _tool()
            try:
                body = _read_json(self)
            except json.JSONDecodeError:
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
            _send(self, 404, {"error": "not_found"})

        def do_DELETE(self):
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
    url = f"http://127.0.0.1:{chosen}/"
    print(json.dumps({"url": url, "ledger": str(Path(ledger_path).resolve())}, ensure_ascii=False))
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
