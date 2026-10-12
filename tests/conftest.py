import base64
import asyncio
import logging
import os
import anyio.to_thread
from pathlib import Path
import secrets
import tempfile

import pytest

# Establish disposable configuration before importing any application module.
# A test run must not read the installed server's keys or persist a generated
# encryption key into /etc/boron (or a device such as /dev/null).
_test_config = tempfile.TemporaryDirectory(prefix="boron-test-config-")
_test_root = Path(_test_config.name)
(_test_root / "boron.toml").write_text("")
(_test_root / "secrets.env").write_text(
    "SESSION_SECRET=" + secrets.token_hex(32) + "\n"
    "APP_ENV_KEY=" + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode() + "\n"
)
(_test_root / "secrets.env").chmod(0o600)
(_test_root / "api-secrets.env").write_text("")
os.environ["BORON_CONFIG"] = str(_test_root / "boron.toml")
os.environ["BORON_SECRETS"] = str(_test_root / "secrets.env")
os.environ["BORON_API_SECRETS"] = str(_test_root / "api-secrets.env")

from shared.config import settings
from testsupport.host_requirements import chown_or_skip


_DOVECOT_OWNERSHIP_MODULES = frozenset({
    "test_mail_restore_gate.py",
    "test_snapshot_mail_dispatch.py",
    "test_snapshot_mail_files.py",
    "test_snapshot_mail_guard.py",
    "test_snapshot_mail_guard_config.py",
    "test_snapshot_mail_journal.py",
    "test_snapshot_mail_metadata.py",
    "test_snapshot_mail_restore.py",
    "test_snapshot_mail_routing_finalization.py",
    "test_snapshot_mail_routing_journal.py",
    "test_snapshot_mail_routing_rollback.py",
    "test_snapshot_mail_safety.py",
    "test_snapshot_mail_sieve_activation.py",
    "test_snapshot_routing_queue.py",
    "test_snapshot_routing_supervised.py",
})


@pytest.fixture(scope="session", autouse=True)
def _make_shared_pytest_parent_traversable(tmp_path_factory):
    """Let disposable service identities reach only their opened fixtures.

    Pytest normally makes its explicit base directory mode 0700. The harness
    parent contains only test data, so make this one shared ancestor
    traversable. Individual fixture directories retain their private modes.
    """
    base = tmp_path_factory.getbasetemp()
    base.chmod(0o755)


@pytest.fixture(autouse=True)
def _restore_process_logging_after_test():
    """A logging test must not leave handlers pointing into deleted tmp data."""
    root = logging.getLogger()
    proc = logging.getLogger("borond.proc")
    root_handlers, root_level = list(root.handlers), root.level
    proc_handlers, proc_level, proc_propagate = (
        list(proc.handlers), proc.level, proc.propagate
    )
    yield
    for handler in root.handlers:
        if handler not in root_handlers:
            handler.close()
    root.handlers = root_handlers
    root.setLevel(root_level)
    for handler in proc.handlers:
        if handler not in proc_handlers:
            handler.close()
    proc.handlers = proc_handlers
    proc.setLevel(proc_level)
    proc.propagate = proc_propagate


@pytest.fixture(autouse=True)
def _require_dovecot_identity_mapping(request):
    """Skip service-ownership integration modules in unmapped user namespaces."""
    filename = request.node.path.name
    if os.geteuid() != 0:
        return
    if not filename.startswith("test_snapshot_mail_") and filename not in _DOVECOT_OWNERSHIP_MODULES:
        return
    import grp

    probe_root = request.getfixturevalue("tmp_path")
    probe = probe_root / "dovecot-gid-probe"
    probe.touch()
    chown_or_skip(probe, 0, grp.getgrnam("dovecot").gr_gid)


@pytest.fixture(autouse=True)
def _inline_anyio_sync_calls(monkeypatch):
    """Keep Starlette sync dependencies deterministic in constrained CI.

    The managed runner can suppress the event-loop wakeup sent when an AnyIO
    worker thread completes. The worker has already returned, but the ASGI
    request then waits forever. Unit tests exercise the same callable and
    exception semantics inline; production still uses AnyIO's worker pool.
    """
    async def run_sync_inline(func, *args, **_options):
        return func(*args)

    monkeypatch.setattr(anyio.to_thread, "run_sync", run_sync_inline)


