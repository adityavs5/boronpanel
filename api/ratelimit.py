"""Run A feature 6: in-memory sliding-window rate limiting.

Pure-Python, no Redis (goal's explicit constraint): forgehost-api is a
single uvicorn process (deploy/forgehost-api.service runs no --workers
fan-out), so one in-process store IS the global view. Exact sliding
window -- a per-key deque of request timestamps, pruned on every check --
not a fixed-window approximation: the goal names specific windows
("10/5min") and a fixed window would admit up to 2x the limit across a
boundary.

Tiers (most-specific wins; a request is charged against exactly one
bucket):
  - login attempts (POST /login, /login/2fa):      10 / 5 min / IP
  - password reset (POST /change-password):         5 / 1 h  / IP
  - authenticated (session cookie or bearer):     300 / 1 min / credential
  - unauthenticated:                               30 / 1 min / IP

"Per user" for the authed tier is keyed on a hash of the presented
credential (session cookie / bearer value), NOT a DB-resolved user id --
the middleware runs before auth dependencies, and hashing the credential
gives a stable per-session key with zero DB reads on the hot path. Two
hardenings on top of that, both closing the same bypass class (a client
manufacturing its own key material to escape the per-IP pool):

  - Only the panel's actual credential carriers count as "authed": an
    fh_session cookie (api.security.COOKIE_NAME) or a Bearer
    Authorization header. Any other cookie is ignored -- otherwise
    `Cookie: junk=1` would buy the 300/min tier instead of 30/min.
  - The authed tier ALSO charges a per-IP backstop bucket at the same
    300/min. Without it, rotating the (unverified -- verifying would
    cost the DB/crypto work this path exists to avoid) credential value
    on every request would mint a fresh bucket each time and never be
    limited at all. The backstop is checked first, so a rotation flood
    fills one IP bucket rather than minting unbounded credential
    buckets. Effective authed limit: min(300/min/credential across IPs,
    300/min/IP across credentials).

Every rejection is logged (goal: "log all hits") with tier, key, IP and
path; the 429 carries Retry-After per RFC 6585.
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass

from api.security import COOKIE_NAME

logger = logging.getLogger("forgehost.ratelimit")

# (limit, window_seconds) per tier.
LOGIN_LIMIT = (10, 5 * 60)
PASSWORD_RESET_LIMIT = (5, 60 * 60)
AUTHED_LIMIT = (300, 60)
UNAUTHED_LIMIT = (30, 60)

LOGIN_PATHS = {"/login", "/login/2fa"}
PASSWORD_RESET_PATHS = {"/change-password"}

# No rate limiting at all for these prefixes: /healthz is what external
# monitoring polls (same exemption reasoning as the IP whitelist's), and
# /static is the SPA's own hashed asset chunks -- one login-page load
# fetches a dozen of them, which would eat the whole unauthed budget for
# no abuse-prevention gain (the HTML/API requests they hang off are still
# limited).
EXEMPT_PREFIXES = ("/healthz", "/static/")


class SlidingWindowLimiter:
    """Thread-safe exact sliding window. check() either admits the request
    (records its timestamp, returns None) or rejects it (returns the
    seconds until the oldest in-window timestamp expires -- the
    Retry-After value)."""

    # Opportunistic stale-key sweep cadence (checks between sweeps) -- keeps
    # the bucket dict from growing unboundedly with one-off IPs.
    SWEEP_EVERY = 4096

    def __init__(self):
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._checks_since_sweep = 0

    def check(self, key: str, limit: int, window_seconds: float, now: float | None = None) -> float | None:
        now = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = self._buckets[key] = deque()
            cutoff = now - window_seconds
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = bucket[0] + window_seconds - now
                return max(retry_after, 0.001)
            bucket.append(now)

            self._checks_since_sweep += 1
            if self._checks_since_sweep >= self.SWEEP_EVERY:
                self._checks_since_sweep = 0
                self._sweep(now)
            return None

    def _sweep(self, now: float) -> None:
        # Longest window in use is the password-reset hour; anything idle
        # past that can never influence a future check.
        horizon = now - max(LOGIN_LIMIT[1], PASSWORD_RESET_LIMIT[1], AUTHED_LIMIT[1], UNAUTHED_LIMIT[1])
        dead = [k for k, dq in self._buckets.items() if not dq or dq[-1] <= horizon]
        for k in dead:
            del self._buckets[k]


@dataclass
class Decision:
    allowed: bool
    tier: str = ""
    retry_after: float = 0.0


def classify(path: str, method: str, has_credential: bool) -> tuple[str, int, float] | None:
    """Which (tier, limit, window) applies -- None means exempt."""
    for prefix in EXEMPT_PREFIXES:
        if path.startswith(prefix) or path == prefix.rstrip("/"):
            return None
    if method == "POST" and path in LOGIN_PATHS:
        return ("login", *LOGIN_LIMIT)
    if method == "POST" and path in PASSWORD_RESET_PATHS:
        return ("password_reset", *PASSWORD_RESET_LIMIT)
    if has_credential:
        return ("authed", *AUTHED_LIMIT)
    return ("unauthed", *UNAUTHED_LIMIT)


def _session_cookie_value(cookie_header: str | None) -> str | None:
    """The fh_session value out of a raw Cookie header, or None. A tiny
    hand parse (split on ';') rather than http.cookies -- the header is
    attacker-supplied and SimpleCookie's lenient parsing has a history of
    surprises; we only ever need one well-known key's raw value."""
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name.strip() == COOKIE_NAME and value:
            return value
    return None


