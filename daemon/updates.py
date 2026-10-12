"""Panel update system: check GitHub releases, apply updates, roll back.

Design (goal: "release tarballs from GitHub, never git pull on production"):

- `update.check` polls the GitHub releases API for `settings.
  update_github_repo`, caches the answer in the single-row UpdateState
  table for `update_check_cache_seconds` (1h), and compares against the
  running `version.py`.
- `update.start` runs an async job (CpanelImportJob-style, single worker)
  that does everything SAFE while the current code keeps running:
  pre-flight (tests + disk), backup (panel DB + /etc/boron), download
  (github.com release URL only, every redirect hop re-validated against a
  GitHub-owned host allowlist), SHA256 and pinned publisher-signature verify
  BEFORE extraction, tarball
  member validation (no absolute/traversal/link members) + staged
  extraction to /opt/boron-{version}/, venv build, and the new
  version's DB migrations (create_all + additive -- old code keeps working
  after a rollback because migrations are additive-only by project
  convention).
- The DANGEROUS window -- atomic symlink swap of /opt/boron, service
  restarts, health check, swap-back on failure -- is delegated to
  scripts/update_finalize.py, a stdlib-only script launched as a detached
  transient systemd unit. Two reasons, both load-bearing:
    1. `systemctl restart boron-provisiond` kills THIS process --
       an in-daemon implementation would die mid-job with the panel in an
       unknown state (servicemgr.py's registry deliberately refuses the
       panel's own units for exactly this class of footgun).
    2. The finalizer runs from a copy under /var/lib/boron using
       /usr/bin/python3 and ONLY the standard library, so a broken new
       payload (bad venv, broken imports) can never break the machinery
       that rolls it back.
- `update.rollback` reuses the same finalizer to swap back to the previous
  version dir within the retention window (update_keep_old_days).

Every step is appended to the job's `steps` JSON *and* mirrored as a JSON
line to /var/log/boron/updates.log (audit.py's account-events pattern:
best-effort, never fails the operation). Nothing secret is ever logged --
step details are static messages, paths, versions and sizes.

OLS and hosted sites are never touched. Mail guard upgrades reload Dovecot;
only the two panel units restart.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import stat
import tarfile
import tempfile
import time
import urllib.parse
from shared.request_context import ContextThreadPoolExecutor as ThreadPoolExecutor
from pathlib import Path

import httpx
from sqlalchemy import select, update

from daemon import diagnostics, monitoring, notifications
from daemon.test_update_override import (
    TestUpdateOverride,
    load_test_update_override,
    validate_test_asset_url,
)
from daemon.procutil import run
from shared.config import CONFIG_PATH, settings
from shared.db import write_session
from shared.models import UpdateJob, UpdateState, utcnow
from shared.release_signature import archive_digest, verify_signature
from shared.release_metadata import (
    ReleaseMetadataError,
    load_release_metadata,
    source_version_supported,
)
from shared.rpc import RPC_PROTOCOL_VERSION
from shared.migrations import (
    SchemaCompatibilityError,
    assert_rollback_compatible,
    load_schema_manifest,
    rollback_compatibility,
    schema_state_from_path,
)
from shared.validation import ValidationError
from version import BORON_VERSION

logger = logging.getLogger("borond.updates")

# Test hook, same convention as daemon/cloudflare.py's _transport: tests
# swap in an httpx.MockTransport so nothing ever hits the real GitHub API.
_transport: httpx.BaseTransport | None = None

# Test hook for the finalizer launch: tests replace the systemd-run argv
# prefix to exercise the handoff without systemd.
# A signal-killed finalizer must resume from its idempotent transaction rather
# than strand the live symlink in the middle of a swap.  ``on-abnormal`` is
# intentionally narrower than ``on-failure``: handled validation/health
# failures return a non-zero status after rollback and must not be retried.
_SYSTEMD_RUN = [
    "systemd-run", "--collect",
    "--property=Restart=on-abnormal",
    "--property=RestartSec=1s",
    "--property=StartLimitBurst=3",
    "--property=StartLimitIntervalSec=300s",
]

# A self-update must be strictly serialized -- one worker, ever.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="panel-update")

GITHUB_API = "https://api.github.com"
# Hosts a release-asset download may touch. The FIRST hop must be the
# canonical https://github.com/{repo}/releases/download/... URL; GitHub then
# 302s to its CDN (*.githubusercontent.com). The goal's literal "no redirect
# following" is unimplementable against real GitHub (assets are ALWAYS
# served via redirect) -- the security intent (no attacker-steerable hop)
# is enforced instead by re-validating EVERY hop against this allowlist,
# https-only, with a hop cap. Documented in CHECKPOINT-update-3.
_REDIRECT_HOST_SUFFIX = ".githubusercontent.com"
_MAX_REDIRECTS = 4

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
# Version dirs we own under /opt. Deliberately anchored and digits-only so
# neighbours like /opt/boron-nodejs or /opt/boron.pre-filebrowser
# can NEVER match (cleanup deletes matching dirs).
_VERSION_DIR_RE = re.compile(r"^boron-\d+\.\d+\.\d+$")


# --- version comparison ------------------------------------------------------


def parse_version(value: str) -> tuple[int, int, int]:
    """'v1.2.3' / '1.2.3' -> (1, 2, 3). Raises ValidationError otherwise."""
    m = _VERSION_RE.match(str(value).strip().lstrip("vV"))
    if not m:
        raise ValidationError(f"unparseable version: {value!r} (expected x.y.z)")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


# --- updates.log (file mirror of every step) ----------------------------------


def _log_file_event(record: dict) -> None:
    """Best-effort JSON line to updates.log -- the durable file mirror of
    the in-DB job steps (daemon/audit.py's account-events pattern). Never
    raises: a full disk must not fail the update step itself."""
    record = {"at": utcnow().isoformat(), **record}
    try:
        path = Path(settings.log_dir) / "updates.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError:  # pragma: no cover - disk-full etc.
        logger.warning("could not append to updates.log", exc_info=True)


def tail_update_log(max_lines: int = 200) -> list[dict]:
    path = Path(settings.log_dir) / "updates.log"
    if not path.exists():
        return []
    try:
        lines = path.read_text().splitlines()[-max_lines:]
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records


# --- state + release fetching --------------------------------------------------


def _get_state(session) -> UpdateState:
    row = session.get(UpdateState, 1)
    if row is None:
        row = UpdateState(id=1)
        session.add(row)
        session.flush()
    return row


def _require_repo() -> str:
    repo = settings.update_github_repo.strip()
    if not repo:
        raise ValidationError(
            "update checks are not configured -- set update_github_repo "
            '(e.g. "boronpanel/boronpanel") in /etc/boron/boron.toml'
        )
    if not _REPO_RE.match(repo):
        raise ValidationError(f"invalid update_github_repo: {repo!r}")
    return repo


def _github_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"boron/{BORON_VERSION}"}
    token = settings.secrets.get("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _release_download_prefix(repo: str) -> str:
    return f"https://github.com/{repo}/releases/download/"


def _fetch_release(tag: str | None = None) -> dict:
    """Fetch release metadata from the GitHub API (latest, or an exact tag).

    Returns {version, changelog_url, tarball_url, checksum_url, signature_url}. Raises
    ValidationError on anything malformed -- including assets whose
    download URL is not under https://github.com/{repo}/releases/download/
    (goal security rule 1: the tarball URL comes from github.com releases
    only, never anywhere an attacker could steer it).
    """
    test_override = load_test_update_override()
    if test_override is not None:
        return _fetch_test_release(test_override, tag)
    repo = _require_repo()
    url = f"{GITHUB_API}/repos/{repo}/releases/" + (f"tags/{tag}" if tag else "latest")
    with httpx.Client(timeout=20.0, transport=_transport, follow_redirects=False) as client:
        resp = client.get(url, headers=_github_headers())
    if resp.status_code == 404:
        raise ValidationError(f"no release found ({'tag ' + tag if tag else 'latest'}) in {repo}")
    resp.raise_for_status()
    data = resp.json()
    if data.get("draft") or data.get("prerelease"):
        raise ValidationError("draft and pre-release versions are not eligible for panel updates")

    tag_name = str(data.get("tag_name", ""))
    version = "%d.%d.%d" % parse_version(tag_name)
    prefix = _release_download_prefix(repo)
    tarball_url = checksum_url = signature_url = None
    metadata_url = metadata_signature_url = None
    archive_size = None
    for asset in data.get("assets", []):
        name = asset.get("name", "")
        dl = asset.get("browser_download_url", "")
        if name == f"boron-{version}.tar.gz":
            tarball_url = dl
            try:
                archive_size = max(0, int(asset.get("size", 0))) or None
            except (TypeError, ValueError):
                archive_size = None
        elif name == f"boron-{version}.sha256":
            checksum_url = dl
        elif name == f"boron-{version}.tar.gz.sig":
            signature_url = dl
        elif name == f"boron-{version}.metadata.json":
            metadata_url = dl
        elif name == f"boron-{version}.metadata.json.sig":
            metadata_signature_url = dl
    if not tarball_url or not checksum_url or not signature_url:
        raise ValidationError(
            f"release {tag_name} is missing its archive, checksum or publisher signature"
        )
    for dl in (tarball_url, checksum_url, signature_url):
        if not dl.startswith(prefix):
            raise ValidationError(f"refusing release asset URL outside {prefix}: {dl}")
    return {
        "version": version,
        "changelog_url": str(data.get("html_url", "")),
        "tarball_url": tarball_url,
        "checksum_url": checksum_url,
        "signature_url": signature_url,
        "metadata_url": metadata_url,
        "metadata_signature_url": metadata_signature_url,
        "archive_size": archive_size,
        "source": "github",
    }


def _test_manifest_url(override: TestUpdateOverride, tag: str | None) -> str:
    name = "latest.json" if tag is None else f"{tag}.json"
    return f"{override.base_url}/{name}"


def _fetch_test_release(override: TestUpdateOverride, tag: str | None = None) -> dict:
    """Resolve a release from a root-configured, loopback-only lab source."""
    manifest_url = _test_manifest_url(override, tag)
    validate_test_asset_url(manifest_url, override)
    with httpx.Client(
        timeout=20.0, transport=_transport, follow_redirects=False, trust_env=False,
    ) as client:
        response = client.get(manifest_url, headers={"User-Agent": f"boron/{BORON_VERSION}"})
    if response.is_redirect:
        raise ValidationError("test update manifests may not redirect")
    if response.status_code == 404:
        raise ValidationError(f"no test release found ({tag or 'latest'})")
    response.raise_for_status()
    if len(response.content) > 64 * 1024:
        raise ValidationError("test update manifest exceeds 64 KiB")
    try:
        data = response.json()
    except ValueError as exc:
        raise ValidationError("test update manifest is not valid JSON") from exc
    if not isinstance(data, dict) or set(data) - {"version", "changelog_url", "has_metadata", "archive_size"}:
        raise ValidationError("test update manifest has unsupported fields")
    version = "%d.%d.%d" % parse_version(str(data.get("version", "")))
    if tag is not None and version != "%d.%d.%d" % parse_version(tag):
        raise ValidationError("test update manifest version does not match its requested tag")
    has_metadata = data.get("has_metadata", True)
    if not isinstance(has_metadata, bool):
        raise ValidationError("test update manifest has_metadata must be boolean")
    archive_size = data.get("archive_size")
    if archive_size is not None:
        if type(archive_size) is not int or archive_size < 0:
            raise ValidationError("test update manifest archive_size must be a non-negative integer")
    prefix = f"{override.base_url}/boron-{version}"
    result = {
        "version": version,
        "changelog_url": str(data.get("changelog_url") or ""),
        "tarball_url": f"{prefix}.tar.gz",
        "checksum_url": f"{prefix}.sha256",
        "signature_url": f"{prefix}.tar.gz.sig",
        "metadata_url": f"{prefix}.metadata.json" if has_metadata else None,
        "metadata_signature_url": f"{prefix}.metadata.json.sig" if has_metadata else None,
        "archive_size": archive_size,
        "source": "test-loopback",
    }
    for key in ("tarball_url", "checksum_url", "signature_url", "metadata_url", "metadata_signature_url"):
        if result[key]:
            validate_test_asset_url(result[key], override)
    return result


def check(params: dict | None = None) -> dict:
    """Op `update.check`: current vs latest, cached for 1h.

    force=True bypasses the cache (the UI's "Check now" button and the
    daily cron). An unconfigured repo reports configured=False rather than
    erroring, so the admin UI can render a setup hint instead of a 400.
    """
    params = params or {}
    force = bool(params.get("force", False))

    try:
        test_configured = load_test_update_override() is not None
    except ValidationError:
        # A malformed root-created gate is still "configured" so Check now
        # reports the actionable gate error instead of hiding it as disabled.
        test_configured = True
    configured = test_configured or bool(settings.update_github_repo.strip())
    if not configured:
        return {
            "configured": False,
            "current_version": BORON_VERSION,
            "latest_version": None,
            "update_available": False,
            "changelog_url": None,
            "checked_at": None,
            "error": None,
        }

    with write_session() as session:
        state = _get_state(session)
        age = None
        if state.checked_at is not None:
            checked = state.checked_at
            if checked.tzinfo is None:  # SQLite round-trips naive; stored values are UTC
                checked = checked.replace(tzinfo=dt.timezone.utc)
            age = (utcnow() - checked).total_seconds()
        # The cache window applies to FAILED checks too -- otherwise a
        # misconfigured repo would make every dashboard status poll re-hit
        # the GitHub API. force=True (Check now button, daily cron) bypasses.
        fresh = age is not None and age < settings.update_check_cache_seconds
        if force or not fresh:
            try:
                info = _fetch_release()
                state.latest_version = info["version"]
                state.changelog_url = info["changelog_url"]
                state.tarball_url = info["tarball_url"]
                state.checksum_url = info["checksum_url"]
                state.release_metadata = _fetch_release_metadata(info)
                state.release_asset_bytes = info.get("archive_size")
                state.last_error = None
            except (httpx.HTTPError, ValidationError, ValueError) as exc:
                # Keep serving the previous answer (if any); surface the error.
                state.last_error = str(exc)[:1000]
                logger.warning("update check failed: %s", exc)
            state.checked_at = utcnow()

        update_available = False
        if state.latest_version:
            try:
                update_available = is_newer(state.latest_version, BORON_VERSION)
            except ValidationError:
                update_available = False
        return {
            "configured": True,
            "current_version": BORON_VERSION,
            "latest_version": state.latest_version,
            "update_available": update_available,
            "changelog_url": state.changelog_url,
            "checked_at": state.checked_at.isoformat() if state.checked_at else None,
            "error": state.last_error,
            "release_metadata": state.release_metadata or _legacy_release_metadata(),
            "release_asset_bytes": state.release_asset_bytes,
        }


# --- job plumbing ---------------------------------------------------------------


def _job_to_dict(job: UpdateJob) -> dict:
    duration = None
    if job.completed_at and job.started_at:
        # SQLAlchemy's SQLite adapter reads naive UTC timestamps, while
        # the stdlib finalizer writes explicit UTC offsets. Older completed
        # jobs can contain both forms; normalize before subtracting.
        started = job.started_at
        completed = job.completed_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=dt.timezone.utc)
        if completed.tzinfo is None:
            completed = completed.replace(tzinfo=dt.timezone.utc)
        duration = (completed - started).total_seconds()
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "from_version": job.from_version,
        "to_version": job.to_version,
        "initiated_by": job.initiated_by,
        "progress_message": job.progress_message,
        "steps": job.steps,
        "release_metadata": job.release_metadata or {},
        "preflight_report": job.preflight_report or {},
        "advisories": job.advisories or [],
        "runtime_changes": job.runtime_changes or [],
        "acknowledged_data_plane": job.acknowledged_data_plane or [],
        "override_active_tasks": bool(job.override_active_tasks),
        "automatic_rollback_attempted": bool(job.automatic_rollback_attempted),
        "backup_path": job.backup_path,
        "error": job.error,
        "rolled_back": job.rolled_back,
        "old_dir": job.old_dir,
        "new_dir": job.new_dir,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "duration_seconds": duration,
    }


def _update_job(job_id: int, **fields) -> None:
    with write_session() as session:
        job = session.get(UpdateJob, job_id)
        if job is None:  # pragma: no cover
            return
        for key, value in fields.items():
            setattr(job, key, value)


def _step(job_id: int, step: str, status: str, detail: str = "") -> None:
    """Record one step transition: job.steps JSON + updates.log + logger."""
    entry = {"step": step, "status": status, "detail": detail, "at": utcnow().isoformat()}
    with write_session() as session:
        job = session.get(UpdateJob, job_id)
        if job is not None:
            job.steps = [*job.steps, entry]  # reassign so the JSON column change is detected
            job.progress_message = f"{step}: {status}" + (f" -- {detail}" if detail and status != "ok" else "")
    _log_file_event({"job_id": job_id, **entry})
    logger.info("update job %s: %s %s %s", job_id, step, status, detail)


# How long a job may sit in "finalizing" with no news before it is declared
# dead. The finalizer's whole window (swap + two restarts + 90s health
# deadline + failure path) is a few minutes; 30 is very generous.
STALE_FINALIZING_SECONDS = 1800


def recover_interrupted_jobs() -> int:
    """Fail pre-swap jobs abandoned by a daemon restart.

    Pending/running work lives in this process's executor, so no worker can
    still own it after borond starts again.  ``finalizing`` is deliberately
    excluded: the detached finalizer owns that state and is expected to
    restart borond during a healthy update.  Keeping the two states separate
    prevents both a permanently blocked updater and a false failure during
    the atomic swap window.
    """
    recovered: list[dict] = []
    now = utcnow()
    with write_session() as session:
        jobs = session.scalars(
            select(UpdateJob)
            .where(UpdateJob.status.in_(("pending", "running")))
            .order_by(UpdateJob.id)
        ).all()
        for job in jobs:
            reference = secrets.token_hex(8)
            detail = (
                "The provisioning daemon stopped before the atomic release "
                "swap. The active panel release was left unchanged; review "
                f"the update log and start the update again. (reference: {reference})"
            )
            entry = {
                "step": "recovery",
                "status": "failed",
                "detail": detail,
                "at": now.isoformat(),
                "reference": reference,
            }
            job.steps = [*(job.steps or []), entry]
            job.status = "failed"
            job.progress_message = "failed (interrupted before swap)"
            job.error = detail
            job.completed_at = now
            recovered.append({"job_id": job.id, **entry})
    for entry in recovered:
        _log_file_event(entry)
        logger.error(
            "update job %s was interrupted before swap (reference: %s)",
            entry["job_id"], entry["reference"],
        )
    return len(recovered)


def _active_job(session) -> UpdateJob | None:
    """The currently-active job, expiring a stuck 'finalizing' one.

    'finalizing' is terminal-state-by-another-process: the detached
    finalizer writes completed/failed directly. If the finalizer died
    without reporting (kill -9, systemd-run refused mid-restart, ...), the
    job would otherwise block every future update AND rollback forever with
    no admin-facing way out. Staleness is measured from the job's last
    recorded step (the finalize handoff), not job start -- pre-flight tests
    alone take ~15 minutes."""
    job = session.scalar(
        select(UpdateJob)
        .where(UpdateJob.status.in_(("pending", "running", "finalizing")))
        .order_by(UpdateJob.id.desc())
    )
    if job is None or job.status != "finalizing":
        return job
    last = None
    for entry in reversed(job.steps or []):
        if entry.get("at"):
            try:
                last = dt.datetime.fromisoformat(entry["at"])
            except ValueError:
                last = None
            break
    if last is None:
        last = job.started_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=dt.timezone.utc)
    if last is None or (utcnow() - last).total_seconds() <= STALE_FINALIZING_SECONDS:
        return job
    job.status = "failed"
    job.error = (
        "finalizer never reported back (stale for >30min). The symlink swap may or may "
        "not have been applied -- check /var/log/boron/updates.log, the "
        f"{settings.update_live_dir} symlink target, and both panel services before retrying."
    )
    job.completed_at = utcnow()
    job.progress_message = "failed (stale finalizer)"
    _log_file_event({"job_id": job.id, "step": "finalize", "status": "failed",
                     "detail": "expired stale finalizing job"})
    logger.error("update job %s expired: finalizer never reported back", job.id)
    return None


def _live_target() -> str | None:
    """Real dir the live symlink points at, or None while /opt/boron is
    still a plain directory (pre-first-update layout)."""
    live = settings.update_live_dir
    if os.path.islink(live):
        return os.path.realpath(live)
    return None


# --- update job -----------------------------------------------------------------


def start_update(params: dict) -> dict:
    """Op `update.start`. Root RPC policy verifies admin authority and
    the conditional 2FA confirmation; this validates versions/state,
    records who initiated it, and hands off to the single-worker executor.
    """
    initiated_by = str(params.get("initiated_by", "")).strip() or "unknown"

    info = check({"force": bool(params.get("refresh", False))})
    if not info["configured"]:
        raise ValidationError("update_github_repo is not configured")
    to_version = str(params.get("to_version") or info["latest_version"] or "").strip()
    if not to_version:
        raise ValidationError(f"no release available to update to (last check error: {info['error']})")
    parse_version(to_version)
    if not is_newer(to_version, BORON_VERSION):
        raise ValidationError(
            f"target version {to_version} is not newer than the running {BORON_VERSION} "
            "(use update.rollback to go back)"
        )
    metadata = info.get("release_metadata") or _legacy_release_metadata()
    supported, reason = source_version_supported(metadata, BORON_VERSION)
    if not supported:
        raise ValidationError(f"unsupported update path: {reason}")
    data_plane = list(metadata.get("data_plane_reloads") or [])
    if data_plane and not bool(params.get("acknowledge_data_plane", False)):
        raise ValidationError(
            "acknowledge the declared data-plane reloads before updating: "
            + ", ".join(data_plane)
        )
    override_active_tasks = bool(params.get("override_active_tasks", False))

    with write_session() as session:
        active = _active_job(session)
        if active is not None:
            raise ValidationError(f"update job {active.id} is already {active.status}")
        job = UpdateJob(
            kind="update",
            status="pending",
            from_version=BORON_VERSION,
            to_version=to_version,
            initiated_by=initiated_by,
            progress_message="queued",
            release_metadata=metadata,
            acknowledged_data_plane=data_plane if data_plane else [],
            override_active_tasks=override_active_tasks,
        )
        session.add(job)
        session.flush()
        job_id = job.id
        result = _job_to_dict(job)

    _log_file_event({"job_id": job_id, "step": "job", "status": "queued",
                     "detail": f"update {BORON_VERSION} -> {to_version} by {initiated_by}"})
    _executor.submit(_run_update_job, job_id, to_version)
    return result


def _claim_job(job_id: int, kind: str) -> UpdateJob | None:
    """Claim a queued job once, using the root-owned row as authority."""
    with write_session() as session:
        claimed = session.execute(
            update(UpdateJob)
            .where(UpdateJob.id == job_id, UpdateJob.kind == kind,
                   UpdateJob.status == "pending")
            .values(status="running", progress_message="starting")
        )
        if claimed.rowcount != 1:
            return None
        return session.get(UpdateJob, job_id)


def _run_update_job(job_id: int, to_version: str) -> None:
    try:
        job = _claim_job(job_id, "update")
        if job is None:
            return
        to_version = job.to_version
        parse_version(to_version)
        if not is_newer(to_version, BORON_VERSION):
            _fail_step(job_id, "preflight", "queued update is no longer newer than the running version")

        # (a) pre-flight ------------------------------------------------------
        _preflight(job_id)

        # (b) backup ----------------------------------------------------------
        backup_dir = _backup(job_id, to_version)

        # (c)+(d) download + checksum ------------------------------------------
        tarball = _download_and_verify(job_id, to_version)

        # (e) staged extraction ------------------------------------------------
        new_dir = _extract_staged(
            job_id, tarball, to_version, signature_file=tarball + ".sig",
            public_key=_active_release_public_key(),
        )
        _validate_staged_release_metadata(job_id, new_dir)

        # venv for the staged tree (units exec /opt/boron/.venv/... which
        # resolves through the symlink into the new dir after the swap).
        _build_venv(job_id, new_dir)

        # (f) migrations from the NEW version against the live DB --------------
        _run_migrations(job_id, new_dir)
        _install_litespeed_repo_trust(job_id, new_dir, backup_dir)
        _install_db_grant_fix(job_id, new_dir, backup_dir)
        _install_mail_guard(job_id, new_dir)
        _install_ols_webadmin_integration(job_id, new_dir)
        _install_ftps_tls(job_id, new_dir)

        # (g..j) handoff: swap/restart/health/rollback happen in the detached
        # finalizer -- see module docstring for why. backup_dir already
        # recorded in the job's steps for the operator.
        _handoff_to_finalizer(job_id, new_dir)
    except _StepFailed:
        pass  # already recorded by _fail_step
    except Exception as exc:  # noqa: BLE001 -- job boundary
        logger.exception("update job %s crashed", job_id)
        _update_job(job_id, status="failed", error=str(exc)[:4000],
                    progress_message="failed", completed_at=utcnow())
        _log_file_event({"job_id": job_id, "step": "job", "status": "failed", "detail": str(exc)[:500]})


class _StepFailed(Exception):
    pass


def _fail_step(job_id: int, step: str, detail: str) -> None:
    _step(job_id, step, "failed", detail)
    _update_job(job_id, status="failed", error=f"{step}: {detail}"[:4000],
                progress_message=f"{step}: failed", completed_at=utcnow())
    raise _StepFailed()


_ACTIVE_TASK_TABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("backup_jobs", ("pending", "running")),
    ("restore_jobs", ("pending", "running")),
    ("wordpress_jobs", ("pending", "running")),
    ("app_install_jobs", ("pending", "running")),
    ("malware_scan_jobs", ("pending", "running")),
    ("namespace_migration_jobs", ("pending", "running")),
    ("cpanel_import_jobs", ("pending", "running")),
    ("account_archive_import_jobs", ("pending", "running")),
    ("bulk_action_jobs", ("pending", "running")),
    ("command_runs", ("pending", "running")),
    ("imap_migration_jobs", ("pending", "connecting", "running")),
    ("snapshot_runs", ("pending", "running")),
    ("snapshot_downloads", ("pending", "running")),
    ("snapshot_restores", ("pending", "running")),
    ("snapshot_destination_operations", ("pending", "running")),
    ("panel_config_jobs", ("pending", "running")),
    ("stack_jobs", ("queued", "pending", "running")),
)


def _active_task_inventory() -> list[dict]:
    """Return bounded, non-secret identifiers for operations unsafe to overlap."""
    rows: list[dict] = []
    connection = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True, timeout=5)
    try:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        for table, statuses in _ACTIVE_TASK_TABLES:
            if table not in tables:
                continue
            placeholders = ",".join("?" for _status in statuses)
            # Table names and status values are static declarations above.
            query = f'SELECT id, status FROM "{table}" WHERE status IN ({placeholders}) ORDER BY id LIMIT 101'
            for row_id, status in connection.execute(query, statuses):
                rows.append({"kind": table, "id": int(row_id), "status": str(status)})
                if len(rows) >= 100:
                    return rows
    finally:
        connection.close()
    return rows


def _sqlite_integrity() -> tuple[bool, str]:
    try:
        connection = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True, timeout=10)
        try:
            row = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return False, f"SQLite integrity check could not run: {type(exc).__name__}"
    if not row or row[0] != "ok":
        return False, "SQLite integrity check failed"
    return True, "SQLite integrity is valid"


def _tree_size(path: str) -> int:
    total = 0
    root = Path(path)
    if not root.exists():
        return 0
    for item in root.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def preflight(params: dict | None = None) -> dict:
    """Read-only update readiness report used by both the UI and worker."""
    params = params or {}
    override_active = bool(params.get("override_active_tasks", False))
    metadata = params.get("release_metadata")
    archive_size = params.get("release_asset_bytes")
    if not isinstance(metadata, dict):
        info = check({"force": bool(params.get("refresh", False))})
        metadata = info.get("release_metadata") or _legacy_release_metadata()
        archive_size = info.get("release_asset_bytes")
    elif not metadata.get("minimum_source_version"):
        # Legacy queued jobs and hand-built test fixtures predate release
        # metadata.  Treat an empty document exactly like a legacy release;
        # never feed a partially shaped object into the compatibility parser.
        metadata = _legacy_release_metadata()

    checks: list[dict] = []
    integrity_ok, integrity_detail = _sqlite_integrity()
    checks.append({"id": "database_integrity", "status": "pass" if integrity_ok else "fail",
                   "critical": True, "detail": integrity_detail})

    supported, source_detail = source_version_supported(metadata, BORON_VERSION)
    checks.append({"id": "source_version", "status": "pass" if supported else "fail",
                   "critical": True, "detail": source_detail})

    active = _active_task_inventory()
    checks.append({
        "id": "active_tasks",
        "status": "warn" if active and override_active else ("fail" if active else "pass"),
        "critical": bool(active and not override_active),
        "detail": (
            f"{len(active)} active operation(s) will be overridden" if active and override_active
            else f"{len(active)} active operation(s) must finish before updating" if active
            else "No conflicting backup, restore, migration, or maintenance operation is active"
        ),
    })

    requested_archive = max(0, int(archive_size or 0))
    try:
        database_bytes = Path(settings.db_path).stat().st_size
    except OSError:
        database_bytes = 0
    backup_bytes = database_bytes + _tree_size(str(CONFIG_PATH.parent))
    required_bytes = max(
        int(settings.update_preflight_min_free_mb) * 1024 * 1024,
        requested_archive * 3 + backup_bytes * 2,
    )
    disk_failures = []
    disk_observations = []
    for mount in (settings.update_versions_root, settings.update_backup_dir,
                  os.path.dirname(settings.db_path) or "/"):
        target = mount if os.path.exists(mount) else os.path.dirname(mount) or "/"
        try:
            free = shutil.disk_usage(target).free
            disk_observations.append(f"{target}: {free // (1024 * 1024)} MB free")
            if free < required_bytes:
                disk_failures.append(target)
        except OSError:
            disk_failures.append(target)
    checks.append({
        "id": "disk_headroom", "status": "fail" if disk_failures else "pass", "critical": True,
        "detail": "; ".join(disk_observations)
        + f"; need {required_bytes // (1024 * 1024)}MB for release staging and backup",
    })

    diagnostic = diagnostics.run_all({})
    for result in diagnostic.get("checks", []):
        check_id = str(result.get("id", ""))
        if check_id in {"database", "root_helper", "ports"} or check_id.startswith("service_"):
            status = str(result.get("status", "unknown"))
            critical = bool(result.get("critical")) or (
                check_id.startswith("service_") and status == "fail"
            )
            checks.append({
                "id": f"diagnostic_{check_id}", "status": status, "critical": critical,
                "detail": str(result.get("explanation", ""))[:500],
            })
    sender = next((row for row in diagnostic.get("checks", [])
                   if row.get("id") == "notification_sender"), None)
    if sender:
        checks.append({
            "id": "notification_sender", "status": sender.get("status", "unknown"),
            "critical": False, "detail": str(sender.get("explanation", ""))[:500],
        })

    failures = [row for row in checks if row["critical"] and row["status"] == "fail"]
    warnings = [row for row in checks if row["status"] in {"warn", "unknown"}]
    return {
        "can_start": not failures,
        "checks": checks,
        "active_tasks": active,
        "override_required": bool(active),
        "critical_failures": failures,
        "warnings": warnings,
        "release_metadata": metadata,
        "required_free_bytes": required_bytes,
    }


def finalizer_readiness(_params: dict | None = None) -> dict:
    """Authenticated read-only readiness used only by the detached finalizer."""
    return diagnostics.run_all({})


def finalizer_ping(_params: dict | None = None) -> dict:
    return {"ready": True, "version": BORON_VERSION}


def _preflight(job_id: int) -> None:
    live = settings.update_live_dir
    if not os.path.isdir(live):
        _fail_step(job_id, "preflight", f"live install dir {live} does not exist")
    with write_session() as session:
        job = session.get(UpdateJob, job_id)
        metadata = dict(job.release_metadata or {}) if job else _legacy_release_metadata()
        override_active = bool(job and job.override_active_tasks)
    report = preflight({
        "release_metadata": metadata,
        "release_asset_bytes": None,
        "override_active_tasks": override_active,
    })
    with write_session() as session:
        job = session.get(UpdateJob, job_id)
        if job is not None:
            job.preflight_report = report
            job.advisories = report["warnings"]
    if not report["can_start"]:
        detail = "; ".join(row["detail"] for row in report["critical_failures"][:5])
        _fail_step(job_id, "preflight", detail)

    if settings.update_preflight_tests:
        suite_name = "full" if settings.update_preflight_full_tests else "focused safety"
        _step(job_id, "preflight", "running",
              f"running the live install's {suite_name} test suite")
        pytest_bin = os.path.join(live, ".venv", "bin", "python")
        if not os.path.exists(pytest_bin):
            _fail_step(job_id, "preflight", f"no venv python at {pytest_bin}")
        proc = _pytest_run(pytest_bin, live)
        if proc.returncode != 0:
            tail = (proc.stdout or "").strip().splitlines()[-15:]
            _fail_step(job_id, "preflight",
                       f"live {suite_name} test suite failing -- update aborted. Tail: "
                       + " | ".join(tail))
    _step(job_id, "preflight", "ok")


def _pytest_run(python_bin: str, live_dir: str):
    """Run the live tree's focused update checks, or its full legacy suite.

    Every downloadable release has already passed the complete suite in
    scripts/release.sh. The live preflight therefore defaults to the tests
    that protect the update transaction, rollback, DB/config transactions,
    RPC boundary, and mailbox recovery guard. This catches a damaged runtime
    or install without spending nearly an hour retesting the old release.
    """
    # Tests exercise setgid directory semantics and must not inherit the
    # daemon's RestrictSUIDSGID seccomp filter. A transient service provides
    # the same execution context as a manual release check without relaxing
    # the long-running daemon. RuntimeMaxSec also kills orphaned test workers.
    full = settings.update_preflight_full_tests
    runtime_seconds = 3600 if full else 600
    workspace = os.path.join(live_dir, "scripts", "test_workspace.py")
    argv = [
        "systemd-run", "--quiet", "--wait", "--pipe", "--collect",
        f"--property=WorkingDirectory={live_dir}",
        f"--property=RuntimeMaxSec={runtime_seconds}",
        python_bin, workspace,
    ]
    if full:
        argv.append("--require-separate")
    argv.extend([
        "--", python_bin, "-m", "pytest", "-q", "-rs", "--tb=no",
        "-p", "no:cacheprovider",
    ])
    if not full:
        argv.extend([
            "tests/test_update_version.py",
            "tests/test_update_api.py",
            "tests/test_updates.py",
            "tests/test_update_finalizer.py",
            "tests/test_db_schema_transaction.py",
            "tests/test_configtx.py",
            "tests/test_rpc.py",
            "tests/test_snapshot_mail_guard_config.py",
        ])
    return run(argv, cwd=live_dir, timeout=float(runtime_seconds + 60))


def _backup(job_id: int, to_version: str) -> str:
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(settings.update_backup_dir, f"pre-update-{to_version}-{stamp}")
    try:
        os.makedirs(dest, mode=0o700, exist_ok=False)
        # Panel DB via sqlite's own online-backup API -- consistent even with
        # live writers under WAL, unlike a bare file copy.
        src = sqlite3.connect(settings.db_path)
        try:
            dst = sqlite3.connect(os.path.join(dest, "boron.db"))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        # The configured Boron directory including secrets.env -- the backup dir itself is
        # 0700 root-only, matching the strictest file it contains.
        _copytree_preserve_ownership(CONFIG_PATH.parent, Path(dest) / "etc-boron")
    except (OSError, sqlite3.Error) as exc:
        _fail_step(job_id, "backup", f"{exc}")
    _update_job(job_id, backup_path=dest)
    _prune_preupdate_backups(protect={os.path.realpath(dest)})
    _step(job_id, "backup", "ok", dest)
    return dest


def _copytree_preserve_ownership(source: str | Path, target: str | Path) -> None:
    """Copy a root-owned configuration tree without changing uid/gid.

    ``copytree`` preserves modes and timestamps but intentionally assigns the
    copying process as owner.  That breaks files deliberately readable by
    the unprivileged API group, so ownership is reapplied from lstat metadata
    without following symlinks.
    """
    source, target = Path(source), Path(target)
    shutil.copytree(source, target, symlinks=True)
    for source_dir, dirs, files in os.walk(source, followlinks=False):
        relative = Path(source_dir).relative_to(source)
        for name in [".", *dirs, *files]:
            source_path = Path(source_dir) if name == "." else Path(source_dir) / name
            target_path = target / relative if name == "." else target / relative / name
            metadata = source_path.lstat()
            os.chown(target_path, metadata.st_uid, metadata.st_gid, follow_symlinks=False)


def _preupdate_backup_dirs() -> list[Path]:
    root = Path(settings.update_backup_dir)
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    result = []
    for entry in entries:
        try:
            if (entry.name.startswith("pre-update-") and entry.is_dir()
                    and not entry.is_symlink() and entry.resolve().parent == root.resolve()):
                result.append(entry)
        except OSError:
            continue
    return sorted(result, key=lambda item: item.stat().st_mtime, reverse=True)


def _prune_preupdate_backups(*, protect: set[str] | None = None) -> list[str]:
    """Keep the newest N and every snapshot inside the age window."""
    protect = {os.path.realpath(item) for item in (protect or set())}
    keep_count = max(1, int(settings.update_backup_keep_count))
    keep_days = max(1, int(settings.update_backup_keep_days))
    cutoff = time.time() - keep_days * 86400
    removed = []
    for index, entry in enumerate(_preupdate_backup_dirs()):
        try:
            resolved = os.path.realpath(entry)
            if resolved in protect or index < keep_count or entry.stat().st_mtime >= cutoff:
                continue
            shutil.rmtree(entry)
            removed.append(str(entry))
        except OSError:
            logger.warning("could not prune pre-update backup %s", entry, exc_info=True)
    return removed


def _validate_download_url(url: str, repo: str, first_hop: bool) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise ValidationError(f"refusing non-https download hop: {url}")
    host = (parsed.hostname or "").lower()
    if first_hop:
        if not url.startswith(_release_download_prefix(repo)):
            raise ValidationError(f"refusing download outside {_release_download_prefix(repo)}: {url}")
    else:
        if host != "github.com" and not host.endswith(_REDIRECT_HOST_SUFFIX):
            raise ValidationError(f"refusing redirect to non-GitHub host: {host}")


def _safe_download(url: str, dest: str, max_bytes: int) -> int:
    """Download with every redirect hop validated (https + GitHub-owned
    hosts only, hop cap) and a hard size cap. Returns bytes written."""
    repo = _require_repo()
    current = url
    for hop in range(_MAX_REDIRECTS + 1):
        _validate_download_url(current, repo, first_hop=(hop == 0))
        with httpx.Client(timeout=httpx.Timeout(30.0, read=120.0), transport=_transport,
                          follow_redirects=False) as client:
            with client.stream("GET", current, headers={"User-Agent": f"boron/{BORON_VERSION}"}) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location", "")
                    if not location:
                        raise ValidationError("redirect with no Location header")
                    current = urllib.parse.urljoin(current, location)
                    continue
                resp.raise_for_status()
                written = 0
                fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as f:
                    for chunk in resp.iter_bytes():
                        written += len(chunk)
                        if written > max_bytes:
                            raise ValidationError(f"download exceeds {max_bytes} bytes -- aborted")
                        f.write(chunk)
                return written
    raise ValidationError(f"too many redirects (> {_MAX_REDIRECTS}) downloading {url}")


def _safe_test_download(
    url: str, dest: str, max_bytes: int, override: TestUpdateOverride,
) -> int:
    """Download one lab asset from the exact configured loopback origin.

    Redirects are forbidden. Unlike GitHub, a disposable static HTTP server
    has no legitimate CDN hop, and accepting one would widen this root-only
    escape hatch unnecessarily.
    """
    validate_test_asset_url(url, override)
    with httpx.Client(
        timeout=httpx.Timeout(30.0, read=120.0), transport=_transport,
        follow_redirects=False, trust_env=False,
    ) as client:
        with client.stream(
            "GET", url, headers={"User-Agent": f"boron/{BORON_VERSION}"},
        ) as response:
            if response.is_redirect:
                raise ValidationError("test update assets may not redirect")
            response.raise_for_status()
            written = 0
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as output:
                for chunk in response.iter_bytes():
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValidationError(f"download exceeds {max_bytes} bytes -- aborted")
                    output.write(chunk)
            return written


def _override_for_release(info: dict) -> TestUpdateOverride | None:
    source = info.get("source", "github")
    if source == "github":
        return None
    if source != "test-loopback":
        raise ValidationError("release has an unknown download source")
    override = load_test_update_override()
    if override is None:
        raise ValidationError("test update override marker is no longer present")
    for key in (
        "tarball_url", "checksum_url", "signature_url",
        "metadata_url", "metadata_signature_url",
    ):
        if info.get(key):
            validate_test_asset_url(info[key], override)
    return override


def _download_release_asset(info: dict, url: str, dest: str, max_bytes: int) -> int:
    override = _override_for_release(info)
    if override is None:
        return _safe_download(url, dest, max_bytes)
    return _safe_test_download(url, dest, max_bytes, override)


def _active_release_public_key() -> bytes | None:
    """Return the lab key only while both out-of-band root gates remain valid."""
    override = load_test_update_override()
    return None if override is None else override.public_key


def _verify_release_signature(
    version: str, digest: bytes, signature: bytes, public_key: bytes | None,
) -> None:
    # Keep the production call path byte-for-byte equivalent: omitting the
    # optional argument makes shared.release_signature load the shipped pin.
    if public_key is None:
        verify_signature(version, digest, signature)
    else:
        verify_signature(version, digest, signature, public_key)


def _legacy_release_metadata() -> dict:
    """Safe compatibility declaration for releases predating P6 metadata."""
    return {
        "format": 1,
        "minimum_source_version": "0.0.0",
        "maximum_source_version": None,
        "data_plane_reloads": [],
        "control_plane_restarts": ["boron-provisiond.service", "boron-api.service"],
        "runtime_rewrites": [],
        "requires_data_plane_acknowledgement": False,
        "signed": False,
        "legacy": True,
    }


def _fetch_release_metadata(info: dict) -> dict:
    metadata_url = info.get("metadata_url")
    signature_url = info.get("metadata_signature_url")
    if not metadata_url and not signature_url:
        return _legacy_release_metadata()
    if not metadata_url or not signature_url:
        raise ValidationError("release metadata and its signature must both be present")
    with tempfile.TemporaryDirectory(prefix="boron-release-metadata-") as directory:
        metadata_path = os.path.join(directory, "release-metadata.json")
        signature_path = metadata_path + ".sig"
        try:
            _download_release_asset(info, metadata_url, metadata_path, 64 * 1024)
            _download_release_asset(info, signature_url, signature_path, 1024)
            digest = archive_digest(metadata_path)
            override = _override_for_release(info)
            _verify_release_signature(
                info["version"], digest, Path(signature_path).read_bytes(),
                override.public_key if override is not None else None,
            )
            metadata = load_release_metadata(metadata_path)
        except (OSError, ValueError, ReleaseMetadataError, httpx.HTTPError) as exc:
            raise ValidationError(f"release metadata verification failed: {exc}") from exc
    return {
        **metadata,
        "signed": True,
        "legacy": False,
        "digest": digest.hex(),
    }


def _download_and_verify(job_id: int, to_version: str) -> str:
    # Refresh the exact tag, even when latest-release metadata is cached.
    # The detached publisher signature is never taken from mutable DB state.
    try:
        info = _fetch_release(tag=f"v{to_version}")
        if info["version"] != to_version:
            raise ValidationError("release tag does not match target version")
    except (httpx.HTTPError, ValidationError) as exc:
        _fail_step(job_id, "download", f"cannot resolve signed release v{to_version}: {exc}")
    tarball_url = info["tarball_url"]
    checksum_url = info["checksum_url"]
    signature_url = info["signature_url"]

    download_dir = settings.update_download_dir
    os.makedirs(download_dir, mode=0o700, exist_ok=True)
    directory_stat = os.lstat(download_dir)
    if (not stat.S_ISDIR(directory_stat.st_mode) or directory_stat.st_uid != os.geteuid()
            or directory_stat.st_mode & 0o077):
        _fail_step(job_id, "download", "update staging directory must be owned by the daemon and mode 0700")
    tarball = os.path.join(download_dir, f"boron-{to_version}.tar.gz")
    checksum_file = os.path.join(download_dir, f"boron-{to_version}.sha256")
    signature_file = os.path.join(download_dir, f"boron-{to_version}.tar.gz.sig")
    try:
        size = _download_release_asset(
            info, tarball_url, tarball, settings.update_max_download_bytes,
        )
        _download_release_asset(info, checksum_url, checksum_file, 64 * 1024)
        _download_release_asset(info, signature_url, signature_file, 1024)
    except (httpx.HTTPError, ValidationError, OSError) as exc:
        _remove_quietly(tarball, checksum_file, signature_file)
        _fail_step(job_id, "download", str(exc))
    _step(job_id, "download", "ok", f"{size} bytes from {tarball_url}")

    # (d) SHA256 BEFORE anything touches the archive contents.
    try:
        expected = Path(checksum_file).read_text().split()[0].strip().lower()
    except (OSError, IndexError):
        expected = ""
    if not re.fullmatch(r"[0-9a-f]{64}", expected or ""):
        _remove_quietly(tarball, checksum_file, signature_file)
        _fail_step(job_id, "checksum", "checksum asset is malformed -- update aborted")
    digest = archive_digest(tarball)
    if digest.hex() != expected:
        _remove_quietly(tarball, checksum_file, signature_file)
        _fail_step(job_id, "checksum",
                   f"SHA256 mismatch (expected {expected[:12]}..., got {digest.hex()[:12]}...) -- update aborted")
    _step(job_id, "checksum", "ok", expected)
    try:
        override = _override_for_release(info)
        _verify_release_signature(
            to_version, digest, Path(signature_file).read_bytes(),
            override.public_key if override is not None else None,
        )
    except (OSError, ValueError) as exc:
        _remove_quietly(tarball, checksum_file, signature_file)
        _fail_step(job_id, "signature", f"publisher authentication failed: {exc}")
    _remove_quietly(checksum_file)
    _step(job_id, "signature", "ok", "trusted Ed25519 publisher key")
    return tarball


def _remove_quietly(*paths: str) -> None:
    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass


def validate_tarball_members(tf: tarfile.TarFile, expected_prefix: str, max_total_bytes: int) -> None:
    """Reject anything path-traversal- or link-shaped BEFORE extraction.

    Release tarballs contain only regular files and directories under one
    boron-X.Y.Z/ prefix (release.sh builds and self-verifies exactly
    that), so anything else -- absolute paths, `..` components, symlinks,
    hardlinks, devices, fifos, members outside the prefix -- is hostile or
    corrupt and aborts the update. Python 3.12's extraction filter is
    applied as a second layer at extract time; this pre-pass exists so a
    bad archive is rejected as a unit before any member lands on disk.
    """
    total = 0
    for member in tf.getmembers():
        name = member.name
        if name.startswith("/") or ".." in name.split("/"):
            raise ValidationError(f"tarball member escapes extraction dir: {name!r}")
        if not (name == expected_prefix or name.startswith(expected_prefix + "/")):
            raise ValidationError(f"tarball member outside {expected_prefix}/: {name!r}")
        if member.islnk() or member.issym():
            raise ValidationError(f"tarball contains a link member (refused): {name!r}")
        if not (member.isreg() or member.isdir()):
            raise ValidationError(f"tarball contains a special member (refused): {name!r}")
        total += member.size
        if total > max_total_bytes:
            raise ValidationError("tarball declared contents exceed the size cap -- refused")


def _extract_staged(
    job_id: int, tarball: str, to_version: str, *,
    signature_file: str | None = None, public_key: bytes | None = None,
) -> str:
    prefix = f"boron-{to_version}"
    target = os.path.join(settings.update_versions_root, prefix)
    live_target = _live_target()
    if live_target and os.path.realpath(target) == live_target:
        _fail_step(job_id, "extract", f"{target} is the live install -- refusing to overwrite")
    tmp_root = None
    try:
        fd = os.open(tarball, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as archive_file:
            if signature_file is not None:
                # Verify the exact open inode that tarfile will extract. A
                # pathname swap after the download check cannot change these
                # bytes, closing the old verify-by-path/reopen-by-path race.
                digest = hashlib.sha256()
                for chunk in iter(lambda: archive_file.read(1024 * 1024), b""):
                    digest.update(chunk)
                _verify_release_signature(
                    to_version, digest.digest(), Path(signature_file).read_bytes(), public_key,
                )
                archive_file.seek(0)
            with tarfile.open(fileobj=archive_file, mode="r:gz") as tf:
                validate_tarball_members(tf, prefix, settings.update_max_download_bytes * 4)
                # Same filesystem as the target so the final rename is atomic.
                tmp_root = tempfile.mkdtemp(prefix=".boron-extract-", dir=settings.update_versions_root)
                tf.extractall(tmp_root, filter="data")
        extracted = os.path.join(tmp_root, prefix)
        if not os.path.isdir(extracted):
            raise ValidationError("tarball did not contain the expected top-level dir")
        if os.path.isdir(target):
            # Leftover from an earlier failed attempt at this same version --
            # staging only (checked above it isn't live), safe to replace.
            shutil.rmtree(target)
        os.rename(extracted, target)
    except (ValidationError, ValueError, tarfile.TarError, OSError) as exc:
        _fail_step(job_id, "extract", str(exc))
    finally:
        if tmp_root:
            shutil.rmtree(tmp_root, ignore_errors=True)
        _remove_quietly(tarball, signature_file) if signature_file else _remove_quietly(tarball)
    _step(job_id, "extract", "ok", target)
    return target


def _validate_staged_release_metadata(job_id: int, new_dir: str) -> None:
    """Treat the archive-contained document as the final behavior authority."""
    path = Path(new_dir) / "release-metadata.json"
    try:
        metadata = load_release_metadata(path)
        supported, reason = source_version_supported(metadata, BORON_VERSION)
    except ReleaseMetadataError as exc:
        _fail_step(job_id, "release-metadata", str(exc))
    if not supported:
        _fail_step(job_id, "release-metadata", f"unsupported update path: {reason}")
    digest = archive_digest(path).hex()
    failure = None
    with write_session() as session:
        job = session.get(UpdateJob, job_id)
        required = list(metadata.get("data_plane_reloads") or [])
        if job is None:  # pragma: no cover
            failure = "update job disappeared"
        else:
            preview = dict(job.release_metadata or {})
            preview_digest = preview.get("digest")
            acknowledged = list(job.acknowledged_data_plane or [])
            if preview_digest and preview_digest != digest:
                failure = "signed preview differs from signed archive metadata"
            elif required != acknowledged:
                failure = "declared data-plane reloads were not acknowledged: " + ", ".join(required)
            else:
                job.release_metadata = {
                    **metadata,
                    "signed": True,
                    "legacy": False,
                    "digest": digest,
                    "authority": "publisher-signed archive",
                }
    if failure:
        _fail_step(job_id, "release-metadata", failure)
    _step(job_id, "release-metadata", "ok", f"{len(metadata['data_plane_reloads'])} data-plane reload(s) declared")


def _build_venv(job_id: int, new_dir: str) -> None:
    _step(job_id, "venv", "running", "python3 -m venv + pip install -r requirements.txt")
    venv_dir = os.path.join(new_dir, ".venv")
    proc = run(["/usr/bin/python3", "-m", "venv", venv_dir], timeout=120.0)
    if proc.returncode != 0:
        _fail_step(job_id, "venv", f"venv creation failed: {(proc.stderr or '')[-500:]}")
    proc = run(
        [os.path.join(venv_dir, "bin", "pip"), "install", "-q",
         "-r", os.path.join(new_dir, "requirements.txt")],
        timeout=900.0,
    )
    if proc.returncode != 0:
        _fail_step(job_id, "venv", f"pip install failed: {(proc.stderr or '')[-500:]}")
    # The daemon runs with UMask=0027. tarfile's data filter and venv/pip
    # inherit it, leaving root-owned directories inaccessible to boron-api.
    # This staged tree contains public application/dependency files only;
    # config, secrets and databases remain outside it with their own modes.
    try:
        _make_runtime_readable(new_dir)
    except OSError as exc:
        _fail_step(job_id, "venv", f"cannot make staged runtime readable: {exc}")
    _step(job_id, "venv", "ok")


def _make_runtime_readable(root: str) -> None:
    for directory, dirs, files in os.walk(root, followlinks=False):
        os.chmod(directory, 0o755)
        for name in files:
            path = os.path.join(directory, name)
            if os.path.islink(path):
                continue  # venv interpreter links must never chmod system files
            mode = os.stat(path).st_mode
            os.chmod(path, 0o755 if mode & 0o111 else 0o644)



def _run_migrations(job_id: int, new_dir: str) -> None:
    """Run the NEW version's schema migrations (create_all + additive
    columns) against the live DB, using the new tree's own venv -- exactly
    what its daemon would do at boot, done early so a migration crash
    aborts before the swap instead of taking the panel down."""
    try:
        manifest = load_schema_manifest(new_dir)
    except SchemaCompatibilityError as exc:
        _fail_step(job_id, "migrate", f"release schema manifest is invalid: {exc}")
    if manifest.get("legacy"):
        _fail_step(job_id, "migrate", "new release is missing schema-compat.json")

    proc = run(
        [os.path.join(new_dir, ".venv", "bin", "python"), "-c",
         "from shared.db import init_db; init_db()"],
        cwd=new_dir, timeout=120.0,
    )
    if proc.returncode != 0:
        _fail_step(job_id, "migrate", f"new version's migrations failed: {(proc.stderr or '')[-800:]}")
    state = schema_state_from_path(settings.db_path)
    schema_version = int(state["schema_version"])
    if not manifest["minimum"] <= schema_version <= manifest["maximum"]:
        _fail_step(
            job_id,
            "migrate",
            f"release supports schema v{manifest['minimum']}..v{manifest['maximum']}, "
            f"but migration produced v{schema_version}",
        )
    _step(job_id, "migrate", "ok", f"database schema v{schema_version}")


def _install_db_grant_fix(job_id: int, new_dir: str, backup_dir: str) -> None:
    """Repair pre-existing wildcard MariaDB grants before new code starts.

    The staged release owns the migration code. Existing grants are recorded
    in the update's protected backup directory for operator recovery.
    """
    if not Path(new_dir, "scripts/reconcile_db_grants.py").is_file():
        return
    _step(job_id, "db-grants", "running", "Restricting hosted database grants")
    evidence = os.path.join(backup_dir, f"database-grants-before-{job_id}.jsonl")
    result = run(
        [os.path.join(new_dir, ".venv", "bin", "python"), "-m", "scripts.reconcile_db_grants",
         "--apply", "--evidence-path", evidence],
        cwd=new_dir, timeout=120.0,
    )
    if result.returncode != 0:
        _fail_step(job_id, "db-grants", "database grant reconciliation failed; panel version was not switched")
    _step(job_id, "db-grants", "ok")


def _install_litespeed_repo_trust(job_id: int, new_dir: str, backup_dir: str) -> None:
    """Apply the staged release's scoped APT source before activation."""
    script = Path(new_dir, "scripts/reconcile_litespeed_repo.py")
    if not script.is_file():
        return
    _step(job_id, "litespeed-repo", "running", "Scoping LiteSpeed APT repository trust")
    result = run(
        ["/usr/bin/python3", str(script), "--backup-dir", backup_dir],
        timeout=360,
    )
    if result.returncode:
        _fail_step(job_id, "litespeed-repo", "LiteSpeed APT repository reconciliation failed")
    _step(job_id, "litespeed-repo", "ok")


