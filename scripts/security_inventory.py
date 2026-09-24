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
from daemon.rpc_policy import POLICY_BY_OPERATION


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
        path = Path(inspect.getsourcefile(inspect.unwrap(value)) or "").resolve()
        return str(path.relative_to(Path(__file__).resolve().parent.parent))
    except (TypeError, OSError, ValueError):
        return ""


def inventory_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    repo_root = Path(__file__).resolve().parent.parent
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
            "auth_dependencies": "kernel-peer-uid; root-credential; policy=" + POLICY_BY_OPERATION.get(name, "MISSING"),
            "review_status": "pending",
            "test_evidence": "",
        })
    # Root processes and scheduled entry points bypass HTTP entirely. Keep
    # them in the same worklist as routes/RPCs rather than treating OpenAPI
    # coverage as whole-panel coverage.
    for unit in sorted((repo_root / "deploy").glob("*.service")):
        content = unit.read_text().splitlines()
        user = next((line.split("=", 1)[1] for line in content if line.startswith("User=")), "root")
        command = next((line.split("=", 1)[1] for line in content if line.startswith("ExecStart=")), "")
        rows.append({
            "kind": "service", "name": unit.name, "source": str(unit.relative_to(repo_root)),
            "handler": command, "auth_dependencies": f"systemd user={user}",
            "review_status": "pending", "test_evidence": "",
        })
    for cron in sorted((repo_root / "deploy").glob("*.cron")):
        for line_number, line in enumerate(cron.read_text().splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split(None, 6)
            if len(parts) != 7:
                raise ValueError(f"Invalid cron entry at {cron}:{line_number}")
            rows.append({
                "kind": "cron", "name": f"{cron.name}:{line_number}",
                "source": str(cron.relative_to(repo_root)), "handler": parts[6],
                "auth_dependencies": f"cron user={parts[5]}",
                "review_status": "pending", "test_evidence": "",
            })
    for script in sorted((repo_root / "scripts").glob("*.sh")):
        rows.append({
            "kind": "shell-script", "name": script.name,
            "source": str(script.relative_to(repo_root)), "handler": script.name,
            "auth_dependencies": "invocation and effective UID pending review",
            "review_status": "pending", "test_evidence": "",
        })
    for script in sorted((repo_root / "scripts").glob("*.py")):
        rows.append({
            "kind": "python-script", "name": script.name,
            "source": str(script.relative_to(repo_root)), "handler": script.name,
            "auth_dependencies": "invocation and effective UID pending review",
            "review_status": "pending", "test_evidence": "",
        })
    for helper in sorted((repo_root / "daemon").glob("*.c")):
        rows.append({
            "kind": "native-helper", "name": helper.name,
            "source": str(helper.relative_to(repo_root)), "handler": helper.name,
            "auth_dependencies": "setuid/capabilities and caller pending review",
            "review_status": "pending", "test_evidence": "",
        })
    for label, source in (
        ("per-account Node.js unit", "daemon/nodeapps.py"),
        ("per-account Python unit", "daemon/pythonapps.py"),
        ("per-account Redis unit", "daemon/redisacct.py"),
        ("per-account FileBrowser unit", "daemon/filebrowser_accounts.py"),
        ("trusted FileBrowser frontend unit", "daemon/filebrowser_accounts.py"),
    ):
        rows.append({
            "kind": "dynamic-service", "name": label, "source": source,
            "handler": "generated systemd unit", "auth_dependencies": "account owner and unit template pending review",
            "review_status": "pending", "test_evidence": "",
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
    counts = {kind: sum(row["kind"] == kind for row in rows) for kind in sorted({row["kind"] for row in rows})}
    print("Inventoried " + ", ".join(f"{count} {kind}" for kind, count in counts.items()) + ".")


if __name__ == "__main__":
    main()
