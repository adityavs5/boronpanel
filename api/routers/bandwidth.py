"""Phase 7b feature 2: per-account bandwidth graphs + admin cross-account
ranking, built entirely from daemon/usage.py's existing OLS-access-log
parse (Phase 2 feature 5) -- no new data collection, only new bucketing/
ranking views over numbers already independently verifiable against the
same logs an operator would grep by hand.

Charting note (read before reaching for a JS library here): the project
goal for this feature names Chart.js as "already available" -- checked
directly against this repo's `static/` directory before writing any of
this file, and that's not true: only `static/forgehost.css` exists, no
vendored JS at all. The health dashboard (Phase 5 feature 1) already
solved the identical "render a chart" problem under this project's actual
`script-src 'none'` CSP (Security audit finding F10) with server-rendered
inline SVG, no client-side charting library. This module follows that
exact precedent instead of being the first feature to ever loosen CSP or
introduce a vendored JS dependency -- a deliberate, disclosed substitution
in the same spirit as this project's earlier WP-CLI/predis/Chart.js-shaped
decisions, not an oversight.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_admin
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/bandwidth", tags=["bandwidth"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/bandwidth", tags=["ui:bandwidth"])
admin_api_router = APIRouter(prefix="/api/v1/admin/bandwidth", tags=["bandwidth"])
admin_ui_router = APIRouter(prefix="/ui/admin/bandwidth", tags=["ui:bandwidth"])


def _svg_bars(entries: list[dict], width: int = 600, height: int = 160) -> list[dict]:
    """Same server-side-only rendering approach as api/routers/health.py's
    `_svg_polyline` -- plain <rect> geometry computed here, drawn by the
    template with no client JS involved at all."""
    if not entries:
        return []
    values = [e["bytes_served"] for e in entries]
    max_v = max(values) or 1
    n = len(entries)
    bar_width = width / n
    gap = bar_width * 0.15
    bars = []
    for i, entry in enumerate(entries):
        bar_height = (entry["bytes_served"] / max_v) * (height - 20)
        x = i * bar_width + gap / 2
        y = height - bar_height
        bars.append(
            {
                "x": round(x, 1),
                "y": round(y, 1),
                "width": round(max(bar_width - gap, 1), 1),
                "height": round(bar_height, 1),
                "label": entry["label"],
                "bytes_served": entry["bytes_served"],
            }
        )
    return bars


@api_router.get("")
def get_bandwidth(username: str, period: str = "daily", identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("bandwidth.get", identity, username=username, period=period)


@ui_router.get("")
def ui_bandwidth(request: Request, username: str, period: str = "daily", identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    data = call_daemon("bandwidth.get", identity, username=username, period=period)
    bars = _svg_bars(data["buckets"])
    return templates.TemplateResponse(
        request,
        "bandwidth.html",
        {"identity": identity, "username": username, "period": period, "data": data, "bars": bars},
    )


@admin_api_router.get("/ranking")
def get_bandwidth_ranking(period: str = "monthly", identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("bandwidth.ranking", identity, period=period)


@admin_ui_router.get("/ranking")
def ui_bandwidth_ranking(request: Request, period: str = "monthly", identity: Identity = Depends(get_identity)):
    require_admin(identity)
    data = call_daemon("bandwidth.ranking", identity, period=period)
    return templates.TemplateResponse(
        request, "bandwidth_ranking.html", {"identity": identity, "period": period, "data": data}
    )
