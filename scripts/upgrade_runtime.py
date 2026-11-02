#!/usr/bin/env python3
"""Idempotent host integration for self-updates that add system services."""
from __future__ import annotations

import argparse
import re
import base64
import difflib
import hashlib
import json
import os
import shutil
import tempfile
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASSWORD_QUERY = "password_query = SELECT CONCAT(u.local_part, '@', d.domain) AS user, u.password FROM mail_user u JOIN mail_domain d ON d.id=u.domain_id WHERE CONCAT(u.local_part, '@', d.domain)='%u' AND u.active=1 AND d.active=1"
SSO_PASSWORD_QUERY = "password_query = SELECT s.mailbox AS user, s.password FROM webmail_session s JOIN mail_user u ON CONCAT(u.local_part, '@', (SELECT domain FROM mail_domain WHERE id=u.domain_id))=s.mailbox JOIN mail_domain d ON d.id=u.domain_id WHERE s.mailbox='%u' AND s.revoked=0 AND s.expires_at>UTC_TIMESTAMP() AND u.active=1 AND d.active=1"
DOVECOT_SQL_CONFIG = Path('/etc/dovecot/dovecot-sql.conf.ext')
DOVECOT_SSO_SQL_CONFIG = Path('/etc/dovecot/boron-sso-sql.conf.ext')
DOVECOT_SSO_PASSDB_CONFIG = Path('/etc/dovecot/conf.d/09-boron-sso-auth.conf')
DOVECOT_QUOTA_CONFIG = Path('/etc/dovecot/conf.d/91-boron-quota.conf')
DOVECOT_SIEVE_CONFIG = Path('/etc/dovecot/conf.d/92-boron-sieve.conf')
DOVECOT_SIEVE_PLUGIN = Path('/usr/lib/dovecot/modules/lib90_sieve_plugin.so')
# Pigeonhole runs only where a protocol loads it; autoresponders (personal
# ~/.dovecot.sieve), spam-to-Junk and per-mailbox blacklists (sieve_before)
# all execute at LMTP delivery.
DOVECOT_SIEVE_CONTENT = """# Managed Boron mail filtering: run Sieve at LMTP delivery.
protocol lmtp {
  mail_plugins = $mail_plugins sieve
}
"""
# dovecot-pop3d enables POP3 (110 with STARTTLS, 995) through this file; it
# shares the IMAP passdb/userdb, so every login rule applies to it too.
DOVECOT_POP3_PROTOCOL = Path('/usr/share/dovecot/protocols.d/pop3d.protocol')
# Suspended accounts: daemon/mail.py keeps one mail domain per line. The file
# sorts before the webmail (09) and SQL (10-auth) passdbs, so it applies to
# IMAP, POP3, webmail and SMTP AUTH; LMTP delivery reads only the userdb.
DOVECOT_SUSPENDED_PASSDB_CONFIG = Path('/etc/dovecot/conf.d/08-boron-suspended-auth.conf')
DOVECOT_SUSPENDED_DOMAINS = Path('/etc/dovecot/boron-suspended-mail-domains')
DOVECOT_SUSPENDED_PASSDB = f"""# Managed Boron login block for suspended hosting accounts. The list must
# exist: Dovecot refuses every login when a deny passdb cannot be read.
passdb {{
  driver = passwd-file
  args = username_format=%d {DOVECOT_SUSPENDED_DOMAINS}
  deny = yes
}}
"""
POSTFIX_FORWARDS_MAP = Path('/etc/postfix/boron/mysql-virtual-forwards.cf')
# virtual_alias_maps wins over virtual_mailbox_maps, so an address that is
# both a mailbox and a forwarder (or a mailbox under a catch-all) must also
# map to itself, or the forward silently replaces local delivery. Kept
# identical to the map scripts/install.sh writes on fresh servers.
FORWARDS_QUERY = (
    "query = SELECT GROUP_CONCAT(destination SEPARATOR ',') FROM ( "
    "SELECT f.destination AS destination FROM mail_forward f JOIN mail_domain d ON f.domain_id = d.id "
    "WHERE CONCAT(f.source_local_part, '@', d.domain) = '%s' AND f.active = 1 AND d.active = 1 "
    "UNION SELECT CONCAT(m.local_part, '@', d.domain) AS destination FROM mail_user m "
    "JOIN mail_domain d ON m.domain_id = d.id WHERE CONCAT(m.local_part, '@', d.domain) = '%s' "
    "AND m.active = 1 AND d.active = 1 AND (EXISTS (SELECT 1 FROM mail_forward f "
    "WHERE f.domain_id = d.id AND f.source_local_part = m.local_part AND f.active = 1) "
    "OR EXISTS (SELECT 1 FROM mail_catchall c WHERE c.domain_id = d.id AND c.active = 1)) "
    "UNION SELECT c.destination AS destination FROM mail_catchall c JOIN mail_domain d ON c.domain_id = d.id "
    "WHERE CONCAT('@', d.domain) = '%s' AND c.active = 1 AND d.active = 1 ) combined"
)

