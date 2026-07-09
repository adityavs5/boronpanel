"""Fernet-based encryption for NodeApp/PythonApp environment variables at
rest (Phase 7a features 1/2 -- goal's explicit "env vars stored encrypted").

Unlike every other secret in this project (MariaDB admin creds, the
PowerDNS API key, TOTP secrets' own documented tradeoff in shared/models.py),
an app's env vars have no existing root-only file to live in the way
secrets.env already does for daemon-level secrets -- they're customer-
supplied, per-app, and need to survive into a rendered systemd
EnvironmentFile at unit-start time. Fernet (symmetric, authenticated
encryption, from the `cryptography` package already a hard dependency for
Phase 3's SSL dashboard) is the standard, non-home-rolled choice for
"encrypt this at rest, decrypt it later with the same key" -- it is not an
irreversible one-way hash like PanelUser.password_hash, because these values
must be recovered in full to populate a running process's environment.

The key itself is auto-generated on first use and persisted into
/etc/forgehost/secrets.env (0600, root-only -- ARCHITECTURE.md SS4's
existing convention for forgehostd-only secrets) rather than requiring a
manual operator step; every other secret in that file already follows this
"forgehostd is the only reader, nothing else needs it" rule.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from shared.config import settings

_APP_ENV_KEY_NAME = "APP_ENV_KEY"


class AppCryptoError(Exception):
    pass


def _persist_key(key: str) -> None:
    """Appends the freshly generated key to secrets.env, creating the file
    (0600, root-only) if it doesn't exist yet. Read back into
    settings.secrets immediately so the rest of this process sees it
    without needing a restart."""
    secrets_path = Path(os.environ.get("FORGEHOST_SECRETS", "/etc/forgehost/secrets.env"))
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{_APP_ENV_KEY_NAME}={key}\n"
    with open(secrets_path, "a", encoding="utf-8") as f:
        f.write(line)
    os.chmod(secrets_path, 0o600)
    settings.secrets[_APP_ENV_KEY_NAME] = key


def get_key() -> str:
    key = settings.app_env_key
    if key:
        return key
    key = Fernet.generate_key().decode("ascii")
    _persist_key(key)
    return key


def _fernet() -> Fernet:
    return Fernet(get_key().encode("ascii"))


def encrypt_env(env: dict[str, str]) -> str:
    """dict -> Fernet token (str), stored directly in NodeApp.env_vars/
    PythonApp.env_vars. Empty dict encrypts to a real (non-empty) token
    rather than an empty string, so "no env vars set" and "not yet
    encrypted" are never ambiguous on read."""
    if not isinstance(env, dict):
        raise AppCryptoError("env vars must be a dict of string -> string")
    payload = json.dumps(env).encode("utf-8")
    return _fernet().encrypt(payload).decode("ascii")


def decrypt_env(token: str) -> dict[str, str]:
    if not token:
        return {}
    try:
        payload = _fernet().decrypt(token.encode("ascii"))
    except InvalidToken as exc:
        raise AppCryptoError("env vars could not be decrypted (corrupt row or rotated key)") from exc
    return json.loads(payload.decode("utf-8"))


def encrypt_secret(value: str) -> str:
    """Encrypt a single opaque secret string (e.g. a CloudflareAccount API
    token) with the same Fernet key. Sibling of encrypt_env for the "one
    string, not a dict" case -- Cloudflare pool tokens (shared/models.py
    CloudflareAccount.api_token_enc) must be recoverable in full to make
    API calls, exactly the reversible-at-rest property Fernet gives."""
    if not isinstance(value, str):
        raise AppCryptoError("secret must be a string")
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise AppCryptoError("secret could not be decrypted (corrupt row or rotated key)") from exc
