#!/usr/bin/env python3
"""Print the ten application modules with the lowest statement coverage."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "coverage.json")
    report = json.loads(path.read_text())
    rows = []
    for filename, data in report.get("files", {}).items():
        normalized = filename.removeprefix("./")
        if not normalized.startswith(("api/", "daemon/", "shared/", "scripts/")):
            continue
        summary = data["summary"]
        statements = summary.get("num_statements", 0)
        if statements:
            rows.append((summary.get("percent_covered", 0.0), -statements, normalized, statements))
    print("Ten least-covered application modules:")
    for percent, _negative_statements, filename, statements in sorted(rows)[:10]:
        print(f"  {percent:6.2f}%  {statements:5d} statements  {filename}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
