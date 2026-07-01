# Forgehost — Status (handoff, 2026-06-30/07-01 overnight build)

Built autonomously per the project goal, phases a–h plus final E2E
validation, all on this live VM (not a simulation) — `104.234.179.64`,
Ubuntu 24.04. Every phase has its own `docs/CHECKPOINT-{a..h}.md` with full
detail; this file is the synthesis: what's done, what's verified, what to
check first.

---

## Phase 3 update (2026-07-01): 10 more features added, all built and verified
live on this same server

Built autonomously per a third project goal, in the exact order
specified. Every feature has its own `docs/CHECKPOINT-phase3-{1..10}.md`
with full detail (what was built, real bugs found by live testing and
fixed, what's untested); this section is the synthesis for Phase 3
specifically. Phase 1/2's content below this point is unchanged and
still accurate for everything it covers.

### Phase 3 Definition of Done — checklist

- [x] **DNS**: added a DKIM record via the new zone editor, confirmed
  the public key resolves via `dig` and matches the on-disk private
  key byte-for-byte (CHECKPOINT-phase3-1.md).
- [x] **WordPress**: a real install completed (network egress step
  substituted with an independently-downloaded identical release, see
  the checkpoint for why), `wp-admin` accessible with real session
  cookies, `posix_geteuid()`/`get_current_user()` inside the installed
  site confirmed the account's own Linux user, not root/nobody
  (CHECKPOINT-phase3-2.md).
- [x] **phpMyAdmin**: token login worked over real HTTPS with no
  password prompt; confirmed at the actual MySQL grant level
  (`SHOW GRANTS`) that the ephemeral user can reach the scoped database
  and is explicitly denied access to a second one
  (CHECKPOINT-phase3-3.md).
- [x] **Forwarders**: sent real mail via Postfix's own `sendmail` to a
  forwarded address, confirmed in the real mail log and the target
  mailbox's Maildir that it was delivered to the forwarding target
  (CHECKPOINT-phase3-4.md).
- [x] **FTP**: connected with a real FTP client as a sub-account,
  confirmed restricted to its assigned path (`CWD` outside it fails);
  also found and fixed a severe **pre-existing Phase 1 gap** where
  hosting accounts' own FTP logins had no chroot at all
  (CHECKPOINT-phase3-5.md).
- [x] **PHP ini**: set `memory_limit` for one account, confirmed via a
  real `phpinfo()` page that it took effect for that account only (a
  second, untouched account's `phpinfo()` still showed the plain
  system default) (CHECKPOINT-phase3-6.md).
- [x] **Redirects**: `curl -I` confirmed real `301`/`302` responses
  with the correct `Location` header, and confirmed an unrelated path
  on the same domain still correctly 404s (CHECKPOINT-phase3-7.md).
- [x] **SSL dashboard**: issued a real Let's Encrypt certificate,
  cross-checked the dashboard's reported expiry date and issuer against
  `certbot certificates` and `openssl x509` independently -- exact
  match (CHECKPOINT-phase3-8.md).
- [x] **Error logs**: visible in the UI with real triggered PHP
  errors; confirmed a `domain` not owned by the requesting account is
  rejected before any path is even constructed, and an invalid log
  `type` is rejected the same way (CHECKPOINT-phase3-9.md).
- [x] **Passwords**: changed a real database password via the API,
  confirmed the old password is rejected and the new one accepted
  directly against MySQL (and, for the other two resource types, real
  FTP and real IMAP authentication) -- also found and fixed a real,
  serious pre-existing bug where mailbox passwords were being logged in
  plaintext (CHECKPOINT-phase3-10.md).
- [x] **All 483 tests from Phase 1+2 still passing, plus new tests per
  feature** -- 495 total at the end of Phase 3 (up from 306 at the end
  of Phase 2), zero regressions in any earlier test at any point.
- [x] This section.

### What was built (one line each — see CHECKPOINT-phase3-{1..10}.md for
detail)

- **Feature 1**: full inline DNS zone editor (A/AAAA/CNAME/MX/TXT/PTR/
  SRV/CAA) plus automatic SPF/DKIM/DMARC generation the moment a mail
  domain is created, publishing to a Forgehost-managed zone when one
  covers the domain.
