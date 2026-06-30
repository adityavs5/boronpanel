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
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain

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


def _vhost_conf_path(username: str) -> str:
    return VHOST_CONF_TEMPLATE.format(base=OLS_SERVER_BASE, name=username)


def render_vhost_conf(account: Account, domains: list[str], suspended: bool) -> str:
    home_dir = f"{settings.home_base}/{account.username}"
    docroot = f"{home_dir}/public_html"
    template = _env.get_template("vhost.conf.j2")
    return template.render(
        username=account.username,
        docroot=docroot,
        home_dir=home_dir,
        php_app_name=_php_app_name(account.username, account.php_version),
        lsphp_path=_lsphp_path(account.php_version),
        suspended=suspended,
        suspended_page_root=settings.suspended_page_root,
        ssl_key_file="/etc/forgehost/ssl/default.key",
        ssl_cert_file="/etc/forgehost/ssl/default.crt",
        **RESOURCE_DEFAULTS,
    )


def _all_active_vhosts(session) -> list[dict]:
    """Every account that should have a live vhost: active or suspended (a
    suspended account still needs its vhost present, just serving the
    suspended page -- ARCHITECTURE.md SS10). terminated/terminating/error
    accounts are excluded."""
    accounts = session.scalars(
        select(Account).where(Account.status.in_(["active", "suspended"]))
    ).all()
    vhosts = []
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
        vhosts.append({"name": account.username, "home_dir": f"{settings.home_base}/{account.username}", "domains": domains})
    return vhosts


def render_httpd_config(vhosts: list[dict]) -> str:
    template = _env.get_template("httpd_config.conf.j2")
    return template.render(
        server_name="forgehost",
        admin_email="root@localhost",
        min_uid=11,
        min_gid=10,
        default_php_version_nodot=settings.default_php_version.replace(".", ""),
        default_ssl_key="/etc/forgehost/ssl/default.key",
        default_ssl_cert="/etc/forgehost/ssl/default.crt",
        vhosts=vhosts,
    )


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


def _apply_vhost_set(account: Account, domains: list[str], suspended: bool) -> None:
    with write_session() as session:
        vhosts = _all_active_vhosts(session)

    vhost_content = render_vhost_conf(account, domains, suspended)
    httpd_content = render_httpd_config(vhosts)

    writer = ConfigWriterMulti(
        targets={
            "vhost": _vhost_conf_path(account.username),
            "main": HTTPD_CONFIG_PATH,
        },
        validate=_validate_multi,
        reload=_reload,
        verify=_verify,
        backup_dir=settings.backup_dir,
        subsystem="ols",
    )
    result = writer.apply({"vhost": vhost_content, "main": httpd_content})
    if not result.ok:
        raise RuntimeError(f"OLS config transaction failed: {result.summary()}")


def provision_vhost(account: Account, domains: list[str]) -> None:
    _apply_vhost_set(account, domains, suspended=False)


def suspend_vhost(account: Account) -> None:
    with write_session() as session:
        domains = session.scalars(select(Domain.domain).where(Domain.account_id == account.id)).all()
        domains = list(domains) or ([account.primary_domain] if account.primary_domain else [])
    if not domains:
        return
    _apply_vhost_set(account, domains, suspended=True)


def unsuspend_vhost(account: Account) -> None:
    with write_session() as session:
        domains = session.scalars(select(Domain.domain).where(Domain.account_id == account.id)).all()
        domains = list(domains) or ([account.primary_domain] if account.primary_domain else [])
    if not domains:
        return
    _apply_vhost_set(account, domains, suspended=False)


def terminate_vhost(account: Account) -> None:
    """Remove this account's vhost dir + regenerate httpd_config.conf
    without it. Idempotent: safe to call even if the account never had a
    domain/vhost provisioned."""
    vhost_dir = Path(OLS_SERVER_BASE) / "conf" / "vhosts" / account.username
    if not vhost_dir.exists():
        return

    with write_session() as session:
        vhosts = [v for v in _all_active_vhosts(session) if v["name"] != account.username]

    _apply_main_only(vhosts, context="terminate")

    import shutil

    shutil.rmtree(vhost_dir, ignore_errors=True)


def _apply_main_only(vhosts: list[dict], context: str) -> None:
    httpd_content = render_httpd_config(vhosts)
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
        vhosts = _all_active_vhosts(session)
    _apply_main_only(vhosts, context="bootstrap_baseline")
