from __future__ import annotations

import pytest

from api import ratelimit
from api.ratelimit import SlidingWindowLimiter, classify, credential_fingerprint, evaluate


@pytest.fixture(autouse=True)
def fresh_limiter(monkeypatch):
    monkeypatch.setattr(ratelimit, "limiter", SlidingWindowLimiter())


# --- limiter core ------------------------------------------------------------


def test_sliding_window_admits_up_to_limit():
    lim = SlidingWindowLimiter()
    for i in range(10):
        assert lim.check("k", 10, 300, now=100.0 + i) is None
    retry = lim.check("k", 10, 300, now=110.0)
    assert retry is not None
    # Oldest was at t=100, window 300 -> free again at 400 -> retry ~290s.
    assert retry == pytest.approx(290.0, abs=0.01)


def test_sliding_window_slides_not_fixed():
    lim = SlidingWindowLimiter()
    for i in range(10):
        assert lim.check("k", 10, 300, now=100.0 + i) is None
    # At t=400.5 only the t=100 stamp has aged out (cutoff 100.5): exactly
    # one slot frees, is consumed, and the very next request is rejected
    # again -- sliding behavior, not a fixed-window reset.
    assert lim.check("k", 10, 300, now=400.5) is None
    assert lim.check("k", 10, 300, now=400.6) is not None


def test_buckets_are_independent_per_key():
    lim = SlidingWindowLimiter()
    for i in range(10):
        assert lim.check("a", 10, 300, now=100.0 + i) is None
    assert lim.check("a", 10, 300, now=110.0) is not None
    assert lim.check("b", 10, 300, now=110.0) is None


def test_sweep_drops_idle_keys():
    lim = SlidingWindowLimiter()
    lim.check("old", 10, 300, now=100.0)
    lim._sweep(now=100.0 + 3601)
    assert "old" not in lim._buckets


# --- classification ----------------------------------------------------------


def test_classify_login_and_reset_paths():
    assert classify("/login", "POST", has_credential=False)[0] == "login"
    assert classify("/login/2fa", "POST", has_credential=True)[0] == "login"
    assert classify("/change-password", "POST", has_credential=True)[0] == "password_reset"
    # GET /login (the redirect stub) is not an attempt.
    assert classify("/login", "GET", has_credential=False)[0] == "unauthed"


def test_classify_authed_vs_unauthed():
    assert classify("/api/v1/accounts", "GET", has_credential=True)[0] == "authed"
    assert classify("/api/v1/branding", "GET", has_credential=False)[0] == "unauthed"


def test_classify_exemptions():
    assert classify("/healthz", "GET", has_credential=False) is None
    assert classify("/static/dist/assets/app.js", "GET", has_credential=False) is None


def test_credential_fingerprint():
    assert credential_fingerprint(None, None) is None
    a = credential_fingerprint("fh_session=cookie-value", None)
    b = credential_fingerprint("fh_session=other-cookie", None)
    assert a and b and a != b
    assert credential_fingerprint("fh_session=cookie-value", None) == a


def test_only_real_credentials_count():
    """Arbitrary non-panel cookies must NOT buy the authed tier -- that
    would let any client escape the 30/min unauthed pool by sending
    `Cookie: junk=1`."""
    assert credential_fingerprint("junk=1; theme=dark", None) is None
    assert credential_fingerprint("fh_session=", None) is None  # empty value
    assert credential_fingerprint(None, "Basic dXNlcjpwYXNz") is None
    assert credential_fingerprint(None, "Bearer fh_admin_abc123") is not None
    # fh_session found among unrelated cookies, any order/spacing.
    assert credential_fingerprint("a=1;  fh_session=abc; b=2", None) == credential_fingerprint(
        "fh_session=abc", None
    )


# --- evaluate (end-to-end decision) -------------------------------------------


def test_login_limit_11th_attempt_rejected():
    for _ in range(10):
        d = evaluate("/login", "POST", "203.0.113.5", None, None)
        assert d.allowed
    d = evaluate("/login", "POST", "203.0.113.5", None, None)
    assert not d.allowed
    assert d.tier == "login"
    assert d.retry_after > 0


def test_login_limit_is_per_ip():
    for _ in range(10):
        assert evaluate("/login", "POST", "203.0.113.5", None, None).allowed
    assert not evaluate("/login", "POST", "203.0.113.5", None, None).allowed
    assert evaluate("/login", "POST", "203.0.113.6", None, None).allowed


def test_login_limit_ipv6_same_64_prefix_shares_bucket():
    # Audit 3 (Area 11): a single attacker with a routed IPv6 /64 (a normal
    # ISP allocation, not a botnet) could otherwise mint a fresh 10-attempt
    # bucket on every request just by incrementing the host portion of the
    # address -- confirm two distinct addresses in the same /64 share one
    # bucket.
    for i in range(10):
        addr = f"2001:db8:1234:5678::{i:x}"
        assert evaluate("/login", "POST", addr, None, None).allowed
    d = evaluate("/login", "POST", "2001:db8:1234:5678::ffff", None, None)
    assert not d.allowed, "a new address within the same /64 must still be capped"


