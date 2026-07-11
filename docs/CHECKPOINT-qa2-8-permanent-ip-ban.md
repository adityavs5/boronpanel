# QA round 2 — Item 14: permanent, server-wide IP/CIDR block

## What existed already (per investigation)

Two related-but-distinct mechanisms, neither of which is this feature:
`daemon/handlers_ipblock.py` blocks an IP **per domain** (rendered into
that one domain's own OLS vhost `accessControl` block — a different
domain, or a request to the panel/mail/FTP itself, is unaffected).
`daemon/fail2ban.py` bans automatically and **temporarily** (`bantime`);
its only manual action is *unban* (`unban_ip`/`unban_all_in_jail`) — there
is no way to manually/permanently ban an IP through it.
`daemon/firewall.py`'s generic UFW rule tool is fundamentally **port-
scoped** (`add_rule` requires a `port`) — there is no "deny every port
from this IP" shape available through it.

## Fix

New `PermanentIpBan` model (`shared/models.py`) + `daemon/ipban.py`:
`ban_ip`/`unban_ip`/`list_bans`. Enforcement is a genuine `ufw insert 1
deny from <ip>` — blocks every port/protocol from that source, not one
service — inserted at position 1 specifically so it's evaluated *before*
any earlier, broader allow rule (UFW is first-match-wins, top-to-bottom;
appending to the end could leave an existing `allow 80` matching first and
the ban silently never taking effect for that port — the same ordering
lesson Security Audit 3's A3-7 finding already taught this project for a
different iptables rule set). The DB row exists because UFW itself has no
field for "why" — `reason` and `banned_by` (the acting admin, following
`daemon/handlers_notes.py`'s exact established pattern: passed explicitly
from the API router as `actor=identity.username`, never read from the
auto-stripped `_actor` RPC-framing field individual handlers never see) are
metadata layered on top of the real UFW rule.

Guards against the two ways this specific feature could turn into a
self-inflicted outage: refuses `0.0.0.0/0`/`::/0` (would firewall off the
entire server) and refuses loopback addresses (would break the panel's own
loopback-bound services — FileBrowser Quantum on `127.0.0.1:8088`, the
install runbook's own healthz check). Private ranges are deliberately
*not* blocked — an admin may legitimately want to ban a compromised
internal service, and there's no way to distinguish "legitimate" from
"self-lockout" for those short of the two hard-coded cases above.

API: `api/routers/ipban.py`, `GET/POST /api/v1/admin/ip-bans`,
`DELETE /api/v1/admin/ip-bans/{id}`, admin-only. Frontend:
`frontend/src/pages/admin/IpBans.jsx` (list/add/remove, reason field),
new sidebar entry "IP Bans" right after "Firewall".

## A real bug caught while writing this (not left in)

Initially reused `shared.validation.validate_username` to validate the
`actor` field (the admin's own panel login) — wrong: that validator
enforces the *hosting-account* username regex
(`^[a-z][a-z0-9]{0,15}$`), which admin panel logins have no obligation to
follow (mixed case, longer, etc. are all valid admin usernames). Would
have rejected a perfectly normal admin login as an "invalid username" the
first time one didn't happen to match hosting-account naming rules.
Replaced with a dedicated `_validate_actor` (free-text, length-capped,
single-line) — caught by writing
`test_actor_recorded_as_free_text_not_hosting_username` before shipping,
not found live.

## Tests

`tests/test_ipban.py` (new, 14 tests): UFW insert-at-position-1 command
shape, CIDR acceptance, invalid-value rejection (no UFW call made),
duplicate-ban rejection, `0.0.0.0/0`/`::/0` refusal, loopback refusal
(both IPv4 forms + `::1`), private ranges allowed, non-hosting-username
actor accepted, list/unban round-trip, unban-nonexistent-id error.

`python3 -m pytest tests/test_ipban.py tests/test_firewall.py tests/test_fail2ban.py tests/test_apidocs.py -q`
→ 57 passed. Route registration double-checked via `TestClient` (401 =
registered+auth-gated vs. 404 = missing) since this FastAPI version's
`app.routes` doesn't flatten to inspectable paths directly. `npm run
build` — clean, new `IpBans` chunk confirmed present.

## What's still open

Live UFW verification (rule actually blocks a real connection, survives a
UFW reset/reload) deferred to the operator post-deploy, same reasoning as
every other live-firewall-mutation item in this batch — this box's UFW
was already touched live for item 7 (FTP) under explicit authorization for
that specific, narrower change; a new live IP-ban test would ban a real
address from this shared box, which needs its own explicit go-ahead rather
than folding it into a different item's authorization.