def _install_mail_guard(job_id: int, new_dir: str) -> None:
    """Activate the staged version's guard before switching panel code."""
    if not Path(new_dir, 'daemon/snapshot_mail_guard_config.py').is_file():
        return  # Older release without mailbox restoration.
    _step(job_id, 'mail-guard', 'running', 'Preparing mailbox recovery guard')
    checked = run(['/usr/bin/dpkg-query', '-W', '-f=${Status}\\n',
                   'build-essential', 'libssl-dev'], timeout=30)
    if checked.returncode or checked.stdout.splitlines() != ['install ok installed'] * 2:
        installed = run(['/usr/bin/apt-get', '-y', 'install', 'build-essential', 'libssl-dev'], timeout=600)
        if installed.returncode:
            _fail_step(job_id, 'mail-guard', 'Could not install build-essential and libssl-dev for mailbox recovery')
    result = run([os.path.join(new_dir, '.venv/bin/python'), '-m',
                  'daemon.snapshot_mail_guard_config', '--reload', '--backup-dir',
                  str(Path(settings.db_path).parent / 'mail-guard-config-backups')],
                 cwd=new_dir, timeout=120)
    if result.returncode:
        # Dovecot configuration and process diagnostics can contain credentials.
        _fail_step(job_id, 'mail-guard', 'Mailbox recovery guard activation failed; panel version was not switched')
    _step(job_id, 'mail-guard', 'ok')


