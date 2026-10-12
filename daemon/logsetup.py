"""Logging setup for borond, split out from daemon/server.py so it can be
imported and tested (tests/test_daemon_logging.py) without pulling in
daemon.server's business-logic imports -- those register real handlers onto
shared global state at import time (handlers_account.TERMINATE_HOOKS), which
would otherwise pollute every later test in the same pytest process.
"""
from __future__ import annotations

import logging
import json
import os
import pwd
import datetime as dt
import re
from logging.handlers import WatchedFileHandler
from pathlib import Path

from daemon import safeio
from shared.request_context import current_error_reference, current_request_id

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.DOTALL,
)


def redact_log_message(message: str) -> str:
    message = _PRIVATE_KEY.sub("***REDACTED PRIVATE KEY***", message)
    message = _BEARER.sub("Bearer ***REDACTED***", message)
    return _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}{match.group(2)}***REDACTED***", message)


class JsonLineFormatter(logging.Formatter):
    """One bounded structured line; never serialise arbitrary exception text."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": dt.datetime.fromtimestamp(record.created, dt.timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_log_message(record.getMessage())[:8000],
            "request_id": current_request_id(),
            "reference": current_error_reference(),
        }
        if record.exc_info and record.exc_info[0]:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def _watched_handler(path: Path) -> logging.Handler:
    handler = WatchedFileHandler(path, encoding="utf-8")
    handler.setFormatter(JsonLineFormatter())
    return handler


def prepare_log_directory(log_dir: str) -> None:
    """Root owns log names; the API may append only to its precreated files."""
    Path(log_dir).mkdir(parents=True, exist_ok=True, mode=0o750)
    fd = safeio._open_dir_nofollow(log_dir)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid():
            raise PermissionError('log directory must be owned by the daemon identity')
        api = pwd.getpwnam('boron-api')
        os.fchown(fd, os.geteuid(), api.pw_gid)
        # Revoke name replacement before inspecting or opening any log file.
        os.fchmod(fd, 0o750)
        try:
            os.mkdir('api-index', mode=0o700, dir_fd=fd)
        except FileExistsError:
            pass
        index_fd = os.open('api-index', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            os.fchown(index_fd, api.pw_uid, api.pw_gid)
            os.fchmod(index_fd, 0o700)
        finally:
            os.close(index_fd)
    finally:
        os.close(fd)
    for name in ('api-access.log', 'api-error.log'):
        safeio.secure_ensure_file_beneath(log_dir, '.', name, api.pw_uid, api.pw_gid, 0o640)
    safeio.secure_ensure_file_beneath(log_dir, '.', 'daemon.log', os.geteuid(), api.pw_gid, 0o640)


def configure_logging(log_dir: str) -> None:
    os.umask(0o027)
    prepare_log_directory(log_dir)
    daemon_log = Path(log_dir) / "daemon.log"
    """Split borond.proc (daemon/procutil.py's run()) away from the
    journal/stdout sink.

    That logger writes every subprocess's full argument list, for ops
    visibility -- exactly right for ordinary commands, but a real, serious
    pre-existing bug (found and fixed in daemon/mail.py's hash_password()
    during Phase 3 feature 10; see CHECKPOINT-phase3-10.md) proved that any
    *future* call site that still puts a secret in argv rather than
    input_text would leak it not just to daemon.log (redactable, ours to
    control) but also to the systemd journal via the root logger's
    StreamHandler -- and journal entries can't be surgically redacted after
    the fact (append-only, checksummed per-entry), unlike a plain file.
    daemon.log already gives full ops visibility for this logger, so routing
    it away from stdout/journal entirely closes that channel with no loss of
    information, only of an un-redactable duplicate."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.INFO)
    file_handler = _watched_handler(daemon_log)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(JsonLineFormatter())
    root.handlers = [file_handler, stream_handler]
    proc_logger = logging.getLogger("borond.proc")
    proc_logger.propagate = False
    for handler in list(proc_logger.handlers):
        handler.close()
    proc_logger.handlers = [_watched_handler(daemon_log)]
    proc_logger.setLevel(logging.INFO)
