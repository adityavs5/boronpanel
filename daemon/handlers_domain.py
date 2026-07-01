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
from shared.models import Account, Domain, DnsZone
from shared.validation import validate_domain, validate_username

from daemon import ols, powerdns


def _domain_to_dict(domain: Domain) -> dict:
    return {
        "id": domain.id,
        "account_id": domain.account_id,
        "domain": domain.domain,
        "kind": domain.kind,
        "docroot": domain.docroot,
        "ssl_status": domain.ssl_status,
        "created_at": domain.created_at.isoformat() if domain.created_at else None,
    }


def _find_parent_zone(domain_name: str) -> str | None:
    """A subdomain doesn't get its own DNS zone -- it's an A record within
    whatever zone already covers it, same as any real-world DNS setup (a
    fresh zone per subdomain would be unusual and wasteful). Only matches
    zones Forgehost itself manages (DnsZone rows); if the covering domain's
    DNS is hosted elsewhere, there's nothing for us to automate here, same
    reasoning as ssl.py's zone_managed check in _challenge_plan."""
    with write_session() as session:
        zones = session.scalars(select(DnsZone.zone)).all()
    return next((z for z in zones if domain_name == z or domain_name.endswith(f".{z}")), None)


def _subdomain_label(domain_name: str, parent_zone: str) -> str:
    if domain_name == parent_zone:
        return "@"
    return domain_name[: -(len(parent_zone) + 1)]


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
        ensure_docroot(username, docroot)
        if parent_zone and settings.server_public_ip:
            powerdns.upsert_record(parent_zone, dns_label, "A", [settings.server_public_ip])
            dns_record_created = True
        ols.provision_vhost(account_snapshot)
    except Exception:
        if dns_record_created:
            try:
                powerdns.delete_record(parent_zone, dns_label, "A")
            except powerdns.PowerDnsError:
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
            powerdns.delete_record(parent_zone, label, "A")
        except powerdns.PowerDnsError:
            pass  # already gone or zone unreachable -- vhost removal below is what actually matters

    ols.remove_domain_vhost(account_snapshot, domain_name)

    return {"domain": domain_name, "kind": kind, "status": "removed"}


def list_domains(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        domains = session.scalars(select(Domain).where(Domain.account_id == account.id)).all()
        return {"domains": [_domain_to_dict(d) for d in domains]}


def ensure_docroot(username: str, docroot: str) -> None:
    import os
    import pwd

    pw = pwd.getpwnam(username)
    os.makedirs(docroot, exist_ok=True)
    os.chown(docroot, pw.pw_uid, pw.pw_gid)
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
    os.chmod(docroot, 0o750)
    _grant_webserver_acl(docroot)

    # vhost.conf.j2 always declares a context for this path (ACME HTTP-01
    # webroot, Phase f) -- OLS's `-t` rejects a context whose location
    # doesn't exist yet, so it must be created at domain-add time, not
    # deferred until SSL issuance.
    acme_dir = f"{docroot}/.well-known/acme-challenge"
    os.makedirs(acme_dir, exist_ok=True)
    os.chown(f"{docroot}/.well-known", pw.pw_uid, pw.pw_gid)
    os.chown(acme_dir, pw.pw_uid, pw.pw_gid)

    logs_dir = f"{settings.home_base}/{username}/logs"
    os.makedirs(logs_dir, exist_ok=True)
    os.chown(logs_dir, pw.pw_uid, pw.pw_gid)
    tmp_dir = f"{settings.home_base}/{username}/tmp"
    os.makedirs(tmp_dir, exist_ok=True)
    os.chown(tmp_dir, pw.pw_uid, pw.pw_gid)
    os.chmod(tmp_dir, 0o750)


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
