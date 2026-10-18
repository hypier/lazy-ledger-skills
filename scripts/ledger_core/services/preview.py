"""Read-only live previews of agent-authored files over frozen report data."""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from . import generative_ui as ui, ui_generation as generation, reports
from .profile import runtime_dir
from ..storage.documents import atomic_write_json, write_text_secure

# This is a transport shell, not a report layout. Financial content lives in a
# separate opaque iframe with its own document CSP and no parent action bridge.
CLIENT = r'''
const status = document.getElementById('status');
const host = document.getElementById('preview');
let version = 0;
let stopped = false;
async function poll() {
  try {
    const response = await fetch('status', {cache: 'no-store'});
    if (!response.ok) throw new Error('status');
    const state = await response.json();
    if (state.version > version) {
      await new Promise((resolve, reject) => {
        const frame = document.createElement('iframe');
        frame.title = '账本实时预览';
        frame.setAttribute('sandbox', 'allow-scripts');
        frame.referrerPolicy = 'no-referrer';
        frame.className = 'pending';
        let settled = false;
        const timeout = setTimeout(() => finish(false), 10000);
        function finish(ok) {
          if (settled) return;
          settled = true; clearTimeout(timeout);
          if (ok) {
            host.querySelectorAll('iframe').forEach(old => {if (old !== frame) old.remove();});
            frame.className = ''; version = state.version; resolve();
          } else {frame.remove(); reject(new Error('load'));}
        }
        frame.onload = () => finish(true);
        frame.onerror = () => finish(false);
        frame.src = 'view?v=' + state.version;
        host.appendChild(frame);
      });
    }
    status.textContent = state.state === 'invalid'
      ? (version ? '新修改未通过校验，保留上一版。请查看 status.json 并修正源码。' : '等待有效源码，请查看 status.json。')
      : state.state === 'waiting' ? '等待源码保存并通过校验…'
      : state.state === 'checking' ? '正在校验新修改…'
      : state.state === 'stopped' ? '预览服务已停止。'
      : '自动更新已连接 · 预览版本 ' + state.version + ' · 数据快照保持不变';
  } catch {
    status.textContent = '预览连接中断，保留当前页面；正在重连…';
  }
  if (!stopped) setTimeout(poll, 1000);
}
window.addEventListener('pagehide', () => {stopped = true;});
poll();
'''


def shell():
    digest = base64.b64encode(hashlib.sha256(CLIENT.encode()).digest()).decode()
    policy = (f"default-src 'none'; script-src 'sha256-{digest}'; style-src 'unsafe-inline'; "
              "connect-src 'self'; frame-src 'self'; base-uri 'none'; form-action 'none'")
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta http-equiv="Content-Security-Policy" content="{policy}">'
            '<title>账本自动预览</title><style>body{margin:0;font:14px/1.5 system-ui;background:#f7f8fc;color:#263345}'
            '#status{padding:10px 20px;margin:0;border-bottom:1px solid #e2e6ee}iframe{display:block;border:0;width:100%;height:calc(100vh - 44px)}'
            'iframe.pending{position:absolute;left:-100000px;top:0}</style></head><body>'
            '<p id="status" role="status">正在连接预览…</p><main id="preview"></main>'
            f'<script>{CLIENT}</script></body></html>')


