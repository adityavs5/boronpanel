import pytest

from daemon import custom_pages as cp
from shared.validation import ValidationError


@pytest.fixture()
def safe_fs(tmp_path, monkeypatch):
    """custom_pages talks to daemon/safeio.py (real O_NOFOLLOW filesystem
    ops, including a real fchown) and pwd.getpwnam (a real system user) --
    getpwnam is faked to return the CURRENT process's own uid/gid (same
    "chown to self always succeeds, whether the test runs as root or not"
    convention tests/test_safeio.py's own fixtures use, e.g.
    os.getuid()/os.getgid()), so this test suite runs the same way root or
    unprivileged."""
    import os
    import types

    fake_home = tmp_path / "home" / "demo1"
    fake_home.mkdir(parents=True)
    monkeypatch.setattr(cp.settings, "home_base", str(tmp_path / "home"))

    def fake_getpwnam(username):
        return types.SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())

    import pwd as real_pwd

    monkeypatch.setattr(real_pwd, "getpwnam", fake_getpwnam)
    monkeypatch.setattr(cp, "_grant_webserver_acl", lambda *args: None)
    monkeypatch.setattr(cp, "_grant_traversal_acl", lambda *args: None)
    return fake_home


def test_ensure_pages_dir_creates_directory(safe_fs):
    path = cp.ensure_pages_dir("demo1", "demo1.example")
    assert path.endswith("demo1.example/error_pages")
    from pathlib import Path

    assert Path(path).is_dir()


def test_set_and_get_error_page(safe_fs):
    cp.set_error_page("demo1", "demo1.example", 404, "<html>custom 404</html>")
    content = cp.get_error_page("demo1", "demo1.example", 404)
    assert content == "<html>custom 404</html>"


def test_set_error_page_rejects_unknown_code(safe_fs):
    with pytest.raises(ValidationError):
        cp.set_error_page("demo1", "demo1.example", 401, "<html>nope</html>")


def test_set_error_page_rejects_empty_content(safe_fs):
    with pytest.raises(ValidationError):
        cp.set_error_page("demo1", "demo1.example", 404, "   ")


def test_get_error_page_absent_returns_none(safe_fs):
    assert cp.get_error_page("demo1", "demo1.example", 500) is None


def test_error_page_read_delete_deny_symlink_ancestor(safe_fs, tmp_path):
    protected = tmp_path / 'protected'
    (protected / 'error_pages').mkdir(parents=True)
    canary = protected / 'error_pages/404.html'
    canary.write_text('private canary')
    (safe_fs / 'demo1.example').symlink_to(protected, target_is_directory=True)
    assert cp.get_error_page('demo1', 'demo1.example', 404) is None
    cp.delete_error_page('demo1', 'demo1.example', 404)
    assert canary.read_text() == 'private canary'


def test_acl_helpers_drop_privileges(monkeypatch):
    from daemon import handlers_domain, procutil
    calls = []
    def capture(args, **kwargs):
        calls.append((args, kwargs))
    monkeypatch.setattr(cp, 'run', capture)
    monkeypatch.setattr(procutil, 'run', capture)
    cp._grant_webserver_acl('/fixture/pages', 1234, 1234)
    cp._grant_traversal_acl('/fixture/domain', 1234, 1234)
    handlers_domain._grant_webserver_acl('/fixture/docroot', 1234, 1234)
    assert len(calls) == 3
    assert all(kwargs['uid'] == 1234 and kwargs['gid'] == 1234 for _, kwargs in calls)
    assert all('-P' in args for args, _ in calls if '-R' in args)


def test_delete_error_page_removes_it(safe_fs):
    cp.set_error_page("demo1", "demo1.example", 403, "<html>forbidden</html>")
    cp.delete_error_page("demo1", "demo1.example", 403)
    assert cp.get_error_page("demo1", "demo1.example", 403) is None


def test_delete_error_page_idempotent_when_absent(safe_fs):
    cp.delete_error_page("demo1", "demo1.example", 500)  # must not raise


def test_list_error_pages_reports_custom_vs_default(safe_fs):
    cp.set_error_page("demo1", "demo1.example", 404, "<html>custom</html>")
    result = cp.list_error_pages("demo1", "demo1.example")
    by_code = {p["code"]: p["has_custom"] for p in result["pages"]}
    assert by_code[404] is True
    assert by_code[500] is False


def test_resolve_error_pages_uses_default_for_uncustomized_code(safe_fs):
    cp.set_error_page("demo1", "demo1.example", 404, "<html>custom</html>")
    pages = cp.resolve_error_pages("demo1", "demo1.example")
    assert pages[404] == "/.boron-error-pages/404.html"
    assert pages[500] == "/.boron-default-errors/500.html"


def test_resolve_error_pages_maintenance_overrides_503(safe_fs):
    pages = cp.resolve_error_pages("demo1", "demo1.example", maintenance_active=True)
    assert pages[503] == f"/.boron-error-pages/{cp.MAINTENANCE_PAGE_NAME}"


def test_resolve_error_pages_without_maintenance_uses_default_503(safe_fs):
    pages = cp.resolve_error_pages("demo1", "demo1.example", maintenance_active=False)
    assert pages[503] == "/.boron-default-errors/503.html"


def test_default_branded_pages_exist_on_disk():
    for code in cp.ERROR_CODES:
        assert (cp.DEFAULT_PAGES_DIR / f"{code}.html").exists()