SSL_DIR = Path("/etc/boron/ssl")
DOVECOT_BORON_CONFIG = Path("/etc/dovecot/conf.d/90-boron.conf")
POSTFIX_MAIN_CONFIG = Path('/etc/postfix/main.cf')
POSTFIX_MASTER_CONFIG = Path('/etc/postfix/master.cf')
POSTFIX_SENDER_LOGIN = Path('/etc/postfix/boron/mysql-sender-login.cf')
MSMTP_CONFIG = Path("/etc/boron/msmtprc")
APPARMOR_BWRAP_PROFILE = Path("/etc/apparmor.d/boron-bwrap")
OLS_CONFIG = Path("/usr/local/lsws/conf/httpd_config.conf")
OLS_VHOST_ROOT = Path("/usr/local/lsws/conf/vhosts")
OLS_NAMESPACE_POLICY = Path("/usr/local/lsws/lsns/conf/boron-nsconf.conf")
OLS_WAF_RUNTIME = Path("/etc/modsecurity/boron-runtime.conf")
RUNTIME_BACKUP = Path("/var/lib/boron/update-runtime-backup.json")
FIREWALL_RECOVER_COMMAND = Path("/usr/local/sbin/boron-firewall-recover")
LICENSE_CRON = Path("/etc/cron.d/boron-license")
MAIL_ROUTING_CRON = Path("/etc/cron.d/boron-mail-routing")
CRON_DIR = Path("/etc/cron.d")
# deploy/<name>.cron -> /etc/cron.d/<name>. install.sh installs the core set on
# every profile; the optional set exists only where the installer profile (dns)
# or the operator (GeoIP key) enabled it, so an upgrade refreshes it in place
# but never adds it.
CORE_CRON_JOBS = ("boron-jobs", "boron-monitoring", "boron-update", "boron-maintenance",
                  "boron-sitestats", "boron-certbot-renew")
OPTIONAL_CRON_JOBS = ("boron-cloudflare", "boron-geoip")
OLS_LIFECYCLE_CONFIG = Path("/etc/systemd/system/lshttpd.service.d/boron-lifecycle.conf")
MAIL_SUBMIT_SOCKET_UNIT = Path("/etc/systemd/system/boron-mail-submit.socket")
MAIL_SUBMIT_SERVICE_UNIT = Path("/etc/systemd/system/boron-mail-submit@.service")
MAIL_EGRESS_UNIT = Path("/etc/systemd/system/boron-mail-egress.service")
SYSTEMD_UNIT_DIR = Path("/etc/systemd/system")
DEFAULT_APP_ENV_DIR = Path("/etc/boron/app-env")

DATA_PLANE_UNITS = {
    "lshttpd.service", "mariadb.service", "postfix.service", "dovecot.service",
    "pdns.service", "redis-server.service", "fail2ban.service",
}
_ALLOWED_DATA_PLANE_RELOADS: set[str] = set()
_SENSITIVE_PATH_PARTS = ("secret", "private", ".key", "password", "token", "credential")
_REDACTION_PATTERNS = (
    re.compile(r"(?i)(password|secret|token|api[_-]?key|private[_-]?key)\s*([=:])\s*[^\s#]+"),
    re.compile(r"(?i)(authorization\s*:)\s*[^\s]+"),
)


def _runtime_report_path() -> Path:
    return RUNTIME_BACKUP.with_name("update-runtime-changes.json")


def _canonical_unit(value: str) -> str:
    value = value.strip()
    aliases = {"lshttpd": "lshttpd.service", "mariadb": "mariadb.service",
               "postfix": "postfix.service", "dovecot": "dovecot.service",
               "pdns": "pdns.service", "redis-server": "redis-server.service",
               "fail2ban": "fail2ban.service"}
    return aliases.get(value, value if value.endswith((".service", ".socket")) else f"{value}.service")


def load_release_policy(path: str | None) -> dict:
    """Load the signed release document copied inside the verified archive.

    The daemon verifies the detached signature before extraction.  This
    process repeats structural checks because it is the last boundary before
    data-plane reloads.
    """
    if not path:
        # The v3.1.x finalizer predates the explicit --release-metadata
        # argument.  Its new_dir is nevertheless a publisher-signed archive,
        # so use the policy embedded in that same verified tree.  This keeps
        # N-1 upgrades fail-closed to the release's declared unit allow-list
        # instead of either blocking every required reload or authorizing an
        # unconstrained fallback.
        embedded = ROOT / "release-metadata.json"
        if not embedded.is_file():
            return {"data_plane_reloads": [], "runtime_rewrites": []}
        path = str(embedded)
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError("release metadata must be an object")
    reloads = document.get("data_plane_reloads", [])
    rewrites = document.get("runtime_rewrites", [])
    if not isinstance(reloads, list) or not all(isinstance(item, str) for item in reloads):
        raise RuntimeError("release metadata data_plane_reloads is invalid")
    if not isinstance(rewrites, list) or not all(isinstance(item, str) for item in rewrites):
        raise RuntimeError("release metadata runtime_rewrites is invalid")
    declared = {_canonical_unit(item) for item in reloads}
    unknown = declared - DATA_PLANE_UNITS
    if unknown:
        raise RuntimeError("release metadata declares unsupported data-plane units: " + ", ".join(sorted(unknown)))
    return {"data_plane_reloads": sorted(declared), "runtime_rewrites": list(dict.fromkeys(rewrites))}


def command(args: list[str], timeout: int = 900) -> None:
    if len(args) >= 3 and Path(args[0]).name == "systemctl" and args[1] in {"reload", "restart", "try-restart"}:
        unit = _canonical_unit(args[2])
        if unit in DATA_PLANE_UNITS and unit not in _ALLOWED_DATA_PLANE_RELOADS:
            raise RuntimeError(
                f"release metadata does not authorize {args[1]} of data-plane service {unit}"
            )
    subprocess.run(args, check=True, timeout=timeout, env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})


def repair_interrupted_dpkg() -> None:
    """Finish a dpkg run that was cut short before installing packages.

    The daemon installs packages itself (phpMyAdmin, the stack manager) and a
    panel restart can kill apt/dpkg mid-run; every later apt-get install then
    fails until 'dpkg --configure -a' runs. Best effort: when it cannot
    repair the state, the apt-get install that follows reports the error."""
    subprocess.run(["dpkg", "--configure", "-a"], check=False, timeout=900,
                   env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})


