#!/opt/boron/.venv/bin/python
"""Run A feature 8: export the OpenAPI schema to docs/api/openapi.json.

The schema is a build artifact -- committing it lets it be diffed in code
review (a route added/removed/retyped shows up as a schema change) and read
without standing up the API. Re-run after changing any route or model:

    python scripts/export_openapi.py

It imports the real FastAPI app and dumps app.openapi(), so it always
matches what /api/openapi.json serves.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Importing the app does NOT require the real session secret: the boot-time
# secret check runs in the lifespan hook (only when actually serving), not at
# import, so schema export works with no secrets configured.
from api.main import app  # noqa: E402


def main() -> int:
    out = Path(__file__).resolve().parent.parent / "docs" / "api" / "openapi.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    schema = app.openapi()
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    paths = len(schema.get("paths", {}))
    print(f"wrote {out} ({paths} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