def _install_ols_webadmin_integration(job_id: int, new_dir: str) -> None:
    script = Path(new_dir, 'scripts/reconcile_ols_webadmin.py')
    if not script.is_file():
        return
    _step(job_id, 'ols-webadmin', 'running', 'Applying HTTPS, permissions, and firewall policy')
    result = run(['/usr/bin/python3', str(script)], timeout=120)
    if result.returncode:
        _fail_step(job_id, 'ols-webadmin', 'OpenLiteSpeed WebAdmin integration failed')
    _step(job_id, 'ols-webadmin', 'ok')


def _install_ftps_tls(job_id: int, new_dir: str) -> None:
    """Reconcile FTP with the panel certificate before activating new code."""
    if not settings.panel_hostname or not Path(new_dir, 'scripts/reconcile_ftps_tls.py').is_file():
        return
    _step(job_id, 'ftps-tls', 'running', 'Applying current panel certificate to FTPS')
    result = run([os.path.join(new_dir, '.venv/bin/python'), '-m',
                  'scripts.reconcile_ftps_tls'], cwd=new_dir, timeout=120)
    if result.returncode:
        _fail_step(job_id, 'ftps-tls', 'FTPS certificate reconciliation failed')
    _step(job_id, 'ftps-tls', 'ok')


def _admin_alert_target() -> tuple[str, str]:
    """(sender, recipient) for finalizer failure alerts -- resolved NOW,
    while real daemon code is running, and passed to the stdlib-only
    finalizer as plain arguments."""
    with write_session() as session:
        sender = notifications._get_settings(session).sender_address or ""
        recipient = monitoring._get_settings(session).admin_email or ""
    return sender, recipient


