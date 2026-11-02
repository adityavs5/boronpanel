"""phpMyAdmin is installed without blocking a configuration save, and a
failed install is reported instead of breaking an upgrade or install.

Before: saving a phpMyAdmin hostname in Server Setup on a server without the
package rolled the whole save back (bootstrap_pma found no document root),
an apt failure during upgrade_runtime aborted the update, and the installer
wrote pma_hostname before the package existed, so the OLS config test in
bootstrap_baseline failed on the phpMyAdmin vhost's missing paths.
"""
import functools
import json
import threading
import time
from types import SimpleNamespace

import pytest

from daemon import diagnostics, panel_config, panel_tls, pma
from daemon.procutil import CommandFailure
from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain
from shared.validation import ValidationError

HOST = "pma.example.com"


@pytest.fixture()
def server(isolated_db, tmp_path, monkeypatch):
    config = tmp_path / "boron.toml"
    config.write_text('panel_hostname = "panel.example.com"\npma_hostname = ""\n')
    docroot = tmp_path / "phpmyadmin"
    for key, value in (("backup_dir", str(tmp_path / "backups")), ("panel_hostname", "panel.example.com"),
                       ("webmail_hostname", ""), ("pma_hostname", ""), ("pma_docroot", str(docroot))):
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(pma, "_pending_hostname", None)
    monkeypatch.setattr(pma, "_worker_active", False)
    monkeypatch.setattr(pma, "database_component_enabled", lambda: True)
    monkeypatch.setattr(panel_tls, "bootstrap_challenge", lambda: None)
    # The background publication saves through the same function; keep both
    # on this test's configuration file.
    monkeypatch.setattr(panel_config, "apply_setup_values",
                        functools.partial(panel_config.apply_setup_values, config_path=config))
    published = []

    def bootstrap():
        (docroot / "boron_signon.php").write_text("<?php\n")
        published.append(settings.pma_hostname)

    monkeypatch.setattr(pma, "bootstrap_pma", bootstrap)
    return SimpleNamespace(config=config, docroot=docroot, published=published)


def _install(server, release=None, error=None):
    def install(**_kwargs):
        if release is not None:
            assert release.wait(5)
        if error is not None:
            raise error
        server.docroot.mkdir(exist_ok=True)
        (server.docroot / "index.php").write_text("<?php\n")
        return True
    return install


def _wait_for_worker():
    deadline = time.monotonic() + 10
    while pma._worker_active:
        assert time.monotonic() < deadline, "the phpMyAdmin worker did not finish"
        time.sleep(0.01)


