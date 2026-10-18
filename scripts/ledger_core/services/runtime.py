"""Portable capability contract. Machine permissions never come from this file."""
import copy
import hashlib
import json

from .profile import runtime_dir


def initial(schema_version):
    return {"version": 1, "schema_version": schema_version, "semantics_version": 2,
            "profile_version": 1, "extensions": [], "custom_fields": {},
            "presentation": {"default": "chat", "formats": ["chat", "json", "csv", "markdown", "html"], "templates": []}}


def validate(value, schema_version):
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Unsupported runtime manifest version")
    if value.get("schema_version") != schema_version or value.get("semantics_version") != 2:
        raise ValueError("Runtime schema/semantics mismatch; migrate before continuing")
    if value.get("profile_version") != 1:
        raise ValueError("Unsupported runtime profile version")
    if not isinstance(value.get("custom_fields"), dict) or not isinstance(value.get("extensions"), list):
        raise ValueError("Invalid runtime capabilities")
    identities = set()
    enabled = set()
    for extension in value["extensions"]:
        from ..extensions.registry import descriptor
        descriptor(extension, schema_version)
        key = (extension["id"], extension["version"])
        if key in identities or extension["enabled"] and extension["id"] in enabled:
            raise ValueError("Duplicate extension version or multiple active versions")
        identities.add(key)
        if extension["enabled"]:
            enabled.add(extension["id"])
    for name, spec in value["custom_fields"].items():
        if not name.startswith("x_") or not isinstance(spec, dict) or spec.get("type") not in ("string", "number", "boolean", "object", "array"):
            raise ValueError("Custom fields must be x_ names with a supported JSON type")
        if spec.get("affects_totals") is not False:
            raise ValueError("Financial semantic changes require a core migration")
    return value


def read(path, schema_version):
    target = runtime_dir(path) / "runtime.json"
    text = target.read_text(encoding="utf-8") if target.exists() else None
    value = json.loads(text) if text is not None else initial(schema_version)
    return validate(value, schema_version), hashlib.sha256(text.encode()).hexdigest() if text is not None else None


def attach(ledger, path):
    ledger.runtime, ledger.runtime_token = read(path, ledger["schema_version"])


def document(ledger, path):
    # Validate against the old schema while migrating; the new value is checked separately.
    target = runtime_dir(path) / "runtime.json"
    text = target.read_text(encoding="utf-8") if target.exists() else None
    token = hashlib.sha256(text.encode()).hexdigest() if text is not None else None
    if token != getattr(ledger, "runtime_token", token):
        raise ValueError("Runtime capabilities changed; reload context")
    value = copy.deepcopy(getattr(ledger, "runtime", initial(ledger["schema_version"])))
    value["schema_version"] = ledger["schema_version"]
    validate(value, ledger["schema_version"])
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"
