"""Per-account PHP extension enable/disable (daemon/phpext.py).

Discovery is exercised against a fake /usr/local/lsws tree under tmp_path
(the real one's inventory differs per box and per lsphp point release), and
scan dirs are materialized under a tmp home_base -- no test touches the
live filesystem or OLS.
"""
import os
from pathlib import Path

import pytest

from daemon import handlers_account as ha
from daemon import phpext
from shared.config import settings
from shared.validation import ValidationError


@pytest.fixture()
def fake_lsws(tmp_path, monkeypatch):
    """Two fake lsphp builds: 8.3 has redis+mysqli (both stock-enabled via
    mods-available snippets) plus a pgsql.so that ships NO snippet
    (stock-disabled, enable generates one); 8.1 only has mysqli -- so the
    'not built for this version' skip path is covered."""
    base = tmp_path / "lsws"
    for version, sos, inis in (
        ("8.3", ["redis", "mysqli", "pgsql"], {"50-redis.ini": "extension=redis.so\n", "mysqli.ini": "extension=mysqli.so\n"}),
        ("8.1", ["mysqli"], {"mysqli.ini": "extension=mysqli.so\n"}),
    ):
        nodot = version.replace(".", "")
        so_dir = base / f"lsphp{nodot}/lib/php/20230831"
        so_dir.mkdir(parents=True)
        for name in sos:
            (so_dir / f"{name}.so").touch()
        mods = base / f"lsphp{nodot}/etc/php/{version}/mods-available"
        mods.mkdir(parents=True)
        for filename, content in inis.items():
            (mods / filename).write_text(content)
    monkeypatch.setattr(phpext, "LSWS_BASE", str(base))
    monkeypatch.setattr(settings, "php_versions", ("8.1", "8.3"))
    # The real dep table references igbinary, which this fake tree doesn't
    # ship -- neutralize it here; test_set_extensions_enforces_dependencies
    # restores a table scoped to the fake inventory.
    monkeypatch.setattr(phpext, "EXTENSION_DEPS", {})
    return base


@pytest.fixture()
def account(isolated_db, tmp_path, monkeypatch, fake_lsws):
    monkeypatch.setattr(settings, "home_base", str(tmp_path / "home"))
    (tmp_path / "home" / "demo1").mkdir(parents=True)
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    ha.create_account({"username": "demo1"})


@pytest.fixture()
def stub_apply(monkeypatch):
    calls = []
    monkeypatch.setattr(phpext.ols, "refresh_vhost", lambda account: calls.append(("refresh", account.username)))
    monkeypatch.setattr(phpext.sysops, "recycle_php_workers", lambda username: calls.append(("recycle", username)))
    return calls


def test_inventory_discovers_per_version_extensions(fake_lsws):
    inv = phpext.inventory()
    assert inv["redis"] == {"versions": ["8.3"], "stock_enabled": True}
    assert inv["mysqli"] == {"versions": ["8.1", "8.3"], "stock_enabled": True}
    assert inv["pgsql"] == {"versions": ["8.3"], "stock_enabled": False}


def test_list_extensions_defaults_to_stock(account, stub_apply):
    result = phpext.list_extensions({"username": "demo1"})
    assert result["overridden"] is False
    assert result["enabled"] == ["mysqli", "redis"]  # stock set, pgsql has no snippet
    by_name = {e["name"]: e for e in result["extensions"]}
    assert by_name["pgsql"]["enabled"] is False


def test_set_extensions_materializes_scan_dirs(account, stub_apply):
    result = phpext.set_extensions({"username": "demo1", "enabled": ["mysqli", "pgsql"]})
    assert result["overridden"] is True
    assert result["enabled"] == ["mysqli", "pgsql"]

    scan_83 = Path(f"{settings.home_base}/demo1/.php/83/conf.d")
    # stock snippet -> symlink preserving the packaged filename/load order
    assert (scan_83 / "mysqli.ini").is_symlink()
    # .so without a stock snippet -> generated one-liner
    assert (scan_83 / "pgsql.ini").read_text() == "extension=pgsql.so\n"
    # disabled extension's snippet must NOT be present
    assert not (scan_83 / "50-redis.ini").exists()
    # pgsql isn't built for 8.1 -- skipped there, mysqli still linked
    scan_81 = Path(f"{settings.home_base}/demo1/.php/81/conf.d")
    assert (scan_81 / "mysqli.ini").is_symlink()
    assert not (scan_81 / "pgsql.ini").exists()
    # the account must be able to read but never write these dirs
    assert (os.stat(scan_83).st_mode & 0o777) == 0o755

    # vhost refresh + warm-worker recycle both happened
    assert ("refresh", "demo1") in stub_apply
    assert ("recycle", "demo1") in stub_apply


def test_set_extensions_rewrites_stale_snippets(account, stub_apply):
    phpext.set_extensions({"username": "demo1", "enabled": ["mysqli", "redis"]})
    phpext.set_extensions({"username": "demo1", "enabled": ["mysqli"]})
    scan_83 = Path(f"{settings.home_base}/demo1/.php/83/conf.d")
    assert not (scan_83 / "50-redis.ini").exists()


def test_set_extensions_rejects_unknown_name(account, stub_apply):
    with pytest.raises(ValidationError):
        phpext.set_extensions({"username": "demo1", "enabled": ["mysqli", "evil/../../etc"]})


def test_set_extensions_enforces_dependencies(account, stub_apply, monkeypatch):
    """A set that loads an extension without its required sibling (this
    box's phpredis needs igbinary) must be rejected up front -- PHP would
    otherwise accept the config and silently skip the extension with only
    a startup warning."""
    monkeypatch.setattr(phpext, "EXTENSION_DEPS", {"redis": ("mysqli",)})
    with pytest.raises(ValidationError):
        phpext.set_extensions({"username": "demo1", "enabled": ["redis"]})
    result = phpext.set_extensions({"username": "demo1", "enabled": ["redis", "mysqli"]})
    assert result["enabled"] == ["mysqli", "redis"]


def test_reset_extensions_removes_override_and_dirs(account, stub_apply):
    phpext.set_extensions({"username": "demo1", "enabled": ["mysqli"]})
    result = phpext.reset_extensions({"username": "demo1"})
    assert result["overridden"] is False
    assert result["enabled"] == ["mysqli", "redis"]  # back to stock
    assert not Path(f"{settings.home_base}/demo1/.php").exists()


def test_reset_extensions_idempotent_when_never_set(account, stub_apply):
    phpext.reset_extensions({"username": "demo1"})  # must not raise


def test_terminate_account_php_extensions(account, stub_apply):
    phpext.set_extensions({"username": "demo1", "enabled": ["mysqli"]})

    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import Account

    with write_session() as session:
        acct = session.scalar(select(Account).where(Account.username == "demo1"))
        phpext.terminate_account_php_extensions(acct)

    assert phpext.list_extensions({"username": "demo1"})["overridden"] is False


def test_bootstrap_rematerializes_missing_dirs(account, stub_apply):
    phpext.set_extensions({"username": "demo1", "enabled": ["mysqli"]})
    import shutil

    shutil.rmtree(f"{settings.home_base}/demo1/.php")
    phpext.bootstrap_all_php_extensions()
    assert Path(f"{settings.home_base}/demo1/.php/83/conf.d/mysqli.ini").is_symlink()
