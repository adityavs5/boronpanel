import json
import stat

import pytest

from daemon import ols
from daemon.procutil import ProcResult
from shared.models import OlsServerSettings
from shared.db import write_session


def test_render_uses_persisted_server_settings_shape():
    values = {**ols.OLS_SETTINGS_DEFAULTS, "max_connections": 22000, "quic_enabled": False, "log_level": "INFO"}
    rendered = ols.render_httpd_config([], [], ols_settings=values)
    assert "maxConnections               22000" in rendered
    assert "quicEnable                   0" in rendered
    assert "logLevel             INFO" in rendered


def test_update_settings_persists_only_after_config_transaction(isolated_db, monkeypatch):
    monkeypatch.setattr(ols, "refresh_main_config", lambda: None)
    monkeypatch.setattr(ols, "admin_status", lambda _params: {"settings": ols._ols_settings()})
    result = ols.update_admin_settings({**ols.OLS_SETTINGS_DEFAULTS, "max_connections": 18000, "gzip_level": 7})
    assert result["settings"]["max_connections"] == 18000
    with write_session() as session:
        row = session.get(OlsServerSettings, 1)
        assert row.gzip_level == 7


def test_update_settings_rolls_database_back_when_ols_rejects_change(isolated_db, monkeypatch):
    def fail():
        raise RuntimeError("validation failed")

    monkeypatch.setattr(ols, "refresh_main_config", fail)
    with pytest.raises(RuntimeError, match="validation failed"):
        ols.update_admin_settings({**ols.OLS_SETTINGS_DEFAULTS, "max_connections": 19000})
    assert ols._ols_settings() == ols.OLS_SETTINGS_DEFAULTS


@pytest.mark.parametrize("field,value", [
    ("max_connections", 1), ("gzip_level", 10), ("brotli_level", 0),
    ("connection_timeout", 5000), ("log_level", "TRACE"),
])
def test_update_rejects_unsafe_or_unsupported_values(field, value, isolated_db):
    with pytest.raises(Exception):
        ols._validate_admin_settings({**ols.OLS_SETTINGS_DEFAULTS, field: value})


def test_password_reset_and_confirmed_reveal_never_put_secret_in_argv(tmp_path, monkeypatch):
    script = tmp_path / "admpass.sh"
    script.write_text("#!/bin/sh\n")
    credential = tmp_path / "credential.json"
    monkeypatch.setattr(ols, "OLS_PASSWORD_SCRIPT", script)
    monkeypatch.setattr(ols, "OLS_CREDENTIAL_PATH", credential)
    captured = {}

    def fake_run(args, **kwargs):
        captured.update(args=args, **kwargs)
        return ProcResult(args=args, returncode=0, stdout="password updated", stderr="")

    monkeypatch.setattr(ols, "run", fake_run)
    reset = ols.reset_admin_password({"username": "webadmin"})
    assert reset["password"] not in " ".join(captured["args"])
    assert reset["password"] in captured["input_text"]
    assert stat.S_IMODE(credential.stat().st_mode) == 0o600
    assert ols.credential_status({})["password_available"] is True
    with pytest.raises(Exception, match="confirm"):
        ols.reveal_admin_password({})
    assert ols.reveal_admin_password({"confirm": True}) == reset
    assert json.loads(credential.read_text())["username"] == "webadmin"


def test_existing_one_way_password_is_reported_as_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(ols, "OLS_CREDENTIAL_PATH", tmp_path / "missing.json")
    assert ols.credential_status({}) == {"username": "admin", "password_available": False, "reset_at": None}
    with pytest.raises(Exception, match="not recoverable"):
        ols.reveal_admin_password({"confirm": True})
