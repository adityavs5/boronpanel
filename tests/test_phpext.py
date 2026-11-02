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
from daemon import phpdirectives, phpext
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


def _scan(*parts):
    return Path(phpdirectives.PHP_SCAN_ROOT, *parts)


@pytest.fixture()
def account(isolated_db, tmp_path, monkeypatch, fake_lsws):
    monkeypatch.setattr(settings, "home_base", str(tmp_path / "home"))
    (tmp_path / "usr-local-lib").mkdir(mode=0o755)
    monkeypatch.setattr(phpdirectives, "PHP_SCAN_ROOT", str(tmp_path / "usr-local-lib" / "php-scan"))
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

    scan_83 = _scan("demo1/83/conf.d")
    # stock snippet -> symlink preserving the packaged filename/load order
    assert (scan_83 / "mysqli.ini").is_symlink()
    # .so without a stock snippet -> generated one-liner
    assert (scan_83 / "pgsql.ini").read_text() == "extension=pgsql.so\n"
    # disabled extension's snippet must NOT be present
    assert not (scan_83 / "50-redis.ini").exists()
    # pgsql isn't built for 8.1 -- skipped there, mysqli still linked
    scan_81 = _scan("demo1/81/conf.d")
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
    scan_83 = _scan("demo1/83/conf.d")
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
    assert not _scan("demo1").exists()
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

    shutil.rmtree(_scan("demo1"))
    phpext.bootstrap_all_php_extensions()
    assert _scan("demo1/83/conf.d/mysqli.ini").is_symlink()


@pytest.mark.parametrize('component', ['demo1', 'demo1/81', 'demo1/81/conf.d'])
def test_extension_materialization_rejects_symlink_ancestors(account, stub_apply, tmp_path, component):
    home=_scan()
    home.mkdir(mode=0o711, exist_ok=True)
    outside=tmp_path/'protected'
    (outside/'81/conf.d').mkdir(parents=True)
    marker=outside/'81/conf.d/keep.ini'
    marker.write_text('protected canary')
    outside.chmod(0o700)
    link=home/component
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(phpext.UnsafePathError):
        phpext.set_extensions({'username':'demo1','enabled':['mysqli','pgsql']})
    assert marker.read_text()=='protected canary'
    assert outside.stat().st_mode & 0o777 == 0o700
    assert not (outside/'pgsql.ini').exists()


def test_reset_unlinks_scan_root_symlink_without_following(account, stub_apply, tmp_path):
    home=Path(settings.home_base)/'demo1'
    outside=tmp_path/'protected';outside.mkdir()
    marker=outside/'keep.ini';marker.write_text('protected canary')
    (home/'.php').symlink_to(outside, target_is_directory=True)
    phpext.reset_extensions({'username':'demo1'})
    assert marker.read_text()=='protected canary'
    assert not (home/'.php').is_symlink()


def test_extension_writes_stay_on_open_directory_after_swap(account, stub_apply, monkeypatch, tmp_path):
    home=_scan('demo1')
    outside=tmp_path/'protected';outside.mkdir()
    marker=outside/'keep.ini';marker.write_text('protected canary')
    original=phpext.open_dir_beneath
    swapped=False
    def swap(root, relative='.'):
        nonlocal swapped
        fd=original(root, relative)
        if relative.endswith('83/conf.d') and not swapped:
            directory=Path(root)/relative
            directory.rename(directory.with_name('conf.retained'))
            directory.symlink_to(outside, target_is_directory=True)
            swapped=True
        return fd
    monkeypatch.setattr(phpext,'open_dir_beneath',swap)
    phpext.set_extensions({'username':'demo1','enabled':['mysqli','pgsql']})
    assert swapped and marker.read_text()=='protected canary'
    assert sorted(p.name for p in outside.iterdir())==['keep.ini']
    assert (home/'83/conf.retained/pgsql.ini').read_text()=='extension=pgsql.so\n'


def test_scan_dir_is_outside_the_account_home(account, stub_apply):
    """Regression: the scan dir used to be ~/.php under the account-owned
    home, which the account could rename away and recreate with its own
    ini files (disable_functions=, extension=/home/u/x.so)."""
    phpext.set_extensions({"username": "demo1", "enabled": ["mysqli"]})
    rendered = phpdirectives.php_scan_dir("demo1", "8.3")
    assert rendered == str(_scan("demo1/83/conf.d"))
    assert not rendered.startswith(settings.home_base.rstrip("/") + "/")
    assert not Path(f"{settings.home_base}/demo1/.php").exists()
    # An account-planted ~/.php is never what PHP_INI_SCAN_DIR points at.
    planted = Path(f"{settings.home_base}/demo1/.php/83/conf.d")
    planted.mkdir(parents=True)
    (planted / "evil.ini").write_text("disable_functions=\n")
    phpext.set_extensions({"username": "demo1", "enabled": ["mysqli", "pgsql"]})
    assert sorted(p.name for p in _scan("demo1/83/conf.d").iterdir()) == ["mysqli.ini", "pgsql.ini"]
    assert (os.stat(_scan()).st_mode & 0o777) == 0o711


