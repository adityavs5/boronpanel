# Checkpoint: Phase 3, Feature 7 — Redirect manager

## What was built

- **Per-domain path redirects**, rendered into that domain's own vhost as
  a standard, OLS/mod_rewrite-compatible `RewriteRule` -- extending the
  vhost-level `rewrite {}` block that already existed for the suspended-
  page redirect (Phase b), not a separate mechanism. Redirects and the
  suspended-page rule are mutually exclusive (`{% if suspended %}...
  {% elif redirects %}...{% endif %}`) -- a suspended account serves the
  suspended page for everything, custom redirects don't reactivate
  serving during suspension.
- **New `Redirect` table**, keyed by domain *name* (string), not a FK to
  `domains.id` -- matches this project's existing convention for
  domain-scoped features (`DnsZone.zone`, `MailDomain.domain`) rather
  than introducing the first FK to `domains.id` in the whole schema;
  cleanup on domain removal is explicit application code (this
  project's established manual-cascade pattern, no DB-level
  `ON DELETE CASCADE` anywhere in this schema).
- **Validation is the actual injection defense**, not a formality: the
  `path` is restricted to a small safe charset (letters/digits/`._/~%-`)
  that excludes every regex/rewrite-rule metacharacter that could let a
  crafted path escape its intended single-path match or inject
  additional rewrite directives, and the one allowed-but-regex-special
  character (`.`, common in real paths like `/old.html`) is escaped to
  a literal `\.` in Python before ever reaching the template -- the
  *validated* path is never interpolated directly into a regex context.
  `target_url` must be an absolute `http(s)://` URL with no whitespace/
  bracket/newline characters (which could otherwise break out of the
  rewrite rule's own `[R=...]` flag syntax). `status_code` is restricted
  to exactly `301`/`302` per the goal.
- **Create-or-replace (upsert) semantics** by `(domain, path)` -- matches
  this project's existing DNS-record-editor UX ("add/replace a record"),
  so the UI needs only one form for both adding a new redirect and
  editing an existing one.
- API: full CRUD at `/accounts/{u}/domains/{d}/redirects` (per the
  goal). UI: a redirect list page per domain, linked from each domain
  row on the account detail page.

## A systemic validation bug found by live testing, fixed across the whole module (not just this feature)

Writing this feature's own path validator (`REDIRECT_PATH_RE = re.compile
(r"^/[...]*$")`) and testing it against a raw literal `"\n"` at the end
of a path revealed that **Python's `$` regex anchor matches either at
the true end of a string OR immediately before a single trailing
newline** (documented `re` behavior) -- so a bare `"^...$"`-anchored
validator incorrectly *accepts* a value with a trailing newline appended.
Auditing `shared/validation.py` for the same pattern found it affects
**every other `^...$`-anchored regex in the file**, including
`validate_username` (a **Phase 1** validator, pre-existing this whole
project): `validate_username("root\n")` incorrectly passed, despite
being trivially confusable with (but distinct from, so not caught by
the exact-match `RESERVED_USERNAMES` check) the actually-reserved
`"root"`. **Fixed** by switching every affected regex in the file from
`^...$` to `\A...\Z` (which anchor strictly to the literal start/end of
the string, no newline exception) -- `validate_username`,
`validate_domain`'s internal pattern, `validate_db_identifier`,
`validate_mailbox_local_part`, `validate_email_address` (Phase 3 feature
4), the PHP-ini validators (Phase 3 feature 6), `validate_iso_date`
(Phase 3 feature 4), and this feature's own `validate_redirect_path`.
No behavior change for any well-formed input; only rejects previously-
mis-accepted trailing-newline-appended garbage. This is exactly the
"fix cleanly, don't work around, even when it's a pre-existing Phase 1
gap unrelated to the feature currently being built" posture this project
has followed all session -- flagged here in detail since it's a
correctness fix to shared, foundational code, not scoped to just this
feature.

## A second real gap found by live testing: redirects for a terminated account's PRIMARY domain were never cleaned up

`handlers_domain.remove_domain`'s existing redirect cleanup (added
alongside this feature) only runs for addon/subdomain removal -- but an
account's **primary** domain's `Domain` row deliberately survives
`account.terminate` (the same established pattern as `Account` itself),
so that cleanup path never runs for it. Confirmed live: terminating a
test account left its primary domain's redirect rows sitting in the DB
indefinitely, orphaned config state for a vhost that no longer exists.
**Fixed** with a new `terminate_account_redirects` `TERMINATE_HOOKS`
entry (mirrors Phase 3 feature 6's identical `PhpIniOverride` cleanup,
added for the same reason: a later `account.reactivate` should start
from a clean slate rather than silently reapplying settings the
operator has lost visibility into) -- confirmed live afterward with a
fresh terminate cycle that the redirect row count for that domain drops
to zero.

## Testing

`tests/test_handlers_redirect.py` (new, 14 tests): happy path, default
status code, path/target/status-code validation rejections, upsert
behavior (same path twice replaces rather than duplicating), `PUT`
semantics, list/delete, terminate-account cleanup (the bug above), and
domain-removal cleanup. `tests/test_validation.py` extended (12 new
tests) for the redirect validators plus a dedicated regression test for
the `\A`/`\Z` fix on `validate_username`. `tests/test_ols.py` extended (5
new tests): omits the rewrite block entirely when no redirects exist,
renders a correct `RewriteRule` when they do, confirms suspension
overrides/ignores redirects, and confirms the dot-escaping helper
produces the exact expected regex-safe string. 461 tests passing (up
from 423).

## Live verification performed

1. Created a real account + domain, added a 301 and a 302 redirect --
   `curl -I` against both confirmed the exact status code and `Location`
   header for each (the goal's own DONE WHEN bar), and a **third,
   unrelated path** on the same domain correctly still returned a plain
   `404` (not caught by either rewrite rule).
2. Added a redirect for a path containing a literal `.`
   (`/old.html`) -- confirmed it redirects on the exact path, **and**
   confirmed a *different* path that a literal (unescaped) `.` would
   have incorrectly matched as "any character" (`/oldXhtml`) correctly
   still 404s, proving the dot-escaping fix is doing real work, not just
   passing its own unit test.
3. Confirmed update (`redirect.update`) changes an existing redirect's
   target/code in place, re-verified via a fresh `curl -I`.
4. Confirmed delete removes a redirect -- re-verified the deleted path
   now correctly 404s instead of redirecting.
5. Found and fixed both bugs above via this live testing.
6. Exercised the UI page through real HTTP (session-cookie
   authenticated): added a redirect via the form, reloaded, confirmed it
   appears in the rendered list.
7. `account.terminate` re-verified (after the fix) to leave zero
   orphaned redirect rows for the account's primary domain.

## What's untested / explicitly out of scope

- Whole-domain wildcard redirects (e.g. "redirect every path on this
  domain to a different domain") -- the goal's own wording is
  "path → URL", a single specific path per rule, which is what was
  built; a wildcard would need a different rewrite pattern
  (`RewriteRule ^(.*)$ ...`) not requested here.
- Redirect chains/loops detection (a redirect whose target happens to be
  another path on the same domain that's itself redirected) -- not
  validated against; OLS/the browser would just follow the chain
  normally, same as any webserver.
