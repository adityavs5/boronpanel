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


def test_with_disable_functions_returns_php_ini_unchanged_when_none():
    """No admin override at any scope -- absence means default (the
    system php.ini's own hardened disable_functions applies, nothing
    rendered here)."""
    assert ols._with_disable_functions(None, None) is None
    php_ini = {"memory_limit": "256M", "extras": []}
    assert ols._with_disable_functions(php_ini, None) is php_ini


def test_with_disable_functions_builds_minimal_php_ini_when_none_existed():
    """An account/domain with zero other PHP customization can still get
    a disable_functions override rendered -- must not require some other
    php_ini row to already exist first."""
    result = ols._with_disable_functions(None, "exec,system")
    assert result["memory_limit"] == ols.phpdirectives.DEFAULTS["memory_limit"]
    assert {"name": "disable_functions", "value": "exec,system"} in result["extras"]


def test_with_disable_functions_appends_to_existing_extras():
    php_ini = {"memory_limit": "256M", "extras": [{"name": "max_input_vars", "value": "2000"}]}
    result = ols._with_disable_functions(php_ini, "exec")
    names = {e["name"] for e in result["extras"]}
    assert names == {"max_input_vars", "disable_functions"}


def test_with_disable_functions_replaces_not_duplicates():
    php_ini = {"extras": [{"name": "disable_functions", "value": "exec"}]}
    result = ols._with_disable_functions(php_ini, "exec,system")
    matching = [e for e in result["extras"] if e["name"] == "disable_functions"]
    assert matching == [{"name": "disable_functions", "value": "exec,system"}]


def test_render_vhost_conf_renders_disable_functions_extra():
    account = make_account()
    domain = make_domain()
    php_ini = ols._with_disable_functions(None, "exec,shell_exec")
    content = ols.render_vhost_conf(account, domain, suspended=False, php_ini=php_ini)
    assert 'php_admin_value disable_functions "exec,shell_exec"' in content


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
    # environment's real /etc/boron/boron.toml
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
        {"username": "demo1", "php_app_name": "demo1_php83", "lsphp_path": "/usr/local/lsws/lsphp83/bin/lsphp", "home_dir": "/home/demo1"},
        {"username": "demo2", "php_app_name": "demo2_php81", "lsphp_path": "/usr/local/lsws/lsphp81/bin/lsphp", "home_dir": "/home/demo2"},
    ]
    content = ols.render_httpd_config([], account_procs)
    assert "extProcessor demo1_php83{" in content
    assert "extProcessor demo2_php81{" in content
    assert "extUser                         demo1" in content
    assert "extUser                         demo2" in content
    assert "TMPDIR=/home/demo1/tmp" in content
    assert "TMPDIR=/home/demo2/tmp" in content


def test_render_httpd_config_scan_dir_env_only_when_extension_override():
    """PHP_INI_SCAN_DIR must be rendered for exactly the accounts with an
    extension override (php_scan_dir set) -- an account without one keeps
    the compiled-in stock scan dir by having NO env line at all."""
    account_procs = [
        {"username": "demo1", "php_app_name": "demo1_php83", "lsphp_path": "/usr/local/lsws/lsphp83/bin/lsphp", "home_dir": "/home/demo1", "php_scan_dir": "/home/demo1/.php/83/conf.d"},
        {"username": "demo2", "php_app_name": "demo2_php81", "lsphp_path": "/usr/local/lsws/lsphp81/bin/lsphp", "home_dir": "/home/demo2", "php_scan_dir": None},
    ]
    content = ols.render_httpd_config([], account_procs)
    assert "PHP_INI_SCAN_DIR=/home/demo1/.php/83/conf.d" in content
    assert "PHP_INI_SCAN_DIR=/home/demo2" not in content


