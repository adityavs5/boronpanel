"""daemon/updates.py -- update check, download safety, tarball validation,
job guards, rollback candidacy, cleanup safety.

All GitHub traffic goes through updates._transport (httpx.MockTransport);
nothing here touches the network, /opt, or systemd. Filesystem work happens
under tmp_path via monkeypatched settings.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import time
from pathlib import Path

import httpx
import pytest

from shared.config import settings
from shared.models import UpdateJob, UpdateState
from shared.validation import ValidationError

import daemon.updates as updates


# --- helpers ---------------------------------------------------------------------


def _release_json(version="1.0.1", repo="acme/boron", assets=True, tag=None):
    tag = tag or f"v{version}"
    prefix = f"https://github.com/{repo}/releases/download/{tag}"
    data = {
        "tag_name": tag,
        "html_url": f"https://github.com/{repo}/releases/tag/{tag}",
        "assets": [],
    }
    if assets:
        data["assets"] = [
            {"name": f"boron-{version}.tar.gz",
             "browser_download_url": f"{prefix}/boron-{version}.tar.gz"},
            {"name": f"boron-{version}.sha256",
             "browser_download_url": f"{prefix}/boron-{version}.sha256"},
        ]
    return data


def _make_tarball_bytes(version="1.0.1", members_extra=None) -> bytes:
    """A minimal well-formed release tarball: boron-{v}/ prefix, regular
    files only."""
    buf = io.BytesIO()
    prefix = f"boron-{version}"
    files = {
        f"{prefix}/version.py": f'BORON_VERSION = "{version}"\n',
        f"{prefix}/requirements.txt": "httpx\n",
        f"{prefix}/daemon/server.py": "# stub\n",
        f"{prefix}/api/main.py": "# stub\n",
    }
    if members_extra:
        files.update(members_extra)
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _mock_github(monkeypatch, version="1.0.1", repo="acme/boron",
                 tarball: bytes | None = None, sha256_text: str | None = None,
                 counter: dict | None = None, redirect_host="objects.githubusercontent.com"):
    """MockTransport speaking the whole flow: releases API -> 302 from
    github.com -> content from the CDN host."""
    tarball = tarball if tarball is not None else _make_tarball_bytes(version)
    sha = sha256_text if sha256_text is not None else (
        hashlib.sha256(tarball).hexdigest() + f"  boron-{version}.tar.gz\n")

    def handler(request: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter["calls"] = counter.get("calls", 0) + 1
        url = str(request.url)
        host = request.url.host
        if host == "api.github.com":
            return httpx.Response(200, json=_release_json(version, repo))
        if host == "github.com":
            name = url.rsplit("/", 1)[-1]
            return httpx.Response(302, headers={
                "location": f"https://{redirect_host}/x/{name}"})
        if host == redirect_host:
            if url.endswith(".sha256"):
                return httpx.Response(200, content=sha.encode())
            return httpx.Response(200, content=tarball)
        return httpx.Response(404)

    monkeypatch.setattr(updates, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(settings, "update_github_repo", repo)
    return tarball


@pytest.fixture
def update_env(isolated_db, tmp_path, monkeypatch):
    """Filesystem sandbox: versions root, live symlink layout, download and
    backup dirs, log dir all under tmp_path."""
    root = tmp_path / "opt"
    root.mkdir()
    live_target = root / "boron-1.0.0"
    live_target.mkdir()
    live = root / "boron"
    live.symlink_to(live_target)
    monkeypatch.setattr(settings, "update_versions_root", str(root))
    monkeypatch.setattr(settings, "update_live_dir", str(live))
    monkeypatch.setattr(settings, "update_download_dir", str(tmp_path / "dl"))
    monkeypatch.setattr(settings, "update_backup_dir", str(tmp_path / "backups"))
    monkeypatch.setattr(settings, "log_dir", str(tmp_path / "log"))
    return {"root": root, "live": live, "live_target": live_target, "tmp": tmp_path}


def _make_job(kind="update", status="running", **kw) -> int:
    from shared.db import write_session

    with write_session() as session:
        job = UpdateJob(kind=kind, status=status, from_version="1.0.0",
                        to_version=kw.pop("to_version", "1.0.1"),
                        initiated_by="tester", **kw)
        session.add(job)
        session.flush()
        return job.id


def _get_job(job_id: int) -> dict:
    from shared.db import write_session

    with write_session() as session:
        return updates._job_to_dict(session.get(UpdateJob, job_id))


# --- version comparison -------------------------------------------------------------


def test_parse_version_accepts_v_prefix():
    assert updates.parse_version("v1.2.3") == (1, 2, 3)
    assert updates.parse_version("1.2.3") == (1, 2, 3)


@pytest.mark.parametrize("bad", ["", "1.2", "1.2.3.4", "abc", "1.2.x", "v", "1.2.3-rc1"])
def test_parse_version_rejects_garbage(bad):
    with pytest.raises(ValidationError):
        updates.parse_version(bad)


def test_is_newer_comparisons():
    assert updates.is_newer("1.0.1", "1.0.0")
    assert updates.is_newer("2.0.0", "1.99.99")
    assert updates.is_newer("1.10.0", "1.9.9")  # numeric, not lexicographic
    assert not updates.is_newer("1.0.0", "1.0.0")
    assert not updates.is_newer("0.9.9", "1.0.0")


# --- update.check --------------------------------------------------------------------


def test_check_unconfigured_reports_not_configured(isolated_db, monkeypatch):
    monkeypatch.setattr(settings, "update_github_repo", "")
    out = updates.check({})
    assert out["configured"] is False
    assert out["update_available"] is False
    assert out["current_version"] == updates.BORON_VERSION


def test_check_fetches_compares_and_caches(isolated_db, monkeypatch):
    counter = {}
    _mock_github(monkeypatch, version="99.0.0", counter=counter)
    out = updates.check({})
    assert out["configured"] and out["update_available"]
    assert out["latest_version"] == "99.0.0"
    assert out["changelog_url"].startswith("https://github.com/")
    assert counter["calls"] == 1

    # Second call inside the cache window: served from UpdateState, no HTTP.
    out2 = updates.check({})
    assert out2["latest_version"] == "99.0.0"
    assert counter["calls"] == 1

    # force=True bypasses the cache.
    updates.check({"force": True})
    assert counter["calls"] == 2


def test_check_not_available_when_older_or_equal(isolated_db, monkeypatch):
    _mock_github(monkeypatch, version=updates.BORON_VERSION)
    out = updates.check({"force": True})
    assert out["update_available"] is False


def test_check_error_is_cached_and_surfaced(isolated_db, monkeypatch):
    def handler(request):
        handler.calls = getattr(handler, "calls", 0) + 1
        return httpx.Response(404)

    monkeypatch.setattr(updates, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(settings, "update_github_repo", "acme/boron")
    out = updates.check({})
    assert out["error"] and "no release found" in out["error"]
    assert out["update_available"] is False
    # A failed check is cached too -- dashboard polling must not hammer GitHub.
    updates.check({})
    assert handler.calls == 1


def test_check_refuses_assets_outside_github_releases(isolated_db, monkeypatch):
    def handler(request):
        data = _release_json("1.0.1")
        data["assets"][0]["browser_download_url"] = "https://evil.example.com/boron-1.0.1.tar.gz"
        return httpx.Response(200, json=data)

    monkeypatch.setattr(updates, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(settings, "update_github_repo", "acme/boron")
    out = updates.check({"force": True})
    assert "refusing release asset URL" in (out["error"] or "")
    assert out["latest_version"] is None


# --- download safety ------------------------------------------------------------------


def test_safe_download_follows_github_redirect_only(update_env, monkeypatch, tmp_path):
    tarball = _mock_github(monkeypatch)
    dest = tmp_path / "out.tar.gz"
    n = updates._safe_download(
        "https://github.com/acme/boron/releases/download/v1.0.1/boron-1.0.1.tar.gz",
        str(dest), max_bytes=10_000_000)
    assert n == len(tarball) and dest.read_bytes() == tarball


def test_safe_download_refuses_first_hop_outside_release_prefix(update_env, monkeypatch):
    _mock_github(monkeypatch)
    with pytest.raises(ValidationError, match="refusing download outside"):
        updates._safe_download("https://github.com/acme/boron/archive/main.tar.gz",
                               "/dev/null", max_bytes=1000)
    with pytest.raises(ValidationError, match="refusing"):
        updates._safe_download("https://evil.example.com/acme/boron/releases/download/x.tar.gz",
                               "/dev/null", max_bytes=1000)


def test_safe_download_refuses_redirect_to_non_github_host(update_env, monkeypatch, tmp_path):
    _mock_github(monkeypatch, redirect_host="evil.example.com")
    with pytest.raises(ValidationError, match="non-GitHub host"):
        updates._safe_download(
            "https://github.com/acme/boron/releases/download/v1.0.1/boron-1.0.1.tar.gz",
            str(tmp_path / "x"), max_bytes=10_000_000)


def test_safe_download_refuses_http_redirect(update_env, monkeypatch, tmp_path):
    def handler(request):
        if request.url.host == "github.com":
            return httpx.Response(302, headers={"location": "http://objects.githubusercontent.com/x"})
        return httpx.Response(200, content=b"x")

    monkeypatch.setattr(updates, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(settings, "update_github_repo", "acme/boron")
    with pytest.raises(ValidationError, match="non-https"):
        updates._safe_download(
            "https://github.com/acme/boron/releases/download/v1.0.1/boron-1.0.1.tar.gz",
            str(tmp_path / "x"), max_bytes=1000)


def test_safe_download_enforces_size_cap(update_env, monkeypatch, tmp_path):
    _mock_github(monkeypatch, tarball=b"A" * 5000)
    with pytest.raises(ValidationError, match="exceeds"):
        updates._safe_download(
            "https://github.com/acme/boron/releases/download/v1.0.1/boron-1.0.1.tar.gz",
            str(tmp_path / "x"), max_bytes=1024)


def test_safe_download_redirect_loop_capped(update_env, monkeypatch, tmp_path):
    def handler(request):
        return httpx.Response(302, headers={
            "location": "https://github.com/acme/boron/releases/download/v1/loop.tar.gz"})

    monkeypatch.setattr(updates, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(settings, "update_github_repo", "acme/boron")
    with pytest.raises(ValidationError, match="too many redirects"):
        updates._safe_download(
            "https://github.com/acme/boron/releases/download/v1/loop.tar.gz",
            str(tmp_path / "x"), max_bytes=1000)


# --- checksum gate (goal: mismatch -> update aborted) ------------------------------------


def test_checksum_mismatch_aborts_update(update_env, monkeypatch):
    _mock_github(monkeypatch, sha256_text="0" * 64 + "  boron-1.0.1.tar.gz\n")
    updates.check({"force": True})  # populate cached asset URLs
    job_id = _make_job()
    with pytest.raises(updates._StepFailed):
        updates._download_and_verify(job_id, "1.0.1")
    job = _get_job(job_id)
    assert job["status"] == "failed"
    assert "SHA256 mismatch" in job["error"]
    # The poisoned download must not survive.
    assert not list((update_env["tmp"] / "dl").glob("*.tar.gz"))


def test_checksum_malformed_asset_aborts(update_env, monkeypatch):
    _mock_github(monkeypatch, sha256_text="not a checksum at all\n")
    updates.check({"force": True})
    job_id = _make_job()
    with pytest.raises(updates._StepFailed):
        updates._download_and_verify(job_id, "1.0.1")
    assert "malformed" in _get_job(job_id)["error"]


def test_checksum_match_passes(update_env, monkeypatch):
    tarball = _mock_github(monkeypatch)
    updates.check({"force": True})
    job_id = _make_job()
    path = updates._download_and_verify(job_id, "1.0.1")
    assert os.path.exists(path)
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == hashlib.sha256(tarball).hexdigest()


# --- tarball member validation (goal: path traversal -> rejected) --------------------------


def _open_tar(data: bytes) -> tarfile.TarFile:
    return tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")


def _tar_with_member(info: tarfile.TarInfo, content: bytes = b"") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        base = tarfile.TarInfo("boron-1.0.1/ok.txt")
        base.size = 2
        tf.addfile(base, io.BytesIO(b"ok"))
        if info.isreg():
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
        else:
            tf.addfile(info)
    return buf.getvalue()


def test_tarball_traversal_dotdot_rejected():
    evil = tarfile.TarInfo("boron-1.0.1/../../etc/cron.d/pwned")
    with _open_tar(_tar_with_member(evil, b"boom")) as tf:
        with pytest.raises(ValidationError, match="escapes extraction dir"):
            updates.validate_tarball_members(tf, "boron-1.0.1", 10_000_000)


def test_tarball_absolute_path_rejected():
    evil = tarfile.TarInfo("/etc/shadow")
    with _open_tar(_tar_with_member(evil, b"boom")) as tf:
        with pytest.raises(ValidationError, match="escapes extraction dir"):
            updates.validate_tarball_members(tf, "boron-1.0.1", 10_000_000)


def test_tarball_member_outside_prefix_rejected():
    evil = tarfile.TarInfo("boron-9.9.9/sneaky.py")
    with _open_tar(_tar_with_member(evil, b"boom")) as tf:
        with pytest.raises(ValidationError, match="outside boron-1.0.1"):
            updates.validate_tarball_members(tf, "boron-1.0.1", 10_000_000)


def test_tarball_symlink_member_rejected():
    evil = tarfile.TarInfo("boron-1.0.1/link")
    evil.type = tarfile.SYMTYPE
    evil.linkname = "/etc/boron/secrets.env"
    with _open_tar(_tar_with_member(evil)) as tf:
        with pytest.raises(ValidationError, match="link member"):
            updates.validate_tarball_members(tf, "boron-1.0.1", 10_000_000)


def test_tarball_hardlink_member_rejected():
    evil = tarfile.TarInfo("boron-1.0.1/hard")
    evil.type = tarfile.LNKTYPE
    evil.linkname = "../../../etc/passwd"
    with _open_tar(_tar_with_member(evil)) as tf:
        with pytest.raises(ValidationError, match="link member"):
            updates.validate_tarball_members(tf, "boron-1.0.1", 10_000_000)


def test_tarball_device_member_rejected():
    evil = tarfile.TarInfo("boron-1.0.1/dev")
    evil.type = tarfile.CHRTYPE
    with _open_tar(_tar_with_member(evil)) as tf:
        with pytest.raises(ValidationError, match="special member"):
            updates.validate_tarball_members(tf, "boron-1.0.1", 10_000_000)


def test_tarball_size_bomb_rejected():
    with _open_tar(_make_tarball_bytes("1.0.1")) as tf:
        with pytest.raises(ValidationError, match="size cap"):
            updates.validate_tarball_members(tf, "boron-1.0.1", max_total_bytes=1)


def test_tarball_wellformed_passes():
    with _open_tar(_make_tarball_bytes("1.0.1")) as tf:
        updates.validate_tarball_members(tf, "boron-1.0.1", 10_000_000)  # no raise


def test_extract_staged_rejects_traversal_and_stages_good(update_env, monkeypatch, tmp_path):
    # Hostile archive at the extraction step (not just the pre-validator).
    evil = tarfile.TarInfo("boron-1.0.1/../pwn")
    bad = tmp_path / "bad.tar.gz"
    bad.write_bytes(_tar_with_member(evil, b"boom"))
    job_id = _make_job()
    with pytest.raises(updates._StepFailed):
        updates._extract_staged(job_id, str(bad), "1.0.1")
    assert _get_job(job_id)["status"] == "failed"
    assert not (update_env["root"] / "pwn").exists()
    assert not (update_env["root"] .parent / "pwn").exists()

    good = tmp_path / "good.tar.gz"
    good.write_bytes(_make_tarball_bytes("1.0.1"))
    job2 = _make_job()
    target = updates._extract_staged(job2, str(good), "1.0.1")
    assert target == str(update_env["root"] / "boron-1.0.1")
    assert (update_env["root"] / "boron-1.0.1" / "version.py").exists()
    # Staging never touches the live tree.
    assert os.path.realpath(update_env["live"]) == str(update_env["live_target"])


def test_extract_refuses_to_overwrite_live_target(update_env, tmp_path):
    # Point the live symlink at boron-1.0.1, then try updating "to" it.
    live = update_env["live"]
    target = update_env["root"] / "boron-1.0.1"
    target.mkdir()
    live.unlink()
    live.symlink_to(target)
    good = tmp_path / "good.tar.gz"
    good.write_bytes(_make_tarball_bytes("1.0.1"))
    job_id = _make_job()
    with pytest.raises(updates._StepFailed):
        updates._extract_staged(job_id, str(good), "1.0.1")
    assert "live install" in _get_job(job_id)["error"]


# --- job guards -----------------------------------------------------------------------


def test_start_update_requires_configured_repo(isolated_db, monkeypatch):
    monkeypatch.setattr(settings, "update_github_repo", "")
    with pytest.raises(ValidationError, match="not configured"):
        updates.start_update({"initiated_by": "admin"})


def test_start_update_refuses_non_newer_target(isolated_db, monkeypatch):
    _mock_github(monkeypatch, version=updates.BORON_VERSION)
    with pytest.raises(ValidationError, match="not newer"):
        updates.start_update({"initiated_by": "admin"})


def test_start_update_refuses_concurrent_job(isolated_db, monkeypatch):
    _mock_github(monkeypatch, version="99.0.0")
    _make_job(status="running", to_version="99.0.0")
    with pytest.raises(ValidationError, match="already"):
        updates.start_update({"initiated_by": "admin"})


def test_start_update_queues_job(isolated_db, monkeypatch):
    _mock_github(monkeypatch, version="99.0.0")
    # Keep the worker from actually running the pipeline.
    monkeypatch.setattr(updates._executor, "submit", lambda *a, **k: None)
    out = updates.start_update({"initiated_by": "admin"})
    assert out["kind"] == "update" and out["status"] == "pending"
    assert out["from_version"] == updates.BORON_VERSION
    assert out["to_version"] == "99.0.0"
    assert out["initiated_by"] == "admin"


# --- rollback -------------------------------------------------------------------------


def test_rollback_candidate_none_without_history(update_env):
    assert updates.rollback_candidate() is None
    with pytest.raises(ValidationError, match="no rollback available"):
        updates.start_rollback({"initiated_by": "admin"})


def test_rollback_candidate_found_and_job_queued(update_env, monkeypatch):
    root, live = update_env["root"], update_env["live"]
    new_dir = root / "boron-1.0.1"
    new_dir.mkdir()
    live.unlink()
    live.symlink_to(new_dir)
    _make_job(status="completed", to_version="1.0.1",
              old_dir=str(update_env["live_target"]), new_dir=str(new_dir))

    cand = updates.rollback_candidate()
    assert cand and cand["to_version"] == "1.0.0"
    assert cand["old_dir"] == str(update_env["live_target"])

    monkeypatch.setattr(updates._executor, "submit", lambda *a, **k: None)
    out = updates.start_rollback({"initiated_by": "admin"})
    assert out["kind"] == "rollback"
    assert out["to_version"] == "1.0.0" and out["from_version"] == "1.0.1"


def test_rollback_candidate_gone_when_old_dir_pruned(update_env):
    root, live = update_env["root"], update_env["live"]
    new_dir = root / "boron-1.0.1"
    new_dir.mkdir()
    live.unlink()
    live.symlink_to(new_dir)
    _make_job(status="completed", to_version="1.0.1",
              old_dir=str(root / "boron-0.9.0"),  # doesn't exist
              new_dir=str(new_dir))
    assert updates.rollback_candidate() is None


# --- history / status / log --------------------------------------------------------------


def test_history_newest_first_with_duration(isolated_db):
    from shared.db import write_session

    a = _make_job(status="completed")
    with write_session() as session:
        job = session.get(UpdateJob, a)
        job.completed_at = job.started_at
    b = _make_job(status="failed", to_version="1.0.2")
    out = updates.get_history({})
    assert [j["id"] for j in out["jobs"]] == [b, a]
    assert out["jobs"][1]["duration_seconds"] == 0.0


def test_status_shape(update_env, monkeypatch):
    _mock_github(monkeypatch, version="99.0.0")
    out = updates.get_status({})
    assert out["update_available"] is True
    assert out["active_job"] is None
    assert out["rollback_available"] is False
    assert out["symlink_layout"] is True
    assert out["live_target"] == str(update_env["live_target"])


def test_get_log_returns_job_and_file_tail(update_env, monkeypatch):
    job_id = _make_job()
    updates._step(job_id, "download", "ok", "1234 bytes")
    out = updates.get_log({"job_id": job_id})
    assert out["job"]["id"] == job_id
    assert out["job"]["steps"][-1]["step"] == "download"
    assert any(r.get("step") == "download" for r in out["log"])


# --- notify + cleanup ---------------------------------------------------------------------


def test_notify_emails_once_per_release(update_env, monkeypatch):
    from daemon import monitoring, notifications
    from shared.db import write_session

    _mock_github(monkeypatch, version="99.0.0")
    with write_session() as session:
        notifications._get_settings(session).sender_address = "panel@example.com"
        monitoring._get_settings(session).admin_email = "admin@example.com"

    sent = []
    monkeypatch.setattr(notifications, "_send_email",
                        lambda s, r, subj, body: sent.append((r, subj)))
    out = updates.notify_if_update_available()
    assert out["notified"] is True
    assert sent and "99.0.0" in sent[0][1]

    out2 = updates.notify_if_update_available()
    assert out2["notified"] is False  # deduped per release
    assert len(sent) == 1


def test_notify_skips_without_recipient(update_env, monkeypatch):
    from daemon import notifications

    _mock_github(monkeypatch, version="99.0.0")
    sent = []
    monkeypatch.setattr(notifications, "_send_email",
                        lambda *a: sent.append(a))
    out = updates.notify_if_update_available()
    assert out["notified"] is False and not sent


def test_cleanup_never_touches_neighbours_or_live(update_env, monkeypatch):
    root = update_env["root"]
    old_time = time.time() - 10 * 86400

    prunable = root / "boron-0.9.0"
    prunable.mkdir()
    os.utime(prunable, (old_time, old_time))

    nodejs = root / "boron-nodejs"   # the real /opt neighbour -- NEVER touched
    nodejs.mkdir()
    os.utime(nodejs, (old_time, old_time))
    snapshot = root / "boron.pre-filebrowser"
    snapshot.mkdir()
    os.utime(snapshot, (old_time, old_time))

    recent = root / "boron-1.0.2"
    recent.mkdir()  # fresh mtime -- inside the rollback window

    live_target = update_env["live_target"]  # boron-1.0.0, old but live
    os.utime(live_target, (old_time, old_time))

    out = updates.cleanup_old_versions()
    assert out["removed"] == ["boron-0.9.0"]
    assert not prunable.exists()
    assert nodejs.exists() and snapshot.exists() and recent.exists() and live_target.exists()
    assert sorted(out["kept"]) == ["boron-1.0.0", "boron-1.0.2"]


# --- full pipeline stitch (download -> checksum -> extract -> handoff) ---------------


def test_full_update_job_pipeline(update_env, monkeypatch):
    """_run_update_job end-to-end: real (mocked-transport) download, real
    checksum, real staged extraction; subprocess-y steps (pytest/venv/pip/
    migrations/systemd-run) recorded via a fake `run`; ends in `finalizing`
    with the handoff argv pointing the finalizer at the right dirs."""
    _mock_github(monkeypatch, version="1.0.1")

    ran = []

    class FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(updates, "run", lambda argv, **kw: (ran.append(argv), FakeProc())[1])
    # The live target needs a venv python for the preflight existence check.
    venv_py = update_env["live_target"] / ".venv" / "bin"
    venv_py.mkdir(parents=True)
    (venv_py / "python").write_text("")
    # The finalizer copy lands next to the control-plane DB -- which
    # isolated_db already points at tmp, so nothing touches /var/lib.
    staged_copies = Path(settings.db_path).parent

    out = updates.start_update({"initiated_by": "admin", "to_version": "1.0.1"})
    job_id = out["id"]
    updates._executor.submit(lambda: None).result()  # drain the single worker

    job = _get_job(job_id)
    assert job["status"] == "finalizing", job
    steps = {s["step"]: s["status"] for s in job["steps"]}
    assert steps["preflight"] == "ok"
    assert steps["backup"] == "ok"
    assert steps["download"] == "ok"
    assert steps["checksum"] == "ok"
    assert steps["extract"] == "ok"
    assert steps["venv"] == "ok"
    assert steps["migrate"] == "ok"
    assert job["old_dir"] == str(update_env["live_target"])
    assert job["new_dir"] == str(update_env["root"] / "boron-1.0.1")

    # The staged tree really exists and carries the new version.
    assert (update_env["root"] / "boron-1.0.1" / "version.py").read_text() \
        == 'BORON_VERSION = "1.0.1"\n'
    # Backup ran before anything else touched disk.
    backups = list((update_env["tmp"] / "backups").glob("pre-update-1.0.1-*"))
    assert len(backups) == 1
    assert (backups[0] / "boron.db").exists()

    # The handoff argv: systemd-run + the finalizer with matching dirs.
    handoff = ran[-1]
    assert handoff[0] == "systemd-run"
    assert "--mode" in handoff and handoff[handoff.index("--mode") + 1] == "update"
    assert handoff[handoff.index("--new-dir") + 1] == str(update_env["root"] / "boron-1.0.1")
    assert handoff[handoff.index("--old-dir") + 1] == str(update_env["live_target"])
    # The finalizer copy was staged.
    assert (staged_copies / f"update-finalize-{job_id}.py").exists()


def test_cleanup_protects_recent_rollback_target_despite_old_mtime(update_env):
    """Regression: the first-ever update CONVERTS the months-old
    /opt/boron dir into the rollback target -- its mtime predates the
    update, so an mtime-only cleanup would prune it the same night and void
    the 3-day rollback window. Dirs referenced by a recently-completed job
    must survive regardless of mtime; once the job ages out of the window,
    normal pruning applies."""
    from shared.db import write_session

    root = update_env["root"]
    old_time = time.time() - 300 * 86400  # "converted from a months-old live dir"

    rollback_target = root / "boron-0.9.9"
    rollback_target.mkdir()
    os.utime(rollback_target, (old_time, old_time))

    job_id = _make_job(status="completed", to_version="1.0.0",
                       old_dir=str(rollback_target), new_dir=str(update_env["live_target"]))
    with write_session() as session:
        job = session.get(UpdateJob, job_id)
        job.completed_at = job.started_at  # completed just now

    out = updates.cleanup_old_versions()
    assert rollback_target.exists()
    assert "boron-0.9.9" in out["kept"]

    # Age the job past the retention window -> the dir becomes prunable.
    import datetime as dtm
    with write_session() as session:
        job = session.get(UpdateJob, job_id)
        job.completed_at = job.started_at - dtm.timedelta(days=10)
    out2 = updates.cleanup_old_versions()
    assert not rollback_target.exists()
    assert "boron-0.9.9" in out2["removed"]


def test_stale_finalizing_job_expires_and_unblocks(update_env, monkeypatch):
    """Regression: a finalizer that died without reporting (kill -9 etc.)
    must not block updates/rollbacks forever. A 'finalizing' job whose last
    step is older than STALE_FINALIZING_SECONDS is auto-failed; a fresh one
    keeps blocking."""
    import datetime as dtm

    from shared.db import write_session

    _mock_github(monkeypatch, version="99.0.0")
    job_id = _make_job(status="finalizing")
    stale_at = (updates.utcnow() - dtm.timedelta(seconds=updates.STALE_FINALIZING_SECONDS + 60)).isoformat()
    with write_session() as session:
        job = session.get(UpdateJob, job_id)
        job.steps = [{"step": "finalize", "status": "running", "detail": "", "at": stale_at}]

    # The stale job is expired on read and no longer blocks a new update.
    monkeypatch.setattr(updates._executor, "submit", lambda *a, **k: None)
    out = updates.start_update({"initiated_by": "admin"})
    assert out["status"] == "pending"
    expired = _get_job(job_id)
    assert expired["status"] == "failed"
    assert "never reported back" in expired["error"]

    # A FRESH finalizing job still blocks.
    fresh_id = _make_job(status="finalizing", to_version="99.0.1")
    with write_session() as session:
        job = session.get(UpdateJob, fresh_id)
        job.steps = [{"step": "finalize", "status": "running", "detail": "",
                      "at": updates.utcnow().isoformat()}]
    # (the job queued above is pending -- complete it so only the fresh
    # finalizing one is active)
    with write_session() as session:
        session.get(UpdateJob, out["id"]).status = "failed"
    with pytest.raises(ValidationError, match="already finalizing"):
        updates.start_update({"initiated_by": "admin"})
    assert _get_job(fresh_id)["status"] == "finalizing"
