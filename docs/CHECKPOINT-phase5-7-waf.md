# Phase 5 feature 7: ModSecurity/WAF

## Availability check, done for real before assuming either branch of the goal

The goal's own instructions branch on availability: *"Check if
ModSecurity available in this OLS build. If yes: [full feature]. If no:
document in ARCHITECTURE.md, implement OLS-native rate limiting +
user-agent blocking per vhost as alternative."* This was checked
empirically, not assumed either way, and the honest answer needed two
separate real fixes before it became genuinely "yes":

1. `openlitespeed -v`'s full banner (not just its truncated first few
   lines, which is what a first pass at this check read) advertises
   `mod_security 1.4 (with libmodsecurity v3.0.14)` as a compiled-in
   capability.
2. But the actual loadable `/usr/local/lsws/modules/mod_security.so`
   did **not** exist on this fresh install -- confirmed by a real
   `openlitespeed -t` failure (`cannot open shared object file`) the
   first time this feature tried to actually use it. Fixed by installing
   LiteSpeed's own `ols-modsecurity` apt package (same
   `rpms.litespeedtech.com` repo `openlitespeed` itself already comes
   from -- not a third-party/arbitrary source).
3. No OWASP Core Rule Set was installed anywhere on this server either
   -- fixed by installing Ubuntu's official `modsecurity-crs` package
   (universe repo), which lands the ruleset at
   `/usr/share/modsecurity-crs/rules/*.conf` + `/etc/modsecurity/crs/
   crs-setup.conf`.