def _recorded_backup_path(job: UpdateJob) -> Path | None:
    """Return the protected update backup, including legacy job records.

    Releases before ``backup_path`` was mapped stored the same absolute path
    in the successful backup step.  Accept that legacy evidence only when it
    resolves directly below the configured backup root and uses the updater's
    own pre-update directory prefix.
    """
    value = str(job.backup_path or "").strip()
    if not value:
        for step in reversed(job.steps or []):
            if (isinstance(step, dict) and step.get("step") == "backup"
                    and step.get("status") == "ok"):
                value = str(step.get("detail") or "").strip()
                break
    if not value:
        return None
    candidate = Path(value)
    root = Path(settings.update_backup_dir)
    try:
        if (not candidate.is_absolute() or candidate.is_symlink()
                or candidate.resolve().parent != root.resolve()
                or not candidate.name.startswith("pre-update-")):
            return None
    except OSError:
        return None
    return candidate


def _handoff_to_finalizer(job_id: int, new_dir: str, kind: str = "update") -> None:
    live = settings.update_live_dir
    convert_from_dir = None
    if os.path.islink(live):
        old_dir = os.path.realpath(live)
    else:
        # First-ever update: /opt/boron is still a real directory. The
        # finalizer moves it aside to a versioned dir, then symlinks.
        with write_session() as session:
            job = session.get(UpdateJob, job_id)
            old_dir = os.path.join(settings.update_versions_root, f"boron-{job.from_version}")
        if os.path.exists(old_dir):
            _fail_step(job_id, "finalize",
                       f"cannot convert {live} to a symlink: {old_dir} already exists")
        convert_from_dir = live

    finalizer_src = Path(__file__).resolve().parent.parent / "scripts" / "update_finalize.py"
    # Staged next to the control-plane DB (/var/lib/boron in production):
    # a location that survives the /opt symlink swap and isn't inside either
    # version dir -- the finalizer must not be yanked out from under itself.
    finalizer_copy = Path(settings.db_path).parent / f"update-finalize-{job_id}.py"
    try:
        shutil.copyfile(finalizer_src, finalizer_copy)
        os.chmod(finalizer_copy, 0o700)
    except OSError as exc:
        _fail_step(job_id, "finalize", f"cannot stage finalizer: {exc}")

    credential = secrets.token_urlsafe(32)
    credential_file = Path(settings.db_path).parent / f"update-finalize-{job_id}.credential"
    try:
        fd = os.open(
            credential_file,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        with os.fdopen(fd, "w") as stream:
            stream.write(credential)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        finalizer_copy.unlink(missing_ok=True)
        _fail_step(job_id, "finalize", f"cannot stage finalizer credential: {exc}")

    sender, recipient = _admin_alert_target()
    handoff_failure = None
    with write_session() as session:
        job = session.get(UpdateJob, job_id)
        if job is None:
            handoff_failure = "update job disappeared before finalizer handoff"
        elif kind == "rollback":
            candidate = session.scalars(
                select(UpdateJob)
                .where(UpdateJob.kind == "update", UpdateJob.status == "completed",
                       UpdateJob.new_dir == old_dir, UpdateJob.old_dir == new_dir)
                .order_by(UpdateJob.id.desc())
                .limit(1)
            ).first()
            recorded_backup = _recorded_backup_path(candidate) if candidate is not None else None
            target_config = recorded_backup / "etc-boron" if recorded_backup is not None else None
            if target_config is None or not target_config.is_dir() or target_config.is_symlink():
                handoff_failure = "rollback configuration snapshot is unavailable"
        else:
            target_config = None
            recorded_backup = _recorded_backup_path(job)
            rescue_config = recorded_backup / "etc-boron" if recorded_backup is not None else None
            if rescue_config is None or not rescue_config.is_dir() or rescue_config.is_symlink():
                handoff_failure = "pre-update configuration snapshot is unavailable"
    if handoff_failure:
        _fail_step(job_id, "finalize", handoff_failure)
    if kind == "rollback":
        rescue_config = Path(settings.db_path).parent / f"rollback-config-rescue-{job_id}"
        try:
            _copytree_preserve_ownership(CONFIG_PATH.parent, rescue_config)
        except OSError as exc:
            shutil.rmtree(rescue_config, ignore_errors=True)
            _fail_step(job_id, "backup", f"cannot snapshot current configuration: {exc}")
    _update_job(job_id, old_dir=old_dir, new_dir=new_dir, status="finalizing",
                finalizer_token_hash=hashlib.sha256(credential.encode()).hexdigest(),
                progress_message="finalizing: swapping symlink and restarting panel services")
    _step(job_id, "finalize", "running",
          f"handing off to detached finalizer (swap {live} -> {new_dir}, restart, health check)")

    argv = [
        *_SYSTEMD_RUN,
        f"--unit=boron-update-finalize-{job_id}",
        "/usr/bin/python3", str(finalizer_copy),
        "--job-id", str(job_id),
        "--db", settings.db_path,
        "--live", live,
        "--new-dir", new_dir,
        "--old-dir", old_dir,
        "--mode", kind,
        "--log", str(Path(settings.log_dir) / "updates.log"),
        "--api-health-url", f"https://127.0.0.1:{settings.api_bind_port}/healthz",
        "--rpc-socket", settings.rpc_socket,
        "--rpc-protocol-version", RPC_PROTOCOL_VERSION,
        "--credential-file", str(credential_file),
        "--release-metadata", str(Path(new_dir) / "release-metadata.json"),
        "--config-dir", str(CONFIG_PATH.parent),
        "--recovery-config-backup", str(rescue_config),
        "--health-timeout", str(float(settings.update_readiness_timeout_seconds)),
        "--alert-sender", sender,
        "--alert-recipient", recipient,
    ]
    if target_config is not None:
        argv += ["--target-config-backup", str(target_config)]
    if convert_from_dir:
        argv += ["--convert-live-dir"]
    proc = run(argv, timeout=30.0)
    if proc.returncode != 0:
        credential_file.unlink(missing_ok=True)
        _fail_step(job_id, "finalize",
                   f"could not launch finalizer: {(proc.stderr or proc.stdout or '')[-500:]}")
    # From here the finalizer owns the job row; this daemon process is about
    # to be restarted by it.


# --- rollback --------------------------------------------------------------------


def rollback_candidate() -> dict | None:
    """The update we can roll back to, if any: the newest completed update
    job whose new_dir is what the live symlink currently points at and
    whose old_dir still exists on disk (the cleanup cron prunes it after
    update_keep_old_days)."""
    live_target = _live_target()
    if live_target is None:
        return None
    with write_session() as session:
        jobs = session.scalars(
            select(UpdateJob)
            .where(UpdateJob.kind == "update", UpdateJob.status == "completed",
                   UpdateJob.rolled_back == False)  # noqa: E712
            .order_by(UpdateJob.id.desc())
            .limit(10)
        ).all()
        for job in jobs:
            if (job.new_dir and job.old_dir
                    and os.path.realpath(job.new_dir) == live_target
                    and os.path.isdir(job.old_dir)):
                try:
                    compatibility = rollback_compatibility(job.old_dir, settings.db_path)
                except SchemaCompatibilityError as exc:
                    compatibility = {
                        "compatible": False,
                        "reason": str(exc),
                        "schema_version": None,
                        "target": None,
                    }
                return {"job_id": job.id, "to_version": job.from_version,
                        "from_version": job.to_version, "old_dir": job.old_dir,
                        "schema_compatibility": compatibility}
    return None


def start_rollback(params: dict) -> dict:
    """Op `update.rollback`: swap back to the previous version dir (manual,
    within the retention window). Same finalizer, same guards as start."""
    initiated_by = str(params.get("initiated_by", "")).strip() or "unknown"
    candidate = rollback_candidate()
    if candidate is None:
        raise ValidationError(
            "no rollback available -- either no update has completed, the previous "
            f"version dir was pruned (kept {settings.update_keep_old_days} days), or "
            "the live symlink no longer points at the last update's target"
        )
    compatibility = candidate["schema_compatibility"]
    if not compatibility["compatible"]:
        raise ValidationError(
            "Rollback is blocked because the previous panel version cannot safely use "
            f"the current database: {compatibility['reason']}"
        )

    with write_session() as session:
        active = _active_job(session)
        if active is not None:
            raise ValidationError(f"update job {active.id} is already {active.status}")
        job = UpdateJob(
            kind="rollback",
            status="pending",
            from_version=candidate["from_version"],
            to_version=candidate["to_version"],
            old_dir=candidate["old_dir"],
            initiated_by=initiated_by,
            progress_message="queued",
        )
        session.add(job)
        session.flush()
        job_id = job.id
        result = _job_to_dict(job)

    _log_file_event({"job_id": job_id, "step": "job", "status": "queued",
                     "detail": f"rollback {candidate['from_version']} -> {candidate['to_version']} by {initiated_by}"})
    _executor.submit(_run_rollback_job, job_id, candidate["old_dir"])
    return result


def _run_rollback_job(job_id: int, target_dir: str) -> None:
    try:
        job = _claim_job(job_id, "rollback")
        if job is None:
            return
        candidate = rollback_candidate()
        if (candidate is None or not job.old_dir
                or candidate["old_dir"] != job.old_dir
                or candidate["to_version"] != job.to_version
                or candidate["from_version"] != job.from_version):
            _fail_step(job_id, "preflight", "queued rollback target is no longer current")
        target_dir = job.old_dir
        # Sanity: the rollback target must still look like a runnable install.
        for essential in ("version.py", ".venv/bin/python", "daemon/server.py"):
            if not os.path.exists(os.path.join(target_dir, essential)):
                _fail_step(job_id, "preflight", f"rollback target {target_dir} is missing {essential}")
        try:
            compatibility = assert_rollback_compatible(target_dir, settings.db_path)
        except SchemaCompatibilityError as exc:
            _fail_step(job_id, "preflight", f"schema compatibility check failed: {exc}")
        _step(
            job_id,
            "preflight",
            "ok",
            f"rolling back to {target_dir}; {compatibility['reason']}",
        )
        _handoff_to_finalizer(job_id, target_dir, kind="rollback")
    except _StepFailed:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.exception("rollback job %s crashed", job_id)
        _update_job(job_id, status="failed", error=str(exc)[:4000],
                    progress_message="failed", completed_at=utcnow())


# --- manual control-plane database recovery --------------------------------------


def _database_restore_status_path() -> Path:
    return Path(settings.db_path).parent / "database-restore-status.json"


def _read_database_restore_status() -> dict | None:
    path = _database_restore_status_path()
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or metadata.st_size > 64 * 1024:
            return None
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    return {str(key): value for key, value in document.items()
            if key in {"state", "source", "target", "rescue", "error", "rolled_back",
                       "recovery_healthy", "updated_at"}}


def _database_backup_row(directory: Path) -> dict | None:
    database = directory / "boron.db"
    if not database.is_file() or database.is_symlink():
        return None
    try:
        integrity = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5)
        try:
            valid = integrity.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        finally:
            integrity.close()
        compatibility = rollback_compatibility(settings.update_live_dir, str(database))
        metadata = directory.stat()
    except (OSError, sqlite3.Error, SchemaCompatibilityError) as exc:
        valid = False
        compatibility = {"compatible": False, "reason": f"Snapshot could not be validated ({type(exc).__name__})"}
        try:
            metadata = directory.stat()
        except OSError:
            return None
    return {
        "id": directory.name,
        "created_at": dt.datetime.fromtimestamp(metadata.st_mtime, dt.timezone.utc).isoformat(),
        "database_bytes": database.stat().st_size,
        "integrity_ok": valid,
        "schema_compatible": bool(valid and compatibility.get("compatible")),
        "compatibility_reason": str(compatibility.get("reason", ""))[:500],
    }


