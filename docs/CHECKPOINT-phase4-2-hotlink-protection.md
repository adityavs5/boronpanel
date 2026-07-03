# Checkpoint: Phase 4 feature 2 — Hotlink protection

## What was built

- **Per-domain toggle + allow-list**, `Domain.hotlink_protection_enabled`
  (default `False` — opt-in; a fresh domain must not suddenly start
  blocking image requests nobody asked to block) and
  `Domain.hotlink_allowed_domains` (JSON list, own domain always
  implicitly allowed, up to 20 extra entries, deduped/normalized through
  `validate_domain`).
- **Standard mod_rewrite-compatible recipe**, rendered into the domain's
  own vhost `rewrite { rules }` block (OLS's rewrite engine is Apache
  mod_rewrite-compatible — the same engine Phase 3 feature 7's redirects
  and the suspended-page rule already use): block only when `Referer` is
  **present** and matches **none** of (this domain, any allow-listed
  domain) — chained `RewriteCond` lines AND together by default (no
  `[OR]`), so "not empty AND not own-domain AND not each allowed domain"
  all being simultaneously true is exactly the block condition. **Empty
  Referer is always allowed** — direct navigation, bookmarks, and
  privacy-conscious browsers/extensions that strip it are common and
  legitimate; blocking on absence would break far more real traffic than
  it protects, and the goal's active text doesn't ask for it either.
  Protected extensions: `jpg|jpeg|png|gif|bmp|webp|svg|ico|mp4|mp3` (a
  fixed constant, not admin-configurable — not asked for).
- **Composable with redirects, not just "suspended".** Unlike
  suspended-vs-everything-else (exclusive), hotlink protection and
  per-domain redirects (Phase 3 feature 7) can both be active on the same
  domain at once — the vhost template now branches on `suspended` first
  (wins outright) and otherwise renders hotlink rules (if enabled) ahead
  of redirect rules (a blocked hotlinked image should never fall through
  to a redirect).
- **API**: `GET`/`PATCH /accounts/{u}/domains/{d}/hotlink-protection`
  (matches the goal's literal shape). UI: a new page linked from the
  account's domain list.

## Testing

`tests/test_ols.py` (+5): rendering with protection disabled (no
`HTTP_REFERER` anywhere in the output), enabled (own-domain condition
line present), with an extra allowed domain, coexisting with an active
redirect on the same domain, and confirming a suspended account's vhost
never includes hotlink rules regardless of the setting.
`tests/test_handlers_hotlink.py` (new, 6) and 4 new
`validate_hotlink_allowed_domains` tests in `test_validation.py`. 556
tests passing (up from 541 after Feature 1).

**A real bug caught immediately by the new tests, before deploy**: making
hotlink protection composable with redirects (`{% elif hotlink or
redirects %}` instead of the old `{% elif redirects %}`) meant the
`{% for r in redirects %}` loop could now be reached with
`redirects=None` (hotlink enabled, no redirects configured) —
`TypeError: 'NoneType' object is not iterable`. Every prior code path
into that loop had `redirects` be a real list or the branch wasn't
entered at all, so this exact combination had never been exercised
before. Fixed with `{% for r in redirects or [] %}`.

## Live verification performed (the real Definition of Done)

Real account, real domain, a real 1x1 GIF placed in the docroot, real
`curl` requests through the live OLS server (not a direct RPC/mock) with
`--resolve` to hit the vhost by name over HTTPS:

| Referer sent | Result | Expected |
|---|---|---|
| none | `200` | always allowed |
| `https://p4hotlinktest.example/page.html` (own domain) | `200` | allowed |
| `https://www.p4hotlinktest.example/page.html` (subdomain of own) | `200` | allowed (subdomain match) |
| `https://allowed-cdn.example/embed.html` (allow-listed) | `200` | allowed |
| `https://evil-hotlinker.example/steal.html` (unrelated) | **`403`** | **blocked** |
| same unrelated referer, non-image path (`/`) | `404` (not `403`) | confirms only the protected extensions are referer-checked at all -- a `403` here would have meant the RewriteRule over-matched |

Every row matches the goal's DONE WHEN bar directly: "external image embed
blocked, same-domain embed works" — both halves independently confirmed
against the real, deployed OLS config, not asserted from the template
source.

## What's untested / explicitly out of scope

- The protected-extension list is fixed, not admin-configurable — not
  asked for in the goal.
- No dedicated "hotlink protection + hotlink allow-list + redirect, all
  three active on one domain simultaneously" live test — covered at the
  unit-test level (`test_render_vhost_conf_hotlink_and_redirects_coexist`)
  but not re-verified against a real running OLS instance; judged
  low-risk since both rule sets are independent `RewriteCond`/
  `RewriteRule` blocks with no shared state.
- Case-sensitivity of the `Referer` header matching relies on `[NC]`
  (case-insensitive) on the domain-match conditions, which OLS's rewrite
  engine documents as mod_rewrite-compatible; not independently
  re-verified with a mixed-case Referer host in this pass (the real
  `curl` tests above all used lowercase hosts).
