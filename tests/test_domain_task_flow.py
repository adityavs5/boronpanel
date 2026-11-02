"""Customers, and resellers for the accounts they own, add subdomains and
addon domains through the task flow the SPA uses (taskops.domain.add) and
poll the task to completion (live finding UW C1: every customer add failed
with 403 "domain target unavailable"). A name another account already holds
is a 409, not "account and domain targets disagree".

Runs the real credential resolution, authorization, control-task worker and
handlers_domain.add_domain; only the web server, filesystem and DNS
provider calls are stubbed.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, func, select

from api.rpc import http_status_for_rpc_error
from daemon import control_tasks, handlers_auth, server
from daemon import handlers_domain as hd
from daemon.rpc_authority import AuthorizationError
from shared.config import settings
from shared.db import write_session
from shared.models import (Account, ControlTask, DnsZone, Domain, MailDomain, ResellerAccount,
                           ResellerPlan, ResellerProfile)
from shared.validation import ConflictError

TAKEN = "domain already belongs to another account"


@pytest.fixture(autouse=True)
def immediate_dispatch(monkeypatch):
    def run_immediately(loop, _executor, handler, params):
        future = loop.create_future()
        try:
            future.set_result(handler(params))
        except Exception as exc:  # noqa: BLE001 - mirror executor Future behavior
            future.set_exception(exc)
        return future

    monkeypatch.setattr(asyncio.BaseEventLoop, "run_in_executor", run_immediately)
    monkeypatch.setattr(server.audit, "record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server.audit, "record_account_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server.audit, "begin_action_trace", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(server.audit, "end_action_trace", lambda *_args, **_kwargs: None)


@pytest.fixture(autouse=True)
def hosting_stubs(monkeypatch):
    calls = {"vhost": [], "zones": [], "records": []}
    monkeypatch.setattr(settings, "server_public_ip", "192.0.2.10")
    monkeypatch.setattr(hd.sysops, "ensure_web_logs", lambda username: None)
    monkeypatch.setattr(hd, "ensure_docroot", lambda username, docroot, domain_name=None, **_kwargs: None)
    monkeypatch.setattr(hd.ols, "provision_vhost", lambda account: calls["vhost"].append(account.username))
    monkeypatch.setattr(hd, "_create_managed_zone",
                        lambda username, domain: calls["zones"].append(domain) or {"zone": domain})
    monkeypatch.setattr(hd, "_delete_managed_zone", lambda domain: {"zone": domain, "status": "deleted"})
    monkeypatch.setattr(hd.dnsprovider, "upsert_record",
                        lambda zone, label, rtype, values, ttl=None, proxied=False:
                        calls["records"].append((zone, label, rtype, tuple(values))))
    return calls


@pytest.fixture
def worker(monkeypatch):
    """The control-task executor, run on demand in this thread."""
    queued: list[int] = []
    monkeypatch.setattr(control_tasks._executor, "submit", lambda _fn, task_id: queued.append(task_id))

    def drain() -> None:
        while queued:
            control_tasks._run(queued.pop(0))

    return drain


def _hosting_account(username: str) -> int:
    """An active account with its primary domain and that domain's zone."""
    with write_session() as db:
        account = Account(username=username, status="active", primary_domain=f"{username}.example")
        db.add(account)
        db.flush()
        db.add(Domain(account_id=account.id, domain=f"{username}.example", kind="primary",
                      docroot=f"/home/{username}/public_html"))
        db.add(DnsZone(account_id=account.id, zone=f"{username}.example"))
        return account.id


@pytest.fixture
def tenants(isolated_db):
    """cust1 and cust2 are customers; resold1 belongs to the reseller."""
    ids = {name: _hosting_account(name) for name in ("cust1", "cust2", "resold1")}
    with write_session() as db:
        db.add(Domain(account_id=ids["cust2"], domain="taken.example", kind="addon",
                      docroot="/home/cust2/taken.example/public_html"))
        db.add(MailDomain(account_id=ids["cust2"], domain="mailonly.example"))
    customer = handlers_auth.create_panel_user({
        "username": "cust1", "password": "CustomerPass123!", "role": "customer", "account_id": ids["cust1"],
    })
    reseller = handlers_auth.create_panel_user({
        "username": "seller", "password": "ResellerPass123!", "role": "reseller",
    })
    with write_session() as db:
        plan = ResellerPlan(name="reseller-plan")
        db.add(plan)
        db.flush()
        profile = ResellerProfile(panel_user_id=reseller["id"], plan_id=plan.id)
        db.add(profile)
        db.flush()
        db.add(ResellerAccount(reseller_id=profile.id, account_id=ids["resold1"]))
    return {
        "ids": ids,
        "customer": {"type": "session",
                     "value": handlers_auth.create_session({"panel_user_id": customer["id"]})["session_id"]},
        "reseller": {"type": "session",
                     "value": handlers_auth.create_session({"panel_user_id": reseller["id"]})["session_id"]},
    }


