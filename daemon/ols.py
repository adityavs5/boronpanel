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
import json
from pathlib import Path
import pwd
import re
import secrets
import stat
import string

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from sqlalchemy import func, select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain, DomainForwarding, FileAuthDir, LscacheSettings, MaintenanceMode, NodeApp, OlsServerSettings, PhpExtensionSet, PhpIniDirective, PhpIniOverride, PythonApp, Redirect, WafCustomRule, WafDomainOverride, WafSettings, WildcardDomain, utcnow
from shared.validation import ValidationError, validate_domain

from daemon.configtx import ConfigWriterMulti, StepResult
from daemon.procutil import run
from daemon import phpdirectives, phpfunctions, sysops

logger = logging.getLogger("borond.ols")

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)

# Missing-features batch, goal feature 4: shared, server-wide Boron-branded
# default error pages, shipped with the app -- see daemon/custom_pages.py's
# module docstring for the full context/resolution story.
# Generated configurations outlive the release that rendered them. Resolve
# assets through the live installation link so pruning old releases is safe.
DEFAULT_ERROR_PAGES_DIR = Path("/opt/boron/templates/error_pages")

HTTPD_CONFIG_PATH = "/usr/local/lsws/conf/httpd_config.conf"
VHOST_CONF_TEMPLATE = "{base}/conf/vhosts/{name}/vhconf.conf"
OLS_SERVER_BASE = "/usr/local/lsws"

RESOURCE_DEFAULTS = {
    "mem_soft_limit": "512M",
    "mem_hard_limit": "1024M",
    "proc_soft_limit": 50,
    "proc_hard_limit": 75,
}

OLS_SETTINGS_DEFAULTS = {
    "max_connections": 10000,
    "max_ssl_connections": 10000,
    "connection_timeout": 300,
    "keep_alive_timeout": 5,
    "max_keep_alive_requests": 10000,
    "gzip_level": 6,
    "brotli_level": 6,
    "gzip_enabled": True,
    "brotli_enabled": True,
    "quic_enabled": True,
    "log_level": "WARN",
    "log_keep_days": 30,
}
OLS_CREDENTIAL_PATH = Path("/var/lib/boron/ols-admin-credential.json")
OLS_PASSWORD_SCRIPT = Path("/usr/local/lsws/admin/misc/admpass.sh")
_OLS_USERNAME_RE = re.compile(r"\A[A-Za-z0-9._-]{1,32}\Z")


def _ols_settings() -> dict:
    with write_session() as session:
        return _ols_settings_from_session(session)


def _ols_settings_from_session(session) -> dict:
    if session is None:
        return dict(OLS_SETTINGS_DEFAULTS)
    row = session.get(OlsServerSettings, 1)
    if row is None:
        return dict(OLS_SETTINGS_DEFAULTS)
    return {key: getattr(row, key) for key in OLS_SETTINGS_DEFAULTS}


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


DEFAULT_SSL_KEY = "/etc/boron/ssl/default.key"
DEFAULT_SSL_CERT = "/etc/boron/ssl/default.crt"


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
        "suspended": bool(d.suspended),
        "hotlink_protection_enabled": d.hotlink_protection_enabled,
        "hotlink_allowed_domains": d.hotlink_allowed_domains or [],
        "ip_block_list": d.ip_block_list or [],
        # Phase 7a feature 6: None means "inherit Account.php_version" --
        # render_vhost_conf resolves the effective version itself.
        "php_version": d.php_version,
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


def _app_proxy_map(session) -> dict[str, dict]:
    """Phase 7a features 1/2: domain -> the NodeJS/Python app bound to it,
    if any. NodeApp.domain/PythonApp.domain are each already unique
    (daemon/nodeapps.py's/pythonapps.py's own _assert_domain_free), so at
    most one of the two loops below ever contributes a given domain key.
    handler_name is derived from the domain's own vhost_name (already
    guaranteed collision-free, see _vhost_name's docstring) rather than
    the app's own customer-chosen `name`, since two different accounts'
    apps could otherwise pick the identical name."""
    result: dict[str, dict] = {}
    for row in session.scalars(select(NodeApp)).all():
        result[row.domain] = {"handler_name": f"proxy_{_vhost_name(row.domain)}", "port": row.port, "kind": "node"}
    for row in session.scalars(select(PythonApp)).all():
        result[row.domain] = {"handler_name": f"proxy_{_vhost_name(row.domain)}", "port": row.port, "kind": "python"}
    return result


CACHE_STORE_ROOT = "/usr/local/lsws/cachedata"


