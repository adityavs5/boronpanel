"""SSH key management (Phase 4 feature 6).

Superseded ARCHITECTURE.md SS5's original v1 scope ("no interactive shell")
explicitly -- adding the first key upgrades the account's shell from
`/usr/sbin/nologin` to `/bin/bash` (daemon/sysops.py); removing the last key
reverts it, so an account with zero configured keys can never SSH in even if
something else were to go wrong. `~/.ssh` (0700) and `authorized_keys`
(0600), both owned by the account's own uid/gid, satisfy sshd's `StrictModes`
requirement (refuses to use a world/group-writable authorized_keys) without
any sshd_config change -- PubkeyAuthentication is already on by default and
this server has no AllowUsers/DenyUsers restricting who may connect.

Keys are validated with a real `ssh-keygen -lf -` call (reads the public key
from stdin, prints its fingerprint) rather than a hand-rolled base64/format
regex -- the same "real system tool over reimplemented parsing" choice this
project already makes for IP/CIDR validation (Python's `ipaddress`) and
Sieve script validation (`sievec`). Private keys are never accepted or
stored -- there is no code path here that could even attempt to parse a
private key as if it were a public one; `ssh-keygen -lf -` on a private key
input fails the same way garbage input does.
"""
from __future__ import annotations

import os
import pwd

from shared.config import settings
from shared.validation import ValidationError, validate_ssh_key_text, validate_username

from daemon import safeio, sysops
from daemon.procutil import run

SSH_KEYGEN_BIN = "/usr/bin/ssh-keygen"


class SshKeyError(Exception):
    pass


def _read_authorized_lines(username: str) -> list[str]:
    """Existing non-blank authorized_keys lines, read symlink-safely: if the
    account planted a symlink at ~/.ssh/authorized_keys (or ~/.ssh itself),
    secure_read_text returns None and we treat it as empty rather than reading
    -- and later rewriting -- the contents of whatever it pointed at."""
    text = safeio.secure_read_text(_ssh_dir(username), "authorized_keys")
    if text is None:
        return []
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _account_home(username: str) -> str:
    return os.path.realpath(f"{settings.home_base}/{username}")


def _ssh_dir(username: str) -> str:
    return os.path.join(_account_home(username), ".ssh")


def _authorized_keys_path(username: str) -> str:
    return os.path.join(_ssh_dir(username), "authorized_keys")


def _key_blob(line: str) -> tuple[str, ...]:
    """(type, base64) -- the part of the line that actually identifies the
    key, ignoring the trailing comment (two entries differing only by
    comment are still the same key)."""
    parts = line.split(None, 2)
    return tuple(parts[:2]) if len(parts) >= 2 else (line,)


def _inspect(key_text: str) -> dict:
    result = run([SSH_KEYGEN_BIN, "-lf", "-"], input_text=key_text + "\n", timeout=10)
    if not result.ok:
        raise ValidationError("not a valid SSH public key (checked with ssh-keygen)")
    # "<bits> SHA256:<fingerprint> <comment> (<type>)" -- comment may be
    # "no comment" if the key itself carries none.
    parts = result.stdout.strip().split(None, 2)
    bits = parts[0] if len(parts) > 0 else ""
    fingerprint = parts[1] if len(parts) > 1 else ""
    rest = parts[2] if len(parts) > 2 else ""
    comment, key_type = "", ""
    if rest.endswith(")") and "(" in rest:
        comment_part, _, type_part = rest.rpartition("(")
        comment = comment_part.strip()
        key_type = type_part.rstrip(")")
    else:
        comment = rest
    if comment == "no comment":
        comment = ""
    return {"bits": bits, "fingerprint": fingerprint, "comment": comment, "type": key_type}


def list_keys(params: dict) -> dict:
    username = validate_username(params["username"])
    keys = []
    for line in _read_authorized_lines(username):
        if line.startswith("#"):
            continue
        try:
            info = _inspect(line)
        except ValidationError:
            continue  # skip a line this module didn't write rather than fail the whole listing
        keys.append(info)
    return {"keys": keys}


def add_key(params: dict) -> dict:
    username = validate_username(params["username"])
    key_text = validate_ssh_key_text(params["key"])
    info = _inspect(key_text)  # raises ValidationError if malformed -- never written if so

    pw = pwd.getpwnam(username)
    # Create ~/.ssh symlink-safely BEFORE reading/writing authorized_keys: the
    # account can write its own home, so a naive makedirs+chown of ~/.ssh is a
    # root privesc primitive (see daemon/safeio.py).
    home = os.path.realpath(f"{settings.home_base}/{username}")
    safeio.secure_mkdirs(home, ".ssh", pw.pw_uid, pw.pw_gid, 0o700)

    existing_lines = _read_authorized_lines(username)
    if _key_blob(key_text) in {_key_blob(ln) for ln in existing_lines}:
        raise ValidationError("this SSH key is already added for this account")

    existing_lines.append(key_text)
    _write_authorized_keys(username, existing_lines, pw.pw_uid, pw.pw_gid)

    if sysops.get_shell(username) == sysops.NOLOGIN_SHELL:
        sysops.set_shell(username, sysops.LOGIN_SHELL)

    return {"fingerprint": info["fingerprint"], "comment": info["comment"], "type": info["type"], "status": "added"}


def delete_key(params: dict) -> dict:
    username = validate_username(params["username"])
    fingerprint = params["fingerprint"]

    lines = _read_authorized_lines(username)
    if not lines:
        raise SshKeyError("no SSH keys configured for this account")

    remaining = []
    removed = False
    for line in lines:
        try:
            info = _inspect(line)
        except ValidationError:
            remaining.append(line)  # leave a line this module didn't write alone
            continue
        if info["fingerprint"] == fingerprint:
            removed = True
            continue
        remaining.append(line)

    if not removed:
        raise SshKeyError(f"no key with fingerprint '{fingerprint}' found for this account")

    pw = pwd.getpwnam(username)
    _write_authorized_keys(username, remaining, pw.pw_uid, pw.pw_gid)

    if not remaining and sysops.get_shell(username) == sysops.LOGIN_SHELL:
        sysops.set_shell(username, sysops.NOLOGIN_SHELL)

    return {"status": "deleted"}


def _write_authorized_keys(username: str, lines: list[str], uid: int, gid: int) -> None:
    # Atomic + symlink-safe: the temp file is created O_EXCL|O_NOFOLLOW inside
    # ~/.ssh and renamed over authorized_keys, so neither a pre-planted temp
    # symlink nor a symlinked destination can redirect this root-owned write.
    content = ("\n".join(lines) + "\n") if lines else ""
    safeio.secure_replace_file(_ssh_dir(username), "authorized_keys", content, uid, gid, 0o600)


def terminate_account_sshkeys(account) -> None:
    """TERMINATE_HOOKS entry: purely a no-op placeholder for symmetry with
    every other per-account feature's termination hook -- authorized_keys
    lives inside the account's own home dir and disappears along with it
    via `userdel --remove`; there is no separate DB row or external state
    for this feature to clean up."""
    return None