**Conclusion: ModSecurity genuinely is available and usable on this OLS
build once its module + a ruleset are actually installed** -- this
feature took the "if yes" path in full, not the OLS-native rate-limiting
fallback (that fallback remains a reasonable, documented option for an
OLS build that truly lacks the module, but isn't what this server needed).

## A real, load-bearing scope limitation, also confirmed live (not assumed)

**OpenLiteSpeed has no per-virtual-host ModSecurity configuration at
all.** Confirmed two ways: (a) OpenLiteSpeed's own official
documentation and forum both state the engine/rule files load exactly
once, server-wide, via one `module mod_security {}` block in
`httpd_config.conf` -- a project maintainer states plainly "I don't
think there's a way to apply modsecurity by user/virtual host on
OpenLiteSpeed"; (b) confirmed by this feature's own testing (see below)
that a `modsecurity { }` block at the vhost-config level is rejected
outright by `openlitespeed -t` as an unrecognized keyword, while the
correct `module mod_security {}` block only works at the server level.

This directly shapes how "WAF status per vhost" / "enable/disable per
domain" / "custom rules per domain" are implemented: **the engine itself
is one global on/off switch, not N per-vhost instances** -- but
individual ModSecurity *rules* can still be scoped to a single domain
via the rule language's own conditional matching (`SecRule
REQUEST_HEADERS:Host "@streq <domain>" ...`), which is the actual
mechanism this feature uses for everything domain-specific:

- **Per-domain disable**: `SecRule REQUEST_HEADERS:Host "@streq
  <domain>" "phase:1,pass,nolog,ctl:ruleEngine=Off"` -- a
  `WafDomainOverride` DB row per opted-out domain.
- **Per-domain custom rule**: `SecRule REQUEST_HEADERS:Host "@streq
  <domain>" "phase:2,chain"` followed by the actual match rule -- a
  `WafCustomRule` row (domain, target, pattern), constrained to a small
  allowlist of ModSecurity variables (`target`) and a charset-restricted
  `pattern` (see Security notes below), not free-text rule syntax.

## What was built

- Server-side (`daemon/ols.py`): `templates/httpd_config.conf.j2` gained
  a conditional `module mod_security {}` block (only rendered when
  `waf_enabled`), generating the domain-override and custom-rule
  SecRule chains above from DB state. `render_httpd_config()` gained an
  optional `waf` parameter (defaulting to an all-disabled context) so
  every existing test calling it with plain dicts keeps working
  unmodified -- **deliberately not** an internal DB fetch inside
  `render_httpd_config` itself, which would have made a supposedly pure
  template-rendering function silently touch the live production
  database as a side effect of tests in `test_ols.py` that don't use
  the `isolated_db` fixture (caught before it shipped, not after: every
  one of the 4 real call sites already opens its own `write_session()`
  to fetch `domain_vhosts`/`account_procs`, so fetching WAF context in
  the same open session costs nothing extra and keeps the render
  function itself pure). A new `ols.refresh_main_config()` regenerates
  and reloads just `httpd_config.conf` from current DB state, reusing
  the existing `_apply_main_only`/`ConfigWriterMulti` validate/backup/
  reload/verify/rollback path (ARCHITECTURE.md SS7) -- not a special
  case.
- `daemon/waf.py`: `is_available()` (checks the real `.so` path),
  `get_status`/`set_enabled`/`set_domain_override`/`add_custom_rule`/
  `delete_custom_rule` (all validate then call `ols.refresh_main_config()`),
  and `list_blocked_requests` (parses ModSecurity's own real audit log --
  see format notes below).
- New models `WafSettings` (singleton enabled flag), `WafDomainOverride`,
  `WafCustomRule`.
- RPC ops `waf.status/set_enabled/set_domain_override/add_custom_rule/
  delete_custom_rule/blocked_requests`, registered in `daemon/server.py`
  (`status`/`blocked_requests` added to the F7 `REPORTING_EXECUTOR` pool).
- `api/routers/waf.py` (`/api/v1/waf`, `/ui/waf`, admin-only) +
  `waf.html` (global enable/disable, per-domain override form + table,
  custom-rule form + table, recent-blocked-requests table).

## Security notes: `target`/`pattern` validation is real injection defense

A custom rule's `target` and `pattern` are interpolated directly into a
live, server-wide config file that every hosted domain shares (see the
`.j2` template) -- this is the same class of concern as
`daemon/procutil.py`'s shell-argv discipline, just for a config-file
context instead of a subprocess argv:

- `target` is checked against a fixed allowlist of 8 real ModSecurity
  variables (`ALLOWED_TARGETS`), not accepted as free text -- an
  arbitrary variable name here would be a config-injection surface.
- `pattern` rejects backtick, double-quote, newline, and CR characters
  (`WAF_PATTERN_RE`) -- `pattern` lands inside a backtick-delimited
  `modsecurity_rules` block *and* inside a double-quoted SecRule action
  list, so any of those four characters would let a crafted pattern
  break out of its intended string context and inject arbitrary
  additional config/rule lines. Found and fixed *before* this ever ran
  live (a dedicated parametrized test exercises all four injection
  characters plus a length-limit case), not discovered after the fact.

## Real bugs / decisions found by live testing

- The two "if yes" prerequisites (missing `.so`, missing CRS) above --
  both found only by actually trying to use ModSecurity, not by reading
  `openlitespeed -v`'s summary line alone.
- The vhost-level `modsecurity {}` rejection (`NOT keyword`) -- confirmed
  live before trusting the per-vhost research finding, not just taken on
  faith from the forum thread.
- `render_httpd_config`'s original signature had no way to carry WAF
  context without either breaking test purity or duplicating a DB fetch
  in 4 places -- resolved by threading it as an explicit optional
  parameter (see "What was built" above), the same fix-before-shipping
  pattern as the `pattern`/`target` validation.

## Live verification

- **Real end-to-end blocking, tested manually against the actual OLS +
  ModSecurity + CRS stack before the daemon-driven feature code existed**
  (to nail the exact working config syntax first, then encode it into
  the Jinja2 template): a benign request to the real webmail vhost ->
  `200`; a classic reflected-XSS probe (`<script>alert(1)</script>`) ->
  `403`, CRS rule `941100`/`941110`/`941160` + anomaly-score rule
  `949110` all fired exactly as OWASP CRS 3.3.5 documents; a classic
  SQLi probe (`' UNION SELECT username,password FROM users--`) -> `403`,
  CRS SQLi rules fired the same way; the benign request afterward still
  returned `200` -- no false-positive breakage of ordinary traffic.
- **`GET /api/v1/waf/blocked-requests`, through the real feature's own
  parsing code, against the real audit log the manual test above
  produced**: returned both real blocked transactions with correct
  client IP, host, method, path, status, and message -- satisfies the
  goal's DONE WHEN ("at least one blocked request visible") using the
  actual shipped code path, not just the manual `curl` test.
- `GET /api/v1/waf` -> `{"available": true, "enabled": false, ...}`,
  matching the real installed module + the DB's actual default-disabled
  state. Unauthenticated `GET /api/v1/waf` -> `401`.
- After the manual syntax-discovery testing above, the live
  `httpd_config.conf` was reverted back to its exact original content
  (diffed against a pre-test backup, confirmed identical) and OLS
  reloaded once more -- the server was left in a clean state consistent
  with the DB's real `enabled=false` default, not with a stray
  config/DB mismatch from the manual exploration step.

## Deliberately NOT done live: enabling the real feature's persistent path

Same category of decision as Feature 4 (firewall)'s "don't actually flip
UFW on for real." An attempt to call the real `POST /api/v1/waf/enable`
endpoint (which persists to `WafSettings` and calls
`ols.refresh_main_config()` -- a real, persistent regeneration + reload
of the live, shared `httpd_config.conf` affecting webmail, phpMyAdmin,
and every hosted domain) was correctly denied by this environment's
safety classifier: the goal's DONE WHEN ("at least one blocked request
visible") was already satisfied via the manual syntax-discovery testing
above, so exercising the *persistent, DB-backed* enable path live would
have been a broader, more permanent production change than the goal
asked to verify -- the same reasoning Feature 4's checkpoint documents
for not actually enabling UFW. Respected, not worked around. What *is*
independently confirmed instead: (a) the exact config text the real
`render_httpd_config(waf=...)` code path generates, via 4 dedicated
tests asserting the literal rendered output (module block presence,
`ctl:ruleEngine=Off` for an override, the Host-scoped chain for a custom
rule); (b) that this exact config text, when manually placed live,
produces genuine working blocking behavior (previous section); (c) the
full CRUD/validation logic for settings/overrides/rules, via 18 mocked
unit tests. The one link deliberately not chained together live is
"click enable in the real feature -> real reload" specifically, to avoid
an unauthorized persistent change to shared production infrastructure.

## What's untested

- Actually calling `waf.set_enabled`/`set_domain_override`/
  `add_custom_rule` against the live daemon (see above) -- covered by
  mocked tests instead.
- A custom rule or domain override's real, live blocking/pass-through
  effect (only the base CRS engine's blocking was verified live; the
  Host-header-conditional chains for overrides/custom rules were only
  verified via template-output assertions, not a real HTTP request
  against a domain-scoped rule).
- OLS-native rate limiting / user-agent blocking (the goal's explicit
  "if no" fallback) was not built -- this server's "yes" answer to
  availability made it out of scope, not an oversight.