def _lscache_for_domain(session, domain_name: str) -> dict | None:
    """Phase 7a feature 4. None (no override block rendered at all) unless
    a row exists AND is enabled -- an untouched domain renders identically
    to before this feature existed, same "absence means default behavior"
    convention as _php_ini_for_account."""
    row = session.scalar(select(LscacheSettings).where(LscacheSettings.domain == domain_name))
    if row is None or not row.enabled:
        return None
    vhost_name = _vhost_name(domain_name)
    return {
        "ttl_seconds": row.ttl_seconds,
        "exclude_paths": row.exclude_paths or [],
        "storagepath": f"{CACHE_STORE_ROOT}/{vhost_name}",
        # Manual/secondary purge mechanism (daemon/lscache.py's own
        # purge() uses direct filesystem deletion of storagepath instead,
        # which doesn't depend on this URI's exact live HTTP semantics) --
        # rendered anyway since it's a real, documented OLS cache-module
        # parameter and costs nothing to also expose.
        "purge_uri": f"/.boron-lscache-purge-{vhost_name}",
    }


def _php_ini_for_account(session, account_id: int) -> dict | None:
    row = session.scalar(select(PhpIniOverride).where(PhpIniOverride.account_id == account_id))
    extra_rows = session.scalars(select(PhpIniDirective).where(PhpIniDirective.account_id == account_id)).all()
    if row is None and not extra_rows:
        return None
    # An account can have extras (PhpIniDirective rows) without the legacy
    # six-column row -- render the legacy fields at their php.ini defaults
    # then (semantically a no-op: they equal what PHP would use anyway),
    # so the template keeps one flat shape either way.
    legacy = phpdirectives.DEFAULTS if row is None else {
        "memory_limit": row.memory_limit,
        "upload_max_filesize": row.upload_max_filesize,
        "post_max_size": row.post_max_size,
        "max_execution_time": row.max_execution_time,
        "display_errors": row.display_errors,
        "error_reporting": row.error_reporting,
    }
    return {
        **legacy,
        # Names are EXTRA_DIRECTIVES registry keys and values passed
        # phpdirectives.validate() at write time -- that pair is what makes
        # interpolating them into php_admin_value lines safe.
        "extras": [{"name": r.name, "value": r.value} for r in extra_rows],
    }


def _with_disable_functions(php_ini: dict | None, disable_functions: str | None) -> dict | None:
    """QA round 2, item 9: layers an admin-set disable_functions override
    (daemon/phpfunctions.py, per-account or per-domain) onto whatever
    _php_ini_for_account already computed for this account -- None means
    no admin override at any scope for this domain, so the account's
    existing php_ini (possibly also None) is returned completely
    untouched, preserving the exact "absence means default" behavior
    every other override in this template already has. Deliberately
    creates a minimal php_ini dict (DEFAULTS + just this one extra) when
    php_ini was None but a disable_functions override IS set -- an
    account with zero other PHP customization can still get a
    disable_functions override rendered."""
    if disable_functions is None:
        return php_ini
    base = php_ini or {**phpdirectives.DEFAULTS, "extras": []}
    extras = [e for e in (base.get("extras") or []) if e["name"] != "disable_functions"]
    extras.append({"name": "disable_functions", "value": disable_functions})
    return {**base, "extras": extras}


def _forwarding_for_domain(session, domain_name: str) -> dict | None:
    """Phase 8 feature 4. None (no whole-domain redirect rendered) unless a
    DomainForwarding row exists. The target URL is a plain replacement string
    (not a regex), already validated http(s) with no newline/bracket/space by
    validate_redirect_target, so it's safe to interpolate into the rewrite."""
    row = session.scalar(select(DomainForwarding).where(DomainForwarding.domain == domain_name))
    if row is None:
        return None
    return {"target_url": row.target_url, "status_code": row.status_code, "keep_path": row.keep_path}


def _maintenance_for_domain(session, domain_name: str) -> dict | None:
    """Missing-features batch, goal feature 2. None (no rewrite-to-503
    branch rendered at all) unless a row exists AND is enabled -- same
    "absence means default behavior" convention _forwarding_for_domain/
    _lscache_for_domain already use."""
    row = session.scalar(select(MaintenanceMode).where(MaintenanceMode.domain == domain_name))
    if row is None or not row.enabled:
        return None
    retry_after_seconds = row.auto_disable_minutes * 60 if row.auto_disable_minutes else 3600
    from daemon import custom_pages

    return {
        "bypass_token": row.bypass_token,
        "bypass_query_param": "fh_bypass",
        "retry_after_seconds": retry_after_seconds,
        "page_name": custom_pages.MAINTENANCE_PAGE_NAME,
    }


def _error_pages_for_domain(username: str, domain_name: str, maintenance_active: bool) -> dict[int, str]:
    """Missing-features batch, goal feature 4 (+ feature 2's own forced-503
    page, see daemon/custom_pages.py's module docstring for why that
    override happens here). A pure filesystem-existence check, no DB row --
    daemon/custom_pages.resolve_error_pages is the single decision point.

    ensure_pages_dir is called here (not only at domain-add time) so a
    domain created before this feature existed still gets a real, existing
    directory backing the vhost's unconditional /.boron-error-pages/
    context the next time its vhost is regenerated for any reason --
    self-healing, rather than requiring a one-off migration script the way
    an earlier feature's tmp-dir backfill needed (refresh_all_vhosts)."""
    from daemon import custom_pages

    try:
        custom_pages.ensure_pages_dir(username, domain_name)
    except (OSError, KeyError):
        pass  # best-effort -- a failure here must not block the whole vhost regen
    return custom_pages.resolve_error_pages(username, domain_name, maintenance_active=maintenance_active)


