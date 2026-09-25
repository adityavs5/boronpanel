"""Phase 8 feature 8: WP-CLI UI.

Auto-detects WordPress installs (a wp-config.php at one of the account's own
domain docroots) and runs an ALLOWLISTED set of WP-CLI subcommands
asynchronously as the account user (via daemon/cmdjobs.py). wp-cli.phar is
fetched server-wide on first use if missing. The command set is a fixed
allowlist built as an argv list (never a shell string), with user-supplied
values (plugin/theme slugs, search/replace strings, user login) passed as
discrete argv elements -- so there is no shell to inject into.
"""
from __future__ import annotations

import os
import re

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain
from shared.validation import ValidationError, generate_strong_password, validate_domain, validate_username

from daemon import cmdjobs, safeio
from daemon.procutil import run

_SLUG_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_WP_VERSION_RE = re.compile(r"\$wp_version\s*=\s*'([^']+)'")


def ensure_wpcli() -> str:
    """Download wp-cli.phar server-wide if missing; return its path. Verified by
    running `--version` (a bad download fails here, not silently later)."""
    path = settings.wpcli_phar_path
    if not os.path.exists(path):
        raise RuntimeError("wp-cli.phar is not installed; install a pinned, locally verified release before enabling WordPress operations")
    check = run([settings.php_cli_bin, path, "--version", "--allow-root", "--no-color"], timeout=30)
    if not check.ok or "WP-CLI" not in (check.stdout + check.stderr):
        raise RuntimeError("wp-cli.phar is present but not runnable")
    return path


def _wp_version_at(docroot: str) -> str | None:
    text = safeio.secure_read_text(os.path.join(docroot, "wp-includes"), "version.php", 20000)
    if text is None:
        return None
    m = _WP_VERSION_RE.search(text)
    return m.group(1) if m else None


def _scan_wp_installs_under(docroot: str) -> list[str]:
    """QA round 2, item 3 (subdirectory installs): every location (relative
    to docroot; "" = the docroot itself) that has its own wp-config.php --
    the docroot itself, plus one level of subdirectories (e.g.
    domain.com/blog). Deliberately not recursive beyond one level --
    unbounded filesystem recursion over an arbitrary customer-owned tree is
    both a real performance risk (docroots can be huge) and unnecessary
    for the real-world case this feature targets."""
    found = []
    if os.path.isfile(os.path.join(docroot, "wp-config.php")):
        found.append("")
    try:
        entries = os.scandir(docroot)
    except OSError:
        return found
    with entries:
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if os.path.isfile(os.path.join(entry.path, "wp-config.php")):
                found.append(entry.name)
    return found


def detect_installs(params: dict) -> dict:
    """Scan the account's own domain docroots (root + one level of
    subdirectories) for a wp-config.php. Reads the WP version straight from
    wp-includes/version.php (no wp-cli run needed). `id` stays exactly the
    bare domain for a root install (unchanged contract, existing callers
    keep working) and becomes "<domain>::<path>" for a subdirectory one.
    An optional `domain` param scopes the scan to just that one domain
    (item 2's per-domain WordPress section) instead of every domain the
    account owns -- omitting it keeps the original account-wide behavior."""
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        query = select(Domain).where(Domain.account_id == account.id)
        if params.get("domain"):
            query = query.where(Domain.domain == validate_domain(params["domain"]))
        domains = session.scalars(query).all()
        docroots = [(d.domain, d.docroot, d.kind) for d in domains]

    installs = []
    seen_docroots = set()
    for domain, docroot, kind in docroots:
        if docroot in seen_docroots:
            continue  # parked/alias domains share a docroot -- list the install(s) once
        seen_docroots.add(docroot)
        for path in _scan_wp_installs_under(docroot):
            install_dir = docroot if not path else os.path.join(docroot, path)
            installs.append({
                "id": domain if not path else f"{domain}::{path}",
                "domain": domain,
                "path": path,
                "docroot": install_dir,
                "wp_version": _wp_version_at(install_dir),
            })
    return {"installs": installs}


def _install_dir(docroot: str, path: str) -> str:
    """Resolve an install's real directory -- the docroot itself if `path`
    is empty, else that subdirectory of it, re-validated to stay inside the
    docroot (rejects a crafted `path` like "../../etc" from ever escaping
    it, the same realpath-containment pattern this project uses everywhere
    else account-owned paths are involved, e.g. daemon/filemanager.py)."""
    path = (path or "").strip().strip("/")
    if not path:
        return docroot
    docroot_real = os.path.realpath(docroot)
    candidate = os.path.realpath(os.path.join(docroot, path))
    if candidate != docroot_real and not candidate.startswith(docroot_real + os.sep):
        raise ValidationError(f"path '{path}' escapes the domain's docroot")
    return candidate


