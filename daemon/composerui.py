"""Phase 8 feature 9: Composer UI.

Runs composer (install / update / require <pkg> / dump-autoload) asynchronously
as the account user in an app directory within the account's home (jailed via
daemon/filemanager's realpath check). composer is already installed on this
server; if it were missing, the phar is fetched server-wide. All args are argv
elements (no shell), so a package spec can't inject a command.
"""
from __future__ import annotations

import os
import re

from shared.config import settings
from shared.db import write_session
from shared.models import Account
from shared.validation import ValidationError, validate_username

from daemon import cmdjobs, filemanager
from daemon.procutil import run
from sqlalchemy import select

# vendor/package optionally with a :version-constraint. Constraint charset is
# composer's own (^ ~ > < = ! | , . * space digits/letters for stability flags).
_PACKAGE_RE = re.compile(
    r"\A[a-z0-9]([_.-]?[a-z0-9]+)*/[a-z0-9]([_.-]?[a-z0-9]+)*(:[A-Za-z0-9.\^~><=!|,* +-]{1,64})?\Z"
)

COMMANDS = {
    "install": ["install", "--no-interaction", "--no-progress"],
    "update": ["update", "--no-interaction", "--no-progress"],
    "dump-autoload": ["dump-autoload", "--no-interaction"],
    # "require" is built dynamically (needs the package arg).
}


def ensure_composer() -> str:
    """Return a runnable composer phar path. Prefers the installed
    /usr/bin/composer; falls back to downloading composer.phar server-wide."""
    if os.path.exists(settings.composer_bin):
        return settings.composer_bin
    fallback = settings.composer_phar_fallback
    if not os.path.exists(fallback):
        os.makedirs(os.path.dirname(fallback), exist_ok=True)
        result = run(["curl", "-fsSL", "-o", fallback, settings.composer_download_url], timeout=120)
        if not result.ok:
            raise RuntimeError(f"failed to download composer: {result.stderr.strip()}")
        os.chmod(fallback, 0o755)
    return fallback


def _resolve_app_dir(username: str, app_dir: str) -> str:
    # Reuse the file-manager jail (realpath within the account home).
    resolved, _ = filemanager._resolve(username, app_dir or "")
    if not os.path.isdir(resolved):
        raise RuntimeError(f"'{app_dir}' is not a directory in the account's home")
    return resolved


def _validate_package(pkg) -> str:
    if not isinstance(pkg, str) or not _PACKAGE_RE.match(pkg.strip()):
        raise ValidationError("package must look like 'vendor/name' or 'vendor/name:^1.2'")
    return pkg.strip()


def run_composer(params: dict) -> dict:
    username = validate_username(params["username"])
    command = params["command"]
    app_dir_rel = params.get("app_dir", "")
    with write_session() as session:
        if session.scalar(select(Account).where(Account.username == username)) is None:
            raise RuntimeError(f"account '{username}' not found")

    app_dir = _resolve_app_dir(username, app_dir_rel)

    if command == "require":
        package = _validate_package(params.get("package"))
        composer_args = ["require", package, "--no-interaction", "--no-progress"]
        display = f"composer require {package}"
    elif command in COMMANDS:
        composer_args = COMMANDS[command]
        display = f"composer {command}"
    else:
        raise ValidationError(f"unknown composer command '{command}'")

    composer = ensure_composer()
    argv = [settings.php_cli_bin, composer, *composer_args]
    return cmdjobs.submit(username, "composer", app_dir, argv, display)


def get_run(params: dict) -> dict:
    return cmdjobs.get_run(params)


def list_runs(params: dict) -> dict:
    return cmdjobs.list_runs(params, "composer")
