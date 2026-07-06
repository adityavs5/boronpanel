"""Local-port allocator shared by NodeApp and PythonApp (Phase 7a features
1/2). One range, checked against BOTH tables, so a Node app and a Python
app can never be handed the same port -- each app's OLS "Web Server
(Proxy)" external app connects to 127.0.0.1:<port>, and two backends
sharing a port would silently proxy to whichever process happened to bind
it first.
"""
from __future__ import annotations

from sqlalchemy import select

from shared.config import settings
from shared.models import NodeApp, PythonApp


class PortAllocationError(Exception):
    pass


def allocate_port(session) -> int:
    used = set(session.scalars(select(NodeApp.port)).all()) | set(session.scalars(select(PythonApp.port)).all())
    for port in range(settings.app_port_range_start, settings.app_port_range_end + 1):
        if port not in used:
            return port
    raise PortAllocationError(
        f"no free port in range {settings.app_port_range_start}-{settings.app_port_range_end}"
    )
