"""Audit-safe parameter rendering, shared with readers of historical rows."""
import re

MAX_PARAM_VALUE_LEN = 500
_SENSITIVE_KEY_PARTS = ("password", "secret", "token", "session_id", "code", "private_key", "api_key", "license", "recovery", "passphrase", "access_key", "credential")
_PRIVATE_PAYLOAD_KEYS = {
    "env", "env_vars", "environment", "content", "contents", "body",
    "content_base64", "headers", "authorization", "credential", "credentials",
}

def sanitize(key: str, value):
    lowered = key.lower()
    if lowered in _PRIVATE_PAYLOAD_KEYS or any(part in lowered for part in _SENSITIVE_KEY_PARTS):
        return "***"
    if isinstance(value, dict):
        return {str(k): sanitize(str(k), v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(key, item) for item in value]
    # URLs may contain basic-auth credentials, signed queries or fragments.
    # Preserve operation/target metadata, not a second copy of the secret.
    if isinstance(value, str) and "://" in value and any(marker in value for marker in ("@", "?", "#")):
        return "***"
    if isinstance(value, str) and any(part in value.lower() for part in ("license", "recovery", "passphrase", "access_key")):
        return "[REDACTED sensitive diagnostic]"
    if isinstance(value, str) and len(value) > MAX_PARAM_VALUE_LEN:
        # File contents (file.read/file.write) and similarly bulky values
        # don't belong verbatim in the audit log -- truncate rather than
        # let one file.write of a 10MB file bloat every audit row forever.
        return f"{value[:MAX_PARAM_VALUE_LEN]}... ({len(value)} chars total)"
    return value



def secret_values(payload):
    values = set()
    def collect(key, value, inherited=False):
        private = inherited or key.lower() in _PRIVATE_PAYLOAD_KEYS or any(part in key.lower() for part in _SENSITIVE_KEY_PARTS)
        if isinstance(value, dict):
            for child, item in value.items():
                collect(str(child), item, private)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(key, item, private)
        elif private and isinstance(value, str) and value:
            values.add(value)
    collect("", payload)
    return values


def sanitize_output(value, *contexts):
    if value is None:
        return None
    text = str(value)
    for secret in sorted(set().union(*(secret_values(item) for item in contexts)), key=len, reverse=True):
        text = text.replace(secret, "***")
    # Free-form/JSON diagnostics do not have a reliable schema. Suppress the
    # entire diagnostic if it names a sensitive field rather than leaking
    # quoted keys, spaced passphrases or parser variants.
    if re.search(
        r"""(?i)["']?[\w-]*(?:password|secret|token|session[_-]?id|private[_-]?key|api[_-]?key|license|recovery|passphrase|access[_-]?key|credential)[\w-]*["']?\s*[:=]""",
        text,
    ):
        return "[REDACTED sensitive diagnostic]"
    return text
