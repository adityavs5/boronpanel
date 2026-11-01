import importlib.util
import re
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
spec = importlib.util.spec_from_file_location("nginx_include", DEPLOY / "nginx_include.py")
nginx_include = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nginx_include)

SITE = """\
# Boron distribution host
server {
    listen 80; listen [::]:80;
    server_name download.boronpanel.com license.boronpanel.com;
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl http2;  listen [::]:443 ssl http2;
    server_name download.boronpanel.com;
    root /srv/download;
    add_header X-Note "braces { in strings } are ignored";
    location = /install.sh { try_files $uri =404; }
    location /channels/ { try_files $uri =404; }   # { comment brace
    location / { return 404; }
}

include /etc/nginx/boron/license.conf;
"""


def test_include_is_inserted_once_in_the_download_tls_block():
    updated, changed = nginx_include.insert_include(SITE)
    assert changed
    lines = updated.splitlines()
    at = lines.index("    include snippets/boron-download-protected.conf;")
    assert lines[at + 1] == "    location / { return 404; }"
    # The port-80 block has its own "location /" and is left alone.
    assert updated.count(nginx_include.INCLUDE) == 1
    assert nginx_include.insert_include(updated) == (updated, False)


@pytest.mark.parametrize("site", [
    SITE.replace("location / { return 404; }", "location / { deny all; }"),
    SITE.replace("server_name download.boronpanel.com;", "server_name other.example;"),
    SITE + SITE[SITE.index("server {\n    listen 443"):],
    SITE.replace("location / { return 404; }\n}", "location / { return 404; }\n"),
])
def test_unexpected_layouts_are_not_changed(site):
    with pytest.raises(nginx_include.NotChanged):
        nginx_include.insert_include(site)


def test_cli_rewrites_atomically_and_keeps_a_backup(tmp_path, capsys):
    site = tmp_path / "boron-dist.conf"
    site.write_text(SITE)
    site.chmod(0o640)
    assert nginx_include.main(["x", str(site)]) == 0
    assert nginx_include.INCLUDE in site.read_text()
    assert site.stat().st_mode & 0o777 == 0o640
    assert (tmp_path / "boron-dist.conf.bak-boron").read_text() == SITE
    assert nginx_include.main(["x", str(site)]) == 1
    site.write_text("server { listen 443; server_name download.boronpanel.com; }\n")
    assert nginx_include.main(["x", str(site)]) == 2
    assert "not changed" in capsys.readouterr().err
    assert sorted(p.name for p in tmp_path.iterdir()) == ["boron-dist.conf", "boron-dist.conf.bak-boron"]


def test_protected_snippet_matches_the_link_format():
    snippet = (DEPLOY / "nginx-download-protected.conf").read_text()
    assert re.search(r"^location \^~ /protected/ \{", snippet, re.MULTILINE)
    assert "secure_link $arg_md5,$arg_expires;" in snippet
    assert 'secure_link_md5 "$secure_link_expires$uri$remote_addr __DOWNLOAD_SECRET__";' in snippet
    assert 'if ($secure_link = "") { return 403; }' in snippet
    assert 'if ($secure_link = "0") { return 410; }' in snippet
    assert "root /srv/download;" in snippet and "autoindex off;" in snippet
    assert "limit_except GET { deny all; }" in snippet
    assert 'add_header Cache-Control "private, no-store" always;' in snippet
    assert snippet.count("{") == snippet.count("}")


def test_install_script_wires_the_download_secret():
    script = (DEPLOY / "install_server.sh").read_text()
    assert "BORON_LICENSE_DOWNLOAD_SECRET" in script and "token_urlsafe(48)" in script
    assert "/etc/nginx/snippets/boron-download-protected.conf" in script
    assert "chmod 0600" in script and "nginx -t" in script
    assert "deploy/nginx_include.py" in script and "/etc/nginx/sites-available/boron-dist.conf" in script
    assert "/srv/download/protected/releases" in script and "/srv/download/protected/channels" in script


def test_install_script_keeps_the_download_secret_out_of_argv():
    # Only builtins may see the expanded secret; /proc/<pid>/cmdline is world-readable.
    script = (DEPLOY / "install_server.sh").read_text()
    uses = [line.strip() for line in script.splitlines() if "$download_secret" in line]
    assert uses
    for line in uses:
        assert line.startswith(("[[ ", "printf ")), line


def test_install_script_renders_the_snippet(tmp_path):
    import shutil
    import subprocess
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    script = (DEPLOY / "install_server.sh").read_text().splitlines()
    start = next(i for i, line in enumerate(script) if line.startswith("snippet_template="))
    render = "\n".join(script[start - 1:start + 3])
    secret = "Ab_-" + "x" * 44
    out = tmp_path / "snippet.conf"
    subprocess.run([bash, "-c", f'set -euo pipefail\nsrc="$1"\nsnippet_tmp="$2"\ndownload_secret="$3"\n{render}',
                    "render", str(DEPLOY.parent), str(out), secret], check=True)
    template = (DEPLOY / "nginx-download-protected.conf").read_text()
    assert out.read_text() == template.replace("__DOWNLOAD_SECRET__", secret)
