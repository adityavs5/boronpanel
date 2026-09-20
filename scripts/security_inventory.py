"""Inventory registered HTTP/WebSocket routes and root-daemon RPC operations.

Run with a disposable BORON_CONFIG/BORON_SECRETS configuration. This only
imports route and operation definitions; it does not start either service.
The output is a coverage starting point, not an authorization verdict.
"""
from __future__ import annotations

import csv
import inspect
import sys
from pathlib import Path

from fastapi.routing import APIRoute, APIWebSocketRoute, _IncludedRouter

from api.main import app
from daemon.server import OP_TABLE


def _dependencies(dependant) -> list[str]:
    if dependant is None:
        return []
    names: list[str] = []
    for dependency in dependant.dependencies:
        names.append(getattr(dependency.call, "__name__", repr(dependency.call)))
        names.extend(_dependencies(dependency))
    return sorted(set(names))


def _source(value) -> str:
    try:
        path = Path(inspect.getsourcefile(value) or "").resolve()
        return str(path.relative_to(Path(__file__).resolve().parent.parent))
    except (TypeError, OSError, ValueError):
        return ""


def inventory_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in app.routes:
        contexts = item.effective_route_contexts() if isinstance(item, _IncludedRouter) else [item]
        for route in contexts:
            original = getattr(route, "original_route", route)
            if not isinstance(original, (APIRoute, APIWebSocketRoute)):
                continue
            endpoint = route.endpoint or original.endpoint
            deps = _dependencies(route.dependant or getattr(original, "dependant", None))
            if endpoint.__name__ in ("proxy", "terminal_ws") and not deps:
                deps = ["manual-auth-inside-handler"]
            methods = sorted(route.methods or {"WEBSOCKET"})
            path = route.path or original.path
            for method in methods:
                rows.append({
                    "kind": "route",
                    "name": f"{method} {path}",
                    "source": _source(endpoint),
                    "handler": endpoint.__name__,
                    "auth_dependencies": ",".join(deps),
                    "review_status": "pending",
                    "test_evidence": "",
                })
    for name, handler in sorted(OP_TABLE.items()):
        rows.append({
            "kind": "rpc",
            "name": name,
            "source": _source(handler),
            "handler": getattr(handler, "__name__", repr(handler)),
            "auth_dependencies": "kernel-peer-uid; operation-policy-pending-review",
            "review_status": "pending",
            "test_evidence": "",
        })
    return sorted(rows, key=lambda row: (row["kind"], row["name"], row["source"]))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: security_inventory.py OUTPUT.csv")
    rows = inventory_rows()
    with open(sys.argv[1], "w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Inventoried {sum(r['kind'] == 'route' for r in rows)} routes and "
          f"{sum(r['kind'] == 'rpc' for r in rows)} RPC operations.")


if __name__ == "__main__":
    main()
