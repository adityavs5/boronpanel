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
  GitHub-owned host allowlist), SHA256 verify BEFORE extraction, tarball
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

OLS and hosted sites are never touched; only the two panel units restart.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from sqlalchemy import select

from daemon import monitoring, notifications
from daemon.procutil import run
from shared.config import settings
from shared.db import write_session
from shared.models import UpdateJob, UpdateState, utcnow
from shared.validation import ValidationError
from version import BORON_VERSION

logger = logging.getLogger("borond.updates")

# Test hook, same convention as daemon/cloudflare.py's _transport: tests
# swap in an httpx.MockTransport so nothing ever hits the real GitHub API.
_transport: httpx.BaseTransport | None = None

# Test hook for the finalizer launch: tests replace the systemd-run argv
# prefix to exercise the handoff without systemd.
_SYSTEMD_RUN = ["systemd-run", "--collect"]

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

    Returns {version, changelog_url, tarball_url, checksum_url}. Raises
    ValidationError on anything malformed -- including assets whose
    download URL is not under https://github.com/{repo}/releases/download/
    (goal security rule 1: the tarball URL comes from github.com releases
    only, never anywhere an attacker could steer it).
    """
    repo = _require_repo()
    url = f"{GITHUB_API}/repos/{repo}/releases/" + (f"tags/{tag}" if tag else "latest")
    with httpx.Client(timeout=20.0, transport=_transport, follow_redirects=False) as client:
        resp = client.get(url, headers=_github_headers())
    if resp.status_code == 404:
        raise ValidationError(f"no release found ({'tag ' + tag if tag else 'latest'}) in {repo}")
    resp.raise_for_status()
    data = resp.json()

    tag_name = str(data.get("tag_name", ""))
    version = "%d.%d.%d" % parse_version(tag_name)
    prefix = _release_download_prefix(repo)
    tarball_url = checksum_url = None
    for asset in data.get("assets", []):
        name = asset.get("name", "")
        dl = asset.get("browser_download_url", "")
        if name == f"boron-{version}.tar.gz":
            tarball_url = dl
        elif name == f"boron-{version}.sha256":
            checksum_url = dl
    if not tarball_url or not checksum_url:
        raise ValidationError(
            f"release {tag_name} is missing boron-{version}.tar.gz and/or its .sha256 asset"
        )
    for dl in (tarball_url, checksum_url):
        if not dl.startswith(prefix):
            raise ValidationError(f"refusing release asset URL outside {prefix}: {dl}")
    return {
        "version": version,
        "changelog_url": str(data.get("html_url", "")),
        "tarball_url": tarball_url,
        "checksum_url": checksum_url,
    }


def check(params: dict | None = None) -> dict:
    """Op `update.check`: current vs latest, cached for 1h.

    force=True bypasses the cache (the UI's "Check now" button and the
    daily cron). An unconfigured repo reports configured=False rather than
    erroring, so the admin UI can render a setup hint instead of a 400.
    """
    params = params or {}
    force = bool(params.get("force", False))

    configured = bool(settings.update_github_repo.strip())
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
        }


# --- job plumbing ---------------------------------------------------------------


def _job_to_dict(job: UpdateJob) -> dict:
    duration = None
    if job.completed_at and job.started_at:
        duration = (job.completed_at - job.started_at).total_seconds()
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "from_version": job.from_version,
        "to_version": job.to_version,
        "initiated_by": job.initiated_by,
        "progress_message": job.progress_message,
        "steps": job.steps,
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
    """Op `update.start`. The API layer has already done require_admin +
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
        )
        session.add(job)
        session.flush()
        job_id = job.id
        result = _job_to_dict(job)

    _log_file_event({"job_id": job_id, "step": "job", "status": "queued",
                     "detail": f"update {BORON_VERSION} -> {to_version} by {initiated_by}"})
    _executor.submit(_run_update_job, job_id, to_version)
    return result


def _run_update_job(job_id: int, to_version: str) -> None:
    try:
        _update_job(job_id, status="running", progress_message="starting")

        # (a) pre-flight ------------------------------------------------------
        _preflight(job_id)

        # (b) backup ----------------------------------------------------------
        backup_dir = _backup(job_id, to_version)

        # (c)+(d) download + checksum ------------------------------------------
        tarball = _download_and_verify(job_id, to_version)

        # (e) staged extraction ------------------------------------------------
        new_dir = _extract_staged(job_id, tarball, to_version)

        # venv for the staged tree (units exec /opt/boron/.venv/... which
        # resolves through the symlink into the new dir after the swap).
        _build_venv(job_id, new_dir)

        # (f) migrations from the NEW version against the live DB --------------
        _run_migrations(job_id, new_dir)

        # (g..j) handoff: swap/restart/health/rollback happen in the detached
        # finalizer -- see module docstring for why. backup_dir already
        # recorded in the job's steps for the operator.
        del backup_dir
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


