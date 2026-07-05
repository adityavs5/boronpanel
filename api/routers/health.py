"""Phase 5 feature 1: server health dashboard. Admin-only -- host-wide
infrastructure telemetry is not a per-account resource, and exposing raw
system metrics (mount points, live process/network counters) to hosting
customers isn't something any other admin-only feature in this project
does either (matches backups.py/tokens.py's admin_router precedent).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/health", tags=["health"])
ui_router = APIRouter(prefix="/ui/health", tags=["ui:health"])


@api_router.get("")
def get_health(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("health.get", identity)


@api_router.get("/history")
def get_health_history(hours: int = 24, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("health.history", identity, hours=hours)


def _svg_polyline(values: list[float], width: int = 600, height: int = 100) -> str:
    """Renders a plain inline SVG polyline server-side -- no client JS
    charting library, so this works unmodified under the project's
    existing `script-src 'none'` CSP (Security audit finding F10)."""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    n = len(values)
    step = width / max(n - 1, 1)
    points = []
    for i, v in enumerate(values):
        x = i * step
        y = height - ((v - lo) / span) * height
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


@ui_router.get("")
def ui_health(request: Request, hours: int = 24, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    live = call_daemon("health.get", identity)
    history = call_daemon("health.history", identity, hours=hours)["points"]
    namespace = call_daemon("namespace.health_summary", identity)

    uptime_seconds = live["uptime_seconds"]
    days, rem = divmod(uptime_seconds, 86400)
    hrs, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    live["uptime_display"] = f"{days}d {hrs}h {mins}m"

    cpu_points = _svg_polyline([p["cpu_pct"] for p in history])
    mem_points = _svg_polyline(
        [(p["mem_used_bytes"] / p["mem_total_bytes"] * 100) if p["mem_total_bytes"] else 0 for p in history]
    )
    net_rx_points = _svg_polyline([p["net_rx_delta"] for p in history])
    net_tx_points = _svg_polyline([p["net_tx_delta"] for p in history])

    return templates.TemplateResponse(
        request,
        "health.html",
        {
            "identity": identity,
            "live": live,
            "history": history,
            "hours": hours,
            "cpu_points": cpu_points,
            "mem_points": mem_points,
            "net_rx_points": net_rx_points,
            "net_tx_points": net_tx_points,
            "namespace": namespace,
        },
    )
