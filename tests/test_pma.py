import json

import pytest
from sqlalchemy import select

from daemon import handlers_account as ha
from daemon import handlers_database as hdb
from daemon import pma
from shared.config import settings
from shared.db import write_session
from shared.models import PmaToken


@pytest.fixture()
def stub_mariadb(monkeypatch):
    users = {}

    def create_database(db_name):
        pass

    def drop_database(db_name):
        pass

    def create_db_user(db_user, password, host="localhost"):
        users[db_user] = password

    def drop_db_user(db_user, host="localhost"):
        users.pop(db_user, None)

    def grant_all(db_name, db_user, host="localhost"):
        pass

    monkeypatch.setattr(hdb.mariadb, "database_exists", lambda name: False)
    monkeypatch.setattr(hdb.mariadb, "create_database", create_database)
    monkeypatch.setattr(hdb.mariadb, "drop_database", drop_database)
    monkeypatch.setattr(hdb.mariadb, "create_db_user", create_db_user)
    monkeypatch.setattr(hdb.mariadb, "drop_db_user", drop_db_user)
    monkeypatch.setattr(hdb.mariadb, "grant_all", grant_all)

    monkeypatch.setattr(pma.mariadb, "create_db_user", create_db_user)
    monkeypatch.setattr(pma.mariadb, "drop_db_user", drop_db_user)
    monkeypatch.setattr(pma.mariadb, "grant_exact_database", grant_all)
    monkeypatch.setattr(pma.mariadb, "generate_password", lambda length=24: "ephemeralpass123")
    return users


@pytest.fixture()
def stub_group(monkeypatch):
    """www-data may not exist in a container test environment -- the real
    chown is skipped either way (KeyError is handled gracefully), but we
    force the KeyError path deterministically here rather than depending
    on the test host's own group database."""

    def fake_getgrnam(name):
        raise KeyError(name)

    monkeypatch.setattr(pma.grp, "getgrnam", fake_getgrnam)


@pytest.fixture()
def account_with_db(isolated_db, monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    ha.create_account({"username": "demo1"})
    return {}


def test_create_token_requires_existing_database(account_with_db, stub_mariadb, stub_group, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "pma_token_dir", str(tmp_path / "pma-tokens"))
    with pytest.raises(pma.PmaError):
        pma.create_token({"username": "demo1", "name": "shop"})


def test_create_token_happy_path(account_with_db, stub_mariadb, stub_group, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "pma_token_dir", str(tmp_path / "pma-tokens"))
    monkeypatch.setattr(settings, "pma_hostname", "pma.example")
    hdb.create_database({"username": "demo1", "name": "shop"})

    result = pma.create_token({"username": "demo1", "name": "shop"})
    assert result["db_name"] == "demo1_shop"
    assert result["pma_url"].startswith("https://pma.example/boron_signon.php?token=")
    assert result["expires_in_seconds"] == settings.pma_token_ttl_seconds

    token_files = list((tmp_path / "pma-tokens").glob("*.json"))
    assert len(token_files) == 1
    data = json.loads(token_files[0].read_text())
    assert data["db_name"] == "demo1_shop"
    assert data["db_user"] in stub_mariadb  # ephemeral user was actually created
    assert data["db_user"] != "demo1_shop"  # never the real db_user

    with write_session() as session:
        row = session.scalar(select(PmaToken).where(PmaToken.db_name == "demo1_shop"))
        assert row is not None
        assert row.ephemeral_db_user == data["db_user"]


def test_create_token_accepts_already_scoped_full_db_name(account_with_db, stub_mariadb, stub_group, tmp_path, monkeypatch):
    """Regression: the Databases page only ever has the full db_name back
    from db.list (e.g. "demo1_shop"); passing that through must not
    double-prefix into "demo1_demo1_shop" and 404 as "not found"."""
    monkeypatch.setattr(settings, "pma_token_dir", str(tmp_path / "pma-tokens"))
    monkeypatch.setattr(settings, "pma_hostname", "pma.example")
    hdb.create_database({"username": "demo1", "name": "shop"})

    result = pma.create_token({"username": "demo1", "name": "demo1_shop"})
    assert result["db_name"] == "demo1_shop"
    assert result["pma_url"] is not None


def test_create_token_without_pma_hostname_returns_no_url(account_with_db, stub_mariadb, stub_group, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "pma_token_dir", str(tmp_path / "pma-tokens"))
    monkeypatch.setattr(settings, "pma_hostname", "")
    hdb.create_database({"username": "demo1", "name": "shop"})
    result = pma.create_token({"username": "demo1", "name": "shop"})
    assert result["pma_url"] is None


def test_two_tokens_for_different_databases_get_different_ephemeral_users(account_with_db, stub_mariadb, stub_group, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "pma_token_dir", str(tmp_path / "pma-tokens"))
    hdb.create_database({"username": "demo1", "name": "shop"})
    hdb.create_database({"username": "demo1", "name": "blog"})

    r1 = pma.create_token({"username": "demo1", "name": "shop"})
    r2 = pma.create_token({"username": "demo1", "name": "blog"})

    files = sorted((tmp_path / "pma-tokens").glob("*.json"))
    assert len(files) == 2
    users = {json.loads(f.read_text())["db_user"] for f in files}
    assert len(users) == 2  # each token got its own distinct, scoped ephemeral user


def test_cleanup_expired_tokens_drops_user_and_file(account_with_db, stub_mariadb, stub_group, tmp_path, monkeypatch):
    import datetime as dt

    monkeypatch.setattr(settings, "pma_token_dir", str(tmp_path / "pma-tokens"))
    hdb.create_database({"username": "demo1", "name": "shop"})
    pma.create_token({"username": "demo1", "name": "shop"})

    with write_session() as session:
        row = session.scalar(select(PmaToken))
        row.expires_at = row.expires_at - dt.timedelta(hours=1)
        ephemeral_user = row.ephemeral_db_user

    assert ephemeral_user in stub_mariadb
    cleaned = pma.cleanup_expired_tokens()
    assert cleaned == 1
    assert ephemeral_user not in stub_mariadb

    with write_session() as session:
        assert session.scalar(select(PmaToken)) is None
    assert list((tmp_path / "pma-tokens").glob("*.json")) == []


def test_cleanup_leaves_unexpired_tokens_alone(account_with_db, stub_mariadb, stub_group, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "pma_token_dir", str(tmp_path / "pma-tokens"))
    hdb.create_database({"username": "demo1", "name": "shop"})
    pma.create_token({"username": "demo1", "name": "shop"})

    cleaned = pma.cleanup_expired_tokens()
    assert cleaned == 0
    with write_session() as session:
        assert session.scalar(select(PmaToken)) is not None


def test_bootstrap_pma_files_generates_config_and_signon_script(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "pma_docroot", str(tmp_path / "pma-docroot"))
    monkeypatch.setattr(settings, "pma_token_dir", "/var/lib/boron-pma-tokens")
    (tmp_path / "pma-docroot").mkdir()

    secret = pma.bootstrap_pma_files()
    config_content = (tmp_path / "pma-docroot" / "config.inc.php").read_text()
    assert secret in config_content
    assert "auth_type'] = 'signon'" in config_content
    assert "/var/lib/boron-pma-tokens" not in config_content  # not templated into config.inc.php itself

    signon_content = (tmp_path / "pma-docroot" / "boron_signon.php").read_text()
    assert "/var/lib/boron-pma-tokens" in signon_content
    assert "PMASignon" in signon_content


def test_bootstrap_pma_files_reuses_existing_blowfish_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "pma_docroot", str(tmp_path / "pma-docroot"))
    monkeypatch.setattr(settings, "pma_token_dir", "/var/lib/boron-pma-tokens")
    (tmp_path / "pma-docroot").mkdir()

    first = pma.bootstrap_pma_files()
    second = pma.bootstrap_pma_files()
    assert first == second


