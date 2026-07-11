import pytest
from cryptography.fernet import Fernet

from daemon import appcrypto


@pytest.fixture(autouse=True)
def fixed_key(monkeypatch):
    monkeypatch.setitem(appcrypto.settings.secrets, "APP_ENV_KEY", Fernet.generate_key().decode("ascii"))


def test_encrypt_decrypt_round_trip():
    env = {"FOO": "bar", "DB_PASSWORD": "hunter2"}
    token = appcrypto.encrypt_env(env)
    assert "hunter2" not in token
    assert appcrypto.decrypt_env(token) == env


def test_encrypt_empty_dict_produces_nonempty_token():
    token = appcrypto.encrypt_env({})
    assert token
    assert appcrypto.decrypt_env(token) == {}


def test_decrypt_empty_string_is_empty_dict():
    assert appcrypto.decrypt_env("") == {}


def test_decrypt_invalid_token_raises():
    with pytest.raises(appcrypto.AppCryptoError):
        appcrypto.decrypt_env("not-a-real-token")


def test_encrypt_rejects_non_dict():
    with pytest.raises(appcrypto.AppCryptoError):
        appcrypto.encrypt_env(["not", "a", "dict"])


def test_get_key_persists_a_generated_key_when_missing(tmp_path, monkeypatch):
    monkeypatch.delitem(appcrypto.settings.secrets, "APP_ENV_KEY", raising=False)
    secrets_path = tmp_path / "secrets.env"
    monkeypatch.setenv("BORON_SECRETS", str(secrets_path))
    key = appcrypto.get_key()
    assert key
    assert secrets_path.read_text().strip() == f"APP_ENV_KEY={key}"
    assert appcrypto.settings.secrets["APP_ENV_KEY"] == key
    # a second call reuses the same key rather than rotating it
    assert appcrypto.get_key() == key
