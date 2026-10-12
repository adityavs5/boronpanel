#!/usr/bin/python3
"""Boron update finalizer -- the detached last mile of a panel update.

Launched by borond (daemon/updates.py) as a transient systemd unit via
systemd-run, from a COPY under /var/lib/boron, interpreted by the SYSTEM
python3. Both of those are deliberate:

- Detached, because this script restarts boron-provisiond -- the very
  process that launched it. An in-daemon implementation would be killed
  mid-swap.
- A copy on the system interpreter with ONLY the standard library, because
  this is the machinery that rolls a broken update BACK. It must keep
  working when the new tree's venv is broken, when /opt/boron points
  somewhere half-dead, and while either panel service is down. It imports
  nothing from the boron codebase.

What it does (goal steps g-j):
  1. Atomically swap the /opt/boron symlink to the new version dir
     (converting the legacy real-directory layout to a symlink on the
     first-ever update).
  2. Restart boron-provisiond + boron-api.
  3. Health-check both: HTTPS GET /healthz on the API, and a real
     length-prefixed JSON RPC round trip on the daemon socket (any
     well-formed reply proves the daemon event loop is serving -- the
     rolled-back-to version may predate the update.* ops).
  4. On failure: swap back, restart again, re-health-check, mark the job
     failed+rolled_back, email the admin. Exit 1 (rolled back) / 2 (swap
     back also failed -- operator intervention needed).
  5. On success: mark the job completed. OLS and hosted sites are never
     touched; panel downtime is the two service restarts (~10-30s).

State is written directly to the update_jobs row via sqlite3 (datetime text
format matching what SQLAlchemy's sqlite dialect writes, so the daemon's ORM
reads it back transparently) and mirrored as JSON lines to updates.log.
Nothing secret is ever logged or emailed: paths, versions, unit names and
exit codes only.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pwd
import shutil
import socket
import sqlite3
import ssl
import struct
import subprocess
import sys
import time
import urllib.request


def now_db() -> str:
    """Timestamp string in the exact format SQLAlchemy's sqlite dialect
    writes for timezone-aware DateTime columns (str() of an aware UTC
    datetime), so ORM reads parse it like any daemon-written row."""
    return str(datetime.datetime.now(datetime.timezone.utc))


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class Finalizer:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.swapped = False
        self._credential = self._read_credential()

    def _read_credential(self) -> str:
        try:
            with open(self.args.credential_file, encoding="utf-8") as stream:
                value = stream.read().strip()
        except OSError as exc:
            raise RuntimeError("update finalizer credential is unavailable") from exc
        if not value or len(value) > 4096:
            raise RuntimeError("update finalizer credential is invalid")
        return value

    def cleanup_private_state(self) -> None:
        try:
            os.unlink(self.args.credential_file)
        except OSError:
            pass
        if self.args.mode == "rollback" and self.args.recovery_config_backup:
            try:
                shutil.rmtree(self.args.recovery_config_backup)
            except OSError:
                pass

    def restore_config(self, source: str, step: str, ownership_source: str = "") -> bool:
        """Restore an exact root-owned config snapshot before code rollback.

        A newer daemon may persist a config schema marker or new keys that an
        N-1 release cannot parse.  The update backup is therefore part of the
        rollback transaction, just like the application symlink.  A manual
        rollback also has a just-in-time rescue snapshot so a failed rollback
        can return to the newer version without losing its configuration.
        """
        if not source:
            self.log(step, "failed", "configuration snapshot was not supplied")
            return False
        source = os.path.realpath(source)
        target = os.path.abspath(self.args.config_dir)
        try:
            source_stat = os.lstat(source)
            if not os.path.isdir(source) or os.path.islink(source):
                raise RuntimeError("configuration snapshot is not a regular directory")
            if source_stat.st_uid != 0 or source_stat.st_mode & 0o022:
                raise RuntimeError("configuration snapshot ownership or mode is unsafe")
            parent = os.path.dirname(target)
            temporary = os.path.join(parent, f".{os.path.basename(target)}.restore.{os.getpid()}")
            shutil.rmtree(temporary, ignore_errors=True)
            shutil.copytree(source, temporary, symlinks=True)
            metadata_root = (
                os.path.realpath(ownership_source)
                if ownership_source and os.path.isdir(ownership_source)
                and not os.path.islink(ownership_source)
                else source
            )
            for source_dir, dirs, files in os.walk(source, followlinks=False):
                relative = os.path.relpath(source_dir, source)
                target_dir = temporary if relative == "." else os.path.join(temporary, relative)
                for source_path, target_path in [
                    (source_dir, target_dir),
                    *((os.path.join(source_dir, name), os.path.join(target_dir, name))
                      for name in dirs + files),
                ]:
                    metadata_path = (
                        metadata_root if source_path == source
                        else os.path.join(metadata_root, os.path.relpath(source_path, source))
                    )
                    if not os.path.lexists(metadata_path):
                        metadata_path = source_path
                    metadata = os.lstat(metadata_path)
                    os.chown(target_path, metadata.st_uid, metadata.st_gid, follow_symlinks=False)
            previous = target + f".previous.{os.getpid()}"
            os.rename(target, previous)
            try:
                os.rename(temporary, target)
            except OSError:
                os.rename(previous, target)
                raise
            shutil.rmtree(previous)
            self.log(step, "ok", "protected configuration snapshot restored")
            return True
        except (OSError, RuntimeError) as exc:
            self.log(step, "failed", type(exc).__name__)
            return False

    # --- logging -----------------------------------------------------------

    def log(self, step: str, status: str, detail: str = "") -> None:
        record = {"at": now_iso(), "job_id": self.args.job_id, "source": "finalizer",
                  "step": step, "status": status, "detail": detail}
        line = json.dumps(record, separators=(",", ":"))
        print(line, flush=True)  # journal via the transient unit
        try:
            with open(self.args.log, "a") as f:
                f.write(line + "\n")
        except OSError:
            pass
        self._append_job_step(step, status, detail)

    # --- job row (direct sqlite, best-effort) -------------------------------

    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.args.db, timeout=15)
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def _append_job_step(self, step: str, status: str, detail: str) -> None:
        try:
            with self._db() as conn:
                row = conn.execute("SELECT steps FROM update_jobs WHERE id=?",
                                   (self.args.job_id,)).fetchone()
                steps = json.loads(row[0]) if row and row[0] else []
                steps.append({"step": step, "status": status, "detail": detail, "at": now_iso()})
                conn.execute("UPDATE update_jobs SET steps=?, progress_message=? WHERE id=?",
                             (json.dumps(steps), f"{step}: {status}"[:250], self.args.job_id))
        except (sqlite3.Error, ValueError, OSError):
            pass

    def finish_job(self, status: str, error: str | None = None, rolled_back: bool = False) -> None:
        try:
            with self._db() as conn:
                conn.execute(
                    "UPDATE update_jobs SET status=?, error=?, rolled_back=?, "
                    "completed_at=?, progress_message=? WHERE id=?",
                    (status, error[:4000] if error else None, 1 if rolled_back else 0,
                     now_db(), status, self.args.job_id),
                )
        except (sqlite3.Error, OSError) as exc:
            print(f"WARNING: could not write terminal job state: {exc}", file=sys.stderr)

    # --- the dangerous window ------------------------------------------------

    def swap_symlink(self, target: str) -> None:
        """Atomic: build a temp symlink next to the live path, rename over it
        (rename replaces symlinks atomically on the same filesystem)."""
        live = self.args.live
        tmp = f"{live}.swap.{os.getpid()}"
        try:
            os.symlink(target, tmp)
            os.rename(tmp, live)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def convert_live_dir(self) -> None:
        """First-ever update: /opt/boron is a plain directory. Move it to
        the versioned old_dir (same filesystem -- a fast rename), then
        symlink. The services keep running through the rename (their cwd and
        open files follow the inode); they restart seconds later anyway."""
        live, old = self.args.live, self.args.old_dir
        if os.path.islink(live):
            raise RuntimeError(f"--convert-live-dir given but {live} is already a symlink")
        if os.path.exists(old):
            raise RuntimeError(f"conversion target {old} already exists")
        os.rename(live, old)
        self.swap_symlink(self.args.new_dir)

    def restart_services(self) -> bool:
        ok = True
        for unit in self.args.units.split(","):
            unit = unit.strip()
            proc = subprocess.run([self.args.systemctl_bin, "restart", unit],
                                  capture_output=True, text=True, timeout=90)
            if proc.returncode != 0:
                self.log("restart", "failed", f"{unit}: rc={proc.returncode} {proc.stderr.strip()[:300]}")
                ok = False
            else:
                self.log("restart", "ok", unit)
        return ok

    def stop_services_for_rollback(self) -> None:
        """Quiesce new code before restoring files and database modes."""
        for unit in self.args.units.split(","):
            unit = unit.strip()
            if not unit:
                continue
            proc = subprocess.run([self.args.systemctl_bin, "stop", unit],
                                  capture_output=True, text=True, timeout=90)
            self.log("rollback-stop", "ok" if proc.returncode == 0 else "failed", unit)

    def run_runtime_migrations(self) -> bool:
        if self.args.mode != "update":
            return True
        script = os.path.join(self.args.new_dir, "scripts", "upgrade_runtime.py")
        python = os.path.join(self.args.new_dir, ".venv", "bin", "python")
        if not os.path.isfile(script):
            self.log("runtime-migration", "skipped", "release has no runtime migration")
            return True
        if not os.path.isfile(python):
            self.log("runtime-migration", "failed", "release interpreter is missing")
            return False
        try:
            proc = subprocess.run(
                [python, script, "--release-metadata", self.args.release_metadata],
                capture_output=True, text=True, timeout=1200,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.log("runtime-migration", "failed", type(exc).__name__)
            return False
        if proc.returncode != 0:
            self.log("runtime-migration", "failed", f"rc={proc.returncode}; inspect the protected system journal")
            return False
        self.log("runtime-migration", "ok", "host integrations reconciled")
        return True

    def control_runtime_migration(self, action: str) -> bool:
        """Commit or compensate the new release's host migration snapshot."""
        if self.args.mode != "update":
            return True
        script = os.path.join(self.args.new_dir, "scripts", "upgrade_runtime.py")
        python = os.path.join(self.args.new_dir, ".venv", "bin", "python")
        if not os.path.isfile(script):
            return True
        if not os.path.isfile(python):
            self.log(f"runtime-{action}", "failed", "release interpreter is missing")
            return False
        try:
            proc = subprocess.run(
                [python, script, f"--{action}", "--release-metadata", self.args.release_metadata],
                capture_output=True, text=True, timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.log(f"runtime-{action}", "failed", type(exc).__name__)
            return False
        if proc.returncode != 0:
            self.log(f"runtime-{action}", "failed", f"rc={proc.returncode}; inspect the protected system journal")
            return False
        self.log(f"runtime-{action}", "ok", "host migration snapshot reconciled")
        return True

    def collect_runtime_changes(self) -> list[dict] | None:
        """Read the release script's bounded, redacted change report."""
        if self.args.mode != "update":
            return []
        script = os.path.join(self.args.new_dir, "scripts", "upgrade_runtime.py")
        python = os.path.join(self.args.new_dir, ".venv", "bin", "python")
        if not os.path.isfile(script):
            return []
        try:
            proc = subprocess.run(
                [python, script, "--report", "--release-metadata", self.args.release_metadata],
                capture_output=True, text=True, timeout=60,
            )
            document = json.loads(proc.stdout) if proc.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return None
        if not isinstance(document, list) or len(document) > 250:
            return None
        # The migration script already limits and redacts every field.  Repeat
        # structural bounds here because this stdlib-only finalizer is the DB
        # write boundary.
        safe = []
        for row in document:
            if not isinstance(row, dict):
                return None
            item = {
                "path": str(row.get("path", ""))[:512],
                "result": str(row.get("result", ""))[:32],
                "before_exists": bool(row.get("before_exists")),
                "after_exists": bool(row.get("after_exists")),
                "before_sha256": str(row.get("before_sha256") or "")[:64] or None,
                "after_sha256": str(row.get("after_sha256") or "")[:64] or None,
            }
            if row.get("diff"):
                item["diff"] = str(row["diff"])[:4096]
            safe.append(item)
        return safe

    def store_runtime_changes(self, changes: list[dict]) -> bool:
        try:
            with self._db() as conn:
                conn.execute(
                    "UPDATE update_jobs SET runtime_changes=? WHERE id=?",
                    (json.dumps(changes, separators=(",", ":")), self.args.job_id),
                )
            return True
        except (sqlite3.Error, OSError, ValueError):
            self.log("runtime-evidence", "failed", "could not persist runtime configuration evidence")
            return False

    def check_api(self) -> bool:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # the panel's own self-signed cert
        try:
            with urllib.request.urlopen(self.args.api_health_url, context=ctx, timeout=5) as resp:
                return resp.status == 200
        except (OSError, ValueError):
            return False

    def rpc_call(self, op: str, *, legacy_rollback_probe: bool = False) -> dict | None:
        """Authenticated helper call, with a narrow legacy rollback probe."""
        original_uid, original_gid = os.geteuid(), os.getegid()
        try:
            # The daemon authenticates SO_PEERCRED and rejects root peers.
            # This finalizer is single-threaded: drop identity for the socket
            # round trip only, then restore it before DB/log/symlink work.
            account = pwd.getpwnam(self.args.rpc_user)
            if account.pw_uid != original_uid:
                os.setegid(account.pw_gid)
                os.seteuid(account.pw_uid)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(5)
                sock.connect(self.args.rpc_socket)
                payload = json.dumps({
                    "op": "update.status" if legacy_rollback_probe else op,
                    "params": {},
                    "credential": None if legacy_rollback_probe else {
                        "type": "update-finalizer", "value": self._credential,
                    },
                    "request_id": f"finalize-{self.args.job_id}",
                    "protocol": {
                        "version": self.args.rpc_protocol_version,
                        "capabilities": ["json-frame-v1", "request-id-v1"],
                    },
                }).encode()
                sock.sendall(struct.pack(">I", len(payload)) + payload)
                header = self._recv_exact(sock, 4)
                (length,) = struct.unpack(">I", header)
                if length > 16 * 1024 * 1024:
                    return None
                body = json.loads(self._recv_exact(sock, length).decode())
                if not isinstance(body, dict) or "ok" not in body:
                    return None
                # N-1 releases before the handshake do not add a protocol
                # envelope.  Accept that only while deliberately rolling
                # back; an update must prove the newly started helper speaks
                # the requested protocol.
                if legacy_rollback_probe and "protocol" not in body:
                    return body
                protocol = body.get("protocol")
                if not isinstance(protocol, dict):
                    return None
                if protocol.get("version") != self.args.rpc_protocol_version:
                    return None
                return body
        except (OSError, ValueError, KeyError):
            return None
        finally:
            if os.geteuid() != original_uid:
                os.seteuid(original_uid)
            if os.getegid() != original_gid:
                os.setegid(original_gid)

    def check_daemon(self) -> bool:
        if self.args.mode == "rollback":
            return self.rpc_call("update.status", legacy_rollback_probe=True) is not None
        body = self.rpc_call("update.finalizer.ping")
        return bool(body and body.get("ok") is True)

    def readiness(self) -> tuple[list[dict], list[dict]] | None:
        if self.args.mode == "rollback":
            return ([], [])
        body = self.rpc_call("update.readiness")
        result = body.get("result") if body and body.get("ok") is True else None
        if not isinstance(result, dict) or not isinstance(result.get("checks"), list):
            return None
        critical, advisory = [], []
        for row in result["checks"]:
            if not isinstance(row, dict):
                continue
            status = str(row.get("status", "unknown"))
            safe = {
                "id": str(row.get("id", ""))[:64],
                "label": str(row.get("label", ""))[:120],
                "status": status[:16],
                "explanation": str(row.get("explanation", ""))[:500],
            }
            if bool(row.get("critical")) and status in {"fail", "unknown"}:
                critical.append(safe)
            elif status != "pass":
                advisory.append(safe)
        return critical, advisory

    def store_advisories(self, advisories: list[dict]) -> None:
        try:
            with self._db() as conn:
                conn.execute(
                    "UPDATE update_jobs SET advisories=? WHERE id=?",
                    (json.dumps(advisories, separators=(",", ":")), self.args.job_id),
                )
        except (sqlite3.Error, OSError):
            self.log("readiness", "failed", "could not persist advisory results")

    def claim_automatic_rollback(self) -> bool:
        try:
            with self._db() as conn:
                cursor = conn.execute(
                    "UPDATE update_jobs SET automatic_rollback_attempted=1 "
                    "WHERE id=? AND automatic_rollback_attempted=0",
                    (self.args.job_id,),
                )
                return cursor.rowcount == 1
        except (sqlite3.Error, OSError):
            return False

    @staticmethod
    def _recv_exact(sock: socket.socket, n: int) -> bytes:
        chunks, remaining = [], n
        while remaining > 0:
            chunk = sock.recv(remaining)
            if not chunk:
                raise ConnectionError("socket closed")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def health_check(self) -> bool:
        deadline = time.monotonic() + self.args.health_timeout
        api_ok = daemon_ok = False
        while time.monotonic() < deadline:
            api_ok = api_ok or self.check_api()
            daemon_ok = daemon_ok or self.check_daemon()
            if api_ok and daemon_ok:
                return True
            time.sleep(2)
        self.log("healthcheck", "failed", f"api_ok={api_ok} daemon_ok={daemon_ok}")
        return False

    # --- alerting -------------------------------------------------------------

    def alert_admin(self, subject: str, body: str) -> None:
        if not self.args.alert_sender or not self.args.alert_recipient:
            return
        try:
            import smtplib
            from email.message import EmailMessage

            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = self.args.alert_sender
            msg["To"] = self.args.alert_recipient
            msg.set_content(body)
            with smtplib.SMTP("127.0.0.1", 25, timeout=10) as smtp:
                smtp.send_message(msg)
        except OSError:
            self.log("alert", "failed", "could not email admin (SMTP unavailable)")

    # --- main flow --------------------------------------------------------------

    def run(self) -> int:
        a = self.args
        self.log("finalize", "running",
                 f"mode={a.mode} live={a.live} new={a.new_dir} old={a.old_dir}")
        try:
            if a.mode == "rollback":
                self.stop_services_for_rollback()
                if not self.restore_config(
                    a.target_config_backup, "config-rollback", a.recovery_config_backup
                ):
                    self.restart_services()
                    self.finish_job("failed", error="rollback configuration could not be restored")
                    self.cleanup_private_state()
                    return 2
            if a.convert_live_dir:
                self.convert_live_dir()
                self.log("swap", "ok", f"converted {a.live} to symlink layout -> {a.new_dir}")
            else:
                if not os.path.islink(a.live):
                    raise RuntimeError(f"{a.live} is not a symlink (and --convert-live-dir not given)")
                self.swap_symlink(a.new_dir)
                self.log("swap", "ok", f"{a.live} -> {a.new_dir}")
            self.swapped = True

            migrated = self.run_runtime_migrations()
            restarts_ok = self.restart_services() if migrated else False
            healthy = self.health_check() if restarts_ok else False
            if restarts_ok and healthy:
                self.log("healthcheck", "ok", "api + daemon responding")
                readiness = self.readiness()
                if readiness is None:
                    return self.fail_and_swap_back("authenticated readiness check did not complete")
                critical, advisory = readiness
                self.store_advisories(advisory)
                if critical:
                    labels = ", ".join(item["label"] or item["id"] for item in critical[:5])
                    self.log("readiness", "failed", f"critical checks failed: {labels}")
                    return self.fail_and_swap_back(f"critical readiness checks failed: {labels}")
                self.log(
                    "readiness", "warning" if advisory else "ok",
                    f"{len(advisory)} advisory check(s) require attention" if advisory
                    else "all critical and advisory checks passed",
                )
                runtime_changes = self.collect_runtime_changes()
                if runtime_changes is None or not self.store_runtime_changes(runtime_changes):
                    return self.fail_and_swap_back("runtime configuration evidence could not be recorded")
                self.log(
                    "runtime-evidence", "ok",
                    f"recorded {len(runtime_changes)} redacted runtime configuration change(s)",
                )
                # Only discard the host-config snapshot after the new
                # application and daemon have both passed health checks.
                if not self.control_runtime_migration("commit"):
                    return self.fail_and_swap_back("runtime migration snapshot commit failed")
                # Start the rollback-retention clock NOW: a first-ever update
                # converted a months-old directory into the rollback target,
                # whose stale mtime would otherwise look prunable to the
                # cleanup cron tonight (the daemon's cleanup also protects
                # job-referenced dirs -- this is the second layer).
                try:
                    os.utime(a.old_dir, None)
                except OSError:
                    pass
                self.finish_job("completed")
                self.log("finalize", "ok",
                         f"{'rollback' if a.mode == 'rollback' else 'update'} applied: now running {a.new_dir}")
                try:
                    os.unlink(os.path.abspath(__file__))
                except OSError:
                    pass
                self.cleanup_private_state()
                return 0
            return self.fail_and_swap_back("post-swap health check failed")
        except Exception as exc:  # noqa: BLE001 -- last line of defense
            return self.fail_and_swap_back(f"finalizer error: {exc}")

    def fail_and_swap_back(self, reason: str) -> int:
        a = self.args
        if not self.claim_automatic_rollback():
            self.log("rollback", "failed", "automatic rollback already attempted; loop stopped")
            self.finish_job("failed", error=reason, rolled_back=False)
            self.cleanup_private_state()
            return 2
        self.log("rollback", "running", reason)
        # The new daemon performs application socket migration during
        # startup. Stop it before restoring unit files and backend_mode rows,
        # otherwise systemd's restart loop can rewrite state mid-rollback.
        self.stop_services_for_rollback()
        runtime_rollback_ok = self.control_runtime_migration("rollback")
        config_rollback_ok = self.restore_config(
            a.recovery_config_backup, "config-recovery"
        )
        swap_back_ok = True
        if self.swapped:
            try:
                self.swap_symlink(a.old_dir)
                self.log("rollback", "ok", f"{a.live} swapped back -> {a.old_dir}")
            except OSError as exc:
                swap_back_ok = False
                self.log("rollback", "failed", f"could not swap back: {exc}")
        if swap_back_ok:
            self.restart_services()
            recovered = self.health_check()
            self.log("rollback", "ok" if recovered else "failed",
                     "panel recovered on previous version" if recovered
                     else "panel NOT healthy after swap-back -- manual intervention required")
        fully_rolled_back = self.swapped and swap_back_ok and runtime_rollback_ok and config_rollback_ok
        self.finish_job("failed", error=reason, rolled_back=fully_rolled_back)
        if swap_back_ok and runtime_rollback_ok and config_rollback_ok:
            recovery_detail = (
                f"The panel was automatically swapped back to {a.old_dir}, "
                "the host runtime configuration was restored, and services were restarted.\n"
            )
        elif not swap_back_ok:
            recovery_detail = (
                f"Swapping back to {a.old_dir} ALSO failed -- the panel may be down. "
                f"On the server: ln -sfn {a.old_dir} {a.live} && "
                f"systemctl restart {a.units.replace(',', ' ')}\n"
            )
        else:
            recovery_detail = (
                f"The panel application was swapped back to {a.old_dir}, but restoring "
                "the previous host runtime configuration failed. Inspect the update log, then run: "
                f"{a.new_dir}/.venv/bin/python {a.new_dir}/scripts/upgrade_runtime.py --rollback\n"
            )
        self.alert_admin(
            f"[Boron] {'Rollback' if a.mode == 'rollback' else 'Update'} FAILED"
            + ("" if swap_back_ok and runtime_rollback_ok else " -- MANUAL INTERVENTION REQUIRED"),
            f"Update job {a.job_id} failed: {reason}\n\n"
            + recovery_detail
            + f"Details: /var/log/boron/updates.log (job {a.job_id}).\n",
        )
        self.cleanup_private_state()
        return 1 if swap_back_ok and runtime_rollback_ok and config_rollback_ok else 2


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--job-id", type=int, required=True)
    p.add_argument("--db", required=True)
    p.add_argument("--live", required=True)
    p.add_argument("--new-dir", required=True)
    p.add_argument("--old-dir", required=True)
    p.add_argument("--mode", choices=("update", "rollback"), required=True)
    p.add_argument("--log", required=True)
    p.add_argument("--api-health-url", required=True)
    p.add_argument("--rpc-socket", required=True)
    p.add_argument("--rpc-protocol-version", required=True)
    p.add_argument("--credential-file", required=True)
    p.add_argument("--release-metadata", required=True)
    p.add_argument("--config-dir", default="/etc/boron")
    p.add_argument("--target-config-backup", default="")
    p.add_argument("--recovery-config-backup", default="")
    p.add_argument("--rpc-user", default="boron-api")
    p.add_argument("--alert-sender", default="")
    p.add_argument("--alert-recipient", default="")
    p.add_argument("--convert-live-dir", action="store_true",
                   help="first-ever update: move the real live dir aside and symlink")
    p.add_argument("--units", default="boron-provisiond,boron-api")
    p.add_argument("--systemctl-bin", default="systemctl")
    p.add_argument("--health-timeout", type=float, default=90.0)
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(Finalizer(parse_args()).run())
