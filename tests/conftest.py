import base64
import os
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

    from daemon import cgroups, filebrowser, nsisolation, resellers, resource_manager, sysops

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
