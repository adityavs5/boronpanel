import pytest

from shared.config import settings


@pytest.fixture(autouse=True)
def _isolate_request_logs(tmp_path_factory, monkeypatch):
    """Never let a test write the API request logs into the real
    /var/log/forgehost. Any test that exercises the app through its ASGI
    stack (TestClient) runs the Run A f7 access-log middleware, which writes
    to settings.log_dir on first use -- without this, running `pytest` as
    root on a live box pollutes the production access log and, worse, leaves
    root-owned api-access.log/api-error.log the unprivileged service then
    can't reopen. Point log_dir at a throwaway dir for every test."""
    import api.logsetup as logsetup

    d = tmp_path_factory.mktemp("fhlogs")
    monkeypatch.setattr(settings, "log_dir", str(d))
    logsetup.reset()
    yield
    logsetup.reset()


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """Point the control-plane DB at a throwaway SQLite file for this test
    and reset shared.db's cached engine/sessionmaker globals so each test
    gets a clean schema instead of sharing state across tests."""
    import shared.db as db_module

    db_path = tmp_path / "forgehost-test.db"
    monkeypatch.setattr(settings, "db_path", str(db_path))
    monkeypatch.setattr(db_module, "_write_engine", None)
    monkeypatch.setattr(db_module, "_WriteSession", None)
    monkeypatch.setattr(db_module, "_read_engine", None)
    monkeypatch.setattr(db_module, "_ReadSession", None)
    db_module.init_db()
    yield db_path
