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

    def check_api(self) -> bool:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # the panel's own self-signed cert
        try:
            with urllib.request.urlopen(self.args.api_health_url, context=ctx, timeout=5) as resp:
                return resp.status == 200
        except (OSError, ValueError):
            return False

    def check_daemon(self) -> bool:
        """One real RPC round trip: 4-byte BE length + JSON, op update.status.
        ANY well-formed reply frame (ok:true or ok:false) proves the daemon
        is serving -- a rolled-back-to version may not know the op."""
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
                    "op": "update.status",
                    "params": {"_actor": "update-finalizer", "_role": "admin", "_ip": None},
                    "request_id": f"finalize-{self.args.job_id}",
                }).encode()
                sock.sendall(struct.pack(">I", len(payload)) + payload)
                header = self._recv_exact(sock, 4)
                (length,) = struct.unpack(">I", header)
                if length > 16 * 1024 * 1024:
                    return False
                body = json.loads(self._recv_exact(sock, length).decode())
                return isinstance(body, dict) and "ok" in body
        except (OSError, ValueError, KeyError):
            return False
        finally:
            if os.geteuid() != original_uid:
                os.seteuid(original_uid)
            if os.getegid() != original_gid:
                os.setegid(original_gid)

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
            if a.convert_live_dir:
                self.convert_live_dir()
                self.log("swap", "ok", f"converted {a.live} to symlink layout -> {a.new_dir}")
            else:
                if not os.path.islink(a.live):
                    raise RuntimeError(f"{a.live} is not a symlink (and --convert-live-dir not given)")
                self.swap_symlink(a.new_dir)
                self.log("swap", "ok", f"{a.live} -> {a.new_dir}")
            self.swapped = True

            restarts_ok = self.restart_services()
            healthy = self.health_check() if restarts_ok else False
            if restarts_ok and healthy:
                self.log("healthcheck", "ok", "api + daemon responding")
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
                return 0
            return self.fail_and_swap_back("post-swap health check failed")
        except Exception as exc:  # noqa: BLE001 -- last line of defense
            return self.fail_and_swap_back(f"finalizer error: {exc}")

    def fail_and_swap_back(self, reason: str) -> int:
        a = self.args
        self.log("rollback", "running", reason)
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
        self.finish_job("failed", error=reason, rolled_back=self.swapped and swap_back_ok)
        self.alert_admin(
            f"[Boron] {'Rollback' if a.mode == 'rollback' else 'Update'} FAILED"
            + ("" if swap_back_ok else " -- MANUAL INTERVENTION REQUIRED"),
            f"Update job {a.job_id} failed: {reason}\n\n"
            + (f"The panel was automatically swapped back to {a.old_dir} and restarted.\n"
               if swap_back_ok else
               f"Swapping back to {a.old_dir} ALSO failed -- the panel may be down. "
               f"On the server: ln -sfn {a.old_dir} {a.live} && "
               f"systemctl restart {a.units.replace(',', ' ')}\n")
            + f"Details: /var/log/boron/updates.log (job {a.job_id}).\n",
        )
        return 1 if swap_back_ok else 2


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