def test_render_vhost_conf_includes_extra_directives():
    """PhpIniDirective key/value rows (max_input_vars etc.) render as
    php_admin_value lines alongside the legacy six."""
    account = make_account()
    domain = make_domain()
    php_ini = {
        "memory_limit": "128M",
        "upload_max_filesize": "2M",
        "post_max_size": "8M",
        "max_execution_time": 30,
        "display_errors": False,
        "error_reporting": "E_ALL",
        "extras": [
            {"name": "max_input_vars", "value": "5000"},
            {"name": "date.timezone", "value": "Asia/Kolkata"},
        ],
    }
    content = ols.render_vhost_conf(account, domain, suspended=False, php_ini=php_ini)
    assert 'php_admin_value max_input_vars "5000"' in content
    assert 'php_admin_value date.timezone "Asia/Kolkata"' in content


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
    content = ols.render_webmail_vhost_conf("/etc/boron/ssl/default.key", "/etc/boron/ssl/default.crt")
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
    content = ols.render_pma_vhost_conf("/etc/boron/ssl/default.key", "/etc/boron/ssl/default.crt")
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
        "waf_audit_log": "/var/log/boron/modsecurity-audit.log",
        "waf_rules_file": "/etc/modsecurity/modsec_includes.conf",
        "waf_domain_overrides": [],
        "waf_custom_rules": [],
    }
    content = ols.render_httpd_config([], [], waf=waf)
    assert "module mod_security {" in content
    assert "modsecurity         on" in content
    assert "modsecurity_rules_file   /etc/modsecurity/modsec_includes.conf" in content
    assert "SecAuditLog /var/log/boron/modsecurity-audit.log" in content


def test_render_httpd_config_waf_domain_override_generates_rule_engine_off():
    waf = {
        "waf_enabled": True,
        "waf_audit_log": "/var/log/boron/modsecurity-audit.log",
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
        "waf_audit_log": "/var/log/boron/modsecurity-audit.log",
        "waf_rules_file": "/etc/modsecurity/modsec_includes.conf",
        "waf_domain_overrides": [],
        "waf_custom_rules": [{"id": 7, "domain": "shop.example.com", "target": "ARGS", "pattern": "badbot"}],
    }
    content = ols.render_httpd_config([], [], waf=waf)
    assert '@streq shop.example.com' in content
    assert 'SecRule ARGS "@rx badbot"' in content
    assert "boron-custom-rule-7" in content


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


def test_render_vhost_conf_disables_symlink_following():
    """Security fix (Phase 6a research finding): this was hardcoded to
    `allowSymbolLink 1` (follow unconditionally) -- confirmed via this
    box's own installed OLS docs (VirtualHosts_Help.html) that this is a
    whole-vhost, not per-context, setting, and that 0 is the documented
    security-hardened choice. No Boron automation creates or relies on
    a symlink under an account's docroot (confirmed by grep across every
    daemon/*.py), so disabling it has no legitimate functionality to
    break."""
    account = make_account()
    domain = make_domain()
    content = ols.render_vhost_conf(account, domain, suspended=False)
    assert "allowSymbolLink           0" in content
    assert "allowSymbolLink           1" not in content


def test_render_vhost_conf_open_basedir_excludes_shared_system_tmp():
    """Security fix (Phase 6a research finding): every account's
    open_basedir used to include the shared, world-writable-sticky system
    /tmp, letting one account enumerate another's temp/upload-in-progress
    filenames. The account's own private tmp dir was already in this
    string before the fix -- only the shared ":/tmp" fallback is removed."""
    account = make_account()
    domain = make_domain()
    content = ols.render_vhost_conf(account, domain, suspended=False)
    assert 'php_admin_value open_basedir "/home/demo1/public_html:/home/demo1/tmp"' in content
    assert ":/tmp\"" not in content


def test_render_vhost_conf_sets_upload_tmp_dir_to_account_tmp():
    """upload_tmp_dir defaults to empty in the real lsphp build (confirmed
    via `lsphp -i`), which falls through to the system /tmp open_basedir
    now excludes -- without this override, uploads would fail with an
    open_basedir violation."""
    account = make_account()
    domain = make_domain()
    content = ols.render_vhost_conf(account, domain, suspended=False)
    assert 'php_admin_value upload_tmp_dir "/home/demo1/tmp"' in content


