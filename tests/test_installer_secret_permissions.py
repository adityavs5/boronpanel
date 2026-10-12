from pathlib import Path


def test_roundcube_config_is_published_after_broad_permission_passes():
    source = (Path(__file__).resolve().parent.parent / "scripts" / "install.sh").read_text()
    start = source.index("setup_webmail()")
    end = source.index("# --- 6. systemd services", start)
    function = source[start:end]

    temp_create = function.index('config_tmp="$(mktemp /var/www/roundcube/config/.config.inc.php.XXXXXX)"')
    secret_write = function.index('cat >"$config_tmp" <<EOF')
    broad_chown = function.index("find /var/www/roundcube -type f")
    publish = function.index('mv -f "$config_tmp" /var/www/roundcube/config/config.inc.php')

    assert temp_create < secret_write < broad_chown < publish
    assert "! -path '/var/www/roundcube/config/config.inc.php'" in function
    assert "! -name '.config.inc.php.*'" in function
    assert 'chown root:www-data "$config_tmp"' in function
    assert 'chmod 0640 "$config_tmp"' in function
