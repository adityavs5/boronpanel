"""Build trusted, pre-placed launch commands for account-owned CLI work."""
from __future__ import annotations

from pwd import getpwnam as _getpwnam
import re
import secrets

from sqlalchemy import select

from daemon import cgroups, resource_manager
from shared.db import write_session
from shared.models import Account
from shared.validation import validate_username

_TOKEN_RE = re.compile(r"^[a-z0-9-]{1,48}$")
_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def wrap(
    username: str,
    argv: list[str],
    *,
    token: str | None = None,
    cwd: str | None = None,
    home: str | None = None,
    env: dict[str, str] | None = None,
) -> list[str]:
    """Return a systemd-run argv placed in the account slice before exec.

    The caller still uses procutil.run, preserving bounded output, timeouts,
    secret redaction and stdin behavior. No customer value becomes shell text.
    """
    username = validate_username(username)
    if not argv or not all(isinstance(value, str) and value for value in argv):
        raise ValueError("account command must be a non-empty argument list")
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None or account.status != "active" or account.uid is None or account.gid is None:
            raise RuntimeError("account command requires an active Linux identity")
        account_id, uid, gid = account.id, account.uid, account.gid
    pw = _getpwnam(username)
    if (pw.pw_uid, pw.pw_gid) != (uid, gid):
        raise RuntimeError("account command identity does not match the control-plane record")

    # Apply before credential/command admission. systemd then creates the
    # transient service below this already-limited parent before execve().
    resource_manager.apply_account(account_id)
    raw_token = token or secrets.token_hex(8)
    if not _TOKEN_RE.fullmatch(raw_token):
        raise ValueError("invalid account command token")
    command = [
        "/usr/bin/systemd-run", "--quiet", "--wait", "--pipe", "--collect",
        "--service-type=exec", f"--unit=boron-account-{uid}-{raw_token}",
        f"--slice={cgroups.user_slice_name(uid)}", f"--uid={uid}", f"--gid={gid}",
        f"--setenv=HOME={home or pw.pw_dir}", f"--setenv=PATH={_PATH}",
    ]
    if cwd:
        command.append(f"--working-directory={cwd}")
    for key, value in (env or {}).items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or "\x00" in value or "\n" in value:
            raise ValueError("invalid account command environment")
        command.append(f"--setenv={key}={value}")
    return [*command, "--", *argv]
