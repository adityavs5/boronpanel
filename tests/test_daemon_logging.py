"""Regression test for the plaintext-password-in-journal exposure found and
fixed at the start of Phase 4 (docs/CHECKPOINT-phase4-0-password-audit.md): a
real daemon.log audit found `doveadm pw -s ARGON2ID -p <password>` lines that
predated the Phase 3 stdin fix, mirrored verbatim into the systemd journal by
the root logger's StreamHandler -- and journal entries can't be redacted
after the fact.

Imports from daemon.logsetup specifically, NOT daemon.server -- daemon.server
registers real TERMINATE_HOOKS onto handlers_account's shared global list at
import time, which would otherwise permanently pollute every later test in
the same pytest process the first time anything imports it (found live: this
test originally did `from daemon.server import configure_logging`, which
made tests/test_handlers_account.py's terminate-hook test start failing
whenever the full suite ran, never in isolation -- a real instance of the
project's own "no test should require root/live services" principle needing
the module boundary to actually be side-effect-free, not just usually so)."""
import logging
import stat

import pytest

from daemon.logsetup import configure_logging


@pytest.fixture(autouse=True)
def _reset_proc_logger():
    proc_logger = logging.getLogger("borond.proc")
    original_handlers = list(proc_logger.handlers)
    original_propagate = proc_logger.propagate
    yield
    proc_logger.handlers = original_handlers
    proc_logger.propagate = original_propagate


def test_proc_logger_does_not_propagate_to_root(tmp_path):
    configure_logging(str(tmp_path))
    proc_logger = logging.getLogger("borond.proc")
    assert proc_logger.propagate is False
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o2770


def test_proc_logger_has_only_a_file_handler_no_stream_handler(tmp_path):
    configure_logging(str(tmp_path))
    proc_logger = logging.getLogger("borond.proc")
    assert len(proc_logger.handlers) == 1
    assert isinstance(proc_logger.handlers[0], logging.FileHandler)
    assert not any(
        type(h) is logging.StreamHandler for h in proc_logger.handlers
    )


def test_proc_logger_message_reaches_file_not_root_handlers(tmp_path):
    configure_logging(str(tmp_path))
    proc_logger = logging.getLogger("borond.proc")
    proc_logger.info("exec: doveadm pw -s ARGON2ID")

    log_file = tmp_path / "daemon.log"
    assert log_file.exists()
    assert "exec: doveadm pw" in log_file.read_text()


def test_configure_logging_is_idempotent_no_duplicate_handlers(tmp_path):
    configure_logging(str(tmp_path))
    configure_logging(str(tmp_path))
    proc_logger = logging.getLogger("borond.proc")
    assert len(proc_logger.handlers) == 1


def test_chmod_denial_does_not_prevent_daemon_logging(tmp_path, monkeypatch):
    original_chmod = type(tmp_path).chmod

    def deny_directory_only(path, mode):
        if path == tmp_path:
            raise PermissionError
        return original_chmod(path, mode)

    monkeypatch.setattr("daemon.logsetup.Path.chmod", deny_directory_only)

    configure_logging(str(tmp_path))

    assert (tmp_path / "daemon.log").is_file()
