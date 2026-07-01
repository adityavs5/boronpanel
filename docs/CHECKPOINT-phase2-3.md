# Checkpoint: Phase 2, Feature 3 — Roundcube webmail

## What was built

- **Roundcube 1.6.6** installed via Ubuntu's own apt package
  (`roundcube-core`, `roundcube-mysql`) — free, no manual tarball/composer
  install needed. `dbconfig-common`'s automatic DB setup was deliberately
  skipped (preseeded `roundcube/dbconfig-install=false`) in favor of
  configuring its database by hand, consistent with how every other
  service in this project (PowerDNS, `forgehost_mail`) has its credentials
  provisioned — no surprise interactive prompts, no dependency on
  dbconfig-common's own conventions.
- A dedicated `roundcube` MariaDB database + user (own address book/
  cache/settings only — entirely separate from the `forgehost_mail` schema
  Postfix/Dovecot use for actual mail routing/auth), scoped grant on just
  that one database.
- Enabled Postfix's `submission` (587) service with SASL-required auth
  (was present but commented out in the stock `master.cf` since Phase 1
  never needed it) — Roundcube's default SMTP config targets 587.
- **Deployed once, server-wide** (`daemon/ols.py: bootstrap_webmail()`),
  not per-account: a static `virtualHost roundcube` + `extProcessor
  roundcube_php` (running as `www-data`, not any hosting account) folded
  directly into `httpd_config.conf.j2` as always-present template content
  (same pattern as the pre-existing static `PanelAdmin` listener),
  conditional on `webmail_hostname` being configured. Reuses the exact
  same `ConfigWriterMulti` validate→reload→verify→rollback pipeline every
  other OLS config change goes through — no new safety mechanism needed.
- **Login needs zero Forgehost-side integration code**: Roundcube
  authenticates directly against Dovecot's IMAP (localhost:143) using
  whatever address/password the user types — any mailbox created via
  Phase 1's `mail.create_mailbox` already works, confirmed live (below).
- SSL: reused Phase f's `ssl.issue` RPC unchanged in spirit -- extended
  `_challenge_plan`/`certificate_status`/the deploy hook with one special
  case for the static `webmail_hostname` (not a per-account `Domain` row),
  same HTTP-01-webroot mechanism as any hosted vhost.
- `webmail_url`/`webmail_hostname` settings (the former existed since
  Phase 1 as an unused link-out placeholder; now genuinely wired).
- 5 new unit tests (webmail block conditionally present/absent in the
  regenerated `httpd_config.conf`, webroot rendering, bootstrap guard,
  SSL challenge-plan special case).

## Three real bugs found by live testing, all permission/ownership issues
(not logic bugs) -- the recurring theme across this whole project

1. **`setUIDMode 2` on Roundcube's vhost failed `openlitespeed -t`** with
   the same "uid below CGIRLimit minimum" warning Phase 1 hit with OLS's
   stock Example vhost — except this time it couldn't just be deleted,
   since Roundcube is a real vhost we need working. Root cause understood
   correctly this time: Roundcube isn't multi-tenant, so DocRoot-UID mode
   was never the right choice for it in the first place — isolation
   already comes entirely from the `roundcube_php` extProcessor's
   `extUser`/`extGroup=www-data`. Fixed by using `setUIDMode 0` (server
   uid) instead, which needs no docroot-ownership precondition at all.
2. **`/var/lib/roundcube/public_html` was root:root** (the Debian package's
   own default), which independent of `setUIDMode` still fails the same
   OLS minimum-uid check for *any* vhost pointed at it. Fixed by chowning
   it (and `temp/`) to `www-data`, matching the extProcessor's own
   uid — the same identity was always going to execute PHP there, so this
   just makes file ownership agree with that instead of contradicting it.
