from __future__ import annotations

import os

import pytest

from daemon.panel_config import render_ports
from shared.config import (
    ConfigError,
    CURRENT_CONFIG_SCHEMA_VERSION,
    load_settings,
    migrate_config_file,
)


def test_previous_config_loads_and_migrates_without_losing_unknown_values(tmp_path, caplog):
    config = tmp_path / "boron.toml"
    config.write_text(
        "# operator comment\n"
        "panel_hostname = \"panel.example.test\"\n"
        "api_bind_port = 2443\n"
        "future_release_key = \"keep-me\"\n"
    )
    config.chmod(0o640)
    secrets = tmp_path / "secrets.env"
    secrets.write_text("TEST_SENTINEL=do-not-touch\n")
    secrets.chmod(0o600)
    secret_before = (secrets.read_bytes(), secrets.stat().st_mode, secrets.stat().st_uid, secrets.stat().st_gid)

    loaded = load_settings(config, secrets, tmp_path / "missing-api-secrets")
    assert loaded.panel_hostname == "panel.example.test"
    assert loaded.api_bind_port == 2443
    assert loaded.unknown_config == {"future_release_key": "keep-me"}
    assert "future_release_key" in caplog.text

    metadata = config.stat()
    result = migrate_config_file(config)
    assert result["changed"] is True
    text = config.read_text()
    assert "# operator comment" in text
    assert 'future_release_key = "keep-me"' in text
    assert f"config_schema_version = {CURRENT_CONFIG_SCHEMA_VERSION}" in text
    assert (config.stat().st_mode, config.stat().st_uid, config.stat().st_gid) == (
        metadata.st_mode, metadata.st_uid, metadata.st_gid
    )
    assert secret_before == (secrets.read_bytes(), secrets.stat().st_mode, secrets.stat().st_uid, secrets.stat().st_gid)
    assert migrate_config_file(config)["changed"] is False


def test_removed_config_key_is_mapped_and_persisted(tmp_path):
    config = tmp_path / "boron.toml"
    config.write_text("config_version = 0\npanel_hostname = \"panel.example.test\"\n")
    loaded = load_settings(config, tmp_path / "none", tmp_path / "none-api")
    assert loaded.config_schema_version == 1
    migrate_config_file(config)
    assert "config_version" not in config.read_text()
    assert "config_schema_version = 1" in config.read_text()


@pytest.mark.parametrize(
    ("document", "key"),
    [
        ('api_bind_port = "2222"\n', "api_bind_port"),
        ('config_schema_version = "1"\n', "config_schema_version"),
    ],
)
def test_bad_config_names_the_key(document, key, tmp_path):
    config = tmp_path / "bad.toml"
    config.write_text(document)
    with pytest.raises(ConfigError, match=key):
        load_settings(config, tmp_path / "none", tmp_path / "none-api")


def test_future_config_schema_is_rejected(tmp_path):
    config = tmp_path / "future.toml"
    config.write_text("config_schema_version = 99\n")
    with pytest.raises(ConfigError, match="newer than this panel supports"):
        load_settings(config, tmp_path / "none", tmp_path / "none-api")


def test_install_profile_and_component_values_are_validated(tmp_path):
    config = tmp_path / "bad-profile.toml"
    config.write_text('config_schema_version = 1\ninstall_profile = "enterprise"\n')
    with pytest.raises(ConfigError, match="install_profile"):
        load_settings(config, tmp_path / "none", tmp_path / "none-api")
    config.write_text('config_schema_version = 1\ninstall_profile = "minimal"\nenabled_components = ["web", "mystery"]\n')
    with pytest.raises(ConfigError, match="enabled_components"):
        load_settings(config, tmp_path / "none", tmp_path / "none-api")


def test_panel_writer_preserves_unknown_future_key():
    original = (
        "config_schema_version = 1\n"
        "api_bind_port = 2222\n"
        "api_customer_port = 2222\n"
        "future_release_key = \"keep-me\"\n"
    )
    rendered = render_ports(original, 2443, 2444)
    assert 'future_release_key = "keep-me"' in rendered
    assert "api_bind_port = 2443" in rendered


def test_migrate_config_file_refuses_symlink(tmp_path):
    target = tmp_path / "target.toml"
    target.write_text("")
    link = tmp_path / "boron.toml"
    link.symlink_to(target)
    with pytest.raises(ConfigError, match="non-symlink"):
        migrate_config_file(link)
