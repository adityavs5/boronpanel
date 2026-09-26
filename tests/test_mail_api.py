from fastapi import HTTPException, Response
from starlette.requests import Request

from api.routers import mail as mail_router
from api.security import Identity
from shared.db import write_session
from shared.models import Account, Domain


def _request(origin: str = "https://panel.example.test:2222") -> Request:
    return Request({
        "type": "http", "http_version": "1.1", "method": "POST",
        "scheme": "https", "path": "/webmail-session", "raw_path": b"/webmail-session",
        "query_string": b"", "server": ("panel.example.test", 2222),
        "client": ("192.0.2.8", 45678),
        "headers": [
            (b"host", b"panel.example.test:2222"),
            (b"origin", origin.encode()),
        ],
    })


def _fixture_account():
    with write_session() as db:
        account = Account(username="mailone", status="active")
        db.add(account)
        db.flush()
        db.add(Domain(
            account_id=account.id, domain="example.test", kind="primary",
            docroot="/home/mailone/public_html",
        ))
        return account.id


def test_webmail_session_requires_same_origin_direct_customer_session(isolated_db, monkeypatch):
    account_id = _fixture_account()
    calls = []
    monkeypatch.setattr(
        mail_router, "call_daemon",
        lambda op, identity, **params: calls.append((op, identity, params)) or {
            "token": "opaque-launch-token", "url": "https://webmail.example.test",
        },
    )
    identity = Identity(7, "mail-login", "customer", account_id, "session")
    response = Response()
    result = mail_router.create_webmail_session(
        "mailone", "hello", mail_router.WebmailSessionBody(domain="example.test"),
        _request(), response, identity,
    )
    assert result["token"] == "opaque-launch-token"
    assert calls[0][0] == "webmail.launch.create"
    assert calls[0][2] == {"username": "mailone", "mailbox": "hello@example.test"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"

    denied = [
        Identity(7, "mail-token", "customer", account_id, "token"),
        Identity(1, "administrator", "admin", None, "session"),
        Identity(1, "administrator", "customer", account_id, "session", impersonator="administrator"),
    ]
    for other in denied:
        try:
            mail_router.create_webmail_session(
                "mailone", "hello", mail_router.WebmailSessionBody(domain="example.test"),
                _request(), Response(), other,
            )
        except HTTPException as exc:
            assert exc.status_code == 403
        else:
            raise AssertionError("non-direct customer identity created a webmail launch")


def test_webmail_session_rejects_cross_origin_before_rpc(isolated_db, monkeypatch):
    account_id = _fixture_account()
    called = []
    monkeypatch.setattr(mail_router, "call_daemon", lambda *_args, **_kwargs: called.append(True))
    identity = Identity(7, "mail-login", "customer", account_id, "session")
    try:
        mail_router.create_webmail_session(
            "mailone", "hello", mail_router.WebmailSessionBody(domain="example.test"),
            _request("https://attacker.example"), Response(), identity,
        )
    except HTTPException as exc:
        assert exc.status_code == 403
        assert "same-origin" in exc.detail
    else:
        raise AssertionError("cross-origin launch was accepted")
    assert called == []