def test_all_active_vhosts_account_procs_includes_home_dir(isolated_db):
    """Feeds templates/httpd_config.conf.j2's new per-account TMPDIR env
    line (see test_render_httpd_config_includes_one_extprocessor_per_account) --
    without this key present, rendering would raise (StrictUndefined)."""
    from shared.db import write_session
    from shared.models import Account as AccountModel
    from shared.models import Domain as DomainModel

    with write_session() as session:
        account = AccountModel(username="demo1", uid=5001, gid=5001, status="active", php_version="8.3")
        session.add(account)
        session.flush()
        session.add(DomainModel(account_id=account.id, domain="demo1.example", docroot="/home/demo1/public_html", ssl_status="none"))

    with write_session() as session:
        _, account_procs = ols._all_active_vhosts(session)
    assert account_procs == [{
        "username": "demo1",
        "php_app_name": "demo1_php83",
        "lsphp_path": "/usr/local/lsws/lsphp83/bin/lsphp",
        "home_dir": "/home/demo1",
        # None (no PhpExtensionSet row) means no PHP_INI_SCAN_DIR env line
        # is rendered -- stock compiled-in extension behavior.
        "php_scan_dir": None,
    }]


def test_refresh_all_vhosts_bootstraps_tmp_dir_and_refreshes_active_accounts(isolated_db, monkeypatch):
    """Security fix migration (Phase 6a research finding): backfills
    pre-existing accounts (created before this fix) with both the tmp dir
    their new open_basedir depends on and a re-rendered vhost -- not just
    accounts created after the fix. Terminated accounts must be excluded,
    same as _all_active_vhosts already excludes them from httpd_config.conf."""
    from shared.db import write_session
    from shared.models import Account as AccountModel

    with write_session() as session:
        session.add(AccountModel(username="active1", uid=5001, gid=5001, status="active", php_version="8.3"))
        session.add(AccountModel(username="suspended1", uid=5002, gid=5002, status="suspended", php_version="8.3"))
        session.add(AccountModel(username="terminated1", uid=5003, gid=5003, status="terminated", php_version="8.3"))

    tmp_dir_calls = []
    refresh_calls = []
    monkeypatch.setattr(ols.sysops, "ensure_tmp_dir", lambda username: tmp_dir_calls.append(username))
    monkeypatch.setattr(ols, "refresh_vhost", lambda account: refresh_calls.append(account.username))

    ols.refresh_all_vhosts()

    assert sorted(tmp_dir_calls) == ["active1", "suspended1"]
    assert sorted(refresh_calls) == ["active1", "suspended1"]


# --- Phase 7a features 1/2: NodeJS/Python app reverse-proxy rendering ------


def test_render_vhost_conf_renders_proxy_context_when_app_proxy_set():
    account = make_account()
    domain = make_domain()
    content = ols.render_vhost_conf(
        account, domain, suspended=False,
        app_proxy={"handler_name": "proxy_demo1_example", "port": 30000},
    )
    assert "type                    proxy" in content
    assert "handler                 proxy_demo1_example" in content
    # PHP-specific directives must not appear for a pure-proxy domain
    assert "phpIniOverride" not in content
    assert "open_basedir" not in content


def test_render_vhost_conf_suspended_wins_over_app_proxy():
    account = make_account()
    domain = make_domain()
    content = ols.render_vhost_conf(
        account, domain, suspended=True,
        app_proxy={"handler_name": "proxy_demo1_example", "port": 30000},
    )
    assert "type                    proxy" not in content
    assert "_suspended" in content


def test_render_vhost_conf_falls_back_to_domain_dict_app_proxy():
    """_apply_targets doesn't pass app_proxy as an explicit kwarg -- it
    relies on the domain dict itself (from ols._domains_as_plain) already
    carrying an "app_proxy" key, populated via ols._app_proxy_map."""
    account = make_account()
    domain = make_domain(app_proxy={"handler_name": "proxy_demo1_example", "port": 30001})
    content = ols.render_vhost_conf(account, domain, suspended=False)
    assert "handler                 proxy_demo1_example" in content