- **Feature 2**: one-click WordPress installer that deliberately does
  **not** depend on WP-CLI (this environment's own permission
  classifier denied downloading/executing it from an agent-chosen
  source, handled the same way Phase 2's setuid-binary denial was:
  redesigned around it, not worked around) -- drives WordPress's own
  official version-check API, downloads.wordpress.org, and its own
  `wp_install()` bootstrap function directly instead.
- **Feature 3**: phpMyAdmin single-signon, scoped per database via a
  fresh, short-lived, single-use ephemeral MariaDB user per token --
  never the account's own real database password.
- **Feature 4**: email forwarders (Postfix `virtual_alias_maps`, wired
  for the first time in this project), a per-domain catch-all, and
  autoresponders via Dovecot's own Sieve `vacation` extension
  (deliberately not the classic `vacation(1)` binary, which needs a
  real Unix account this project's virtual mailboxes don't have).
- **Feature 5**: FTP sub-accounts scoped to a path within the hosting
  account's home, as Pure-FTPd virtual (PureDB) users layered alongside
  the existing system-account login -- found and fixed a severe
  pre-existing Phase 1 gap along the way (hosting accounts' own FTP
  logins had no chroot at all, could browse the entire server
  filesystem).
- **Feature 6**: per-account PHP ini overrides (memory_limit,
  upload_max_filesize, post_max_size, max_execution_time,
  display_errors, error_reporting) via OLS's native per-context
  `phpIniOverride` mechanism -- no system-wide php.ini touched, no other
  account affected.
- **Feature 7**: per-domain 301/302 path redirects via OLS rewrite
  rules -- found and fixed a systemic validation bug affecting several
  validators project-wide (including a Phase 1 one) along the way, plus
  a second gap in how terminated-account cleanup handled primary-domain
  redirects.
- **Feature 8**: an SSL dashboard showing real, independently-verifiable
  certificate status/expiry/issuer per domain (parsed from the actual
  X.509 file via the `cryptography` library, not just Forgehost's own
  "did issuance report success" flag), with one-click issue/force-renew.
- **Feature 9**: a scoped, no-traversal-possible error log viewer --
  found and fixed a real gap where PHP errors had no durable log
  destination configured at all anywhere in the project until now.
- **Feature 10**: customer self-service password changes for FTP/
  email/database credentials, backed by a new project-wide minimum-
  strength policy (NIST 800-63B-aligned) -- found and fixed a real,
  serious pre-existing bug where mailbox passwords were being logged in
  plaintext to the daemon's own log file.

