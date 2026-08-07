"""Run A feature 7: structured request logging for boron-api.

Every HTTP request is written as one JSON line to
`{log_dir}/api-access.log`; requests that produced a 5xx are ALSO written
to `{log_dir}/api-error.log` (the separate error stream the goal asks for,
and what the admin "last 100 errors" view reads back). Both are plain
append-only files rotated by logrotate out-of-process
(deploy/boron-api.logrotate, `copytruncate` so this long-lived process
never has to reopen an fd -- see that file's own comment for the race
tradeoff that buys).

Deliberately its own tiny module, not folded into api/main.py: the record
shape is unit-tested directly (tests/test_request_logging.py) without
standing up the ASGI app, exactly like daemon/logsetup.py is split out
from daemon/server.py for the same reason.

Robustness rule: logging must never take the API down. If the log
directory isn't writable (a misconfigured box, or simply not yet created
before the installer's chown runs), configuration falls back to a
no-op sink and warns once -- a dropped access-log line is never worth a
500 on a real request.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from shared.config import settings

_ACCESS_LOGGER = "boron.access"
_ERROR_LOGGER = "boron.access.error"

# Configuration is done lazily on first record so it picks up the runtime
# settings.log_dir (which tests monkeypatch) whether or not the ASGI
# lifespan hook ran -- a bare TestClient(app) doesn't trigger lifespan.
_configured_for: str | None = None
_bootstrap_logger = logging.getLogger("boron.logsetup")


def access_log_path(log_dir: str | None = None) -> Path:
    return Path(log_dir or settings.log_dir) / "api-access.log"


def error_log_path(log_dir: str | None = None) -> Path:
    return Path(log_dir or settings.log_dir) / "api-error.log"


def _plain_file_handler(path: Path) -> logging.Handler:
    handler = logging.FileHandler(path, encoding="utf-8")
    try:
        path.chmod(0o640)
    except OSError:
        pass
    # The record's message IS the finished JSON line -- no level/logger
    # decoration, so the file is machine-parseable one-object-per-line.
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler


def configure(log_dir: str | None = None) -> None:
    """(Re)attach file handlers for the access + error loggers. Idempotent
    per log_dir. Safe to call repeatedly; safe to call with an unwritable
    dir (falls back to a no-op sink)."""
    global _configured_for
    log_dir = log_dir or settings.log_dir

    access = logging.getLogger(_ACCESS_LOGGER)
    error = logging.getLogger(_ERROR_LOGGER)
    for lg in (access, error):
        # Close and drop any handlers from a previous configure() so a
        # re-point (tests, or a config reload) doesn't double-write.
        for h in list(lg.handlers):
            lg.removeHandler(h)
            h.close()
        lg.setLevel(logging.INFO)
        lg.propagate = False  # never duplicate access lines onto the root/journal

    try:
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        # Production creates this directory as root:boron-api so root-owned
        # services and the unprivileged API can share it. The API may write
        # there through the group bit but cannot chmod a root-owned directory;
        # that harmless EPERM must not disable request logging.
        try:
            directory.chmod(0o750)
        except PermissionError:
            pass
        access.addHandler(_plain_file_handler(access_log_path(log_dir)))
        error.addHandler(_plain_file_handler(error_log_path(log_dir)))
    except OSError as exc:
        # Unwritable log dir: don't crash, don't spam. One warning, then a
        # NullHandler so records are silently dropped rather than hitting
        # logging's last-resort stderr handler on every request.
        access.addHandler(logging.NullHandler())
        error.addHandler(logging.NullHandler())
        _bootstrap_logger.warning("api request logging disabled: %s not writable (%s)", log_dir, exc)

    _configured_for = log_dir


def _ensure_configured() -> None:
    if _configured_for != settings.log_dir:
        configure(settings.log_dir)


def reset() -> None:
    """Test hook: force the next record to reconfigure from settings.log_dir."""
    global _configured_for
    _configured_for = None


def build_record(method: str, path: str, status: int, duration_ms: float, user: str | None, ip: str | None) -> dict:
    """The structured access record. Pure, so it's asserted directly in
    tests without any file or ASGI plumbing."""
    return {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "method": method,
        "path": path,
        "status": status,
        "duration_ms": round(duration_ms, 1),
        "user": user,
        "ip": ip,
    }


def record_access(method: str, path: str, status: int, duration_ms: float, user: str | None, ip: str | None) -> None:
    _ensure_configured()
    line = json.dumps(build_record(method, path, status, duration_ms, user, ip), separators=(",", ":"))
    logging.getLogger(_ACCESS_LOGGER).info(line)
    if status >= 500:
        logging.getLogger(_ERROR_LOGGER).info(line)


def tail_error_records(limit: int = 100, log_dir: str | None = None) -> list[dict]:
    """Last `limit` 5xx records, newest first, for the admin error view.
    Reads only the tail of the file (errors should be rare, but the file is
    unbounded between logrotate runs, so don't slurp an arbitrarily large
    one). Malformed lines are skipped, never fatal."""
    path = error_log_path(log_dir)
    if not path.exists():
        return []
    # 256 KiB comfortably holds far more than 100 JSON error lines; reading
    # from the end keeps this O(tail) rather than O(file).
    read_bytes = 256 * 1024
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - read_bytes))
        chunk = fh.read()
    text = chunk.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if size > read_bytes and lines:
        # The first line is probably a partial record from the seek cut.
        lines = lines[1:]
    records: list[dict] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(records) >= limit:
            break
    return records
