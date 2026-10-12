"""Read-only, account-scoped website troubleshooting checks."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain, UsageSnapshot
from shared.validation import ValidationError, validate_domain, validate_username
from daemon import cgroups, ipmanager, logs, mailqueue, ols, ssl
from daemon.procutil import run


def _finding(name: str, status: str, confidence: str, explanation: str, next_step: str) -> dict:
    return {"name": name, "status": status, "confidence": confidence,
            "explanation": explanation, "next_step": next_step}


def _dns_check(domain: str, expected: str | None) -> dict:
    result = run(["getent", "ahosts", domain], timeout=8, output_limit=64 * 1024)
    addresses = sorted({line.split()[0] for line in result.stdout.splitlines() if line.split()})
    if not addresses:
        return _finding("DNS", "fail", "high", "The domain does not currently resolve to an IP address.", "Check the domain's A or AAAA record with your DNS provider.")
    if expected and expected not in addresses:
        return _finding("DNS", "fail", "high", f"The domain resolves away from this account's server address ({expected}).", "Correct the A or AAAA record only after confirming where the site should be hosted.")
    if expected:
        return _finding("DNS", "pass", "high", f"The domain resolves to this account's server address ({expected}).", "No DNS change is needed.")
    return _finding("DNS", "unknown", "low", "The domain resolves, but Boron has no assigned server IP to compare it with.", "Ask the server administrator to configure the server public IP.")


def _certificate_check(domain: Domain) -> dict:
    details = ssl._cert_file_details(domain.domain)
    if details is None:
        return _finding("Certificate", "fail", "high", "No readable managed certificate is installed for this domain.", "Issue a certificate from SSL Certificates after DNS points to this server.")
    if details["cert_status"] == "expired":
        return _finding("Certificate", "fail", "high", f"The certificate expired on {details['expiry_date']}.", "Renew or reissue the certificate now.")
    if details["cert_status"] == "expiring":
        return _finding("Certificate", "warn", "high", f"The certificate expires in {details['days_remaining']} days.", "Check automatic renewal or renew it from SSL Certificates.")
    return _finding("Certificate", "pass", "high", f"The certificate is valid until {details['expiry_date']}.", "No certificate change is needed.")


def _web_php_check(account: Account, domain: Domain) -> dict:
    if account.status != "active" or domain.suspended:
        reason = "The hosting account is not active." if account.status != "active" else "This domain is suspended."
        return _finding("Web and PHP", "fail", "high", reason, "Unsuspend the account or domain before troubleshooting the application.")
    service = run(["systemctl", "is-active", "lshttpd"], timeout=5, output_limit=4096)
    vhost_path = Path(ols._vhost_conf_path(ols._vhost_name(domain.domain)))
    version = domain.php_version or account.php_version
    php_binary = Path(settings.lsphp_base) / f"lsphp{version.replace('.', '')}" / "bin" / "lsphp"
    missing = []
    if not service.ok or service.stdout.strip() != "active":
        missing.append("OpenLiteSpeed is not active")
    if not vhost_path.is_file():
        missing.append("the domain virtual-host configuration is missing")
    if not php_binary.is_file():
        missing.append(f"PHP {version} is not installed")
    if missing:
        return _finding("Web and PHP", "fail", "high", "; ".join(missing) + ".", "Ask the administrator to restore the web service or PHP runtime, then rebuild the domain configuration.")
    return _finding("Web and PHP", "pass", "high", f"OpenLiteSpeed, the domain configuration, and PHP {version} are present.", "Check the recent error summary if the application still fails.")


def _error_check(username: str, domain: str) -> dict:
    counts, unavailable = {}, []
    for kind in ("error", "php"):
        try:
            result = logs.get_log({"username": username, "domain": domain, "type": kind, "lines": 100})
            counts[kind] = result["line_count"]
        except Exception:
            unavailable.append(kind)
    total = sum(counts.values())
    if total:
        return _finding("Recent errors", "warn", "medium", f"The latest bounded log sample contains {total} web/PHP error line(s).", "Open Logs and review the recent entries for this domain.")
    if unavailable:
        return _finding("Recent errors", "unknown", "low", "One or more error logs are not available yet.", "Load the site once, then check Logs again.")
    return _finding("Recent errors", "pass", "medium", "No entries were found in the latest bounded web/PHP error sample.", "No log action is suggested by this check.")


def _storage_check(account: Account, snapshot: UsageSnapshot | None) -> dict:
    if snapshot is None:
        return _finding("Disk and inodes", "unknown", "low", "No usage snapshot has been collected for this account yet.", "Wait for the usage collector or open Disk Usage to request a fresh snapshot.")
    used = snapshot.disk_home_bytes + snapshot.disk_mail_bytes + snapshot.disk_db_bytes
    disk_limit = account.quota_hard_mb * 1024 * 1024 if account.quota_hard_mb else 0
    disk_ratio = used / disk_limit if disk_limit else 0
    inode_ratio = snapshot.inode_count / account.inode_hard_limit if account.inode_hard_limit else 0
    if disk_ratio >= .98 or inode_ratio >= .98:
        return _finding("Disk and inodes", "fail", "high", "The account is at or above 98% of its disk or inode limit.", "Remove unnecessary files or ask the administrator to raise the account limit.")
    if disk_ratio >= .9 or inode_ratio >= .9:
        return _finding("Disk and inodes", "warn", "high", "The account is above 90% of its disk or inode limit.", "Review Disk Usage before the site runs out of capacity.")
    return _finding("Disk and inodes", "pass", "high", f"Usage is below warning levels ({used / 1073741824:.2f} GB, {snapshot.inode_count:,} inodes).", "No storage change is needed.")


def _cgroup_check(account: Account) -> dict:
    try:
        root = cgroups._cgroup_path(account.username, account.uid)
        cpu = dict(line.split(None, 1) for line in (root / "cpu.stat").read_text().splitlines() if " " in line)
        memory = dict(line.split(None, 1) for line in (root / "memory.events").read_text().splitlines() if " " in line)
    except Exception:
        return _finding("Resource throttling", "unknown", "low", "Live cgroup counters are not available for this account.", "Retry while the site is receiving traffic, or ask the administrator to rebuild its resource policy.")
    oom = int(memory.get("oom_kill", 0)) + int(memory.get("oom", 0))
    throttled = int(cpu.get("nr_throttled", 0))
    if oom:
        return _finding("Resource throttling", "fail", "high", f"The resource controller recorded {oom} out-of-memory event(s).", "Reduce application memory use or ask the administrator to review the memory limit.")
    if throttled:
        return _finding("Resource throttling", "warn", "medium", f"The CPU controller has throttled this account {throttled} time(s) since the cgroup started.", "Compare site response time with CPU usage; ask for a limit review if throttling is sustained.")
    return _finding("Resource throttling", "pass", "medium", "No CPU throttling or out-of-memory event is recorded in the current cgroup.", "No resource-limit change is suggested by this snapshot.")


def _mail_queue_check(domains: list[str]) -> dict:
    entries = {}
    try:
        for domain in domains:
            for row in mailqueue.list_queue({"search": domain})["entries"]:
                entries[row["queue_id"]] = row["status"]
    except Exception:
        return _finding("Mail queue", "unknown", "low", "The mail queue could not be inspected.", "Ask the administrator to check Postfix if mail is also affected.")
    if entries:
        return _finding("Mail queue", "warn", "medium", f"{len(entries)} queued message(s) involve this account's domains.", "Open Email delivery tracking or ask the administrator to inspect the mail queue.")
    return _finding("Mail queue", "pass", "medium", "No queued message involves this account's domains.", "No mail-queue action is needed.")


def check(params: dict) -> dict:
    username = validate_username(params["username"])
    requested = params.get("domain")
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise ValidationError(f"account '{username}' not found")
        domains = session.scalars(select(Domain).where(Domain.account_id == account.id).order_by(Domain.domain)).all()
        if not domains:
            raise ValidationError("create a domain before running site health checks")
        if requested:
            name = validate_domain(requested)
            domain = next((row for row in domains if row.domain == name), None)
            if domain is None:
                raise ValidationError(f"domain '{name}' does not belong to this account")
        else:
            domain = next((row for row in domains if row.domain == account.primary_domain), domains[0])
        snapshot_row = session.scalar(select(UsageSnapshot).where(UsageSnapshot.account_id == account.id).order_by(UsageSnapshot.taken_at.desc()))
        snapshot = None if snapshot_row is None else SimpleNamespace(
            disk_home_bytes=snapshot_row.disk_home_bytes,
            disk_mail_bytes=snapshot_row.disk_mail_bytes,
            disk_db_bytes=snapshot_row.disk_db_bytes,
            inode_count=snapshot_row.inode_count,
        )
        account_id, domain_names = account.id, [row.domain for row in domains]
        # Copy only non-secret scalar state before the session closes; do not
        # hold a SQLite transaction during bounded OS/network checks.
        account_state = SimpleNamespace(
            username=account.username, uid=account.uid, status=account.status,
            php_version=account.php_version, quota_hard_mb=account.quota_hard_mb,
            inode_hard_limit=account.inode_hard_limit,
        )
        domain_state = SimpleNamespace(
            domain=domain.domain, suspended=domain.suspended,
            php_version=domain.php_version,
        )
    findings = [
        _dns_check(domain_state.domain, ipmanager.address_for_account(account_id)),
        _certificate_check(domain_state), _web_php_check(account_state, domain_state),
        _error_check(username, domain_state.domain), _storage_check(account_state, snapshot),
        _cgroup_check(account_state), _mail_queue_check(domain_names),
    ]
    overall = "fail" if any(row["status"] == "fail" for row in findings) else "warn" if any(row["status"] in {"warn", "unknown"} for row in findings) else "pass"
    return {"username": username, "domain": domain_state.domain, "domains": domain_names,
            "overall": overall, "findings": findings, "read_only": True}