def test_login_limit_ipv6_different_64_prefix_is_independent():
    for _ in range(10):
        assert evaluate("/login", "POST", "2001:db8:1111::1", None, None).allowed
    assert not evaluate("/login", "POST", "2001:db8:1111::2", None, None).allowed
    # A genuinely different /64 prefix is a different (legitimate) client.
    assert evaluate("/login", "POST", "2001:db8:2222::1", None, None).allowed


def test_login_limit_applies_even_with_session_cookie():
    """A valid session must not buy extra brute-force attempts."""
    for _ in range(10):
        assert evaluate("/login", "POST", "203.0.113.5", "some-cookie", None).allowed
    assert not evaluate("/login", "POST", "203.0.113.5", "other-cookie", None).allowed


def test_password_reset_limit_5_per_hour():
    for _ in range(5):
        assert evaluate("/change-password", "POST", "203.0.113.5", "fh_session=c", None).allowed
    d = evaluate("/change-password", "POST", "203.0.113.5", "fh_session=c", None)
    assert not d.allowed
    assert d.tier == "password_reset"


def test_unauthed_30_per_minute_per_ip():
    for _ in range(30):
        assert evaluate("/api/v1/branding", "GET", "203.0.113.9", None, None).allowed
    d = evaluate("/api/v1/branding", "GET", "203.0.113.9", None, None)
    assert not d.allowed
    assert d.tier == "unauthed"


def test_authed_keyed_by_credential_across_ips():
    """The per-credential bucket accumulates across IPs: 150 requests from
    each of two IPs exhausts one session's 300/min even though neither
    IP's own backstop is anywhere near full."""
    for _ in range(150):
        assert evaluate("/api/v1/accounts", "GET", "203.0.113.9", "fh_session=sess-A", None).allowed
        assert evaluate("/api/v1/accounts", "GET", "203.0.113.10", "fh_session=sess-A", None).allowed
    d = evaluate("/api/v1/accounts", "GET", "203.0.113.11", "fh_session=sess-A", None)
    assert not d.allowed
    assert d.tier == "authed"
    # A different credential (on yet another IP) still has its own budget.
    assert evaluate("/api/v1/accounts", "GET", "203.0.113.12", "fh_session=sess-B", None).allowed


def test_rotating_credentials_capped_by_ip_backstop():
    """The bypass this design must close: minting a fresh (unverified)
    credential per request must not evade limiting -- the per-IP backstop
    catches it at the same 300/min."""
    for i in range(300):
        assert evaluate("/api/v1/accounts", "GET", "203.0.113.66", f"fh_session=rot-{i}", None).allowed
    d = evaluate("/api/v1/accounts", "GET", "203.0.113.66", "fh_session=rot-300", None)
    assert not d.allowed
    assert d.tier == "authed"
    assert d.retry_after > 0


def test_junk_cookie_lands_in_unauthed_tier():
    for _ in range(30):
        assert evaluate("/api/v1/branding", "GET", "203.0.113.77", "junk=1", None).allowed
    d = evaluate("/api/v1/branding", "GET", "203.0.113.77", "junk=1", None)
    assert not d.allowed
    assert d.tier == "unauthed"


def test_exempt_paths_never_limited():
    for _ in range(100):
        assert evaluate("/healthz", "GET", "203.0.113.9", None, None).allowed


def test_rejection_logged(caplog):
    import logging

    for _ in range(10):
        evaluate("/login", "POST", "203.0.113.5", None, None)
    with caplog.at_level(logging.WARNING, logger="forgehost.ratelimit"):
        evaluate("/login", "POST", "203.0.113.5", None, None)
    assert any("rate limit hit" in r.message for r in caplog.records)


# --- middleware wiring (real ASGI app) ----------------------------------------


def test_middleware_returns_429_with_retry_after(isolated_db):
    # isolated_db: the IP-whitelist middleware and the login handler both
    # touch the control-plane DB on this request path -- point them at a
    # throwaway file, never the live one.
    import warnings

    warnings.filterwarnings("ignore")
    from fastapi.testclient import TestClient

    import api.main as main

    client = TestClient(main.app)
    last = None
    for _ in range(11):
        last = client.post("/login", data={"username": "x", "password": "y"})
    assert last.status_code == 429
    assert "retry-after" in last.headers
    assert int(last.headers["retry-after"]) >= 1
    # Rejections still carry the security headers (the headers middleware
    # wraps the rate limiter, not the other way around).
    assert last.headers.get("x-content-type-options") == "nosniff"
