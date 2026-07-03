"""Regression tests for a critical, broad cross-account authorization bug
found while building Phase 4 feature 1 (docs/CHECKPOINT-phase4-0b-cross-account-idor.md):
40 route handlers across 8 routers took a `domain` (or bare `job_id`) path/
body parameter and checked only `require_account_access(identity, username)`
-- that the caller owns the account named in the URL -- never that the
`domain`/`job_id` itself actually belongs to that account. Since forgehostd
does not re-derive authorization by design (ARCHITECTURE.md SS2: "the daemon
does not re-derive authorization, that already happened in the API layer"),
several of the underlying daemon handlers had *no* ownership check at any
layer either (mail forwarders/catchall/autoresponders, DNS records/zones,
redirects, WordPress/backup job polling) -- a customer could target another
tenant's domain or job_id by supplying their own valid username alongside
someone else's domain/job_id.

These tests call the router functions directly (FastAPI's route decorators
return the plain function unchanged) with a crafted cross-account Identity,
and monkeypatch call_daemon to fail the test if it's ever reached -- proving
the rejection happens at the authorization check, before any daemon call.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi import HTTPException  # noqa: E402

from api.routers import domains, email, mail as mail_router, redirects, ssl_router, wordpress as wp_router  # noqa: E402
from api.security import Identity  # noqa: E402
from daemon import handlers_account as ha  # noqa: E402
from daemon import handlers_auth as hauth  # noqa: E402
from daemon import backup, wordpress  # noqa: E402
from daemon.backup import BackupError  # noqa: E402
from daemon.wordpress import WordPressError  # noqa: E402
from shared.db import write_session  # noqa: E402
from shared.models import Account, BackupDestination, BackupJob, Domain, WordPressJob  # noqa: E402


def _make_local_destination(session) -> int:
    dest = BackupDestination(name="test-local", kind="local", local_path="/tmp/backups-test")
    session.add(dest)
    session.flush()
    return dest.id


def _stub_sysops(monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)


@pytest.fixture()
def two_tenants(isolated_db, monkeypatch):
    """Account 'acct1' (customer identity scoped to it) and account 'acct2'
    owning domain 'acct2.example' -- acct1's identity is the attacker
    perspective throughout these tests."""
    _stub_sysops(monkeypatch)
    acct1 = ha.create_account({"username": "acct1"})
    acct2 = ha.create_account({"username": "acct2"})
    user1 = hauth.create_panel_user({"username": "acct1login", "password": "CustPass123!", "role": "customer", "account_id": acct1["id"]})

    with write_session() as session:
        session.add(Domain(account_id=acct2["id"], domain="acct2.example", kind="primary", docroot="/home/acct2/public_html"))
        session.add(Domain(account_id=acct1["id"], domain="acct1.example", kind="primary", docroot="/home/acct1/public_html"))

    attacker_identity = Identity(user1["id"], "acct1login", "customer", acct1["id"], "session")
    return acct1, acct2, attacker_identity


def _forbid_call_daemon(monkeypatch, module):
    def _boom(*args, **kwargs):
        raise AssertionError("call_daemon must not be reached when authorization should deny the request")
    monkeypatch.setattr(module, "call_daemon", _boom)


class _FakeBody:
    """Minimal stand-in for a Pydantic body model -- exposes both attribute
    access (body.domain) and .model_dump(), whichever the handler uses."""

    def __init__(self, **kwargs):
        self._data = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)

    def model_dump(self, **_kwargs):
        return dict(self._data)


def test_email_set_catchall_rejects_cross_account_domain(two_tenants, monkeypatch):
    _acct1, _acct2, attacker = two_tenants
    _forbid_call_daemon(monkeypatch, email)
    with pytest.raises(HTTPException) as exc_info:
        email.set_catchall(username="acct1", domain="acct2.example", body=_FakeBody(destination="attacker@evil.example"), identity=attacker)
    assert exc_info.value.status_code == 403


def test_email_set_autoresponder_rejects_cross_account_domain(two_tenants, monkeypatch):
    _acct1, _acct2, attacker = two_tenants
    _forbid_call_daemon(monkeypatch, email)
    with pytest.raises(HTTPException) as exc_info:
        email.ui_set_catchall(username="acct1", domain="acct2.example", destination="attacker@evil.example", identity=attacker)
    assert exc_info.value.status_code == 403


def test_redirects_create_rejects_cross_account_domain(two_tenants, monkeypatch):
    _acct1, _acct2, attacker = two_tenants
    _forbid_call_daemon(monkeypatch, redirects)
    with pytest.raises(HTTPException) as exc_info:
        redirects.create_redirect(
            username="acct1", domain="acct2.example",
            body=_FakeBody(path="/", target_url="https://evil.example/", status_code=301),
            identity=attacker,
        )
    assert exc_info.value.status_code == 403


def test_mail_change_mailbox_password_rejects_cross_account_domain(two_tenants, monkeypatch):
    _acct1, _acct2, attacker = two_tenants
    _forbid_call_daemon(monkeypatch, mail_router)
    with pytest.raises(HTTPException) as exc_info:
        mail_router.change_mailbox_password(
            username="acct1", local_part="postmaster",
            body=_FakeBody(domain="acct2.example", password="Whatever123!Pass"),
            identity=attacker,
        )
    assert exc_info.value.status_code == 403


def test_ssl_issue_or_renew_rejects_cross_account_domain(two_tenants, monkeypatch):
    _acct1, _acct2, attacker = two_tenants
    _forbid_call_daemon(monkeypatch, ssl_router)
    with pytest.raises(HTTPException) as exc_info:
        ssl_router.issue_or_renew_certificate(username="acct1", domain="acct2.example", body=_FakeBody(force=False), identity=attacker)
    assert exc_info.value.status_code == 403


def test_wordpress_trigger_install_rejects_cross_account_domain(two_tenants, monkeypatch):
    _acct1, _acct2, attacker = two_tenants
    _forbid_call_daemon(monkeypatch, wp_router)
    with pytest.raises(HTTPException) as exc_info:
        wp_router.trigger_install(username="acct1", domain="acct2.example", body=_FakeBody(title=None, admin_user=None, admin_email=None, admin_password=None), identity=attacker)
    assert exc_info.value.status_code == 403


def test_domains_remove_rejects_cross_account_domain(two_tenants, monkeypatch):
    _acct1, _acct2, attacker = two_tenants
    _forbid_call_daemon(monkeypatch, domains)
    with pytest.raises(HTTPException) as exc_info:
        domains.remove_domain(username="acct1", domain="acct2.example", identity=attacker)
    assert exc_info.value.status_code == 403


def test_dns_ui_set_record_rejects_cross_account_domain(two_tenants, monkeypatch):
    from api.routers import dns
    _acct1, _acct2, attacker = two_tenants
    _forbid_call_daemon(monkeypatch, dns)
    with pytest.raises(HTTPException) as exc_info:
        dns.ui_set_record(username="acct1", domain="acct2.example", subdomain="@", type="A", values="1.2.3.4", ttl=3600, identity=attacker)
    assert exc_info.value.status_code == 403


# --- Daemon-layer fixes: job_id is not a capability ------------------------
# (docs/CHECKPOINT-phase4-0b-cross-account-idor.md) -- these ops don't take a
# `domain` at all, only a bare integer job_id, so the fix lives in the
# daemon handler itself (require username, cross-check the job's own
# account_id), not in an API-layer require_domain_access call.


def test_wordpress_get_job_rejects_wrong_account(isolated_db):
    with write_session() as session:
        acct1 = Account(username="wpown1", uid=6001, gid=6001, status="active")
        acct2 = Account(username="wpown2", uid=6002, gid=6002, status="active")
        session.add_all([acct1, acct2])
        session.flush()
        job = WordPressJob(account_id=acct2.id, domain="wpown2.example", status="completed", admin_url="https://x/", admin_user="admin", admin_password="TopSecret123!")
        session.add(job)
        session.flush()
        job_id = job.id

    with pytest.raises(WordPressError):
        wordpress.get_job({"job_id": job_id, "username": "wpown1"})

    # the rightful owner can still read it (and the one-time reveal still works)
    result = wordpress.get_job({"job_id": job_id, "username": "wpown2"})
    assert result["admin_password"] == "TopSecret123!"


def test_backup_get_job_rejects_wrong_account(isolated_db):
    with write_session() as session:
        dest_id = _make_local_destination(session)
        acct1 = Account(username="bkown1", uid=6011, gid=6011, status="active")
        acct2 = Account(username="bkown2", uid=6012, gid=6012, status="active")
        session.add_all([acct1, acct2])
        session.flush()
        job = BackupJob(account_id=acct2.id, kind="full", status="completed", destination_id=dest_id)
        session.add(job)
        session.flush()
        job_id = job.id

    with pytest.raises(BackupError):
        backup.get_job({"job_id": job_id, "username": "bkown1"})

    result = backup.get_job({"job_id": job_id, "username": "bkown2"})
    assert result["id"] == job_id


def test_backup_trigger_restore_rejects_wrong_account(isolated_db):
    with write_session() as session:
        dest_id = _make_local_destination(session)
        acct1 = Account(username="bkown3", uid=6021, gid=6021, status="active")
        acct2 = Account(username="bkown4", uid=6022, gid=6022, status="active")
        session.add_all([acct1, acct2])
        session.flush()
        job = BackupJob(account_id=acct2.id, kind="full", status="completed", destination_id=dest_id)
        session.add(job)
        session.flush()
        job_id = job.id

    with pytest.raises(BackupError):
        backup.trigger_restore({"backup_job_id": job_id, "username": "bkown3"})
