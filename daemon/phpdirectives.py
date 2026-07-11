"""Registry of per-account php.ini directives the panel can override.

Lives in its own module (not daemon/handlers_php_ini.py) because both the
handlers AND daemon/ols.py's vhost renderer need it, and handlers_php_ini
imports ols -- putting it in the handlers module would be a circular import.

Two tiers, matching the two storage shapes:

- LEGACY_DIRECTIVES: the six Phase 3 originals, stored as typed columns on
  PhpIniOverride. Their DEFAULTS must match this server's real
  /usr/local/lsws/lsphp83/etc/php/8.3/litespeed/php.ini -- they're shown as
  the account's *current* effective values whenever no override exists, so
  a mismatch means a customer sees e.g. "64M" pre-filled while uploads
  actually fail at 2M (found stale once already, Phase 4 feature 9).

- EXTRA_DIRECTIVES: everything added since (max_input_vars etc.), stored as
  PhpIniDirective key/value rows with the value already rendered to its
  php_admin_value string form. Defaults verified against the same live
  php.ini (max_input_vars is commented out there, so PHP's built-in 1000
  applies; date.timezone is unset, so PHP falls back to UTC).

Every value rendered into the OLS config passes through validate() first --
names are registry keys only (never user text), and each type's validator
is the injection defense for its value.
"""
from __future__ import annotations

import re

from shared.config import settings
from shared.validation import (
    ValidationError,
    validate_php_bounded_int,
    validate_php_timezone,
)

# QA round 2, item 9: hardened php.ini default. This is the SAME set
# scripts/install.sh's setup_php_hardening() writes verbatim into the real
# system lsphp php.ini files (both 8.1 and 8.3) -- defined once here and
# imported by the installer (via a python -c one-liner, matching this
# script's existing bootstrap-call pattern) so the two can never drift
# apart. A standard, conservative shell/process-execution hardening set --
# the same functions every mainstream shared-hosting panel disables by
# default (cPanel's own stock disable_functions list is the closest public
# reference point).
DEFAULT_DISABLE_FUNCTIONS = (
    "exec", "system", "shell_exec", "passthru", "popen", "proc_open",
    "proc_get_status", "proc_close", "proc_terminate", "proc_nice",
    "pcntl_exec",
)

_FUNCTION_NAME_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
MAX_DISABLE_FUNCTIONS = 100


def validate_disable_functions(value) -> str:
    """Accepts a list of function names or a single comma-separated string
    (the admin UI's textarea sends the latter); returns the canonical,
    deduplicated, sorted, comma-joined string that's actually interpolated
    into `php_admin_value disable_functions "<value>"`. An empty result
    (admin explicitly clears every function) is valid -- that's a real,
    if unusual, "re-enable everything for this scope" request, distinct
    from "no override at all" (which is a missing row, not an empty one)."""
    if isinstance(value, str):
        names = [v.strip() for v in value.split(",")]
    elif isinstance(value, (list, tuple)):
        names = [str(v).strip() for v in value]
    else:
        raise ValidationError("disable_functions must be a comma-separated string or a list of function names")
    names = [n for n in names if n]
    if len(names) > MAX_DISABLE_FUNCTIONS:
        raise ValidationError(f"disable_functions accepts at most {MAX_DISABLE_FUNCTIONS} function names")
    for name in names:
        if not _FUNCTION_NAME_RE.match(name):
            raise ValidationError(f"'{name}' is not a valid PHP function name")
    return ",".join(sorted(set(names)))


def php_scan_dir(username: str, php_version: str) -> str:
    """Per-account, per-version PHP_INI_SCAN_DIR (daemon/phpext.py
    materializes it; ols.py renders it into the account's extProcessor).
    Defined here because both need it and phpext imports ols -- same
    circularity reason this whole module exists. Under the account HOME
    (not /etc/boron, confirmed invisible inside a live nsisolation
    jail) but root-owned, so the account can read it and cannot write it."""
    return f"{settings.home_base}/{username}/.php/{php_version.replace('.', '')}/conf.d"

DEFAULTS = {
    "memory_limit": "128M",
    "upload_max_filesize": "2M",
    "post_max_size": "8M",
    "max_execution_time": 30,
    "display_errors": False,
    "error_reporting": "E_ALL & ~E_DEPRECATED & ~E_STRICT",
}

# Descriptor metadata for the six legacy directives, consumed by the API's
# GET response so the UI renders every field from one server-provided list
# instead of hardcoding types/bounds a second time.
LEGACY_DIRECTIVES = [
    {"name": "memory_limit", "type": "size", "min_mb": 1, "max_mb": 2048},
    {"name": "upload_max_filesize", "type": "size", "min_mb": 1, "max_mb": 2048},
    {"name": "post_max_size", "type": "size", "min_mb": 1, "max_mb": 2048},
    {"name": "max_execution_time", "type": "int", "min": 1, "max": 300},
    {"name": "display_errors", "type": "bool"},
    {"name": "error_reporting", "type": "string"},
]

EXTRA_DIRECTIVES = {
    "max_input_vars": {"type": "int", "default": "1000", "min": 100, "max": 100000},
    "max_input_time": {"type": "int", "default": "60", "min": -1, "max": 3600},
    "max_file_uploads": {"type": "int", "default": "20", "min": 1, "max": 200},
    "allow_url_fopen": {"type": "bool", "default": "On"},
    "session.gc_maxlifetime": {"type": "int", "default": "1440", "min": 60, "max": 604800},
    "date.timezone": {"type": "timezone", "default": "UTC"},
}


def validate(name: str, value) -> str:
    """Validate one EXTRA_DIRECTIVES value and return its rendered string
    form -- the exact text stored in PhpIniDirective.value and later
    interpolated into `php_admin_value <name> "<value>"`."""
    spec = EXTRA_DIRECTIVES.get(name)
    if spec is None:
        raise ValidationError(f"'{name}' is not a supported PHP directive")
    if spec["type"] == "int":
        return str(validate_php_bounded_int(value, name, spec["min"], spec["max"]))
    if spec["type"] == "bool":
        if isinstance(value, str):
            value = value.strip().lower() in ("on", "true", "1", "yes")
        return "On" if value else "Off"
    return validate_php_timezone(value)
