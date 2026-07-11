"""QA round 2, item 8 (critical): a suspended/unsuspended account's proxied
Cloudflare zones must have their edge cache purged too, not just the
origin's own LSCache -- otherwise a suspended site can keep serving stale
content straight from Cloudflare's edge even after the local fix
(daemon/lscache.py's purge_account_domains) lands. SUSPEND_HOOKS/
UNSUSPEND_HOOKS entry: daemon/cloudflare_ops.py's purge_account_zones.

Unit-tests purge_account_zones' own selection/best-effort logic directly
(which zones it picks, that one failure doesn't stop the rest) by
monkeypatching purge_cache itself -- purge_cache's own real HTTP behavior
against the Cloudflare API is already covered by test_cloudflare_zones.py.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from daemon import cloudflare_ops
from shared.db import write_session
from shared.models import Account, CloudflareZone, Domain


@pytest.fixture()
def account_with_zones(isolated_db):
    with write_session() as session:
        account = Account(username="cfuser", status="active", primary_domain="active1.example")
        session.add(account)
        session.flush()
        session.add(Domain(account_id=account.id, domain="active1.example", kind="primary", docroot="/home/cfuser/public_html"))
        session.add(Domain(account_id=account.id, domain="active2.example", kind="addon", docroot="/home/cfuser/active2.example"))
        session.add(Domain(account_id=account.id, domain="pending.example", kind="addon", docroot="/home/cfuser/pending.example"))
        session.add(CloudflareZone(account_id=account.id, zone="active1.example", cf_zone_id="cfz-1", status="active"))
        session.add(CloudflareZone(account_id=account.id, zone="active2.example", cf_zone_id="cfz-2", status="active"))
        session.add(CloudflareZone(account_id=account.id, zone="pending.example", cf_zone_id="cfz-3", status="pending"))
        account_id = account.id

        other = Account(username="otheruser", status="active", primary_domain="other.example")
        session.add(other)
        session.flush()
        session.add(Domain(account_id=other.id, domain="other.example", kind="primary", docroot="/home/otheruser/public_html"))
        session.add(CloudflareZone(account_id=other.id, zone="other.example", cf_zone_id="cfz-4", status="active"))

    with write_session() as session:
        return session.get(Account, account_id)


def test_purges_only_active_zones_owned_by_this_account(account_with_zones, monkeypatch):
    purged = []
    monkeypatch.setattr(cloudflare_ops, "purge_cache", lambda params: purged.append(params["domain"]) or {"status": "purged"})

    results = cloudflare_ops.purge_account_zones(account_with_zones)

    assert sorted(purged) == ["active1.example", "active2.example"]  # not pending.example, not other.example
    assert {r["zone"] for r in results} == {"active1.example", "active2.example"}
    assert all(r["status"] == "purged" for r in results)


def test_one_zone_failure_does_not_stop_the_rest(account_with_zones, monkeypatch):
    def flaky_purge(params):
        if params["domain"] == "active1.example":
            raise RuntimeError("Cloudflare API hiccup")
        return {"status": "purged"}

    monkeypatch.setattr(cloudflare_ops, "purge_cache", flaky_purge)

    results = cloudflare_ops.purge_account_zones(account_with_zones)  # must not raise

    by_zone = {r["zone"]: r["status"] for r in results}
    assert by_zone["active1.example"] == "error"
    assert by_zone["active2.example"] == "purged"


def test_idempotent_for_account_with_no_cloudflare_zones(isolated_db):
    with write_session() as session:
        account = Account(username="nocfuser", status="active", primary_domain="local-only.example")
        session.add(account)
        session.flush()
        session.add(Domain(account_id=account.id, domain="local-only.example", kind="primary", docroot="/home/nocfuser/public_html"))
        account_id = account.id
    with write_session() as session:
        account = session.get(Account, account_id)
        assert cloudflare_ops.purge_account_zones(account) == []


def test_idempotent_for_account_with_no_domains(isolated_db):
    with write_session() as session:
        account = Account(username="nodomainsuser", status="active", primary_domain="none.example")
        session.add(account)
        session.flush()
        account_id = account.id
    with write_session() as session:
        account = session.get(Account, account_id)
        assert cloudflare_ops.purge_account_zones(account) == []
