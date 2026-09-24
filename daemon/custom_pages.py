"""Per-domain custom error pages (missing-features batch, goal feature 4),
plus the shared on-disk plumbing goal feature 2 (maintenance mode) reuses for
its own forced-503 page.

Custom pages are stored as plain HTML files under
``/home/<user>/<domain>/error_pages/<code>.html`` (the goal's own literal
path) -- deliberately a directory SIBLING to the domain's docroot/logs
directories (same "per-domain state that isn't web content lives alongside,
not inside, the docroot" convention this project already uses for `logs/`),
not inside public_html, so a customer's own deployment/git-push-to-deploy
never accidentally overwrites or exposes them as ordinary site content.

Exposed to OLS via a small per-domain context (``/.boron-error-pages/``,
`daemon/ols.py`) plus a second, shared, server-wide context pointing at this
package's own ``templates/error_pages/`` for the Boron-branded defaults
(goal: "Default: Boron branded pages if customer hasn't set custom
ones") -- OLS's vhost-level ``errorpage <code> { url ... }`` directive then
points at whichever of the two actually has content for that code, decided
here (`resolve_error_pages`), not by OLS itself.

Directory creation goes through daemon/safeio.py (root creating/chowning
paths under an account's own home is a symlink-privesc primitive otherwise --
see that module's docstring) and the docroot's own ACL-grant recipe
(`daemon/handlers_domain.py`'s `_grant_webserver_acl`) so the shared "nobody"
web-server uid can read these files the same way it reads the docroot.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain
from shared.validation import ValidationError, validate_domain

from daemon.procutil import run

ERROR_CODES = (403, 404, 500, 503)
PAGES_DIR_NAME = "error_pages"
# The one file name maintenance mode's own page uses inside the SAME
# error_pages/ directory -- a leading dot so it never collides with, and is
# never listed alongside, the four goal-defined customer-manageable pages
# (list_error_pages/get_error_page/set_error_page/delete_error_page all only
# ever address ERROR_CODES).
MAINTENANCE_PAGE_NAME = ".boron-maintenance.html"

# Bundled with the app (templates/error_pages/*.html), same "resolved
# relative to this module's own file location" trick daemon/ols.py's
# TEMPLATES_DIR already uses -- correct whether running from a git checkout
# or the deployed /opt/boron tree, with zero runtime configuration.
DEFAULT_PAGES_DIR = Path(__file__).resolve().parent.parent / "templates" / "error_pages"

ERROR_PAGES_CONTEXT_URI = "/.boron-error-pages"
DEFAULT_ERRORS_CONTEXT_URI = "/.boron-default-errors"


class CustomPagesError(Exception):
    pass


def _validate_code(code) -> int:
    try:
        code = int(code)
    except (TypeError, ValueError):
        raise ValidationError("error code must be an integer") from None
    if code not in ERROR_CODES:
        raise ValidationError(f"error code must be one of {ERROR_CODES}")
    return code


MAX_ERROR_PAGE_BYTES = 512 * 1024  # generous for a real branded HTML page, bounded against abuse


def _validate_html(content: str) -> str:
    if not isinstance(content, str) or not content.strip():
        raise ValidationError("page content must not be empty")
    if len(content.encode("utf-8")) > MAX_ERROR_PAGE_BYTES:
        raise ValidationError(f"page content must be at most {MAX_ERROR_PAGE_BYTES} bytes")
    if "\x00" in content:
        raise ValidationError("page content must not contain a NUL byte")
    return content


def page_dir(username: str, domain: str) -> str:
    return f"{settings.home_base}/{username}/{domain}/{PAGES_DIR_NAME}"


def _grant_webserver_acl(path: str, uid: int, gid: int) -> None:
    """Same recipe as daemon/handlers_domain.py's _grant_webserver_acl
    (read+traverse ACL, recursive + default, for OLS's shared "nobody"
    worker uid) -- small enough, and specific enough to this directory, that
    duplicating the six-line setfacl call is clearer than adding a cross-
    module import for it."""
    run(["setfacl", "-R", "-P", "-m", "u:nobody:rX", "-d", "-m", "u:nobody:rX", path], uid=uid, gid=gid, check=True)


def _grant_traversal_acl(path: str, uid: int, gid: int) -> None:
    """Execute-only (traversal without listing) ACL for OLS's "nobody"
    worker on a directory that only exists to CONTAIN error_pages/ --
    same "711 home dir: owner full, group/other execute-only" convention
    ARCHITECTURE.md SS6 documents for /home/<user> itself, applied here
    one level lower. Confirmed live as a real, necessary fix (not
    theoretical): secure_mkdirs applies the SAME mode (0750, no "other"
    access) to every path component including this intermediate
    <domain>/ directory, and _grant_webserver_acl above was only ever
    applied to the error_pages/ leaf -- "nobody" could read every file
    inside error_pages/ once it got there, but had no way to traverse
    INTO the parent <domain>/ directory to reach it at all, so OLS's
    errorpage/context fetch 404'd internally and silently fell back to
    its own generic default page. Found by a real curl against a real
    vhost during this feature's live verification, not by unit tests
    (which mock the filesystem and can't catch a real ACL gap)."""
    run(["setfacl", "-m", "u:nobody:x", path], uid=uid, gid=gid, check=True)


def ensure_pages_dir(username: str, domain: str) -> str:
    """Idempotent -- safe to call on every set_error_page (mirrors
    handlers_domain.ensure_docroot's own idempotent create-if-missing
    shape). Returns the created/confirmed directory path."""
    import pwd

    from daemon import safeio

    domain = validate_domain(domain)
    pw = pwd.getpwnam(username)
    home = f"{settings.home_base}/{username}"
    relative = f"{domain}/{PAGES_DIR_NAME}"
    path = safeio.secure_mkdirs(home, relative, pw.pw_uid, pw.pw_gid, 0o750)
    _grant_traversal_acl(f"{home}/{domain}", pw.pw_uid, pw.pw_gid)
    _grant_webserver_acl(path, pw.pw_uid, pw.pw_gid)
    return path


def set_error_page(username: str, domain: str, code, content: str) -> dict:
    from daemon import safeio

    domain = validate_domain(domain)
    code = _validate_code(code)
    content = _validate_html(content)
    import pwd

    pw = pwd.getpwnam(username)
    dir_path = ensure_pages_dir(username, domain)
    safeio.secure_replace_file(dir_path, f"{code}.html", content, pw.pw_uid, pw.pw_gid, 0o640)
    return {"domain": domain, "code": code, "status": "saved"}


def delete_error_page(username: str, domain: str, code) -> dict:
    from daemon import safeio

    domain = validate_domain(domain)
    code = _validate_code(code)
    dir_path = page_dir(username, domain)
    safeio.secure_unlink(dir_path, f"{code}.html")
    return {"domain": domain, "code": code, "status": "deleted"}


def get_error_page(username: str, domain: str, code) -> str | None:
    from daemon import safeio

    domain = validate_domain(domain)
    code = _validate_code(code)
    return safeio.secure_read_text(page_dir(username, domain), f"{code}.html", max_bytes=MAX_ERROR_PAGE_BYTES)


def list_error_pages(username: str, domain: str) -> dict:
    domain = validate_domain(domain)
    custom = existing_codes(username, domain)
    return {
        "domain": domain,
        "pages": [
            {"code": code, "has_custom": code in custom, "default_url": f"{DEFAULT_ERRORS_CONTEXT_URI}/{code}.html"}
            for code in ERROR_CODES
        ],
    }


def existing_codes(username: str, domain: str) -> set[int]:
    """Filesystem-existence check, not a DB row -- matches the goal's own
    framing ("Default: Boron branded pages if customer hasn't set
    custom ones"), and is what daemon/ols.py calls at vhost-render time to
    decide each code's `errorpage ... { url ... }` target."""
    from daemon import safeio

    dir_path = page_dir(username, domain)
    found = set()
    for code in ERROR_CODES:
        if safeio.secure_read_text(dir_path, f"{code}.html", max_bytes=1) is not None:
            found.add(code)
    return found


def set_maintenance_page(username: str, domain: str, content: str) -> None:
    """Called by daemon/handlers_maintenance.py, not directly exposed as an
    RPC op -- the maintenance page's HTML is generated server-side from the
    admin's title/message/estimated_time (daemon/handlers_maintenance.py),
    never customer-uploaded raw HTML the way the four real error pages are."""
    from daemon import safeio

    domain = validate_domain(domain)
    import pwd

    pw = pwd.getpwnam(username)
    dir_path = ensure_pages_dir(username, domain)
    safeio.secure_replace_file(dir_path, MAINTENANCE_PAGE_NAME, content, pw.pw_uid, pw.pw_gid, 0o640)


def resolve_error_pages(username: str, domain: str, maintenance_active: bool = False) -> dict[int, str]:
    """The single decision point daemon/ols.py's vhost render calls: one URL
    per HTTP error code, always fully populated (custom page if the
    customer has one, else the shared Boron-branded default) -- with
    maintenance mode's own page substituted for 503 specifically while
    maintenance is enabled (OLS has exactly one `errorpage 503` slot per
    vhost, so this is the one place that conflict is resolved, rather than
    leaving two competing template branches to fight over it)."""
    custom = existing_codes(username, domain)
    pages: dict[int, str] = {}
    for code in ERROR_CODES:
        if code in custom:
            pages[code] = f"{ERROR_PAGES_CONTEXT_URI}/{code}.html"
        else:
            pages[code] = f"{DEFAULT_ERRORS_CONTEXT_URI}/{code}.html"
    if maintenance_active:
        pages[503] = f"{ERROR_PAGES_CONTEXT_URI}/{MAINTENANCE_PAGE_NAME}"
    return pages


# Deliberately no delete_pages_for_domain cleanup hook: error_pages/ files
# are left in place on domain removal, same "removing a domain is a routing
# change, not a request to destroy content" reasoning
# handlers_domain.remove_domain's own docstring already gives for the
# docroot itself -- only the vhost's context/errorpage wiring goes away
# (ols.remove_domain_vhost).


# --- RPC-facing entry points (daemon/server.py's OP_TABLE) -----------------
# Same params-dict-in/dict-out shape and domain->account resolution as every
# other per-domain feature's handlers_*.py (e.g. daemon/handlers_hotlink.py),
# kept in this module rather than a separate handlers_errorpages.py since
# there's no meaningful split between "the on-disk plumbing" and "the RPC
# surface over it" here -- unlike hotlink/hotlink protection, everything in
# this file already IS the feature.


def _domain_account(session, domain_name: str) -> tuple[Domain, Account]:
    domain_row = session.scalar(select(Domain).where(Domain.domain == domain_name))
    if domain_row is None:
        raise RuntimeError(f"domain '{domain_name}' not found")
    account = session.get(Account, domain_row.account_id)
    if account is None:
        raise RuntimeError(f"domain '{domain_name}' has no owning account")
    return domain_row, account


def rpc_list_error_pages(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    with write_session() as session:
        _domain_row, account = _domain_account(session, domain_name)
        username = account.username
    return list_error_pages(username, domain_name)


def rpc_get_error_page(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    with write_session() as session:
        _domain_row, account = _domain_account(session, domain_name)
        username = account.username
    return {"domain": domain_name, "code": _validate_code(params["code"]), "content": get_error_page(username, domain_name, params["code"])}


def rpc_set_error_page(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    with write_session() as session:
        _domain_row, account = _domain_account(session, domain_name)
        account_snapshot = account
    result = set_error_page(account_snapshot.username, domain_name, params["code"], params["content"])
    from daemon import ols

    ols.refresh_vhost(account_snapshot)
    return result


def rpc_delete_error_page(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    with write_session() as session:
        _domain_row, account = _domain_account(session, domain_name)
        account_snapshot = account
    result = delete_error_page(account_snapshot.username, domain_name, params["code"])
    from daemon import ols

    ols.refresh_vhost(account_snapshot)
    return result
