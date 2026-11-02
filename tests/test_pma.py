import json
import os

import pytest
from sqlalchemy import select

from daemon import handlers_account as ha
from daemon import handlers_database as hdb
from daemon import pma
from shared.config import settings
from shared.db import write_session
from shared.models import Account, PmaToken, utcnow
from shared.validation import NotFoundError, ValidationError


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
    monkeypatch.setattr(hdb.mariadb, "require_ungranted_database_name", lambda name: None)
    monkeypatch.setattr(hdb.mariadb, "user_exists", lambda name, host="localhost": name in users)
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


@pytest.fixture(autouse=True)
def published_pma(tmp_path, monkeypatch):
    """Sign-on tokens are issued only once phpMyAdmin is published; tests that
    exercise an unconfigured server override the hostname themselves."""
    docroot = tmp_path / "published-pma"
    docroot.mkdir()
    (docroot / "boron_signon.php").write_text("<?php\n")
    monkeypatch.setattr(settings, "pma_hostname", "pma.example")
    monkeypatch.setattr(settings, "pma_docroot", str(docroot))
    return docroot


@pytest.fixture()
def account_with_db(isolated_db, monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    ha.create_account({"username": "demo1"})
    return {}


def test_create_token_requires_existing_database(account_with_db, stub_mariadb, stub_group, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "pma_token_dir", str(tmp_path / "pma-tokens"))
    # A missing database is the caller's error (HTTP 404), not a 502.
    with pytest.raises(NotFoundError):
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
    before = set(stub_mariadb)
    result = pma.create_token({"username": "demo1", "name": "shop"})
    assert result["pma_url"] is None and result["available"] is False
    # Live 3.3.1: an unredeemable token still minted a MariaDB login whose
    # grant then blocked reuse of the database name after deletion.
    assert set(stub_mariadb) == before
    with write_session() as session:
        assert session.scalar(select(PmaToken)) is None


def test_create_token_before_phpmyadmin_is_published_mints_no_login(
    account_with_db, stub_mariadb, stub_group, tmp_path, monkeypatch, published_pma
):
    monkeypatch.setattr(settings, "pma_token_dir", str(tmp_path / "pma-tokens"))
    (published_pma / "boron_signon.php").unlink()
    hdb.create_database({"username": "demo1", "name": "shop"})
    before = set(stub_mariadb)
    assert pma.create_token({"username": "demo1", "name": "shop"})["pma_url"] is None
    assert set(stub_mariadb) == before


def test_dropping_database_revokes_its_sign_on_login(account_with_db, stub_mariadb, stub_group, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "pma_token_dir", str(tmp_path / "pma-tokens"))
    monkeypatch.setattr(hdb.mariadb, "revoke_all", lambda *args, **kwargs: None)
    hdb.create_database({"username": "demo1", "name": "shop"})
    hdb.create_database({"username": "demo1", "name": "blog"})
    pma.create_token({"username": "demo1", "name": "shop"})
    pma.create_token({"username": "demo1", "name": "blog"})
    with write_session() as session:
        users = {row.db_name: row.ephemeral_db_user for row in session.scalars(select(PmaToken)).all()}

    hdb.drop_database({"username": "demo1", "name": "shop"})

    assert users["demo1_shop"] not in stub_mariadb
    assert users["demo1_blog"] in stub_mariadb  # other databases keep their sign-on
    with write_session() as session:
        assert [row.db_name for row in session.scalars(select(PmaToken)).all()] == ["demo1_blog"]
    assert len(list((tmp_path / "pma-tokens").glob("*.json"))) == 1


def test_terminating_account_revokes_every_sign_on_login(account_with_db, stub_mariadb, stub_group, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "pma_token_dir", str(tmp_path / "pma-tokens"))
    hdb.create_database({"username": "demo1", "name": "shop"})
    pma.create_token({"username": "demo1", "name": "shop"})
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        session.expunge(account)
    hdb.terminate_account_databases(account)
    assert not any(name.startswith("boronpma_") for name in stub_mariadb)
    with write_session() as session:
        assert session.scalar(select(PmaToken)) is None
    assert list((tmp_path / "pma-tokens").glob("*.json")) == []


def test_orphaned_sign_on_grant_from_older_release_does_not_block_name_reuse(
    account_with_db, stub_mariadb, stub_group, monkeypatch
):
    grants = {"demo1_shop": [("boronpma_6_a650659a8bd3f69b", "localhost")]}
    stub_mariadb["boronpma_6_a650659a8bd3f69b"] = "x"

    def drop(user, host="localhost"):
        stub_mariadb.pop(user, None)
        for name in grants:
            grants[name] = [item for item in grants[name] if item[0] != user]

    def require(name):
        from daemon.mariadb import ConflictError
        if grants.get(name):
            raise ConflictError("still referenced")

    monkeypatch.setattr(hdb.mariadb, "drop_db_user", drop)
    monkeypatch.setattr(hdb.mariadb, "database_grantees", lambda name: list(grants.get(name, [])))
    monkeypatch.setattr(hdb.mariadb, "require_ungranted_database_name", require)
    result = hdb.create_database({"username": "demo1", "name": "shop"})
    assert result["db_name"] == "demo1_shop"
    assert "boronpma_6_a650659a8bd3f69b" not in stub_mariadb


def test_name_reuse_still_refused_for_non_sign_on_grants(account_with_db, stub_mariadb, monkeypatch):
    from shared.validation import ConflictError
    grants = [("other_app", "localhost"), ("boronpma_6_a650659a8bd3f69b", "203.0.113.5")]

    def require(name):
        raise ConflictError("still referenced")

    monkeypatch.setattr(hdb.mariadb, "database_grantees", lambda name: grants)
    monkeypatch.setattr(hdb.mariadb, "require_ungranted_database_name", require)
    with pytest.raises(ConflictError):
        hdb.create_database({"username": "demo1", "name": "shop"})


def test_create_token_validates_pma_hostname_before_temp_user(account_with_db, stub_mariadb, stub_group, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "pma_token_dir", str(tmp_path / "pma-tokens"))
    monkeypatch.setattr(settings, "pma_hostname", "bad/host")
    hdb.create_database({"username": "demo1", "name": "shop"})
    before = set(stub_mariadb)

    with pytest.raises(ValidationError):
        pma.create_token({"username": "demo1", "name": "shop"})

    assert set(stub_mariadb) == before
    assert not (tmp_path / "pma-tokens").exists()
    with write_session() as session:
        assert session.scalar(select(PmaToken)) is None


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


def test_reissuing_same_database_reuses_governed_principal_and_resource_window(
    account_with_db, stub_mariadb, stub_group, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "pma_token_dir", str(tmp_path / "pma-tokens"))
    hdb.create_database({"username": "demo1", "name": "shop"})
    applied = []
    monkeypatch.setattr(pma.db_governor, "apply_new_user", lambda *args: applied.append(args))

    pma.create_token({"username": "demo1", "name": "shop"})
    first = json.loads(next((tmp_path / "pma-tokens").glob("*.json")).read_text())
    pma.create_token({"username": "demo1", "name": "shop"})
    files = list((tmp_path / "pma-tokens").glob("*.json"))
    second = json.loads(files[0].read_text())

    assert len(files) == 1
    assert first["db_user"] == second["db_user"]
    assert first["db_password"] == second["db_password"]
    assert len(applied) == 1
    with write_session() as session:
        assert len(session.scalars(select(PmaToken)).all()) == 1


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
    monkeypatch.setattr(pma.os, "chown", lambda *args: None)
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
    monkeypatch.setattr(pma.os, "chown", lambda *args: None)
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


def test_governor_failure_never_publishes_temporary_login(account_with_db, stub_mariadb, stub_group, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "pma_token_dir", str(tmp_path / "tokens"))
    hdb.create_database({"username": "demo1", "name": "shop"})
    before = set(stub_mariadb)
    monkeypatch.setattr(
        pma.db_governor,
        "apply_new_user",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("governor unavailable")),
    )

    with pytest.raises(RuntimeError, match="governor unavailable"):
        pma.create_token({"username": "demo1", "name": "shop"})

    assert set(stub_mariadb) == before
    assert not list((tmp_path / "tokens").glob("*.json"))
    with write_session() as session:
        assert session.scalar(select(PmaToken)) is None


