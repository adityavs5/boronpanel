"""Input validation shared by the API and the daemon.

Every value that ends up in a shell-out argument, a config file rendered by
Jinja2, or a SQL identifier (which can't be parameterized) is validated here
*before* it reaches any of those contexts. This is a direct response to
CyberPanel's CVE-2024-51567/51568 (RESEARCH.md SS5) -- never trust a value
just because it passed validation somewhere else in the call stack.
"""
from __future__ import annotations

import re

USERNAME_RE = re.compile(r"^[a-z][a-z0-9]{0,15}$")
DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)"
    r"(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$"
)

# Reserved usernames: real system accounts, service accounts forgehost itself
# uses, and names that would collide with a path or convention elsewhere in
# the design (e.g. "vmail" owns mail storage; "_suspended" is a literal path
# segment under /var/www).
RESERVED_USERNAMES = {
    "root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail",
    "news", "uucp", "proxy", "www-data", "backup", "list", "irc", "gnats",
    "nobody", "systemd-network", "systemd-resolve", "messagebus", "sshd",
    "forgehost", "forgehost-api", "forgehostd", "vmail", "mysql", "mariadb",
    "postfix", "dovecot", "pdns", "powerdns", "pure-ftpd", "ftp", "admin",
    "administrator", "_suspended", "lsadm", "nginx", "apache", "litespeed",
}


class ValidationError(ValueError):
    pass


def validate_username(username: str) -> str:
    if not isinstance(username, str) or not USERNAME_RE.match(username):
        raise ValidationError(
            "username must be 1-16 lowercase letters/digits, starting with a letter"
        )
    if username in RESERVED_USERNAMES:
        raise ValidationError(f"username '{username}' is reserved")
    return username


def validate_domain(domain: str) -> str:
    if not isinstance(domain, str):
        raise ValidationError("domain must be a string")
    candidate = domain.strip().lower().rstrip(".")
    try:
        candidate.encode("idna")
    except UnicodeError as exc:
        raise ValidationError(f"domain '{domain}' is not valid IDNA") from exc
    if not DOMAIN_RE.match(candidate):
        raise ValidationError(f"domain '{domain}' is not a syntactically valid hostname")
    return candidate


def validate_php_version(version: str, allowed: tuple[str, ...]) -> str:
    if version not in allowed:
        raise ValidationError(f"php version '{version}' is not installed (allowed: {allowed})")
    return version


def validate_db_identifier(name: str, max_len: int = 64) -> str:
    """MariaDB identifier used in unparameterizable DDL (CREATE DATABASE/USER).

    Deliberately stricter than what MariaDB itself allows: lowercase
    alnum/underscore only, must start with a letter. This is the same class
    of bug CyberPanel had in its shell-out paths, applied to SQL DDL instead.
    """
    if not isinstance(name, str) or not re.match(r"^[a-z][a-z0-9_]{0,62}$", name):
        raise ValidationError(f"identifier '{name}' is not a safe SQL identifier")
    if len(name) > max_len:
        raise ValidationError(f"identifier '{name}' exceeds {max_len} chars")
    return name


def validate_record_type(rtype: str) -> str:
    allowed = {"A", "AAAA", "CNAME", "MX", "TXT"}
    if rtype not in allowed:
        raise ValidationError(f"record type '{rtype}' not supported (allowed: {sorted(allowed)})")
    return rtype


def validate_mailbox_local_part(local: str) -> str:
    if not isinstance(local, str) or not re.match(r"^[a-z][a-z0-9._-]{0,63}$", local):
        raise ValidationError(f"mailbox local-part '{local}' is invalid")
    return local
