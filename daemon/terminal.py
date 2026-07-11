"""Phase 8 feature 7: web terminal — the daemon (root) side.

Opening a terminal session generates an EPHEMERAL Ed25519 keypair in memory,
injects the PUBLIC key into the account's own `~/.ssh/authorized_keys` (with a
`boron-terminal-<sid>-<epoch>` marker comment), and returns the PRIVATE key
to boron-api over the RPC socket. The private key is NEVER written to disk
here (or anywhere) -- the API holds it in memory for the life of the WebSocket,
connects to sshd on 127.0.0.1 as the account user with it, and discards it.
Closing the session removes the marker line. sshd auth as the account's own uid
(never root) is the isolation boundary; the account gets a real shell exactly
like the SSH-keys feature already grants (Phase 4 f6).

Max 3 concurrent sessions per account (counted from the live authorized_keys
markers, so the limit survives an API restart). Stale markers (a session whose
API-side close never ran, e.g. the daemon was unreachable at close) older than
TERMINAL_KEY_MAX_AGE are reaped on the next open, bounding key leakage.

The authorized_keys parsing/mutation is factored into pure helpers so it's
unit-testable without root or a real ssh daemon.
"""
from __future__ import annotations

import fcntl
import logging
import os
import pwd
import secrets
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account
from shared.validation import validate_username

from daemon import sysops

logger = logging.getLogger("borond.terminal")

MAX_CONCURRENT_SESSIONS = 3
# A session whose close never ran leaves its key behind; reap it after this.
TERMINAL_KEY_MAX_AGE_SECONDS = 12 * 3600
SSH_HOST = "127.0.0.1"
SSH_PORT = 22
_MARKER_PREFIX = "boron-terminal-"
# No forwarding of any kind; a pty is still allowed (we need it) -- deliberately
# NOT `restrict`, which would also disable the pty.
_KEY_OPTIONS = "no-agent-forwarding,no-port-forwarding,no-X11-forwarding"


def _marker(session_id: str, epoch: int) -> str:
    return f"{_MARKER_PREFIX}{session_id}-{epoch}"


def generate_keypair() -> tuple[str, str]:
    """Return (private_key_openssh_pem, public_key_authorized_keys). The
    private key is unencrypted OpenSSH PEM (paramiko.Ed25519Key loads it from a
    string), the public key is `ssh-ed25519 AAAA...`."""
    key = ed25519.Ed25519PrivateKey.generate()
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_openssh = key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode()
    return private_pem, public_openssh


def build_authorized_line(public_openssh: str, session_id: str, epoch: int) -> str:
    return f"{_KEY_OPTIONS} {public_openssh} {_marker(session_id, epoch)}"


def _is_terminal_line(line: str) -> bool:
    return _MARKER_PREFIX in line


def _line_session_id(line: str) -> str | None:
    """Extract the <sid> from a terminal marker's `...-<sid>-<epoch>` comment."""
    idx = line.rfind(_MARKER_PREFIX)
    if idx < 0:
        return None
    marker = line[idx + len(_MARKER_PREFIX):].strip().split()[0]
    parts = marker.rsplit("-", 1)
    return parts[0] if len(parts) == 2 else marker


def _line_epoch(line: str) -> int | None:
    idx = line.rfind(_MARKER_PREFIX)
    if idx < 0:
        return None
    marker = line[idx + len(_MARKER_PREFIX):].strip().split()[0]
    parts = marker.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return int(parts[1])
    return None


def prune_and_count(lines: list[str], now: int) -> tuple[list[str], int]:
    """Drop stale terminal markers (older than TERMINAL_KEY_MAX_AGE) and return
    (kept_lines, active_terminal_count). Non-terminal lines (user SSH keys) are
    always kept untouched."""
    kept: list[str] = []
    active = 0
    for line in lines:
        if not _is_terminal_line(line):
            kept.append(line)
            continue
        epoch = _line_epoch(line)
        if epoch is not None and now - epoch > TERMINAL_KEY_MAX_AGE_SECONDS:
            continue  # stale -> reap
        kept.append(line)
        active += 1
    return kept, active


