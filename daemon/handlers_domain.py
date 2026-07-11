"""Domain CRUD: addon/subdomain support for Phase b's vhost templating, and
the foundation Phase c's DNS zones build on top of.

Phase 2 feature 4: subdomains get their own real OLS vhost (daemon/ols.py's
one-vhost-per-domain refactor) and, when their parent domain's zone is
Forgehost-managed, their own DNS A record -- fixing the Phase 1 gap where
every domain under an account silently served identical public_html
content regardless of which domain/subdomain was actually requested.
"""
from __future__ import annotations

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain
from shared.validation import validate_domain, validate_php_version, validate_username

from daemon import dnsprovider, handlers_redirect, lscache, ols, sysops
from daemon.dns_zone_lookup import find_managed_zone, label_within_zone


def _domain_to_dict(domain: Domain) -> dict:
    return {
        "id": domain.id,
        "account_id": domain.account_id,
        "domain": domain.domain,
        "kind": domain.kind,
        "docroot": domain.docroot,
        "ssl_status": domain.ssl_status,
        "ssl_is_wildcard": domain.ssl_is_wildcard,
        # None means "inherit the account's own PHP version" (Phase 7a
        # feature 6) -- the account's own default is not resolved/inlined
        # here, since this dict has no access to the owning Account row;
        # callers that need the *effective* version already have the
        # account loaded (e.g. ols.py's own render path).
        "php_version": domain.php_version,
        "created_at": domain.created_at.isoformat() if domain.created_at else None,
    }


# Phase 3 feature 1 factored these two out into daemon/dns_zone_lookup.py
# (handlers_mail.py needs the identical lookup for SPF/DKIM/DMARC
# auto-publish) -- kept as thin aliases here so this module's existing
# call sites and any external references don't need to change.
_find_parent_zone = find_managed_zone
_subdomain_label = label_within_zone


def add_domain(params: dict) -> dict:
    username = validate_username(params["username"])
    domain_name = validate_domain(params["domain"])
    kind = params.get("kind", "addon")
    if kind not in ("primary", "addon", "subdomain"):
        raise ValueError("kind must be primary, addon, or subdomain")

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        if account.status not in ("active", "suspended"):
            raise RuntimeError(f"cannot add a domain to an account in status '{account.status}'")

        existing = session.scalar(select(Domain).where(Domain.domain == domain_name))
        if existing is not None:
            raise RuntimeError(f"domain '{domain_name}' is already in use")

        is_primary = kind == "primary"
        docroot = (
            f"{settings.home_base}/{username}/public_html"
            if is_primary
            else f"{settings.home_base}/{username}/{domain_name}"
        )
        domain = Domain(account_id=account.id, domain=domain_name, kind=kind, docroot=docroot)
        session.add(domain)
        if is_primary:
            account.primary_domain = domain_name
        session.flush()
        domain_dict = _domain_to_dict(domain)
        account_snapshot = account

    # provision_vhost needs this Domain row in the DB to render the regenerated
    # httpd_config.conf (it queries all domains for the account), so the row
    # has to be committed before the OLS apply -- which means a failed OLS
    # apply must be compensated by deleting the row, or it's left orphaned
    # with no corresponding vhost (caught by real end-to-end testing below).
    parent_zone = _find_parent_zone(domain_name)
    dns_label = _subdomain_label(domain_name, parent_zone) if parent_zone else None
    dns_record_created = False
    try:
        ensure_docroot(username, docroot, domain_name)
        if parent_zone and settings.server_public_ip:
            dnsprovider.upsert_record(parent_zone, dns_label, "A", [settings.server_public_ip])
            dns_record_created = True
        ols.provision_vhost(account_snapshot)
    except Exception:
        if dns_record_created:
            try:
                dnsprovider.delete_record(parent_zone, dns_label, "A")
            except dnsprovider.DnsError:
                pass  # best-effort; the DB-row compensation below is authoritative
        with write_session() as session:
            orphan = session.scalar(select(Domain).where(Domain.domain == domain_name))
            if orphan is not None:
                session.delete(orphan)
            if is_primary:
                account = session.scalar(select(Account).where(Account.username == username))
                account.primary_domain = None
        raise

    domain_dict["dns_record_created"] = dns_record_created
    return domain_dict


