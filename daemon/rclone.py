"""rclone remote management (Phase 2 feature 7).

Credentials for remote backup destinations (S3-compatible, SFTP, Google
Drive service-account, or anything else rclone supports) live entirely in
rclone's own config file, managed exclusively through `rclone config
create/delete` -- never hand-written here, and never duplicated into
Boron's own DB (shared/models.py's BackupDestination only stores the
remote *name* + a path prefix, the same "secrets live in one restricted
file, not the app DB" pattern already used for MariaDB/mail/SSL
credentials elsewhere in this project).

Google Drive specifically: rclone's normal `drive` remote setup is an
interactive OAuth browser flow, which has no place in a headless
non-interactive daemon. Non-interactive Drive access needs a service
account JSON key (`service_account_file`/`service_account_credentials`),
which this module accepts as just another config field -- full OAuth
"connect your Google account from the admin panel" is out of scope here,
documented as such rather than silently unsupported.
"""
from __future__ import annotations

from shared.config import settings

from daemon.procutil import run

RCLONE_CONFIG_PATH = "/etc/rclone.conf"


class RcloneError(Exception):
    pass


def _rclone_args(*args: str) -> list[str]:
    return [settings.rclone_bin, "--config", RCLONE_CONFIG_PATH, *args]


def create_remote(remote_name: str, remote_type: str, config: dict[str, str]) -> None:
    """Non-interactive remote creation -- `rclone config create` accepts
    every backend's config fields as trailing key=value-style args, no
    prompts. `--non-interactive` makes this fail loudly instead of hanging
    if a required field is missing, rather than silently waiting for input
    that will never come in a daemon context."""
    args = _rclone_args("config", "create", remote_name, remote_type, "--non-interactive")
    for key, value in config.items():
        args.append(f"{key}={value}")
    result = run(args, timeout=30)
    if not result.ok:
        raise RcloneError(f"rclone config create failed: {result.stderr.strip() or result.stdout.strip()}")


def delete_remote(remote_name: str) -> None:
    """Idempotent: deleting a remote that doesn't exist is a safe no-op --
    matches every other TERMINATE_HOOKS-style teardown in this project."""
    run(_rclone_args("config", "delete", remote_name), timeout=15)


def remote_exists(remote_name: str) -> bool:
    result = run(_rclone_args("listremotes"), timeout=15)
    if not result.ok:
        return False
    return f"{remote_name}:" in result.stdout.splitlines()


def copy(local_path: str, remote_path: str, timeout: float = 3600) -> None:
    result = run(_rclone_args("copyto", local_path, remote_path), timeout=timeout)
    if not result.ok:
        raise RcloneError(f"rclone copy failed: {result.stderr.strip() or result.stdout.strip()}")


def copy_from_remote(remote_path: str, local_path: str, timeout: float = 3600) -> None:
    result = run(_rclone_args("copyto", remote_path, local_path), timeout=timeout)
    if not result.ok:
        raise RcloneError(f"rclone copy from remote failed: {result.stderr.strip() or result.stdout.strip()}")


def delete_path(remote_path: str, timeout: float = 60) -> None:
    run(_rclone_args("deletefile", remote_path), timeout=timeout)


def size(remote_path: str, timeout: float = 30) -> int:
    result = run(_rclone_args("size", remote_path, "--json"), timeout=timeout)
    if not result.ok:
        return 0
    import json

    try:
        return int(json.loads(result.stdout).get("bytes", 0))
    except (ValueError, KeyError):
        return 0
