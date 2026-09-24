"""QA round 2, item 14: admin blocks an IP/CIDR server-wide, permanently.

Distinct from two existing, unrelated mechanisms this could otherwise be
confused with:
  - `daemon/handlers_ipblock.py` -- a per-DOMAIN denylist rendered into
    that one domain's own OLS vhost `accessControl` block. Only affects
    requests to that specific domain, not the server as a whole.
  - `daemon/fail2ban.py` -- automatic, time-bounded jail bans (sshd,
    postfix, dovecot, the panel login, OLS scan patterns). Only exposes
    *unban* (`unban_ip`/`unban_all_in_jail`); there is no manual/permanent
    ban action there, and a fail2ban ban always expires (`bantime`).

This module is the missing third thing: a manual, admin-reasoned,
server-wide, permanent block, enforced via UFW's own `deny from <ip>`
(blocks every port/protocol from that source, not scoped to one service --
`daemon/firewall.py`'s generic rule tool is fundamentally port-scoped and
has no "deny everything from this IP" shape). Inserted at position 1 so it
is evaluated before any earlier, broader allow rule (UFW evaluates rules
top-to-bottom, first match wins) -- appending to the end could leave an
existing `allow 80` (or similar) matching first and the ban silently
never taking effect for that port, the same ordering lesson this
project's own FileBrowser network-isolation fix (Security Audit 3, A3-7)
already learned the hard way for a different iptables rule set.
"""
from __future__ import annotations

import ipaddress

from sqlalchemy import select

from shared.db import write_session
from shared.models import PermanentIpBan
from shared.validation import ValidationError, validate_ip_or_cidr

from daemon.procutil import run


class IpBanError(Exception):
    pass


def _validate_actor(actor: str) -> str:
    """The acting admin's own panel username, recorded purely as free-text
    audit metadata -- NOT validated against validate_username's
    hosting-account regex (^[a-z][a-z0-9]{0,15}$), which admin panel
    logins are never required to follow."""
    actor = (actor or "").strip()
    if not actor or len(actor) > 64 or "\n" in actor or "\r" in actor:
        raise ValidationError("actor must be a non-empty single-line string up to 64 characters")
    return actor


def _refuse_self_lockout(network_str: str) -> None:
    """A handful of ranges that would either take the whole server offline
    (0.0.0.0/0, ::/0 -- indistinguishable from a self-inflicted outage) or
    break the panel's own loopback-bound services (FileBrowser Quantum on
    127.0.0.1:8088, the install runbook's own `curl 127.0.0.1:9443/healthz`
    liveness check) if banned. Anything else -- including private ranges,
    which an admin may legitimately want to block (e.g. a compromised
    internal service) -- is allowed."""
    net = ipaddress.ip_network(network_str, strict=False)
    if net.num_addresses > 1 and net.prefixlen == 0:
        raise ValidationError("refusing to ban 0.0.0.0/0 or ::/0 -- this would firewall off the entire server")
    if net.is_loopback:
        raise ValidationError("refusing to ban a loopback address -- this would break the panel's own local services")


def _ufw_deny_args(value: str) -> list[str]:
    return ["deny", "from", value]


def list_bans(params: dict) -> dict:
    with write_session() as session:
        rows = session.scalars(select(PermanentIpBan).order_by(PermanentIpBan.created_at.desc())).all()
        return {
            "bans": [
                {
                    "id": r.id,
                    "value": r.value,
                    "reason": r.reason,
                    "banned_by": r.banned_by,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
        }


def ban_ip(params: dict) -> dict:
    value = validate_ip_or_cidr(params["value"])
    reason = (params.get("reason") or "").strip()[:200]
    actor = _validate_actor(params.get("actor") or "admin")
    _refuse_self_lockout(value)

    with write_session() as session:
        existing = session.scalar(select(PermanentIpBan).where(PermanentIpBan.value == value))
        if existing is not None:
            raise IpBanError(f"'{value}' is already permanently banned")

    result = run(["ufw", "insert", "1"] + _ufw_deny_args(value), timeout=20)
    if not result.ok:
        raise RuntimeError(f"ufw insert 1 deny from {value} failed: {result.stderr.strip() or result.stdout.strip()}")

    with write_session() as session:
        row = PermanentIpBan(value=value, reason=reason or None, banned_by=actor)
        session.add(row)
        session.flush()
        return {
            "id": row.id, "value": row.value, "reason": row.reason,
            "banned_by": row.banned_by, "created_at": row.created_at.isoformat(),
        }


def unban_ip(params: dict) -> dict:
    ban_id = int(params["id"])
    with write_session() as session:
        row = session.get(PermanentIpBan, ban_id)
        if row is None:
            raise IpBanError(f"no permanent ban with id {ban_id}")
        value = row.value
        result = run(["ufw", "--force", "delete"] + _ufw_deny_args(value), timeout=20)
        if not result.ok and "could not find" not in (result.stderr or result.stdout or "").lower():
            raise RuntimeError(f"ufw delete deny from {value} failed: {result.stderr.strip() or result.stdout.strip()}")
        session.delete(row)
    return {"id": ban_id, "value": value, "status": "unbanned"}
