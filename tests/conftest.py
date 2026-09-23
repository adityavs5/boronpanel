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