def list_database_backups(_params: dict | None = None) -> dict:
    rows = []
    for directory in _preupdate_backup_dirs():
        row = _database_backup_row(directory)
        if row is not None:
            rows.append(row)
    return {"backups": rows, "restore_status": _read_database_restore_status()}


def restore_database_backup(params: dict) -> dict:
    """Launch a detached, automatically compensating database restore."""
    if params.get("confirm") is not True:
        raise ValidationError("Confirm the panel database restore")
    backup_id = str(params.get("backup_id", ""))
    if not re.fullmatch(r"pre-update-[A-Za-z0-9._-]{1,180}", backup_id):
        raise ValidationError("Select a valid pre-update backup")
    source_dir = next((item for item in _preupdate_backup_dirs() if item.name == backup_id), None)
    if source_dir is None:
        raise ValidationError("The selected pre-update backup is unavailable")
    row = _database_backup_row(source_dir)
    if not row or not row["integrity_ok"] or not row["schema_compatible"]:
        raise ValidationError(
            "This panel database backup cannot be restored by the running release: "
            + (row["compatibility_reason"] if row else "snapshot validation failed")
        )
    active = _active_task_inventory()
    if active:
        raise ValidationError("Wait for active backup, restore, migration, or maintenance tasks to finish")
    status = _read_database_restore_status()
    if status and status.get("state") in {"queued", "validating", "stopping", "starting"}:
        raise ValidationError("A panel database restore is already running")

    root = Path(settings.db_path).parent
    script_source = Path(__file__).resolve().parent.parent / "scripts" / "restore_panel_database.py"
    script_copy = root / "restore-panel-database.py"
    shutil.copyfile(script_source, script_copy)
    os.chmod(script_copy, 0o700)
    status_path = _database_restore_status_path()
    status_path.write_text(json.dumps({
        "state": "queued", "source": backup_id, "updated_at": utcnow().isoformat(),
    }, separators=(",", ":")), encoding="utf-8")
    os.chmod(status_path, 0o600)
    rescue = root / f"boron-before-restore-{utcnow().strftime('%Y%m%d-%H%M%S')}.db"
    argv = [
        *_SYSTEMD_RUN, "--unit=boron-database-restore",
        "/usr/bin/python3", str(script_copy),
        "--source", str(source_dir / "boron.db"),
        "--target", settings.db_path,
        "--rescue", str(rescue),
        "--status-file", str(status_path),
        "--health-url", f"https://127.0.0.1:{settings.api_bind_port}/healthz",
    ]
    result = run(argv, timeout=30)
    if result.returncode:
        status_path.write_text(json.dumps({
            "state": "failed", "source": backup_id,
            "error": "The detached restore worker could not be started",
            "updated_at": utcnow().isoformat(),
        }, separators=(",", ":")), encoding="utf-8")
        raise ValidationError("The detached panel database restore could not be started")
    return {"started": True, "backup_id": backup_id, "status": _read_database_restore_status()}


