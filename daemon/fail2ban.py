"""Phase 5 feature 5: fail2ban integration. XHIGH effort per the goal --
same class of risk as the firewall feature (Feature 4): a wrong ban
action can lock out a legitimate operator, so jail bootstrap is
conservative and every parser here was built against this server's own
real `fail2ban-client`/log output, not assumed from documentation.

Jails managed: `sshd` (Ubuntu's fail2ban package already ships this
enabled by default via /etc/fail2ban/jail.d/defaults-debian.conf,
confirmed live -- nothing to add), plus five this feature adds via its
own `/etc/fail2ban/jail.d/boron.conf` drop-in: `postfix`/`dovecot`/
`pure-ftpd`
(stock fail2ban filters that ship with the package but aren't enabled by
default), a custom `boron-panel-login` filter for this project's own
`/login` endpoint, and a custom `ols-scan` filter for OpenLiteSpeed
(no stock fail2ban filter targets LiteSpeed's access log format).
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import json
import logging
import os
import re
from pathlib import Path

from shared.config import settings
from shared.validation import ValidationError

from daemon.procutil import run

logger = logging.getLogger("borond.fail2ban")

JAIL_D_PATH = "/etc/fail2ban/jail.d/boron.conf"
FILTER_PANEL_LOGIN_PATH = "/etc/fail2ban/filter.d/boron-panel-login.conf"
FILTER_OLS_SCAN_PATH = "/etc/fail2ban/filter.d/ols-scan.conf"
FAIL2BAN_LOG_PATH = "/var/log/fail2ban.log"

MANAGED_JAILS = {"sshd", "postfix", "dovecot", "pure-ftpd", "boron-panel-login", "ols-scan"}


def _atomic_write(path: str, content: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(content)
    os.chmod(tmp, 0o644)
    tmp.rename(p)


_PANEL_LOGIN_FILTER = """# Managed by Boron (daemon/fail2ban.py).
# Matches boron-api's own uvicorn access-log line for a failed /login
# POST (401 = bad credentials, 429 = already locked out by the
# application-layer throttle, Security audit finding F2) -- confirmed
# against this server's real journal MESSAGE field
# (`journalctl -u boron-api -o cat`) before writing this regex, not
# assumed from uvicorn's documentation.
[Definition]
failregex = ^INFO:\\s+<HOST>:\\d+ - "POST /login(?:/2fa)? HTTP/1\\.[01]" (401|429)
ignoreregex =
"""

_OLS_SCAN_FILTER = """# Managed by Boron (daemon/fail2ban.py).
# No stock fail2ban filter targets OpenLiteSpeed's access log (Combined
# Log Format, confirmed against this server's real
# /usr/local/lsws/logs/access.log). Bans IPs generating repeated 404s --
# real scanner traffic on this box (Censys, zgrab, and generic
# vulnerability probes for wp-login.php/.env/phpmyadmin-style paths) all
# show this exact signature.
[Definition]
failregex = ^<HOST> -.*"(GET|POST|HEAD|PUT|DELETE) [^"]*" 404
ignoreregex =
"""

_JAIL_D_CONF_BODY = """#
# backend=systemd + journalmatch, not logpath, for every service-backed
# jail here -- matches this system's own already-working sshd jail
# convention (/etc/fail2ban/jail.d/defaults-debian.conf sets
# backend=systemd globally) rather than introducing a second, untested
# log-reading mechanism. postfix's journalmatch targets
# `postfix@-.service` specifically, not the decoy `postfix.service`
# wrapper unit (daemon/servicemgr.py's own CHECKPOINT documents the same
# real-unit-name finding for Feature 2's service manager).

[postfix]
enabled = true
backend = systemd
journalmatch = _SYSTEMD_UNIT=postfix@-.service

[dovecot]
enabled = true
backend = systemd
journalmatch = _SYSTEMD_UNIT=dovecot.service

[pure-ftpd]
enabled = true
backend = systemd
journalmatch = _SYSTEMD_UNIT=pure-ftpd.service

[boron-panel-login]
enabled = true
filter = boron-panel-login
backend = systemd
journalmatch = _SYSTEMD_UNIT=boron-api.service
port = {panel_ports}
maxretry = 5
findtime = 300
bantime = 900

