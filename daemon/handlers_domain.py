"""Domain CRUD: addon/subdomain support for Phase b's vhost templating, and
the foundation Phase c's DNS zones build on top of.
"""
from __future__ import annotations

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain
from shared.validation import validate_domain, validate_username

from daemon import ols


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
    try:
        _ensure_docroot(username, docroot)
        ols.provision_vhost(account_snapshot, _domains_for_account(account_snapshot.id))
    except Exception:
        with write_session() as session:
            orphan = session.scalar(select(Domain).where(Domain.domain == domain_name))
            if orphan is not None:
                session.delete(orphan)
            if is_primary:
                account = session.scalar(select(Account).where(Account.username == username))
                account.primary_domain = None
        raise

    return domain_dict


def list_domains(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        domains = session.scalars(select(Domain).where(Domain.account_id == account.id)).all()
        return {"domains": [_domain_to_dict(d) for d in domains]}


def _domains_for_account(account_id: int) -> list[str]:
    with write_session() as session:
        return list(session.scalars(select(Domain.domain).where(Domain.account_id == account_id)).all())


def _ensure_docroot(username: str, docroot: str) -> None:
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