def _call(op: str, params: dict, credential: dict) -> dict:
    return asyncio.run(server.dispatch(op, dict(params), credential))


def _add_and_poll(credential: dict, worker, username: str, **body) -> dict:
    started = _call("taskops.domain.add", {"username": username, **body}, credential)
    assert started["status"] == "queued" and started["operation"] == "domain.add"
    worker()
    task = _call("taskops.get", {"task_id": started["id"]}, credential)
    assert task["status"] == "completed", task["progress_message"]
    return task


@pytest.mark.parametrize("role,username", [("customer", "cust1"), ("reseller", "resold1")])
def test_subdomain_and_addon_domain_through_the_task_flow(tenants, worker, hosting_stubs, role, username):
    credential = tenants[role]
    subdomain = _add_and_poll(credential, worker, username, domain=f"blog.{username}.example",
                              kind="subdomain", parent_domain=f"{username}.example",
                              document_root_mode="default")
    addon = _add_and_poll(credential, worker, username, domain=f"{username}-shop.example", kind="addon")

    assert subdomain["result"]["domain"] == f"blog.{username}.example"
    assert subdomain["result"]["kind"] == "subdomain"
    assert subdomain["result"]["dns_record_created"] is True
    assert addon["result"]["kind"] == "addon"
    assert addon["result"]["dns_zone_created"] is True
    assert hosting_stubs["records"] == [(f"{username}.example", "blog", "A", ("192.0.2.10",))]
    assert hosting_stubs["zones"] == [f"{username}-shop.example"]
    with write_session() as db:
        owned = dict(db.execute(select(Domain.domain, Domain.kind).where(
            Domain.account_id == tenants["ids"][username])).all())
    assert owned == {f"{username}.example": "primary", f"blog.{username}.example": "subdomain",
                     f"{username}-shop.example": "addon"}


@pytest.mark.parametrize("role,username", [("customer", "cust1"), ("reseller", "resold1")])
@pytest.mark.parametrize("domain", ["taken.example", "cust2.example", "mailonly.example"])
def test_a_name_another_account_holds_is_a_conflict(tenants, worker, role, username, domain):
    with pytest.raises(ConflictError, match=TAKEN) as raised:
        _call("taskops.domain.add", {"username": username, "domain": domain, "kind": "addon"}, tenants[role])
    assert server.rpc_error_code(raised.value) == "conflict"
    assert http_status_for_rpc_error(server.rpc_error_code(raised.value)) == 409
    with write_session() as db:
        assert db.scalar(select(func.count()).select_from(ControlTask)) == 0


def test_the_synchronous_add_route_reports_the_same_conflict(tenants, monkeypatch):
    monkeypatch.setitem(server.OP_TABLE, "domain.add", lambda _params: pytest.fail("handler reached"))
    with pytest.raises(ConflictError, match=TAKEN):
        _call("domain.add", {"username": "cust1", "domain": "taken.example", "kind": "addon"}, tenants["customer"])


@pytest.mark.parametrize("role,foreign", [("customer", "resold1"), ("reseller", "cust1")])
def test_tasks_stay_scoped_to_the_accounts_a_caller_owns(tenants, worker, role, foreign):
    """Naming an account the caller does not own stays a 403 (never the
    409, which only the owner of the named account gets), and another
    owner's task cannot be polled."""
    credential = tenants[role]
    with pytest.raises(AuthorizationError):
        _call("taskops.domain.add", {"username": foreign, "domain": "taken.example", "kind": "addon"}, credential)
    with pytest.raises(AuthorizationError, match="account ownership required"):
        _call("taskops.domain.add", {"username": foreign, "domain": "fresh.example", "kind": "addon"}, credential)

    other = "reseller" if role == "customer" else "customer"
    started = _call("taskops.domain.add", {"username": foreign, "domain": f"{foreign}-new.example",
                                           "kind": "addon"}, tenants[other])
    with pytest.raises(AuthorizationError, match="control task ownership required"):
        _call("taskops.get", {"task_id": started["id"]}, credential)


def test_reseller_loses_its_tasks_with_the_account(tenants, worker):
    started = _call("taskops.domain.add", {"username": "resold1", "domain": "resold1-new.example",
                                           "kind": "addon"}, tenants["reseller"])
    worker()
    assert _call("taskops.get", {"task_id": started["id"]}, tenants["reseller"])["status"] == "completed"
    with write_session() as db:
        db.execute(delete(ResellerAccount))  # the account returned to the administrator
    with pytest.raises(AuthorizationError):
        _call("taskops.get", {"task_id": started["id"]}, tenants["reseller"])


def test_reseller_cannot_read_server_wide_tasks(tenants):
    with write_session() as db:
        task = ControlTask(operation="account.create", username="newacct", status="queued",
                           initiated_by="administrator", initiated_role="admin")
        db.add(task)
        db.flush()
        task_id = task.id
    with pytest.raises(AuthorizationError, match="control task ownership required"):
        _call("taskops.get", {"task_id": task_id}, tenants["reseller"])