def _preflight(job_id: int) -> None:
    live = settings.update_live_dir
    if not os.path.isdir(live):
        _fail_step(job_id, "preflight", f"live install dir {live} does not exist")

    for mount in (settings.update_versions_root, os.path.dirname(settings.db_path) or "/"):
        try:
            free_mb = shutil.disk_usage(mount).free // (1024 * 1024)
        except OSError as exc:
            _fail_step(job_id, "preflight", f"cannot stat {mount}: {exc}")
        if free_mb < settings.update_preflight_min_free_mb:
            _fail_step(job_id, "preflight",
                       f"only {free_mb}MB free on {mount} (need {settings.update_preflight_min_free_mb}MB)")

    if settings.update_preflight_tests:
        _step(job_id, "preflight", "running", "running the live install's test suite (this takes several minutes)")
        pytest_bin = os.path.join(live, ".venv", "bin", "python")
        if not os.path.exists(pytest_bin):
            _fail_step(job_id, "preflight", f"no venv python at {pytest_bin}")
        proc = _pytest_run(pytest_bin, live)
        if proc.returncode != 0:
            tail = (proc.stdout or "").strip().splitlines()[-15:]
            _fail_step(job_id, "preflight",
                       "live test suite failing -- update aborted. Tail: " + " | ".join(tail))
    _step(job_id, "preflight", "ok")


def _pytest_run(python_bin: str, live_dir: str):
    """Isolated so tests can monkeypatch it. Runs the LIVE tree's own suite
    (goal 4a) -- the same discipline as a manual run on this box: conftest's
    autouse isolation keeps tests away from production log/db state."""
    # Tests exercise setgid directory semantics and must not inherit the
    # daemon's RestrictSUIDSGID seccomp filter. A transient service provides
    # the same execution context as a manual release check without relaxing
    # the long-running daemon. RuntimeMaxSec also kills orphaned test workers.
    return run(
        ["systemd-run", "--quiet", "--wait", "--pipe", "--collect",
         f"--property=WorkingDirectory={live_dir}",
         "--property=RuntimeMaxSec=3600",
         python_bin, "-m", "pytest", "-q", "--tb=no", "-p", "no:cacheprovider"],
        cwd=live_dir, timeout=3660.0,
    )


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
        # /etc/boron including secrets.env -- the backup dir itself is
        # 0700 root-only, matching the strictest file it contains.
        shutil.copytree("/etc/boron", os.path.join(dest, "etc-boron"), symlinks=True)
    except (OSError, sqlite3.Error) as exc:
        _fail_step(job_id, "backup", f"{exc}")
    _step(job_id, "backup", "ok", dest)
    return dest


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
                with open(dest, "wb") as f:
                    for chunk in resp.iter_bytes():
                        written += len(chunk)
                        if written > max_bytes:
                            raise ValidationError(f"download exceeds {max_bytes} bytes -- aborted")
                        f.write(chunk)
                return written
    raise ValidationError(f"too many redirects (> {_MAX_REDIRECTS}) downloading {url}")


