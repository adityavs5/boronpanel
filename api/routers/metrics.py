"""Optional, localhost-only OpenMetrics endpoint without tenant labels."""
from __future__ import annotations

import ipaddress

import psutil
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select

from shared.config import settings
from shared.db import read_session
from shared.models import ControlTask, LoginFailureEvent, utcnow

api_router = APIRouter(tags=["monitoring"])


def _local_peer(request: Request) -> bool:
    try:
        return bool(request.client and ipaddress.ip_address(request.client.host).is_loopback)
    except ValueError:
        return False


def _line(name: str, value: int | float, help_text: str, metric_type: str = "gauge") -> list[str]:
    return [f"# HELP {name} {help_text}", f"# TYPE {name} {metric_type}", f"{name} {value}"]


@api_router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
def openmetrics(request: Request):
    if not settings.openmetrics_enabled:
        raise HTTPException(status_code=404, detail="Not found")
    if not _local_peer(request):
        raise HTTPException(status_code=403, detail="OpenMetrics is available only from localhost")

    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    cpu_count = psutil.cpu_count() or 1
    load1 = psutil.getloadavg()[0]
    with read_session() as db:
        active_tasks = int(db.scalar(select(func.count()).select_from(ControlTask).where(
            ControlTask.status.in_(("queued", "running"))
        )) or 0)
        recent_failures = int(db.scalar(select(func.count()).select_from(LoginFailureEvent).where(
            LoginFailureEvent.occurred_at >= utcnow().replace(second=0, microsecond=0)
        )) or 0)

    lines: list[str] = []
    lines += _line("boron_host_memory_used_ratio", round(memory.percent / 100, 6), "Fraction of host memory in use.")
    lines += _line("boron_host_disk_free_ratio", round(disk.free / disk.total, 6) if disk.total else 0, "Fraction of root filesystem bytes free.")
    lines += _line("boron_host_load1_per_cpu", round(load1 / cpu_count, 6), "One-minute host load divided by logical CPU count.")
    lines += _line("boron_control_tasks_active", active_tasks, "Queued or running control-plane tasks.")
    lines += _line("boron_login_failures_current_minute", recent_failures, "Failed panel sign-ins recorded during the current UTC minute.")
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4; charset=utf-8")
