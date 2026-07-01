import pytest

from daemon import ols
from shared.models import Account


def make_account(**overrides):
    defaults = dict(username="demo1", status="active", uid=2000, gid=2000, php_version="8.3")
    defaults.update(overrides)
    account = Account(**defaults)
    account.id = 1
    return account


def make_domain(**overrides):
    defaults = dict(id=1, domain="demo1.example", docroot="/home/demo1/public_html", ssl_status="none")
    defaults.update(overrides)
    return defaults


def test_render_vhost_conf_active_uses_real_docroot():
    account = make_account()
    domain = make_domain()
    content = ols.render_vhost_conf(account, domain, suspended=False)
    assert "docRoot                   /home/demo1/public_html" in content
    assert "lsapi:demo1_php83 php" in content
    assert "_suspended" not in content


def test_render_vhost_conf_uses_domains_own_docroot_not_account_public_html():
    """Phase 2 feature 4: the actual fix for the Phase 1 gap -- a
    subdomain/addon domain's own docroot must be served, not silently
    aliased to the account's public_html like every non-primary domain
    used to be before this vhost-per-domain refactor."""
    account = make_account()
    domain = make_domain(domain="blog.demo1.example", docroot="/home/demo1/blog.demo1.example")
    content = ols.render_vhost_conf(account, domain, suspended=False)
    assert "docRoot                   /home/demo1/blog.demo1.example" in content
    assert "docRoot                   /home/demo1/public_html" not in content


def test_render_vhost_conf_suspended_points_at_suspended_page():
    account = make_account()
    domain = make_domain()
    content = ols.render_vhost_conf(account, domain, suspended=True)
    assert ols.settings.suspended_page_root in content
    assert "RewriteRule ^(.*)$ /index.html [L]" in content
    # scripthandler stays defined even while suspended -- unsuspend must be
    # a pure metadata flip, not a config rebuild from scratch.
    assert "lsapi:demo1_php83 php" in content


def test_render_vhost_conf_php_version_selects_correct_app_name():
    account = make_account(php_version="8.1")
    domain = make_domain()
    content = ols.render_vhost_conf(account, domain, suspended=False)
    assert "lsapi:demo1_php81 php" in content


def test_render_vhost_conf_no_longer_defines_its_own_extprocessor():
    """Phase 2 feature 4: the extProcessor moved to server level
    (httpd_config.conf.j2, one per account) so multiple per-domain vhosts
    under the same account can share it -- a per-domain vhost file must
    only *reference* it by name, never define one itself."""
    account = make_account()
    domain = make_domain()
    content = ols.render_vhost_conf(account, domain, suspended=False)
    assert "extprocessor" not in content.lower()


def test_render_vhost_conf_uses_domain_specific_ssl_paths():
    account = make_account()
    domain = make_domain()
    content = ols.render_vhost_conf(account, domain, suspended=False, ssl_key_file="/custom/key.pem", ssl_cert_file="/custom/cert.pem")
    assert "keyFile                 /custom/key.pem" in content
    assert "certFile                /custom/cert.pem" in content


def test_render_httpd_config_empty_vhosts_has_no_virtualhost_block(monkeypatch):
    # Explicit, not incidental: this test's whole point is "no vhosts in ->
    # no virtualHost block out", so it must not depend on whatever
    # webmail_hostname happens to be set to in this environment's real
    # /etc/forgehost/forgehost.toml (shared.config.settings is a
    # module-level singleton loaded from the live system config, not
    # reset between tests) -- caught when Phase 2 feature 3 configured a
    # real webmail_hostname on this deployment and this test started
    # failing for a reason that had nothing to do with what it's testing.
    monkeypatch.setattr(ols.settings, "webmail_hostname", "")
    content = ols.render_httpd_config([], [])
    assert "virtualHost" not in content
    assert "listener HTTP{" in content


