import pytest

from shared.config import settings


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
