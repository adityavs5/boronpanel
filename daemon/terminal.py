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
import stat
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account
from shared.validation import validate_username

from daemon import sysops
from daemon.safeio import UnsafePathError, secure_mkdirs

logger = logging.getLogger("borond.terminal")

MAX_CONCURRENT_SESSIONS = 3
# A session whose close never ran leaves its key behind; reap it after this.
TERMINAL_KEY_MAX_AGE_SECONDS = 12 * 3600
SSH_HOST = "127.0.0.1"
SSH_PORT = 22
_MARKER_PREFIX = "boron-terminal-"
# No forwarding of any kind; a pty is still allowed (we need it) -- deliberately
# NOT `restrict`, which would also disable the pty.  The private key is only
# held by boron-api, but sshd should still enforce the local-only design if that
# key is ever mishandled.
_KEY_OPTIONS = 'from="127.0.0.1",no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-user-rc'
_LEGACY_KEY_OPTIONS = (
    "no-agent-forwarding,no-port-forwarding,no-X11-forwarding",
)


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


def _line_marker(line: str) -> str | None:
    parts = line.strip().split()
    if len(parts) != 4:
        return None
    options, key_type, _key_body, comment = parts
    if options != _KEY_OPTIONS and options not in _LEGACY_KEY_OPTIONS:
        return None
    if key_type != "ssh-ed25519" or not comment.startswith(_MARKER_PREFIX):
        return None
    return comment[len(_MARKER_PREFIX):]


def _is_terminal_line(line: str) -> bool:
    return _line_marker(line) is not None


def _line_session_id(line: str) -> str | None:
    """Extract the <sid> from a terminal marker's `...-<sid>-<epoch>` comment."""
    marker = _line_marker(line)
    if marker is None:
        return None
    parts = marker.rsplit("-", 1)
    return parts[0] if len(parts) == 2 else marker


def _line_epoch(line: str) -> int | None:
    marker = _line_marker(line)
    if marker is None:
        return None
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
    # Do not realpath through an attacker-provided component.  The account
    # home itself is anchored below the root-owned home_base and opened with
    # O_NOFOLLOW by secure_mkdirs/_open_ssh_dir below.
    ssh_dir = os.path.join(settings.home_base, username, ".ssh")
    return ssh_dir, os.path.join(ssh_dir, "authorized_keys"), pw.pw_uid, pw.pw_gid


def _open_ssh_dir(ssh_dir: str) -> int:
    return os.open(ssh_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)


def _read_lines(dir_fd: int) -> list[str]:
    try:
        fd = os.open("authorized_keys", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise UnsafePathError("authorized_keys is not a safe regular file") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise UnsafePathError("authorized_keys is not a regular file")
        with os.fdopen(fd, "r", closefd=False) as f:
            return [ln.rstrip("\n") for ln in f if ln.strip()]
    finally:
        os.close(fd)


def _write_lines(dir_fd: int, lines: list[str], uid: int, gid: int) -> None:
    tmp = f".authorized_keys.tmp.{os.getpid()}.{secrets.token_hex(4)}"
    content = ("\n".join(lines) + "\n") if lines else ""
    renamed = False
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dir_fd)
    try:
        try:
            data = memoryview(content.encode())
            while data:
                written = os.write(fd, data)
                if written <= 0:
                    raise OSError("short write while updating authorized_keys")
                data = data[written:]
            os.fchown(fd, uid, gid)
            os.fchmod(fd, 0o600)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(tmp, "authorized_keys", src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        renamed = True
        os.fsync(dir_fd)
    finally:
        if not renamed:
            try:
                os.unlink(tmp, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
            except OSError:
                logger.warning("failed to remove temporary authorized_keys file %s", tmp)


def _locked(dir_fd: int):
    """A lock on <ssh_dir>/.boron-terminal.lock serializing authorized_keys
    read-modify-write across concurrent open/close (this module and, harmlessly,
    only this module -- the SSH-keys feature edits distinct lines)."""
    fd = os.open(".boron-terminal.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dir_fd)
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

    ssh_dir, _path, uid, gid = _ssh_paths(username)
    home = os.path.dirname(ssh_dir)
    secure_mkdirs(home, ".ssh", uid, gid, 0o700)

    now = int(time.time())
    session_id = secrets.token_hex(16)
    private_pem, public_openssh = generate_keypair()

    dir_fd = _open_ssh_dir(ssh_dir)
    fd = _locked(dir_fd)
    try:
        lines = _read_lines(dir_fd)
        lines, active = prune_and_count(lines, now)
        if active >= MAX_CONCURRENT_SESSIONS:
            raise RuntimeError(f"maximum of {MAX_CONCURRENT_SESSIONS} concurrent terminal sessions reached for this account")
        lines.append(build_authorized_line(public_openssh, session_id, now))
        _write_lines(dir_fd, lines, uid, gid)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        os.close(dir_fd)

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
    ssh_dir, _path, uid, gid = _ssh_paths(username)
    try:
        dir_fd = _open_ssh_dir(ssh_dir)
    except OSError:
        return {"status": "not_found"}
    fd = _locked(dir_fd)
    try:
        lines = _read_lines(dir_fd)
        remaining = remove_session_line(lines, session_id)
        # Also reap any stale terminal markers while we hold the lock.
        remaining, _ = prune_and_count(remaining, int(time.time()))
        _write_lines(dir_fd, remaining, uid, gid)
        has_any_keys = any(ln.strip() and not ln.strip().startswith("#") for ln in remaining)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        os.close(dir_fd)

    # Symmetric with sshkeys.delete_key: if the account now has NO authorized
    # keys at all, revert the shell so a keyless account can't SSH in.
    if not has_any_keys and sysops.get_shell(username) == sysops.LOGIN_SHELL:
        sysops.set_shell(username, sysops.NOLOGIN_SHELL)

    return {"status": "closed", "session_id": session_id}


def list_sessions(params: dict) -> dict:
    username = validate_username(params["username"])
    ssh_dir, _, _, _ = _ssh_paths(username)
    try:
        dir_fd = _open_ssh_dir(ssh_dir)
    except OSError:
        return {"active": 0, "max": MAX_CONCURRENT_SESSIONS}
    try:
        lines = _read_lines(dir_fd)
    finally:
        os.close(dir_fd)
    _, active = prune_and_count(lines, int(time.time()))
    return {"active": active, "max": MAX_CONCURRENT_SESSIONS}