def test_render_httpd_config_includes_web_extprocessor_for_app_proxy_domain():
    domain_vhosts = [{
        "vhost_name": "demo1_example", "domain": "demo1.example", "account_home": "/home/demo1",
        "app_proxy": {"handler_name": "proxy_demo1_example", "port": 30000},
    }]
    content = ols.render_httpd_config(domain_vhosts, [])
    assert "extProcessor proxy_demo1_example{" in content
    assert "address                         127.0.0.1:30000" in content
    # "proxy", not "web" -- confirmed empirically against this server's
    # real openlitespeed binary during live E2E verification: "type web"
    # fails `openlitespeed -t` with "Unknown external processor <type>:
    # web"; `strings` on the binary lists "proxy" as the real accepted
    # keyword (daemon/ols.py's own comment in the template has the detail).
    assert "type                            proxy" in content


def test_render_httpd_config_omits_extprocessor_for_plain_domains():
    domain_vhosts = [{"vhost_name": "demo1_example", "domain": "demo1.example", "account_home": "/home/demo1", "app_proxy": None}]
    content = ols.render_httpd_config(domain_vhosts, [])
    assert "extProcessor proxy_demo1_example" not in content


def test_app_proxy_map_reflects_node_and_python_apps(isolated_db):
    from shared.db import write_session
    from shared.models import Account as AccountModel
    from shared.models import Domain as DomainModel
    from shared.models import NodeApp, PythonApp

    with write_session() as session:
        account = AccountModel(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)
        session.flush()
        session.add(DomainModel(account_id=account.id, domain="node.example", docroot="/home/demo1/node.example"))
        session.add(DomainModel(account_id=account.id, domain="py.example", docroot="/home/demo1/py.example"))
        session.add(NodeApp(account_id=account.id, domain="node.example", name="n1", entry_point="a.js", port=30000, node_version="20", env_vars=""))
        session.add(PythonApp(account_id=account.id, domain="py.example", name="p1", entry_point="app:app", app_type="asgi", port=30001, env_vars=""))

    with write_session() as session:
        mapping = ols._app_proxy_map(session)

    assert mapping["node.example"]["port"] == 30000
    assert mapping["node.example"]["kind"] == "node"
    assert mapping["py.example"]["port"] == 30001
    assert mapping["py.example"]["kind"] == "python"


def test_all_active_vhosts_includes_app_proxy_per_domain(isolated_db):
    from shared.db import write_session
    from shared.models import Account as AccountModel
    from shared.models import Domain as DomainModel
    from shared.models import NodeApp

    with write_session() as session:
        account = AccountModel(username="demo1", uid=5001, gid=5001, status="active", php_version="8.3")
        session.add(account)
        session.flush()
        session.add(DomainModel(account_id=account.id, domain="demo1.example", docroot="/home/demo1/public_html"))
        session.add(NodeApp(account_id=account.id, domain="demo1.example", name="n1", entry_point="a.js", port=30000, node_version="20", env_vars=""))

    with write_session() as session:
        domain_vhosts, _ = ols._all_active_vhosts(session)

    assert domain_vhosts[0]["app_proxy"]["port"] == 30000


# --- Phase 7a feature 4: LSCache rendering ---------------------------------


def test_render_vhost_conf_renders_lscache_override_when_set():
    account = make_account()
    domain = make_domain()
    content = ols.render_vhost_conf(
        account, domain, suspended=False,
        lscache={"ttl_seconds": 7200, "exclude_paths": ["/cart"], "storagepath": "/usr/local/lsws/cachedata/x", "purge_uri": "/.purge"},
    )
    assert "module cache {" in content
    assert "enableCache             1" in content
    assert "expireInSeconds         7200" in content
    assert "noCacheUrl              /cart" in content
    assert "storagepath             /usr/local/lsws/cachedata/x" in content


def test_render_vhost_conf_omits_lscache_block_when_not_set():
    account = make_account()
    domain = make_domain()
    content = ols.render_vhost_conf(account, domain, suspended=False)
    assert "module cache {" not in content