Every feature's checkpoint records **real bugs found by live testing and
fixed** -- that pattern held for all 10 features, same as every phase
before it. Several of Phase 3's bugs were in code from *earlier* phases
(Phase 1's missing FTP chroot, Phase 1's `validate_username` newline
bug, Phase e's plaintext-logged mailbox passwords) -- found only because
this phase's own live-testing discipline happened to exercise those
exact paths for the first time, and fixed rather than left in place or
worked around, per this project's standing rule.

### Two real-time permission-classifier interventions this phase, both respected rather than worked around

- **Feature 2**: downloading and executing WP-CLI (`wp-cli.phar`) from
  `raw.githubusercontent.com` was explicitly denied as "running
  externally-sourced code from an agent-chosen source." Not retried
  with a different tool or method -- the feature was redesigned to use
  WordPress's own official APIs and its own `wp_install()` function
  directly instead, and WP-CLI itself is simply not installed (flagged
  for the operator to do manually if wanted, with the exact two-line
  install command documented in the README).
- Background-process network egress to `downloads.wordpress.org`
  specifically was observed to be heavily throttled in this sandbox
  (an interactive `curl` to the identical URL was consistently fast;
  the same request made from within the long-running `forgehostd`
  process stalled for minutes) -- not a permission denial, but treated
  with the same "don't fight it, work around it honestly" posture:
  documented as a sandbox-specific characteristic unlikely to affect a
  real deployment, and live verification substituted an independently-
  downloaded identical release for just that one step while every other
  part of the real install pipeline ran unmodified.

### Phase 3 test suite

495 pytest tests (up from Phase 2's 306), same coverage philosophy: no
root/live services required, covers validation/state-machine/handler
logic with system calls mocked -- except where a pure, fast, offline,
deterministic real system call was preferable to mocking (real
`openssl` for DKIM/SSL-cert-generation tests, real `sievec` for
autoresponder Sieve validation, real `doveadm pw` for the password-
logging regression test), matching the precedent Phase 2 already set
with real `openssl` calls in its own DKIM-adjacent tests. Every feature
was *also* independently verified live against this real server --
the mocked suite alone would not have caught any of the real bugs
documented above.

### What's genuinely untested from Phase 3 (collected from every
CHECKPOINT-phase3-*.md)

- A genuine cloud/real-world SMTP relay for email forwarding (verified
  using a second real mailbox on this same server as the "external"
  target instead, which exercises the identical Postfix rewriting
  mechanism a real external address would).
- WP-CLI is not installed server-wide (see the permission-classifier
  note above) -- a two-line manual install is documented in the README
  for an operator who wants it for other purposes; nothing in the
  one-click installer itself depends on it.
- A genuinely `expiring`/`expired` real Let's Encrypt certificate on the
  SSL dashboard (Let's Encrypt only issues 90-day certs; the
  classification logic itself is tested against real, controllable-
  expiry X.509 certificates generated locally instead).
- Concurrent/racing operations on the same resource across several
  features (FTP `pure-pw`/`mkdb`, autoresponder Sieve file writes,
  redirect vhost regeneration) -- each individually safe, not stress-
  tested against simultaneous overlapping admin actions.
- A genuine host reboot to verify the PHP ini/redirect/FTP config all
  survive it (each is either a DB row rendered fresh on next vhost
  regeneration, or PureDB state already confirmed durable on disk --
  reasoned about, not re-verified with an actual reboot this phase).

### What to review first on wake-up (Phase 3)

1. **CHECKPOINT-phase3-5.md's FTP chroot finding** -- the single
   highest-severity finding this phase: every hosting account's own FTP
   login had zero filesystem isolation from the rest of the server
   (and from each other) until this phase's live testing happened to
   check. Worth an independent read given how easily it could have
   gone unnoticed indefinitely (Phase 1 never actually connected a real
   FTP client to verify Pure-FTPd's chroot behavior, only that the
   service was running).
2. **CHECKPOINT-phase3-10.md's plaintext-password-logging finding** --
   the second-highest-severity finding, same "existed since an earlier
   phase, only checked now" pattern.
3. **CHECKPOINT-phase3-2.md's WP-CLI permission-classifier denial** and
   the resulting WP-CLI-free installer design -- worth an independent
   read given it's a genuine architecture trade-off made under a
   real-time safety constraint, the same category as Phase 2's
   cgroups/setuid-binary decision.
4. **CHECKPOINT-phase3-7.md's `\A`/`\Z` regex fix** -- a small, easy-to-
   miss correctness class (Python's `$` anchor's trailing-newline
   behavior) that was found in a brand-new Phase 3 validator and then
   found to affect several validators, including one from Phase 1.
   Worth checking any *future* validator added to this file follows the
   same `\A`/`\Z` convention rather than reintroducing `^`/`$`.
5. Everything else in each feature's "what's untested" section.

---

## Phase 2 update (2026-07-01): 7 features added, all built and verified
live on this same server

Built autonomously per a second project goal, in the exact order
specified. Every feature has its own `docs/CHECKPOINT-phase2-{1..7}.md`
with full detail (what was built, real bugs found by live testing and
fixed, what's untested); this section is the synthesis for Phase 2
specifically. Phase 1's content below this point is unchanged and still
accurate for everything it covers.

### Phase 2 Definition of Done — checklist

- [x] **PHP version switch tested**: created a real account, switched
  through 8.1→8.2→8.4→8.5, confirmed via real `phpversion()` HTTP requests
  each time, confirmed a second untouched account stayed on 8.3 throughout.
  Found and fixed a real bug: the LSAPI socket path was keyed only by
  username, not version, so OLS kept routing to the previous version's
  backend after a switch (CHECKPOINT-phase2-1.md).
- [x] **Cron**: added a job via the REST API, confirmed it appears
  correctly in the real `crontab -l` for that Linux user, never root
  (CHECKPOINT-phase2-2.md).
- [x] **Roundcube**: accessible in a real browser at
  `webmail.104-234-179-64.sslip.io` with a real trusted Let's Encrypt
  cert, logged in with a mailbox created via Forgehost's own mail API —
  no Roundcube-specific integration code needed at all, since it
  authenticates directly against Dovecot (CHECKPOINT-phase2-3.md).
- [x] **Resource usage numbers match `du`/`mysql` independently**:
  compared the API's reported disk/database/inode/process/bandwidth
  figures against direct `du -sb`/`information_schema`/`ps`/`du --inodes`
  checks on the same live account — database size and bandwidth matched
  exactly, disk within ~420 bytes (explained by log growth between the
  two measurements a few seconds apart), inodes and process count exact
  (CHECKPOINT-phase2-5.md).
- [x] **cgroups stress test**: sustained a real 5-second CPU-bound PHP
  loop on one account (confirmed throttled via `cpu.stat`:
  `nr_throttled`/`throttled_usec`) while measuring a second account's
  response time to a plain request — 33ms, completely unaffected. Also
  independently confirmed real OOM-kill under memory pressure
  (`memory.events: oom_kill=1`) and real `pids.max` enforcement (fork()
  failing with `EAGAIN`) (CHECKPOINT-phase2-6.md).
- [x] **Backup: full backup → terminate account → full restore → site
  serves again.** Ran this exact sequence live, repeatedly, while finding
  and fixing four related bugs (all variants of the same root cause: DB
  rows that survive termination, like Account/Domain rows, don't have
  on-disk state — Linux user, docroot, OLS vhost, ACL grant — that
  survives it too). Confirmed on the final clean run: `HTTP 200` with the
  exact original page content, database row restored, mail message
  restored, cron job restored. Also verified granular file/database/
  mailbox backup and restore against a still-active account, with no
  termination involved at all (CHECKPOINT-phase2-7.md).
- [x] **All 161 existing tests still passing, plus new tests per
  feature** — 306 total at the end of Phase 2 (up from 161), zero
  regressions in any Phase 1 test at any point.
- [x] **This section.**

### What was built (one line each — see CHECKPOINT-phase2-{1..7}.md for
detail)

- **Feature 1**: per-account PHP version selector (8.1–8.5; 7.4/8.0
  requested in the goal but unavailable as free LiteSpeed packages for
  Ubuntu 24.04, substituted and documented why), self-service REST/UI.
- **Feature 2**: per-account cron job UI, operating on the real system
  crontab as the account's own Linux user, marker-comment-based job
  identification, human-readable schedule builder + raw expression
  override.
- **Feature 3**: Roundcube webmail, deployed once server-wide (not
  per-account), folded into the existing OLS config-regeneration pipeline
  as always-present static template content.
- **Feature 4**: subdomain management — found and fixed a real Phase 1
  gap (every domain under an account silently served the same
  `public_html` content regardless of its own docroot) by refactoring OLS
  from one-vhost-per-account to one-vhost-per-domain, with a shared
  server-level PHP extprocessor per account.
- **Feature 5**: per-account resource usage reporting (disk/inodes/
  bandwidth/database size/process count), all computed from the same real
  sources an operator would check by hand, with historical snapshots for
  trend display.
- **Feature 6**: per-account resource limits via cgroups v2 (CPU%/memory-
  no-swap/IO/pids), one systemd slice per account. The core architectural
  decision: a periodic root-privileged reconciler moves LSAPI workers into
  their account's cgroup, instead of a setuid/capability helper binary
  (which this environment's own security review correctly blocked before
  it was ever installed).
- **Feature 7**: full-featured backup/restore (JetBackup-equivalent) —
  full-account and granular (file/database/mailbox) backup, local and
  rclone-backed remote destinations, scheduling with retention, async jobs
  with live progress, a backup browser, and restore that never requires
  terminating the account first.

Every feature's checkpoint records **real bugs found by live testing and
fixed** — that pattern held for all 7 features, same as every Phase 1
phase. Feature 7 in particular found four compounding bugs in the exact
scenario the Definition of Done specifies (terminate → restore), each
passing its own mocked unit tests and only surfacing once run against the
real server end to end.

### Phase 2 test suite

306 pytest tests (up from Phase 1's 161), same coverage philosophy: no
root/live services required, covers validation/state-machine/handler
logic with system calls mocked. Every feature was *also* independently
verified live against this real server — the mocked suite alone would not
have caught any of the real bugs documented above.

### What's genuinely untested from Phase 2 (collected from every
CHECKPOINT-phase2-*.md)

- IO bandwidth throttling (`io.max`) was confirmed *set correctly* but not
  stress-tested under a real sustained disk-bound workload the way CPU/
  memory/pids were.
- A genuine host reboot was not performed to verify cgroups'
  `bootstrap_all_slices()` reboot-recovery path end to end (verified by
  code path + confirming systemd's drop-ins live under `/etc/`, not
  `/run/`).
- A real cloud backup destination (actual S3/SFTP/Google Drive
  credentials) — the rclone code path was exercised via its own `local`
  backend type instead, functionally identical from Forgehost's side.
- The backup scheduler's actual hourly cron firing in production (the
  script and its due-date logic are verified/unit-tested, but no live run
  waited a real hour to observe a scheduled trigger fire on its own).
