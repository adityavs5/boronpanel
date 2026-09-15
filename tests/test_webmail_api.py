from api.routers import mail
from api.security import Identity


CUSTOMER = Identity(2, "customer", "customer", 7, "session")


def test_authenticated_user_receives_public_webmail_url(monkeypatch):
    monkeypatch.setattr(mail.settings, "webmail_url", "https://webmail.example.test")
    assert mail.webmail_settings(CUSTOMER) == {
        "enabled": True,
        "url": "https://webmail.example.test",
    }


def test_webmail_endpoint_reports_disabled_without_a_url(monkeypatch):
    monkeypatch.setattr(mail.settings, "webmail_url", "")
    assert mail.webmail_settings(CUSTOMER) == {"enabled": False, "url": ""}
