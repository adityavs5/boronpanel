"""Input validation shared by the API and the daemon.

Every value that ends up in a shell-out argument, a config file rendered by
Jinja2, or a SQL identifier (which can't be parameterized) is validated here
*before* it reaches any of those contexts. This is a direct response to
CyberPanel's CVE-2024-51567/51568 (RESEARCH.md SS5) -- never trust a value
just because it passed validation somewhere else in the call stack.
"""
from __future__ import annotations

import ipaddress
import re
import secrets
import socket
import string

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


def validate_php_bounded_int(value, field: str, minimum: int, maximum: int) -> int:
    """Shared shape for the newer integer php.ini directives
    (max_input_vars, max_file_uploads, ...) -- same conservative
    hard-ceiling posture as validate_php_max_execution_time above, just
    parameterized instead of one bespoke function per directive."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be an integer") from None
    if not (minimum <= value <= maximum):
        raise ValidationError(f"{field} must be between {minimum} and {maximum}")
    return value


def validate_php_timezone(value: str) -> str:
    """date.timezone is interpolated into an OLS config file
    (`php_admin_value date.timezone "<value>"`), so like error_reporting
    this is the actual injection defense -- membership in the system tz
    database, checked against zoneinfo, not just a charset regex."""
    import zoneinfo

    if not isinstance(value, str) or not re.match(r"\A[A-Za-z0-9_+/-]{1,64}\Z", value):
        raise ValidationError(f"'{value}' is not a valid timezone identifier")
    if value not in zoneinfo.available_timezones():
        raise ValidationError(f"'{value}' is not a known timezone (expected e.g. 'Asia/Kolkata' or 'UTC')")
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

    if not isinstance(url, str) or len(url) > 2000 or any(c in url for c in ("\n", "\r", "\t", "[", "]", " ")):
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


# Phase 4 feature 1: SpamAssassin required_score. SpamAssassin itself
# accepts any positive float, but a threshold below ~1 flags nearly
# everything as spam (useless) and one above ~20 is indistinguishable in
# practice from "disabled" (which this feature already has an explicit,
# separate toggle for) -- bounding the range catches obvious fat-finger
# input (e.g. "50" meant as a percentage) without being paternalistic
# about the real, wide range (3-10 is typical) legitimate configs use.
MIN_SPAM_THRESHOLD = 1.0
MAX_SPAM_THRESHOLD = 20.0


def validate_spam_threshold(value) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        raise ValidationError("spam threshold must be a number") from None
    if not (MIN_SPAM_THRESHOLD <= score <= MAX_SPAM_THRESHOLD):
        raise ValidationError(f"spam threshold must be between {MIN_SPAM_THRESHOLD} and {MAX_SPAM_THRESHOLD}")
    return score


# Phase 4 feature 3: IP blocker. Python's own ipaddress module is the
# validator -- it already correctly rejects the input classes that matter
# here (malformed octets, out-of-range prefix lengths, trailing garbage)
# without reimplementing IPv4/IPv6/CIDR parsing by hand.
MAX_IP_BLOCK_ENTRIES = 200


def validate_ip_or_cidr(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("IP/CIDR entry must not be empty")
    value = value.strip()
    try:
        if "/" in value:
            network = ipaddress.ip_network(value, strict=False)
            return str(network)
        return str(ipaddress.ip_address(value))
    except ValueError:
        raise ValidationError(f"'{value}' is not a valid IP address or CIDR range") from None


# Phase 4 feature 4: directory privacy (.htpasswd users). No colon (the
# htpasswd file's own field separator) or whitespace -- both would corrupt
# the file's line-based format, the same class of injection this module's
# other \A/\Z-anchored validators exist to prevent for their own contexts.
HTPASSWD_USERNAME_RE = re.compile(r"\A[A-Za-z0-9._-]{1,64}\Z")


def validate_htpasswd_username(value: str) -> str:
    if not isinstance(value, str) or not HTPASSWD_USERNAME_RE.match(value):
        raise ValidationError("username must be 1-64 characters: letters, digits, dot, underscore, hyphen only")
    return value


# Security audit finding F5: this charset restriction IS the injection
# defense, not cosmetic -- the traversal jail is daemon/filemanager.py's
# realpath check (reused directly, not reimplemented), but a value that
# passes that jail is still later interpolated *unescaped* into a bash
# post-receive hook (daemon/gitrepo.py) and an OLS vhost realm/context
# block (daemon/fileauth.py). A prior version of this validator only
# rejected empty strings and a NUL byte, so a directory name/path
# containing '"', '`', '$(...)', or a newline survived validation and
# reached those interpolation sites unescaped -- a real, confirmed
# command/config-injection defect (see docs/AUDIT-FINDINGS.md F5).
PROTECTED_DIR_RE = re.compile(r"\A[A-Za-z0-9_./-]+\Z")


def validate_protected_dir_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("directory path must not be empty")
    candidate = value.strip().strip("/")
    if not candidate or not PROTECTED_DIR_RE.match(candidate):
        raise ValidationError(
            "directory path may only contain letters, digits, '.', '_', '-', and '/'"
        )
    return candidate


# Phase 4 feature 5: git repos. Used directly as a directory name
# (`<name>.git`) and inside a shell-invoked git command's argument list --
# never shell=True, but still conservative: no dots/slashes, so a name can
# never itself look like a path segment (`..`, `a/b`).
GIT_REPO_NAME_RE = re.compile(r"\A[a-z][a-z0-9-]{0,62}\Z")


def validate_git_repo_name(value: str) -> str:
    if not isinstance(value, str) or not GIT_REPO_NAME_RE.match(value):
        raise ValidationError("repo name must start with a lowercase letter and contain only lowercase letters, digits, hyphens (max 63 chars)")
    return value


MAX_HOTLINK_ALLOWED_DOMAINS = 20


def validate_hotlink_allowed_domains(values) -> list[str]:
    if not isinstance(values, list):
        raise ValidationError("allowed_domains must be a list")
    if len(values) > MAX_HOTLINK_ALLOWED_DOMAINS:
        raise ValidationError(f"allowed_domains must have at most {MAX_HOTLINK_ALLOWED_DOMAINS} entries")
    seen = set()
    result = []
    for value in values:
        domain = validate_domain(value)
        if domain not in seen:
            seen.add(domain)
            result.append(domain)
    return result


# Phase 3 feature 10 originally set this to an 8-char, NIST SP 800-63B-
# aligned, length-dominant policy with no composition rules (the modern-
# guidance argument: composition rules mostly encourage predictable
# substitutions like "Password1!"). **Superseded explicitly by the Phase 4
# goal**, which specifies a traditional composition policy in so many words
# (12+ characters, upper+lower+number+special all required) -- an explicit
# instruction overrides the earlier in-house reasoning; the NIST-aligned
# rationale is left here only as a record of what changed and why, not as
# live guidance. Still rejects the most common trivially-weak patterns
# (blocklist, all-one-character, purely numeric) on top of the new
# composition checks, since composition rules alone don't catch those
# either (e.g. "Password123!" passes composition but is the single most
# common weak password in every real-world breach corpus).
MIN_PASSWORD_LENGTH = 12
_COMMON_WEAK_PASSWORDS = {
    "password", "password1", "password123", "12345678", "123456789",
    "1234567890", "qwertyuiop", "letmein123", "changeme123", "admin1234",
    "password1!", "password123!", "welcome123!", "iloveyou123",
}
_SPECIAL_CHARS_RE = re.compile(r"[^A-Za-z0-9]")


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
    if not any(c.isupper() for c in password):
        raise ValidationError("password must contain at least one uppercase letter")
    if not any(c.islower() for c in password):
        raise ValidationError("password must contain at least one lowercase letter")
    if not any(c.isdigit() for c in password):
        raise ValidationError("password must contain at least one number")
    if not _SPECIAL_CHARS_RE.search(password):
        raise ValidationError("password must contain at least one special character")
    return password


_PASSWORD_SPECIAL_CHARS = "!@#$%^&*-_=+"


def generate_strong_password(length: int = 20) -> str:
    """The one place this project generates a random password -- every
    auto-generated password (account creation, WordPress/app-installer
    admin accounts, database users) must itself satisfy
    validate_password_strength, the same as any customer-supplied one.

    Before this consolidation, four separate call sites
    (daemon/sysops.py, daemon/wordpress.py, daemon/appinstaller.py,
    daemon/mariadb.py) each had their own near-identical
    generate_password() drawing only from letters+digits -- none of them
    would have actually passed validate_password_strength's own special-
    character requirement if anything had ever checked. Found during
    Phase 4 feature 12's codebase-wide audit. Not a practical secrecy
    weakness on its own (20+ random alnum characters is already far
    stronger than the 12-char human-chosen minimum this validator
    enforces) -- fixed anyway, for genuine consistency with "12+ char
    strong passwords... on ALL password operations codebase-wide," and so
    the generator and the validator can never silently drift apart again.
    """
    if length < MIN_PASSWORD_LENGTH:
        raise ValueError(f"generated password length must be >= {MIN_PASSWORD_LENGTH}")
    alphabet = string.ascii_letters + string.digits + _PASSWORD_SPECIAL_CHARS
    while True:
        required = [
            secrets.choice(string.ascii_uppercase),
            secrets.choice(string.ascii_lowercase),
            secrets.choice(string.digits),
            secrets.choice(_PASSWORD_SPECIAL_CHARS),
        ]
        rest = [secrets.choice(alphabet) for _ in range(length - len(required))]
        chars = required + rest
        secrets.SystemRandom().shuffle(chars)
        candidate = "".join(chars)
        try:
            return validate_password_strength(candidate)
        except ValidationError:
            continue  # vanishingly rare (e.g. landed on a disallowed common password); retry


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


# Phase 4 feature 6: SSH keys. Only a cheap, pure pre-check here (no NUL
# byte, single line only -- authorized_keys is one-key-per-line, so a
# multi-line paste would silently corrupt the file/add unintended keys,
# and a reasonable length ceiling) -- the actual cryptographic format
# check is a real `ssh-keygen -lf -` call in daemon/sshkeys.py, the same
# "cheap pure check here, real system-tool validation in the daemon"
# split autoresponder.py's Sieve validation already uses.
MAX_SSH_KEY_LENGTH = 8192


def validate_ssh_key_text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("SSH public key must not be empty")
    value = value.strip()
    if "\x00" in value:
        raise ValidationError("SSH public key must not contain a NUL byte")
    if len(value) > MAX_SSH_KEY_LENGTH:
        raise ValidationError(f"SSH public key must be at most {MAX_SSH_KEY_LENGTH} characters")
    if "\n" in value:
        raise ValidationError("SSH public key must be a single line (one key per entry)")
    return value


# Phase 4 feature 10: cron MAILTO. Empty is valid and means "no MAILTO
# line at all" (daemon/cron.py falls back to cron's own native per-owner
# default -- always the account's own Linux user, since every crontab is
# always written via `crontab -u <username>`, never root, so this is a
# reset, not a "suppress all mail" state). The one thing actually worth
# rejecting: a customer typing "root" (in any of its addressable forms)
# and having their cron output routed to the server operator's own
# mailbox -- an information-disclosure surprise on a shared box, not
# something cron's own syntax prevents on its own.
# Phase 7a feature 4: LSCache. Bounds keep an admin/customer from
# configuring an effectively-infinite (or effectively-zero, i.e.
# pointless) cache TTL -- the same "obvious fat-finger input" guard
# validate_spam_threshold already applies to its own numeric range.
MIN_CACHE_TTL_SECONDS = 30
MAX_CACHE_TTL_SECONDS = 604800  # 7 days
MAX_LSCACHE_EXCLUDE_PATHS = 50


def validate_cache_ttl_seconds(value) -> int:
    try:
        ttl = int(value)
    except (TypeError, ValueError):
        raise ValidationError("ttl_seconds must be an integer") from None
    if not (MIN_CACHE_TTL_SECONDS <= ttl <= MAX_CACHE_TTL_SECONDS):
        raise ValidationError(f"ttl_seconds must be between {MIN_CACHE_TTL_SECONDS} and {MAX_CACHE_TTL_SECONDS}")
    return ttl


def validate_lscache_exclude_paths(values) -> list[str]:
    if not isinstance(values, list):
        raise ValidationError("exclude_paths must be a list")
    if len(values) > MAX_LSCACHE_EXCLUDE_PATHS:
        raise ValidationError(f"exclude_paths must have at most {MAX_LSCACHE_EXCLUDE_PATHS} entries")
    seen = set()
    result = []
    for value in values:
        # Same charset/shape as a redirect path -- both are rendered
        # directly into an OLS vhost config as a bare path value.
        path = validate_redirect_path(value)
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


def validate_cron_mailto(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    local_part = value.split("@", 1)[0].strip('"').lower()
    if local_part == "root":
        raise ValidationError("MAILTO must not be 'root' -- shared hosting crontabs must never target the server's root mailbox")
    return validate_email_address(value)


# Phase 7a features 1/2: NodeJS/Python app hosting. App name is used both
# as a DB-unique-per-account key and as a path segment (<home>/nodeapps/
# <name>, <home>/logs/node/<name>.log) -- same conservative charset as
# validate_git_repo_name, for the identical "never itself looks like a
# path segment" reason.
APP_NAME_RE = re.compile(r"\A[a-z][a-z0-9-]{0,62}\Z")


def validate_app_name(value: str) -> str:
    if not isinstance(value, str) or not APP_NAME_RE.match(value):
        raise ValidationError("app name must start with a lowercase letter and contain only lowercase letters, digits, hyphens (max 63 chars)")
    return value


# The entry point is rendered into a systemd unit's ExecStart argument list
# (never shell=True, daemon/procutil.py's own hard rule) and is always
# joined onto the app's own directory before use -- still validated here as
# a relative, traversal-free path (no leading '/', no '..' segment, no NUL/
# newline) so a crafted value can never escape the app's own directory or
# inject an extra argv entry via a newline the way a raw string might.
def validate_app_entry_point(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("entry point must not be empty")
    value = value.strip()
    if "\x00" in value or "\n" in value:
        raise ValidationError("entry point must not contain a NUL byte or newline")
    if value.startswith("/") or value.startswith("~"):
        raise ValidationError("entry point must be a path relative to the app's own directory")
    parts = value.split("/")
    if any(p in ("..", "") for p in parts):
        raise ValidationError("entry point must not contain '..' or empty path segments")
    if len(value) > 512:
        raise ValidationError("entry point must be at most 512 characters")
    return value


# Phase 7a feature 2: Python WSGI/ASGI entry point, gunicorn/uvicorn's own
# "module:callable" syntax (e.g. "app:app", "myproject.wsgi:application") --
# passed as a plain argv element to gunicorn/uvicorn (never shell=True,
# daemon/procutil.py's own hard rule), but still charset-restricted the
# same conservative way validate_app_entry_point is, rather than accepting
# arbitrary text.
PYTHON_ENTRY_POINT_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*\Z")


def validate_python_entry_point(value: str) -> str:
    if not isinstance(value, str) or not PYTHON_ENTRY_POINT_RE.match(value.strip()):
        raise ValidationError("entry point must look like 'module:callable' (e.g. 'app:app'), letters/digits/underscore/dots only")
    return value.strip()


ENV_VAR_KEY_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
MAX_ENV_VARS = 50
# Reserved: Forgehost's own app-hosting machinery sets PORT itself (from
# the allocated port, not a customer-supplied value) -- letting a customer
# override it would silently break the very reverse-proxy binding OLS was
# configured to expect.
RESERVED_ENV_KEYS = {"PORT"}


# Phase 7b feature 4: webhooks. The URL is dereferenced by forgehostd itself
# (an outbound HTTP POST, daemon/webhooks.py) -- restricted to http/https
# with a real netloc, same shape as validate_redirect_target, so a crafted
# value can't smuggle a newline/control character into the request line a
# raw string might otherwise allow through httpx.
def validate_webhook_url(url: str) -> str:
    import urllib.parse

    if not isinstance(url, str) or len(url) > 2000 or any(c in url for c in ("\n", "\r", "\t", " ")):
        raise ValidationError(f"'{url}' is not a valid webhook URL")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValidationError(f"'{url}' must be an absolute http:// or https:// URL")
    # Security-audit-2 (Medium) SSRF guard, creation-time half: reject an
    # obvious literal internal IP up front for immediate operator feedback.
    # daemon/webhooks.py additionally re-resolves + re-checks the destination
    # at *delivery* time (the authoritative guard -- it also catches
    # hostname-based targets and DNS-rebinding, which a literal-IP check here
    # cannot).
    host = parsed.hostname
    if host:
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None and (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ):
            raise ValidationError(
                f"'{url}' points at a non-public address -- internal/loopback/link-local/"
                "metadata endpoints are not allowed as webhook targets"
            )
    return url


# Phase 7b feature 5: account usage-alert limits (bandwidth/database/email
# account/subdomain counts). None means "not tracked" (no alert ever fires
# for that resource) -- the same "no row/no value = feature not engaged"
# convention validate_php_size's callers already rely on elsewhere in this
# module, just at the single-field level here instead of a whole row.
def validate_resource_limit(value, field: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        limit = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be an integer or empty (untracked)") from None
    if limit < 1:
        raise ValidationError(f"{field} must be at least 1 (use empty/None for 'not tracked')")
    return limit


def validate_webhook_events(values, allowed: tuple[str, ...]) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValidationError("events must be a non-empty list")
    seen = set()
    result = []
    for value in values:
        if value not in allowed:
            raise ValidationError(f"event '{value}' is not one of {allowed}")
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


# Missing-features batch, goal feature 2: maintenance mode. Bounds mirror
# the other free-text admin-facing fields in this module (redirect target,
# webhook URL) -- generous enough for a real message, capped so a crafted
# huge value can't bloat the vhost's rendered error page or the audit log.
MAINTENANCE_TITLE_MAX_LEN = 200
MAINTENANCE_MESSAGE_MAX_LEN = 2000
MAINTENANCE_ESTIMATED_TIME_MAX_LEN = 100
# The goal's own four presets. None = manual (no auto_disable_at at all).
MAINTENANCE_AUTO_DISABLE_MINUTES = (60, 240, 1440, None)


def _validate_plain_text(value: str, field: str, max_len: int) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    value = value.strip()
    if len(value) > max_len:
        raise ValidationError(f"{field} must be at most {max_len} characters")
    if "\x00" in value:
        raise ValidationError(f"{field} must not contain a NUL byte")
    return value


def validate_maintenance_title(value: str) -> str:
    return _validate_plain_text(value, "title", MAINTENANCE_TITLE_MAX_LEN)


def validate_maintenance_message(value: str) -> str:
    return _validate_plain_text(value, "message", MAINTENANCE_MESSAGE_MAX_LEN)


def validate_maintenance_estimated_time(value: str) -> str:
    return _validate_plain_text(value, "estimated_time", MAINTENANCE_ESTIMATED_TIME_MAX_LEN)


def validate_maintenance_auto_disable_minutes(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        raise ValidationError("auto_disable_minutes must be one of 60, 240, 1440, or null (manual)") from None
    if minutes not in MAINTENANCE_AUTO_DISABLE_MINUTES:
        raise ValidationError("auto_disable_minutes must be one of 60, 240, 1440, or null (manual)")
    return minutes


def generate_bypass_token() -> str:
    """A capability token embedded in a URL/query string, not a login
    credential -- generated server-side, never customer-chosen, same
    "generated, not human-picked" posture as ApiToken/PmaToken. Not run
    through validate_password_strength (that validator is for human-memorable
    login passwords; this is a 32-byte random URL-safe string, already far
    higher entropy than anything that check enforces)."""
    return secrets.token_urlsafe(32)


# Missing-features batch, goal feature 5: per-mailbox spam filter entries.
# A pattern is either a full email address (validate_email_address's own
# syntax) or a bare domain (validate_domain) -- rendered into a per-mailbox
# SpamAssassin user_prefs file as `blacklist_from <pattern>` / `whitelist_from
# <pattern>`, which is the actual injection-defense reason this is validated
# as one of those two known-safe shapes rather than accepted as free text.
def validate_spam_filter_pattern(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("pattern must not be empty")
    value = value.strip().lower()
    if "@" in value:
        return validate_email_address(value)
    return validate_domain(value)


def validate_spam_filter_kind(value: str) -> str:
    if value not in ("blacklist", "whitelist"):
        raise ValidationError("kind must be 'blacklist' or 'whitelist'")
    return value


# Missing-features batch, goal feature 1: IMAPSync migrations. Blocks
# 127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 (the goal's own
# literal list, "127.x/10.x/192.168.x", widened to the full private/loopback/
# link-local/reserved set already used by validate_webhook_url's SSRF guard
# below -- the same class of check, applied to a hostname a customer supplies
# as an IMAP migration SOURCE instead of a webhook target). Resolves the
# hostname (never trusts a literal-IP-only check, which DNS rebinding could
# bypass) -- same two-layer posture validate_webhook_url documents: this is
# the creation-time half, daemon/imapsync.py re-checks again immediately
# before connecting (the authoritative guard).
def validate_imap_source_host(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("source host must not be empty")
    host = value.strip().lower()
    if len(host) > 253 or "\x00" in host or any(c in host for c in ("\n", "\r", "\t", " ")):
        raise ValidationError(f"'{value}' is not a valid hostname")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is None:
        try:
            host.encode("idna")
        except UnicodeError:
            raise ValidationError(f"'{value}' is not a valid hostname") from None
        try:
            resolved = socket.getaddrinfo(host, None)
        except OSError as exc:
            raise ValidationError(f"could not resolve source host '{value}': {exc}") from None
        addresses = [ipaddress.ip_address(r[4][0]) for r in resolved]
    else:
        addresses = [ip]
    for addr in addresses:
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or addr.is_multicast or addr.is_unspecified:
            raise ValidationError(
                f"'{value}' resolves to a non-public address ({addr}) -- internal/loopback/link-local/"
                "reserved hosts are not allowed as an IMAP migration source"
            )
    return host


def validate_imap_source_port(value) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValidationError("source port must be an integer") from None
    if not (1 <= port <= 65535):
        raise ValidationError("source port must be between 1 and 65535")
    return port


def validate_env_vars(value) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValidationError("env vars must be a JSON object of string -> string")
    if len(value) > MAX_ENV_VARS:
        raise ValidationError(f"at most {MAX_ENV_VARS} env vars are allowed")
    result: dict[str, str] = {}
    for key, val in value.items():
        if not isinstance(key, str) or not ENV_VAR_KEY_RE.match(key):
            raise ValidationError(f"env var name '{key}' is invalid (letters, digits, underscore, must not start with a digit)")
        if key in RESERVED_ENV_KEYS:
            raise ValidationError(f"env var name '{key}' is reserved by Forgehost")
        if not isinstance(val, str):
            raise ValidationError(f"env var '{key}' value must be a string")
        if "\x00" in val or "\n" in val:
            raise ValidationError(f"env var '{key}' value must not contain a NUL byte or newline")
        if len(val) > 4000:
            raise ValidationError(f"env var '{key}' value is too long (max 4000 characters)")
        result[key] = val
    return result
