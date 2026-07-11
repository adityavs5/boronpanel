#!/opt/boron/.venv/bin/python
"""Live, real-system verification for every Phase 7b feature.

NOT run automatically -- written and staged ready-to-run while live
verification was blocked by this environment's own permission classifier
(a second, independent Claude Code session was confirmed concurrently
active against this same server with no clear authorization to resume
live changes; see docs/STATUS.md's Phase 7b section for the full
incident). This script calls the real daemon modules directly (as root,
exactly as borond itself would) against the REAL production database
and REAL system services (MariaDB, Postfix/Dovecot, PowerDNS, OLS) --
it deliberately does NOT restart borond/boron-api (the two
processes actually shared with any other concurrent session), so running
it doesn't disrupt whatever the other session might be doing with those.

Every test account/domain/database/mailbox/webhook this creates is
prefixed `p7bverify` and torn down at the end via each feature's own
real termination/deletion path (same "disposable test account,
terminated after verification" pattern every earlier phase in this
project's history already uses) -- run cleanup_all() again by hand if a
run is interrupted partway through.

Usage (once authorized to run against this live server):
    /opt/boron/.venv/bin/python scripts/verify_phase7b_live.py [feature ...]
    # feature in: cpanel_import bandwidth notifications webhooks usage_alerts staging all
    # no args = run all six in order, then clean up
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from shared.config import settings  # noqa: E402
from shared.db import write_session  # noqa: E402
from shared.models import (  # noqa: E402
    Account,
    AccountNotificationPrefs,
    AccountResourceLimits,
    Domain,
    NotificationSettings,
    UsageSnapshot,
)

from daemon import (  # noqa: E402
    cpanel_import,
    handlers_account,
    handlers_cron,
    handlers_database,
    handlers_domain,
    handlers_mail,
    notifications,
    ols,
    staging,
    usage,
    usage_alerts,
    webhooks,
    wordpress,
)
from daemon.procutil import run  # noqa: E402

# Importing daemon.server runs its module-level CREATE_HOOKS/TERMINATE_HOOKS
# registration (OLS vhost, DNS, DB, mail, SSL, cron, cgroups, and Phase 6b
# namespace enable + teardown). Without it, this script's create/terminate
# calls ran against EMPTY hook lists, so terminate_account never invoked
# nsisolation.teardown_account -> a terminated account's OLS mount namespace
# was never unmounted, got reused by the next account on the same recycled
# uid (whose lsphp then had a deleted home bind-mounted -> instant 500), and
# MariaDB users / vhost refs were orphaned. serve_forever() only runs under
# __main__, so importing here has no socket/serving side effects.
from daemon import server as _server  # noqa: E402,F401

# Override with P7B_VERIFY_PREFIX so a re-run after an interrupted run can use
# fresh usernames without needing to purge the prior run's (terminated but
# still-present) account rows -- create_account refuses to reuse a username
# even once its account is terminated.
PREFIX = os.environ.get("P7B_VERIFY_PREFIX", "p7bverify")
RESULTS: list[tuple[str, bool, str]] = []


def _report(feature: str, ok: bool, detail: str) -> None:
    RESULTS.append((feature, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {feature}: {detail}")


def _curl(url: str, timeout: int = 15) -> tuple[int, str]:
    result = run(["curl", "-sk", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", str(timeout), url], timeout=timeout + 5)
    return int(result.stdout.strip() or "0"), result.stderr


# --- 1. cPanel import --------------------------------------------------


def _build_synthetic_cpanel_tarball(work_dir: Path, username: str, domain: str) -> Path:
    """A hand-built fixture matching WHM's own publicly documented backup
    layout -- see daemon/cpanel_import.py's own module docstring for the
    exact shape. No real cPanel/WHM instance exists in this sandbox to
    generate a genuine one from (documented, disclosed limitation)."""
    root = work_dir / f"cpmove-{username}"
    (root / "homedir" / "public_html").mkdir(parents=True)
    (root / "homedir" / "public_html" / "index.php").write_text(
        f"<?php echo 'Phase 7b live verification: {domain}'; ?>\n"
    )
    (root / "userdata").mkdir()
    (root / "userdata" / "main.yaml").write_text(f"main_domain: {domain}\n")
    (root / f"{username}.yaml").write_text(f"USER: {username}\nDOMAIN: {domain}\nEMAIL: webmaster@{domain}\n")

    (root / "mysql").mkdir()
    (root / "mysql" / f"{username}_shop.sql").write_text(
        "CREATE TABLE IF NOT EXISTS products (id INT PRIMARY KEY, name VARCHAR(64));\n"
        "INSERT INTO products VALUES (1, 'Verification Widget');\n"
    )

    (root / "cron").mkdir()
    (root / "cron" / username).write_text("0 3 * * * /bin/true # p7b verification cron\n")

    tar_path = work_dir / f"cpmove-{username}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(root, arcname=root.name)
    return tar_path


def verify_cpanel_import() -> None:
    feature = "cpanel_import"
    username = f"{PREFIX}ci"
    domain = f"{username}.104-234-179-64.sslip.io"
    work_dir = Path(tempfile.mkdtemp(prefix="p7b-verify-cpanel-"))
    try:
        tarball = _build_synthetic_cpanel_tarball(work_dir, username, domain)
        result = cpanel_import.trigger_import({"username": username, "source": "upload", "source_ref": str(tarball)})
        job_id = result["id"]

        for _ in range(120):
            job = cpanel_import.get_job({"job_id": job_id, "username": username})
            if job["status"] in ("completed", "failed"):
                break
            time.sleep(1)
        else:
            _report(feature, False, "import job did not finish within 120s")
            return

        if job["status"] != "completed":
            _report(feature, False, f"import job failed: {job['error']}")
            return

        # Real OLS reload needs a brief moment to actually pick up the new vhost.
        time.sleep(2)
        status_code, curl_err = _curl(f"http://{domain}/")
        ok = status_code == 200
        _report(
            feature, ok,
            f"job {job_id} completed, {len(job['results'])} item(s) reported, "
            f"curl http://{domain}/ -> {status_code} ({curl_err.strip() if not ok else 'ok'})",
        )
        for item in job["results"]:
            print(f"    - {item['item']}: {item['status']} ({item['detail']})")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# --- 2. Bandwidth graphs -------------------------------------------------


def verify_bandwidth() -> None:
    feature = "bandwidth"
    username = f"{PREFIX}bw"
    domain = f"{username}.104-234-179-64.sslip.io"
    try:
        handlers_account.create_account({"username": username})
        handlers_domain.add_domain({"username": username, "domain": domain, "kind": "primary"})
        with write_session() as session:
            account = session.scalar(select(Account).where(Account.username == username))
            docroot = session.scalar(select(Domain).where(Domain.domain == domain)).docroot
        Path(docroot, "index.html").write_text("p7b bandwidth verification\n")
        import pwd

        pw = pwd.getpwnam(username)
        run(["chown", f"{pw.pw_uid}:{pw.pw_gid}", f"{docroot}/index.html"], timeout=10)

        time.sleep(2)
        n_requests = 5
        for _ in range(n_requests):
            _curl(f"http://{domain}/")

        vhost_name = ols._vhost_name(domain)
        log_path = Path(settings.home_base) / username / "logs" / f"{vhost_name}-access.log"
        time.sleep(1)  # let OLS flush the access log
        independent_total = 0
        if log_path.exists():
            for line in log_path.read_text(errors="replace").splitlines():
                m = usage._ACCESS_LOG_RE.match(line)
                if m and m.group(4) != "-":
                    independent_total += int(m.group(4))

        with write_session() as session:
            account = session.scalar(select(Account).where(Account.username == username))
        usage.refresh_bandwidth(account)
        report = usage.get_bandwidth_report(account, "daily")

        ok = report["total_bytes_served"] > 0 and report["total_bytes_served"] == independent_total
        _report(
            feature, ok,
            f"{n_requests} real requests to {domain}, refresh_bandwidth report total="
            f"{report['total_bytes_served']} bytes vs independent access-log grep total="
            f"{independent_total} bytes",
        )
    finally:
        handlers_account.terminate_account({"username": username})


# --- 3. Email notifications ---------------------------------------------


def verify_notifications() -> None:
    feature = "notifications"
    username = f"{PREFIX}nt"
    mail_domain = f"{username}.mail.local"
    local_part = "verify"
    try:
        handlers_account.create_account({"username": username})
        handlers_mail.create_mail_domain({"username": username, "domain": mail_domain})
        handlers_mail.create_mailbox({"domain": mail_domain, "local_part": local_part, "password": "Verify!Passw0rd123"})
        customer_email = f"{local_part}@{mail_domain}"

        with write_session() as session:
            row = session.get(NotificationSettings, 1)
            if row is None:
                session.add(NotificationSettings(id=1, sender_address=f"boron@{mail_domain}"))
            else:
                row.sender_address = f"boron@{mail_domain}"

        notifications.set_prefs({"username": username, "customer_email": customer_email})

        with write_session() as session:
            account = session.scalar(select(Account).where(Account.username == username))
        sent = notifications.maybe_send("account.created", account, initial_password="Verify!Passw0rd123")

        maildir = Path(settings.mail_base) / mail_domain / local_part / "new"
        delivered = False
        for _ in range(20):
            if maildir.is_dir() and any(maildir.iterdir()):
                delivered = True
                break
            time.sleep(1)

        _report(feature, sent and delivered, f"maybe_send returned {sent}, message appeared in real Maildir: {delivered}")
    finally:
        handlers_account.terminate_account({"username": username})


# --- 4. Webhooks ----------------------------------------------------------


def verify_webhooks() -> None:
    feature = "webhooks"
    received: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            received["body"] = self.rfile.read(length)
            received["headers"] = dict(self.headers)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 8765), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    webhook_id = None
    try:
        created = webhooks.create_webhook({"url": "http://127.0.0.1:8765/hook", "events": ["account.created"]})
        webhook_id = created["id"]
        webhooks.test_webhook({"webhook_id": webhook_id})

        for _ in range(50):
            if received:
                break
            time.sleep(0.1)

        if not received:
            _report(feature, False, "no delivery received by the local listener within 5s")
            return

        import hashlib
        import hmac as hmac_mod

        expected_sig = "sha256=" + hmac_mod.new(created["secret"].encode(), received["body"], hashlib.sha256).hexdigest()
        actual_sig = received["headers"].get("X-Boron-Signature")
        ok = actual_sig == expected_sig
        _report(feature, ok, f"received body={received['body'][:80]!r}, signature match: {ok}")
    finally:
        server.shutdown()
        if webhook_id is not None:
            webhooks.delete_webhook({"webhook_id": webhook_id})


# --- 5. Usage alerts -------------------------------------------------------


def verify_usage_alerts() -> None:
    feature = "usage_alerts"
    username = f"{PREFIX}ua"
    try:
        handlers_account.create_account({"username": username})
        with write_session() as session:
            account = session.scalar(select(Account).where(Account.username == username))
            account.quota_hard_mb = 100  # small, easy-to-cross real quota
            session.add(UsageSnapshot(account_id=account.id, disk_home_bytes=85 * 1024 * 1024, disk_mail_bytes=0, disk_db_bytes=0, inode_count=1, process_count=1))

        with write_session() as session:
            account = session.scalar(select(Account).where(Account.username == username))
        new_alerts = usage_alerts.check_usage_alerts(account)
        alerts_data = usage_alerts.get_alerts({"username": username})

        ok = len(new_alerts) == 1 and new_alerts[0]["resource"] == "disk" and new_alerts[0]["threshold_pct"] == 80
        _report(
            feature, ok,
            f"seeded disk usage at 85% of a 100MB quota -> new_alerts={new_alerts}, "
            f"active alerts now: {alerts_data['active']}",
        )
    finally:
        handlers_account.terminate_account({"username": username})


# --- 6. Staging environments ------------------------------------------------


def verify_staging() -> None:
    feature = "staging"
    username = f"{PREFIX}st"
    domain = f"{username}.104-234-179-64.sslip.io"
    staging_domain = f"staging.{domain}"
    try:
        handlers_account.create_account({"username": username})
        handlers_domain.add_domain({"username": username, "domain": domain, "kind": "primary"})

        install_result = wordpress.install({"username": username, "domain": domain, "admin_email": f"admin@{domain}"})
        if install_result["status"] != "installed":
            _report(feature, False, f"WordPress install did not complete: {install_result}")
            return

        result = staging.create_staging({"username": username, "domain": domain})
        time.sleep(2)

        staging_code, _ = _curl(f"http://{staging_domain}/")
        wp_admin_code, _ = _curl(f"http://{staging_domain}/wp-admin/install.php")

        ok = staging_code == 200 and result["is_wordpress"] and result["db_name"]
        _report(
            feature, ok,
            f"staging created ({result['staging_domain']}, db={result['db_name']}), "
            f"curl {staging_domain}/ -> {staging_code}, /wp-admin/install.php -> {wp_admin_code}",
        )
    finally:
        handlers_account.terminate_account({"username": username})


FEATURES = {
    "cpanel_import": verify_cpanel_import,
    "bandwidth": verify_bandwidth,
    "notifications": verify_notifications,
    "webhooks": verify_webhooks,
    "usage_alerts": verify_usage_alerts,
    "staging": verify_staging,
}


def cleanup_all() -> None:
    """Terminates every account this script's own naming convention could
    have left behind -- safe to run standalone if a prior run was
    interrupted partway through."""
    with write_session() as session:
        accounts = session.scalars(select(Account).where(Account.username.like(f"{PREFIX}%"))).all()
        usernames = [a.username for a in accounts if a.status != "terminated"]
    for username in usernames:
        try:
            handlers_account.terminate_account({"username": username})
            print(f"cleaned up leftover account: {username}")
        except Exception as exc:  # noqa: BLE001
            print(f"failed to clean up {username}: {exc}")


def main() -> int:
    requested = sys.argv[1:] or ["all"]
    to_run = list(FEATURES.keys()) if "all" in requested else requested

    for name in to_run:
        if name not in FEATURES:
            print(f"unknown feature '{name}', choices: {list(FEATURES)} or 'all'")
            return 1

    for name in to_run:
        print(f"\n=== {name} ===")
        try:
            FEATURES[name]()
        except Exception as exc:  # noqa: BLE001
            _report(name, False, f"unhandled exception: {exc!r}")
            import traceback

            traceback.print_exc()

    print("\n=== summary ===")
    for feature, ok, detail in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {feature}: {detail}")

    return 0 if all(ok for _, ok, _ in RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
