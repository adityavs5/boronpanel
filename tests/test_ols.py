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


def test_render_vhost_conf_omits_php_ini_block_when_not_set():
    account = make_account()
    domain = make_domain()
    content = ols.render_vhost_conf(account, domain, suspended=False, php_ini=None)
    assert "memory_limit" not in content


def test_render_vhost_conf_always_sets_php_error_log():
    """Phase 3 feature 9: unconditional, not gated on a PhpIniOverride
    row existing -- every account needs a real, persistent PHP error
    log to view, whether or not it has ever customized its PHP ini."""
    account = make_account()
    domain = make_domain()
    content = ols.render_vhost_conf(account, domain, suspended=False, php_ini=None)
    assert 'php_admin_value log_errors "On"' in content
    assert 'php_admin_value error_log "/home/demo1/logs/php-error.log"' in content


def test_render_vhost_conf_includes_php_ini_overrides_when_set():
    """Phase 3 feature 6: rendered into THIS account's own vhost context
    only -- OLS's native per-context phpIniOverride mechanism, not a
    system-wide php.ini edit."""
    account = make_account()
    domain = make_domain()
    php_ini = {
        "memory_limit": "512M",
        "upload_max_filesize": "128M",
        "post_max_size": "128M",
        "max_execution_time": 60,
        "display_errors": True,
        "error_reporting": "E_ALL",
    }
    content = ols.render_vhost_conf(account, domain, suspended=False, php_ini=php_ini)
    assert 'php_admin_value memory_limit "512M"' in content
    assert 'php_admin_value upload_max_filesize "128M"' in content
    assert 'php_admin_value post_max_size "128M"' in content
    assert 'php_admin_value max_execution_time "60"' in content
    assert 'php_admin_value display_errors "On"' in content
    assert 'php_admin_value error_reporting "E_ALL"' in content


def test_render_vhost_conf_php_ini_display_errors_off():
    account = make_account()
    domain = make_domain()
    php_ini = {
        "memory_limit": "256M", "upload_max_filesize": "64M", "post_max_size": "64M",
        "max_execution_time": 30, "display_errors": False, "error_reporting": "E_ALL",
    }
    content = ols.render_vhost_conf(account, domain, suspended=False, php_ini=php_ini)
    assert 'php_admin_value display_errors "Off"' in content


def test_render_vhost_conf_omits_redirect_rules_when_none():
    account = make_account()
    domain = make_domain()
    content = ols.render_vhost_conf(account, domain, suspended=False, redirects=None)
    assert "RewriteRule" not in content


def test_render_vhost_conf_includes_redirect_rule():
    account = make_account()
    domain = make_domain()
    redirects = [{"regex_path": "/old\\-page", "target_url": "https://example.com/new", "status_code": 301}]
    content = ols.render_vhost_conf(account, domain, suspended=False, redirects=redirects)
    assert "RewriteRule ^/old\\-page$ https://example.com/new [R=301,L]" in content


def test_render_vhost_conf_suspended_ignores_redirects():
    """A suspended account serves the suspended page for everything --
    custom redirects must not re-enable serving during suspension."""
    account = make_account()
    domain = make_domain()
    redirects = [{"regex_path": "/old", "target_url": "https://example.com/new", "status_code": 301}]
    content = ols.render_vhost_conf(account, domain, suspended=True, redirects=redirects)
    assert "RewriteRule ^/old$" not in content


def test_render_vhost_conf_omits_hotlink_rules_when_disabled():
    account = make_account()
    domain = make_domain(hotlink_protection_enabled=False, hotlink_allowed_domains=[])
    content = ols.render_vhost_conf(account, domain, suspended=False)
    assert "HTTP_REFERER" not in content


def test_render_vhost_conf_includes_hotlink_rules_when_enabled():
    account = make_account()
    domain = make_domain(hotlink_protection_enabled=True, hotlink_allowed_domains=[])
    content = ols.render_vhost_conf(account, domain, suspended=False)
    assert "RewriteCond %{HTTP_REFERER} !^$" in content
    assert "RewriteCond %{HTTP_REFERER} !^https?://([^/]+\\.)?demo1\\.example(/|$) [NC]" in content
    assert f"RewriteRule \\.({ols.HOTLINK_PROTECTED_EXTENSIONS})$ - [F,L]" in content


def test_render_vhost_conf_hotlink_includes_allowed_domains():
    account = make_account()
    domain = make_domain(hotlink_protection_enabled=True, hotlink_allowed_domains=["cdn.example"])
    content = ols.render_vhost_conf(account, domain, suspended=False)
    assert "!^https?://([^/]+\\.)?demo1\\.example(/|$) [NC]" in content
    assert "!^https?://([^/]+\\.)?cdn\\.example(/|$) [NC]" in content


