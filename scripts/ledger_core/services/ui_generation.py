"""Agent-authored presentation sources, validated without changing financial data."""
import base64
import html
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser

from . import generative_ui as ui


class GenerationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__(json.dumps({"valid": False, "errors": errors}, ensure_ascii=False))


def specification():
    _, assets = ui.asset_bytes()
    return {"renderer": ui.catalog()["renderer"], "catalog": ui.catalog(),
            **json.loads(assets["generation.json"]),
            "html": {"data_global": "window.ledgerData", "source_limit_bytes": 500000,
                     "sandbox": "allow-scripts", "external_resources": False,
                     "review": "Agent must review generated code and compare displayed amounts to snapshot; HTML is not semantically verified"}}


def validate_lang(source, kind):
    if not isinstance(source, str) or len(source.encode()) > 400000:
        raise GenerationError([{"code": "source-size", "message": "OpenUI source exceeds 400000 bytes"}])
    ui.asset_bytes()  # Verify the installed validator before executing it.
    node = shutil.which("node")
    if not node:
        raise GenerationError([{"code": "runtime", "message": "AI OpenUI validation requires Node.js >=18; no npm install is needed. Static formats remain available."}])
    try:
        run = subprocess.run([node, str(ui.ASSETS / "validate.cjs")],
                             input=json.dumps({"lang": source, "kind": kind}), text=True,
                             capture_output=True, timeout=15)
        result = json.loads(run.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        raise GenerationError([{"code": "validator", "message": "OpenUI validator failed or timed out"}]) from exc
    if run.returncode or not result.get("valid"):
        raise GenerationError(result.get("errors", [{"code": "validator", "message": "OpenUI validation failed"}]))
    return result["refs"]


class HtmlSource(HTMLParser):
    """Normalize a document inside a trusted shell; CSP/sandbox are the boundary.

    This rejects obvious external resources, not every possible JS behavior.
    Generated scripts must also be reviewed by the agent before submission.
    """
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts = []
        self.scripts = []
        self.in_script = False
        self.script_parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"base", "iframe", "frame", "frameset", "object", "embed", "link", "meta", "portal"}:
            raise ValueError(f"HTML element {tag} is unavailable; use self-contained content")
        for key, value in attrs:
            value = value or ""
            if key.startswith("on") or key in {"srcdoc", "action", "formaction", "target", "ping", "srcset"}:
                raise ValueError(f"HTML attribute {key} is unavailable; use local script event listeners")
            if key in {"src", "href", "xlink:href", "poster", "data", "background"} and not (
                    value.startswith("#") or (tag == "img" and key == "src" and re.match(r"data:image/(png|jpeg|gif|webp);base64,", value))):
                raise ValueError("HTML resources and links must be local fragments or embedded raster images")
            if tag == "script" and key in {"src", "type"}:
                raise ValueError("Use plain inline scripts, without src or type")
        if tag in {"html", "head", "body"}:
            return
        if tag == "script":
            self.in_script = True
            self.script_parts = []
        self.parts.append(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "script":
            self.scripts.append("".join(self.script_parts))
            self.in_script = False
        if tag not in {"html", "head", "body"}:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        self.parts.append(data)
        if self.in_script:
            self.script_parts.append(data)

    def handle_entityref(self, name):
        self.parts.append(f"&{name};")

    def handle_charref(self, name):
        self.parts.append(f"&#{name};")


def normalize_html(source, view):
    if not isinstance(source, str) or not source.strip() or len(source.encode()) > 500000:
        raise ValueError("HTML source requires 1–500000 bytes")
    parser = HtmlSource()
    parser.feed(source)
    parser.close()
    if parser.in_script:
        raise ValueError("Unclosed HTML script")
    # Include only selected datasets and public provenance, never a full report.
    data = {k: view[k] for k in ("datasets", "currency", "period", "snapshot_id", "revision", "coverage")}
    payload = base64.b64encode(json.dumps(data, ensure_ascii=False).encode()).decode()
    bootstrap = ('const freezeLedgerValue=v=>{if(v&&typeof v==="object"){Object.values(v).forEach(freezeLedgerValue);Object.freeze(v);}return v;};'
                 'Object.defineProperty(window,"ledgerData",{value:freezeLedgerValue(JSON.parse(new TextDecoder().decode('
                 f'Uint8Array.from(atob("{payload}"),c=>c.charCodeAt(0))))),writable:false,configurable:false}});')
    policy = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; font-src 'none'; frame-src 'none'; worker-src 'none'; base-uri 'none'; form-action 'none'"
    doc = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<meta http-equiv="Content-Security-Policy" content="{html.escape(policy, quote=True)}">'
           f'<script>{bootstrap}</script></head><body>' + ''.join(parser.parts) + '</body></html>')
    return doc, [bootstrap, *parser.scripts]


def compose(report, *, lang=None, html_source=None, refs=None):
    if (lang is None) == (html_source is None):
        raise ValueError("Provide exactly one OpenUI or HTML source")
    kind = report.get("kind", "report")
    if lang is not None:
        if refs is not None:
            raise ValueError("OpenUI refs are discovered from the source; do not provide --refs")
        refs = validate_lang(lang, kind)
    elif not refs:
        raise ValueError("HTML requires explicit --refs to select snapshot data")
    view = ui.project(report, refs=refs)
    view["mode"] = "openui" if lang is not None else "html"
    if lang is not None:
        view["lang"] = lang
    else:
        view["html_source"] = html_source
        view["html_document"], view["html_scripts"] = normalize_html(html_source, view)
        view["lang"] = ""
    return view
