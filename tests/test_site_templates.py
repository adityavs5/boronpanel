"""QA round 2, item 10: admin-editable suspension page + welcome email
template."""
import pytest

from daemon import site_templates
from shared.validation import ValidationError


@pytest.fixture()
def suspended_page_root(tmp_path, monkeypatch, isolated_db):
    root = tmp_path / "_suspended"
    root.mkdir()
    (root / "index.html").write_text("<html>original suspended page</html>")
    monkeypatch.setattr(site_templates.settings, "suspended_page_root", str(root))
    return root


@pytest.fixture()
def backup_dir(tmp_path, monkeypatch):
    d = tmp_path / "backups"
    monkeypatch.setattr(site_templates.settings, "backup_dir", str(d))
    return d


# --- suspended page ----------------------------------------------------------


def test_get_suspended_page_reads_current_content(suspended_page_root):
    result = site_templates.get_suspended_page({})
    assert "original suspended page" in result["content"]


def test_get_suspended_page_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(site_templates.settings, "suspended_page_root", str(tmp_path / "doesnotexist"))
    with pytest.raises(site_templates.SiteTemplatesError):
        site_templates.get_suspended_page({})


def test_set_suspended_page_replaces_content(suspended_page_root, backup_dir):
    new_content = "<html><body>This account has been suspended.</body></html>"
    result = site_templates.set_suspended_page({"content": new_content})
    assert result["status"] == "saved"
    assert (suspended_page_root / "index.html").read_text() == new_content


def test_set_suspended_page_backs_up_previous_version(suspended_page_root, backup_dir):
    site_templates.set_suspended_page({"content": "<html>new content</html>"})
    backups = list((backup_dir / "suspended-page").iterdir())
    assert len(backups) == 1
    assert "original suspended page" in (backups[0] / "index.html").read_text()


def test_set_suspended_page_is_atomic_no_tmp_file_left(suspended_page_root, backup_dir):
    site_templates.set_suspended_page({"content": "<html>x</html>"})
    assert not (suspended_page_root / "index.html.tmp").exists()
    assert list(suspended_page_root.iterdir()) == [suspended_page_root / "index.html"]


def test_set_suspended_page_rejects_empty_content(suspended_page_root, backup_dir):
    with pytest.raises(ValidationError):
        site_templates.set_suspended_page({"content": ""})


def test_set_suspended_page_rejects_oversized_content(suspended_page_root, backup_dir):
    with pytest.raises(ValidationError):
        site_templates.set_suspended_page({"content": "x" * (site_templates.MAX_PAGE_BYTES + 1)})


def test_set_suspended_page_rejects_nul_byte(suspended_page_root, backup_dir):
    with pytest.raises(ValidationError):
        site_templates.set_suspended_page({"content": "hello\x00world"})


def test_set_suspended_page_sets_world_readable_mode(suspended_page_root, backup_dir):
    import stat

    site_templates.set_suspended_page({"content": "<html>x</html>"})
    mode = (suspended_page_root / "index.html").stat().st_mode
    assert mode & stat.S_IROTH  # OLS's "nobody" worker must be able to read it


def test_set_suspended_page_creates_root_if_missing(tmp_path, backup_dir, monkeypatch, isolated_db):
    root = tmp_path / "brand-new-root"
    monkeypatch.setattr(site_templates.settings, "suspended_page_root", str(root))
    result = site_templates.set_suspended_page({"content": "<html>x</html>"})
    assert result["status"] == "saved"
    assert (root / "index.html").exists()


def test_suspension_design_catalog_has_four_safe_previews(suspended_page_root, backup_dir):
    result = site_templates.get_suspension_designs({})
    assert {item["key"] for item in result["templates"]} == {"clean", "gradient", "classic", "minimal"}
    assert all("<script" not in item["html"].lower() for item in result["templates"])


def test_apply_suspension_design_escapes_copy_and_persists_selection(suspended_page_root, backup_dir):
    result = site_templates.apply_suspension_design({
        "template_key": "gradient",
        "accent_color": "#AABBCC",
        "heading": "Account <script>alert(1)</script>",
        "message": "Contact <img src=x onerror=alert(2)> support.",
    })
    content = (suspended_page_root / "index.html").read_text()
    assert result["template_key"] == "gradient"
    assert "<script>" not in content and "<img" not in content
    assert "&lt;script&gt;" in content and "&lt;img" in content
    fetched = site_templates.get_suspension_designs({})
    assert fetched["current"]["heading"].startswith("Account <script>")


def test_apply_suspension_design_rejects_invalid_color(suspended_page_root, backup_dir):
    with pytest.raises(ValidationError, match="six-digit hex"):
        site_templates.apply_suspension_design({
            "template_key": "clean", "accent_color": "red",
            "heading": "Suspended", "message": "Contact support.",
        })


def test_custom_html_marks_design_custom(suspended_page_root, backup_dir):
    site_templates.apply_suspension_design({
        "template_key": "minimal", "accent_color": "#123456",
        "heading": "Suspended", "message": "Contact support.",
    })
    site_templates.set_suspended_page({"content": "<html>custom</html>"})
    assert site_templates.get_suspension_designs({})["current"]["template_key"] == "custom"


# --- welcome email template ---------------------------------------------------


def test_get_welcome_email_template_default_when_never_set(isolated_db):
    result = site_templates.get_welcome_email_template({})
    assert result["subject"] is None
    assert result["body"] is None
    assert "username" in result["placeholders"]


def test_set_and_get_welcome_email_template(isolated_db):
    result = site_templates.set_welcome_email_template({
        "subject": "Welcome to {{panel_name}}!",
        "body": "Hi {{username}}, your password is {{password}}.",
    })
    assert result["subject"] == "Welcome to {{panel_name}}!"
    fetched = site_templates.get_welcome_email_template({})
    assert fetched["body"] == "Hi {{username}}, your password is {{password}}."


def test_set_welcome_email_template_upserts_not_duplicates(isolated_db):
    site_templates.set_welcome_email_template({"subject": "First", "body": "Body one"})
    site_templates.set_welcome_email_template({"subject": "Second", "body": "Body two"})
    result = site_templates.get_welcome_email_template({})
    assert result["subject"] == "Second"


def test_set_welcome_email_template_rejects_unknown_placeholder(isolated_db):
    with pytest.raises(ValidationError, match="unknown placeholder"):
        site_templates.set_welcome_email_template({"subject": "Hi", "body": "Your {{secret_token}} is here"})


def test_set_welcome_email_template_accepts_known_placeholders(isolated_db):
    body = "{{username}} {{password}} {{primary_domain}} {{panel_name}}"
    result = site_templates.set_welcome_email_template({"subject": "s", "body": body})
    assert result["body"] == body


def test_set_welcome_email_template_rejects_oversized_body(isolated_db):
    with pytest.raises(ValidationError):
        site_templates.set_welcome_email_template({"body": "x" * (site_templates.MAX_BODY_LEN + 1)})


def test_reset_welcome_email_template_reverts_to_default(isolated_db):
    site_templates.set_welcome_email_template({"subject": "Custom", "body": "Custom body"})
    site_templates.reset_welcome_email_template({})
    result = site_templates.get_welcome_email_template({})
    assert result["subject"] is None
    assert result["body"] is None


def test_reset_welcome_email_template_idempotent_when_never_set(isolated_db):
    result = site_templates.reset_welcome_email_template({})  # must not raise
    assert result["status"] == "reset"