[ols-scan]
enabled = true
filter = ols-scan
backend = auto
logpath = /usr/local/lsws/logs/access.log
port = http,https
maxretry = 20
findtime = 60
bantime = 3600
"""


_IGNOREIP_BASE = ["127.0.0.1/8", "::1"]


def _cloudflare_ranges() -> list[str]:
    """Phase 2+3 feature 4: the materialized Cloudflare edge CIDRs
    (settings.cloudflare_ranges_file, refreshed by cf.refresh_ranges), or []
    when the file is absent/unreadable (feature not configured / rolled back
    -> plain base ignoreip, identical to before this feature)."""
    try:
        data = json.loads(Path(settings.cloudflare_ranges_file).read_text())
    except (OSError, ValueError):
        return []
    return list(data.get("ipv4_cidrs") or []) + list(data.get("ipv6_cidrs") or [])


def _render_jail_conf(cf_ranges: list[str]) -> str:
    """Full jail.d/boron.conf: a [DEFAULT] block whose `ignoreip` never
    bans localhost or a Cloudflare edge (feature 4 -- a proxied site sees
    every request from a CF edge, so an unguarded ols-scan jail would ban
    the edge and blackhole the whole site), followed by the jail
    definitions. Cloudflare ranges default grey; this is belt-and-braces
    with the OLS real-IP config (feature 3)."""
    try:
        from daemon.firewall import _bypass_addresses
        bypass = _bypass_addresses()
    except Exception:
        bypass = []
    ignoreip = " ".join(dict.fromkeys(_IGNOREIP_BASE + list(cf_ranges) + bypass))
    default_block = (
        "# Managed by Boron (daemon/fail2ban.py). Do not edit by hand --\n"
        "# regenerated by the admin jails action and by cf.refresh_ranges.\n\n"
        "[DEFAULT]\n"
        "# Phase 2+3 feature 4: never ban localhost or a Cloudflare edge IP.\n"
        f"ignoreip = {ignoreip}\n\n"
    )
    from shared.panel_ports import listener_ports
    ports = ",".join(str(port) for port in sorted(set(listener_ports())))
    return default_block + _JAIL_D_CONF_BODY.replace("{panel_ports}", ports)


def bootstrap_jails(params: dict | None = None) -> dict:
    """Idempotent -- writes the filter/jail config, then reloads fail2ban
    so the new jails take effect immediately. Safe to call repeatedly
    (e.g. re-run after an operator tweaks then wants to reset to
    defaults). Includes the current Cloudflare ranges in the ignoreip."""
    _atomic_write(FILTER_PANEL_LOGIN_PATH, _PANEL_LOGIN_FILTER)
    _atomic_write(FILTER_OLS_SCAN_PATH, _OLS_SCAN_FILTER)
    _atomic_write(JAIL_D_PATH, _render_jail_conf(_cloudflare_ranges()))
    result = run(["fail2ban-client", "reload"], timeout=30)
    if not result.ok:
        raise RuntimeError(f"fail2ban-client reload failed: {result.stderr.strip() or result.stdout.strip()}")
    return {"status": "ok", "jails": sorted(MANAGED_JAILS)}



def reconcile_managed_jails() -> bool:
    """Upgrade an existing managed installation when daemon code changes."""
    if not Path(JAIL_D_PATH).exists():
        return False  # The installer owns first-time fail2ban activation.
    expected = {JAIL_D_PATH: _render_jail_conf(_cloudflare_ranges()),
                FILTER_PANEL_LOGIN_PATH: _PANEL_LOGIN_FILTER,
                FILTER_OLS_SCAN_PATH: _OLS_SCAN_FILTER}
    if all(Path(path).is_file() and Path(path).read_text() == content
           for path, content in expected.items()):
        return False
    bootstrap_jails()
    return True

def refresh_cloudflare_ignoreip() -> bool:
    """Rewrite jail.d/boron.conf with the current Cloudflare ranges and
    reload fail2ban. Best-effort (returns False on reload failure rather than
    raising) -- called from cf.refresh_ranges, which must not fail the whole
    refresh because fail2ban isn't reloadable this instant. No-op-safe if the
    conf is unchanged (fail2ban reload is cheap and idempotent)."""
    try:
        _atomic_write(JAIL_D_PATH, _render_jail_conf(_cloudflare_ranges()))
    except OSError:
        logger.exception("could not write fail2ban jail conf with Cloudflare ranges")
        return False
    result = run(["fail2ban-client", "reload"], timeout=30)
    if not result.ok:
        logger.error("fail2ban reload after Cloudflare ranges refresh failed: %s", result.stderr.strip())
        return False
    return True


def cloudflare_ignoreip_configured() -> bool:
    """True when the live jail conf's ignoreip actually contains the current
    Cloudflare ranges (feature 4 half of the rails-status gate). False when
    there are no ranges yet, or the file is missing/out of date."""
    ranges = _cloudflare_ranges()
    if not ranges:
        return False
    try:
        conf = Path(JAIL_D_PATH).read_text()
    except OSError:
        return False
    for line in conf.splitlines():
        if line.strip().startswith("ignoreip"):
            return all(cidr in line for cidr in ranges)
    return False


# --- status / listing -------------------------------------------------------

_INT_FIELD_RE = {
    "currently_failed": re.compile(r"Currently failed:\s*(\d+)"),
    "total_failed": re.compile(r"Total failed:\s*(\d+)"),
    "currently_banned": re.compile(r"Currently banned:\s*(\d+)"),
    "total_banned": re.compile(r"Total banned:\s*(\d+)"),
}
_BANNED_IP_LIST_RE = re.compile(r"Banned IP list:\s*(.*)")


def _list_jail_names() -> list[str]:
    result = run(["fail2ban-client", "status"], timeout=15)
    for line in result.stdout.splitlines():
        if "Jail list:" in line:
            raw = line.split("Jail list:", 1)[1].strip()
            return [j.strip() for j in raw.split(",") if j.strip()]
    return []


def _jail_status(jail: str) -> dict:
    result = run(["fail2ban-client", "status", jail], timeout=15)
    data = {"jail": jail, "currently_failed": 0, "total_failed": 0, "currently_banned": 0, "total_banned": 0, "banned_ips": []}
    if not result.ok:
        data["error"] = result.stderr.strip() or result.stdout.strip()
        return data
    for line in result.stdout.splitlines():
        for key, pattern in _INT_FIELD_RE.items():
            m = pattern.search(line)
            if m:
                data[key] = int(m.group(1))
        m = _BANNED_IP_LIST_RE.search(line)
        if m:
            data["banned_ips"] = [ip for ip in m.group(1).split() if ip]
    return data


def list_jails(params: dict) -> dict:
    jails = [_jail_status(name) for name in _list_jail_names()]
    return {"jails": jails}


def get_jail(params: dict) -> dict:
    jail = params["jail"]
    if jail not in _list_jail_names():
        raise ValidationError(f"jail '{jail}' is not currently active")
    return _jail_status(jail)


def _validate_jail(jail: str) -> str:
    active = _list_jail_names()
    if jail not in active:
        raise ValidationError(f"jail '{jail}' is not currently active (active: {active})")
    return jail


def _validate_ip(ip: str) -> str:
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        raise ValidationError(f"'{ip}' is not a valid IP address") from None
    return ip


def unban_network(value: str) -> list[dict]:
    """Remove active fail2ban entries covered by a newly trusted IP/CIDR."""
    network = ipaddress.ip_network(value, strict=False)
    removed = []
    for jail in _list_jail_names():
        for candidate in _jail_status(jail).get("banned_ips", []):
            try:
                address = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if address.version != network.version or address not in network:
                continue
            result = run(["fail2ban-client", "set", jail, "unbanip", candidate], timeout=15)
            if result.ok:
                removed.append({"jail": jail, "ip": candidate})
    return removed


def unban_ip(params: dict) -> dict:
    jail = _validate_jail(params["jail"])
    ip = _validate_ip(params["ip"])
    result = run(["fail2ban-client", "set", jail, "unbanip", ip], timeout=15)
    if not result.ok:
        raise RuntimeError(f"unban failed: {result.stderr.strip() or result.stdout.strip()}")
    return {"jail": jail, "ip": ip, "status": "unbanned"}


def unban_all_in_jail(params: dict) -> dict:
    jail = _validate_jail(params["jail"])
    status = _jail_status(jail)
    unbanned = []
    for ip in status["banned_ips"]:
        result = run(["fail2ban-client", "set", jail, "unbanip", ip], timeout=15)
        if result.ok:
            unbanned.append(ip)
    return {"jail": jail, "unbanned": unbanned, "count": len(unbanned)}


# --- recent ban events, parsed from fail2ban's own log ----------------------

_EVENT_RE = re.compile(
    r"\A(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+fail2ban\.actions\s+\[\d+\]:\s+NOTICE\s+\[(?P<jail>\S+)\]\s+(?P<action>Ban|Unban)\s+(?P<ip>\S+)"
)


def recent_events(params: dict) -> dict:
    limit = int(params.get("limit", 50) or 50)
    jail_filter = params.get("jail")
    result = run(["tail", "-n", "2000", FAIL2BAN_LOG_PATH], timeout=15)
    events = []
    for line in result.stdout.splitlines():
        m = _EVENT_RE.match(line)
        if not m:
            continue
        if jail_filter and m.group("jail") != jail_filter:
            continue
        events.append(
            {
                "timestamp": m.group("ts"),
                "jail": m.group("jail"),
                "action": m.group("action"),
                "ip": m.group("ip"),
            }
        )
    events.reverse()  # most recent first
    return {"events": events[:limit]}
