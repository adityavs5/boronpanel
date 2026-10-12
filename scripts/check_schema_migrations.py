#!/usr/bin/env python3
"""Static policy gate for Boron schema revisions."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.migrations import CURRENT_SCHEMA_VERSION, REVISIONS

_IDENTIFIER = r'["`\[]?([A-Za-z_][A-Za-z0-9_]*)["`\]]?'


def scan_revision_source(source: str) -> list[str]:
    """Reject a same-release add/drop or add/rename contraction."""
    additions = {
        match.group(1).lower()
        for match in re.finditer(rf"\bADD\s+COLUMN\s+{_IDENTIFIER}", source, re.IGNORECASE)
    }
    drops = {
        match.group(1).lower()
        for match in re.finditer(rf"\bDROP\s+COLUMN\s+{_IDENTIFIER}", source, re.IGNORECASE)
    }
    rename_sources = {
        match.group(1).lower()
        for match in re.finditer(
            rf"\bRENAME\s+COLUMN\s+{_IDENTIFIER}\s+TO\s+{_IDENTIFIER}",
            source,
            re.IGNORECASE,
        )
    }
    problems = []
    for column in sorted(additions & drops):
        problems.append(f"column {column!r} is added and dropped in the same release")
    for column in sorted(additions & rename_sources):
        problems.append(f"column {column!r} is added and renamed in the same release")
    return problems


def validate_registry() -> list[str]:
    problems = []
    numbers = [revision.revision for revision in REVISIONS]
    if numbers != list(range(1, CURRENT_SCHEMA_VERSION + 1)):
        problems.append("revision numbers must be contiguous and end at CURRENT_SCHEMA_VERSION")
    if any(revision.compatibility not in {"additive", "breaking"} for revision in REVISIONS):
        problems.append("every revision must declare additive or breaking compatibility")
    return problems


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    requested = list(sys.argv[1:] if argv is None else argv)
    paths = [Path(item) for item in requested] or [root / "shared/migrations.py"]
    problems = validate_registry()
    for path in paths:
        try:
            source = path.read_text()
        except OSError as exc:
            problems.append(f"cannot read revision source {path}: {exc}")
            continue
        problems.extend(f"{path}: {problem}" for problem in scan_revision_source(source))
    if problems:
        for problem in problems:
            print(f"schema migration policy: {problem}", file=sys.stderr)
        return 1
    print(f"Schema migration policy passed ({len(REVISIONS)} ordered revision(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