def test_render_httpd_config_includes_each_domain_as_its_own_vhost():
    """Phase 2 feature 4: one vhost per domain now, keyed to a
    dot-to-underscore sanitized vhost name, with its own listener map
    entry -- not one aggregated vhost per account like before."""
    domain_vhosts = [
        {"vhost_name": "demo1_example", "domain": "demo1.example", "account_home": "/home/demo1"},
        {"vhost_name": "www_demo1_example", "domain": "www.demo1.example", "account_home": "/home/demo1"},
        {"vhost_name": "demo2_example", "domain": "demo2.example", "account_home": "/home/demo2"},
    ]
    content = ols.render_httpd_config(domain_vhosts, [])
    assert "virtualHost demo1_example{" in content
    assert "virtualHost www_demo1_example{" in content
    assert "virtualHost demo2_example{" in content
    assert "map                      demo1_example demo1.example" in content
    assert "map                      www_demo1_example www.demo1.example" in content
    assert "map                      demo2_example demo2.example" in content


def test_render_httpd_config_includes_one_extprocessor_per_account():
    account_procs = [
        {"username": "demo1", "php_app_name": "demo1_php83", "lsphp_path": "/usr/local/lsws/lsphp83/bin/lsphp"},
        {"username": "demo2", "php_app_name": "demo2_php81", "lsphp_path": "/usr/local/lsws/lsphp81/bin/lsphp"},
    ]
    content = ols.render_httpd_config([], account_procs)
    assert "extProcessor demo1_php83{" in content
    assert "extProcessor demo2_php81{" in content
    assert "extUser                         demo1" in content
    assert "extUser                         demo2" in content


def test_php_app_name_and_lsphp_path_helpers():
    assert ols._php_app_name("demo1", "8.3") == "demo1_php83"
    assert ols._lsphp_path("8.1") == "/usr/local/lsws/lsphp81/bin/lsphp"


def test_vhost_name_sanitizes_dots():
    assert ols._vhost_name("blog.demo1.example") == "blog_demo1_example"


def test_ssl_paths_for_domain_falls_back_to_default_when_not_active():
    domain = make_domain(ssl_status="none")
    assert ols._ssl_paths_for_domain(domain) == (ols.DEFAULT_SSL_KEY, ols.DEFAULT_SSL_CERT)


def test_ssl_paths_for_domain_falls_back_when_active_but_files_missing():
    # ssl_status says active but no certbot files actually exist for this
    # made-up domain -- must not hand OLS a path that doesn't exist.
    domain = make_domain(domain="not-a-real-cert.example", ssl_status="active")
    assert ols._ssl_paths_for_domain(domain) == (ols.DEFAULT_SSL_KEY, ols.DEFAULT_SSL_CERT)


def test_static_precheck_rejects_unbalanced_braces():
    assert not ols._static_precheck("context / { allowBrowse 1").ok


def test_static_precheck_rejects_empty():
    assert not ols._static_precheck("   ").ok


def test_static_precheck_accepts_balanced():
    assert ols._static_precheck("context / { allowBrowse 1 }").ok


def test_render_httpd_config_includes_webmail_block_when_configured(monkeypatch):
    monkeypatch.setattr(ols.settings, "webmail_hostname", "webmail.example.com")
    content = ols.render_httpd_config([], [])
    assert "virtualHost roundcube{" in content
    assert "extProcessor roundcube_php{" in content
    assert "map                      roundcube webmail.example.com" in content


def test_render_httpd_config_omits_webmail_block_when_not_configured(monkeypatch):
    monkeypatch.setattr(ols.settings, "webmail_hostname", "")
    content = ols.render_httpd_config([], [])
    assert "virtualHost roundcube{" not in content
    assert "extProcessor roundcube_php{" not in content


def test_render_webmail_vhost_conf_uses_configured_docroot(monkeypatch):
    monkeypatch.setattr(ols.settings, "webmail_docroot", "/var/lib/roundcube/public_html")
    content = ols.render_webmail_vhost_conf("/etc/forgehost/ssl/default.key", "/etc/forgehost/ssl/default.crt")
    assert "docRoot                   /var/lib/roundcube/public_html" in content
    assert "lsapi:roundcube_php php" in content


def test_bootstrap_webmail_requires_hostname_configured(monkeypatch):
    monkeypatch.setattr(ols.settings, "webmail_hostname", "")
    with pytest.raises(RuntimeError):
        ols.bootstrap_webmail()
