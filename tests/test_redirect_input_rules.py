"""Redirect and forwarding inputs are refused when saved, not dropped at
render time (round 1 web review; UW L6: '/../../x' was accepted)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from daemon import forwarding
from daemon import handlers_redirect as hr
from shared.db import write_session
from shared.models import Account, Domain, DomainForwarding, Redirect
from shared.validation import ValidationError


@pytest.fixture
def site(isolated_db, monkeypatch):
    refreshed = []
    # handlers_redirect and forwarding share daemon.ols.
    monkeypatch.setattr(hr.ols, "refresh_vhost", lambda account: refreshed.append(account.username))
    with write_session() as db:
        account = Account(username="demo1", status="active", primary_domain="demo1.example")
        db.add(account)
        db.flush()
        db.add(Domain(account_id=account.id, domain="demo1.example", kind="primary",
                      docroot="/home/demo1/public_html"))
    return refreshed


def test_dot_segment_redirect_path_is_refused_when_saved(site):
    with pytest.raises(ValidationError, match=r"'\.' and '\.\.' segments are not allowed"):
        hr.create_redirect({"domain": "demo1.example", "path": "/../../x",
                            "target_url": "https://example.com/new"})
    assert site == []


def test_a_stored_dot_segment_redirect_can_still_be_deleted(site):
    with write_session() as db:
        db.add(Redirect(domain="demo1.example", path="/../../x", target_url="https://example.com/new",
                        status_code=301))
    assert hr.delete_redirect({"domain": "demo1.example", "path": "/../../x"})["status"] == "deleted"
    with write_session() as db:
        assert db.scalar(select(Redirect)) is None


@pytest.mark.parametrize("target", ["https://a.example/%{HTTP_COOKIE}", "https://a.example/$1",
                                    "https://a.example/a%20b"])
def test_domain_forwarding_with_rewrite_variables_is_refused_when_saved(site, target):
    """Forwarding targets used to be stored, then silently skipped when the
    vhost was rendered (ols._forwarding_for_domain)."""
    with pytest.raises(ValidationError, match="rewrite variables"):
        forwarding.set_forwarding({"username": "demo1", "domain": "demo1.example", "target_url": target})
    with write_session() as db:
        assert db.scalar(select(DomainForwarding)) is None
    assert site == []