def _download_and_verify(job_id: int, to_version: str) -> str:
    with write_session() as session:
        state = _get_state(session)
        tarball_url, checksum_url = state.tarball_url, state.checksum_url
    # The cached URLs are for the *latest* release; refetch by tag when
    # updating to anything else (or when the cache is empty).
    if not tarball_url or f"boron-{to_version}.tar.gz" not in tarball_url:
        try:
            info = _fetch_release(tag=f"v{to_version}")
        except (httpx.HTTPError, ValidationError) as exc:
            _fail_step(job_id, "download", f"cannot resolve release v{to_version}: {exc}")
        tarball_url, checksum_url = info["tarball_url"], info["checksum_url"]

    download_dir = settings.update_download_dir
    os.makedirs(download_dir, mode=0o700, exist_ok=True)
    tarball = os.path.join(download_dir, f"boron-{to_version}.tar.gz")
    checksum_file = os.path.join(download_dir, f"boron-{to_version}.sha256")
    try:
        size = _safe_download(tarball_url, tarball, settings.update_max_download_bytes)
        _safe_download(checksum_url, checksum_file, 64 * 1024)
    except (httpx.HTTPError, ValidationError, OSError) as exc:
        _fail_step(job_id, "download", str(exc))
    _step(job_id, "download", "ok", f"{size} bytes from {tarball_url}")

    # (d) SHA256 BEFORE anything touches the archive contents.
    try:
        expected = Path(checksum_file).read_text().split()[0].strip().lower()
    except (OSError, IndexError):
        expected = ""
    if not re.fullmatch(r"[0-9a-f]{64}", expected or ""):
        _remove_quietly(tarball, checksum_file)
        _fail_step(job_id, "checksum", "checksum asset is malformed -- update aborted")
    digest = hashlib.sha256()
    with open(tarball, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != expected:
        _remove_quietly(tarball, checksum_file)
        _fail_step(job_id, "checksum",
                   f"SHA256 mismatch (expected {expected[:12]}..., got {digest.hexdigest()[:12]}...) -- update aborted")
    _step(job_id, "checksum", "ok", expected)
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


def _extract_staged(job_id: int, tarball: str, to_version: str) -> str:
    prefix = f"boron-{to_version}"
    target = os.path.join(settings.update_versions_root, prefix)
    live_target = _live_target()
    if live_target and os.path.realpath(target) == live_target:
        _fail_step(job_id, "extract", f"{target} is the live install -- refusing to overwrite")
    tmp_root = None
    try:
        with tarfile.open(tarball, "r:gz") as tf:
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
    except (ValidationError, tarfile.TarError, OSError) as exc:
        _fail_step(job_id, "extract", str(exc))
    finally:
        if tmp_root:
            shutil.rmtree(tmp_root, ignore_errors=True)
        _remove_quietly(tarball)
    _step(job_id, "extract", "ok", target)
    return target


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
    proc = run(
        [os.path.join(new_dir, ".venv", "bin", "python"), "-c",
         "from shared.db import init_db; init_db()"],
        cwd=new_dir, timeout=120.0,
    )
    if proc.returncode != 0:
        _fail_step(job_id, "migrate", f"new version's migrations failed: {(proc.stderr or '')[-800:]}")
    _step(job_id, "migrate", "ok")


def _admin_alert_target() -> tuple[str, str]:
    """(sender, recipient) for finalizer failure alerts -- resolved NOW,
    while real daemon code is running, and passed to the stdlib-only
    finalizer as plain arguments."""
    with write_session() as session:
        sender = notifications._get_settings(session).sender_address or ""
        recipient = monitoring._get_settings(session).admin_email or ""
    return sender, recipient


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

    sender, recipient = _admin_alert_target()
    _update_job(job_id, old_dir=old_dir, new_dir=new_dir, status="finalizing",
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
        "--alert-sender", sender,
        "--alert-recipient", recipient,
    ]
    if convert_from_dir:
        argv += ["--convert-live-dir"]
    proc = run(argv, timeout=30.0)
    if proc.returncode != 0:
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
                return {"job_id": job.id, "to_version": job.from_version,
                        "from_version": job.to_version, "old_dir": job.old_dir}
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

    with write_session() as session:
        active = _active_job(session)
        if active is not None:
            raise ValidationError(f"update job {active.id} is already {active.status}")
        job = UpdateJob(
            kind="rollback",
            status="pending",
            from_version=candidate["from_version"],
            to_version=candidate["to_version"],
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
        _update_job(job_id, status="running", progress_message="starting rollback")
        # Sanity: the rollback target must still look like a runnable install.
        for essential in ("version.py", ".venv/bin/python", "daemon/server.py"):
            if not os.path.exists(os.path.join(target_dir, essential)):
                _fail_step(job_id, "preflight", f"rollback target {target_dir} is missing {essential}")
        _step(job_id, "preflight", "ok", f"rolling back to {target_dir}")
        _handoff_to_finalizer(job_id, target_dir, kind="rollback")
    except _StepFailed:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.exception("rollback job %s crashed", job_id)
        _update_job(job_id, status="failed", error=str(exc)[:4000],
                    progress_message="failed", completed_at=utcnow())


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
        "rollback_available": candidate is not None,
        "rollback_to": candidate["to_version"] if candidate else None,
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
