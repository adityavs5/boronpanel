import pytest

from daemon import ols
from shared.models import Account


def make_account(**overrides):
    defaults = dict(username="demo1", status="active", uid=2000, gid=2000, php_version="8.3")
    defaults.update(overrides)
    account = Account(**defaults)
    account.id = 1
    return account


def test_render_vhost_conf_active_uses_real_docroot():
    account = make_account()
    content = ols.render_vhost_conf(account, ["demo1.example"], suspended=False)
    assert "docRoot                   /home/demo1/public_html" in content
    assert "extUser                 demo1" in content
    assert "extGroup                demo1" in content
    assert "/usr/local/lsws/lsphp83/bin/lsphp" in content
    assert "_suspended" not in content


def test_render_vhost_conf_suspended_points_at_suspended_page():
    account = make_account()
    content = ols.render_vhost_conf(account, ["demo1.example"], suspended=True)
    assert ols.settings.suspended_page_root in content
    assert "RewriteRule ^(.*)$ /index.html [L]" in content
    # PHP external app stays defined even while suspended -- unsuspend must
    # be a pure metadata flip, not a config rebuild from scratch.
    assert "extUser                 demo1" in content


def test_render_vhost_conf_php_version_selects_correct_app_name():
    account = make_account(php_version="8.1")
    content = ols.render_vhost_conf(account, ["demo1.example"], suspended=False)
    assert "demo1_php81" in content
    assert "/usr/local/lsws/lsphp81/bin/lsphp" in content


def test_render_httpd_config_empty_vhosts_has_no_virtualhost_block():
    content = ols.render_httpd_config([])
    assert "virtualHost" not in content
    assert "listener HTTP{" in content


def test_render_httpd_config_includes_each_vhost_and_its_domains():
    vhosts = [
        {"name": "demo1", "home_dir": "/home/demo1", "domains": ["demo1.example", "www.demo1.example"]},
        {"name": "demo2", "home_dir": "/home/demo2", "domains": ["demo2.example"]},
    ]
    content = ols.render_httpd_config(vhosts)
    assert "virtualHost demo1{" in content
    assert "virtualHost demo2{" in content
    assert "map                      demo1 demo1.example, www.demo1.example" in content
    assert "map                      demo2 demo2.example" in content


def test_php_app_name_and_lsphp_path_helpers():
    assert ols._php_app_name("demo1", "8.3") == "demo1_php83"
    assert ols._lsphp_path("8.1") == "/usr/local/lsws/lsphp81/bin/lsphp"


def test_static_precheck_rejects_unbalanced_braces():
    assert not ols._static_precheck("context / { allowBrowse 1").ok


def test_static_precheck_rejects_empty():
    assert not ols._static_precheck("   ").ok


def test_static_precheck_accepts_balanced():
    assert ols._static_precheck("context / { allowBrowse 1 }").ok
