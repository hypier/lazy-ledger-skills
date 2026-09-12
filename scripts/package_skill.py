#!/usr/bin/env python3
"""Validate and package the Lazy Ledger skill.

Unlike the generic packager, this one honours `.skillignore` so runtime data
(ledger JSON, backups, generated reports) never ends up in the distributable.

Usage:
    python3 scripts/package_skill.py                 # validate + package to dist/
    python3 scripts/package_skill.py --validate-only # checks only, writes nothing
    python3 scripts/package_skill.py ./dist          # custom output directory
"""
from __future__ import annotations

import fnmatch
import re
import sys
import zipfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
IGNORE_FILE = SKILL_DIR / ".skillignore"
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)
SCALAR_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", re.M)
REF_LINK_RE = re.compile(r"\]\((references/[^)#]+)\)")
REQUIRED_META = ("name", "description")
OPTIONAL_META = ("version", "license")


class ValidationError(Exception):
    pass


def read_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise ValidationError("SKILL.md is missing YAML frontmatter (--- ... ---)")
    return dict(SCALAR_RE.findall(match.group(1)))


def load_ignore_patterns() -> list[str]:
    if not IGNORE_FILE.exists():
        raise ValidationError(".skillignore is missing; refusing to package runtime data")
    patterns = []
    for raw in IGNORE_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def is_ignored(rel_path: str, patterns: list[str]) -> bool:
    parts = rel_path.split("/")
    for raw in patterns:
        pat = raw.rstrip("/")
        if not pat:
            continue
        if rel_path == pat or rel_path.startswith(pat + "/"):
            return True
        # A bare directory name anywhere in the path, e.g. `__pycache__/`.
        if any(seg == pat for seg in parts[:-1]):
            return True
        if any(ch in pat for ch in "*?["):
            if fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(parts[-1], pat):
                return True
            for i in range(1, len(parts)):
                if fnmatch.fnmatch("/".join(parts[:i]), pat):
                    return True
    return False


def validate(skill_dir: Path) -> tuple[str, list[str]]:
    """Return (skill_name, packaged_relative_paths)."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise ValidationError("SKILL.md not found")

    meta = read_frontmatter(skill_md)
    for key in REQUIRED_META:
        value = meta.get(key, "").strip()
        if not value:
            raise ValidationError(f"SKILL.md frontmatter is missing required field: {key}")

    name = meta["name"].strip()
    if name != skill_dir.name:
        raise ValidationError(f"frontmatter name '{name}' does not match directory '{skill_dir.name}'")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
        raise ValidationError(f"name '{name}' must be lowercase hyphen-separated")

    description = meta["description"].strip()
    if len(description) < 40:
        raise ValidationError("description is too short to trigger reliably")
    if not description.startswith(("This skill", "Maintain", "Use ")):
        # Not fatal: just a nudge that the first words carry the trigger signal.
        print(f"  note: description starts with '{description[:24]}...'; lead with the main verb")

    # Every reference linked from SKILL.md must exist.
    body = skill_md.read_text(encoding="utf-8")
    missing = [ref for ref in REF_LINK_RE.findall(body) if not (skill_dir / ref).exists()]
    if missing:
        raise ValidationError("SKILL.md links to missing references: " + ", ".join(missing))

    for rel in ("scripts/ledger", "scripts/ledger_tool.py", "scripts/ledger_audit.py", "scripts/bill_screenshot.py"):
        if not (skill_dir / rel).exists():
            raise ValidationError(f"missing required script: {rel}")

    wrapper = skill_dir / "scripts" / "ledger"
    if not wrapper.stat().st_mode & 0o111:
        raise ValidationError("scripts/ledger is not executable; run: chmod +x scripts/ledger")

    patterns = load_ignore_patterns()
    included = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(skill_dir).as_posix()
        if rel == ".skillignore":
            continue
        if is_ignored(rel, patterns):
            continue
        included.append(rel)

    if not included:
        raise ValidationError("no files left to package; check .skillignore")

    # Guard against accidentally shipping a real ledger.
    leaked = [rel for rel in included if re.search(r"^lazy-ledger.*\.json$", rel)]
    if leaked:
        raise ValidationError("runtime ledger would be packaged: " + ", ".join(leaked))

    return name, included


def package(skill_dir: Path, output_dir: Path) -> tuple[Path, list[str]]:
    name, included = validate(skill_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{name}.skill"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for rel in included:
            archive.write(skill_dir / rel, f"{name}/{rel}")
    return target, included


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("-")]
    validate_only = "--validate-only" in argv[1:]
    output_dir = Path(args[0]).resolve() if args else SKILL_DIR / "dist"

    print("Validating skill...")
    try:
        name, included = validate(SKILL_DIR)
    except ValidationError as exc:
        print(f"FAILED: {exc}")
        return 1
    print(f"OK: {name} ({len(included)} files)")

    if validate_only:
        return 0

    try:
        target, included = package(SKILL_DIR, output_dir)
    except ValidationError as exc:
        print(f"FAILED: {exc}")
        return 1
    print(f"Packaged -> {target}")
    for rel in included:
        print(f"  + {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