def remove_session_line(lines: list[str], session_id: str) -> list[str]:
    return [ln for ln in lines if _line_session_id(ln) != session_id]


def _ssh_paths(username: str) -> tuple[str, str, int, int]:
    pw = pwd.getpwnam(username)
    ssh_dir = os.path.join(os.path.realpath(f"{settings.home_base}/{username}"), ".ssh")
    return ssh_dir, os.path.join(ssh_dir, "authorized_keys"), pw.pw_uid, pw.pw_gid


def _read_lines(path: str) -> list[str]:
    if not os.path.isfile(path):
        return []
    with open(path) as f:
        return [ln.rstrip("\n") for ln in f if ln.strip()]


def _write_lines(path: str, lines: list[str], uid: int, gid: int) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    content = ("\n".join(lines) + "\n") if lines else ""
    with open(tmp, "w") as f:
        f.write(content)
    os.chmod(tmp, 0o600)
    os.chown(tmp, uid, gid)
    os.replace(tmp, path)


def _locked(ssh_dir: str):
    """A lock on <ssh_dir>/.boron-terminal.lock serializing authorized_keys
    read-modify-write across concurrent open/close (this module and, harmlessly,
    only this module -- the SSH-keys feature edits distinct lines)."""
    lock_path = os.path.join(ssh_dir, ".boron-terminal.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def open_session(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        if account.status != "active":
            raise RuntimeError(f"cannot open a terminal for an account in status '{account.status}'")

    ssh_dir, path, uid, gid = _ssh_paths(username)
    os.makedirs(ssh_dir, exist_ok=True)
    os.chmod(ssh_dir, 0o700)
    os.chown(ssh_dir, uid, gid)

    now = int(time.time())
    session_id = secrets.token_hex(16)
    private_pem, public_openssh = generate_keypair()

    fd = _locked(ssh_dir)
    try:
        lines = _read_lines(path)
        lines, active = prune_and_count(lines, now)
        if active >= MAX_CONCURRENT_SESSIONS:
            raise RuntimeError(f"maximum of {MAX_CONCURRENT_SESSIONS} concurrent terminal sessions reached for this account")
        lines.append(build_authorized_line(public_openssh, session_id, now))
        _write_lines(path, lines, uid, gid)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    # A shell is required for sshd to yield a terminal (same grant the SSH-keys
    # feature makes). Idempotent.
    shell_upgraded = False
    if sysops.get_shell(username) == sysops.NOLOGIN_SHELL:
        sysops.set_shell(username, sysops.LOGIN_SHELL)
        shell_upgraded = True

    return {
        "session_id": session_id,
        "private_key": private_pem,
        "username": username,
        "host": SSH_HOST,
        "port": SSH_PORT,
        "shell_upgraded": shell_upgraded,
    }


def close_session(params: dict) -> dict:
    username = validate_username(params["username"])
    session_id = params["session_id"]
    ssh_dir, path, uid, gid = _ssh_paths(username)
    if not os.path.isfile(path):
        return {"status": "not_found"}

    fd = _locked(ssh_dir)
    try:
        lines = _read_lines(path)
        remaining = remove_session_line(lines, session_id)
        # Also reap any stale terminal markers while we hold the lock.
        remaining, _ = prune_and_count(remaining, int(time.time()))
        _write_lines(path, remaining, uid, gid)
        has_any_keys = any(ln.strip() and not ln.strip().startswith("#") for ln in remaining)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    # Symmetric with sshkeys.delete_key: if the account now has NO authorized
    # keys at all, revert the shell so a keyless account can't SSH in.
    if not has_any_keys and sysops.get_shell(username) == sysops.LOGIN_SHELL:
        sysops.set_shell(username, sysops.NOLOGIN_SHELL)

    return {"status": "closed", "session_id": session_id}


def list_sessions(params: dict) -> dict:
    username = validate_username(params["username"])
    _, path, _, _ = _ssh_paths(username)
    lines = _read_lines(path)
    _, active = prune_and_count(lines, int(time.time()))
    return {"active": active, "max": MAX_CONCURRENT_SESSIONS}