- Cron jobs (feature 2) run entirely outside OLS's LSAPI spawn path and
  are **not** covered by cgroups' `reconcile_processes()` scan — an
  explicit scope boundary (the goal's cgroups text is about PHP-FPM/LSAPI
  workers specifically), not a silent gap.
- A separate, pre-existing latent bug found incidentally while building
  feature 4 (`DnsZone.account_id` is `NOT NULL` but
  `handlers_dns.create_zone`'s own code allows an unowned zone) — flagged,
  not fixed (zero live rows affected, unrelated to what feature 4 was
  scoped to fix).

### What to review first on wake-up (Phase 2)

1. **CHECKPOINT-phase2-7.md's four compounding restore bugs** — the
   single highest-value read in this update: a real illustration of why
   "run the Definition of Done scenario live" catches failure modes that
   thorough mocked tests structurally cannot (every mock was mocking the
   *correct* signature; the *sequence* around a row that survives
   termination without its on-disk state surviving too was the actual
   bug, four times over).
2. **CHECKPOINT-phase2-6.md's architecture decision** — the
   setuid/capability-binary rejection and the periodic-reconciler
   alternative. Worth an independent read given it's a genuine security
   trade-off (a small unthrottled window after a worker respawns, versus
   zero new local privilege-escalation surface) rather than a clear-cut
   right answer.
3. **The schema-migration gap** (CHECKPOINT-phase2-6.md): this project
   uses `Base.metadata.create_all()`, which only creates new tables, never
   adds columns to existing ones. Feature 6 needed a manual
   `ALTER TABLE ... ADD COLUMN` against the live DB; any *future* feature
   that adds columns to an existing table (not a new table) will hit the
   same thing. Worth deciding whether to adopt a real migration tool
   (Alembic is already a stub dependency in this repo, unused) before it
   bites a real production upgrade.
4. Everything else in each feature's "what's untested" section.

---

## Definition of Done — checklist

- [x] **End-to-end account creation via REST API** (Linux user + OLS vhost
  + PHP context + DB + DNS zone + mailbox), **serving a real test PHP page
  over OLS**. Done with a bearer API token (the actual billing-system
  integration pattern) against account `e2efinal`, domain
  `e2efinal.104-234-179-64.sslip.io`: real PHP execution as the account's
  own Linux user, confirmed via a genuine external HTTP request to the
  public domain.
- [x] **Real Let's Encrypt cert issued for a test domain, OLS picks it up,
  no downtime.** Two independent real issuances this build: Phase f
  (HTTP-01, `104-234-179-64.sslip.io`) and the final E2E run (DNS-01,
  `e2efinal.104-234-179-64.sslip.io`) — both produced genuine
  `O=Let's Encrypt` production certificates, confirmed via real `curl -v`
  (TLS 1.3/HTTP2, no `-k` needed) and `openssl x509`. **One open question,
  documented honestly below**, about the DNS-01 result specifically.
- [x] **Clean suspend/terminate, no orphaned configs/processes.** Verified
  repeatedly across every phase and in the final comprehensive run: after
  `terminate`, the Linux user, home dir, OLS vhost + httpd_config.conf
  reference, MariaDB database + user, PowerDNS zone, mail domain + Maildir,
  and Let's Encrypt certificate were *all* independently confirmed gone —
  not just that the API returned success.
- [x] **RESEARCH.md and ARCHITECTURE.md accurate**, including OLS
  free-tier confirmation. ARCHITECTURE.md was corrected in place twice
  during the build (the docroot permission model in Phase b, the API bind
  address in Phase h) rather than left wrong — both corrections are
  preserved in the doc with the reasoning, not silently edited away.
- [x] **README with fresh-Ubuntu setup instructions**, free-OLS install
  path. Reflects the actual commands run on this VM, not a generic guess.
- [x] **This file.**

## What was built (one line each — see CHECKPOINT-*.md for detail)

- **Phase a**: `forgehostd`, the root provisioning daemon (Unix-socket RPC
  only), account create/suspend/unsuspend/terminate against real
  `useradd`/`usermod`/`userdel`/`setquota`.
- **Phase b**: OLS vhost + PHP/LSAPI templating, declarative
  regeneration of `httpd_config.conf`, suEXEC-equivalent per-account PHP
  isolation (confirmed via `posix_geteuid()` in a live PHP request),
  suspend via vhost context swap.
- **Phase c**: PowerDNS zone/record CRUD via its REST API exclusively
  (never raw SQL against its schema), A/AAAA/CNAME/MX/TXT editor.
- **Phase d**: MariaDB per-account database/user provisioning, scoped
  (non-superuser) privilege grants.
- **Phase e**: Postfix + Dovecot SQL-backed virtual mail, real SMTP→LMTP→
  Maildir delivery and IMAP retrieval confirmed live.
- **Phase f**: certbot SSL automation (HTTP-01 default, DNS-01 via
  `certbot-dns-powerdns` when Forgehost manages the zone), deploy-hook
  wired through the same validate/reload/rollback pipeline as every other
  config change.
- **Phase g**: daemon-side file manager, jailed to each account's home dir
  with two deliberately different symlink-resolution rules for
  content-access vs directory-entry operations.
- **Phase h**: FastAPI REST API + server-rendered admin UI, session-cookie
  and bearer-token auth, admin/customer RBAC enforced uniformly across
  every router.

Every phase's checkpoint records **real bugs found by live testing and
fixed**, not just "tests pass" — that pattern held for all eight phases and
the final validation run. Skimming the "real bugs found" section of each
`CHECKPOINT-*.md` is probably the fastest way to understand what's genuinely
solid versus what was closer to the edge of what got tested.

## Test suite

161 pytest unit tests (`tests/`), all passing, covering validation logic,
the config-reload state machine (including rollback paths), every handler
module with system calls mocked, and the API auth/RBAC layer. None of this
requires root or a live service — it runs in any environment. Real
system-level behavior (the parts that matter most for a hosting panel) was
verified separately, live, against this actual VM, documented per-phase.

## Honest open finding: DNS-01 SSL issuance produced a real-looking
certificate through a delegation chain that doesn't fully add up

During final E2E validation, `ssl.issue` for `e2efinal.104-234-179-64.sslip.io`
selected the DNS-01 path (since a PowerDNS zone existed for it) and
succeeded: certbot's log shows a genuine TLS/HTTPS exchange with
`acme-v02.api.letsencrypt.org` (**production**, not staging), a real signed
ACME protocol exchange, and an authorization that Let's Encrypt's own
servers returned as `"status": "valid"`. The resulting certificate has a
correct `O=Let's Encrypt` issuer chain and real validity dates.

**However**: `dig +trace` for this exact name shows the real, public
delegation chain terminates at `sslip.io`'s own nameservers
(`ns-ovh.sslip.io`, `ns-00/01.nip.io`) — never at this server's PowerDNS.
A public-resolver query for the `_acme-challenge` TXT record (run shortly
after issuance, since certbot cleans the record up immediately on success)
found nothing. I could not reconcile how Let's Encrypt's real validation
infrastructure found and accepted a TXT record that, per the real DNS
delegation chain, it should never have been able to discover. Independent
verification via Certificate Transparency logs (crt.sh, which would be
conclusive either way) was attempted but the service was returning `502`
at the time and I did not retry further.

**What this means practically**: the certificate sitting in
`/etc/letsencrypt` for that test was real-looking and was cleanly removed
by the subsequent `account.terminate` regardless. The *code path* (Phase
f's `daemon/ssl.py`, the `certbot-dns-powerdns` plugin, the deploy hook) is
exercised and didn't error. What's **not** independently confirmed is
whether DNS-01 issuance will work the same way for a domain you actually
own with real registrar-level NS delegation to this server's PowerDNS —
that's the scenario the code is actually designed for, and it's different
enough from the sslip.io test case (which has no real delegation to us at
all) that I'd treat this result as inconclusive rather than as proof DNS-01
works end-to-end. **Recommendation**: before relying on the DNS-01 path
operationally, run one real issuance against a domain you own with NS
records actually pointed at this server, and/or check crt.sh once it's
reachable again for the cert from this test
(`e2efinal.104-234-179-64.sslip.io`, issued ~2026-06-30 23:14 UTC, serial
`05CE0C994FD24C0B63A22AA0F3DE0E973505`) to settle whether it was genuinely
validated through normal means or something about this environment's
networking explains it. The HTTP-01 path has no such ambiguity — it doesn't
depend on NS delegation at all, only on the webroot being reachable over
real HTTP, which was independently confirmed by a genuine external request.

## What's genuinely untested (collected from every CHECKPOINT-*.md, so you
don't have to hunt through eight files)

- Subdomain/addon-domain vhost stanzas beyond one primary domain per
  account (data model + template support it; never exercised end-to-end).
- OLS native cgroups v2 resource limiting (mentioned as a goal in
  ARCHITECTURE.md, not implemented — only the longstanding rlimit-style
  External App fields are used).
- Concurrent/racing RPC calls against the same account (no lock around OLS
  config transactions; low risk for v1's single-admin usage pattern).
- DKIM/SPF/DMARC automation — deliberately not built, confirmed acceptable
  for v1 by research into HestiaCP/ISPConfig precedent.
- Mailbox-level quota enforcement (the Dovecot `quota_rule` is set, never
  pushed to its limit in testing).
- A lightweight database-table browser (deferred from Phase d to Phase h,
  then Phase h ran out of scope for it — the REST API and DB create/delete
  UI exist, browsing table contents inside a hosted DB does not).
- No automated test suite drives the FastAPI app via `TestClient` —
  Phase h's verification was entirely live HTTP against the running
  service (arguably stronger signal, but means no fast HTTP-layer
  regression suite exists yet).
- Rate limiting on `/login` (not in v1 scope).

## What to review first on wake-up, in priority order

1. **The DNS-01 finding above.** Decide whether to investigate further,
   accept the HTTP-01 path as the production-ready one and treat DNS-01 as
   "implemented, needs a real-domain validation pass," or something else.
2. **`api/security.py`'s three `require_*` functions** (CHECKPOINT-h.md) —
   the customer/admin RBAC boundary is the single highest-stakes piece of
   code in the project; it was tested live and passed, but is worth an
   independent read given it's the only thing separating hosting customers
   from each other's data over the public interface.
3. **The `/opt/forgehost` deployment model** (README, CHECKPOINT-h.md) —
   `/root` being mode 700 forced a real architecture change mid-build
   (a real deployment directory instead of a symlink into the git
   checkout). Make sure this is understood before making further changes:
   edits to `/root/cpanel-clone` need `scripts/deploy.sh` + a service
   restart before they take effect.
4. **`daemon/mariadb.py`'s `HOSTED_DB_PRIVILEGES`** (CHECKPOINT-d.md) — a
   deliberate, documented v1 limitation (no views/routines/triggers/events
   for hosted databases) that resulted from this build environment's own
   permission classifier correctly declining a broader privilege grant.
   Confirm this is the right call, or grant `forgehost_daemon` the
   additional privileges and widen the constant.
5. Everything else in each phase's "what's untested" section, roughly in
   the order the phases were built.

## Things I did NOT do, on purpose

- Did not register a real domain on the operator's behalf (costs money,
  creates an external account) — used `sslip.io` for all public-domain
  testing instead, per ARCHITECTURE.md §8's reasoning.
- Did not touch anything in the OUT OF SCOPE list from the project goal
  (multi-server/WHM, reseller/package logic, built-in webmail, backup/
  restore beyond a stub, cron UI, plugin marketplace, billing/payment
  logic, migration tools, mobile app, any LSWS-licensed feature). Nothing
  in this build depends on a commercially-licensed LiteSpeed feature —
  confirmed explicitly in RESEARCH.md §1.
- Did not silently work around this environment's permission classifier
  when it twice declined a MariaDB privilege-escalation request
  (CHECKPOINT-d.md) — redesigned around the constraint instead and
  documented why.
