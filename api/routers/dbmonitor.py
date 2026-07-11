"""Missing-features batch, goal feature 7: live MariaDB monitor.
GET /admin/db/monitor + DELETE /admin/db/queries/{id} (the goal's own
literal shape) -- admin-only, auto-refreshed every 10s by the UI."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin

api_router = APIRouter(prefix="/api/v1/admin/db", tags=["dbmonitor"])


class BootstrapKillPrivilegeBody(BaseModel):
    confirm: bool = False


@api_router.get("/monitor")
def get_monitor(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    processlist = call_daemon("dbmonitor.processlist", identity)
    slow_queries = call_daemon("dbmonitor.slow_queries", identity)
    db_sizes = call_daemon("dbmonitor.db_sizes", identity)
    connections = call_daemon("dbmonitor.connections", identity)
    kill_privilege = call_daemon("dbmonitor.kill_privilege_status", identity)
    return {
        "processes": processlist["processes"],
        "slow_queries": slow_queries["queries"],
        "slow_queries_lookback_hours": slow_queries["lookback_hours"],
        "db_sizes": db_sizes,
        "connections": connections,
        "kill_privilege": kill_privilege,
    }


@api_router.delete("/queries/{thread_id}")
def kill_query(thread_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("dbmonitor.kill_query", identity, thread_id=thread_id)


@api_router.post("/kill-privilege/bootstrap")
def bootstrap_kill_privilege(body: BootstrapKillPrivilegeBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("dbmonitor.bootstrap_kill_privilege", identity, confirm=body.confirm)
