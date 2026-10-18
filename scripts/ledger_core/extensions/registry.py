"""Portable code bundles and separate device-local validation permissions."""
import json
import re
from pathlib import Path
from . import runner
from ..services.profile import runtime_dir
from ..storage import repository
from ..storage.documents import atomic_write_json, write_text_secure, ledger_lock
from ..services import proposals


def digest(bundle):
    return repository.fingerprint(bundle)


def descriptor(item, schema_version):
    if not isinstance(item, dict) or item.get("kind") != "converter" or item.get("protocol") != 1:
        raise ValueError("Unsupported extension protocol")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", item.get("id", "")) or not re.fullmatch(r"\d+\.\d+\.\d+", item.get("version", "")):
        raise ValueError("Extension needs a stable ID and semantic version")
    bundle = item.get("bundle", {})
    if not isinstance(bundle, dict):
        raise ValueError("Invalid extension bundle")
    if item.get("digest") != digest(bundle):
        raise ValueError("Extension bundle digest mismatch")
    if bundle.get("permissions") != {"data": "input-only", "network": False, "writes": "scratch-only"}:
        raise ValueError("Extensions may only read provided input and write disposable scratch data")
    if schema_version not in bundle.get("core_schema", []):
        raise ValueError("Extension is incompatible with the ledger schema")
    if bundle.get("dependencies", []) not in ([], ["stdlib"]):
        raise ValueError("This runner supports self-contained Python standard-library converters")
    if item.get("state") not in {"candidate", "validated", "enabled", "disabled"}:
        raise ValueError("Unknown extension state")
    if bool(item.get("enabled")) != (item["state"] == "enabled"):
        raise ValueError("Extension enabled state mismatch")
    script = bundle.get("script")
    if not isinstance(script, str) or len(script.encode()) > 64 * 1024:
        raise ValueError("Converter source must be at most 64 KiB")
    fixtures = bundle.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures or len(json.dumps(fixtures).encode()) > 128 * 1024:
        raise ValueError("Invalid or oversized extension fixtures")
    if any(not isinstance(f, dict) or "input" not in f or "expected" not in f for f in fixtures):
        raise ValueError("Fixtures need input and expected output")
    try:
        compile(script, "extension.py", "exec")
    except SyntaxError as exc:
        raise ValueError("Extension source has a syntax error") from exc
    return item


def target(path, item):
    root = runtime_dir(path) / "extensions"
    result = root / item["id"] / item["version"] / "main.py"
    if root.is_symlink() or not result.resolve().is_relative_to(root.resolve()):
        raise ValueError("Extension paths must stay inside the ledger extension directory")
    return result


def register(path, manifest_path):
    location = Path(manifest_path)
    data = json.loads(location.read_text(encoding="utf-8"))
    if data.get("entrypoint") != "main.py":
        raise ValueError("Converter entrypoint must be main.py beside its manifest")
    entry = location.parent / "main.py"
    if entry.is_symlink() or entry.stat().st_size > 64 * 1024:
        raise ValueError("Invalid extension source")
    bundle = {k: data[k] for k in ("permissions", "core_schema", "fixtures")}
    bundle.update(script=entry.read_text(encoding="utf-8"), dependencies=data.get("dependencies", []), source=data.get("source", "user-authored"))
    if not isinstance(bundle["fixtures"], list) or not bundle["fixtures"] or len(json.dumps(bundle["fixtures"]).encode()) > 128 * 1024:
        raise ValueError("Extension needs bounded input/output fixtures")
    item = {"id": data["id"], "version": data["version"], "kind": "converter", "protocol": 1,
            "bundle": bundle, "digest": digest(bundle), "state": "candidate", "enabled": False}
    with ledger_lock(path):
        ledger = repository.load(path, create=False)
        descriptor(item, ledger["schema_version"])
        existing = next((x for x in ledger.runtime["extensions"] if x["id"] == item["id"] and x["version"] == item["version"]), None)
        if existing:
            if existing["digest"] == item["digest"]:
                return existing
            raise ValueError("An extension version is immutable; register a new version")
        write_text_secure(target(path, item), bundle["script"])
        ledger.runtime["extensions"].append(item)
        repository.save(path, ledger)
    return {k:v for k,v in item.items() if k != "bundle"}