def remove_domain(params: dict) -> dict:
    """Subdomain/addon delete (Phase 2 feature 4). Deliberately refuses to
    remove an account's primary domain -- that's not "delete a domain", an
    account without a primary domain is a different, unhandled state; use
    account.terminate or reassign the primary domain instead.

    Files (docroot, logs) are deliberately left on disk: removing a domain
    is a routing change, not a request to destroy the customer's content.
    Only the OLS vhost/serving path (and, if this add created one, the
    subdomain's DNS A record) is torn down."""
    username = validate_username(params["username"])
    domain_name = validate_domain(params["domain"])

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        domain = session.scalar(
            select(Domain).where(Domain.domain == domain_name, Domain.account_id == account.id)
        )
        if domain is None:
            raise RuntimeError(f"domain '{domain_name}' not found for account '{username}'")
        if domain.kind == "primary":
            raise RuntimeError("cannot remove an account's primary domain -- terminate or reassign the account instead")
        kind = domain.kind
        account_snapshot = account
        session.delete(domain)

    parent_zone = _find_parent_zone(domain_name)
    if parent_zone:
        label = _subdomain_label(domain_name, parent_zone)
        try:
            dnsprovider.delete_record(parent_zone, label, "A")
        except dnsprovider.DnsError:
            pass  # already gone or zone unreachable -- vhost removal below is what actually matters

    ols.remove_domain_vhost(account_snapshot, domain_name)
    handlers_redirect.delete_redirects_for_domain(domain_name)
    lscache.delete_settings_for_domain(domain_name)
    # Phase 8 feature 4: drop any whole-domain forwarding row for this domain.
    from daemon import forwarding

    forwarding.delete_forwarding_for_domain(domain_name)
    # Phase 8 feature 6: drop any email-routing row (refreshes the relay map).
    from daemon import handlers_email_routing

    handlers_email_routing.delete_routing_for_domain(domain_name)
    # Missing-features batch, goal features 2/3: drop any maintenance-mode /
    # wildcard-domain bookkeeping row for this domain (the vhost itself is
    # already gone, ols.remove_domain_vhost above).
    from daemon import handlers_maintenance, handlers_wildcard

    handlers_maintenance.delete_maintenance_for_domain(domain_name)
    handlers_wildcard.delete_wildcard_for_domain(domain_name)

    return {"domain": domain_name, "kind": kind, "status": "removed"}