def test_cleanup_keeps_failed_revocation_for_retry(account_with_db,stub_mariadb,stub_group,tmp_path,monkeypatch):
    import datetime as dt
    monkeypatch.setattr(settings,'pma_token_dir',str(tmp_path/'tokens'))
    hdb.create_database({'username':'demo1','name':'shop'})
    pma.create_token({'username':'demo1','name':'shop'})
    with write_session() as session:
        session.scalar(select(PmaToken)).expires_at -= dt.timedelta(hours=1)
    def fail(*args):raise RuntimeError('Database temporarily unavailable')
    monkeypatch.setattr(pma.mariadb,'drop_db_user',fail)
    assert pma.cleanup_expired_tokens()==0
    with write_session() as session:assert session.scalar(select(PmaToken)) is not None


def test_token_storage_failure_removes_temporary_login(account_with_db,stub_mariadb,stub_group,tmp_path,monkeypatch):
    from contextlib import contextmanager
    monkeypatch.setattr(settings,'pma_token_dir',str(tmp_path/'tokens'))
    hdb.create_database({'username':'demo1','name':'shop'})
    before=set(stub_mariadb)
    original=pma.write_session
    calls=0
    @contextmanager
    def fail_second():
        nonlocal calls
        calls+=1
        if calls==2:raise RuntimeError('Control database unavailable')
        with original() as session:yield session
    monkeypatch.setattr(pma,'write_session',fail_second)
    with pytest.raises(RuntimeError,match='Control database'):
        pma.create_token({'username':'demo1','name':'shop'})
    assert set(stub_mariadb)==before
    assert not list((tmp_path/'tokens').glob('*.json'))


def test_prepare_root_keeps_php_worker_from_owning_package(isolated_db,tmp_path,monkeypatch):
    from types import SimpleNamespace
    root=tmp_path/'pma';root.mkdir();(root/'index.php').write_text('<?php')
    monkeypatch.setattr(settings,'pma_hostname','pma.example.com')
    monkeypatch.setattr(settings,'pma_docroot',str(root))
    monkeypatch.setattr(pma.pwd,'getpwnam',lambda name:SimpleNamespace(pw_uid=990,pw_gid=990,pw_shell='/usr/sbin/nologin'))
    ownership=[]
    monkeypatch.setattr(pma.os,'chown',lambda path,uid,gid:ownership.append((path,uid,gid)))
    pma._prepare_service_root()
    assert ownership[0]==(root,990,990)
    assert root.stat().st_mode & 0o777==0o555
    assert all(uid==0 for path,uid,gid in ownership[1:])


def test_package_configuration_outside_webroot_is_used(tmp_path, monkeypatch):
    root = tmp_path / 'pma'
    (root / 'libraries').mkdir(parents=True)
    config = tmp_path / 'config.inc.php'
    (root / 'libraries/vendor_config.php').write_text("<?php return ['configFile' => '" + str(config) + "'];")
    monkeypatch.setattr(settings, 'pma_docroot', str(root))
    secret = pma.bootstrap_pma_files()
    assert "'auth_type'] = 'signon'" in config.read_text()
    assert not (root / 'config.inc.php').exists()
    assert pma.bootstrap_pma_files() == secret
