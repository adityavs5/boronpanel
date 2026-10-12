"""Run A feature 7: structured request logging for boron-api.

Every HTTP request is written as one JSON line to
`{log_dir}/api-access.log`; requests that produced a 4xx or 5xx are also
written to `{log_dir}/api-error.log` and an indexed API-owned sidecar used by
the administrator search. Both logs are JSON lines. Watched file handlers
reopen after logrotate's atomic rename/create, avoiding copytruncate loss.

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
import hashlib
import json
import logging
from logging.handlers import WatchedFileHandler
from pathlib import Path
import sqlite3

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


def error_index_path(log_dir: str | None = None) -> Path:
    return Path(log_dir or settings.log_dir) / "api-index" / "errors.sqlite3"


def _plain_file_handler(path: Path) -> logging.Handler:
    # Reopen after logrotate's atomic rename/create.  This avoids copytruncate's
    # race where lines written between copy and truncate are lost.
    handler = WatchedFileHandler(path, encoding="utf-8")
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
        # Root precreates API-owned files in a root-owned, non-writable
        # directory. The API may append to its files but cannot replace names
        # used by root cron jobs. A harmless chmod EPERM must not disable logs.
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

    try:
        _initialize_error_index(log_dir)
    except (OSError, sqlite3.Error) as exc:
        # File logging remains useful while a package upgrade is between the
        # daemon precreating api-index and the API restart.
        _bootstrap_logger.warning("API error search index disabled: %s", type(exc).__name__)

    _configured_for = log_dir


def _ensure_configured() -> None:
    if _configured_for != settings.log_dir:
        configure(settings.log_dir)


def reset() -> None:
    """Test hook: force the next record to reconfigure from settings.log_dir."""
    global _configured_for
    _configured_for = None


def build_record(method: str, path: str, status: int, duration_ms: float,
                 user: str | None, ip: str | None, *, request_id: str | None = None,
                 reference: str | None = None) -> dict:
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
        "request_id": request_id,
        "reference": reference,
    }


def record_access(method: str, path: str, status: int, duration_ms: float,
                  user: str | None, ip: str | None, *, request_id: str | None = None,
                  reference: str | None = None) -> None:
    _ensure_configured()
    record = build_record(method, path, status, duration_ms, user, ip,
                          request_id=request_id, reference=reference)
    line = json.dumps(record, separators=(",", ":"))
    logging.getLogger(_ACCESS_LOGGER).info(line)
    if status >= 400:
        logging.getLogger(_ERROR_LOGGER).info(line)
        _index_error_record(record)


def tail_error_records(limit: int = 100, log_dir: str | None = None) -> list[dict]:
    """Last ``limit`` error records, newest first, as an index fallback.
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


def record_operation_error(diagnostic: dict, user: str | None, ip: str | None,
                           *, status: int = 502, request_id: str | None = None) -> None:
    """Metadata comes from root's safe failure boundary, never vendor text."""
    _ensure_configured()
    record = {
        **build_record('RPC', '', status, 0, user, ip, request_id=request_id,
                       reference=diagnostic.get("reference")),
        **diagnostic,
    }
    logging.getLogger(_ERROR_LOGGER).info(json.dumps(record, separators=(',', ':')))
    _index_error_record(record)


def _initialize_error_index(log_dir: str | None = None) -> None:
    path = error_index_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    with sqlite3.connect(path, timeout=2.0) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute(
            """CREATE TABLE IF NOT EXISTS error_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                reference TEXT,
                request_id TEXT,
                status INTEGER NOT NULL,
                record_json TEXT NOT NULL,
                fingerprint TEXT NOT NULL
            )"""
        )
        db.execute("CREATE INDEX IF NOT EXISTS ix_error_reference ON error_records(reference)")
        db.execute("CREATE INDEX IF NOT EXISTS ix_error_request_id ON error_records(request_id)")
        db.execute("CREATE INDEX IF NOT EXISTS ix_error_timestamp ON error_records(timestamp)")
        db.execute("CREATE INDEX IF NOT EXISTS ix_error_status ON error_records(status)")


def _index_error_record(record: dict, log_dir: str | None = None) -> None:
    """Best-effort searchable index containing the same allowlisted log row."""
    try:
        raw = json.dumps(record, separators=(",", ":"), sort_keys=True)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with sqlite3.connect(error_index_path(log_dir), timeout=2.0) as db:
            db.execute(
                "INSERT INTO error_records(timestamp,reference,request_id,status,record_json,fingerprint) "
                "VALUES(?,?,?,?,?,?)",
                (record.get("timestamp") or dt.datetime.now(dt.timezone.utc).isoformat(),
                 record.get("reference"), record.get("request_id"), int(record.get("status") or 500),
                 raw, digest),
            )
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        _bootstrap_logger.warning("could not index API error record: %s", type(exc).__name__)


def search_error_records(*, limit: int = 100, reference: str = "", request_id: str = "",
                         date_from: str = "", date_to: str = "", status: int | None = None,
                         log_dir: str | None = None) -> list[dict]:
    """Indexed server-side search; parameters are always bound, never SQL."""
    _ensure_configured()
    limit = max(1, min(int(limit), 500))
    clauses: list[str] = []
    values: list[object] = []
    if reference:
        clauses.append("reference = ?")
        values.append(reference[:64])
    if request_id:
        clauses.append("request_id = ?")
        values.append(request_id[:64])
    if date_from:
        clauses.append("timestamp >= ?")
        values.append(date_from[:64])
    if date_to:
        clauses.append("timestamp <= ?")
        values.append(date_to[:64])
    if status is not None:
        clauses.append("status = ?")
        values.append(int(status))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    try:
        with sqlite3.connect(error_index_path(log_dir), timeout=2.0) as db:
            rows = db.execute(
                f"SELECT record_json FROM error_records{where} ORDER BY id DESC LIMIT ?",  # noqa: S608
                (*values, limit),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]
    except (OSError, sqlite3.Error, json.JSONDecodeError, ValueError):
        # An old install can briefly lack the precreated index directory while
        # services restart. Preserve the pre-P2 recent-tail behavior.
        records = tail_error_records(limit=max(limit, 100), log_dir=log_dir)
        def matches(row: dict) -> bool:
            return (
                (not reference or row.get("reference") == reference)
                and (not request_id or row.get("request_id") == request_id)
                and (not date_from or str(row.get("timestamp", "")) >= date_from)
                and (not date_to or str(row.get("timestamp", "")) <= date_to)
                and (status is None or row.get("status") == status)
            )
        return [row for row in records if matches(row)][:limit]