@pytest.fixture
def immediate_asyncio_executor(monkeypatch):
    """Execute direct daemon-dispatch calls without a sandbox worker thread.

    The managed test sandbox can suppress the cross-thread event-loop wakeup
    used by ``run_in_executor`` even after the worker has completed. Production
    dispatch threading is covered separately by request-context/executor tests;
    tests using this fixture are checking handler, audit and authorization
    semantics and must not spend minutes waiting on that environment artifact.
    """
    def run_immediately(loop, _executor, handler, *args):
        future = loop.create_future()
        try:
            future.set_result(handler(*args))
        except Exception as exc:  # noqa: BLE001 - mirror executor Future behavior
            future.set_exception(exc)
        return future

    monkeypatch.setattr(asyncio.BaseEventLoop, "run_in_executor", run_immediately)


@pytest.fixture(autouse=True)
def _isolate_request_logs(tmp_path_factory, monkeypatch):
    """Never let a test write the API request logs into the real
    /var/log/boron. Any test that exercises the app through its ASGI
    stack (TestClient) runs the Run A f7 access-log middleware, which writes
    to settings.log_dir on first use -- without this, running `pytest` as
    root on a live box pollutes the production access log and, worse, leaves
    root-owned api-access.log/api-error.log the unprivileged service then
    can't reopen. Point log_dir at a throwaway dir for every test."""
    import api.logsetup as logsetup

    d = tmp_path_factory.mktemp("fhlogs")
    monkeypatch.setattr(settings, "log_dir", str(d))
    # SQL/mail helper decorators also acquire private cross-process locks.
    # Even tests without a control-plane database must never use live locks.
    monkeypatch.setattr(settings, "snapshot_private_dir", str(d / "snapshot-private"))
    logsetup.reset()
    yield
    logsetup.reset()


@pytest.fixture(autouse=True)
def _isolate_privileged_account_side_effects(monkeypatch, request):
    """Keep mocked hosting identities from mutating the live test host.

    Many control-plane unit tests deliberately replace create_linux_user with
    a fake UID and never create a passwd entry or home. New account hooks must
    therefore be stubbed centrally, while their dedicated unit suites retain
    the real function under test. Live integration coverage is performed by
    the disposable-account release probe, not by fictional demo1 users.
    """
    filename = request.node.path.name

    from daemon import (
        cgroups,
        filebrowser,
        handlers_ftp,
        nsisolation,
        resellers,
        resource_manager,
        sysops,
    )

    if filename != "test_sysops.py":
        monkeypatch.setattr(
            sysops, "ensure_web_logs", lambda username: f"/home/{username}/logs"
        )
    if filename not in {"test_cgroups.py", "test_resource_manager.py", "test_resource_limits.py"}:
        monkeypatch.setattr(cgroups, "apply_limits", lambda *args, **kwargs: None)
        monkeypatch.setattr(resource_manager, "apply_account", lambda *args, **kwargs: None)
    # daemon.server stores this one as a direct function reference rather than
    # resolving resource_manager.apply_account at call time. Replace the
    # per-test hook list so suite collection order cannot make reseller unit
    # tests call the live host resource manager for fictional accounts.
    monkeypatch.setattr(resellers, "RESOURCE_HOOKS", [])
    if filename != "test_nsisolation.py":
        monkeypatch.setattr(nsisolation, "enable_for_account", lambda account: None)
    if not filename.startswith("test_filebrowser"):
        monkeypatch.setattr(filebrowser, "add_source_for_account", lambda account: None)
    if filename != "test_handlers_ftp.py":
        # daemon.server registers FTP suspend hooks during test collection.
        # Fictional unit-test users do not have passwd entries, so keep that
        # host integration out of unrelated account mutation tests.
        monkeypatch.setattr(
            handlers_ftp, "set_account_ftp_suspended", lambda account, suspended: None
        )


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """Point the control-plane DB at a throwaway SQLite file for this test
    and reset shared.db's cached engine/sessionmaker globals so each test
    gets a clean schema instead of sharing state across tests."""
    import shared.db as db_module

    monkeypatch.setattr(settings, "snapshot_private_dir", str(tmp_path / "snapshot-private"))
    db_path = tmp_path / "boron-test.db"
    monkeypatch.setattr(settings, "db_path", str(db_path))
    monkeypatch.setattr(db_module, "_write_engine", None)
    monkeypatch.setattr(db_module, "_WriteSession", None)
    monkeypatch.setattr(db_module, "_read_engine", None)
    monkeypatch.setattr(db_module, "_ReadSession", None)
    # Unit tests must not depend on (or mutate ownership through) a live
    # boron-api system group when the suite happens to run as root.
    monkeypatch.setattr(db_module, "_grant_api_group_read", lambda: None)
    db_module.init_db()
    yield db_path