def find(ledger, extension_id, version):
    found = next((x for x in ledger.runtime["extensions"] if x["id"] == extension_id and x["version"] == version), None)
    if not found:
        raise ValueError("Extension version is not registered")
    descriptor(found, ledger["schema_version"])
    return found


def device_settings(path):
    target_path = runtime_dir(path) / "extensions-local.json"
    return json.loads(target_path.read_text()) if target_path.exists() else {}


def checked_script(path, item, *, materialize=False):
    file = target(path, item)
    if not file.exists() and materialize:
        write_text_secure(file, item["bundle"]["script"])
    if not file.exists() or file.is_symlink() or file.read_text(encoding="utf-8") != item["bundle"]["script"]:
        raise ValueError("Extension source changed or is missing; register and validate a new version")
    return item["bundle"]["script"]


def output(value):
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("candidates"), list):
        raise ValueError("Converter output must be candidates-v1")
    if any(not isinstance(row, dict) for row in value["candidates"]):
        raise ValueError("Each converter candidate must be an object")
    return value


def validate_version(path, extension_id, version):
    ledger = repository.load(path, create=False)
    item = find(ledger, extension_id, version)
    script = checked_script(path, item, materialize=True)
    for fixture in item["bundle"]["fixtures"]:
        actual = output(runner.execute(script, fixture["input"]))
        if actual != fixture["expected"]:
            raise ValueError("Extension fixture output differs from expected data")
        operations = [{"action": "add", "after": proposals.normalize_row(ledger, row)} for row in actual["candidates"]]
        proposals.apply_to_copy(ledger, operations)
    with ledger_lock(path):
        current = repository.load(path, create=False)
        live = find(current, extension_id, version)
        if proposals.context_token(current) != proposals.context_token(ledger):
            raise ValueError("Context changed during extension validation")
        checked_script(path, live)
        live.update(state="validated", enabled=False)
        device = device_settings(path)
        device[extension_id + "@" + version] = {"validated": item["digest"], "enabled": False}
        atomic_write_json(runtime_dir(path) / "extensions-local.json", device)
        repository.save(path, current)
    return {"id": extension_id, "version": version, "state": "validated", "fixtures": len(item["bundle"]["fixtures"])}


def set_enabled(path, extension_id, version, enabled):
    with ledger_lock(path):
        ledger = repository.load(path, create=False)
        item = find(ledger, extension_id, version)
        device = device_settings(path)
        key = extension_id + "@" + version
        if enabled:
            checked_script(path, item)
            if not runner.available() or device.get(key, {}).get("validated") != item["digest"]:
                raise ValueError("Validate this exact bundle on this device before enabling it")
            # Upgrading chooses exactly one active version of a converter.
            for other in ledger.runtime["extensions"]:
                if other["id"] == extension_id:
                    other.update(state="disabled", enabled=False)
                    device.get(extension_id + "@" + other["version"], {}).update(enabled=False)
        item.update(state="enabled" if enabled else "disabled", enabled=enabled)
        device.setdefault(key, {}).update(enabled=enabled)
        atomic_write_json(runtime_dir(path) / "extensions-local.json", device)
        repository.save(path, ledger)
    return {"id": extension_id, "version": version, "enabled": enabled}


def run(path, extension_id, version, payload):
    ledger = repository.load(path, create=False)
    item = find(ledger, extension_id, version)
    grant = device_settings(path).get(extension_id + "@" + version, {})
    if not item["enabled"] or not grant.get("enabled") or grant.get("validated") != item["digest"]:
        raise ValueError("Extension is not enabled and validated on this device")
    script = checked_script(path, item)
    result = output(runner.execute(script, payload))
    current = repository.load(path, create=False)
    if proposals.context_token(current) != proposals.context_token(ledger):
        raise ValueError("Context changed during extension execution")
    checked_script(path, item)
    candidates = []
    for row in result["candidates"]:
        candidate = dict(row)
        candidate["field_evidence"] = {key: {"kind": "extension", "id": extension_id, "version": version,
                                           "digest": item["digest"], "input_hash": digest(payload)} for key in row}
        candidates.append({"action": "add", "after": candidate})
    return proposals.prepare(path, {"reason": "Converter " + extension_id + "@" + version,
                                    "operations": candidates, "evidence": {"extension": item["digest"], "input_hash": digest(payload)}},
                             expected_context=proposals.context_token(ledger))