def list_domains(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        domains = session.scalars(select(Domain).where(Domain.account_id == account.id)).all()
        return {"domains": [_domain_to_dict(d) for d in domains]}


def set_domain_php_version(params: dict) -> dict:
    """Phase 7a feature 6: per-domain PHP version override. `php_version`
    empty/None clears the override (back to inheriting Account.php_version)
    -- the same "empty means reset to default" convention
    handlers_php_ini.reset_php_ini's absence-of-a-row already establishes,
    applied here as a column value instead of a whole row's presence,
    since Domain itself is not a new-table-per-override the way
    PhpIniOverride is (a domain already has a row for other reasons).

    ols.refresh_vhost regenerates this account's ENTIRE vhost set (this
    project's established declarative-full-regen pattern, ARCHITECTURE.md
    SS6/SS7) -- functionally this only changes the touched domain's own
    scripthandler target and, if no other domain under the account still
    uses the previous effective version, removes that now-unused
    extProcessor block; every other domain (this account's and every other
    account's) keeps serving throughout, the same "no impact to other
    domains/accounts" guarantee every other account-level config change in
    this project already provides via the shared reload-safety pipeline."""
    username = validate_username(params["username"])
    domain_name = validate_domain(params["domain"])
    raw_version = params.get("php_version") or None
    version = validate_php_version(raw_version, settings.php_versions) if raw_version else None

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        domain = session.scalar(select(Domain).where(Domain.domain == domain_name, Domain.account_id == account.id))
        if domain is None:
            raise RuntimeError(f"domain '{domain_name}' not found for account '{username}'")
        domain.php_version = version
        session.flush()
        result = _domain_to_dict(domain)
        account_snapshot = account

    ols.refresh_vhost(account_snapshot)
    return result


def ensure_docroot(username: str, docroot: str, domain_name: str | None = None) -> None:
    import os
    import pwd

    from daemon import safeio

    pw = pwd.getpwnam(username)
    home = f"{settings.home_base}/{username}"
    rel = os.path.relpath(docroot, home)
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        raise RuntimeError(f"docroot '{docroot}' is not inside account home '{home}'")
    # Symlink-safe create+chown. The account can write its own home, so a naive
    # os.makedirs(exist_ok=True)+os.chown here is a root privilege-escalation
    # primitive (a symlink planted at docroot makes root chown its target) --
    # secure_mkdirs creates/re-owns each component through O_NOFOLLOW fds.
    #
    # 750, not world-readable: confirmed by real testing that OLS's
    # "DocRoot UID" vhost setting only affects the LSAPI/PHP external app's
    # uid (extUser/extGroup, already set) -- the main worker process that
    # serves static files and locates index.php in the first place keeps
    # running as the server-wide "nobody" user the whole time, it never
    # actually switches uid per request. Making the docroot world-readable
    # (755) to work around that would let *every* Linux account on the box
    # read every other account's files via a plain `cat` -- caught by
    # cross-account testing while building Phase b. The correct fix, same
    # pattern cPanel/DirectAdmin use: grant read+traverse via a POSIX ACL
    # scoped to the one shared web-server uid ("nobody"), not by loosening
    # the "other" bits for every local user.
    safeio.secure_mkdirs(home, rel, pw.pw_uid, pw.pw_gid, 0o750)
    _grant_webserver_acl(docroot)

    # vhost.conf.j2 always declares a context for this path (ACME HTTP-01
    # webroot, Phase f) -- OLS's `-t` rejects a context whose location
    # doesn't exist yet, so it must be created at domain-add time, not
    # deferred until SSL issuance. 0755 (like the original makedirs default)
    # so the challenge stays reachable; `nobody` is granted read via the
    # default ACL _grant_webserver_acl just set recursively on docroot.
    safeio.secure_mkdirs(docroot, ".well-known/acme-challenge", pw.pw_uid, pw.pw_gid, 0o755)

    # Shared with sysops.create_linux_user (account-creation time) and
    # ols.refresh_all_vhosts's migration pass (pre-existing accounts) --
    # one place owns this directory's creation/perms.
    safeio.secure_mkdirs(home, "logs", pw.pw_uid, pw.pw_gid, 0o750)
    sysops.ensure_tmp_dir(username)

    # Missing-features batch, goal feature 4: every domain's vhost
    # unconditionally declares a context for /.forgehost-error-pages/
    # (daemon/ols.py, daemon/custom_pages.py) -- same "OLS -t rejects a
    # context whose location doesn't exist yet" reasoning as the
    # acme-challenge dir above, so this has to exist at domain-add time too,
    # not deferred until the customer first uploads a custom error page.
    if domain_name is not None:
        from daemon import custom_pages

        custom_pages.ensure_pages_dir(username, domain_name)


def _grant_webserver_acl(docroot: str) -> None:
    """Read+traverse ACL for OLS's worker uid ("nobody", per
    httpd_config.conf.j2's top-level `user`/`group`), recursively and as a
    default ACL so files/dirs created later (uploads, file manager, deploys)
    inherit it automatically without another setfacl call.
    """
    from daemon.procutil import run

    run(
        [
            "setfacl", "-R",
            "-m", "u:nobody:rX",
            "-d", "-m", "u:nobody:rX",
            docroot,
        ],
        check=True,
    )