def test_token_dir_refuses_symlink_without_chowning_target(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    target.chmod(0o755)
    link = tmp_path / "tokens"
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(settings, "pma_token_dir", str(link))

    with pytest.raises(pma.PmaError, match="symbolic link"):
        pma._token_dir()

    assert target.stat().st_mode & 0o777 == 0o755


def test_preexisting_token_path_does_not_follow_symlink(account_with_db, stub_mariadb, stub_group, tmp_path, monkeypatch):
    import hashlib

    token = "KnownPmaTokenValueForCollisionTest"
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    canary = tmp_path / "canary.json"
    canary.write_text("do-not-overwrite")
    (token_dir / f"{token_hash}.json").symlink_to(canary)
    monkeypatch.setattr(settings, "pma_token_dir", str(token_dir))
    monkeypatch.setattr(settings, "pma_hostname", "pma.example")
    monkeypatch.setattr(pma.secrets, "token_urlsafe", lambda _n: token)
    hdb.create_database({"username": "demo1", "name": "shop"})
    before = set(stub_mariadb)

    with pytest.raises(FileExistsError):
        pma.create_token({"username": "demo1", "name": "shop"})

    assert canary.read_text() == "do-not-overwrite"
    assert set(stub_mariadb) == before
    assert not os.path.lexists(token_dir / f"{token_hash}.json")


def test_cleanup_invalid_token_hash_does_not_unlink_outside_token_dir(account_with_db, stub_mariadb, stub_group, tmp_path, monkeypatch):
    import datetime as dt

    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("keep")
    monkeypatch.setattr(settings, "pma_token_dir", str(token_dir))
    user = "boronphpmyadminlogin_badrow"
    stub_mariadb[user] = "pw"
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        session.add(
            PmaToken(
                token_hash="../outside",
                account_id=account.id,
                db_name="demo1_shop",
                ephemeral_db_user=user,
                expires_at=utcnow() - dt.timedelta(minutes=1),
            )
        )

    assert pma.cleanup_expired_tokens() == 1
    assert outside.read_text() == "keep"
    assert user not in stub_mariadb
    with write_session() as session:
        assert session.scalar(select(PmaToken)) is None


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
    monkeypatch.setattr(pma.os, "chown", lambda *args: None)
    secret = pma.bootstrap_pma_files()
    assert "'auth_type'] = 'signon'" in config.read_text()
    assert not (root / 'config.inc.php').exists()
    assert pma.bootstrap_pma_files() == secret


def test_renaming_database_revokes_the_old_names_sign_on_login(
    account_with_db, stub_mariadb, stub_group, tmp_path, monkeypatch
):
    """The old name's sign-on login kept native grants on a name that no
    longer exists, which blocked its reuse exactly like a deleted database."""
    from daemon import snapshot_databases
    monkeypatch.setattr(settings, "pma_token_dir", str(tmp_path / "pma-tokens"))
    hdb.create_database({"username": "demo1", "name": "shop"})
    pma.create_token({"username": "demo1", "name": "shop"})
    with write_session() as session:
        old_login = session.scalar(select(PmaToken.ephemeral_db_user))
    assert old_login in stub_mariadb

    monkeypatch.setattr(hdb, "_require_safety_capacity", lambda *args, **kwargs: None)
    monkeypatch.setattr(hdb.mariadb, "database_statistics",
                        lambda names: {name: {"table_count": 1, "size_bytes": 0} for name in names})
    monkeypatch.setattr(hdb.mariadb, "grant_database_privileges", lambda *args, **kwargs: None)
    monkeypatch.setattr(hdb.mariadb, "revoke_all", lambda *args, **kwargs: None)
    monkeypatch.setattr(snapshot_databases, "dump_database",
                        lambda name, output, work, **kwargs: output.write_text("-- dump"))
    monkeypatch.setattr(snapshot_databases, "restore_database",
                        lambda *args, **kwargs: {"database": args[0], "bytes_read": 1})

    result = hdb.rename_database({"username": "demo1", "name": "shop", "new_name": "store"})

    assert result["db_name"] == "demo1_store"
    assert old_login not in stub_mariadb
    with write_session() as session:
        assert session.scalar(select(PmaToken)) is None


def test_install_package_refreshes_stale_package_lists_once(tmp_path, monkeypatch):
    """Upgraded servers can hold package lists older than the mirror."""
    from types import SimpleNamespace
    from daemon import procutil
    docroot = tmp_path / "phpmyadmin"
    monkeypatch.setattr(settings, "pma_docroot", str(docroot))
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args[:2] if args[0] == "apt-get" else args[:1])
        if args[:2] == ["apt-get", "install"]:
            if kwargs.get("check"):
                docroot.mkdir()
                (docroot / "index.php").write_text("<?php\n")
                return SimpleNamespace(ok=True)
            return SimpleNamespace(ok=False)
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(procutil, "run", fake_run)
    assert pma.install_package() is True
    assert calls == [["debconf-set-selections"], ["apt-get", "install"], ["apt-get", "-o"], ["apt-get", "install"]]
    calls.clear()
    assert pma.install_package() is False  # already installed: nothing runs
    assert calls == []


