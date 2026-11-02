import pytest
from sqlalchemy import select

from daemon import handlers_account as ha
from daemon import handlers_domain as hd
from daemon import handlers_redirect as hr
from shared.db import write_session
from shared.models import Redirect
from shared.validation import ValidationError


@pytest.fixture()
def stub_ols(monkeypatch):
    calls = []
    monkeypatch.setattr(hr.ols, "refresh_vhost", lambda account: calls.append(account.username))
    return calls


@pytest.fixture()
def account_with_domain(isolated_db, monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    ha.create_account({"username": "demo1"})

    from sqlalchemy import select as _select

    from shared.models import Account, Domain

    with write_session() as session:
        account = session.scalar(_select(Account).where(Account.username == "demo1"))
        session.add(Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot="/home/demo1/public_html"))


def test_create_redirect_happy_path(account_with_domain, stub_ols):
    result = hr.create_redirect({"domain": "demo1.example", "path": "/old", "target_url": "https://example.com/new", "status_code": 301})
    assert result["path"] == "/old"
    assert result["status_code"] == 301
    assert "demo1" in stub_ols


def test_create_redirect_defaults_to_301(account_with_domain, stub_ols):
    result = hr.create_redirect({"domain": "demo1.example", "path": "/old", "target_url": "https://example.com/new"})
    assert result["status_code"] == 301


def test_create_redirect_rejects_invalid_path(account_with_domain, stub_ols):
    with pytest.raises(ValidationError):
        hr.create_redirect({"domain": "demo1.example", "path": "no-leading-slash", "target_url": "https://example.com/new"})


def test_create_redirect_rejects_invalid_target(account_with_domain, stub_ols):
    with pytest.raises(ValidationError):
        hr.create_redirect({"domain": "demo1.example", "path": "/old", "target_url": "not-a-url"})


def test_create_redirect_rejects_bad_status_code(account_with_domain, stub_ols):
    with pytest.raises(ValidationError):
        hr.create_redirect({"domain": "demo1.example", "path": "/old", "target_url": "https://example.com/new", "status_code": 307})


def test_create_redirect_rejects_existing_path_without_overwriting(account_with_domain, stub_ols):
    """Regression: a second POST for the same path silently replaced it."""
    from shared.validation import ConflictError

    hr.create_redirect({"domain": "demo1.example", "path": "/old", "target_url": "https://example.com/first", "status_code": 301})
    with pytest.raises(ConflictError):
        hr.create_redirect({"domain": "demo1.example", "path": "/old", "target_url": "https://example.com/second", "status_code": 302})

    with write_session() as session:
        rows = session.scalars(select(Redirect).where(Redirect.domain == "demo1.example")).all()
        assert [(r.target_url, r.status_code) for r in rows] == [("https://example.com/first", 301)]


def test_update_redirect_of_missing_path_is_not_found(account_with_domain, stub_ols):
    """Regression: PUT on a path that did not exist created it."""
    from shared.validation import NotFoundError

    with pytest.raises(NotFoundError):
        hr.update_redirect({"domain": "demo1.example", "path": "/nosuch", "target_url": "https://example.com/x"})
    with write_session() as session:
        assert session.scalars(select(Redirect)).all() == []


def test_delete_redirect_of_missing_path_is_not_found(account_with_domain, stub_ols):
    from shared.validation import NotFoundError

    with pytest.raises(NotFoundError):
        hr.delete_redirect({"domain": "demo1.example", "path": "/nosuch"})


@pytest.mark.parametrize("target", ["https://example.com/%{HTTP_COOKIE}", "https://example.com/$0", "https://example.com/%{ENV:SECRET}"])
def test_redirect_target_cannot_carry_rewrite_variables(account_with_domain, stub_ols, target):
    """Regression: %{HTTP_COOKIE} copied visitors' cookies into the Location
    of a redirect to an external site."""
    with pytest.raises(ValidationError, match="rewrite variables"):
        hr.create_redirect({"domain": "demo1.example", "path": "/var", "target_url": target, "status_code": 302})
    assert stub_ols == []


def test_update_redirect_via_put_semantics(account_with_domain, stub_ols):
    hr.create_redirect({"domain": "demo1.example", "path": "/old", "target_url": "https://example.com/first", "status_code": 301})
    result = hr.update_redirect({"domain": "demo1.example", "path": "/old", "target_url": "https://example.com/updated", "status_code": 301})
    assert result["target_url"] == "https://example.com/updated"


def test_list_redirects(account_with_domain, stub_ols):
    hr.create_redirect({"domain": "demo1.example", "path": "/a", "target_url": "https://example.com/a2"})
    hr.create_redirect({"domain": "demo1.example", "path": "/b", "target_url": "https://example.com/b2"})
    result = hr.list_redirects({"domain": "demo1.example"})["redirects"]
    assert {r["path"] for r in result} == {"/a", "/b"}


def test_delete_redirect(account_with_domain, stub_ols):
    hr.create_redirect({"domain": "demo1.example", "path": "/old", "target_url": "https://example.com/new"})
    result = hr.delete_redirect({"domain": "demo1.example", "path": "/old"})
    assert result["status"] == "deleted"
    assert hr.list_redirects({"domain": "demo1.example"})["redirects"] == []


def test_delete_redirect_not_found(account_with_domain, stub_ols):
    with pytest.raises(RuntimeError):
        hr.delete_redirect({"domain": "demo1.example", "path": "/nope"})


def test_create_redirect_requires_existing_domain(isolated_db, stub_ols):
    with pytest.raises(RuntimeError):
        hr.create_redirect({"domain": "nodomain.example", "path": "/old", "target_url": "https://example.com/new"})


def test_terminate_account_cleans_up_redirects_for_primary_domain(account_with_domain, stub_ols):
    """Regression test for a real bug found by live testing: the primary
    domain's Domain row survives account.terminate (same pattern as
    Account itself), so handlers_domain.remove_domain's own cleanup
    never runs for it -- confirmed live that redirect rows for a
    terminated account's primary domain lingered until this hook was
    added."""
    hr.create_redirect({"domain": "demo1.example", "path": "/old", "target_url": "https://example.com/new"})

    from shared.models import Account

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        hr.terminate_account_redirects(account)

    with write_session() as session:
        rows = session.scalars(select(Redirect).where(Redirect.domain == "demo1.example")).all()
        assert rows == []


def test_remove_domain_cleans_up_redirects(account_with_domain, stub_ols, monkeypatch):
    monkeypatch.setattr(hd.ols, "provision_vhost", lambda account: None)
    monkeypatch.setattr(hd.ols, "remove_domain_vhost", lambda account, domain: None)
    monkeypatch.setattr(hd, "ensure_docroot", lambda username, docroot, domain_name=None, **kwargs: None)
    monkeypatch.setattr(hd, "_create_managed_zone", lambda username, domain: {"zone": domain})

    hd.add_domain({"username": "demo1", "domain": "addon.demo1.example", "kind": "addon"})
    hr.create_redirect({"domain": "addon.demo1.example", "path": "/old", "target_url": "https://example.com/new"})

    hd.remove_domain({"username": "demo1", "domain": "addon.demo1.example"})

    with write_session() as session:
        rows = session.scalars(select(Redirect).where(Redirect.domain == "addon.demo1.example")).all()
        assert rows == []