class PreviewSession:
    def __init__(self, report, source, directory, *, mode="openui", refs=None):
        reports.check(report)
        if mode not in {"openui", "html"}:
            raise ValueError("Unknown preview mode")
        if mode == "html" and not refs:
            raise ValueError("HTML preview requires --refs")
        if mode == "openui" and refs is not None:
            raise ValueError("OpenUI refs are discovered from source")
        self.report, self.source, self.directory = report, Path(source), Path(directory)
        self.mode, self.refs = mode, refs
        self.directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.observed = None
        self.attempted = None
        self.document = None
        self.status = {"state": "waiting", "version": 0, "snapshot_id": report["id"], "errors": []}
        self._status()

    def _status(self, **changes):
        with self.lock:
            self.status.update(changes)
            atomic_write_json(self.directory / "status.json", self.status)

    def read_source(self):
        with self.source.open('rb') as handle:
            raw = handle.read(500001)
        if len(raw) > 500000:
            raise ValueError("Generated source exceeds 500000 bytes")
        return raw.decode('utf-8'), hashlib.sha256(raw).hexdigest()

    def tick(self):
        """Two matching samples debounce saves; never publish stale validation."""
        try:
            source, digest = self.read_source()
        except (OSError, ValueError, UnicodeError) as exc:
            key = (type(exc).__name__, str(exc))
            if key != self.attempted:
                self.attempted = key
                self._status(state="invalid", errors=[{"code": "source", "message": str(exc)}])
            self.observed = None
            return
        if digest == self.attempted:
            return
        if digest != self.observed:
            self.observed = digest
            return
        self._status(state="checking", errors=[])
        try:
            view = generation.compose(self.report, lang=source if self.mode == 'openui' else None,
                                      html_source=source if self.mode == 'html' else None, refs=self.refs)
            ui.asset_bytes()  # Do not publish a fallback-only build as a successful update.
            document = ui.document(view)
            errors = []
        except (ValueError, OSError, KeyError, TypeError, RecursionError) as exc:
            document = None
            errors = getattr(exc, 'errors', [{"code": "validation", "message": str(exc)}])
        try:
            _, latest = self.read_source()
        except (OSError, ValueError, UnicodeError):
            self.observed = None
            return
        if latest != digest:
            self.observed = latest
            return
        if document is None:
            self.attempted = digest
            self._status(state="invalid", source_digest=digest, errors=errors)
            return
        write_text_secure(self.directory / "preview.html", document)
        with self.lock:
            status = {**self.status, "state": "ready", "version": self.status['version'] + 1,
                      "source_digest": digest, "errors": []}
            atomic_write_json(self.directory / "status.json", status)
            self.document, self.status = document, status
            self.attempted = digest

    def watch(self, interval):
        while not self.stop.wait(interval):
            try:
                self.tick()
            except OSError as exc:
                # A session storage failure must be visible, not silently kill the watcher.
                with self.lock:
                    self.status.update(state="invalid", errors=[{"code": "storage", "message": str(exc)}])

    def snapshot(self):
        with self.lock:
            return dict(self.status)


def server_for(session, port=0):
    token = secrets.token_urlsafe(32)
    prefix = '/' + token + '/'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Do not log the session capability URL or financial content.

        def do_GET(self):
            host = f"127.0.0.1:{self.server.server_port}"
            if (self.headers.get('Host') != host or
                self.headers.get('Origin') not in {None, 'http://' + host} or
                self.headers.get('Sec-Fetch-Site') == 'cross-site'):
                self.send_error(403)
                return
            url = urlsplit(self.path)
            if not url.path.startswith(prefix):
                self.send_error(404)
                return
            route = url.path[len(prefix):]
            if route == '':
                content, content_type = shell(), 'text/html; charset=utf-8'
            elif route == 'status':
                content, content_type = json.dumps(session.snapshot()), 'application/json'
            elif route == 'view':
                with session.lock:
                    content = session.document
                if content is None:
                    self.send_error(503, "Waiting for valid source")
                    return
                content_type = 'text/html; charset=utf-8'
            else:
                self.send_error(404)
                return
            raw = content.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if route != 'view':
                self.send_header('Content-Security-Policy', "frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server, f'http://127.0.0.1:{server.server_port}{prefix}'


def run(ledger, report_id, source, directory, *, mode='openui', refs=None, port=0, interval=0.5):
    if not 0.1 <= interval <= 10:
        raise ValueError("Preview interval must be between 0.1 and 10 seconds")
    if not 0 <= port <= 65535:
        raise ValueError("Invalid preview port")
    target = Path(directory).resolve()
    protected = [Path(ledger).resolve(), runtime_dir(ledger).resolve(), Path(__file__).resolve().parents[3]]
    if any(target == p or p in target.parents for p in protected):
        raise ValueError("Create the preview session outside the ledger, runtime and installed skill")
    source = Path(source).resolve()
    if source == target or target in source.parents:
        raise ValueError('Keep authored source outside the generated session directory')
    session = PreviewSession(reports.read_archive(ledger, report_id), source, target, mode=mode, refs=refs)
    server = None
    worker = None
    try:
        server, url = server_for(session, port)
        atomic_write_json(target / 'session.json', {"url": url, "pid": os.getpid(), "source": str(session.source),
                                                  "snapshot_id": report_id, "mode": mode})
        worker = threading.Thread(target=session.watch, args=(interval,), daemon=True)
        worker.start()
        print(json.dumps({"url": url, "status": str(target / 'status.json'),
                          "preview": str(target / 'preview.html'), "snapshot_id": report_id}), flush=True)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        session.stop.set()
        if server:
            server.server_close()
        if worker:
            worker.join(timeout=17)
        session._status(state='stopped')
