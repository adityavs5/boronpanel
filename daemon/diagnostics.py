"""Bounded, read-only host diagnostics for administrators and updates.

Every check returns a small allowlisted result. Commands are argument arrays
with explicit timeouts, customer files are never opened, and no check may
restart a service or rewrite configuration.
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import os
import sqlite3
import time
from pathlib import Path
from typing import Callable

from cryptography import x509
from sqlalchemy import select

from daemon import filebrowser, mailqueue, nsisolation, servicemgr
from daemon.procutil import run
from shared.config import settings
from shared.db import write_session
from shared.models import Account, NotificationSettings, SnapshotDestination

STATUS_VALUES = {"pass", "warn", "fail", "unknown"}
_SERVICE_UNITS = {
    "openlitespeed": servicemgr.SERVICE_REGISTRY["ols"],
    "mariadb": servicemgr.SERVICE_REGISTRY["mariadb"],
    "postfix": servicemgr.SERVICE_REGISTRY["postfix"],
    "dovecot": servicemgr.SERVICE_REGISTRY["dovecot"],
    "powerdns": servicemgr.SERVICE_REGISTRY["powerdns"],
    "redis": "redis-server.service",
    "fail2ban": "fail2ban.service",
}


def _result(check_id: str, label: str, status: str, explanation: str,
            suggested_fix: str = "", *, critical: bool = False,
            duration_ms: int = 0) -> dict:
    if status not in STATUS_VALUES:
        status = "unknown"
    return {
        "id": check_id[:64],
        "label": label[:120],
        "status": status,
        "explanation": explanation[:500],
        "suggested_fix": suggested_fix[:500],
        "critical": bool(critical),
        "read_only": True,
        "duration_ms": max(0, int(duration_ms)),
    }


def _database() -> dict:
    path = Path(settings.db_path)
    if not path.is_file():
        return _result("database", "Panel database", "fail", "The panel database file is missing.",
                       "Restore the control-plane database before starting the panel.", critical=True)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
    finally:
        connection.close()
    size = page_count * page_size
    if not integrity or integrity[0] != "ok":
        return _result("database", "Panel database", "fail",
                       f"SQLite integrity check failed. Database size is {size} bytes.",
                       "Stop panel writes and restore a verified database backup.", critical=True)
    return _result("database", "Panel database", "pass",
                   f"SQLite integrity is valid. Database size is {size} bytes.", critical=True)


def _root_helper() -> dict:
    return _result("root_helper", "Root helper", "pass",
                   "The authenticated request reached the privileged helper and its worker pool.",
                   critical=True)


def _service(name: str, unit: str) -> dict:
    loaded = run(["systemctl", "show", unit, "--property=LoadState", "--value"], timeout=5)
    load_state = loaded.stdout.strip() if loaded.ok else "unknown"
    if load_state in ("not-found", "masked", "unknown", ""):
        return _result(f"service_{name}", f"Service: {name}", "unknown",
                       f"{unit} is not installed or cannot be inspected.",
                       "Install or enable this component if the selected server profile requires it.")
    active = run(["systemctl", "is-active", unit], timeout=5)
    state = active.stdout.strip() or active.stderr.strip()
    if active.ok and state == "active":
        return _result(f"service_{name}", f"Service: {name}", "pass", f"{unit} is active.")
    return _result(f"service_{name}", f"Service: {name}", "fail",
                   f"{unit} is loaded but its state is {state or 'unknown'}.",
                   f"Review the {name} service logs before starting or restarting it.")


def _storage() -> dict:
    paths = [Path("/"), Path(settings.home_base), Path(settings.db_path).parent]
    observations = []
    worst = "pass"
    for path in dict.fromkeys(paths):
        target = path if path.exists() else path.parent
        usage = os.statvfs(target)
        free_bytes = usage.f_bavail * usage.f_frsize
        total_bytes = usage.f_blocks * usage.f_frsize
        free_inodes = usage.f_favail
        total_inodes = usage.f_files
        byte_pct = (free_bytes / total_bytes * 100) if total_bytes else 0
        inode_pct = (free_inodes / total_inodes * 100) if total_inodes else 100
        headroom = min(byte_pct, inode_pct)
        if headroom < 5:
            worst = "fail"
        elif headroom < 15 and worst != "fail":
            worst = "warn"
        observations.append(f"{target}: {byte_pct:.1f}% disk and {inode_pct:.1f}% inodes free")
    suggestion = "Free disk space or inodes before provisioning, backing up, or updating." if worst != "pass" else ""
    return _result("storage", "Disk and inode headroom", worst, "; ".join(observations), suggestion,
                   critical=worst == "fail")


def _proc_ports(path: str, tcp: bool) -> set[int]:
    ports: set[int] = set()
    try:
        lines = Path(path).read_text().splitlines()[1:]
    except OSError:
        return ports
    for line in lines:
        fields = line.split()
        if len(fields) < 4 or (tcp and fields[3] != "0A"):
            continue
        try:
            ports.add(int(fields[1].rsplit(":", 1)[1], 16))
        except (IndexError, ValueError):
            continue
    return ports


def _ports() -> dict:
    tcp = _proc_ports("/proc/net/tcp", True) | _proc_ports("/proc/net/tcp6", True)
    udp = _proc_ports("/proc/net/udp", False) | _proc_ports("/proc/net/udp6", False)
    expected_tcp = {
        int(settings.api_bind_port): "panel", 80: "HTTP", 443: "HTTPS",
        25: "SMTP", 143: "IMAP", 993: "IMAPS", 53: "DNS TCP",
    }
    missing = [f"{label} ({port}/tcp)" for port, label in expected_tcp.items() if port not in tcp]
    if 53 not in udp:
        missing.append("DNS (53/udp)")
    if int(settings.api_bind_port) not in tcp:
        return _result("ports", "Service ports", "fail",
                       "The panel listener is missing; other missing listeners: " + ", ".join(missing),
                       "Verify the panel service and listener configuration.", critical=True)
    status = "warn" if missing else "pass"
    explanation = "All expected service listeners are present." if not missing else "Missing listeners: " + ", ".join(missing)
    return _result("ports", "Service ports", status, explanation,
                   "Review disabled components and the corresponding service configuration." if missing else "")


def _dns() -> dict:
    hosts = [settings.panel_hostname, settings.webmail_hostname, settings.mail_hostname, settings.pma_hostname]
    hosts = list(dict.fromkeys(str(item).strip().lower() for item in hosts if str(item).strip()))
    if not hosts:
        return _result("dns", "Service DNS", "unknown", "No service hostnames are configured.",
                       "Set the panel and service hostnames in Server Setup.")
    failed = []
    resolved = []
    for host in hosts:
        probe = run(["getent", "ahosts", host], timeout=5)
        if probe.ok and probe.stdout.strip():
            resolved.append(host)
        else:
            failed.append(host)
    if failed:
        return _result("dns", "Service DNS", "fail",
                       f"Resolved {len(resolved)} of {len(hosts)} configured service hostnames.",
                       "Create or correct DNS records for: " + ", ".join(failed))
    return _result("dns", "Service DNS", "pass", f"All {len(hosts)} configured service hostnames resolve.")


def _certificate_paths() -> list[tuple[str, Path]]:
    rows: list[tuple[str, Path]] = []
    if settings.panel_hostname:
        rows.append((settings.panel_hostname, Path("/etc/boron/ssl/api/panel.crt")))
    for host in (settings.webmail_hostname, settings.mail_hostname, settings.pma_hostname):
        host = str(host or "").strip().lower()
        if host and host not in {item[0] for item in rows}:
            rows.append((host, Path("/etc/letsencrypt/live") / host / "fullchain.pem"))
    return rows


def _certificates() -> dict:
    rows = _certificate_paths()
    if not rows:
        return _result("certificates", "Service certificates", "unknown",
                       "No service hostnames are configured.", "Configure service hostnames before issuing certificates.")
    now = dt.datetime.now(dt.timezone.utc)
    failures, warnings = [], []
    for host, path in rows:
        try:
            certificate = x509.load_pem_x509_certificate(path.read_bytes())
            remaining = (certificate.not_valid_after_utc - now).days
        except (OSError, ValueError):
            failures.append(f"{host}: certificate missing or unreadable")
            continue
        if remaining < 0:
            failures.append(f"{host}: expired")
        elif remaining < 30:
            warnings.append(f"{host}: {remaining} days remaining")
    if failures:
        return _result("certificates", "Service certificates", "fail", "; ".join(failures),
                       "Issue or renew the affected service certificates.")
    if warnings:
        return _result("certificates", "Service certificates", "warn", "; ".join(warnings),
                       "Confirm automatic renewal before the certificates expire.")
    return _result("certificates", "Service certificates", "pass",
                   f"All {len(rows)} configured service certificates are valid for at least 30 days.")


def _mail_queue() -> dict:
    queue = mailqueue.list_queue({})
    count = int(queue.get("count", 0))
    status = "warn" if count >= 100 else "pass"
    return _result("mail_queue", "Mail queue", status, f"Postfix reports {count} queued recipient entries.",
                   "Inspect deferred deliveries and mail service health." if status == "warn" else "")


def _backup_destinations() -> dict:
    with write_session() as db:
        rows = db.scalars(select(SnapshotDestination)).all()
    if not rows:
        return _result("backup_destinations", "Backup destinations", "warn",
                       "No backup destinations are configured.", "Add and test at least one backup destination.")
    enabled = [row for row in rows if row.enabled]
    problematic = [row for row in enabled if row.status not in ("ready", "verified", "active")]
    status = "warn" if not enabled or problematic else "pass"
    explanation = f"{len(enabled)} of {len(rows)} destinations are enabled; {len(problematic)} need verification. No connection test was run."
    return _result("backup_destinations", "Backup destinations", status, explanation,
                   "Open Backup Manager and test destinations that are not ready." if status == "warn" else "")


def _isolation() -> dict:
    with write_session() as db:
        account = db.scalar(select(Account).where(Account.status == "active").order_by(Account.id))
    if account is None:
        return _result("isolation", "Web/PHP isolation", "unknown", "No active account is available for a self-test.",
                       "Create an account before testing tenant isolation.")
    outcome = nsisolation.self_test({"username": account.username})
    if outcome.get("status") == "passed":
        return _result("isolation", "Web/PHP isolation", "pass", "The sample account passed the isolation self-test.")
    if outcome.get("status") == "incomplete":
        return _result("isolation", "Web/PHP isolation", "unknown",
                       "Isolation is configured; runtime verification awaits a live PHP worker.",
                       "Load a PHP site for the sample account, then run diagnostics again.")
    return _result("isolation", "Web/PHP isolation", "fail", "The sample account failed one or more isolation checks.",
                   "Open Filesystem Isolation and review the per-check result before rebuilding.", critical=True)


def _file_manager() -> dict:
    with write_session() as db:
        account = db.scalar(select(Account).where(Account.status == "active").order_by(Account.id))
    if account is None:
        return _result("file_manager", "File Manager backend", "unknown", "No active account is available for a backend check.")
    status = filebrowser.status({})
    home = Path(settings.home_base) / account.username
    if not status.get("binary_installed") or not home.is_dir():
        return _result("file_manager", "File Manager backend", "fail",
                       "The backend binary or sample account home is unavailable.",
                       "Reconcile the File Manager installation and account home.")
    unit = run(["systemctl", "show", f"boron-filebrowser@{account.username}.service",
                "--property=LoadState", "--value"], timeout=5)
    if unit.ok and unit.stdout.strip() not in ("", "not-found"):
        return _result("file_manager", "File Manager backend", "pass",
                       "The backend binary, sample account home, and on-demand unit are available.")
    return _result("file_manager", "File Manager backend", "warn",
                   "The backend binary and account home exist, but the on-demand unit is unavailable.",
                   "Run the File Manager bootstrap/reconcile action.")


def _notification_sender() -> dict:
    with write_session() as db:
        row = db.get(NotificationSettings, 1)
    configured = bool(row and str(row.sender_address or "").strip())
    if configured:
        return _result("notification_sender", "Notification sender", "pass", "A notification sender address is configured.")
    return _result("notification_sender", "Notification sender", "warn", "No notification sender address is configured.",
                   "Set a sender address before relying on operational alerts.")


def check_registry() -> list[tuple[str, Callable[[], dict]]]:
    checks: list[tuple[str, Callable[[], dict]]] = [
        ("database", _database), ("root_helper", _root_helper),
    ]
    checks.extend((f"service_{name}", lambda name=name, unit=unit: _service(name, unit))
                  for name, unit in _SERVICE_UNITS.items())
    checks.extend([
        ("storage", _storage), ("ports", _ports), ("dns", _dns),
        ("certificates", _certificates), ("mail_queue", _mail_queue),
        ("backup_destinations", _backup_destinations), ("isolation", _isolation),
        ("file_manager", _file_manager), ("notification_sender", _notification_sender),
    ])
    return checks


def _invoke(check_id: str, check: Callable[[], dict]) -> dict:
    started = time.monotonic()
    try:
        result = check()
        if not isinstance(result, dict):
            raise TypeError("check did not return a result")
    except Exception:  # noqa: BLE001 - arbitrary OS probes must not abort the run
        result = _result(check_id, check_id.replace("_", " ").title(), "unknown",
                         "The check could not complete safely.",
                         "Review the panel and helper logs using the request reference.")
    result = dict(result)
    result["duration_ms"] = max(0, int((time.monotonic() - started) * 1000))
    result["read_only"] = True
    return result


def run_all(params: dict | None = None, *, checks=None) -> dict:
    """Run independent checks concurrently and return before the hard budget."""
    started_at = dt.datetime.now(dt.timezone.utc)
    started = time.monotonic()
    registry = list(checks if checks is not None else check_registry())
    per_check = max(0.1, min(float(settings.diagnostics_check_timeout_seconds), 30.0))
    total = max(per_check, min(float(settings.diagnostics_total_timeout_seconds), 59.0))
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(registry)),
                                                     thread_name_prefix="boron-diagnostic")
    futures = {executor.submit(_invoke, check_id, check): check_id for check_id, check in registry}
    done, pending = concurrent.futures.wait(futures, timeout=min(per_check, total))
    results = [future.result() for future in done]
    for future in pending:
        check_id = futures[future]
        future.cancel()
        results.append(_result(check_id, check_id.replace("_", " ").title(), "unknown",
                               f"The check exceeded the {per_check:g}-second per-check budget.",
                               "Review the relevant service directly; the diagnostics run continued."))
    executor.shutdown(wait=False, cancel_futures=True)
    order = {check_id: index for index, (check_id, _check) in enumerate(registry)}
    results.sort(key=lambda item: order.get(item["id"], len(order)))
    if any(item["status"] == "fail" for item in results):
        overall = "fail"
    elif any(item["status"] == "warn" for item in results):
        overall = "warn"
    elif any(item["status"] == "unknown" for item in results):
        overall = "unknown"
    else:
        overall = "pass"
    return {
        "status": overall,
        "started_at": started_at.isoformat(),
        "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
        "checks": results,
        "summary": {status: sum(1 for item in results if item["status"] == status)
                    for status in ("pass", "warn", "fail", "unknown")},
        "read_only": True,
        "constraints": [
            "No services were restarted or reloaded.",
            "Backup destinations were inspected from saved state only; no remote test was run.",
            "Customer file contents and mail content were not read.",
        ],
    }