# --- status / history / log ops ----------------------------------------------------


def get_status(params: dict | None = None) -> dict:
    """Op `update.status`: everything the admin UI's update card needs in
    one call. Serves the cached check (may refresh if the 1h cache
    expired); never forces a GitHub call -- that's update.check force=True.
    """
    info = check({})
    with write_session() as session:
        active = _active_job(session)
        last = session.scalar(select(UpdateJob).order_by(UpdateJob.id.desc()))
        active_dict = _job_to_dict(active) if active else None
        last_dict = _job_to_dict(last) if last else None
    candidate = rollback_candidate()
    live = settings.update_live_dir
    return {
        **info,
        "active_job": active_dict,
        "last_job": last_dict,
        "rollback_available": bool(
            candidate and candidate["schema_compatibility"]["compatible"]
        ),
        "rollback_to": candidate["to_version"] if candidate else None,
        "rollback_blocked_reason": (
            candidate["schema_compatibility"]["reason"]
            if candidate and not candidate["schema_compatibility"]["compatible"]
            else None
        ),
        "live_dir": live,
        "live_target": _live_target(),
        "symlink_layout": os.path.islink(live),
    }


def get_history(params: dict | None = None) -> dict:
    limit = min(int((params or {}).get("limit", 50)), 200)
    with write_session() as session:
        jobs = session.scalars(select(UpdateJob).order_by(UpdateJob.id.desc()).limit(limit)).all()
        return {"jobs": [_job_to_dict(j) for j in jobs]}