def test_a_save_without_the_package_returns_and_publishes_when_installed(server, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(pma, "install_package", _install(server, release))

    outcome = panel_config.apply_setup_values({"pma_hostname": HOST}, reload_services=True)

    # The save did not wait for apt, and the hostname is held back: its vhost
    # would point at a document root that does not exist yet.
    assert outcome["phpmyadmin"] == {"status": "installing", "hostname": HOST}
    assert 'pma_hostname = ""' in server.config.read_text() and settings.pma_hostname == ""
    status = pma.provisioning_status()
    assert status["provisioning"] == "installing" and status["target_hostname"] == HOST
    check = pma.readiness_check()
    assert check["status"] == "warn" and f"being installed for {HOST}" in check["explanation"]

    release.set()
    _wait_for_worker()

    assert f'pma_hostname = "{HOST}"' in server.config.read_text() and settings.pma_hostname == HOST
    assert server.published == [HOST]
    assert pma.provisioning_status()["provisioning"] == "published"
    assert pma.readiness_check()["status"] == "pass"


def test_a_failed_background_install_is_reported_and_saves_nothing(server, monkeypatch):
    failure = CommandFailure("apt-get failed (rc=100): Reading package lists...\n"
                             "E: Unable to locate package phpmyadmin", 100)
    monkeypatch.setattr(pma, "install_package", _install(server, error=failure))

    panel_config.apply_setup_values({"pma_hostname": HOST}, reload_services=True)
    _wait_for_worker()

    assert 'pma_hostname = ""' in server.config.read_text() and server.published == []
    status = pma.provisioning_status()
    assert status["provisioning"] == "failed" and status["last_stage"] == "install"
    assert status["last_error"] == "E: Unable to locate package phpmyadmin"
    assert status["target_hostname"] == HOST
    check = pma.readiness_check()
    assert check["status"] == "warn" and not check["critical"]
    assert check["explanation"] == "phpMyAdmin could not be installed from Server Setup: E: Unable to locate package phpmyadmin"
    assert "save the phpMyAdmin hostname in Panel settings" in check["suggested_fix"]


def test_the_latest_choice_wins_while_the_package_installs(server, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(pma, "install_package", _install(server, release))
    panel_config.apply_setup_values({"pma_hostname": HOST}, reload_services=True)
    panel_config.apply_setup_values({"pma_hostname": "db.example.com"}, reload_services=True)
    release.set()
    _wait_for_worker()
    assert server.published == ["db.example.com"]
    assert 'pma_hostname = "db.example.com"' in server.config.read_text()


def test_clearing_the_hostname_while_installing_cancels_its_publication(server, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(pma, "install_package", _install(server, release))
    panel_config.apply_setup_values({"pma_hostname": HOST}, reload_services=True)
    panel_config.apply_setup_values({"pma_hostname": ""}, reload_services=True)
    release.set()
    _wait_for_worker()
    assert server.published == [] and 'pma_hostname = ""' in server.config.read_text()
    assert pma.provisioning_status()["provisioning"] == "installed"
    check = pma.readiness_check()
    assert check["explanation"] == (
        "phpMyAdmin is installed but not published: no phpMyAdmin hostname is configured.")
    # Not publishing it is the administrator's choice; a warning would become
    # an advisory on every update of a server that does not use phpMyAdmin.
    assert check["status"] == "pass"


def test_an_installed_package_is_published_with_the_save(server, monkeypatch):
    monkeypatch.setattr(pma, "install_package", lambda **_kwargs: pytest.fail("must not install"))
    _install(server)()
    outcome = panel_config.apply_setup_values({"pma_hostname": HOST}, reload_services=True)
    assert "phpmyadmin" not in outcome and server.published == [HOST] and not pma._worker_active
    assert f'pma_hostname = "{HOST}"' in server.config.read_text()


def test_a_first_publication_writes_its_vhost_before_other_renders(server, tmp_path, monkeypatch):
    from daemon import ols
    order = []
    vhost = tmp_path / "phpmyadmin-vhconf.conf"
    monkeypatch.setattr(ols, "_pma_vhost_conf_path", lambda: str(vhost))
    monkeypatch.setattr(settings, "webmail_hostname", "webmail.example.com")
    monkeypatch.setattr(panel_tls, "bootstrap_challenge", lambda: order.append("panel"))
    monkeypatch.setattr(ols, "bootstrap_webmail", lambda: order.append("webmail"))
    monkeypatch.setattr(pma, "bootstrap_pma", lambda: order.append("phpmyadmin"))
    _install(server)()
    # The panel and webmail bootstraps render the main config, which names
    # the phpMyAdmin vhost; its config must already exist then.
    panel_config.apply_setup_values({"pma_hostname": HOST}, reload_services=True)
    assert order == ["phpmyadmin", "panel", "webmail"]
    order.clear()
    vhost.write_text("")
    panel_config.apply_setup_values({"pma_hostname": "db.example.com"}, reload_services=True)
    assert order == ["panel", "webmail", "phpmyadmin"]


def test_a_held_back_hostname_is_still_checked_for_conflicts(server, monkeypatch):
    monkeypatch.setattr(pma, "install_package", lambda **_kwargs: pytest.fail("must not install"))
    with write_session() as session:
        account = Account(username="alpha", status="active")
        session.add(account)
        session.flush()
        session.add(Domain(account_id=account.id, domain=HOST, kind="addon", docroot="/fixture/alpha"))
    with pytest.raises(ValidationError, match="belongs to a customer site"):
        panel_config.apply_setup_values({"pma_hostname": HOST}, reload_services=True)
    assert not pma._worker_active and 'pma_hostname = ""' in server.config.read_text()


def test_a_restart_during_the_install_is_reported(server):
    pma._record_provision("installing", source="setup", hostname=HOST)
    state = pma.provision_state()
    state["pid"] = -1  # written by a daemon process that no longer runs
    pma._provision_state_path().write_text(json.dumps(state))
    assert pma.provisioning_status()["provisioning"] == "interrupted"
    check = pma.readiness_check()
    assert check["status"] == "warn" and "interrupted by a panel restart" in check["explanation"]
    assert "dpkg --configure -a" in check["suggested_fix"]


def test_readiness_passes_where_phpmyadmin_is_not_used(server, monkeypatch):
    monkeypatch.setattr(pma, "database_component_enabled", lambda: False)
    assert pma.readiness_check()["status"] == "pass"
    assert any(check_id == "phpmyadmin" for check_id, _check in diagnostics.check_registry())


def test_upgrade_records_a_failed_install_and_carries_on(isolated_db, monkeypatch):
    from scripts import upgrade_runtime
    monkeypatch.setattr(pma, "database_component_enabled", lambda: True)
    monkeypatch.setattr(settings, "pma_hostname", "")
    budgets = []

    def install(*, budget_seconds):
        budgets.append(budget_seconds)
        raise CommandFailure("apt-get failed (rc=100): E: Failed to fetch http://archive.example/p.deb 404 Not Found", 100)

    monkeypatch.setattr(pma, "install_package", install)
    upgrade_runtime.configure_phpmyadmin()  # must not raise: the update goes on

    assert budgets == [pma.UPGRADE_INSTALL_BUDGET_SECONDS]
    state = pma.provision_state()
    assert (state["status"], state["stage"], state["source"]) == ("failed", "install", "upgrade")
    check = pma.readiness_check()
    assert check["status"] == "warn"
    assert check["explanation"] == ("phpMyAdmin could not be installed during a panel update: "
                                    "E: Failed to fetch http://archive.example/p.deb 404 Not Found")


def test_install_steps_share_one_time_budget(tmp_path, monkeypatch):
    from daemon import procutil
    monkeypatch.setattr(settings, "pma_docroot", str(tmp_path / "phpmyadmin"))
    clock = [1000.0]
    monkeypatch.setattr(pma, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs.get("timeout")))
        if args[:2] == ["apt-get", "install"]:
            clock[0] += 300  # a hung mirror uses up the whole budget
            return SimpleNamespace(ok=False)
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(procutil, "run", fake_run)
    with pytest.raises(pma.PmaError, match="ran out of time"):
        pma.install_package(budget_seconds=300)
    assert [args[:2] for args, _timeout in calls] == [["debconf-set-selections"], ["apt-get", "install"]]
    install_args, install_timeout = calls[1]
    assert install_timeout == 300 and "DPkg::Lock::Timeout=100" in install_args


def test_status_route_is_for_administrators(monkeypatch):
    from fastapi import HTTPException
    from api.routers import pma as pma_router
    from api.security import Identity
    from daemon import server as daemon_server
    from daemon.rpc_policy import POLICY_BY_OPERATION
    from shared.token_policy import READ_ONLY_OPERATIONS
    monkeypatch.setattr(pma_router, "call_daemon", lambda *args, **kwargs: pytest.fail("reached the daemon"))
    for role in ("customer", "reseller"):
        with pytest.raises(HTTPException) as error:
            pma_router.phpmyadmin_status(Identity(1, "alpha-login", role, 1, "session"))
        assert error.value.status_code == 403
    monkeypatch.setattr(pma_router, "call_daemon", lambda op, identity, **params: {"op": op})
    assert pma_router.phpmyadmin_status(Identity(1, "root", "admin", None, "session")) == {"op": "pma.status"}
    assert POLICY_BY_OPERATION["pma.status"] == "global_admin" and "pma.status" in READ_ONLY_OPERATIONS
    assert daemon_server.OP_TABLE["pma.status"] is pma.provisioning_status


def test_panel_settings_saves_the_hostname_without_server_setup(server, monkeypatch):
    # Server Setup reaches service hostnames only after its DNS steps, which a
    # server with DNS hosted elsewhere cannot complete; Panel settings can.
    server.docroot.mkdir()
    (server.docroot / "index.php").write_text("<?php\n")
    monkeypatch.setattr(pma, "vhost_config_exists", lambda: True)

    assert panel_config.apply_pma_hostname(" PMA.Example.com ")["status"] == "completed"
    assert f'pma_hostname = "{HOST}"' in server.config.read_text() and settings.pma_hostname == HOST
    assert server.published == [HOST]
    assert panel_config.apply_pma_hostname(HOST) == {"status": "unchanged", "hostname": HOST}
    with pytest.raises(ValidationError):
        panel_config.apply_pma_hostname("panel.example.com")
