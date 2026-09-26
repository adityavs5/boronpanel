import os

import pytest

from daemon import htaccess
from shared.db import write_session
from shared.models import Account, Domain
from shared.validation import ValidationError


def _domain(tmp_path, monkeypatch, *, symlink=False):
    home_base = tmp_path / "home"
    docroot = home_base / "owner1" / "domains" / "example.test" / "public_html"
    docroot.mkdir(parents=True)
    value = docroot
    if symlink:
        outside = tmp_path / "outside"
        outside.mkdir()
        link = home_base / "owner1" / "linked"
        link.symlink_to(outside, target_is_directory=True)
        value = link
    monkeypatch.setattr(htaccess.settings, "home_base", str(home_base))
    with write_session() as db:
        account = Account(username="owner1", status="active")
        db.add(account)
        db.flush()
        db.add(Domain(account_id=account.id, domain="example.test", docroot=str(value)))
    return docroot


def test_manual_reload_uses_owned_docroot_and_validated_ols_reload(isolated_db, tmp_path, monkeypatch):
    _domain(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(htaccess.ols, "graceful_reload", lambda params: calls.append(params) or {"config_valid": True})
    result = htaccess.reload_domain({"username": "owner1", "domain": "example.test"})
    assert result["config_valid"] is True
    assert calls == [{"confirm": True}]


def test_manual_reload_rejects_symlink_docroot(isolated_db, tmp_path, monkeypatch):
    _domain(tmp_path, monkeypatch, symlink=True)
    with pytest.raises(ValidationError, match="outside its account home|unavailable or unsafe"):
        htaccess.reload_domain({"username": "owner1", "domain": "example.test"})