def test_render_vhost_conf_hotlink_and_redirects_coexist():
    account = make_account()
    domain = make_domain(hotlink_protection_enabled=True, hotlink_allowed_domains=[])
    redirects = [{"regex_path": "/old", "target_url": "https://example.com/new", "status_code": 301}]
    content = ols.render_vhost_conf(account, domain, suspended=False, redirects=redirects)
    assert "HTTP_REFERER" in content
    assert "RewriteRule ^/old$ https://example.com/new [R=301,L]" in content


def test_render_vhost_conf_suspended_ignores_hotlink():
    account = make_account()
    domain = make_domain(hotlink_protection_enabled=True, hotlink_allowed_domains=[])
    content = ols.render_vhost_conf(account, domain, suspended=True)
    assert "HTTP_REFERER" not in content


def test_render_vhost_conf_omits_realm_when_no_protected_dirs():
    account = make_account()
    domain = make_domain()
    content = ols.render_vhost_conf(account, domain, suspended=False, protected_dirs=None)
    assert "realm" not in content


def test_render_vhost_conf_includes_realm_and_context_for_protected_dir():
    account = make_account()
    domain = make_domain()
    protected_dirs = [{"realm_name": "demo1_members", "relative_path": "members", "htpasswd_path": "/home/demo1/public_html/members/.htpasswd"}]
    content = ols.render_vhost_conf(account, domain, suspended=False, protected_dirs=protected_dirs)
    assert "realm demo1_members {" in content
    assert "location              /home/demo1/public_html/members/.htpasswd" in content
    assert "context /members/ {" in content
    assert "realm                   demo1_members" in content
    assert "location                /home/demo1/public_html/members/" in content


def test_render_vhost_conf_suspended_omits_protected_dirs():
    account = make_account()
    domain = make_domain()
    protected_dirs = [{"realm_name": "demo1_members", "relative_path": "members", "htpasswd_path": "/x/.htpasswd"}]
    content = ols.render_vhost_conf(account, domain, suspended=True, protected_dirs=protected_dirs)
    assert "realm" not in content
    assert "context /members/" not in content


def test_protected_dirs_for_domain_rebases_relative_to_docroot(isolated_db):
    from shared.db import write_session
    from shared.models import Account as AccountModel
    from shared.models import FileAuthDir

    with write_session() as session:
        account = AccountModel(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)
        session.flush()
        session.add(FileAuthDir(account_id=account.id, path="public_html/members", realm_name="demo1_members"))
        account_id = account.id

    with write_session() as session:
        result = ols._protected_dirs_for_domain(session, "demo1", account_id, "/home/demo1/public_html")
    assert result == [{
        "realm_name": "demo1_members",
        "relative_path": "members",
        "htpasswd_path": "/home/demo1/public_html/members/.htpasswd",
    }]


def test_protected_dirs_for_domain_excludes_other_domains(isolated_db):
    from shared.db import write_session
    from shared.models import Account as AccountModel
    from shared.models import FileAuthDir

    with write_session() as session:
        account = AccountModel(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)
        session.flush()
        session.add(FileAuthDir(account_id=account.id, path="othersite.example/secret", realm_name="demo1_secret"))
        account_id = account.id

    with write_session() as session:
        result = ols._protected_dirs_for_domain(session, "demo1", account_id, "/home/demo1/public_html")
    assert result == []


def test_render_vhost_conf_omits_access_control_when_no_blocks():
    account = make_account()
    domain = make_domain(ip_block_list=[])
    content = ols.render_vhost_conf(account, domain, suspended=False)
    assert "accessControl" not in content


def test_render_vhost_conf_includes_access_control_when_blocked():
    account = make_account()
    domain = make_domain(ip_block_list=["203.0.113.7", "198.51.100.0/24"])
    content = ols.render_vhost_conf(account, domain, suspended=False)
    assert "accessControl" in content
    assert "allow                 *" in content
    assert "deny                  203.0.113.7, 198.51.100.0/24" in content


def test_render_vhost_conf_access_control_applies_even_when_suspended():
    """A blocked IP shouldn't see the suspended page either."""
    account = make_account()
    domain = make_domain(ip_block_list=["203.0.113.7"])
    content = ols.render_vhost_conf(account, domain, suspended=True)
    assert "accessControl" in content
    assert "deny                  203.0.113.7" in content


def test_redirects_for_domain_escapes_literal_dots(isolated_db):
    from shared.db import write_session
    from shared.models import Redirect

    with write_session() as session:
        session.add(Redirect(domain="demo1.example", path="/old.html", target_url="https://example.com/new", status_code=301))

    with write_session() as session:
        result = ols._redirects_for_domain(session, "demo1.example")
    assert result[0]["regex_path"] == "/old\\.html"


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
    # webmail_hostname/pma_hostname happen to be set to in this
    # environment's real /etc/forgehost/forgehost.toml
    # (shared.config.settings is a module-level singleton loaded from the
    # live system config, not reset between tests) -- caught when Phase 2
    # feature 3 configured a real webmail_hostname on this deployment and
    # this test started failing for a reason that had nothing to do with
    # what it's testing; Phase 3 feature 3's pma_hostname hit the exact
    # same thing.
    monkeypatch.setattr(ols.settings, "webmail_hostname", "")
    monkeypatch.setattr(ols.settings, "pma_hostname", "")
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


