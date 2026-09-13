"""scripts/update_finalize.py -- REAL end-to-end runs in a /tmp sandbox.

This is the goal's "rollback verified in test environment" item: the actual
finalizer script (a tmp COPY -- it deletes itself on success, exactly like
the production copy under /var/lib/boron) performs a real atomic
symlink swap on real directories, calls a fake systemctl whose invocations
we assert, health-checks against a real local HTTP server and a real Unix
socket speaking the length-prefixed RPC framing, and writes terminal job
state that we read back through the daemon's own SQLAlchemy ORM (proving
the datetime format interoperates). The live /opt/boron is never
involved.
"""
from __future__ import annotations

import http.server
import json
import os
import pwd
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from shared.config import settings
from shared.db import write_session
from shared.models import UpdateJob

REPO_ROOT = Path(__file__).resolve().parent.parent
FINALIZER_SRC = REPO_ROOT / "scripts" / "update_finalize.py"


# --- sandbox helpers -----------------------------------------------------------


@pytest.fixture
def sandbox(isolated_db):
    """Short-pathed /tmp sandbox (unix socket paths are length-limited, and
    pytest's tmp_path nests deep)."""
    base = Path(tempfile.mkdtemp(prefix="fhfin-", dir="/tmp"))
    try:
        root = base / "opt"
        root.mkdir()
        old = root / "boron-1.0.0"
        old.mkdir()
        (old / "marker").write_text("old-version")
        new = root / "boron-1.0.1"
        new.mkdir()
        (new / "marker").write_text("new-version")
        live = root / "boron"
        live.symlink_to(old)

        # Fake systemctl: append argv to a log, exit 0.
        systemctl_log = base / "systemctl.log"
        systemctl = base / "systemctl"
        systemctl.write_text(
            "#!/bin/bash\n"
            f"echo \"$@\" >> {systemctl_log}\n"
            "exit 0\n"
        )
        systemctl.chmod(0o755)

        # The finalizer must run from a copy -- it unlinks itself on success.
        script = base / "update_finalize.py"
        shutil.copyfile(FINALIZER_SRC, script)

        yield {
            "base": base, "root": root, "old": old, "new": new, "live": live,
            "script": script, "systemctl": systemctl, "systemctl_log": systemctl_log,
            "log": base / "updates.log", "rpc_socket": str(base / "provisiond.sock"),
        }
    finally:
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def health_servers(sandbox):
    """A real HTTP /healthz endpoint + a real Unix socket that answers one
    length-prefixed JSON frame per connection."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *a):  # quiet
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    stop = threading.Event()

    def rpc_server():
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sandbox["rpc_socket"])
        os.chmod(sandbox["rpc_socket"], 0o666)
        srv.listen(4)
        srv.settimeout(0.5)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with conn:
                try:
                    _, uid, _ = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    sandbox.setdefault("rpc_peer_uids", []).append(uid)
                    if "expected_rpc_uid" in sandbox and uid != sandbox["expected_rpc_uid"]:
                        continue
                    header = conn.recv(4)
                    if len(header) < 4:
                        continue
                    (length,) = struct.unpack(">I", header)
                    conn.recv(length)  # drain the request
                    payload = json.dumps({"ok": True, "result": {}}).encode()
                    conn.sendall(struct.pack(">I", len(payload)) + payload)
                except OSError:
                    pass
        srv.close()

    t = threading.Thread(target=rpc_server, daemon=True)
    t.start()
    yield {"api_url": f"http://127.0.0.1:{httpd.server_address[1]}/healthz"}
    stop.set()
    httpd.shutdown()
    t.join(timeout=2)


def _make_job(kind="update", status="finalizing", old_dir="", new_dir="") -> int:
    with write_session() as session:
        job = UpdateJob(kind=kind, status=status, from_version="1.0.0",
                        to_version="1.0.1", initiated_by="tester",
                        old_dir=old_dir, new_dir=new_dir)
        session.add(job)
        session.flush()
        return job.id


def _run_finalizer(sandbox, job_id, *, mode="update", api_url, health_timeout=20,
                   convert=False, old=None, new=None, rpc_user=None):
    argv = [
        sys.executable, str(sandbox["script"]),
        "--job-id", str(job_id),
        "--db", settings.db_path,
        "--live", str(sandbox["live"]),
        "--new-dir", str(new or sandbox["new"]),
        "--old-dir", str(old or sandbox["old"]),
        "--mode", mode,
        "--log", str(sandbox["log"]),
        "--api-health-url", api_url,
        "--rpc-socket", sandbox["rpc_socket"],
        "--rpc-user", rpc_user or pwd.getpwuid(os.geteuid()).pw_name,
        "--systemctl-bin", str(sandbox["systemctl"]),
        "--health-timeout", str(health_timeout),
    ]
    if convert:
        argv.append("--convert-live-dir")
    return subprocess.run(argv, capture_output=True, text=True, timeout=120)


def _read_job(job_id) -> UpdateJob:
    with write_session() as session:
        job = session.get(UpdateJob, job_id)
        session.expunge(job)
        return job


# --- the tests --------------------------------------------------------------------


def test_update_success_swaps_restarts_and_completes(sandbox, health_servers):
    job_id = _make_job(old_dir=str(sandbox["old"]), new_dir=str(sandbox["new"]))
    proc = _run_finalizer(sandbox, job_id, api_url=health_servers["api_url"])
    assert proc.returncode == 0, proc.stdout + proc.stderr

    # Atomic swap happened: live now points at the new version.
    assert os.readlink(sandbox["live"]) == str(sandbox["new"])
    assert (sandbox["live"] / "marker").read_text() == "new-version"

    # Both panel units (and nothing else) were restarted, daemon first.
    restarts = sandbox["systemctl_log"].read_text().strip().splitlines()
    assert restarts == ["restart boron-provisiond", "restart boron-api"]

    # Terminal job state, read back through the daemon's own ORM -- this is
    # the datetime-format interop check as much as a status check.
    job = _read_job(job_id)
    assert job.status == "completed"
    assert job.rolled_back is False
    assert job.completed_at is not None
    steps = [(s["step"], s["status"]) for s in job.steps]
    assert ("swap", "ok") in steps and ("healthcheck", "ok") in steps

    # updates.log carries the same trail; the success path self-deletes the copy.
    log_lines = [json.loads(l) for l in sandbox["log"].read_text().splitlines()]
    assert any(r["step"] == "finalize" and r["status"] == "ok" for r in log_lines)
    assert not sandbox["script"].exists()


def test_update_failure_swaps_back_and_marks_rolled_back(sandbox):
    """No health servers at all -> the health check must fail -> the
    finalizer must swap back to the old version and record failed+rolled_back.
    This is the automatic-rollback path (goal 4j) exercised for real."""
    job_id = _make_job(old_dir=str(sandbox["old"]), new_dir=str(sandbox["new"]))
    proc = _run_finalizer(
        sandbox, job_id, health_timeout=3,
        api_url="http://127.0.0.1:1/healthz",  # nothing listens on port 1
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr

    # Swapped back: live points at the OLD version again.
    assert os.readlink(sandbox["live"]) == str(sandbox["old"])
    assert (sandbox["live"] / "marker").read_text() == "old-version"

    # Restarted twice: once into the new version, once back out of it.
    restarts = sandbox["systemctl_log"].read_text().strip().splitlines()
    assert restarts == ["restart boron-provisiond", "restart boron-api"] * 2

    job = _read_job(job_id)
    assert job.status == "failed"
    assert job.rolled_back is True
    assert "health check failed" in job.error
    steps = [(s["step"], s["status"]) for s in job.steps]
    assert ("healthcheck", "failed") in steps
    assert ("rollback", "ok") in steps
    # Failure path keeps the script copy for forensics.
    assert sandbox["script"].exists()


def test_first_update_converts_real_dir_to_symlink_layout(sandbox, health_servers):
    """The live install starts as a plain directory (today's production
    layout); the first update must convert it: mv aside + symlink."""
    live = sandbox["live"]
    live.unlink()  # replace the fixture's symlink with a REAL directory
    real = sandbox["root"] / "boron"
    real.mkdir()
    (real / "marker").write_text("legacy-live")
    converted_old = sandbox["root"] / "boron-1.0.0-legacy"

    job_id = _make_job(old_dir=str(converted_old), new_dir=str(sandbox["new"]))
    proc = _run_finalizer(sandbox, job_id, api_url=health_servers["api_url"],
                          convert=True, old=converted_old)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    assert os.path.islink(live)
    assert os.readlink(live) == str(sandbox["new"])
    # The original live tree was preserved at the converted location.
    assert (converted_old / "marker").read_text() == "legacy-live"
    assert _read_job(job_id).status == "completed"


def test_rollback_mode_swaps_to_previous_version(sandbox, health_servers):
    """Manual rollback (goal 5): live points at 1.0.1; roll back to 1.0.0."""
    live = sandbox["live"]
    live.unlink()
    live.symlink_to(sandbox["new"])  # currently on the new version

    job_id = _make_job(kind="rollback", old_dir=str(sandbox["new"]),
                       new_dir=str(sandbox["old"]))
    proc = _run_finalizer(sandbox, job_id, mode="rollback",
                          api_url=health_servers["api_url"],
                          old=sandbox["new"], new=sandbox["old"])
    assert proc.returncode == 0, proc.stdout + proc.stderr

    assert os.readlink(live) == str(sandbox["old"])
    assert (live / "marker").read_text() == "old-version"
    job = _read_job(job_id)
    assert job.status == "completed" and job.kind == "rollback"


def test_swap_is_refused_on_non_symlink_without_convert_flag(sandbox, health_servers):
    live = sandbox["live"]
    live.unlink()
    real = sandbox["root"] / "boron"
    real.mkdir()
    job_id = _make_job(old_dir=str(sandbox["old"]), new_dir=str(sandbox["new"]))
    proc = _run_finalizer(sandbox, job_id, api_url=health_servers["api_url"],
                          health_timeout=3)
    # Nothing was swapped, so nothing is swapped back; job fails cleanly.
    assert proc.returncode in (1, 2)
    assert real.is_dir() and not os.path.islink(real)
    job = _read_job(job_id)
    assert job.status == "failed"
    assert "not a symlink" in job.error


@pytest.mark.skipif(os.geteuid() != 0, reason="credential switching requires root")
def test_health_probe_uses_api_identity_then_restores_root(sandbox, health_servers):
    account = pwd.getpwnam("nobody")
    sandbox["base"].chmod(0o711)
    sandbox["expected_rpc_uid"] = account.pw_uid
    job_id = _make_job()
    proc = _run_finalizer(sandbox, job_id, api_url=health_servers["api_url"], rpc_user="nobody")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert sandbox["rpc_peer_uids"] == [account.pw_uid]
    assert _read_job(job_id).status == "completed"  # root-only DB write after probe
