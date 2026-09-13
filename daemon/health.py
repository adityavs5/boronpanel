"""Phase 5 feature 1: server health dashboard.

Host-wide infrastructure telemetry -- CPU/load/RAM/disk/network/uptime --
independent of any hosting account. Live figures come straight from
psutil (a thin wrapper over the same /proc sources `top`/`free`/`df`
already read); 24h history is built from HealthSnapshot rows a periodic
cron script appends every ~60s (scripts/health_snapshot.py), not
recomputed on the fly, so the dashboard stays fast and the trend survives
across page loads/restarts.
"""
from __future__ import annotations

import datetime as dt
import time

import psutil
from daemon import clock_health
from sqlalchemy import delete, select

from shared.db import write_session
from shared.models import HealthSnapshot

# 24h of graph data plus a comfortable margin -- this table has no
# natural cleanup event (unlike UsageSnapshot, which is scoped to an
# account and pruned by termination), so pruning has to be explicit or it
# grows forever.
RETENTION_HOURS = 48


def _disk_usage() -> list[dict]:
    disks = []
    seen_devices = set()
    for part in psutil.disk_partitions(all=False):
        if part.device in seen_devices:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        seen_devices.add(part.device)
        disks.append(
            {
                "mount": part.mountpoint,
                "device": part.device,
                "fstype": part.fstype,
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "pct": usage.percent,
            }
        )
    return disks


def get_live(params: dict | None = None) -> dict:
    cpu_pct = psutil.cpu_percent(interval=0.3)
    load1, load5, load15 = psutil.getloadavg()
    mem = psutil.virtual_memory()
    net = psutil.net_io_counters()
    uptime_seconds = int(time.time() - psutil.boot_time())
    return {
        "clock": clock_health.get_status(),
        "cpu_pct": round(cpu_pct, 1),
        "cpu_count": psutil.cpu_count() or 1,
        "load1": load1,
        "load5": load5,
        "load15": load15,
        "mem_total": mem.total,
        "mem_used": mem.used,
        "mem_pct": mem.percent,
        "disks": _disk_usage(),
        "net_rx_bytes": net.bytes_recv,
        "net_tx_bytes": net.bytes_sent,
        "uptime_seconds": uptime_seconds,
    }


def take_snapshot(params: dict | None = None) -> dict:
    """Called by scripts/health_snapshot.py every ~60s."""
    live = get_live()
    with write_session() as session:
        session.add(
            HealthSnapshot(
                cpu_pct=live["cpu_pct"],
                load1=live["load1"],
                load5=live["load5"],
                load15=live["load15"],
                mem_total_bytes=live["mem_total"],
                mem_used_bytes=live["mem_used"],
                disks=live["disks"],
                net_rx_bytes=live["net_rx_bytes"],
                net_tx_bytes=live["net_tx_bytes"],
            )
        )
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=RETENTION_HOURS)
        session.execute(delete(HealthSnapshot).where(HealthSnapshot.taken_at < cutoff))
    return {"status": "ok"}


def get_history(params: dict) -> dict:
    hours = int(params.get("hours", 24) or 24)
    hours = max(1, min(hours, RETENTION_HOURS))
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    with write_session() as session:
        rows = session.scalars(
            select(HealthSnapshot).where(HealthSnapshot.taken_at >= cutoff).order_by(HealthSnapshot.taken_at)
        ).all()
        points = []
        prev = None
        for row in rows:
            net_rx_delta = max(0, row.net_rx_bytes - prev.net_rx_bytes) if prev else 0
            net_tx_delta = max(0, row.net_tx_bytes - prev.net_tx_bytes) if prev else 0
            points.append(
                {
                    "taken_at": row.taken_at.isoformat(),
                    "cpu_pct": row.cpu_pct,
                    "load1": row.load1,
                    "mem_used_bytes": row.mem_used_bytes,
                    "mem_total_bytes": row.mem_total_bytes,
                    "net_rx_delta": net_rx_delta,
                    "net_tx_delta": net_tx_delta,
                }
            )
            prev = row
        return {"points": points}