def test_render_httpd_config_includes_pma_block_when_configured(monkeypatch):
    monkeypatch.setattr(ols.settings, "pma_hostname", "pma.example.com")
    content = ols.render_httpd_config([], [])
    assert "virtualHost phpmyadmin{" in content
    assert "extProcessor pma_php{" in content
    assert "map                      phpmyadmin pma.example.com" in content


def test_render_httpd_config_omits_pma_block_when_not_configured(monkeypatch):
    monkeypatch.setattr(ols.settings, "pma_hostname", "")
    content = ols.render_httpd_config([], [])
    assert "virtualHost phpmyadmin{" not in content
    assert "extProcessor pma_php{" not in content


def test_render_pma_vhost_conf_uses_configured_docroot(monkeypatch):
    monkeypatch.setattr(ols.settings, "pma_docroot", "/usr/share/phpmyadmin")
    content = ols.render_pma_vhost_conf("/etc/forgehost/ssl/default.key", "/etc/forgehost/ssl/default.crt")
    assert "docRoot                   /usr/share/phpmyadmin" in content
    assert "lsapi:pma_php php" in content
    assert "include_path" in content


def test_bootstrap_pma_requires_hostname_configured(monkeypatch):
    monkeypatch.setattr(ols.settings, "pma_hostname", "")
    with pytest.raises(RuntimeError):
        ols.bootstrap_pma()


# --- Phase 5 feature 7: ModSecurity/WAF template context -------------------


def test_render_httpd_config_omits_modsecurity_module_when_waf_disabled():
    content = ols.render_httpd_config([], [])
    assert "module mod_security" not in content


def test_render_httpd_config_includes_modsecurity_module_when_waf_enabled():
    waf = {
        "waf_enabled": True,
        "waf_audit_log": "/var/log/forgehost/modsecurity-audit.log",
        "waf_rules_file": "/etc/modsecurity/modsec_includes.conf",
        "waf_domain_overrides": [],
        "waf_custom_rules": [],
    }
    content = ols.render_httpd_config([], [], waf=waf)
    assert "module mod_security {" in content
    assert "modsecurity         on" in content
    assert "modsecurity_rules_file   /etc/modsecurity/modsec_includes.conf" in content
    assert "SecAuditLog /var/log/forgehost/modsecurity-audit.log" in content


def test_render_httpd_config_waf_domain_override_generates_rule_engine_off():
    waf = {
        "waf_enabled": True,
        "waf_audit_log": "/var/log/forgehost/modsecurity-audit.log",
        "waf_rules_file": "/etc/modsecurity/modsec_includes.conf",
        "waf_domain_overrides": [{"domain": "example.com"}],
        "waf_custom_rules": [],
    }
    content = ols.render_httpd_config([], [], waf=waf)
    assert '@streq example.com' in content
    assert "ctl:ruleEngine=Off" in content


def test_render_httpd_config_waf_custom_rule_generates_scoped_chain():
    waf = {
        "waf_enabled": True,
        "waf_audit_log": "/var/log/forgehost/modsecurity-audit.log",
        "waf_rules_file": "/etc/modsecurity/modsec_includes.conf",
        "waf_domain_overrides": [],
        "waf_custom_rules": [{"id": 7, "domain": "shop.example.com", "target": "ARGS", "pattern": "badbot"}],
    }
    content = ols.render_httpd_config([], [], waf=waf)
    assert '@streq shop.example.com' in content
    assert 'SecRule ARGS "@rx badbot"' in content
    assert "forgehost-custom-rule-7" in content


def test_waf_template_context_reflects_db_state(isolated_db):
    from shared.db import write_session
    from shared.models import WafCustomRule, WafDomainOverride, WafSettings

    with write_session() as session:
        session.add(WafSettings(id=1, enabled=True))
        session.add(WafDomainOverride(domain="off.example.com", disabled=True))
        session.add(WafCustomRule(domain="shop.example.com", target="ARGS", pattern="badbot"))

    with write_session() as session:
        ctx = ols.waf_template_context(session)

    assert ctx["waf_enabled"] is True
    assert ctx["waf_domain_overrides"] == [{"domain": "off.example.com"}]
    assert len(ctx["waf_custom_rules"]) == 1
    assert ctx["waf_custom_rules"][0]["domain"] == "shop.example.com"
