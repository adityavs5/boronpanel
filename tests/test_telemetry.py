from shared import telemetry


def test_before_send_removes_request_and_identity_secrets():
    event = {
        "server_name": "private-host",
        "user": {"username": "customer"},
        "request": {
            "url": "https://panel.test/api/users/customer?token=secret",
            "query_string": "token=secret",
            "cookies": {"fh_session": "secret"},
            "data": {"password": "secret"},
            "headers": {
                "Authorization": "Bearer secret",
                "Cookie": "fh_session=secret",
                "User-Agent": "browser",
                "Content-Type": "application/json",
                "X-Tenant": "customer",
            },
        },
        "contexts": {"custom": {"api_token": "secret", "safe": "ok"}},
    }
    scrubbed = telemetry.before_send(event)
    assert "server_name" not in scrubbed and "user" not in scrubbed
    assert "url" not in scrubbed["request"]
    assert set(scrubbed["request"]["headers"]) == {"User-Agent", "Content-Type"}
    assert scrubbed["contexts"]["custom"] == {"api_token": "[Filtered]", "safe": "ok"}
    assert "secret" not in repr(scrubbed)


def test_initialize_is_noop_without_operator_dsn(monkeypatch):
    monkeypatch.setitem(telemetry.settings.secrets, "SENTRY_DSN", "")
    assert telemetry.initialize("api") is False