def test_scan_root_must_not_be_group_or_world_writable(account, stub_apply):
    _scan().mkdir(mode=0o711)
    os.chmod(_scan(), 0o777)
    with pytest.raises(phpext.UnsafePathError):
        phpext.set_extensions({"username": "demo1", "enabled": ["mysqli"]})


def test_scan_root_rejects_symlinked_root(account, stub_apply, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _scan().symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(phpext.UnsafePathError):
        phpext.set_extensions({"username": "demo1", "enabled": ["mysqli"]})
    assert list(elsewhere.iterdir()) == []


def _legacy_tree():
    legacy = Path(f"{settings.home_base}/demo1/.php/83/conf.d")
    legacy.mkdir(parents=True)
    (legacy / "mysqli.ini").write_text("extension=mysqli.so\n")
    return legacy.parent.parent


def test_bootstrap_migrates_legacy_home_scan_dirs(account, stub_apply, monkeypatch):
    phpext.set_extensions({"username": "demo1", "enabled": ["mysqli"]})
    legacy = _legacy_tree()
    import shutil
    shutil.rmtree(_scan("demo1"))
    order = []
    monkeypatch.setattr(phpext.ols, "refresh_main_config",
                        lambda: order.append(("refresh", _scan("demo1/83/conf.d/mysqli.ini").is_symlink(), legacy.exists())))
    monkeypatch.setattr(phpext.sysops, "recycle_php_workers", lambda username: order.append(("recycle", username)))
    phpext.bootstrap_all_php_extensions()
    # new dir exists before the config is repointed; old one survives until after
    assert order == [("refresh", True, True), ("recycle", "demo1")]
    assert not legacy.exists()
    assert _scan("demo1/83/conf.d/mysqli.ini").is_symlink()
    # second boot: nothing legacy left, so no extra OLS reload
    order.clear()
    phpext.bootstrap_all_php_extensions()
    assert order == []


def test_bootstrap_repoints_config_even_without_root_owned_legacy_tree(account, stub_apply, monkeypatch, tmp_path):
    """An account that already swapped ~/.php for its own leaves no root-owned
    tree, but the live httpd_config still points PHP at it: still refresh."""
    phpext.set_extensions({"username": "demo1", "enabled": ["mysqli"]})
    config = tmp_path / "httpd_config.conf"
    config.write_text(f"    env PHP_INI_SCAN_DIR={settings.home_base}/demo1/.php/83/conf.d\n")
    monkeypatch.setattr(phpext.ols, "HTTPD_CONFIG_PATH", str(config))
    monkeypatch.setattr(phpext, "_legacy_scan_tree_present", lambda username: False)
    order = []
    monkeypatch.setattr(phpext.ols, "refresh_main_config", lambda: order.append("refresh"))
    monkeypatch.setattr(phpext.sysops, "recycle_php_workers", lambda username: order.append(("recycle", username)))
    phpext.bootstrap_all_php_extensions()
    assert order == ["refresh", ("recycle", "demo1")]
    config.write_text(f"    env PHP_INI_SCAN_DIR={phpdirectives.PHP_SCAN_ROOT}/demo1/83/conf.d\n")
    order.clear()
    phpext.bootstrap_all_php_extensions()
    assert order == []


def test_bootstrap_keeps_legacy_tree_when_config_refresh_fails(account, stub_apply, monkeypatch):
    phpext.set_extensions({"username": "demo1", "enabled": ["mysqli"]})
    legacy = _legacy_tree()

    def fail():
        raise RuntimeError("ols validate failed")

    monkeypatch.setattr(phpext.ols, "refresh_main_config", fail)
    phpext.bootstrap_all_php_extensions()
    assert legacy.exists()


def test_rename_moves_scan_dirs(account, stub_apply):
    phpext.set_extensions({"username": "demo1", "enabled": ["mysqli"]})
    phpext.rename_scan_dirs("demo1", "demo2")
    assert not _scan("demo1").exists()
    assert _scan("demo2/83/conf.d/mysqli.ini").is_symlink()
    phpext.rename_scan_dirs("demo2", "demo1")
    assert _scan("demo1/83/conf.d/mysqli.ini").is_symlink()
    phpext.rename_scan_dirs("nobodyhere", "demo3")  # no override: no-op


def test_terminate_removes_scan_dirs(account, stub_apply):
    phpext.set_extensions({"username": "demo1", "enabled": ["mysqli"]})
    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import Account

    with write_session() as session:
        acct = session.scalar(select(Account).where(Account.username == "demo1"))
        phpext.terminate_account_php_extensions(acct)
    assert not _scan("demo1").exists()
