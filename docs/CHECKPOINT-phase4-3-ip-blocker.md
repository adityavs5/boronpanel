# Checkpoint: Phase 4 feature 3 — IP blocker

## What was built

- **`Domain.ip_block_list`** (JSON list, up to 200 entries, each validated
  through Python's own `ipaddress` module rather than a hand-rolled
  regex — it already correctly rejects the input classes that matter
  here, malformed octets/out-of-range prefixes/trailing garbage, and
  normalizes netmask-style CIDR (`/255.255.255.0`) to prefix-length form
  (`/24`) for free).
- **Rendered into OLS's own native per-vhost `accessControl` block**
  (found by reading this server's actual installed OLS docs/shipped
  templates, `VHSecurity_Help.html` + `conf/templates/ccl.conf`, rather
  than assumed) — `allow *` / `deny <comma-list>`, applied inside
  `context / { }`, **regardless of suspended state** (a blocked IP
  shouldn't see the suspended page either — the one place this feature's
  vhost-level directive is deliberately placed *outside* the
  suspended/active branch, unlike hotlink/redirects which only apply to
  the active branch).
- **API**: full CRUD as the goal's literal shape asks —
  `GET`/`POST`/`DELETE /accounts/{u}/domains/{d}/ip-block`. UI: new page
  linked from the account's domain list, list + add/unblock form.

## Testing

`tests/test_validation.py` (+18): valid IPv4/IPv6 addresses and CIDR
ranges, netmask normalization, and rejection of malformed/out-of-range/
injection-attempt input. `tests/test_ols.py` (+3): `accessControl`
omitted when the block list is empty, rendered correctly with multiple
entries when not, and confirmed to render even for a suspended vhost.
`tests/test_handlers_ipblock.py` (new, 9): add/remove/list, duplicate
rejection, CIDR normalization, `refresh_vhost` invocation, unknown-domain
rejection. 581 tests passing (up from 556 after Feature 2).

## Live verification performed (the real Definition of Done)

Real account/domain/docroot, real OLS vhost, real `curl` requests over
HTTPS via `--resolve` — using this VM's own loopback address as the
"blocked IP" (the only address I can control which end the connection
actually arrives from):

1. Baseline (nothing blocked): `200`.
2. Blocked an unrelated IP (`203.0.113.99`) **and** `127.0.0.1` (this
   session's own connecting address) — confirmed the rendered
   `vhconf.conf` shows `accessControl { allow * / deny 203.0.113.99,
   127.0.0.1 }` exactly.
3. Real request from `127.0.0.1` (now blocked): **`403`**.
4. Unblocked `127.0.0.1` only, leaving `203.0.113.99` still blocked —
   real request from `127.0.0.1`: **`200`**, confirming the DONE WHEN
   bar directly: *"blocked IP gets 403, other IPs unaffected"* — the
   still-blocked, unrelated entry had zero effect on the now-unblocked
   address.

## What's untested / explicitly out of scope

- IPv6 blocking wasn't exercised end-to-end over a real IPv6 connection
  (this VM/test only has a routable IPv4 path to verify against) — the
  validator and template rendering both handle IPv6/CIDR6 correctly at
  the unit level (`test_valid_ip_or_cidr`), but the live curl
  confirmation above is IPv4-only.
- No UI-level (browser form) live click-through was performed — verified
  via the same RPC path the API layer calls, plus a direct inspection of
  the rendered on-disk vhost config, which is the part that actually
  determines OLS's real behavior.
- 200-entry-per-domain cap is enforced but not exercised at the boundary
  (adding the 201st entry) in a live test, only unit-tested.