def get_log(params: dict | None = None) -> dict:
    params = params or {}
    job_dict = None
    with write_session() as session:
        if params.get("job_id") is not None:
            job = session.get(UpdateJob, int(params["job_id"]))
            if job is None:
                raise LookupError(f"no update job {params['job_id']}")
        else:
            job = session.scalar(select(UpdateJob).order_by(UpdateJob.id.desc()))
        if job is not None:
            job_dict = _job_to_dict(job)
    return {"job": job_dict, "log": tail_update_log()}


# --- daily cron: notify + prune -----------------------------------------------------


def notify_if_update_available() -> dict:
    """Daily cron body: force-refresh the check; email the admin ONCE per
    distinct new release (last_notified_version dedup)."""
    info = check({"force": True})
    if not info["configured"] or not info["update_available"]:
        return {**info, "notified": False}

    from daemon import monitoring, notifications

    latest = info["latest_version"]
    with write_session() as session:
        state = _get_state(session)
        if state.last_notified_version == latest:
            return {**info, "notified": False}
        sender = notifications._get_settings(session).sender_address or ""
        recipient = monitoring._get_settings(session).admin_email or ""
    if not sender or not recipient:
        logger.info("update %s available but admin email/sender not configured -- not emailing", latest)
        return {**info, "notified": False}
    try:  # noqa: SIM105
        notifications._send_email(
            sender, recipient,
            f"[Boron] Update available: v{latest}",
            f"A new Boron release is available.\n\n"
            f"  Current version: {info['current_version']}\n"
            f"  Latest version:  {latest}\n"
            f"  Changelog:       {info['changelog_url']}\n\n"
            f"Apply it from the admin panel: Updates page -> Update now.\n",
        )
    except OSError as exc:
        logger.warning("could not send update-available email: %s", exc)
        return {**info, "notified": False}
    with write_session() as session:
        _get_state(session).last_notified_version = latest
    logger.info("emailed admin about available update v%s", latest)
    return {**info, "notified": True}


