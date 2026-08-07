"""Logging setup for borond, split out from daemon/server.py so it can be
imported and tested (tests/test_daemon_logging.py) without pulling in
daemon.server's business-logic imports -- those register real handlers onto
shared global state at import time (handlers_account.TERMINATE_HOOKS), which
would otherwise pollute every later test in the same pytest process.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path


def configure_logging(log_dir: str) -> None:
    os.umask(0o027)
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    # Keep the installer's setgid + group-write contract: boron-api must be
    # able to create request logs through its service group. Some hardened
    # service environments deny chmod even to the daemon's root process; the
    # installer remains the authority for ownership/mode, and that denial
    # must not put this critical daemon into a restart loop.
    try:
        Path(log_dir).chmod(0o2770)
    except PermissionError:
        pass
    daemon_log = Path(log_dir) / "daemon.log"
    daemon_log.touch(exist_ok=True, mode=0o640)
    daemon_log.chmod(0o640)
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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(daemon_log),
            logging.StreamHandler(),
        ],
    )
    proc_logger = logging.getLogger("borond.proc")
    proc_logger.propagate = False
    proc_logger.handlers = [logging.FileHandler(daemon_log)]
    proc_logger.setLevel(logging.INFO)
