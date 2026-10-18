"""Domain welcome templates stay in protected configuration, not tenant files."""
import os
import stat

import pytest

from daemon import site_templates
from shared.db import write_session
from shared.models import BrandingSettings
from shared.validation import ValidationError


@pytest.fixture(autouse=True)
def database(isolated_db):
    return isolated_db


def test_default_page_is_bundled_and_has_no_external_dependencies():
    result = site_templates.get_welcome_page()
    assert result["is_custom"] is False
    assert result["content"] == result["default_content"]
    assert result["placeholders"] == ["domain", "panel_name"]
    assert "{{domain}}" in result["content"]
    assert "<script" not in result["content"]
    assert "https://" not in result["content"]


def test_custom_template_roundtrip_reset_and_private_backup(database):
    default = site_templates.get_welcome_page()["content"]
    content = "<h1>{{domain}}</h1><p>{{panel_name}}</p>"
    result = site_templates.set_welcome_page({"content": content})
    assert result["content"] == content and result["is_custom"]
    directory = database.parent / "site-templates"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / "welcome.html").stat().st_mode) == 0o600
    assert not list(directory.glob("*.tmp"))
    reset = site_templates.reset_welcome_page()
    assert reset["content"] == default and not reset["is_custom"]
    assert not (directory / "welcome.html").exists()
    assert [p.read_text() for p in directory.glob("welcome-backup-*.html")] == [content]
    assert site_templates.reset_welcome_page()["status"] == "reset"


def test_backup_history_is_bounded(database):
    for number in range(15):
        site_templates.set_welcome_page({"content": f"<p>{number}</p>"})
    assert len(list((database.parent / "site-templates").glob("welcome-backup-*.html"))) == 10
    assert site_templates.get_welcome_page()["content"] == "<p>14</p>"


@pytest.mark.parametrize("content", ["", "x\x00y", "x" * (site_templates.MAX_PAGE_BYTES + 1), "<p>{{password}}</p>", "{{ __import__('os') }}"])
def test_invalid_template_never_replaces_current(content):
    original = site_templates.get_welcome_page()["content"]
    with pytest.raises(ValidationError):
        site_templates.set_welcome_page({"content": content})
    assert site_templates.get_welcome_page()["content"] == original


def test_rendering_escapes_branding_without_evaluating_code():
    with write_session() as session:
        session.add(BrandingSettings(id=1, panel_name='<script>"Brand"</script>'))
    site_templates.set_welcome_page({"content": "<h1>{{ domain }}</h1><p>{{panel_name}}</p>{% untouched %}"})
    rendered = site_templates.render_domain_welcome("example.com")
    assert "<h1>example.com</h1>" in rendered
    assert "&lt;script&gt;&quot;Brand&quot;&lt;/script&gt;" in rendered
    assert "{% untouched %}" in rendered


def test_invalid_domain_cannot_be_rendered():
    with pytest.raises(ValidationError):
        site_templates.render_domain_welcome('<script>.com')


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "writable", "fifo"])
def test_unsafe_override_is_rejected(database, kind):
    directory = database.parent / "site-templates"
    directory.mkdir(mode=0o700)
    target = database.parent / "untouched.html"
    target.write_text("Do not modify")
    path = directory / "welcome.html"
    if kind == "symlink":
        path.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, path)
    elif kind == "fifo":
        os.mkfifo(path, 0o600)
    else:
        path.write_text("Not protected")
        path.chmod(0o666)
    for operation in (site_templates.get_welcome_page, site_templates.reset_welcome_page,
                      lambda: site_templates.set_welcome_page({"content": "<p>Replacement</p>"})):
        with pytest.raises((OSError, site_templates.SiteTemplatesError)):
            operation()
    assert target.read_text() == "Do not modify"


@pytest.mark.parametrize("kind", ["symlink", "public"])
def test_unsafe_configuration_directory_is_rejected(database, kind):
    directory = database.parent / "site-templates"
    if kind == "symlink":
        target = database.parent / "elsewhere"
        target.mkdir()
        directory.symlink_to(target, target_is_directory=True)
    else:
        directory.mkdir(mode=0o755)
    with pytest.raises((OSError, site_templates.SiteTemplatesError)):
        site_templates.set_welcome_page({"content": "<p>Replacement</p>"})


@pytest.mark.parametrize("action", ["get", "set", "reset"])
def test_welcome_api_keeps_global_admin_authorization(monkeypatch, action):
    from fastapi import HTTPException
    from api.routers import site_templates as router
    from api.security import Identity
    from daemon.rpc_policy import POLICY_BY_OPERATION
    from shared.token_policy import READ_ONLY_OPERATIONS

    calls = []
    monkeypatch.setattr(router, "call_daemon", lambda operation, identity, **params: calls.append((operation, params)) or {"ok": True})
    operation = "templates.welcome_page." + action
    handler = getattr(router, action + "_welcome_page")
    body = [router.WelcomePageBody(content="<p>{{domain}}</p>")] if action == "set" else []
    customer = Identity(panel_user_id=2, username="customer", role="customer", account_id=1, auth_method="session")
    with pytest.raises(HTTPException) as denied:
        handler(*body, customer)
    assert denied.value.status_code == 403 and calls == []
    admin = Identity(panel_user_id=1, username="admin", role="admin", account_id=None, auth_method="session")
    assert handler(*body, admin) == {"ok": True}
    assert calls == [(operation, {"content": "<p>{{domain}}</p>"} if action == "set" else {})]
    assert POLICY_BY_OPERATION[operation] == "global_admin"
    assert (operation in READ_ONLY_OPERATIONS) is (action == "get")
