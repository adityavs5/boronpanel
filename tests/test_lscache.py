import os

import pytest
from sqlalchemy import select

from daemon import handlers_account as ha
from daemon import handlers_domain as hd
from daemon import lscache
from shared.db import write_session
from shared.models import Account, Domain, LscacheSettings
from shared.validation import ValidationError


@pytest.fixture()
def stub_ols(monkeypatch):
    calls = []
    monkeypatch.setattr(lscache.ols, "refresh_vhost", lambda account: calls.append(account.username))
    return calls


@pytest.fixture()
def account_with_domain(isolated_db, tmp_path, monkeypatch, stub_ols):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    ha.create_account({"username": "demo1"})

    docroot = tmp_path / "public_html"
    docroot.mkdir()

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        session.add(Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot=str(docroot)))

    monkeypatch.setattr(lscache, "CACHE_STORE_ROOT", tmp_path / "cachedata")
    return {"docroot": docroot}


def test_get_settings_defaults_when_never_configured(account_with_domain):
    result = lscache.get_settings({"domain": "demo1.example"})
    assert result["enabled"] is False
    assert result["ttl_seconds"] == 3600
    assert result["exclude_paths"] == []
    assert result["wordpress_detected"] is False


def test_set_settings_enables_and_calls_refresh_vhost(account_with_domain, stub_ols):
    result = lscache.set_settings({"domain": "demo1.example", "enabled": True, "ttl_seconds": 7200, "exclude_paths": ["/cart", "/checkout"]})
    assert result["enabled"] is True
    assert result["ttl_seconds"] == 7200
    assert result["exclude_paths"] == ["/cart", "/checkout"]
    assert "demo1" in stub_ols


def test_set_settings_upserts_not_duplicates(account_with_domain):
    lscache.set_settings({"domain": "demo1.example", "enabled": True, "ttl_seconds": 60})
    lscache.set_settings({"domain": "demo1.example", "enabled": True, "ttl_seconds": 120})
    with write_session() as session:
        rows = session.scalars(select(LscacheSettings).where(LscacheSettings.domain == "demo1.example")).all()
        assert len(rows) == 1
        assert rows[0].ttl_seconds == 120


def test_set_settings_rejects_ttl_out_of_range(account_with_domain):
    with pytest.raises(ValidationError):
        lscache.set_settings({"domain": "demo1.example", "enabled": True, "ttl_seconds": 5})
    with pytest.raises(ValidationError):
        lscache.set_settings({"domain": "demo1.example", "enabled": True, "ttl_seconds": 99999999})


def test_set_settings_rejects_invalid_exclude_path(account_with_domain):
    with pytest.raises(ValidationError):
        lscache.set_settings({"domain": "demo1.example", "enabled": True, "exclude_paths": ["no-leading-slash"]})


def test_wordpress_detection_no_plugin_shows_reminder(account_with_domain):
    (account_with_domain["docroot"] / "wp-config.php").write_text("<?php")
    result = lscache.get_settings({"domain": "demo1.example"})
    assert result["wordpress_detected"] is True
    assert result["litespeed_cache_plugin_installed"] is False
    assert "LiteSpeed Cache plugin is not installed" in result["plugin_reminder"]


def test_wordpress_detection_with_plugin_no_reminder(account_with_domain):
    docroot = account_with_domain["docroot"]
    (docroot / "wp-config.php").write_text("<?php")
    plugin_dir = docroot / "wp-content" / "plugins" / "litespeed-cache"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "litespeed-cache.php").write_text("<?php")
    result = lscache.get_settings({"domain": "demo1.example"})
    assert result["litespeed_cache_plugin_installed"] is True
    assert result["plugin_reminder"] is None


def test_purge_requires_enabled_first(account_with_domain):
    with pytest.raises(RuntimeError):
        lscache.purge({"domain": "demo1.example"})


def test_purge_removes_cached_files_and_records_timestamp(account_with_domain):
    lscache.set_settings({"domain": "demo1.example", "enabled": True})
    storage_dir = lscache.cache_storage_path("demo1.example")
    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / "cached-object-1").write_text("data")
    assert any(storage_dir.iterdir())

    result = lscache.purge({"domain": "demo1.example"})
    assert result["last_purged_at"] is not None
    assert not any(storage_dir.iterdir())


def test_purge_preserves_storage_directory_ownership_and_mode(account_with_domain):
    """Real bug found live during this feature's own verification: OLS's
    own worker process ("nobody") creates storagepath itself with specific
    ownership/permissions (setgid, group-writable) it needs to keep
    writing new cache entries -- an earlier purge() implementation
    recreated the directory from scratch (root:root, default mode),
    silently blocking every future cache write. purge() must only ever
    clear the directory's CONTENTS, never replace the directory itself."""
    lscache.set_settings({"domain": "demo1.example", "enabled": True})
    storage_dir = lscache.cache_storage_path("demo1.example")
    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / "cached-object").write_text("data")
    os.chmod(storage_dir, 0o2775)  # simulate OLS's own real-world setgid dir mode

    import stat as statmod

    before = os.stat(storage_dir)
    lscache.purge({"domain": "demo1.example"})
    after = os.stat(storage_dir)

    assert statmod.S_IMODE(after.st_mode) == statmod.S_IMODE(before.st_mode)
    assert after.st_ino == before.st_ino  # same directory, not recreated
    assert not any(storage_dir.iterdir())


def test_get_stats_counts_cached_objects(account_with_domain):
    lscache.set_settings({"domain": "demo1.example", "enabled": True})
    storage_dir = lscache.cache_storage_path("demo1.example")
    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / "a").write_text("1")
    (storage_dir / "b").write_text("2")

    result = lscache.get_stats({"domain": "demo1.example"})
    assert result["cached_object_count"] == 2


def test_get_stats_zero_when_never_enabled(account_with_domain):
    result = lscache.get_stats({"domain": "demo1.example"})
    assert result["cached_object_count"] == 0
    assert result["last_purged_at"] is None


def test_delete_settings_for_domain_removes_row_and_cache_dir(account_with_domain):
    lscache.set_settings({"domain": "demo1.example", "enabled": True})
    storage_dir = lscache.cache_storage_path("demo1.example")
    storage_dir.mkdir(parents=True, exist_ok=True)

    lscache.delete_settings_for_domain("demo1.example")

    with write_session() as session:
        assert session.scalar(select(LscacheSettings).where(LscacheSettings.domain == "demo1.example")) is None
    assert not storage_dir.exists()


def test_remove_domain_cleans_up_lscache_settings(account_with_domain, monkeypatch):
    monkeypatch.setattr(hd.ols, "remove_domain_vhost", lambda account, domain_name: None)
    monkeypatch.setattr(hd.powerdns, "delete_record", lambda *a, **k: None)
    monkeypatch.setattr(hd, "_find_parent_zone", lambda domain_name: None)

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        session.add(Domain(account_id=account.id, domain="addon.example", kind="addon", docroot="/home/demo1/addon.example"))

    lscache.set_settings({"domain": "addon.example", "enabled": True})
    hd.remove_domain({"username": "demo1", "domain": "addon.example"})

    with write_session() as session:
        assert session.scalar(select(LscacheSettings).where(LscacheSettings.domain == "addon.example")) is None