def _runtime_paths() -> list[Path]:
    paths = [MSMTP_CONFIG, APPARMOR_BWRAP_PROFILE, OLS_CONFIG, OLS_NAMESPACE_POLICY, OLS_WAF_RUNTIME, FIREWALL_RECOVER_COMMAND, OLS_LIFECYCLE_CONFIG,
             LICENSE_CRON, MAIL_ROUTING_CRON,
             *(CRON_DIR / name for name in (*CORE_CRON_JOBS, *OPTIONAL_CRON_JOBS)),
             DOVECOT_BORON_CONFIG, POSTFIX_MAIN_CONFIG, SSL_DIR / 'mail.crt', SSL_DIR / 'mail.key',
             POSTFIX_MASTER_CONFIG, POSTFIX_SENDER_LOGIN,
             MAIL_SUBMIT_SOCKET_UNIT, MAIL_SUBMIT_SERVICE_UNIT, MAIL_EGRESS_UNIT,
             DOVECOT_SQL_CONFIG, DOVECOT_SSO_SQL_CONFIG, DOVECOT_SSO_PASSDB_CONFIG, DOVECOT_QUOTA_CONFIG,
             DOVECOT_SIEVE_CONFIG, POSTFIX_FORWARDS_MAP,
             DOVECOT_SUSPENDED_PASSDB_CONFIG, DOVECOT_SUSPENDED_DOMAINS]
    if OLS_VHOST_ROOT.exists():
        paths.extend(sorted(OLS_VHOST_ROOT.glob("*/vhconf.conf")))
    paths.extend(_php_ini_paths())
    for pattern in ("boron-node-*.service", "boron-node-*.socket",
                    "boron-python-*.service", "boron-python-*.socket"):
        paths.extend(sorted(SYSTEMD_UNIT_DIR.glob(pattern)))
    if DEFAULT_APP_ENV_DIR.exists():
        paths.extend(sorted(DEFAULT_APP_ENV_DIR.glob("boron-node-*.service.env")))
        paths.extend(sorted(DEFAULT_APP_ENV_DIR.glob("boron-python-*.service.env")))
    return paths


def _php_ini_paths() -> list[Path]:
    """The installed lsphp php.ini files configure_php_ini_defaults edits."""
    from daemon.phpext import php_ini_path
    from shared.config import settings

    return [path for path in map(php_ini_path, settings.php_versions) if path.is_file()]


def _app_runtime_records() -> list[dict]:
    """Capture application database mode and pre-update service state."""
    from sqlalchemy import select
    from shared.config import settings
    from shared.db import read_session
    from shared.models import Account, NodeApp, PythonApp

    records = []
    with read_session() as session:
        accounts = {row.id: row.username for row in session.scalars(select(Account)).all()}
        groups = (("node", NodeApp, session.scalars(select(NodeApp)).all()),
                  ("python", PythonApp, session.scalars(select(PythonApp)).all()))
        for kind, _model, rows in groups:
            for row in rows:
                username = accounts.get(row.account_id)
                if username is None:
                    continue
                service = f"boron-{kind}-{username}-{row.id}.service"
                state = subprocess.run(["systemctl", "is-active", service], capture_output=True,
                                       text=True, check=False, timeout=10)
                records.append({
                    "kind": kind, "id": row.id, "backend_mode": row.backend_mode,
                    "service": service, "socket": service[:-8] + ".socket",
                    "env": str(Path(settings.app_env_dir) / f"{service}.env"),
                    "enabled": bool(row.enabled), "active": state.stdout.strip() == "active",
                })
    return records


def create_runtime_backup() -> None:
    """Snapshot every host file this isolation migration can change."""
    if RUNTIME_BACKUP.exists():
        raise RuntimeError(
            f"unresolved runtime migration backup exists at {RUNTIME_BACKUP}; run upgrade_runtime.py --rollback"
        )
    applications = _app_runtime_records()
    paths = list(_runtime_paths())
    for app in applications:
        paths.extend((SYSTEMD_UNIT_DIR / app["service"], SYSTEMD_UNIT_DIR / app["socket"], Path(app["env"])))
    records = []
    for path in dict.fromkeys(paths):
        exists = path.is_file()
        stat = path.stat() if exists else None
        records.append({
            "path": str(path),
            "exists": exists,
            "mode": stat.st_mode & 0o7777 if stat else None,
            "uid": stat.st_uid if stat else None,
            "gid": stat.st_gid if stat else None,
            "data": base64.b64encode(path.read_bytes()).decode("ascii") if exists else "",
        })
    RUNTIME_BACKUP.parent.mkdir(parents=True, exist_ok=True)
    _runtime_report_path().unlink(missing_ok=True)
    _write_private_atomic(RUNTIME_BACKUP,
                          json.dumps({"version": 2, "records": records, "applications": applications},
                                     separators=(",", ":")).encode(),
                          0o600, os.getuid(), os.getgid())