3. **My own `Edit` tool calls silently reset `/etc/roundcube/config.inc.php`
   to `root:root`**, losing the package's original `root:www-data` group
   ownership — Roundcube's PHP process (running as `www-data`) then got a
   generic `Permission denied` reading its own config, which surfaced
   several layers up as a *database* error ("Access denied ... using
   password: NO", since the DSN construction silently fell through to
   empty values when the config file failed to load). Diagnosed by
   reproducing the exact `include()` call via a standalone `lsphp` CLI
   invocation as `www-data`, bypassing OLS entirely, which pinpointed the
   real cause in under a minute instead of chasing the misleading DB error
   message. Fixed with a `chown`/`chmod` pass; noting the mechanism (Edit
   tool rewrites can reset ownership on system files, not just the
   deploy.sh-owned repo files) as a reason to double-check permissions
   after editing any live `/etc` file this way going forward, not just
   files this project generates itself.

## An unrelated real test-hygiene bug found in the same pass

`tests/test_ols.py::test_render_httpd_config_empty_vhosts_has_no_virtualhost_block`
(written in Phase b) started failing — not because anything about vhost
rendering broke, but because `shared.config.settings` is a module-level
singleton loaded once from the *real* `/etc/forgehost/forgehost.toml` on
this machine, and that file now has a real `webmail_hostname` configured
for this feature. The test's assumption ("no vhosts in → no `virtualHost`
block out") was never actually pinned down against unrelated ambient
config — fixed by having it explicitly `monkeypatch` `webmail_hostname` to
empty, so it can't silently start failing again the next time some
unrelated feature adds a new always-on template block.

## Real end-to-end verification performed

1. `system.bootstrap_webmail` → real `openlitespeed -t` validated, real
   graceful reload, confirmed via `systemctl is-active lshttpd`.
2. A real external HTTP request to `webmail.104-234-179-64.sslip.io`
   (the same real, public, no-registration-needed domain pattern used
   throughout this project) returned Roundcube's actual login page —
   first attempt returned a generic PHP "Internal Error" page (bug #3
   above), root-caused via direct `lsphp` CLI reproduction, fixed, then
   re-verified clean.
3. **Real Let's Encrypt production certificate issued** for the webmail
   hostname via the existing `ssl.issue` RPC (HTTP-01 — no DNS-01
   ambiguity like the one flagged in `docs/STATUS.md` from Phase f/E2E
   validation) — confirmed via genuine `curl -v` (TLS 1.3, HTTP/2, no `-k`
   needed, real `O=Let's Encrypt` issuer chain).
4. Created a **real Forgehost account, mail domain, and mailbox**
   end-to-end via the RPC API, then performed a **real browser-equivalent
   login** against Roundcube (fetched the login page for its CSRF token,
   POSTed real credentials, followed the redirect chain) — landed on
   `?_task=mail` with page title "Forgehost Webmail :: Inbox", confirming
   genuine IMAP authentication succeeded against Dovecot using a mailbox
   password that was never touched by any Roundcube-specific code.
5. Terminated the test account — confirmed Roundcube itself (server-wide
   infrastructure, not an account resource) is completely unaffected,
   still serving `HTTP 200`.
6. Full 196-test suite (191 existing + 5 new) passing, after fixing the
   test-hygiene issue above.

## What's untested

- Sending mail *through* Roundcube (the SMTP/587 submission path) was not
  exercised — only IMAP login/inbox display. The submission service was
  confirmed listening and SASL-configured, but no message was actually
  composed and sent through it.
- No plugin beyond Roundcube's own defaults was installed (`archive`,
  `zipdownload` etc. are commented out in `config.inc.php`, matching the
  Debian package's own shipped default) — not needed for v1 scope
  ("deploy once server-wide... per-account login works automatically"),
  not attempted.
- Certificate renewal for the webmail hostname specifically (relies on
  certbot's own stock timer, same as every other cert in this project —
  not re-verified per-domain, since that's upstream certbot behavior, not
  Forgehost code).

## What to review first on wake-up

- The three ownership/permission bugs above are all instances of the same
  underlying lesson repeated from Phase 1 (docroot ownership must satisfy
  BOTH the actual PHP-execution uid AND OLS's own `-t`-time minimum-uid
  check) — worth internalizing as a checklist item for any *future* static
  (non-account) vhost this project adds, rather than rediscovering it each
  time.