def credential_fingerprint(cookie: str | None, authorization: str | None) -> str | None:
    """Stable per-credential key with no DB read: hash the panel credential
    the client presented (fh_session cookie value or Bearer token). Returns
    None -- i.e. the unauthed per-IP tier -- for anything else; arbitrary
    non-credential cookies must not buy the bigger authed budget."""
    material = _session_cookie_value(cookie)
    if material is None and authorization:
        scheme, _, token = authorization.strip().partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            material = token.strip()
    if not material:
        return None
    return hashlib.sha256(material.encode()).hexdigest()[:16]


limiter = SlidingWindowLimiter()


def _ip_bucket(ip: str | None) -> str:
    """Audit 3 (Area 11) finding: keying strictly on the literal address let
    an attacker with a routed IPv6 prefix -- a normal residential/mobile ISP
    allocation, not a botnet -- trivially rotate addresses to defeat the
    per-IP login/unauthenticated limiters (confirmed: each new address in
    the prefix started a brand-new bucket). Normalize IPv6 addresses to
    their /64 network (the standard ISP-routed allocation unit) so one
    client maps to one bucket regardless of which address in its prefix it
    uses; IPv4 is left as a literal address (NAT already aggregates most
    residential IPv4 to one address per household, and /64-style
    aggregation has no IPv4 analog at that granularity)."""
    if not ip:
        return "unknown"
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return ip


def evaluate(path: str, method: str, client_ip: str | None, cookie: str | None, authorization: str | None) -> Decision:
    """The whole per-request decision, pure enough to unit-test without an
    ASGI app (same extraction pattern as api/main.py's ip_allowed)."""
    fingerprint = credential_fingerprint(cookie, authorization)
    rule = classify(path, method, fingerprint is not None)
    if rule is None:
        return Decision(allowed=True, tier="exempt")
    tier, limit, window = rule
    ip_bucket = _ip_bucket(client_ip)
    # login/password-reset are per-IP regardless of any credential (a valid
    # session shouldn't buy extra brute-force attempts against /login).
    if tier in ("login", "password_reset") or fingerprint is None:
        key = f"{tier}:ip:{ip_bucket}"
        retry_after = limiter.check(key, limit, window)
    else:
        # Per-IP backstop first (see module docstring): a credential-
        # rotation flood saturates this one bucket instead of minting a
        # fresh per-credential bucket on every request.
        key = f"{tier}:ip:{ip_bucket}"
        retry_after = limiter.check(key, limit, window)
        if retry_after is None:
            key = f"{tier}:cred:{fingerprint}"
            retry_after = limiter.check(key, limit, window)
    if retry_after is None:
        return Decision(allowed=True, tier=tier)
    logger.warning(
        "rate limit hit: tier=%s key=%s ip=%s path=%s retry_after=%.1fs",
        tier, key, client_ip, path, retry_after,
    )
    return Decision(allowed=False, tier=tier, retry_after=retry_after)