def cleanup_old_versions(params: dict | None = None) -> dict:
    """Prune stale /opt/boron-X.Y.Z dirs after the rollback window
    (goal 4k). Hard guards, in order: basename must match the strict
    version-dir regex (so /opt/boron-nodejs etc. can never match), must
    be a real non-symlink directory, must not be the live symlink's target,
    must not be referenced by any update/rollback job completed within the
    window, and must be older than update_keep_old_days.

    The job-reference guard is load-bearing, not redundant with mtime: the
    first-ever update CONVERTS the months-old /opt/boron directory into
    the versioned rollback target -- its mtime long predates the update, so
    an mtime-only rule would prune the rollback target the very same night
    and silently void the 3-day rollback promise (the finalizer also bumps
    the old dir's mtime on success, but a DB-backed guard doesn't depend on
    that having happened)."""
    root = settings.update_versions_root
    live_target = _live_target()
    cutoff = time.time() - settings.update_keep_old_days * 86400

    protected: set[str] = set()
    with write_session() as session:
        cutoff_dt = utcnow() - dt.timedelta(days=settings.update_keep_old_days)
        recent = session.scalars(
            select(UpdateJob).where(UpdateJob.completed_at.is_not(None))
            .order_by(UpdateJob.id.desc()).limit(20)
        ).all()
        for job in recent:
            completed = job.completed_at
            if completed is not None and completed.tzinfo is None:
                completed = completed.replace(tzinfo=dt.timezone.utc)
            if completed is not None and completed >= cutoff_dt:
                for d in (job.old_dir, job.new_dir):
                    if d:
                        protected.add(os.path.realpath(d))

    removed, kept = [], []
    try:
        entries = os.listdir(root)
    except OSError:
        entries = []
    for name in entries:
        if not _VERSION_DIR_RE.match(name):
            continue
        path = os.path.join(root, name)
        if os.path.islink(path) or not os.path.isdir(path):
            continue
        if (live_target and os.path.realpath(path) == live_target) or os.path.realpath(path) in protected:
            kept.append(name)
            continue
        try:
            if os.path.getmtime(path) > cutoff:
                kept.append(name)
                continue
            shutil.rmtree(path)
            removed.append(name)
            _log_file_event({"step": "cleanup", "status": "ok", "detail": f"pruned {path}"})
        except OSError as exc:
            logger.warning("could not prune %s: %s", path, exc)
    return {"removed": removed, "kept": kept}