def _wildcard_map(session) -> dict[str, bool]:
    """Missing-features batch, goal feature 3: domain -> whether *.domain
    should route to this same vhost, for every domain at once (mirrors
    _app_proxy_map's own "look everything up in one query, not one per
    domain" shape)."""
    rows = session.scalars(select(WildcardDomain).where(WildcardDomain.enabled == True)).all()  # noqa: E712
    return {r.domain: True for r in rows}


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
    app_proxy: dict | None = None,
    lscache: dict | None = None,
    forwarding: dict | None = None,
    maintenance: dict | None = None,
    error_pages: dict[int, str] | None = None,
) -> str:
    home_dir = f"{settings.home_base}/{account.username}"
    template = _env.get_template("vhost.conf.j2")
    return template.render(
        vhost_name=_vhost_name(domain["domain"]),
        domain_name=domain["domain"],
        docroot=domain["docroot"],
        home_dir=home_dir,
        forwarding=forwarding or domain.get("forwarding"),
        # Phase 7a feature 6: this domain's own PHP version override, if
        # any, else the account's own default -- must match whichever
        # extProcessor _all_active_vhosts actually declared for this
        # (account, effective-version) pair in httpd_config.conf, or this
        # scripthandler would reference an extProcessor that doesn't exist.
        php_app_name=_php_app_name(account.username, domain.get("php_version") or account.php_version),
        suspended=suspended,
        suspended_page_root=settings.suspended_page_root,
        ssl_key_file=ssl_key_file,
        ssl_cert_file=ssl_cert_file,
        php_ini=php_ini,
        redirects=redirects,
        hotlink=_hotlink_context(domain),
        ip_block_list=domain.get("ip_block_list") or [],
        protected_dirs=protected_dirs or [],
        app_proxy=app_proxy or domain.get("app_proxy"),
        lscache=lscache or domain.get("lscache"),
        maintenance=maintenance or domain.get("maintenance"),
        error_pages=error_pages or domain.get("error_pages"),
        default_error_pages_dir=str(DEFAULT_ERROR_PAGES_DIR),
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
    app_proxies = _app_proxy_map(session)
    wildcard_domains = _wildcard_map(session)
    # Accounts with a per-account extension selection get a PHP_INI_SCAN_DIR
    # env line on their extProcessor (daemon/phpext.py's mechanism); everyone
    # else keeps the compiled-in stock scan dir by rendering nothing.
    ext_override_ids = set(session.scalars(select(PhpExtensionSet.account_id)).all())
    reserved_hosts = set(session.scalars(select(Domain.domain)).all())
    reserved_hosts.update((settings.webmail_hostname, settings.pma_hostname, settings.panel_hostname))
    domain_vhosts = []
    account_procs = []
    for account in accounts:
        # Domain rows are the only source of truth for what gets a listener
        # map entry. Account.primary_domain is a denormalized display field
        # only -- falling back to it here previously caused a phantom vhost
        # to be regenerated after a failed domain.add's compensation logic
        # deleted the Domain row but raced with this read, found during
        # Phase b's real end-to-end testing.
        domains = session.scalars(select(Domain).where(Domain.account_id == account.id)).all()
        if not domains:
            continue  # an account with no domain yet has no vhost/listener entry
        account_home = f"{settings.home_base}/{account.username}"
        # Phase 7a feature 6: one extProcessor per distinct EFFECTIVE PHP
        # version actually in use across this account's own domains, not
        # unconditionally one per account -- most accounts still have
        # exactly one entry here (every domain inheriting Account.php_version,
        # the pre-existing behavior), but a domain with its own override
        # contributes a second, separate extProcessor block if no sibling
        # domain already uses that same effective version.
        versions_seen: set[str] = set()
        for d in domains:
            effective_version = d.php_version or account.php_version
            if effective_version not in versions_seen:
                versions_seen.add(effective_version)
                account_procs.append({
                    "username": account.username,
                    "php_app_name": _php_app_name(account.username, effective_version),
                    "lsphp_path": _lsphp_path(effective_version),
                    "home_dir": account_home,
                    "php_scan_dir": (
                        phpdirectives.php_scan_dir(account.username, effective_version)
                        if account.id in ext_override_ids else None
                    ),
                })
            domain_vhosts.append({
                "vhost_name": _vhost_name(d.domain),
                "domain": d.domain,
                "www_alias": not d.domain.startswith("www.") and "www." + d.domain not in reserved_hosts,
                "account_home": account_home,
                "app_proxy": app_proxies.get(d.domain),
                # Missing-features batch, goal feature 3: whether
                # httpd_config.conf.j2's listener map should also match
                # *.<domain> to this same vhost (confirmed against this
                # server's own installed OLS docs that the map's domain
                # field accepts wildcard syntax directly).
                "wildcard": wildcard_domains.get(d.domain, False),
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


# Phase 5 feature 7: ModSecurity/WAF. Kept here (not in daemon/waf.py) since
# it's read at every httpd_config.conf render, same as every other piece of
# this function's context -- daemon/waf.py imports these path constants
# rather than duplicating them.
WAF_RULES_FILE = "/etc/modsecurity/modsec_includes.conf"
WAF_RUNTIME_FILE = "/etc/modsecurity/boron-runtime.conf"
WAF_AUDIT_LOG = "/var/log/boron/modsecurity-audit.log"


def waf_template_context(session) -> dict:
    """Takes an already-open session (the same one the caller already used
    to fetch domain_vhosts/account_procs) rather than opening its own --
    render_httpd_config must stay a pure function of its arguments so every
    existing test that calls it directly with plain dicts (no DB involved
    at all) keeps working, and so this never touches the live DB as a
    surprise side effect of what looks like template rendering."""
    waf_settings = session.get(WafSettings, 1)
    enabled = bool(waf_settings and waf_settings.enabled)
    overrides = session.scalars(select(WafDomainOverride).where(WafDomainOverride.disabled == True)).all()  # noqa: E712
    rules = session.scalars(select(WafCustomRule)).all()
    return {
        "waf_enabled": enabled,
        "waf_audit_log": WAF_AUDIT_LOG,
        "waf_rules_file": WAF_RULES_FILE,
        "waf_domain_overrides": [{"domain": o.domain} for o in overrides],
        "waf_custom_rules": [{"id": r.id, "domain": r.domain, "target": r.target, "pattern": r.pattern} for r in rules],
    }


_WAF_DISABLED_CONTEXT = {
    "waf_enabled": False,
    "waf_audit_log": WAF_AUDIT_LOG,
    "waf_rules_file": WAF_RULES_FILE,
    "waf_domain_overrides": [],
    "waf_custom_rules": [],
}


def cloudflare_trusted_ips() -> list[str]:
    """Phase 2+3 feature 3: the Cloudflare edge CIDRs to mark trusted in the
    OLS real-IP config, read from the materialized ranges file
    (settings.cloudflare_ranges_file, refreshed by cf.refresh_ranges). Absent
    / unreadable -> [] so the render is byte-identical to pre-feature (the
    rollback path). Kept a pure read here so render_httpd_config stays a
    function of its inputs for every existing dict-only test."""
    import json

    try:
        data = json.loads(Path(settings.cloudflare_ranges_file).read_text())
    except (OSError, ValueError):
        return []
    return list(data.get("ipv4_cidrs") or []) + list(data.get("ipv6_cidrs") or [])


def render_waf_rules(waf: dict) -> str:
    return _env.get_template('modsecurity_rules.conf.j2').render(**waf)


def _attach_waf_config(targets: dict, contents: dict, waf: dict) -> None:
    if waf.get('waf_enabled'):
        targets['__boron_waf_rules'] = WAF_RUNTIME_FILE
        contents['__boron_waf_rules'] = render_waf_rules(waf)


def render_httpd_config(
    domain_vhosts: list[dict],
    account_procs: list[dict],
    waf: dict | None = None,
    cloudflare_ranges: list[str] | None = None,
    ols_settings: dict | None = None,
) -> str:
    template = _env.get_template("httpd_config.conf.j2")
    return template.render(
        server_name="boron",
        waf_runtime_file=WAF_RUNTIME_FILE,
        cloudflare_trusted_ips=cloudflare_trusted_ips() if cloudflare_ranges is None else cloudflare_ranges,
        admin_email="root@localhost",
        min_uid=11,
        min_gid=10,
        default_php_version_nodot=settings.default_php_version.replace(".", ""),
        default_ssl_key="/etc/boron/ssl/default.key",
        default_ssl_cert="/etc/boron/ssl/default.crt",
        domain_vhosts=domain_vhosts,
        account_procs=account_procs,
        panel_hostname=settings.panel_hostname,
        panel_acme_webroot=settings.panel_acme_webroot,
        webmail_hostname=settings.webmail_hostname,
        webmail_vhroot=str(Path(settings.webmail_docroot).parent),
        webmail_docroot=settings.webmail_docroot,
        webmail_lsphp_path=_lsphp_path(settings.default_php_version),
        pma_hostname=settings.pma_hostname,
        pma_docroot=settings.pma_docroot,
        pma_lsphp_path=_lsphp_path(settings.default_php_version),
        ols_settings=ols_settings or dict(OLS_SETTINGS_DEFAULTS),
        **(waf or _WAF_DISABLED_CONTEXT),
        **RESOURCE_DEFAULTS,
    )


def _webmail_vhost_conf_path() -> str:
    return VHOST_CONF_TEMPLATE.format(base=OLS_SERVER_BASE, name=WEBMAIL_VHOST_NAME)


def render_webmail_vhost_conf(ssl_key_file: str, ssl_cert_file: str) -> str:
    template = _env.get_template("roundcube_vhost.conf.j2")
    return template.render(
        docroot=settings.webmail_docroot,
        # Keep logs inside Roundcube's namespace-mounted tree: the www-data
        # LSAPI worker can write here without widening Boron's own log access.
        log_dir=str(Path(settings.webmail_docroot).parent / "logs"),
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
        raise RuntimeError("webmail_hostname is not set in boron.toml")

    with write_session() as session:
        domain_vhosts, account_procs = _all_active_vhosts(session)
        ssl_key_file, ssl_cert_file = _webmail_ssl_paths(session)
        waf = waf_template_context(session)
        ols_settings = _ols_settings_from_session(session)

    httpd_content = render_httpd_config(domain_vhosts, account_procs, waf=waf, ols_settings=ols_settings)
    webmail_content = render_webmail_vhost_conf(ssl_key_file, ssl_cert_file)

    targets = {"main": HTTPD_CONFIG_PATH, "webmail": _webmail_vhost_conf_path()}
    contents = {"main": httpd_content, "webmail": webmail_content}
    _attach_waf_config(targets, contents, waf)
    writer = ConfigWriterMulti(
        targets=targets,
        validate=_validate_multi,
        reload=_reload,
        verify=_verify,
        backup_dir=settings.backup_dir,
        subsystem="ols",
    )
    result = writer.apply(contents)
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
        raise RuntimeError("pma_hostname is not set in boron.toml")

    with write_session() as session:
        domain_vhosts, account_procs = _all_active_vhosts(session)
        ssl_key_file, ssl_cert_file = _pma_ssl_paths(session)
        waf = waf_template_context(session)
        ols_settings = _ols_settings_from_session(session)

    httpd_content = render_httpd_config(domain_vhosts, account_procs, waf=waf, ols_settings=ols_settings)
    pma_content = render_pma_vhost_conf(ssl_key_file, ssl_cert_file)

    targets = {"main": HTTPD_CONFIG_PATH, "pma": _pma_vhost_conf_path()}
    contents = {"main": httpd_content, "pma": pma_content}
    # PHP namespaces hide /var/lib by default. Bind only the token directory;
    # its root:www-data 0770 permissions still deny hosting account identities.
    namespace_path = Path(OLS_SERVER_BASE) / "conf/nsconf.conf"
    if namespace_path.exists():
        token_path = str(settings.pma_token_dir)
        if not token_path.startswith("/") or any(c.isspace() or c in ',\"$' for c in token_path):
            raise RuntimeError("unsafe phpMyAdmin token namespace path")
        namespace_content = namespace_path.read_text()
        binding = f"{token_path},bind-try"
        if binding not in namespace_content.splitlines():
            namespace_content = namespace_content.rstrip() + "\n" + binding + "\n"
        targets["namespace"] = str(namespace_path)
        contents["namespace"] = namespace_content

    _attach_waf_config(targets, contents, waf)
    writer = ConfigWriterMulti(
        targets=targets,
        validate=_validate_multi,
        reload=_reload,
        verify=_verify,
        backup_dir=settings.backup_dir,
        subsystem="ols",
    )
    result = writer.apply(contents)
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
        # ModSecurity regex braces are not OLS configuration block delimiters.
        if name == "__boron_waf_rules":
            if not path.read_text().strip():
                return StepResult(False, "empty WAF rules")
            continue
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
        app_proxies = _app_proxy_map(session)
        plain = []
        for d in rows:
            entry = _domain_row_to_plain(d)
            entry["app_proxy"] = app_proxies.get(d.domain)
            entry["lscache"] = _lscache_for_domain(session, d.domain)
            plain.append(entry)
        return plain


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
        disable_functions_by_domain = {
            domain["domain"]: phpfunctions.effective_disable_functions(session, account.id, domain["domain"])
            for domain in domains
        }
        redirects_by_domain = {domain["domain"]: _redirects_for_domain(session, domain["domain"]) for domain in domains}
        forwarding_by_domain = {domain["domain"]: _forwarding_for_domain(session, domain["domain"]) for domain in domains}
        protected_dirs_by_domain = {
            domain["domain"]: _protected_dirs_for_domain(session, account.username, account.id, domain["docroot"])
            for domain in domains
        }
        maintenance_by_domain = {domain["domain"]: _maintenance_for_domain(session, domain["domain"]) for domain in domains}
        waf = waf_template_context(session)
        ols_settings = _ols_settings_from_session(session)

    error_pages_by_domain = {
        domain["domain"]: _error_pages_for_domain(
            account.username, domain["domain"], maintenance_active=bool(maintenance_by_domain[domain["domain"]])
        )
        for domain in domains
    }

    targets = {"main": HTTPD_CONFIG_PATH}
    content = {"main": render_httpd_config(domain_vhosts, account_procs, waf=waf, ols_settings=ols_settings)}
    for domain in domains:
        vhost_name = _vhost_name(domain["domain"])
        ssl_key_file, ssl_cert_file = _ssl_paths_for_domain(domain)
        targets[vhost_name] = _vhost_conf_path(vhost_name)
        content[vhost_name] = render_vhost_conf(
            account, domain, suspended or bool(domain.get("suspended")),
            ssl_key_file=ssl_key_file, ssl_cert_file=ssl_cert_file,
            php_ini=_with_disable_functions(php_ini, disable_functions_by_domain[domain["domain"]]),
            redirects=redirects_by_domain[domain["domain"]],
            protected_dirs=protected_dirs_by_domain[domain["domain"]],
            forwarding=forwarding_by_domain[domain["domain"]],
            maintenance=maintenance_by_domain[domain["domain"]],
            error_pages=error_pages_by_domain[domain["domain"]],
        )

    _attach_waf_config(targets, content, waf)
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
    with write_session() as session:
        waf = waf_template_context(session)
        ols_settings = _ols_settings_from_session(session)
    httpd_content = render_httpd_config(domain_vhosts, account_procs, waf=waf, ols_settings=ols_settings)
    targets = {"main": HTTPD_CONFIG_PATH}
    contents = {"main": httpd_content}
    _attach_waf_config(targets, contents, waf)
    writer = ConfigWriterMulti(
        targets=targets,
        validate=_validate_multi,
        reload=_reload,
        verify=_verify,
        backup_dir=settings.backup_dir,
        subsystem="ols",
    )
    result = writer.apply(contents)
    if not result.ok:
        raise RuntimeError(f"OLS config transaction failed during {context}: {result.summary()}")


def refresh_main_config() -> None:
    """Re-render + re-apply just httpd_config.conf from current DB state,
    with no domain/account change involved -- used by daemon/waf.py
    whenever WAF settings/overrides/custom rules change, the same
    validate/backup/reload/verify/rollback path every other OLS config
    change goes through (ARCHITECTURE.md SS7), not a special case."""
    with write_session() as session:
        domain_vhosts, account_procs = _all_active_vhosts(session)
    _apply_main_only(domain_vhosts, account_procs, context="waf_settings_change")


def refresh_all_vhosts() -> None:
    """One-time (idempotent, safe to re-run) migration for a shared-
    template security fix (per-account /tmp + allowSymbolLink hardening,
    Phase 6a research findings): re-renders every active/suspended
    account's own vhost.conf(s) + httpd_config.conf from the current
    templates, and backfills private tmp and OLS log ACLs for every one --
    so an account created before this fix picks up both the new template
    content and the private tmp dir it now depends on, not just accounts
    created after. Same category as bootstrap_webmail/bootstrap_pma: run
    explicitly once, not wired into any hook or RPC op, since a config-
    mutating action with no trigger event would be surprising there.

    Uses refresh_vhost's own account_snapshot pattern (handlers_account.
    set_php_version): accounts are queried and detached before use here,
    matching the established convention for reading scalar attributes off
    an Account row after its session has closed.
    """
    with write_session() as session:
        accounts = list(session.scalars(
            select(Account).where(Account.status.in_(["active", "suspended"]))
        ).all())
    for account in accounts:
        sysops.ensure_tmp_dir(account.username)
        sysops.ensure_web_logs(account.username)
        refresh_vhost(account)


def migrate_error_page_paths() -> int:
    """Repair generated paths before old release directories are pruned.

    Apply all affected vhosts together: a single stale vhost can otherwise
    make validation of every unrelated configuration transaction fail.
    """
    targets, contents = {}, {}
    pattern = re.compile(r"(?m)^([ \t]*location[ \t]+)/opt/boron-\d+\.\d+\.\d+/templates/error_pages/[ \t]*$")
    for path in sorted((Path(OLS_SERVER_BASE) / "conf/vhosts").glob("*/vhconf.conf")):
        if path.is_symlink() or path.parent.is_symlink():
            raise RuntimeError("Refusing symlink in managed vhost configuration")
        old = path.read_text()
        new = pattern.sub(lambda match: match[1] + str(DEFAULT_ERROR_PAGES_DIR) + "/", old)
        if new != old:
            targets[path.parent.name] = str(path)
            contents[path.parent.name] = new
    if not targets:
        return 0
    writer = ConfigWriterMulti(
        targets=targets, validate=_validate_multi, reload=_reload, verify=_verify,
        backup_dir=settings.backup_dir, subsystem="ols",
    )
    result = writer.apply(contents)
    if not result.ok:
        raise RuntimeError(f"OLS error-page migration failed: {result.summary()}")
    return len(targets)


def bootstrap_baseline() -> None:
    """Replace the stock OLS install's bundled 'Example' vhost with a clean,
    Boron-managed httpd_config.conf containing whatever vhosts already
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
        waf = waf_template_context(session)
        ols_settings = _ols_settings_from_session(session)

    # A configured infrastructure hostname makes the main template reference
    # its vhost immediately.  A fresh install must therefore create the main
    # config and those vhost configs in one transaction; applying main first
    # makes `openlitespeed -t` fail on missing paths before the installer can
    # reach the later Roundcube/panel certificate bootstrap steps.
    targets = {"main": HTTPD_CONFIG_PATH}
    contents = {
        "main": render_httpd_config(
            domain_vhosts, account_procs, waf=waf, ols_settings=ols_settings
        )
    }

    if settings.panel_hostname:
        from daemon import panel_tls

        validate_domain(settings.panel_hostname)
        webroot = Path(settings.panel_acme_webroot)
        if not webroot.is_absolute() or webroot.resolve() != webroot:
            raise ValidationError(
                "Panel certificate webroot must be an absolute path without symlinks"
            )
        challenge = webroot / ".well-known/acme-challenge"
        challenge.mkdir(parents=True, exist_ok=True, mode=0o755)
        owner = panel_tls._challenge_owner()
        os.chown(webroot, owner.pw_uid, owner.pw_gid)
        webroot.chmod(0o555)
        for directory in (webroot / ".well-known", challenge):
            os.chown(directory, 0, 0)
            directory.chmod(0o755)
        targets["panel"] = str(
            Path(settings.vhost_conf_dir) / "boron-panel-acme/vhconf.conf"
        )
        contents["panel"] = _env.get_template(
            "panel_acme_vhost.conf.j2"
        ).render(webroot=str(webroot))

    if settings.webmail_hostname:
        validate_domain(settings.webmail_hostname)
        docroot = Path(settings.webmail_docroot)
        if not docroot.is_absolute() or docroot.resolve() != docroot:
            raise ValidationError(
                "Webmail document root must be an absolute path without symlinks"
            )
        challenge = docroot / ".well-known/acme-challenge"
        challenge.mkdir(parents=True, exist_ok=True, mode=0o755)
        web_user = pwd.getpwnam("www-data")
        os.chown(docroot, web_user.pw_uid, web_user.pw_gid)
        docroot.chmod(0o755)
        log_dir = docroot.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
        os.chown(log_dir, web_user.pw_uid, web_user.pw_gid)
        log_dir.chmod(0o750)
        targets["webmail"] = _webmail_vhost_conf_path()
        contents["webmail"] = render_webmail_vhost_conf(
            *_webmail_ssl_paths(None)
        )

    _attach_waf_config(targets, contents, waf)
    writer = ConfigWriterMulti(
        targets=targets,
        validate=_validate_multi,
        reload=_reload,
        verify=_verify,
        backup_dir=settings.backup_dir,
        subsystem="ols",
    )
    result = writer.apply(contents)
    if not result.ok:
        raise RuntimeError(
            "OLS config transaction failed during bootstrap_baseline: "
            f"{result.summary()}"
        )


# --- Expansion: administrator OpenLiteSpeed controls -----------------------

def _bounded_setting(name: str, value, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValidationError(f"{name} must be a number")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must be a number") from None
    if not minimum <= number <= maximum:
        raise ValidationError(f"{name} must be between {minimum} and {maximum}")
    return number


def _validate_admin_settings(params: dict) -> dict:
    current = _ols_settings()
    values = {
        "max_connections": _bounded_setting("max_connections", params.get("max_connections", current["max_connections"]), 100, 1000000),
        "max_ssl_connections": _bounded_setting("max_ssl_connections", params.get("max_ssl_connections", current["max_ssl_connections"]), 100, 1000000),
        "connection_timeout": _bounded_setting("connection_timeout", params.get("connection_timeout", current["connection_timeout"]), 10, 3600),
        "keep_alive_timeout": _bounded_setting("keep_alive_timeout", params.get("keep_alive_timeout", current["keep_alive_timeout"]), 1, 120),
        "max_keep_alive_requests": _bounded_setting("max_keep_alive_requests", params.get("max_keep_alive_requests", current["max_keep_alive_requests"]), 100, 100000),
        "gzip_level": _bounded_setting("gzip_level", params.get("gzip_level", current["gzip_level"]), 1, 9),
        "brotli_level": _bounded_setting("brotli_level", params.get("brotli_level", current["brotli_level"]), 1, 11),
        "log_keep_days": _bounded_setting("log_keep_days", params.get("log_keep_days", current["log_keep_days"]), 1, 365),
    }
    log_level = str(params.get("log_level", current["log_level"])).upper()
    if log_level not in {"DEBUG", "INFO", "NOTICE", "WARN", "ERROR"}:
        raise ValidationError("log_level must be DEBUG, INFO, NOTICE, WARN, or ERROR")
    values["log_level"] = log_level
    for name in ("gzip_enabled", "brotli_enabled", "quic_enabled"):
        value = params.get(name, current[name])
        if not isinstance(value, bool):
            raise ValidationError(f"{name} must be true or false")
        values[name] = value
    return values


def credential_status(params: dict) -> dict:
    available = False
    username = "admin"
    reset_at = None
    try:
        info = OLS_CREDENTIAL_PATH.stat()
        if info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600:
            data = json.loads(OLS_CREDENTIAL_PATH.read_text())
            available = bool(data.get("password"))
            username = str(data.get("username") or "admin")
            reset_at = data.get("reset_at")
    except (OSError, ValueError, TypeError):
        pass
    return {"username": username, "password_available": available, "reset_at": reset_at}


def admin_status(params: dict) -> dict:
    service = run(["systemctl", "is-active", "lsws"], timeout=10)
    version = run([f"{OLS_SERVER_BASE}/bin/lshttpd", "-v"], timeout=10)
    config_check = run([f"{OLS_SERVER_BASE}/bin/openlitespeed", "-t"], timeout=30)
    admin_cert = Path(f"{OLS_SERVER_BASE}/admin/conf/webadmin.crt")
    tls_check = run(["openssl", "x509", "-in", str(admin_cert), "-noout", "-checkhost", settings.panel_hostname], timeout=10) if admin_cert.exists() else None
    with write_session() as session:
        accounts = session.scalar(select(func.count()).select_from(Account)) or 0
        domains = session.scalar(select(func.count()).select_from(Domain)) or 0
    return {
        "active": service.stdout.strip() == "active",
        "version": (version.stdout or version.stderr).strip()[:200],
        "config_valid": config_check.ok,
        "config_message": (config_check.stderr or config_check.stdout).strip()[-500:],
        "settings": _ols_settings(),
        "credential": credential_status({}),
        "webadmin_port": 7080,
        "webadmin_tls_valid": bool(tls_check and tls_check.ok),
        "webadmin_tls_hostname": settings.panel_hostname,
        "accounts": accounts,
        "domains": domains,
    }


def update_admin_settings(params: dict) -> dict:
    previous = _ols_settings()
    values = _validate_admin_settings(params)
    with write_session() as session:
        row = session.get(OlsServerSettings, 1)
        if row is None:
            row = OlsServerSettings(id=1)
            session.add(row)
        for key, value in values.items():
            setattr(row, key, value)
    try:
        refresh_main_config()
    except Exception:
        with write_session() as session:
            row = session.get(OlsServerSettings, 1)
            if row is None:
                row = OlsServerSettings(id=1)
                session.add(row)
            for key, value in previous.items():
                setattr(row, key, value)
        raise
    return admin_status({})


def graceful_reload(params: dict) -> dict:
    if not bool(params.get("confirm")):
        raise ValidationError("OpenLiteSpeed reload requires confirm=true")
    check = run([f"{OLS_SERVER_BASE}/bin/openlitespeed", "-t"], timeout=30)
    if not check.ok:
        raise RuntimeError(f"OpenLiteSpeed configuration is invalid: {(check.stderr or check.stdout).strip()}")
    result = run(["systemctl", "reload", "lsws"], timeout=30)
    if not result.ok:
        raise RuntimeError(f"OpenLiteSpeed reload failed: {(result.stderr or result.stdout).strip()}")
    return admin_status({})


def _write_credential(username: str, password: str) -> str:
    OLS_CREDENTIAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = OLS_CREDENTIAL_PATH.with_name(f".{OLS_CREDENTIAL_PATH.name}.{os.getpid()}.{secrets.token_hex(6)}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    reset_at = utcnow().isoformat()
    try:
        payload = json.dumps({"username": username, "password": password, "reset_at": reset_at}).encode()
        view = memoryview(payload)
        while view:
            view = view[os.write(fd, view):]
        os.fchmod(fd, 0o600)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, OLS_CREDENTIAL_PATH)
    return reset_at


def reset_admin_password(params: dict) -> dict:
    username = str(params.get("username") or "admin").strip()
    if not _OLS_USERNAME_RE.fullmatch(username):
        raise ValidationError("WebAdmin username must be 1-32 letters, digits, dots, underscores, or hyphens")
    if not OLS_PASSWORD_SCRIPT.is_file():
        raise RuntimeError("OpenLiteSpeed password reset script is unavailable")
    alphabet = string.ascii_letters + string.digits + "!@#%+_-"
    password = "".join(secrets.choice(alphabet) for _ in range(28))
    result = run([str(OLS_PASSWORD_SCRIPT)], input_text=f"{username}\n{password}\n{password}\n", timeout=30)
    if not result.ok:
        raise RuntimeError(f"OpenLiteSpeed password reset failed: {(result.stderr or result.stdout).strip()}")
    reset_at = _write_credential(username, password)
    return {"username": username, "password": password, "reset_at": reset_at}


def reveal_admin_password(params: dict) -> dict:
    if not bool(params.get("confirm")):
        raise ValidationError("password reveal requires confirm=true")
    status = credential_status({})
    if not status["password_available"]:
        raise ValidationError("the existing WebAdmin password is not recoverable; reset it first")
    data = json.loads(OLS_CREDENTIAL_PATH.read_text())
    return {"username": data["username"], "password": data["password"], "reset_at": data["reset_at"]}