def test_render_vhost_conf_suspended_renders_explicit_cache_off_block():
    """QA round 2, item 8 follow-up (found live): merely OMITTING the cache
    block while suspended is not enough -- the server-level default has
    checkPublicCache/checkPrivateCache on and honors response cache-control
    headers (ignoreRespCacheCtrl 0), so a WordPress site running the
    LiteSpeed Cache plugin (which stamps public,max-age=604800) kept
    serving its cached homepage from the module's DEFAULT storage path
    through both an on-disk purge and a full lshttpd restart. A suspended
    vhost must render an explicit override that disables cache LOOKUPS."""
    account = make_account()
    domain = make_domain()
    content = ols.render_vhost_conf(
        account, domain, suspended=True,
        lscache={"ttl_seconds": 3600, "exclude_paths": [], "storagepath": "/x", "purge_uri": "/.purge"},
    )
    assert "module cache {" in content
    assert "enableCache             0" in content
    assert "checkPublicCache        0" in content
    assert "checkPrivateCache       0" in content
    assert "enablePrivateCache      0" in content
    assert "ignoreRespCacheCtrl     1" in content
    # And none of the normal LSCache-enabled parameters leak through.
    assert "enableCache             1" not in content
    assert "storagepath" not in content


def test_render_vhost_conf_suspended_cache_off_block_even_without_lscache_config():
    """The plugin-driven caching this guards against never depended on a
    per-domain LscacheSettings row existing at all -- the off-block must
    render for EVERY suspended vhost, not just LSCache-enabled ones."""
    account = make_account()
    domain = make_domain()
    content = ols.render_vhost_conf(account, domain, suspended=True, lscache=None)
    assert "module cache {" in content
    assert "checkPublicCache        0" in content


# --- Phase 7a feature 6: per-domain PHP version override -------------------


def test_render_vhost_conf_uses_domain_php_override_when_set():
    account = make_account(php_version="8.3")
    domain = make_domain(php_version="8.1")
    content = ols.render_vhost_conf(account, domain, suspended=False)
    assert "lsapi:demo1_php81 php" in content
    assert "lsapi:demo1_php83 php" not in content


def test_render_vhost_conf_falls_back_to_account_version_when_no_override():
    account = make_account(php_version="8.3")
    domain = make_domain(php_version=None)
    content = ols.render_vhost_conf(account, domain, suspended=False)
    assert "lsapi:demo1_php83 php" in content


def test_all_active_vhosts_declares_separate_extprocessor_per_effective_version(isolated_db):
    from shared.db import write_session
    from shared.models import Account as AccountModel
    from shared.models import Domain as DomainModel

    with write_session() as session:
        account = AccountModel(username="demo1", uid=5001, gid=5001, status="active", php_version="8.3")
        session.add(account)
        session.flush()
        session.add(DomainModel(account_id=account.id, domain="a.example", docroot="/home/demo1/a", php_version=None))
        session.add(DomainModel(account_id=account.id, domain="b.example", docroot="/home/demo1/b", php_version="8.1"))
        session.add(DomainModel(account_id=account.id, domain="c.example", docroot="/home/demo1/c", php_version="8.1"))

    with write_session() as session:
        _domain_vhosts, account_procs = ols._all_active_vhosts(session)

    php_app_names = sorted(p["php_app_name"] for p in account_procs)
    assert php_app_names == ["demo1_php81", "demo1_php83"]  # one per DISTINCT effective version, not per domain


def test_www_alias_mapping_preserves_explicit_hosts(isolated_db):
    from shared.db import write_session
    from shared.models import Domain
    with write_session() as s:
        a=Account(username='alice',status='active');s.add(a);s.flush()
        for name in ('alice.example','www.alice.example','other.example'):
            s.add(Domain(account_id=a.id,domain=name,kind='addon',docroot='/home/alice/'+name))
    with write_session() as s:
        hosts,_=ols._all_active_vhosts(s)
    assert not next(h for h in hosts if h['domain']=='alice.example')['www_alias']
    assert not next(h for h in hosts if h['domain']=='www.alice.example')['www_alias']
    assert next(h for h in hosts if h['domain']=='other.example')['www_alias']
