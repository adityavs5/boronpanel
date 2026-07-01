"""Input validation shared by the API and the daemon.

Every value that ends up in a shell-out argument, a config file rendered by
Jinja2, or a SQL identifier (which can't be parameterized) is validated here
*before* it reaches any of those contexts. This is a direct response to
CyberPanel's CVE-2024-51567/51568 (RESEARCH.md SS5) -- never trust a value
just because it passed validation somewhere else in the call stack.
"""
from __future__ import annotations

import re

# \A/\Z, not ^/$, throughout this module: Python's $ matches either at the
# true end of the string OR immediately before a single trailing newline
# (documented behavior), so "root\n" would incorrectly pass a "^...$"
# username check -- caught live while adding Phase 3's redirect-path
# validator (a test asserting "/a\n" is rejected failed until this was
# fixed) and then audited across every other regex in this file, since
# it's the same latent class of bug regardless of which validator it's
# in. \A/\Z anchor to the literal start/end of the string with no
# newline exception.
USERNAME_RE = re.compile(r"\A[a-z][a-z0-9]{0,15}\Z")
DOMAIN_RE = re.compile(
    r"\A(?=.{1,253}\Z)(?!-)[a-z0-9-]{1,63}(?<!-)"
    r"(\.(?!-)[a-z0-9-]{1,63}(?<!-))+\Z"
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
    if not isinstance(name, str) or not re.match(r"\A[a-z][a-z0-9_]{0,62}\Z", name):
        raise ValidationError(f"identifier '{name}' is not a safe SQL identifier")
    if len(name) > max_len:
        raise ValidationError(f"identifier '{name}' exceeds {max_len} chars")
    return name


def validate_record_type(rtype: str) -> str:
    # Phase 3 feature 1: full zone editor -- PTR/SRV/CAA added to the
    # original A/AAAA/CNAME/MX/TXT set (Phase c's v1 scope).
    allowed = {"A", "AAAA", "CNAME", "MX", "TXT", "PTR", "SRV", "CAA"}
    if rtype not in allowed:
        raise ValidationError(f"record type '{rtype}' not supported (allowed: {sorted(allowed)})")
    return rtype


def validate_mailbox_local_part(local: str) -> str:
    if not isinstance(local, str) or not re.match(r"\A[a-z][a-z0-9._-]{0,63}\Z", local):
        raise ValidationError(f"mailbox local-part '{local}' is invalid")
    return local


EMAIL_RE = re.compile(r"\A[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}\Z")


def validate_email_address(address: str) -> str:
    """Phase 3 feature 4: an email forwarder's/catch-all's destination is
    deliberately allowed to be ANY external address (that's the whole
    point of a forwarder), so this only checks syntactic shape -- not
    domain ownership -- unlike validate_mailbox_local_part, which is
    scoped to Forgehost-managed mailboxes."""
    if not isinstance(address, str) or not EMAIL_RE.match(address):
        raise ValidationError(f"'{address}' is not a syntactically valid email address")
    return address


# Phase 3 feature 6: PHP ini overrides. Deliberately conservative bounds
# for a shared-hosting environment -- e.g. memory_limit=-1 ("unlimited")
# is rejected outright (the goal calls this out explicitly), and every
# numeric value has a hard ceiling so one account can't configure PHP to
# consume resources far beyond its own cgroup allocation (Phase 2
# feature 6) regardless of what the cgroup itself would eventually cap.
PHP_SIZE_RE = re.compile(r"\A(\d+)([KMG])\Z", re.IGNORECASE)
PHP_SIZE_MIN_MB = 1
PHP_SIZE_MAX_MB = 2048
PHP_MAX_EXECUTION_TIME_MIN = 1
PHP_MAX_EXECUTION_TIME_MAX = 300
# Only characters that can legally appear in a PHP error_reporting
# bitmask expression (E_* constants, whitespace, & | ~ ^ ( ) 0-9) --
# this value is interpolated directly into an OLS config file
# (`php_admin_value error_reporting "<value>"`), so this is the actual
# injection defense for this field, not a formality.
PHP_ERROR_REPORTING_RE = re.compile(r"\A[A-Za-z0-9_&|~^() -]{1,128}\Z")


def _php_size_to_mb(value: str) -> int:
    m = PHP_SIZE_RE.match(value.strip())
    if not m:
        raise ValidationError(f"'{value}' is not a valid PHP size (expected e.g. '256M')")
    number, unit = int(m.group(1)), m.group(2).upper()
    return {"K": number / 1024, "M": number, "G": number * 1024}[unit]


def validate_php_size(value: str, field: str) -> str:
    mb = _php_size_to_mb(value)
    if value.strip() in ("-1", "0"):
        raise ValidationError(f"{field} must not be unlimited (-1/0) on shared hosting")
    if not (PHP_SIZE_MIN_MB <= mb <= PHP_SIZE_MAX_MB):
        raise ValidationError(f"{field} must be between {PHP_SIZE_MIN_MB}M and {PHP_SIZE_MAX_MB}M")
    return value.strip()


def validate_php_memory_limit(value: str) -> str:
    if value.strip() == "-1":
        raise ValidationError("memory_limit must not be unlimited (-1) on shared hosting")
    return validate_php_size(value, "memory_limit")


def validate_php_max_execution_time(value: int) -> int:
    value = int(value)
    if value == 0:
        raise ValidationError("max_execution_time must not be unlimited (0) on shared hosting")
    if not (PHP_MAX_EXECUTION_TIME_MIN <= value <= PHP_MAX_EXECUTION_TIME_MAX):
        raise ValidationError(
            f"max_execution_time must be between {PHP_MAX_EXECUTION_TIME_MIN} and {PHP_MAX_EXECUTION_TIME_MAX} seconds"
        )
    return value


def validate_php_error_reporting(value: str) -> str:
    if not PHP_ERROR_REPORTING_RE.match(value):
        raise ValidationError(f"'{value}' is not a valid error_reporting expression")
    return value


# Phase 3 feature 7: redirect manager. The path is rendered directly
# into an OLS RewriteRule pattern (daemon/ols.py) -- deliberately
# restricted to a small, safe charset that excludes every regex/rewrite-
# rule metacharacter that could let a crafted path escape the intended
# single-path match or inject additional rewrite directives (`[`, `]`,
# whitespace, newlines, etc.). "." is allowed (common in real paths like
# "/old.html") but is escaped to a literal "\." at render time, not here
# -- validation only decides what's a legal *path*, escaping for the
# regex context it will be embedded in is the renderer's job.
REDIRECT_PATH_RE = re.compile(r"\A/[A-Za-z0-9._/~%-]*\Z")
REDIRECT_STATUS_CODES = (301, 302)


def validate_redirect_path(path: str) -> str:
    if not isinstance(path, str) or not REDIRECT_PATH_RE.match(path) or len(path) > 500:
        raise ValidationError(f"'{path}' is not a valid redirect path (must start with '/', safe URL characters only)")
    return path


def validate_redirect_target(url: str) -> str:
    import urllib.parse

    if not isinstance(url, str) or len(url) > 2000 or any(c in url for c in ("\n", "\r", "[", "]", " ")):
        raise ValidationError(f"'{url}' is not a valid redirect target URL")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValidationError(f"'{url}' must be an absolute http:// or https:// URL")
    return url


def validate_redirect_status_code(code: int) -> int:
    code = int(code)
    if code not in REDIRECT_STATUS_CODES:
        raise ValidationError(f"status_code must be one of {REDIRECT_STATUS_CODES}")
    return code


# Phase 3 feature 10: password manager. NIST SP 800-63B-aligned posture
# (length is the dominant factor; arbitrary complexity-composition rules
# are de-emphasized in modern guidance) rather than a traditional
# "must contain 1 uppercase/1 digit/1 symbol" policy, which mostly
# encourages predictable substitutions (Password1! satisfies it, isn't
# meaningfully stronger). Still rejects the most common trivially-weak
# patterns (blocklist, all-one-character, purely numeric) since a bare
# length check alone lets those through. 8 chars is NIST 800-63B's own
# stated minimum (section 5.1.1.2) -- deliberately not a stricter
# in-house number, so this can be cited against a real published
# standard rather than an arbitrary house policy.
MIN_PASSWORD_LENGTH = 8
_COMMON_WEAK_PASSWORDS = {
    "password", "password1", "password123", "12345678", "123456789",
    "1234567890", "qwertyuiop", "letmein123", "changeme123", "admin1234",
}


def validate_password_strength(password: str) -> str:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > 256:
        raise ValidationError("password must be at most 256 characters")
    if password.lower() in _COMMON_WEAK_PASSWORDS:
        raise ValidationError("password is too common/predictable")
    if len(set(password)) == 1:
        raise ValidationError("password must not be a single repeated character")
    if password.isdigit():
        raise ValidationError("password must not be purely numeric")
    return password


DATE_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")


def validate_iso_date(value: str) -> str:
    import datetime as dt

    if not isinstance(value, str) or not DATE_RE.match(value):
        raise ValidationError(f"'{value}' is not a valid YYYY-MM-DD date")
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"'{value}' is not a valid calendar date") from exc
    return value
