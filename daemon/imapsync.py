"""IMAPSync migrations (missing-features batch, goal feature 1):
customer self-service email migration from an external IMAP server into an
existing Forgehost mailbox.

**Credential handling (the goal's explicit, non-negotiable requirement --
"credentials never stored after job completes or logged anywhere")**: this
module never writes a source or destination password to the database, ever
-- `shared/models.ImapMigrationJob` has no password column at all (see its
own docstring for why that's structural, not just discipline). Both
passwords exist only as Python local variables for the lifetime of
`_run_job` (a background thread), passed to `imapsync` via `--passfile1`/
`--passfile2` pointing at 0600 root-only files under a private
per-job tmpfs directory (`/run/forgehost/imapsync/<job_id>/`) that is
removed in a `finally` block regardless of outcome -- never via argv (which
`ps aux` on this same box could read) and never logged (`daemon/procutil.run`'s
own `redact` list additionally scrubs both values from the one INFO-level
"exec: ..." log line it always writes).

**Destination credentials**: Forgehost never stores a mailbox's own IMAP/
Dovecot password in recoverable form (SQL-backed virtual mailboxes,
ARCHITECTURE.md SS4, hashed at rest -- `shared/passwords.py`) -- so imapsync,
which needs a real destination IMAP login the same as the source, cannot be
handed a password Forgehost doesn't have. Rather than silently rotating the
mailbox's password (which would break the customer's own already-configured
mail clients) or storing it, the customer supplies BOTH credentials up
front, exactly like every other IMAP-to-IMAP migration tool (including
imapsync's own typical CLI usage): their existing Forgehost mailbox
password (which they already know, having set it) alongside the source
server's credentials. Both are handled with the identical
never-stored/never-logged discipline above.

**Internal-IP blocking**: `shared/validation.validate_imap_source_host`
(shared with webhooks' own SSRF guard shape) rejects a source host that
resolves to a private/loopback/link-local/reserved address at job-creation
time; this module does not re-resolve at connect time the way
`daemon/webhooks.py` does for delivery-time SSRF (imapsync itself makes the
real connection, and its own connection failure surfaces as a normal job
failure -- there is no "deliver now vs. later" TOCTOU window here the way
there is for a webhook queued for retry).

**Per-folder progress**: this project's other long-running background jobs
(WordPressJob, AppInstallJob, CpanelImportJob) all report progress via
discrete STEPS -- one `daemon/procutil.run()` call per step, updating the
job row between calls -- rather than parsing a single long-running
subprocess's live stdout. This module follows the same convention: one
`imapsync` invocation PER FOLDER (`--folder <name>`), so "live progress per
folder" falls directly out of polling the job row between folders, with no
new streaming-subprocess primitive needed in `daemon/procutil.py`. This also
gives clean per-folder success/failure isolation for the goal's own
per-folder results report, and a natural, low-overhead cancellation point
(checked before starting each next folder).
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, ImapMigrationJob, MailDomain, MailUser, utcnow
from shared.validation import (
    ValidationError,
    validate_domain,
    validate_email_address,
    validate_imap_source_host,
    validate_imap_source_port,
    validate_mailbox_local_part,
    validate_username,
)

from daemon.procutil import run

IMAPSYNC_BIN = "/usr/local/bin/imapsync"
# The tool's own official upstream repository (github.com/imapsync/imapsync,
# mirrored from the canonical gitlab.com/imapsync/imapsync) -- a single,
# no-compilation Perl script, matching this project's existing "fetch a
# well-known FOSS tool server-wide on first use if missing" precedent
# (daemon/wpcli.py's wp-cli.phar, daemon/composerui.py's composer.phar,
# shared/config.py's own wpcli_download_url/composer_download_url).
IMAPSYNC_DOWNLOAD_URL = "https://raw.githubusercontent.com/imapsync/imapsync/master/imapsync"
# imapsync's own documented required/recommended Perl modules for a
# password-auth IMAP-to-IMAP sync (OAuth-specific modules deliberately
# excluded -- this feature only supports password auth, matching the goal's
# own "enter host/port/email/password" scope) -- confirmed present in
# Ubuntu 24.04's own repos (apt-cache policy) before relying on them, the
# same "don't assume a package is packaged, check" discipline
# shared/config.py's php_versions comment documents for lsphp packages.
IMAPSYNC_APT_PACKAGES = (
    "libmail-imapclient-perl", "libio-socket-ssl-perl", "libauthen-ntlm-perl",
    "libunicode-string-perl", "libfile-copy-recursive-perl", "libterm-readkey-perl",
    "libdata-uniqid-perl", "libregexp-common-perl", "libsys-meminfo-perl",
    "libio-tee-perl", "libfile-tail-perl", "libtest-pod-perl", "libtest-mockobject-perl",
    "libjson-webtoken-perl", "libcrypt-openssl-rsa-perl", "libmodule-scandeps-perl",
    "liblist-moreutils-perl", "libwww-perl", "libcgi-pm-perl",
)

IMAPSYNC_RUN_DIR = "/run/forgehost/imapsync"
DEST_HOST = "127.0.0.1"
DEST_PORT = 993
FOLDER_TIMEOUT_SECONDS = 600
LIST_FOLDERS_TIMEOUT_SECONDS = 60
MAX_FOLDERS_PER_JOB = 200

IMAPSYNC_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="imapsync")


class ImapSyncError(Exception):
    pass


def ensure_installed() -> None:
    """Idempotent -- a no-op once /usr/local/bin/imapsync exists. Not run
    at daemon startup (a network-fetching, package-installing action on
    every boot would be surprising) -- called lazily, once, the first time
    a migration actually starts, same "explicit action, not an import-time
    or startup side effect" posture bootstrap_baseline/bootstrap_pma/
    bootstrap_spamassassin already establish for their own one-time infra
    setup."""
    if Path(IMAPSYNC_BIN).exists():
        return
    apt_result = run(["apt-get", "install", "-y", *IMAPSYNC_APT_PACKAGES], timeout=180)
    if not apt_result.ok:
        raise ImapSyncError(f"failed to install imapsync's Perl dependencies: {apt_result.stderr.strip() or apt_result.stdout.strip()}")
    download_result = run(["curl", "-sSL", "-f", "-o", IMAPSYNC_BIN, IMAPSYNC_DOWNLOAD_URL], timeout=60)
    if not download_result.ok:
        raise ImapSyncError(f"failed to download imapsync: {download_result.stderr.strip() or download_result.stdout.strip()}")
    os.chmod(IMAPSYNC_BIN, 0o755)
    check = run([IMAPSYNC_BIN, "--version"], timeout=30)
    if not check.ok:
        raise ImapSyncError(f"imapsync installed but --version failed: {check.stderr.strip() or check.stdout.strip()}")


def _job_passfile_dir(job_id: int) -> str:
    path = f"{IMAPSYNC_RUN_DIR}/{job_id}"
    Path(path).mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def _write_passfile(dir_path: str, name: str, password: str) -> str:
    path = f"{dir_path}/{name}"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, password.encode("utf-8"))
    finally:
        os.close(fd)
    return path


def _cleanup_passfile_dir(dir_path: str) -> None:
    shutil.rmtree(dir_path, ignore_errors=True)


_HOST1_FOLDERS_HEADER_RE = re.compile(r"^Host1:\s*folders list", re.IGNORECASE)
_HOST2_FOLDERS_HEADER_RE = re.compile(r"^Host2:\s*folders list", re.IGNORECASE)
_BRACKETED_FOLDER_RE = re.compile(r"^\[(.+)\]$")


def _parse_host1_folders(stdout: str) -> list[str]:
    """Empirically corrected during this feature's live verification (a
    real run against a real Dovecot mailbox, not just reasoned about): this
    imapsync version (2.229) emits the Host1 folder list as bare
    "[FolderName]" lines directly under a "Host1: folders list (...)"
    header, immediately followed by an identical "Host2: folders list"
    section -- neither of the two patterns this parser originally shipped
    with ("Host1 N/N Names: [...]" and "Host1 folder: ...") ever appears in
    this version's real output at all, so the original code always
    silently returned zero folders. Only the Host1 section is parsed (up
    to the Host2 header) so list_source_folders's host2=host1 self-connect
    trick below doesn't double-count the identical list Host2 also
    prints."""
    folders: list[str] = []
    in_host1_section = False
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if _HOST1_FOLDERS_HEADER_RE.match(line):
            in_host1_section = True
            continue
        if _HOST2_FOLDERS_HEADER_RE.match(line):
            break
        if in_host1_section:
            m = _BRACKETED_FOLDER_RE.match(line)
            if m:
                folders.append(m.group(1))
    return folders


def list_source_folders(source_host: str, source_port: int, source_email: str, source_password: str, use_ssl: bool = True) -> list[str]:
    """Lists the source server's folders without syncing any messages
    (`--justfolders`) -- used both by the UI's "select mailboxes" step and
    as a cheap connectivity check before a real job is created.

    **Empirically corrected during this feature's live verification**:
    imapsync's own `--help` documents that "imapsync --host1 imaphost
    alone implies --justconnect" -- confirmed live that supplying only
    `--host1`/`--user1`/`--passfile1` (no host2 at all) makes imapsync
    silently skip login and folder listing entirely and just print
    connection banners, regardless of `--justfolders` also being passed.
    imapsync has no real single-host mode. The fix: point host2 at the
    SAME source server with the SAME credentials (a harmless self-sync --
    `--justfolders` never touches messages, so this never writes
    anything), which makes imapsync actually authenticate and list
    real folders."""
    ensure_installed()
    with tempfile.TemporaryDirectory(dir=IMAPSYNC_RUN_DIR if Path(IMAPSYNC_RUN_DIR).exists() else None) as tmp:
        os.chmod(tmp, 0o700)
        passfile = _write_passfile(tmp, "passfile1", source_password)
        args = [
            IMAPSYNC_BIN,
            "--host1", source_host, "--port1", str(source_port), "--user1", source_email, "--passfile1", passfile,
            "--host2", source_host, "--port2", str(source_port), "--user2", source_email, "--passfile2", passfile,
            "--justfolders", "--nofoldersizes",
        ]
        args += ["--ssl1", "--ssl2"] if use_ssl else ["--notls1", "--notls2"]
        result = run(args, timeout=LIST_FOLDERS_TIMEOUT_SECONDS, redact=[source_password])
    if not result.ok:
        raise ImapSyncError(f"could not list folders on source server: {_summarize_error(result.stdout, result.stderr)}")
    return _parse_host1_folders(result.stdout)


def _summarize_error(stdout: str, stderr: str) -> str:
    text = (stderr.strip() or stdout.strip())
    return text[-800:] if text else "imapsync exited non-zero with no output"


def _domain_account_for_mailbox(session, domain_name: str, local_part: str) -> tuple[MailUser, Account]:
    mail_user = session.scalar(
        select(MailUser).where(MailUser.domain == domain_name, MailUser.local_part == local_part)
    )
    if mail_user is None:
        raise RuntimeError(f"mailbox '{local_part}@{domain_name}' does not exist")
    mail_domain = session.scalar(select(MailDomain).where(MailDomain.domain == domain_name))
    if mail_domain is None:
        raise RuntimeError(f"mail domain '{domain_name}' has no owning account")
    account = session.get(Account, mail_domain.account_id)
    if account is None:
        raise RuntimeError(f"mail domain '{domain_name}' has no owning account")
    return mail_user, account


def rpc_list_folders(params: dict) -> dict:
    source_host = validate_imap_source_host(params["source_host"])
    source_port = validate_imap_source_port(params.get("source_port", 993))
    source_email = validate_email_address(params["source_email"])
    source_password = params["source_password"]
    if not isinstance(source_password, str) or not source_password:
        raise ValidationError("source_password must not be empty")
    use_ssl = bool(params.get("source_ssl", True))
    folders = list_source_folders(source_host, source_port, source_email, source_password, use_ssl)
    return {"folders": folders}


def start_migration(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    local_part = validate_mailbox_local_part(params["local_part"])
    source_host = validate_imap_source_host(params["source_host"])
    source_port = validate_imap_source_port(params.get("source_port", 993))
    source_email = validate_email_address(params["source_email"])
    source_password = params["source_password"]
    dest_password = params["dest_password"]
    use_ssl = bool(params.get("source_ssl", True))
    folders = params.get("folders") or []
    if not isinstance(folders, list) or len(folders) > MAX_FOLDERS_PER_JOB:
        raise ValidationError(f"folders must be a list of at most {MAX_FOLDERS_PER_JOB} names")
    if not isinstance(source_password, str) or not source_password:
        raise ValidationError("source_password must not be empty")
    if not isinstance(dest_password, str) or not dest_password:
        raise ValidationError("dest_password must not be empty")

    with write_session() as session:
        _mail_user, account = _domain_account_for_mailbox(session, domain_name, local_part)
        job = ImapMigrationJob(
            account_id=account.id,
            mailbox=f"{local_part}@{domain_name}",
            source_host=source_host,
            source_port=source_port,
            source_email=source_email,
            folders=folders,
            status="pending",
        )
        session.add(job)
        session.flush()
        job_id = job.id
        result = _job_to_dict(job)

    IMAPSYNC_EXECUTOR.submit(_run_job, job_id, source_password, dest_password, use_ssl)
    return result


def _job_to_dict(job: ImapMigrationJob) -> dict:
    return {
        "id": job.id,
        "account_id": job.account_id,
        "mailbox": job.mailbox,
        "source_host": job.source_host,
        "source_port": job.source_port,
        "source_email": job.source_email,
        "folders": job.folders or [],
        "status": job.status,
        "current_folder": job.current_folder,
        "folders_total": job.folders_total,
        "folders_done": job.folders_done,
        "messages_total": job.messages_total,
        "messages_done": job.messages_done,
        "progress_message": job.progress_message,
        "results": job.results or [],
        "error": job.error,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def _update_job(job_id: int, **fields) -> str | None:
    """Returns the (possibly just-updated) status, so the worker loop can
    check for a cancellation request without a second query."""
    with write_session() as session:
        job = session.get(ImapMigrationJob, job_id)
        if job is None:
            return None
        for key, value in fields.items():
            setattr(job, key, value)
        session.flush()
        return job.status


# Empirically corrected during this feature's live verification: this
# imapsync version's (2.229) real Statistics block prints
# "Messages transferred                    : N" (no "Total" prefix,
# variable padding) -- neither of this parser's original two patterns
# ("Total messages transferred:" / "Msgs found in host1...host2...")
# ever appears in real output, so every real migration always reported
# 0 messages copied despite genuinely transferring them (confirmed via
# a real IMAP SELECT/SEARCH against the destination Dovecot mailbox
# showing the real message count while the job row showed 0).
_MSG_COPIED_RE = re.compile(r"^Messages transferred\s*:\s*(\d+)", re.IGNORECASE | re.MULTILINE)


def _parse_folder_result(stdout: str) -> int:
    m = _MSG_COPIED_RE.search(stdout)
    return int(m.group(1)) if m else 0


def _run_job(job_id: int, source_password: str, dest_password: str, use_ssl: bool) -> None:
    dir_path = _job_passfile_dir(job_id)
    try:
        try:
            ensure_installed()
        except ImapSyncError as exc:
            _update_job(job_id, status="failed", error=str(exc), completed_at=utcnow())
            return

        with write_session() as session:
            job = session.get(ImapMigrationJob, job_id)
            mailbox = job.mailbox
            requested_folders = list(job.folders or [])
            source_host, source_port, source_email = job.source_host, job.source_port, job.source_email

        local_part, _, domain_name = mailbox.partition("@")

        _update_job(job_id, status="connecting", progress_message="Listing source folders")
        try:
            folders = requested_folders or list_source_folders(source_host, source_port, source_email, source_password, use_ssl)
        except ImapSyncError as exc:
            _update_job(job_id, status="failed", error=str(exc), completed_at=utcnow())
            return
        if not folders:
            _update_job(job_id, status="failed", error="no folders found on source server", completed_at=utcnow())
            return

        status = _update_job(job_id, status="running", folders_total=len(folders), progress_message="Starting migration")
        source_passfile = _write_passfile(dir_path, "passfile1", source_password)
        dest_passfile = _write_passfile(dir_path, "passfile2", dest_password)

        results: list[dict] = []
        folders_done = 0
        messages_done = 0
        for folder in folders:
            status = _update_job(job_id, current_folder=folder, progress_message=f"Syncing folder '{folder}'")
            if status == "cancelled":
                break

            args = [
                IMAPSYNC_BIN,
                "--host1", source_host, "--port1", str(source_port), "--user1", source_email, "--passfile1", source_passfile,
                "--host2", DEST_HOST, "--port2", str(DEST_PORT), "--user2", mailbox, "--passfile2", dest_passfile,
                "--folder", folder, "--nofoldersizes", "--noexpunge", "--syncinternaldates",
                "--sslargs2", "SSL_verify_mode=0",
            ]
            args += ["--ssl1"] if use_ssl else ["--notls1"]
            args += ["--ssl2"]

            result = run(args, timeout=FOLDER_TIMEOUT_SECONDS, redact=[source_password, dest_password])
            if result.ok:
                copied = _parse_folder_result(result.stdout)
                messages_done += copied
                folders_done += 1
                results.append({"folder": folder, "status": "ok", "messages": copied, "detail": ""})
            else:
                results.append({"folder": folder, "status": "failed", "messages": 0, "detail": _summarize_error(result.stdout, result.stderr)})

            status = _update_job(
                job_id, folders_done=folders_done, messages_done=messages_done, results=results,
                progress_message=f"Completed folder '{folder}' ({folders_done}/{len(folders)})",
            )
            if status == "cancelled":
                break

        final_status = "cancelled" if status == "cancelled" else ("completed" if all(r["status"] == "ok" for r in results) else "failed")
        error = None if final_status != "failed" else "one or more folders failed to sync -- see results for detail"
        _update_job(
            job_id, status=final_status, current_folder=None, results=results,
            progress_message="Migration cancelled" if final_status == "cancelled" else "Migration finished",
            error=error, completed_at=utcnow(),
        )
    finally:
        # source_password/dest_password fall out of scope with this function
        # returning -- never assigned to any object that outlives it, never
        # written anywhere but the passfiles just removed here.
        _cleanup_passfile_dir(dir_path)


def _job_for_account(session, job_id: int, username: str) -> ImapMigrationJob:
    """Cross-account IDOR guard (job_id is a small sequential int, not a
    capability -- same class of check backup.py's get_job already applies
    to its own job ids): resolves username independently and reports "not
    found" identically whether the job doesn't exist or belongs to a
    different account, rather than a distinguishable permission error."""
    account = session.scalar(select(Account).where(Account.username == username))
    job = session.get(ImapMigrationJob, job_id)
    if account is None or job is None or job.account_id != account.id:
        raise RuntimeError(f"migration job {job_id} not found")
    return job


def get_status(params: dict) -> dict:
    with write_session() as session:
        job = _job_for_account(session, int(params["id"]), validate_username(params["username"]))
        return _job_to_dict(job)


def list_jobs(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        jobs = session.scalars(
            select(ImapMigrationJob).where(ImapMigrationJob.account_id == account.id).order_by(ImapMigrationJob.started_at.desc())
        ).all()
        return {"username": username, "jobs": [_job_to_dict(j) for j in jobs]}


def cancel_migration(params: dict) -> dict:
    with write_session() as session:
        job = _job_for_account(session, int(params["id"]), validate_username(params["username"]))
        if job.status in ("completed", "failed", "cancelled"):
            return _job_to_dict(job)
        job.status = "cancelled"
        session.flush()
        return _job_to_dict(job)


ACTIVE_STATUSES = ("pending", "connecting", "running")


def list_active_admin(params: dict) -> dict:
    """Admin sees all active jobs (goal's explicit requirement), across
    every account."""
    with write_session() as session:
        jobs = session.scalars(
            select(ImapMigrationJob).where(ImapMigrationJob.status.in_(ACTIVE_STATUSES)).order_by(ImapMigrationJob.started_at.desc())
        ).all()
        account_ids = {j.account_id for j in jobs}
        accounts_by_id = (
            {a.id: a.username for a in session.scalars(select(Account).where(Account.id.in_(account_ids))).all()}
            if account_ids else {}
        )
        return {"jobs": [{**_job_to_dict(j), "username": accounts_by_id.get(j.account_id)} for j in jobs]}