def _write_private_atomic(path: Path, content: bytes, mode: int, uid: int, gid: int) -> None:
    """Create private before writing any configuration or backup secrets."""
    with tempfile.NamedTemporaryFile(prefix=f'.{path.name}.', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchown(stream.fileno(), uid, gid)
            os.fchmod(stream.fileno(), mode)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def restore_runtime_backup() -> None:
    """Compensate host mutations when the application update rolls back."""
    if not RUNTIME_BACKUP.exists():
        return
    manifest = json.loads(RUNTIME_BACKUP.read_text())
    if manifest.get("version") not in (1, 2) or not isinstance(manifest.get("records"), list):
        raise RuntimeError("runtime migration backup has an unsupported format")
    records = manifest["records"]
    applications = manifest.get("applications", []) if manifest.get("version") == 2 else []
    if not isinstance(applications, list):
        raise RuntimeError("runtime migration backup has invalid application state")
    for app in applications:
        subprocess.run(["systemctl", "stop", str(app["socket"])], check=False, timeout=30)
    egress_record = next((row for row in records if row["path"] == str(MAIL_EGRESS_UNIT)), None)
    if egress_record and not egress_record["exists"]:
        # Stop while the unit file (and its ExecStop rule removal) still exists.
        subprocess.run(["systemctl", "disable", "--now", MAIL_EGRESS_UNIT.name], check=False, timeout=30)
    apparmor_record = next((row for row in records if row["path"] == str(APPARMOR_BWRAP_PROFILE)), None)
    if apparmor_record and not apparmor_record["exists"] and APPARMOR_BWRAP_PROFILE.exists():
        command(["apparmor_parser", "-R", str(APPARMOR_BWRAP_PROFILE)], timeout=30)
    for row in records:
        path = Path(row["path"])
        if not row["exists"]:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_private_atomic(path, base64.b64decode(row["data"], validate=True),
                              int(row["mode"]), int(row["uid"]), int(row["gid"]))
    if apparmor_record and apparmor_record["exists"]:
        command(["apparmor_parser", "-r", str(APPARMOR_BWRAP_PROFILE)], timeout=30)
    if applications:
        from shared.db import write_session
        from shared.models import NodeApp, PythonApp
        models = {"node": NodeApp, "python": PythonApp}
        with write_session() as session:
            for app in applications:
                model = models.get(app.get("kind"))
                row = session.get(model, int(app["id"])) if model else None
                if row is not None:
                    row.backend_mode = app["backend_mode"]
    if applications or any(row["path"] in {str(OLS_LIFECYCLE_CONFIG), str(MAIL_SUBMIT_SOCKET_UNIT), str(MAIL_SUBMIT_SERVICE_UNIT), str(MAIL_EGRESS_UNIT)} for row in records):
        command(["systemctl", "daemon-reload"], timeout=30)
        if egress_record and egress_record["exists"]:
            command(["systemctl", "restart", MAIL_EGRESS_UNIT.name], timeout=30)
        socket_record = next((row for row in records if row["path"] == str(MAIL_SUBMIT_SOCKET_UNIT)), None)
        if socket_record and not socket_record["exists"]:
            subprocess.run(["systemctl", "disable", "--now", "boron-mail-submit.socket"], check=False, timeout=30)
        elif socket_record:
            command(["systemctl", "restart", "boron-mail-submit.socket"], timeout=30)
        for app in applications:
            subprocess.run(["systemctl", "disable", "--now", str(app["socket"])], check=False, timeout=30)
            service = str(app["service"])
            if app.get("enabled"):
                command(["systemctl", "enable", service], timeout=30)
                if app.get("active"):
                    command(["systemctl", "restart", service], timeout=60)
                else:
                    subprocess.run(["systemctl", "stop", service], check=False, timeout=30)
            else:
                subprocess.run(["systemctl", "disable", "--now", service], check=False, timeout=30)
    if any(row["path"] == str(OLS_CONFIG) and row["exists"] for row in records):
        command(["/usr/local/lsws/bin/openlitespeed", "-t"], timeout=30)
        command(["systemctl", "reload", "lshttpd"], timeout=60)
    if any(row['path'] in {str(DOVECOT_BORON_CONFIG), str(DOVECOT_SQL_CONFIG), str(DOVECOT_SSO_SQL_CONFIG), str(DOVECOT_SSO_PASSDB_CONFIG), str(DOVECOT_QUOTA_CONFIG), str(DOVECOT_SUSPENDED_PASSDB_CONFIG)} and row['exists'] for row in records):
        command(['doveconf', '-n'], timeout=30)
        command(['systemctl', 'reload', 'dovecot'], timeout=60)
    if any(row['path'] == str(POSTFIX_MAIN_CONFIG) and row['exists'] for row in records):
        command(['postfix', 'check'], timeout=30)
        command(['systemctl', 'reload', 'postfix'], timeout=60)
    RUNTIME_BACKUP.unlink()
    _runtime_report_path().unlink(missing_ok=True)


def _redact_line(value: str) -> str:
    value = value.replace("\x00", "")[:1000]
    for pattern in _REDACTION_PATTERNS:
        value = pattern.sub(lambda match: f"{match.group(1)}{match.group(2) if match.lastindex and match.lastindex > 1 else ''}[REDACTED]", value)
    return value


def _safe_diff(path: Path, before: bytes, after: bytes) -> str | None:
    if any(part in path.name.lower() or part in str(path.parent).lower()
           for part in _SENSITIVE_PATH_PARTS):
        return None
    try:
        before_text = before.decode("utf-8")
        after_text = after.decode("utf-8")
    except UnicodeDecodeError:
        return None
    diff = difflib.unified_diff(
        [_redact_line(line) + "\n" for line in before_text.splitlines()],
        [_redact_line(line) + "\n" for line in after_text.splitlines()],
        fromfile="before", tofile="after", n=2,
    )
    return "".join(diff)[:4096]


def write_runtime_report() -> list[dict]:
    """Compare the private rollback manifest with current host files.

    The report intentionally stores hashes for every file and a bounded diff
    only for non-sensitive UTF-8 configuration.  Private keys and secret-like
    paths can never contribute content.
    """
    if not RUNTIME_BACKUP.exists():
        return []
    manifest = json.loads(RUNTIME_BACKUP.read_text(encoding="utf-8"))
    changes = []
    for row in manifest.get("records", []):
        path = Path(str(row.get("path", "")))
        if not path.is_absolute():
            continue
        before = base64.b64decode(row.get("data", ""), validate=True) if row.get("exists") else b""
        try:
            after = path.read_bytes() if path.is_file() and not path.is_symlink() else b""
        except OSError:
            after = b""
        before_exists = bool(row.get("exists"))
        after_exists = bool(after) or (path.is_file() and not path.is_symlink())
        if before_exists == after_exists and before == after:
            continue
        item = {
            "path": str(path),
            "result": "changed",
            "before_exists": before_exists,
            "after_exists": after_exists,
            "before_sha256": hashlib.sha256(before).hexdigest() if before_exists else None,
            "after_sha256": hashlib.sha256(after).hexdigest() if after_exists else None,
        }
        difference = _safe_diff(path, before, after)
        if difference:
            item["diff"] = difference
        changes.append(item)
        if len(changes) >= 250:
            break
    payload = json.dumps(changes, separators=(",", ":"), ensure_ascii=True).encode()
    if len(payload) > 256 * 1024:
        raise RuntimeError("runtime change report exceeds its safety limit")
    report_path = _runtime_report_path()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _write_private_atomic(report_path, payload, 0o600, os.getuid(), os.getgid())
    return changes


def read_runtime_report() -> list[dict]:
    report_path = _runtime_report_path()
    if not report_path.exists():
        return []
    document = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(document, list):
        raise RuntimeError("runtime change report is invalid")
    return document


def commit_runtime_backup() -> None:
    RUNTIME_BACKUP.unlink(missing_ok=True)
    _runtime_report_path().unlink(missing_ok=True)


def configure_firewall_recovery() -> None:
    """Updates need the same independent recovery command as fresh installs."""
    source = ROOT / "scripts" / "firewall_recover.py"
    FIREWALL_RECOVER_COMMAND.parent.mkdir(parents=True, exist_ok=True)
    temporary = FIREWALL_RECOVER_COMMAND.with_name(".boron-firewall-recover.upgrade")
    try:
        with temporary.open("wb") as target:
            target.write(source.read_bytes())
            target.flush()
            os.fsync(target.fileno())
        temporary.chmod(0o755)
        os.chown(temporary, 0, 0)
        temporary.replace(FIREWALL_RECOVER_COMMAND)
    finally:
        temporary.unlink(missing_ok=True)


def configure_license_cron() -> None:
    """Panels updated from releases without licensing need its periodic refresh."""
    _atomic_copy(ROOT / "deploy" / "boron-license.cron", LICENSE_CRON, 0o644)


def configure_scheduled_jobs() -> None:
    """Reconcile the scheduled jobs with this release.

    Releases up to 3.3.1 ran most scripts directly (`root /opt/boron/scripts/x.py`),
    which depends on an exec bit those files never had in the release
    archive: cron logged "Permission denied" and scheduled backups, usage
    snapshots/alerts, inode reconciliation, phpMyAdmin token cleanup and
    SSL-expiry warnings never ran. Every entry now names the venv
    interpreter explicitly, so the installed copies must be refreshed.
    """
    for name in CORE_CRON_JOBS:
        _atomic_copy(ROOT / "deploy" / f"{name}.cron", CRON_DIR / name, 0o644)
    for name in OPTIONAL_CRON_JOBS:
        if (CRON_DIR / name).exists():
            _atomic_copy(ROOT / "deploy" / f"{name}.cron", CRON_DIR / name, 0o644)


def configure_mail_routing_cron() -> None:
    """Mail domains wait for their public MX before local delivery starts;
    the sweep that starts it must exist on upgraded mail servers too."""
    if not POSTFIX_MAIN_CONFIG.exists():
        return
    _atomic_copy(ROOT / "deploy" / "boron-mail-routing.cron", MAIL_ROUTING_CRON, 0o644)


def configure_ols_lifecycle() -> None:
    """Bring upgraded servers in line with the fresh-install service unit."""
    OLS_LIFECYCLE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temporary = OLS_LIFECYCLE_CONFIG.with_suffix(".tmp")
    try:
        temporary.write_text("[Service]\nKillMode=mixed\nTimeoutStopSec=20s\nPIDFile=/run/openlitespeed.pid\n")
        temporary.chmod(0o644)
        os.chown(temporary, 0, 0)
        temporary.replace(OLS_LIFECYCLE_CONFIG)
    finally:
        temporary.unlink(missing_ok=True)
    command(["systemctl", "daemon-reload"], timeout=30)


def configure_ols_namespace_policy() -> None:
    """Install the namespace policy outside OLS's WebAdmin-owned conf tree."""
    source = ROOT / "deploy" / "ols-nsconf.conf"
    OLS_NAMESPACE_POLICY.parent.mkdir(parents=True, exist_ok=True)
    temporary = OLS_NAMESPACE_POLICY.with_name(".boron-nsconf.upgrade")
    try:
        temporary.write_bytes(source.read_bytes())
        temporary.chmod(0o644)
        os.chown(temporary, 0, 0)
        temporary.replace(OLS_NAMESPACE_POLICY)
    finally:
        temporary.unlink(missing_ok=True)


def _write_dovecot_config(path, content, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    # SQL connection secrets remain private from the moment of creation.
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix=f'.{path.name}.', delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(content);file.flush();os.fsync(file.fileno())
            os.fchmod(file.fileno(), mode)
            shutil.chown(temporary, user='root', group='dovecot' if mode == 0o640 else 'root')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def _with_mailbox_quota(content):
    def quota_query(match):
        query = match.group(1).strip().rstrip(';')
        if re.search(r'\bAS\s+quota_rule\b', query, re.IGNORECASE):
            return match.group(0)
        # Preserve the operator's existing uid/gid/home/mail/extra fields.
        limit = ("SELECT u.quota_mb FROM mail_user u JOIN mail_domain d ON d.id=u.domain_id "
                 "WHERE CONCAT(u.local_part, '@', d.domain)='%u' AND u.active=1 AND d.active=1")
        return ("user_query = SELECT original.*, CONCAT('*:storage=', COALESCE((" + limit +
                "),0), 'M') AS quota_rule FROM (" + query + ") AS original")
    updated, count = re.subn(r'(?m)^user_query\s*=\s*(.*)$', quota_query, content, count=1)
    if count != 1:
        raise RuntimeError('Dovecot SQL user_query was not found')
    return updated



def configure_dovecot() -> None:
    path = DOVECOT_SQL_CONFIG
    if not path.exists():
        return
    content = path.read_text()
    updated, count = re.subn(r'(?m)^password_query\s*=.*$', PASSWORD_QUERY, content, count=1)
    if count != 1:
        raise RuntimeError('Dovecot SQL password_query was not found')
    updated = _with_mailbox_quota(updated)
    sso_content = re.sub(r'(?m)^password_query\s*=.*$', SSO_PASSWORD_QUERY, updated, count=1)
    # A separate password database lets Dovecot verify either password. A SQL
    # UNION/LIMIT selects just one hash and breaks normal mail clients while
    # a temporary webmail credential is active.
    passdb = ('# Managed Boron temporary webmail authentication.\n'
              'passdb {\n  driver = sql\n'
              f'  args = {DOVECOT_SSO_SQL_CONFIG}\n'
              '  result_success = return-ok\n'
              '  result_failure = continue\n'
              '  result_internalfail = return-fail\n}\n')
    _write_dovecot_config(DOVECOT_SSO_SQL_CONFIG, sso_content, 0o640)
    if updated != content:
        _write_dovecot_config(path, updated, 0o640)
    _write_dovecot_config(DOVECOT_SSO_PASSDB_CONFIG, passdb, 0o644)
    _write_dovecot_config(DOVECOT_QUOTA_CONFIG, """# Managed Boron per-mailbox limits, supplied by SQL userdb.
mail_plugins = $mail_plugins quota
protocol imap {
  mail_plugins = $mail_plugins imap_quota
}
protocol lmtp {
  lmtp_rcpt_check_quota = yes
}
plugin {
  quota = count:User quota
  quota_vsizes = yes
  quota_rule = *:storage=0
}
""", 0o644)
    # The list first (an existing one is the current state), then the passdb.
    if not DOVECOT_SUSPENDED_DOMAINS.exists():
        _write_dovecot_config(DOVECOT_SUSPENDED_DOMAINS, "", 0o640)
    _write_dovecot_config(DOVECOT_SUSPENDED_PASSDB_CONFIG, DOVECOT_SUSPENDED_PASSDB, 0o644)
    configure_dovecot_sieve()


def configure_dovecot_sieve() -> None:
    """Load Pigeonhole for LMTP delivery (dovecot-sieve provides the module).

    Without it every user and global Sieve script is ignored. Loading a
    plugin whose module is missing would stop Dovecot, so the managed file
    is only present while the module is; main() installs dovecot-sieve first.
    """
    if DOVECOT_SIEVE_PLUGIN.is_file():
        _write_dovecot_config(DOVECOT_SIEVE_CONFIG, DOVECOT_SIEVE_CONTENT, 0o644)
    else:
        DOVECOT_SIEVE_CONFIG.unlink(missing_ok=True)


def configure_suspended_mail_logins() -> None:
    """Block mail logins of accounts suspended by a release that left them
    working; later suspensions maintain the list themselves."""
    if not DOVECOT_SUSPENDED_PASSDB_CONFIG.exists():
        return
    from daemon import handlers_mail
    handlers_mail.reconcile_suspended_mail_logins()


def configure_mail_client_ports() -> None:
    """Open implicit-TLS submission (465) where the panel manages UFW, once:
    when this upgrade introduces the service (dkim.configure_signer adds it
    later in main). install.sh opens it and the other mail client ports on
    fresh servers; later upgrades leave the firewall as the operator keeps it.
    configure_signer adds the service only where OpenDKIM and Postfix are
    installed, so without them the port is left closed (nothing would listen)."""
    if not POSTFIX_MASTER_CONFIG.exists() or shutil.which("ufw") is None:
        return
    if shutil.which("opendkim") is None or shutil.which("postconf") is None:
        return
    if re.search(r"(?m)^(submissions|smtps|465)\s+inet\s", POSTFIX_MASTER_CONFIG.read_text()):
        return
    status = subprocess.run(["ufw", "status"], capture_output=True, text=True, timeout=30, check=False)
    if status.stdout.startswith("Status: active"):
        command(["ufw", "allow", "465/tcp"], timeout=60)


def configure_postfix_forwards() -> None:
    """Reconcile the virtual alias query so forwarders keep local delivery."""
    path = POSTFIX_FORWARDS_MAP
    if not path.exists():
        return
    content = path.read_text()
    updated, count = re.subn(r'(?m)^query\s*=.*$', lambda _match: FORWARDS_QUERY, content, count=1)
    if count != 1:
        raise RuntimeError('Postfix forwarder map query was not found')
    if updated == content:
        return
    # The map holds the read-only MariaDB password: keep its root:postfix
    # ownership and 0640 mode.
    stat_result = path.stat()
    _write_private_atomic(path, updated.encode(), 0o640, stat_result.st_uid, stat_result.st_gid)


def _atomic_copy(source: Path, target: Path, mode: int) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.boron-upgrade.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, mode)
        with source.open("rb") as source_handle, os.fdopen(fd, "wb") as target_handle:
            fd = -1
            shutil.copyfileobj(source_handle, target_handle)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        os.replace(temporary, target)
    finally:
        if fd >= 0:
            os.close(fd)
        temporary.unlink(missing_ok=True)


def configure_mail_tls() -> None:
    """Move Postfix/Dovecot to a service-specific certificate pair.

    Existing servers start with a matching copy of the previous default
    certificate. Future certbot deploys replace only mail.crt/mail.key.
    """
    SSL_DIR.mkdir(parents=True, exist_ok=True)
    default_cert, default_key = SSL_DIR / "default.crt", SSL_DIR / "default.key"
    mail_cert, mail_key = SSL_DIR / "mail.crt", SSL_DIR / "mail.key"
    if (not mail_cert.exists() or not mail_key.exists()) and default_cert.exists() and default_key.exists():
        _atomic_copy(default_cert, mail_cert, 0o644)
        _atomic_copy(default_key, mail_key, 0o600)

    if DOVECOT_BORON_CONFIG.exists():
        content = DOVECOT_BORON_CONFIG.read_text()
        updated, cert_count = re.subn(
            r"(?m)^ssl_cert\s*=.*$", f"ssl_cert = <{mail_cert}", content, count=1,
        )
        updated, key_count = re.subn(
            r"(?m)^ssl_key\s*=.*$", f"ssl_key = <{mail_key}", updated, count=1,
        )
        if cert_count != 1 or key_count != 1:
            raise RuntimeError("Dovecot Boron TLS settings were not found")
        if updated != content:
            metadata = DOVECOT_BORON_CONFIG.stat()
            temporary = DOVECOT_BORON_CONFIG.with_name(f".{DOVECOT_BORON_CONFIG.name}.boron-upgrade")
            temporary.write_text(updated)
            os.chmod(temporary, metadata.st_mode & 0o777)
            os.chown(temporary, metadata.st_uid, metadata.st_gid)
            os.replace(temporary, DOVECOT_BORON_CONFIG)

    if shutil.which("postconf"):
        command(["postconf", "-e", f"smtpd_tls_cert_file = {mail_cert}"], timeout=30)
        command(["postconf", "-e", f"smtpd_tls_key_file = {mail_key}"], timeout=30)

    from shared.config import settings
    from scripts.ssl_deploy_hook import LETSENCRYPT_LIVE_DIR, _deploy_mail_service_certificate
    canonical = settings.mail_hostname or settings.webmail_hostname or settings.panel_hostname
    lineage = LETSENCRYPT_LIVE_DIR / canonical if canonical else None
    if lineage is not None and (lineage / 'fullchain.pem').exists() and (lineage / 'privkey.pem').exists():
        _deploy_mail_service_certificate(canonical)


def configure_roundcube() -> None:
    root = Path("/var/www/roundcube")
    config = root / "config/config.inc.php"
    source = ROOT / "integrations/roundcube/boron_sso"
    if not root.exists() or not config.exists():
        return
    target = root / "plugins/boron_sso"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("boron_sso.php", "package.xml"):
        shutil.copy2(source / name, target / name)
    content = config.read_text()
    if "'boron_sso'" not in content and '"boron_sso"' not in content:
        pattern = r"(\$config\['plugins'\]\s*=\s*\[)([^\]]*)(\]\s*;)"
        content, count = re.subn(pattern, lambda m: m.group(1) + m.group(2).rstrip() + (", " if m.group(2).strip() else "") + "'boron_sso'" + m.group(3), content, count=1)
        if count != 1:
            raise RuntimeError("Roundcube plugin configuration was not found")
        config.write_text(content)


def configure_phpmyadmin() -> None:
    """Servers installed before phpMyAdmin was provisioned have no package,
    so Server Setup could never publish it and every sign-on was dead.
    Install it on database servers and republish it when a hostname is set.

    Best-effort: phpMyAdmin is optional, so a failed install (no network, an
    apt error) must not roll back the update. pma logs the failure and
    records it for the readiness checks, which the update finalizer stores
    as an advisory; Server Setup can retry the installation later.
    """
    from daemon import pma
    if not pma.database_component_enabled():
        return
    try:
        pma.provision(source="upgrade", budget_seconds=pma.UPGRADE_INSTALL_BUDGET_SECONDS)
    except Exception:  # noqa: BLE001 - recorded by pma.provision; the upgrade goes on
        pass


def configure_account_resource_parent() -> None:
    """Enable accounting on the common ancestor used by OLS and logind."""
    if not Path("/sys/fs/cgroup/cgroup.controllers").exists():
        raise RuntimeError("Boron resource limits require cgroups v2")
    command([
        "systemctl", "set-property", "user.slice",
        "CPUAccounting=yes", "MemoryAccounting=yes", "IOAccounting=yes", "TasksAccounting=yes",
    ], timeout=30)


def configure_bubblewrap_mail() -> None:
    """Install the sandbox-safe identity-bound mail submission configuration."""
    from shared.config import settings

    hostname = settings.panel_hostname or "localhost"
    content = (
        "defaults\n"
        "auth off\n"
        "tls off\n"
        "syslog off\n\n"
        "account default\n"
        "host 127.0.0.1\n"
        "port 25\n"
        "socket /run/boron-mail-submit.sock\n"
        "auto_from on\n"
        f"maildomain {hostname}\n"
    )
    MSMTP_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temporary = MSMTP_CONFIG.with_name(f".{MSMTP_CONFIG.name}.boron-upgrade")
    temporary.write_text(content)
    temporary.chmod(0o644)
    temporary.replace(MSMTP_CONFIG)

    for source, target in (
        (ROOT / "deploy" / "boron-mail-submit.socket", MAIL_SUBMIT_SOCKET_UNIT),
        (ROOT / "deploy" / "boron-mail-submit@.service", MAIL_SUBMIT_SERVICE_UNIT),
    ):
        temporary = target.with_name(f".{target.name}.boron-upgrade")
        temporary.write_bytes(source.read_bytes())
        temporary.chmod(0o644)
        temporary.replace(target)
    command(["systemctl", "daemon-reload"], timeout=30)
    command(["systemctl", "enable", "boron-mail-submit.socket"], timeout=30)
    command(["systemctl", "restart", "boron-mail-submit.socket"], timeout=30)


def configure_mail_egress() -> None:
    """Only Postfix and root may open TCP/25, local or remote (tenant spoofing guard)."""
    source = ROOT / "deploy" / MAIL_EGRESS_UNIT.name
    MAIL_EGRESS_UNIT.parent.mkdir(parents=True, exist_ok=True)
    temporary = MAIL_EGRESS_UNIT.with_name(f".{MAIL_EGRESS_UNIT.name}.boron-upgrade")
    try:
        temporary.write_bytes(source.read_bytes())
        temporary.chmod(0o644)
        temporary.replace(MAIL_EGRESS_UNIT)
    finally:
        temporary.unlink(missing_ok=True)
    command(["systemctl", "daemon-reload"], timeout=30)
    command(["systemctl", "enable", MAIL_EGRESS_UNIT.name], timeout=30)
    # restart (not start) re-runs ExecStart on an already-active oneshot.
    command(["systemctl", "restart", MAIL_EGRESS_UNIT.name], timeout=60)


def configure_bubblewrap_apparmor() -> None:
    """Allow only Bubblewrap through Ubuntu 24.04's userns AppArmor gate."""
    source = ROOT / "deploy" / "boron-bwrap.apparmor"
    content = source.read_text()
    APPARMOR_BWRAP_PROFILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = APPARMOR_BWRAP_PROFILE.with_name(f".{APPARMOR_BWRAP_PROFILE.name}.boron-upgrade")
    temporary.write_text(content)
    temporary.chmod(0o644)
    temporary.replace(APPARMOR_BWRAP_PROFILE)
    command(["apparmor_parser", "-r", str(APPARMOR_BWRAP_PROFILE)], timeout=30)


def configure_php_ini_defaults() -> bool:
    """Customer PHP must not print errors (file system paths, queries) to
    visitors: display_errors and display_startup_errors Off, log_errors On in
    every lsphp php.ini, the same values install.sh writes on fresh servers
    (daemon/phpdirectives.PHP_INI_DEFAULTS). Returns whether a file changed:
    warm PHP workers keep the old values until they are restarted."""
    from daemon import phpext

    return bool(phpext.reconcile_php_ini_defaults())


def configure_account_lifecycle_state() -> None:
    """Bring accounts suspended/terminated by older releases to current rules.

    Suspension now also expires the Linux login (SSH keys, SFTP, git and cron
    were still accepted) and ends what the account left running, termination
    frees the account's hostnames, and enforced inode limits are back on the
    quota of accounts restored by an older release. Every live account's
    resource limits are re-applied, so its slice names the whole disk and
    byte-exact I/O bandwidth. Fresh installs have no accounts, so install.sh
    needs no counterpart.
    """
    from daemon import handlers_account, inode_quotas, resource_manager

    handlers_account.reconcile_suspended_logins()
    handlers_account.release_terminated_domains()
    inode_quotas.reapply_enforced()
    resource_manager.reapply_all()


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--rollback", action="store_true")
    action.add_argument("--commit", action="store_true")
    action.add_argument("--report", action="store_true")
    parser.add_argument("--release-metadata")
    return parser.parse_args(sys.argv[1:] if argv is None else argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    policy = load_release_policy(args.release_metadata)
    _ALLOWED_DATA_PLANE_RELOADS.clear()
    _ALLOWED_DATA_PLANE_RELOADS.update(policy["data_plane_reloads"])
    if args.rollback:
        restore_runtime_backup()
        return 0
    if args.commit:
        commit_runtime_backup()
        return 0
    if args.report:
        print(json.dumps(read_runtime_report(), separators=(",", ":")))
        return 0
    missing_packages = []
    if shutil.which("opendkim") is None or shutil.which("opendkim-testkey") is None:
        missing_packages.extend(["opendkim", "opendkim-tools"])
    if shutil.which("bwrap") is None:
        missing_packages.append("bubblewrap")
    if shutil.which("msmtp") is None:
        missing_packages.append("msmtp")
    if shutil.which("apparmor_parser") is None:
        missing_packages.append("apparmor")
    if Path("/etc/dovecot/dovecot.conf").exists() and not DOVECOT_SIEVE_PLUGIN.is_file():
        missing_packages.append("dovecot-sieve")
    if Path("/etc/dovecot/dovecot.conf").exists() and not DOVECOT_POP3_PROTOCOL.is_file():
        missing_packages.append("dovecot-pop3d")
    if missing_packages:
        repair_interrupted_dpkg()
        command(["apt-get", "update"])
        command(["apt-get", "install", "-y", *missing_packages])
    # Rotate historically readable keys before loading settings into memory.
    command([sys.executable, str(ROOT / 'scripts/reconcile_powerdns_credentials.py'),
             '--defer-panel-restart'], timeout=180)
    from shared.db import init_db
    from daemon import dkim, ols, spamfilter, webmail_sso

    init_db()
    create_runtime_backup()
    configure_firewall_recovery()
    configure_license_cron()
    configure_scheduled_jobs()
    configure_mail_routing_cron()
    configure_ols_lifecycle()
    configure_ols_namespace_policy()
    webmail_sso.ensure_schema()
    configure_dovecot()
    configure_suspended_mail_logins()
    configure_postfix_forwards()
    spamfilter.repair_virtual_config_permissions()
    configure_mail_tls()
    configure_roundcube()
    configure_phpmyadmin()
    configure_account_resource_parent()
    configure_bubblewrap_mail()
    configure_mail_egress()
    configure_mail_client_ports()
    configure_bubblewrap_apparmor()
    configure_account_lifecycle_state()
    php_ini_changed = configure_php_ini_defaults()
    # Server policy is Off-by-default; refreshing main first keeps every
    # existing vhost on the native namespace until its own atomic refresh
    # enables Bubblewrap. A mid-migration failure therefore degrades to the
    # already-verified compatibility backend instead of breaking PHP.
    ols.refresh_main_config()
    ols.refresh_all_vhosts(recycle_php=php_ini_changed)
    dkim.configure_signer()
    # Placeholder SOA MNAME and unsplit >255-octet TXT (DKIM) in existing
    # zones; best-effort, never fails the upgrade.
    from daemon import dnssetup
    dnssetup.reconcile_zone_records()
    if Path("/etc/dovecot/dovecot.conf").exists():
        command(["doveconf", "-n"], timeout=30)
        command(["systemctl", "reload", "dovecot"], timeout=60)
    command(["postfix", "check"], timeout=30)
    command(["systemctl", "reload", "postfix"], timeout=60)
    write_runtime_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
