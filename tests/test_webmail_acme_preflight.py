"""The webmail ACME preflight must write a probe the web server can read."""
import os
import stat

import pytest

from daemon import ssl
from shared.config import settings
from shared.validation import ValidationError


class _Response:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self, _limit):
        return self._body.encode()


def test_probe_is_readable_by_other_users_under_the_daemon_umask(tmp_path, monkeypatch):
    docroot = tmp_path / "webmail"
    docroot.mkdir()
    monkeypatch.setattr(settings, "webmail_hostname", "webmail.example.test")
    monkeypatch.setattr(settings, "webmail_docroot", str(docroot))
    seen = {}

    class Connection:
        def __init__(self, address, port, timeout):
            pass

        def request(self, method, path, headers):
            probe = docroot / path.lstrip("/")
            seen["modes"] = [stat.S_IMODE(os.stat(p).st_mode) for p in (probe.parent.parent, probe.parent, probe)]
            seen["token"] = probe.name

        def getresponse(self):
            # OpenLiteSpeed runs as nobody: it serves the file only if others can read it.
            readable = all(mode & 0o004 for mode in seen["modes"])
            return _Response(200 if readable else 403, seen["token"] if readable else "")

        def close(self):
            pass

    monkeypatch.setattr(ssl.http.client, "HTTPConnection", Connection)
    previous = os.umask(0o027)
    try:
        result = ssl._http_challenge_preflight("webmail.example.test", ["192.0.2.10"])
    finally:
        os.umask(previous)
    assert result["ok"] is True
    assert seen["modes"] == [0o755, 0o755, 0o644]
    assert not list((docroot / ".well-known" / "acme-challenge").iterdir())


def test_failed_webmail_preflight_is_a_user_error(monkeypatch):
    monkeypatch.setattr(settings, "letsencrypt_email", "admin@example.test")
    monkeypatch.setattr(settings, "webmail_hostname", "webmail.example.test")
    monkeypatch.setattr(ssl, "service_certificate_diagnostics", lambda params: {
        "dns": {"ok": True, "resolved": ["192.0.2.10"]},
        "http_challenge": {"ok": False, "detail": "ACME challenge file was not served by the webmail vhost"},
    })
    with pytest.raises(ValidationError, match="not served by the webmail vhost"):
        ssl.issue_certificate({"domain": "webmail.example.test"})