def test_provision_publishes_only_when_a_hostname_is_configured(isolated_db, monkeypatch):
    published = []
    monkeypatch.setattr(pma, "install_package", lambda **kwargs: True)
    monkeypatch.setattr(pma, "bootstrap_pma", lambda: published.append(True))
    monkeypatch.setattr(settings, "pma_hostname", "")
    assert pma.provision() == {"installed": True, "published": False, "hostname": None}
    assert published == []
    assert pma.provision_state()["status"] == "installed"
    monkeypatch.setattr(settings, "pma_hostname", "phpmyadmin.example.com")
    assert pma.provision() == {"installed": True, "published": True, "hostname": "phpmyadmin.example.com"}
    assert published == [True]
    assert pma.provision_state()["status"] == "published"


@pytest.mark.parametrize("components,has_mariadb,expected", [
    (("database", "web"), True, 1),
    (("web", "mail"), True, 0),       # no database component: never install phpMyAdmin
    (("database",), False, 0),        # component enabled but MariaDB absent
])
def test_upgrade_provisions_phpmyadmin_only_on_database_servers(monkeypatch, components, has_mariadb, expected):
    from scripts import upgrade_runtime
    calls = []
    monkeypatch.setattr(settings, "enabled_components", components)
    monkeypatch.setattr(pma, "provision", lambda **kwargs: calls.append(kwargs) or {})
    monkeypatch.setattr(upgrade_runtime.shutil, "which", lambda name: "/usr/bin/mariadb" if has_mariadb else None)
    monkeypatch.setattr(upgrade_runtime.Path, "exists", lambda self: False)
    upgrade_runtime.configure_phpmyadmin()
    assert calls == [{"source": "upgrade", "budget_seconds": pma.UPGRADE_INSTALL_BUDGET_SECONDS}] * expected


def test_fresh_install_provisions_phpmyadmin_for_the_database_profile():
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "scripts" / "install.sh").read_text()
    # boron.toml gets the hostname only once the package is installed: the
    # phpMyAdmin vhost it adds would otherwise fail bootstrap_baseline's
    # OLS config test on the missing document root.
    assert 'pma_hostname = ""' in source and 'pma_hostname = "${PMA_DOMAIN}"' not in source
    assert "profile_has database && setup_phpmyadmin" in source
    body = source[source.index("setup_phpmyadmin() {"):]
    body = body[:body.index("\n}\n")]
    assert "from daemon.pma import provision" in body
    assert "provision(os.environ.get('BORON_PMA_HOSTNAME') or None)" in body
    # The daemon reloads the saved hostname; a failed install only warns.
    assert "run systemctl restart boron-provisiond" in body
    assert 'warn "phpMyAdmin could not be installed' in body and "die " not in body