def _resolve_docroot(username: str, domain: str) -> str:
    """Root-install resolver, unchanged -- kept for the existing
    account-level DevTools WP-CLI tab (api/routers/devtools.py), which has
    no notion of a subdirectory path and must keep working exactly as
    before. New, domain-scoped call sites (item 2's per-domain WordPress
    section) use _resolve_install_dir below instead."""
    return _resolve_install_dir(username, domain, "")


def _resolve_install_dir(username: str, domain: str, path: str = "") -> str:
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        row = session.scalar(select(Domain).where(Domain.domain == domain, Domain.account_id == account.id))
        if row is None:
            raise RuntimeError(f"domain '{domain}' not found for account '{username}'")
        docroot = row.docroot
    install_dir = _install_dir(docroot, path)
    if not os.path.isfile(os.path.join(install_dir, "wp-config.php")):
        raise RuntimeError(f"no WordPress install (wp-config.php) found at {install_dir}")
    return install_dir


def _slug(value, field: str) -> str:
    if not isinstance(value, str) or not _SLUG_RE.match(value):
        raise ValidationError(f"{field} must be a valid slug (letters, digits, . _ -)")
    return value


def _text(value, field: str, maxlen: int = 2000) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or len(value) > maxlen:
        raise ValidationError(f"{field} is required and must be non-empty text without NUL")
    return value


def _build(action: str, p: dict):
    """Return (wp_args, display, revealed_secret, redact) for an allowlisted
    action, or raise ValidationError."""
    if action == "core_update":
        return (["core", "update"], "core update", None, None)
    if action == "core_check_update":
        return (["core", "check-update", "--format=json"], "core check-update", None, None)
    if action == "plugin_list":
        # Listing must not execute customer extensions. A broken plugin can
        # otherwise make this read-only operation fatal.
        return (["plugin", "list", "--format=json", "--skip-plugins", "--skip-themes"], "plugin list", None, None)
    if action == "theme_list":
        return (["theme", "list", "--format=json", "--skip-plugins", "--skip-themes"], "theme list", None, None)
    if action == "cache_flush":
        return (["cache", "flush"], "cache flush", None, None)
    if action == "maintenance_on":
        return (["maintenance-mode", "activate"], "maintenance-mode activate", None, None)
    if action == "maintenance_off":
        return (["maintenance-mode", "deactivate"], "maintenance-mode deactivate", None, None)
    if action in ("plugin_update", "theme_update"):
        kind = action.split("_")[0]
        if p.get("all"):
            return ([kind, "update", "--all"], f"{kind} update --all", None, None)
        slug = _slug(p.get("name"), "name")
        return ([kind, "update", slug], f"{kind} update {slug}", None, None)
    if action in ("plugin_activate", "plugin_deactivate", "theme_activate", "theme_deactivate"):
        kind, verb = action.split("_")
        slug = _slug(p.get("name"), "name")
        return ([kind, verb, slug], f"{kind} {verb} {slug}", None, None)
    if action == "user_reset_password":
        login = _slug(p.get("user"), "user")
        password = generate_strong_password(20)
        return (
            ["user", "update", login, "--prompt=user_pass"],
            f"user update {login} --prompt=user_pass",
            password,
            [password],
        )
    if action == "search_replace":
        search = _text(p.get("search"), "search")
        replace = _text(p.get("replace"), "replace")
        preview = p.get("preview", True)
        args = ["search-replace", search, replace, "--report-changed-only"]
        if preview:
            args.append("--dry-run")
        return (args, f"search-replace '{search}' '{replace}'" + (" --dry-run" if preview else ""), None, None)
    raise ValidationError(f"unknown WP-CLI action '{action}'")


def _run_wpcli(params: dict) -> dict:
    username = validate_username(params["username"])
    domain = validate_domain(params["domain"])
    path = (params.get("path") or "").strip().strip("/")
    action = params["action"]
    docroot = _resolve_install_dir(username, domain, path)

    wp_args, display, secret, redact = _build(action, params)
    phar = ensure_wpcli()
    argv = [settings.php_cli_bin, phar, f"--path={docroot}", "--no-color", *wp_args]
    return cmdjobs.submit(
        username, "wpcli", docroot, argv, f"wp {display}",
        redact=redact, revealed_secret=secret,
        input_text=secret + "\n" if action == "user_reset_password" else None,
    )


def run_wpcli(params: dict) -> dict:
    from daemon.wpmanager import _operation_lock, ensure_idle
    with _operation_lock:
        ensure_idle(params['username'])
        return _run_wpcli(params)


def get_run(params: dict) -> dict:
    return cmdjobs.get_run(params)


def list_runs(params: dict) -> dict:
    return cmdjobs.list_runs(params, "wpcli")
