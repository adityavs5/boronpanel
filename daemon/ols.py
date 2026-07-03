"""OLS vhost + PHP/LSAPI provisioning (Phase b).

ARCHITECTURE.md SS6: one extprocessor (LSAPI external app) per account, uid-
pinned via extUser/extGroup, ProcessGroup mode (the default -- we never set
"Daemon" mode, which would share a PHP pool across accounts). httpd_config.conf
is regenerated in full on every change rather than incrementally patched
(SS6/SS7) -- see ConfigWriterMulti's docstring for why, including the
OLS-specific adaptation of "validate before reload" forced by `openlitespeed
-t` only validating the live installed config tree.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain, FileAuthDir, PhpIniOverride, Redirect

from daemon.configtx import ConfigWriterMulti, StepResult
from daemon.procutil import run

logger = logging.getLogger("forgehostd.ols")

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)

HTTPD_CONFIG_PATH = "/usr/local/lsws/conf/httpd_config.conf"
VHOST_CONF_TEMPLATE = "{base}/conf/vhosts/{name}/vhconf.conf"
OLS_SERVER_BASE = "/usr/local/lsws"

RESOURCE_DEFAULTS = {
    "mem_soft_limit": "512M",
    "mem_hard_limit": "1024M",
    "proc_soft_limit": 50,
    "proc_hard_limit": 75,
}


def _php_app_name(username: str, php_version: str) -> str:
    return f"{username}_php{php_version.replace('.', '')}"


def _lsphp_path(php_version: str) -> str:
    return f"{OLS_SERVER_BASE}/lsphp{php_version.replace('.', '')}/bin/lsphp"


def _vhost_name(domain: str) -> str:
    """Phase 2 feature 4: domain -> vhost identifier. Collision-free: valid
    domains (shared/validation.py's DOMAIN_RE) only ever contain
    [a-z0-9-.], never underscore, so replacing '.' with '_' can't map two
    distinct valid domains to the same vhost name."""
    return domain.replace(".", "_")


def _vhost_conf_path(vhost_name: str) -> str:
    return VHOST_CONF_TEMPLATE.format(base=OLS_SERVER_BASE, name=vhost_name)


DEFAULT_SSL_KEY = "/etc/forgehost/ssl/default.key"
DEFAULT_SSL_CERT = "/etc/forgehost/ssl/default.crt"


def letsencrypt_cert_paths(cert_name: str) -> tuple[str, str]:
    """Predictable certbot storage layout -- no need to track literal paths
    in our own DB, they're deterministic from the cert's lineage name
    (the primary domain it was requested for)."""
    base = f"/etc/letsencrypt/live/{cert_name}"
    return f"{base}/privkey.pem", f"{base}/fullchain.pem"


def _domain_row_to_plain(d: Domain) -> dict:
    """Plain-value snapshot of a Domain row -- render_vhost_conf/
    _ssl_paths_for_domain only need domain/docroot/ssl_status, and pulling
    these out as plain values (rather than holding a live ORM object
    across a later, separate write_session() block) avoids a
    DetachedInstanceError -- the same reason refresh_vhost/suspend_vhost/
    unsuspend_vhost have always selected Domain.domain scalar strings
    rather than full Domain rows, now applied to the other Domain fields
    Phase 2 feature 4 needs too."""
    return {
        "id": d.id,
        "domain": d.domain,
        "docroot": d.docroot,
        "ssl_status": d.ssl_status,
        "hotlink_protection_enabled": d.hotlink_protection_enabled,
        "hotlink_allowed_domains": d.hotlink_allowed_domains or [],
        "ip_block_list": d.ip_block_list or [],
    }


def _ssl_paths_for_domain(domain: dict) -> tuple[str, str]:
    """Phase 2 feature 4: one cert per DOMAIN now, not one per account
    keyed to its primary domain -- each domain's own Domain.ssl_status
    already existed (Phase f), it just wasn't wired to per-domain vhosts
    since there weren't any yet. Falls back to the bootstrap self-signed
    cert until/unless a real one has been issued for that specific
    domain."""
    if domain["ssl_status"] == "active":
        key, cert = letsencrypt_cert_paths(domain["domain"])
        if Path(key).exists() and Path(cert).exists():
            return key, cert
    return DEFAULT_SSL_KEY, DEFAULT_SSL_CERT


def _php_ini_for_account(session, account_id: int) -> dict | None:
    row = session.scalar(select(PhpIniOverride).where(PhpIniOverride.account_id == account_id))
    if row is None:
        return None
    return {
        "memory_limit": row.memory_limit,
        "upload_max_filesize": row.upload_max_filesize,
        "post_max_size": row.post_max_size,
        "max_execution_time": row.max_execution_time,
        "display_errors": row.display_errors,
        "error_reporting": row.error_reporting,
    }


def _redirects_for_domain(session, domain_name: str) -> list[dict]:
    rows = session.scalars(select(Redirect).where(Redirect.domain == domain_name)).all()
    return [
        {
            # Escaped here, once, in Python -- never in the Jinja template --
            # so the raw validated path is never interpolated directly into
            # an OLS rewrite-rule regex pattern.
            "regex_path": r.path.replace(".", "\\."),
            "target_url": r.target_url,
            "status_code": r.status_code,
        }
        for r in rows
    ]


def _protected_dirs_for_domain(session, username: str, account_id: int, docroot: str) -> list[dict]:
    """Phase 4 feature 4. FileAuthDir.path is relative to the account's
    HOME dir (daemon/fileauth.py, matching daemon/filemanager.py's own
    convention) -- rebased here to be relative to THIS domain's docroot,
    since that's what the vhost's own `location`/context path needs (a
    domain's docroot is some subdirectory of the account home, e.g.
    `<home>/public_html`, and a protected dir several levels under that
    needs a context path relative to the docroot, not the home)."""
    docroot_real = os.path.realpath(docroot)
    home = os.path.realpath(f"{settings.home_base}/{username}")
    rows = session.scalars(select(FileAuthDir).where(FileAuthDir.account_id == account_id)).all()
    result = []
    for row in rows:
        absolute = os.path.realpath(os.path.join(home, row.path))
        if absolute != docroot_real and not absolute.startswith(docroot_real + os.sep):
            continue
        relative_to_docroot = os.path.relpath(absolute, docroot_real)
        result.append({
            "realm_name": row.realm_name,
            "relative_path": "" if relative_to_docroot == "." else relative_to_docroot,
            "htpasswd_path": os.path.join(absolute, ".htpasswd"),
        })
    return result


HOTLINK_PROTECTED_EXTENSIONS = "jpg|jpeg|png|gif|bmp|webp|svg|ico|mp4|mp3"


def _hotlink_context(domain: dict) -> dict | None:
    """Phase 4 feature 2. Standard mod_rewrite-compatible hotlink recipe
    (OLS's rewrite engine is Apache mod_rewrite-compatible, same engine
    already used for suspended-page/redirect rules): block only when
    Referer is present AND doesn't match this domain or an allow-listed
    one -- empty Referer (direct navigation, bookmarks, many privacy-
    conscious browsers/extensions that strip it) is deliberately always
    allowed, never blocked; blocking it too would break far more
    legitimate traffic than it protects."""
    if not domain.get("hotlink_protection_enabled"):
        return None
    # Escaped here, once, in Python -- never in the Jinja template -- same
    # discipline _redirects_for_domain already applies to Redirect.path.
    allowed = [domain["domain"]] + list(domain.get("hotlink_allowed_domains") or [])
    return {"allowed_domains_escaped": [d.replace(".", "\\.") for d in allowed], "extensions": HOTLINK_PROTECTED_EXTENSIONS}


def render_vhost_conf(
    account: Account,
    domain: dict,
    suspended: bool,
    ssl_key_file: str = DEFAULT_SSL_KEY,
    ssl_cert_file: str = DEFAULT_SSL_CERT,
    php_ini: dict | None = None,
    redirects: list[dict] | None = None,
    protected_dirs: list[dict] | None = None,
) -> str:
    home_dir = f"{settings.home_base}/{account.username}"
    template = _env.get_template("vhost.conf.j2")
    return template.render(
        vhost_name=_vhost_name(domain["domain"]),
        docroot=domain["docroot"],
        home_dir=home_dir,
        php_app_name=_php_app_name(account.username, account.php_version),
        suspended=suspended,
        suspended_page_root=settings.suspended_page_root,
        ssl_key_file=ssl_key_file,
        ssl_cert_file=ssl_cert_file,
        php_ini=php_ini,
        redirects=redirects,
        hotlink=_hotlink_context(domain),
        ip_block_list=domain.get("ip_block_list") or [],
        protected_dirs=protected_dirs or [],
    )


def _all_active_vhosts(session) -> tuple[list[dict], list[dict]]:
    """Every account that should have a live vhost: active or suspended (a
    suspended account still needs its vhosts present, just serving the
    suspended page -- ARCHITECTURE.md SS10). terminated/terminating/error
    accounts are excluded.

    Returns (domain_vhosts, account_procs):
      domain_vhosts -- one entry per (account, domain) pair. Phase 2
        feature 4 moved this from one-vhost-per-account to
        one-vhost-per-domain, so each domain gets its own docRoot/listener
        map entry/SSL cert instead of every domain under an account
        silently serving the same public_html content (the Phase 1 gap
        this feature exists to fix).
      account_procs -- one extProcessor per account with >=1 domain,
        deduplicated: php_version is an account-level setting, so every
        domain-vhost under the same account shares ONE extprocessor,
        referenced by name from each vhost's own scripthandler rather than
        each domain spawning a redundant LSAPI backend of its own.
    """
    accounts = session.scalars(
        select(Account).where(Account.status.in_(["active", "suspended"]))
    ).all()
    domain_vhosts = []
    account_procs = []
    for account in accounts:
        # Domain rows are the only source of truth for what gets a listener
        # map entry. Account.primary_domain is a denormalized display field
        # only -- falling back to it here previously caused a phantom vhost
        # to be regenerated after a failed domain.add's compensation logic
        # deleted the Domain row but raced with this read, found during
        # Phase b's real end-to-end testing.
        domains = session.scalars(select(Domain.domain).where(Domain.account_id == account.id)).all()
        if not domains:
            continue  # an account with no domain yet has no vhost/listener entry
        account_home = f"{settings.home_base}/{account.username}"
        account_procs.append({
            "username": account.username,
            "php_app_name": _php_app_name(account.username, account.php_version),
            "lsphp_path": _lsphp_path(account.php_version),
        })
        for domain_name in domains:
            domain_vhosts.append({
                "vhost_name": _vhost_name(domain_name),
                "domain": domain_name,
                "account_home": account_home,
            })
    return domain_vhosts, account_procs


WEBMAIL_VHOST_NAME = "roundcube"


def _webmail_ssl_paths(session) -> tuple[str, str]:
    """Same pattern as _ssl_paths_for_domain, keyed to the static webmail
    hostname instead of a Domain row -- Roundcube gets a
    real cert the same way any other vhost does (see ssl.issue's
    challenge-plan; it just isn't tied to a Domain row's account)."""
    if not settings.webmail_hostname:
        return DEFAULT_SSL_KEY, DEFAULT_SSL_CERT
    key, cert = letsencrypt_cert_paths(settings.webmail_hostname)
    if Path(key).exists() and Path(cert).exists():
        return key, cert
    return DEFAULT_SSL_KEY, DEFAULT_SSL_CERT


def render_httpd_config(domain_vhosts: list[dict], account_procs: list[dict]) -> str:
    template = _env.get_template("httpd_config.conf.j2")
    return template.render(
        server_name="forgehost",
        admin_email="root@localhost",
        min_uid=11,
        min_gid=10,
        default_php_version_nodot=settings.default_php_version.replace(".", ""),
        default_ssl_key="/etc/forgehost/ssl/default.key",
        default_ssl_cert="/etc/forgehost/ssl/default.crt",
        domain_vhosts=domain_vhosts,
        account_procs=account_procs,
        webmail_hostname=settings.webmail_hostname,
        webmail_docroot=settings.webmail_docroot,
        webmail_lsphp_path=_lsphp_path(settings.default_php_version),
        pma_hostname=settings.pma_hostname,
        pma_docroot=settings.pma_docroot,
        pma_lsphp_path=_lsphp_path(settings.default_php_version),
        **RESOURCE_DEFAULTS,
    )


def _webmail_vhost_conf_path() -> str:
    return VHOST_CONF_TEMPLATE.format(base=OLS_SERVER_BASE, name=WEBMAIL_VHOST_NAME)


def render_webmail_vhost_conf(ssl_key_file: str, ssl_cert_file: str) -> str:
    template = _env.get_template("roundcube_vhost.conf.j2")
    return template.render(
        docroot=settings.webmail_docroot,
        log_dir=settings.log_dir,
        ssl_key_file=ssl_key_file,
        ssl_cert_file=ssl_cert_file,
    )


def bootstrap_webmail() -> None:
    """One-time (well, idempotent -- safe to re-run) infra setup, same
    category as bootstrap_baseline: Roundcube isn't an account resource, so
    nothing in the normal account.create/domain.add flow ever triggers
    this. Run explicitly after Roundcube itself is installed and
    configured (README/CHECKPOINT-phase2-3.md)."""
    if not settings.webmail_hostname:
        raise RuntimeError("webmail_hostname is not set in forgehost.toml")

    with write_session() as session:
        domain_vhosts, account_procs = _all_active_vhosts(session)
        ssl_key_file, ssl_cert_file = _webmail_ssl_paths(session)

    httpd_content = render_httpd_config(domain_vhosts, account_procs)
    webmail_content = render_webmail_vhost_conf(ssl_key_file, ssl_cert_file)

    writer = ConfigWriterMulti(
        targets={"main": HTTPD_CONFIG_PATH, "webmail": _webmail_vhost_conf_path()},
        validate=_validate_multi,
        reload=_reload,
        verify=_verify,
        backup_dir=settings.backup_dir,
        subsystem="ols",
    )
    result = writer.apply({"main": httpd_content, "webmail": webmail_content})
    if not result.ok:
        raise RuntimeError(f"OLS config transaction failed during bootstrap_webmail: {result.summary()}")


def refresh_webmail_vhost() -> None:
    """Re-apply webmail's vhost with current SSL status -- used by the
    certbot deploy-hook once a real cert for webmail_hostname is issued,
    same role refresh_vhost() plays for account domains."""
    bootstrap_webmail()


PMA_VHOST_NAME = "phpmyadmin"


def _pma_ssl_paths(session) -> tuple[str, str]:
    if not settings.pma_hostname:
        return DEFAULT_SSL_KEY, DEFAULT_SSL_CERT
    key, cert = letsencrypt_cert_paths(settings.pma_hostname)
    if Path(key).exists() and Path(cert).exists():
        return key, cert
    return DEFAULT_SSL_KEY, DEFAULT_SSL_CERT


def _pma_vhost_conf_path() -> str:
    return VHOST_CONF_TEMPLATE.format(base=OLS_SERVER_BASE, name=PMA_VHOST_NAME)


def render_pma_vhost_conf(ssl_key_file: str, ssl_cert_file: str) -> str:
    template = _env.get_template("pma_vhost.conf.j2")
    return template.render(
        docroot=settings.pma_docroot,
        log_dir=settings.log_dir,
        ssl_key_file=ssl_key_file,
        ssl_cert_file=ssl_cert_file,
    )


def bootstrap_pma() -> None:
    """Same category as bootstrap_webmail -- phpMyAdmin isn't an account
    resource, so nothing in the normal account/domain flow triggers this.
    Run once (idempotent) after phpMyAdmin itself is installed and
    daemon/pma.py has written config.inc.php + the signon script into its
    docroot (README/CHECKPOINT-phase3-3.md)."""
    if not settings.pma_hostname:
        raise RuntimeError("pma_hostname is not set in forgehost.toml")

    with write_session() as session:
        domain_vhosts, account_procs = _all_active_vhosts(session)
        ssl_key_file, ssl_cert_file = _pma_ssl_paths(session)

    httpd_content = render_httpd_config(domain_vhosts, account_procs)
    pma_content = render_pma_vhost_conf(ssl_key_file, ssl_cert_file)

    writer = ConfigWriterMulti(
        targets={"main": HTTPD_CONFIG_PATH, "pma": _pma_vhost_conf_path()},
        validate=_validate_multi,
        reload=_reload,
        verify=_verify,
        backup_dir=settings.backup_dir,
        subsystem="ols",
    )
    result = writer.apply({"main": httpd_content, "pma": pma_content})
    if not result.ok:
        raise RuntimeError(f"OLS config transaction failed during bootstrap_pma: {result.summary()}")


def refresh_pma_vhost() -> None:
    bootstrap_pma()


def _static_precheck(content: str) -> StepResult:
    if content.count("{") != content.count("}"):
        return StepResult(False, "unbalanced braces")
    if not content.strip():
        return StepResult(False, "empty config")
    return StepResult(True)


def _validate_multi(tmp_paths: dict[str, Path]) -> StepResult:
    for name, path in tmp_paths.items():
        result = _static_precheck(path.read_text())
        if not result.ok:
            return StepResult(False, f"{name}: {result.message}")
    return StepResult(True)


def _reload() -> StepResult:
    # Authoritative validation happens here, against the now-live config
    # tree, because `openlitespeed -t` ignores any path override -- see
    # ConfigWriterMulti's docstring. A failure here is what triggers
    # ConfigWriterMulti's rollback; lshttpd is never told to reload an
    # invalid config.
    check = run([f"{OLS_SERVER_BASE}/bin/openlitespeed", "-t"], timeout=20)
    if not check.ok:
        return StepResult(False, f"openlitespeed -t failed: {check.stderr.strip() or check.stdout.strip()}")

    reload_result = run(["systemctl", "reload", "lshttpd"], timeout=30)
    if not reload_result.ok:
        return StepResult(False, f"systemctl reload lshttpd failed: {reload_result.stderr.strip()}")
    return StepResult(True)


def _verify() -> StepResult:
    active = run(["systemctl", "is-active", "lshttpd"], timeout=10)
    if active.stdout.strip() != "active":
        return StepResult(False, f"lshttpd not active after reload: {active.stdout.strip()}")
    return StepResult(True)


def _domains_as_plain(account_id: int) -> list[dict]:
    with write_session() as session:
        rows = session.scalars(select(Domain).where(Domain.account_id == account_id)).all()
        return [_domain_row_to_plain(d) for d in rows]


def _apply_targets(account: Account, domains: list[dict], suspended: bool, context: str) -> None:
    """Regenerate every vhost file in `domains` (each gets its own docRoot/
    SSL cert/log files -- Phase 2 feature 4) plus httpd_config.conf, as ONE
    atomic transaction. All of an account's domain-vhosts must move
    together: a PHP-version switch changes which server-level extprocessor
    name every single one of them references, so applying only some would
    leave the rest pointed at a since-renamed extprocessor."""
    with write_session() as session:
        domain_vhosts, account_procs = _all_active_vhosts(session)
        php_ini = _php_ini_for_account(session, account.id)
        redirects_by_domain = {domain["domain"]: _redirects_for_domain(session, domain["domain"]) for domain in domains}
        protected_dirs_by_domain = {
            domain["domain"]: _protected_dirs_for_domain(session, account.username, account.id, domain["docroot"])
            for domain in domains
        }

    targets = {"main": HTTPD_CONFIG_PATH}
    content = {"main": render_httpd_config(domain_vhosts, account_procs)}
    for domain in domains:
        vhost_name = _vhost_name(domain["domain"])
        ssl_key_file, ssl_cert_file = _ssl_paths_for_domain(domain)
        targets[vhost_name] = _vhost_conf_path(vhost_name)
        content[vhost_name] = render_vhost_conf(
            account, domain, suspended,
            ssl_key_file=ssl_key_file, ssl_cert_file=ssl_cert_file,
            php_ini=php_ini, redirects=redirects_by_domain[domain["domain"]],
            protected_dirs=protected_dirs_by_domain[domain["domain"]],
        )

    writer = ConfigWriterMulti(
        targets=targets,
        validate=_validate_multi,
        reload=_reload,
        verify=_verify,
        backup_dir=settings.backup_dir,
        subsystem="ols",
    )
    result = writer.apply(content)
    if not result.ok:
        raise RuntimeError(f"OLS config transaction failed during {context}: {result.summary()}")


def provision_vhost(account: Account) -> None:
    """Called once a new Domain row is committed (handlers_domain.add_domain)
    -- regenerates every one of the account's domain-vhosts, including the
    brand new one, plus httpd_config.conf."""
    domains = _domains_as_plain(account.id)
    if not domains:
        return
    _apply_targets(account, domains, suspended=False, context="provision")


def refresh_vhost(account: Account) -> None:
    """Re-render and re-apply every one of this account's domain-vhosts
    from current DB state (SSL status per domain, PHP version) -- used by
    the certbot deploy-hook (Phase f) after marking a domain's ssl_status
    active, and by the PHP-version-switch hook (Phase 2 feature 1)."""
    domains = _domains_as_plain(account.id)
    if not domains:
        return
    _apply_targets(account, domains, suspended=(account.status == "suspended"), context="refresh")


def suspend_vhost(account: Account) -> None:
    domains = _domains_as_plain(account.id)
    if not domains:
        return
    _apply_targets(account, domains, suspended=True, context="suspend")


def unsuspend_vhost(account: Account) -> None:
    domains = _domains_as_plain(account.id)
    if not domains:
        return
    _apply_targets(account, domains, suspended=False, context="unsuspend")


def remove_domain_vhost(account: Account, removed_domain_name: str) -> None:
    """Called after a Domain row has already been deleted from the DB
    (handlers_domain.remove_domain) -- regenerates every REMAINING
    domain-vhost for this account plus httpd_config.conf (main is always
    regenerated here, even when zero domains remain, to purge the removed
    domain's now-stale extprocessor/vhost/listener-map entries -- unlike
    provision/refresh/suspend, "no domains left" is a real state change
    here, not a no-op), then removes the deleted domain's own vhost
    directory from disk."""
    remaining = _domains_as_plain(account.id)
    _apply_targets(account, remaining, suspended=(account.status == "suspended"), context="remove_domain")

    import shutil

    vhost_dir = Path(OLS_SERVER_BASE) / "conf" / "vhosts" / _vhost_name(removed_domain_name)
    shutil.rmtree(vhost_dir, ignore_errors=True)


def terminate_vhost(account: Account) -> None:
    """Remove every one of this account's domain-vhost directories +
    regenerate httpd_config.conf without them. Idempotent: safe to call
    even if the account never had a domain/vhost provisioned.

    Account.status is already 'terminating' (set before TERMINATE_HOOKS
    run -- see handlers_account.terminate_account) by the time this runs,
    which is neither 'active' nor 'suspended', so _all_active_vhosts()
    below already excludes this account's domains from the regenerated
    main config without any extra filtering needed here."""
    with write_session() as session:
        domain_names = list(session.scalars(select(Domain.domain).where(Domain.account_id == account.id)).all())
        domain_vhosts, account_procs = _all_active_vhosts(session)

    if not domain_names:
        return

    _apply_main_only(domain_vhosts, account_procs, context="terminate")

    import shutil

    for domain_name in domain_names:
        vhost_dir = Path(OLS_SERVER_BASE) / "conf" / "vhosts" / _vhost_name(domain_name)
        shutil.rmtree(vhost_dir, ignore_errors=True)


def _apply_main_only(domain_vhosts: list[dict], account_procs: list[dict], context: str) -> None:
    httpd_content = render_httpd_config(domain_vhosts, account_procs)
    writer = ConfigWriterMulti(
        targets={"main": HTTPD_CONFIG_PATH},
        validate=_validate_multi,
        reload=_reload,
        verify=_verify,
        backup_dir=settings.backup_dir,
        subsystem="ols",
    )
    result = writer.apply({"main": httpd_content})
    if not result.ok:
        raise RuntimeError(f"OLS config transaction failed during {context}: {result.summary()}")


def bootstrap_baseline() -> None:
    """Replace the stock OLS install's bundled 'Example' vhost with a clean,
    Forgehost-managed httpd_config.conf containing whatever vhosts already
    exist in the DB (none, on a fresh install).

    Run once, deliberately, rather than automatically on every daemon start
    (a config-mutating action on startup would be surprising). The stock
    Example vhost's docroot is owned by root with a uid/gid below OLS's own
    CGIRLimit minUID/minGID, which makes `openlitespeed -t` exit non-zero
    even though it's only a warning -- confirmed empirically while building
    Phase b. Leaving it in place would make every future rollback target
    look like a validation failure too, since rollback restores whatever
    config was live before the failed change.
    """
    with write_session() as session:
        domain_vhosts, account_procs = _all_active_vhosts(session)
    _apply_main_only(domain_vhosts, account_procs, context="bootstrap_baseline")
