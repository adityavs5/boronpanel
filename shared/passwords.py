"""Panel-user password hashing -- bcrypt directly, not via passlib.

passlib's bcrypt backend has a known compatibility break with bcrypt>=4.0
(it probes `bcrypt.__about__.__version__`, removed upstream) -- confirmed
by hitting it directly while building this phase. Using the `bcrypt`
package's own API avoids the broken shim entirely; it's the same handful
of lines passlib would have called through anyway.

Not used for hosted-account/mail/database passwords -- those go through
the system's own `chpasswd`/MariaDB/`doveadm pw` respectively, each on its
own native scheme (shared.config, daemon/mail.py).
"""
from __future__ import annotations

import bcrypt

# bcrypt has a hard 72-byte input limit; longer inputs are silently
# truncated by the library itself, which would make distinct very-long
# passwords collide. Reject rather than silently truncate.
MAX_PASSWORD_BYTES = 72


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        raise ValueError(f"password exceeds {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    raw = password.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(raw, hashed.encode("ascii"))
    except ValueError:
        return False
