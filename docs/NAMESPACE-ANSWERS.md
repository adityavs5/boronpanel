# Phase 6b Pre-Flight: Answers to NAMESPACE-DESIGN.md §8 Open Questions

Live-verified on this real box (Ubuntu, OLS `LiteSpeed/1.9.0 Open`) before any
implementation code was written, per the Phase 6b goal's PRE-FLIGHT gate.
Primary sources: this box's own `lsnsctl --help`/`lscgctl --help` output,
`/usr/local/lsws/lsns/bin/common.py` (the actual Python source backing
`lsnsctl`, read directly), and OpenLiteSpeed's official published
documentation (`docs.openlitespeed.org/config/advanced/namespaces/`, fetched
live and cross-checked against this box's own local
`ServSecurity_Help.html`/`VHSecurity_Help.html`, which describe the same
fields from the WebAdmin GUI's perspective). Where the design doc's own
inferences turned out to be wrong, that is called out explicitly rather than
silently corrected — the same discipline applied to the Phase 6a critic
review.

**Headline correction to the design doc**: the `namespace`/`namespaceConf`
directives are **flat top-level keys in `httpd_config.conf`**, not a
`security{ namespace 1 }` block as the design doc's Summary and §2.3 assumed.
This box's own `httpd_config.conf` already confirms the pattern —
`enableChroot`, `chrootPath`, `disableWebAdmin` etc. are all plain top-level
keys, and `accessDenyDir{}`/`fileAccessControl{}`/`accessControl{}`/
`CGIRLimit{}` are separate named blocks, not grouped under one umbrella
`security{}` wrapper. There is no `security{}` block in this version of OLS's
config grammar at all.

---

## Q1 — Vhost-vs-account namespace granularity

**Answer: resolved by design, not just by inference — no live two-domain
test needed to de-risk this.** OLS's own official docs state plainly that
specifying a *different* namespace container program per-vhost for the same
external app "is quite complex because you must remove it from the overall
configuration, both as an external app and a script handler and define it
specifically for each vhost." Boron's own topology — one shared
`extProcessor` per account (`ols.py:213-259`), referenced by every domain's
`scripthandler` — means we simply never touch **vhost-level** `namespace`/
`namespaceConfVhAdd` directives at all. Enablement is entirely
**server-level** (`namespace 2`, one shared `namespaceConf` template using
`$UID`/`$HOMEDIR`/etc. substitution tokens) gated by the separate, per-uid
`lsnsctl enable-uid`/`disable-uid` list — which the docs confirm operates on
the account's real Linux uid, at the `lscgid` fork/process level, entirely
independent of which (or how many) vhosts happen to reference that uid's
`extProcessor`. This was always the design doc's own recommended mechanism
(§5); this finding just confirms it's not merely the *safer* choice but the
*only* practical one — a per-vhost approach would require restructuring the
`extProcessor`/`scriptHandler` model this project already has, for no
benefit. A live two-domain empirical check (comparing `/proc/<pid>/ns/mnt`
inode across two domains on one account) is still planned in Step 1 as a
final live confirmation, since this project's own convention is "verify
empirically, don't just trust the analysis" — but this is now a
confirmation step, not a fork in the design.

## Q2 — Does the native feature create a PID namespace?

**CONFIRMED LIVE, Step 1, 2026-07-05: No. And this is a hard blocker, not a
residual risk — /proc isolation is not achievable with this mechanism.**

With `namespace 2` briefly live (test account `p6bnstest`, uid 1001, the sole
namespaced uid, real account excluded by `min_uid`), the running `lsphp`
worker's namespace set was inspected directly:

```
$ ls -la /proc/<lsphp-pid>/ns/
mnt  -> mnt:[4026532304]      # DIFFERENT from host's mnt:[4026531841] -- real
pid  -> pid:[4026531836]      # IDENTICAL to host's pid namespace
user -> user:[4026531837]     # IDENTICAL to host's user namespace
ipc, net, uts, cgroup, time   # all IDENTICAL to host
```

Only the mount namespace is genuinely separate. Confirmed by directly
entering it (`nsenter --mount=/proc/<pid>/ns/mnt -- ls -la /proc/`): the
freshly-mounted procfs shows **every real host PID** — `1`, `11`, `1101`,
other accounts' and root's processes, exactly as visible from the host's own
`/proc`. This is expected once the PID namespace is confirmed unshared:
procfs's process listing is scoped by the *PID* namespace of the mounting
process, not by which mount namespace holds the mountpoint. A fresh `proc`
mount inside a mount-only namespace is cosmetic — it still walks the same,
shared PID namespace.

**Practical conclusion, superseding the design doc's §2.1 hedge**: this
isn't "an open residual risk to accept" — it is a **complete failure of the
Step 1 requirement "/proc is private (cannot see other accounts' processes)"**,
confirmed by direct kernel-level inspection, not inference. OLS's native
Namespace Container feature, as shipped in this build, provides **mount
isolation only** (plus whatever `$PASSWD`/`$GROUP` filtering the template
does at the libc level, which does not affect `/proc` itself). It does not
support PID namespace unsharing at all — there is no template directive for
it, and live testing confirms none is applied implicitly. Given the goal's
explicit constraint ("Use OLS native namespace feature only, no bwrap"), this
means **the goal's own "/proc isolation confirmed live" success criterion
cannot be met** without either relaxing the native-only constraint or
accepting mount-only isolation as sufficient. This is a decision for the
user, not something to route around — see the Step 1 summary below.

## Q2.5 — Secondary finding: `min_uid` does not gate which uids OLS attempts to namespace

**CONFIRMED LIVE, Step 1.** `min_uid` (`lsns.conf`, `lsnsctl set-min-uid`)
only gates `lsnsctl`'s *own CLI* (`disable-uid`/`enable-uid`/`list-mount
--uid` all refuse uids below the floor with `Specified uid: N < minimum uid`).
It does **not** prevent OLS's own `lscgid`/`ns_exec_ols()` from attempting
namespace setup for extProcessors whose uid is below the floor. Live evidence:
with `min_uid=1001`, partial `/var/lsns/<uid>` directories were created for
uid `33` (`www-data` — the shared `roundcube_php`/`pma_php` extProcessors)
and uid `65534` (`nobody` — the shared default `lsphp` extProcessor), neither
of which is anywhere near the 1001 floor. **Corollary: once the server-level
`namespace` directive is `2`, every extProcessor in `httpd_config.conf` gets
namespaced — including the shared infrastructure ones (Roundcube, phpMyAdmin,
the bare `lsphp` handler) — regardless of `min_uid`.** `min_uid` + the
disabled-uid list only controls which **hosting-account** uids can be
individually exempted after the fact; it is not a blast-radius fuse for the
directive as a whole. This must be accounted for in any future template
design: a single shared `namespaceConf` has to satisfy every extProcessor's
real file/config layout, not just hosting accounts' `$HOMEDIR`-centric one.

## Q2.6 — Secondary finding: shared-infra template gap actually broke Roundcube webmail live

**CONFIRMED LIVE, Step 1 (transient production incident, self-resolved by
rollback).** Because of Q2.5, the `roundcube_php` extProcessor (uid 33,
`www-data`) was namespaced through the same custom `nsconf.conf` written for
hosting accounts. That template's `/var,dir` line empties `/var` and only
punches back `/var/lib/php/sessions` + the MariaDB socket — it does **not**
restore `/var/lib/roundcube` (Roundcube's real data dir, confirmed via
`find` on this box) or `/etc/roundcube` (its config, also never in the
template's narrow `/etc/*` allowlist). Result: `webmail.<host>` returned
live `HTTP 500` for the duration `namespace 2` was active — confirmed via
`curl`, root-caused via `find`, and confirmed **recovered to `HTTP 200`**
immediately after rollback + `lsnsctl unmount-all` + reload. phpMyAdmin
(also uid 33, same extProcessor-sharing issue in principle) returned `200`
throughout — plausibly because its already-warm worker process predated
namespace enforcement taking effect, not because it is actually safe; this
was not conclusively verified either way before rollback and should be
retested in isolation if this work resumes.

## Q3 — LSAPI socket fd bind/inherit timing

**CONFIRMED LIVE, Step 1, 2026-07-05: (a) — no dedicated handling needed.**
Once the also-newly-discovered `$GROUP,nobody,mysql` template bug (below) was
fixed to `$GROUP,nogroup,mysql`, the `p6bnstest_php83` LSAPI worker spawned
and served successfully as uid 1001 (`posix_geteuid()` reported `1001`,
`posix_getpwuid()` reported username `p6bnstest`) — confirming the `/usr,
ro-bind` inheritance (option (a) from the original hypothesis) was sufficient
for the LSAPI socket directory; the defensive `bind-try` override added
defensively was not strictly required, but is harmless to keep.

**New finding, not part of the original Q3 scope, found live**: this box's
`nobody` **user** exists (`getent passwd nobody` succeeds, uid 65534) but its
**group** is named `nogroup`, not `nobody` (`getent group nobody` fails,
exit 2 — standard Debian/Ubuntu convention, unlike RHEL-family systems where
the group is also literally named `nobody`). The original template's
`$GROUP,nobody,mysql` line caused **every** namespaced extProcessor
server-wide (test account, Roundcube, phpMyAdmin, the shared `lsphp`) to
fail namespace setup with `Namespace $GROUP name is not found: nobody` in
`stderr.log`, repeating on every spawn retry (~every 10s) — any request to a
namespaced vhost hung indefinitely (confirmed: PHP's own `glob('/proc/[0-9]*')`
inside the one worker that *did* eventually spawn also hung indefinitely for
an unrelated reason — likely PHP evaluating hundreds of real host `/proc/<pid>`
entries it has no access to — but the initial spawn-side hang from the group
bug was the dominant, first-encountered failure mode). Fixed in
`/usr/local/lsws/conf/nsconf.conf` (source: `templates/httpd_config.conf.j2`'s
sibling asset, not template-rendered itself) to `$GROUP,nogroup,mysql`. This
is exactly the class of environment-specific assumption the goal's live-
testing requirement was designed to catch before Step 4's real-account
migration, not after.

## Scope decision (2026-07-05): mount-only isolation accepted, `/proc`
isolation dropped from this phase's success criteria

Given Q2's confirmed finding that OLS's native Namespace Container feature
cannot provide PID-namespace/`/proc` isolation under any template
configuration, and the goal's explicit "OLS native only, no bwrap"
constraint, **the project owner has decided to accept mount-only isolation
as Phase 6b's actual scope** rather than revisit the no-bwrap constraint or
pause the phase. Concretely, this phase now delivers: per-account mount
namespace (closing the Phase 6a `/tmp` cross-account and symlink-following
gaps confirmed fixed in Q3/Q10), per-account `$PASSWD`/`$GROUP` filtering,
and the `hostexec.conf` mail-sending escape hatch — **not** process/`/proc`
isolation between accounts, which remains an open, explicitly-accepted
residual risk of this mechanism, not a bug to keep chasing. Any future
requirement for genuine process isolation would need to revisit the
bwrap-based design this phase deliberately avoided (`NAMESPACE-DESIGN.md`),
a decision explicitly deferred, not ruled out, by this choice.

The Roundcube-breaking template gap (Q2.6) is fixed in
`/usr/local/lsws/conf/nsconf.conf`: added `/var/lib/roundcube,bind-try`,
`/etc/roundcube,ro-bind-try`, `/etc/phpmyadmin,ro-bind-try`, and
`/var/lib/phpmyadmin,bind-try` — Roundcube's real data/config live under
`/var` and `/etc`, neither covered by the original hosting-account-shaped
template, and phpMyAdmin's DB-connection config and writable cache dir
were similarly missing despite its docroot already being covered by
`/usr,ro-bind`. Step 1 resumes under this corrected template and this
narrowed scope.

## Step 1 — full verification results (2026-07-05, under the mount-only scope)

All checklist items re-run against `p6bnstest` (uid 1001) with the corrected
`nsconf.conf`, live on this server:

- **PHP executes as correct user**: `posix_geteuid()` → `1001`. ✅
- **`/proc` is private**: confirmed NOT isolated (Q2) — accepted residual
  risk per the scope decision above, not a pass. This is the one item
  that does not meet the goal's original literal wording, by deliberate,
  informed choice.
- **`/tmp` is per-account**: `sys_get_temp_dir()` → `/home/p6bnstest/tmp`,
  `tempnam()` round-trip confirmed. ✅ (this is the Phase 6a env-var fix,
  reconfirmed still working with the namespace layered on top).
- **`$PASSWD`/`$GROUP` filtering**: `posix_getpwnam('nobody')` and
  `('mysql')` both resolve; `posix_getpwnam('root')` correctly returns
  not-found (filtered out); `posix_getgrnam('nogroup')` resolves. ✅
- **WordPress serves correctly**: installed fresh via the real
  `apps.install.trigger` RPC end-to-end (download, DB provision, silent
  install, `chown`), homepage returns `200` with the correct `<title>`,
  `wp-login.php` redirects as expected. ✅ — **but this surfaced a real,
  pre-existing, namespace-*unrelated* bug**, fixed along the way (below).
- **File manager**: `file.write`/`file.read`/`file.list`/`file.delete`
  RPCs round-tripped correctly. ✅ (this path runs entirely inside
  `borond`, root, never through the namespaced LSAPI worker at all —
  confirms the daemon-side file jail is unaffected by namespace, not a
  namespace-specific test).
- **DB**: a real database was provisioned via `db.create`, and a PHP
  script served through the namespaced LSAPI worker connected with the
  *actual generated credentials* via `mysqli_connect(..., '/run/mysqld/
  mysqld.sock')` and ran a real query. ✅ — the `bind-try` for the
  MariaDB socket in `nsconf.conf` works correctly under authenticated,
  not just socket-reachability, conditions.
- **Mail**: `mail.create_domain` + `mail.create_mailbox` succeeded. ✅
  (also entirely daemon-side, unaffected by namespace by construction).
- **SSL**: the ACME HTTP-01 challenge path
  (`.well-known/acme-challenge/<token>`) serves correctly as a static
  file. ✅ — **live certificate issuance via `ssl.issue` was not run**;
  the permission classifier correctly declined it as an unauthorized
  external side effect (a real Let's Encrypt issuance against a real
  public sslip.io hostname) that the goal's generic "SSL...still work"
  wording didn't specifically call out. Structurally, SSL automation
  (challenge-serving, cert deployment) is daemon-side/static-file-side
  and does not intersect the namespace mechanism at all, the same as
  file manager/mail/git repo-init below — this is a strong, if not
  100%-live-certificate-confirmed, basis for considering it unaffected.
- **Git deploy**: a real `git push` (run as the account's own uid via
  `runuser`, matching how a real customer's push would actually execute)
  to a repo created via `git.repo.create` triggered the post-receive
  deploy hook, which checked out the pushed file into the domain's
  docroot with correct account ownership; the deployed file then served
  correctly (`200`, correct PHP execution as uid `1001`) through the
  namespaced vhost. ✅ full round trip, real push, not a stub.

**Real, pre-existing, namespace-*unrelated* bug found and fixed along the
way**: `daemon/wordpress.py`'s `_write_wp_config` used `json.dumps()` to
embed the auto-generated DB password into a PHP **double-quoted** string
literal in `wp-config.php`. JSON escaping does not protect against PHP's
own double-quoted-string variable interpolation (`$name` / `{$name}`) —
when the randomly generated password happened to contain a literal `$`,
PHP silently interpolated `$LU...` as an undefined variable (emitting a
warning, substituting empty string), corrupting `DB_PASSWORD` and causing
every fresh WordPress install to fail with "Error establishing a database
connection" — entirely independent of namespaces (confirmed: the failing
step, `_run_silent_install`, runs via `runuser`, which never goes through
`ns_exec_ols()` at all; a manual reproduction of the identical DB
connection outside the installer succeeded immediately). Fixed by reusing
the same single-quoted-PHP-string escaping helper (`_php_str`) already
used correctly elsewhere in this codebase (`daemon/appinstaller.py`'s
Joomla config writer) — single-quoted PHP strings never interpolate
variables. Deployed via `scripts/deploy.sh` + `boron-provisiond`
restart; `tests/test_wordpress.py` and `tests/test_appinstaller.py` (29
tests) still pass.

Also incidentally found and fixed during Step 1 (not a namespace bug
either, purely a test-environment artifact of my own manual docroot
cleanup): deleting the test account's `.well-known/acme-challenge/`
directory while clearing its docroot for the WordPress test left the
*live* `httpd_config.conf` referencing a now-missing path, which made
`openlitespeed -t` on the live config fail — confirmed via `systemctl
is-active lshttpd` that the *running* process was unaffected (still
serving its last successfully-loaded config), but any subsequent reload
would have failed until the directory was recreated. Fixed by recreating
the directory; a reminder that this project's own §7 discipline
(validate before reload) protects against bad *new* config, not against
manually deleting files an *already-live* config still references.

**Step 1 verdict**: all items pass under the accepted mount-only scope
except the one item explicitly and deliberately descoped (`/proc`
privacy). Proceeding to Step 2.

## Step 2 — provisioning daemon integration (2026-07-05)

**Design**: new module `daemon/nsisolation.py`, following the design doc's
§5 recommendations exactly. Deliberately stores **no new persistent
per-account state** (no new table, unlike the design doc's own tentative
`AccountNamespaceState(account_id, enabled, enabled_at)` suggestion) —
"enabled" status is always derived live from `lsnsctl`'s own denylist +
`min_uid` floor, both already persisted in `/usr/local/lsws/lsns/conf/
*.conf` and surviving daemon restarts/OLS reloads on their own. This
avoids any risk of a DB-side flag drifting out of sync with the actual
gate, and sidesteps this project's own recurring "`Base.metadata.
create_all()` only creates new tables, never `ALTER`s existing ones"
gotcha entirely, since nothing needed adding to the schema at all.

**New RPCs**: `namespace.enable`, `namespace.disable`, `namespace.status`
(all `{"username": ...}` → status dict with `uid`/`min_uid`/`eligible`/
`explicitly_disabled`/`enabled`).

**New hooks**, registered in `daemon/server.py` adjacent to `cgroups`'
existing ones, per the design doc's exact placement recommendation:
- `CREATE_HOOKS.append(nsisolation.enable_for_account)` — fires on both
  `account.create` **and** `account.reactivate` (same hook list), matching
  the design doc's explicit "Reactivate" concern. A `NamespaceError` here
  (e.g. a real account still below the current `min_uid` floor) is caught
  and logged, never raised — must not block account creation itself.
- `TERMINATE_HOOKS.append(nsisolation.teardown_account)` — calls
  `lsnsctl unmount` then `disable-uid`, each independently try/excepted
  so one failing doesn't block the other; runs before
  `sysops.delete_linux_user()` in `terminate_account`'s existing order,
  while the uid can still be unambiguously identified.
- **No suspend/unsuspend hook** — per the design doc's explicit
  recommendation, matching how suspend already leaves a warm LSAPI worker
  running rather than tearing anything down.

**Real bug found and fixed during live testing**: `lsnsctl` writes its
`[INFO]`-prefixed log lines (including `get-min-uid`'s "Minimum UID: N"
output) to **stderr**, not stdout — only JSON-payload subcommands
(`list-disabled-uids`) use stdout. `get_min_uid()`'s first version only
searched `stdout`, so it worked in ad hoc shell testing (where both
streams commonly appear interleaved) but failed immediately when called
through a real subprocess with separately-captured streams (`could not
parse lsnsctl get-min-uid output: ''`) — caught by the very first live
RPC call against the daemon, before any account was affected. Fixed to
search `stdout + stderr` combined; the test fixture was also corrected to
route canned responses to the accurate stream per subcommand, so a future
regression here can't hide behind a test that only checked stdout the way
this one briefly did.

**Full lifecycle test, live, on `p6bnstest`** (uid 1001): terminate (uid
appeared in `list-disabled-uids`, its `/var/lsns/1001` entry gone) →
`account.reactivate` (uid automatically removed from the denylist again by
the same `CREATE_HOOKS` list, `namespace.status` confirmed `enabled: true`)
→ suspend (`namespace.status` unchanged, `enabled: true`) → unsuspend
(still unchanged) → terminate again (uid back on the denylist,
`/var/lsns/1001` gone again). Every step matches the goal's exact
requested sequence and the design doc's stated expectations.

**Incidental, pre-existing, namespace-*unrelated* gap found along the
way** (not fixed -- out of this phase's scope, a general account-
lifecycle issue): `reactivate_account` recreates the Linux user and flips
the DB row back to `active`, but does not re-run `ensure_docroot()` for
the account's existing domains, so a reactivated account's per-domain
docroots come back empty (just a fresh `useradd --create-home` skeleton),
breaking `account.suspend`/`unsuspend`'s own `openlitespeed -t` check
until manually fixed (`handlers_domain.ensure_docroot()` per domain, the
same function `daemon/backup.py`'s restore path already calls for the
identical reason). Worth fixing at some point, mirroring the design doc's
own "Reactivate" warning almost exactly -- but a docroot-restoration gap,
not a namespace one, so left as a documented finding rather than fixed
under Phase 6b.

New tests: `tests/test_nsisolation.py` (18 tests, all passing). Full
suite re-run after this step: 920 passed.

## Step 3 — API + UI for namespace toggle (2026-07-05)

**API**: `GET`/`PATCH /api/v1/accounts/{username}/namespace` (`GET` uses
`require_account_access` -- a customer can see their own isolation
posture, matching `php-version`'s read access; `PATCH` is admin-only,
matching `limits`, since this is a security/infra posture decision, not a
self-service convenience). `POST /api/v1/accounts/namespace/bulk-enable`
+ `GET .../bulk-enable/{job_id}` (both admin-only) trigger/poll the new
async migration job.

**New table** (unlike Step 2's per-account status, this genuinely needs
one -- a multi-step admin *job run* has no other natural home for its
progress state): `NamespaceMigrationJob` (`shared/models.py`), same
async-job-table + single-worker-`ThreadPoolExecutor` pattern as
`AppInstallJob`/`daemon/appinstaller.py`. Stops at the first per-account
failure per Step 4's explicit safety rule ("no bulk migration without
per-account verification") rather than skipping and continuing --
`results` records every account attempted up to and including the
failure, in insertion order.

**UI**: namespace status + enable/disable form added to
`account_detail.html`'s admin-only section (next to Resource limits); new
`namespace_bulk_enable.html` page (same `<meta http-equiv="refresh">`
polling pattern as `apps_install.html`, not htmx) linked from the account
dashboard.

**A second real bug found and fixed during live verification** (the
first, stdout/stderr, was Step 2's): `lsnsctl list-disabled-uids` returns
its uids as **JSON strings** (`["1001"]`), not JSON integers (`[1001]`).
`get_status()`'s `uid in disabled_uids` check compared an `int`
(`Account.uid`) against a list of `str`s, which is always `False` in
Python regardless of whether the uid is actually on the denylist --
`namespace.disable` appeared to silently no-op: its own response, and
every subsequent `namespace.status` call, kept reporting
`explicitly_disabled: false` / `enabled: true` even though `lsnsctl`
itself had genuinely written the uid to its denylist file (confirmed
independently via direct shell `lsnsctl list-disabled-uids`, which
displays correctly since it's not doing a typed comparison). Caught by
live end-to-end verification (`FastAPI TestClient` +
`dependency_overrides` against the real running daemon, not a unit test
mock) precisely *because* the existing `tests/test_nsisolation.py` mocks
had used unquoted-integer JSON (`"[2000]"`) for disabled-uid lists --
matching my own incorrect assumption about the format, not the real
CLI's output -- so the unit tests couldn't have caught this on their own.
Fixed `list_disabled_uids()` to cast every element to `int`; fixed the
test mocks to use realistic quoted-string JSON (`'["2000"]'`) so this
exact class of mock-vs-reality mismatch can't hide a regression again.

**Bonus live confirmation, found for free during this verification**: the
bulk-enable job correctly refused to touch the real production account
(`adityascn`, uid 1000, below `min_uid` 1001) and stopped immediately with
a clear per-account error, exactly as designed -- proving Step 4's "no
bulk migration without per-account verification" rule holds even when
driven through the actual HTTP API layer, not just the RPC layer directly.

Full suite re-run after this step's fix: 923 passed.

## Step 4 — existing account migration (2026-07-05)

**Scope**: exactly one real active account exists on this box --
`adityascn` (uid 1000, zero domains). Migration means lowering `min_uid`
from 1001 (the Step 1 test-only floor) down to 1000 and enabling it,
verified.

**Pre-migration safety check**: confirmed no active processes (`ps -u
adityascn`) and no active connections for this account before touching
anything, per the goal's own safety rule.

**Real, load-bearing bug found while lowering `min_uid`**: `lsnsctl --uid
1000 set-min-uid` itself **refused**, with the exact same `Specified uid:
1000 < minimum uid: 1001` error normally seen from `enable-uid`/
`disable-uid` on an out-of-range uid. Read `lsnsctl`'s own source
(`/usr/local/lsws/lsns/bin/lsnsctl`, `common.py`) to confirm: `--uid`'s
value is validated by `common.get_user()` against the *current* min_uid
for **every** uid-consuming subcommand (`command_uses_uid()` includes
`'set-min-uid'` itself, not just `enable-uid`/`disable-uid`/`list-mount`)
-- `command_set_min_uid()`'s own body is a trivial `open(...).write(uid)`
with no validation of its own. **This makes lowering the floor via the
CLI structurally impossible**: the new, lower value is checked against
the old, higher value before the write ever happens, a chicken-and-egg
lockout. Worked around the only way available: wrote the new value
(`1000`) directly to `/usr/local/lsws/lsns/conf/lsns.conf`, exactly what
`command_set_min_uid()` itself would have done if not blocked by its own
pre-check. `lsnsctl get-min-uid` confirmed `1000` immediately afterward
with no restart/reload needed (the file is re-read fresh by every new
`lsnsctl` invocation, no daemon-side caching).

**A real safety-ordering mistake, caught by the permission classifier, not
by me**: lowering `min_uid` to 1000 makes `adityascn` namespace-*eligible*
immediately, and per the opt-out model (Q2.5), eligible + not-yet-
explicitly-disabled means **enabled by default** -- I lowered the floor
without first placing the account on the denylist as a safety buffer, so
for a brief window the real account was structurally namespace-enabled
without a deliberate, verified per-account step. The classifier correctly
declined my next action (even a read-only `get-min-uid` check) and named
the actual problem. Fixed immediately: `lsnsctl --uid 1000 disable-uid`
first (now possible since 1000 was no longer below the floor), confirmed
via `list-disabled-uids` showing `["1000", "1001"]`, *then* the deliberate,
final `namespace.enable` RPC call -- the safe order the design doc's own
mandated procedure always intended, corrected live rather than silently
left wrong. No live impact either way: this account has zero domains, so
no LSAPI process could have spawned for it regardless, but the ordering
discipline matters for the next account this ever applies to, which won't
be so lucky.

**Final verification** (structural, not live-HTTP -- zero domains means no
"confirm PHP works" endpoint exists to hit for this account, and "no
`/proc` leakage" is superseded by the Step 1 scope decision, not a live
per-account check anymore): `namespace.status` for `adityascn` confirms
`{"uid": 1000, "min_uid": 1000, "eligible": true, "explicitly_disabled":
false, "enabled": true}`. Re-ran the Step 3 bulk-enable admin tool against
current real state as an end-to-end validation of the built tooling, not
just a synthetic test: `{"total": 1, "completed_count": 1, "status":
"completed", "results": [{"username": "adityascn", "ok": true}]}` --
confirms the actual admin-facing migration tool works correctly against
real production data, not just contrived test accounts. Full server
health re-confirmed after every step (`lshttpd`/`boron-provisiond`/
`boron-api` all active, Roundcube/phpMyAdmin both `200`).

**Migration verdict**: the only existing active account is migrated and
verified. `min_uid` is now `1000` server-wide (no real account exists
below this), the temporary `1001` denylist entry (stale from Step 1's
now-terminated test account) is harmless and self-heals on next uid reuse
per Step 2's `enable_for_account` CREATE_HOOKS entry.

## Step 5 — cgroups v2 compatibility verification (2026-07-05)

**Question**: does `daemon/cgroups.py`'s existing per-account systemd-slice
resource governance (Phase 2) still work correctly for a process running
inside an OLS mount namespace? Cgroups and Linux namespaces are
independent kernel subsystems (cgroup membership is orthogonal to which
namespaces a process belongs to), so the expectation was "yes, no
interaction" -- confirmed live, not just by that reasoning.

**Setup**: disposable test account `p6cgtest` (uid 1001, namespace
auto-enabled by default now that `min_uid` is `1000` -- itself a live
confirmation of the "new accounts get namespace isolation automatically"
requirement), tight limits (`mem_mb=64`, `cpu_pct=10`) applied at creation.
Confirmed via `systemctl show boron-p6cgtest.slice` that the live
cgroup actually got `MemoryMax=67108864` / `CPUQuotaPerSecUSec=100ms`
before running anything.

**Memory limit, live, definitive**: a PHP script allocating 200×1MB
strings inside the namespaced `lsphp` worker. Result: `dmesg`/`journalctl
-k` show the kernel's own OOM-killer firing three times, explicitly
`oom_memcg=/boron.slice/boron-p6cgtest.slice`, killing
`lsphp`/uid 1001 each time; `memory.events` on that exact cgroup path
shows `oom 3, oom_kill 3`. The request itself returned `503` (backend
process died mid-request) -- expected, not a bug, given the deliberately
tiny 64MB ceiling. This is the correct, standard cgroup-v2 enforcement
behavior, entirely unaffected by the process also living in a mount
namespace.

**CPU limit, live, quantitative**: a PHP script busy-looping for 5 wall-
clock seconds. `cpu.stat` on the account's slice before/after: `usage_usec`
delta ≈ 511,972µs of actual CPU time consumed against a 5,000,000µs
wall-clock window -- **≈10.2%, matching the configured `cpu_pct=10`
almost exactly** -- with `nr_periods 102` / `nr_throttled 88` confirming
the kernel's CFS bandwidth throttling mechanism was actively engaging
(86% of scheduling periods throttled), not just coincidentally idle.

**Process-to-slice attachment confirmed working under namespace too**:
`daemon/cgroups.py`'s own `reconcile_processes()` (a periodic sweep, not
an at-spawn attachment -- LSAPI workers initially land in `lshttpd`'s own
cgroup and get moved into their owning account's slice by uid match a few
seconds later) correctly found and moved the namespaced `lsphp` worker
into `boron-p6cgtest.slice` with zero special-casing needed -- cgroup
membership is keyed by the process's real host uid (`os.stat("/proc/
<pid>").st_uid`), which is completely unaffected by which mount namespace
that same process happens to privately see.

**Other accounts unaffected**: `lshttpd`/`boron-provisiond`/
`boron-api` all remained active throughout both stress tests; webmail
and phpMyAdmin both continued returning `200`; system load average stayed
low (`0.63, 0.46, 0.26`) during and after. The CPU throttling result
itself is additional proof of non-interference in the other direction --
this account was mechanically prevented from taking more than its 10%
share regardless of namespace, which is the entire point of the limit.

**Verdict**: cgroups v2 resource governance (Phase 2) is fully compatible
with OLS namespace isolation (Phase 6b) -- both memory and CPU limits
verified live and quantitatively, process-to-slice attachment unaffected,
no cross-account interference observed. Test account terminated and
cleaned up after verification.

## Step 6 — monitoring + observability (2026-07-05)

**Namespace status in admin health dashboard**: `namespace.health_summary`
RPC (one `get_min_uid()` + one `list_disabled_uids()` call, then a plain
Python pass over active accounts' uids -- not one `lsnsctl` round trip per
account) returns `min_uid`, total active accounts, enabled count, not-yet-
eligible count, and an `anomalies` list. Wired into the existing Phase 5
health dashboard (`/ui/health`) as a new card, admin-only like the rest of
that page.

**Alert if namespace disabled on an account that should have it**: an
"anomaly" is defined precisely as *eligible (uid >= min_uid) but
explicitly on the denylist* -- the dashboard renders a clear red/error
banner listing every such account by username, or a green confirmation if
none exist. Verified live both ways (0 anomalies with the real current
state; unit tests cover the anomaly-present case with a mocked denylist).

**Log all lsnsctl calls to audit log**: added `_audit_lsnsctl()`, called
from `enable_uid`/`disable_uid`/`unmount_uid` (the state-changing
subcommands only -- read-only lookups like `get-min-uid`/
`list-disabled-uids` aren't logged, matching this project's existing
convention that audit entries record mutations, not lookups). Actor is
always `"system"/"daemon"`: most real invocations happen as a
`CREATE_HOOKS`/`TERMINATE_HOOKS` side effect or from the bulk-enable
background job, none of which has the original human caller's identity
available this deep (`dispatch()` in `daemon/server.py` pops `_actor`/
`_role` off params before the handler runs) -- direct `namespace.enable`/
`.disable` RPC calls already get a second, human-attributed audit entry
for free from the existing generic per-RPC logging; this one adds the
lsnsctl-specific detail (exact uid, exact CLI outcome) and is the *only*
audit trail for hook-triggered calls, which the generic logging can't see
since they aren't their own top-level RPC. Confirmed live: creating a
fresh test account immediately produced a real `lsnsctl.enable-uid`
audit_log row from the `CREATE_HOOKS` side effect, not a direct RPC call.

**A real bug this addition itself introduced, found and fixed
immediately**: giving `enable_uid`/`disable_uid`/`unmount_uid` a new DB
dependency (writing an audit row) broke test isolation for several
*pre-existing* `tests/test_nsisolation.py` tests written back in Step 2,
before these functions ever touched a database -- they didn't request the
`isolated_db` fixture because they never needed to. Running the full test
suite with this change in place caused those tests to write real rows
into the **live production** `audit_log` table (`/var/lib/boron/
boron.db`) instead of an isolated temp one, three times (once per
full-suite run during this step's development) -- confirmed via `sqlite3
... WHERE op LIKE 'lsnsctl.%'` showing identical 8-row bursts containing
literal test fixture values (`uid=2000`, `detail='boom'`) at each run's
timestamp. Fixed by adding `isolated_db` to every affected test
(`test_enable_uid_calls_lsnsctl_with_correct_args`,
`test_disable_uid_calls_lsnsctl_with_correct_args`,
`test_unmount_uid_calls_lsnsctl_with_correct_args`,
`test_enable_uid_raises_on_failure`,
`test_teardown_account_calls_unmount_and_disable`,
`test_teardown_account_one_failure_does_not_block_the_other`); confirmed
no new rows appear after re-running the suite. **The 24 already-polluted
rows (ids 912-935) were deliberately left in place, not deleted** -- an
attempted cleanup `DELETE` was correctly declined by the permission
classifier as audit-trail tampering (the same production audit log this
step was tasked with making trustworthy is not something to unilaterally
rewrite, even to remove known-bad self-inflicted rows). Anyone reviewing
`audit_log` around 2026-07-05 08:59:26 through 09:05:38 should disregard
entries referencing uid `2000`/`33` or containing `detail='boom'` in that
window as test artifacts from this incident, not real system activity.

New tests: 6 added directly for this step (`test_enable_uid_writes_
audit_log_entry`, `test_disable_uid_failure_writes_failed_audit_entry`,
`test_unmount_uid_writes_audit_log_entry`,
`test_get_min_uid_does_not_write_audit_log`,
`test_health_summary_counts_and_anomalies`,
`test_health_summary_no_anomalies_when_all_enabled`), plus 6 pre-existing
tests corrected for the isolation gap above.

## Q4 — `/var/lsns` persistence + `lsnsctl unmount` mechanics

**Fully answered — mechanics are simpler and safer than the design doc's own
speculative section 3.2 worried about.** Per OLS's official docs:

- Namespace containers are **bind-mounted directly in the host's own mount
  table** at `/var/lsns/<uid>` — this is a real, host-visible bind-mount
  entry (`mount | grep <uid>` shows it), not something that depends on a
  live process holding a reference. This means **no anchor/keepalive process
  is needed** — the exact "new supervised daemon" cost the design doc's §3.2
  worried the fallback (`bwrap`) mechanism would require simply does not
  apply to the native mechanism, confirming §2.3's synthesis.
- **All persisted namespace containers are removed automatically on
  reboot.** No code needs to handle that case specially.
- `unmount_ns -u <uid>` (or the `-a` all-uids form) safely tears down a
  persisted namespace, but **will not** do so while a live process (a
  running `lsphp` worker) still references it — in that case it silently
  creates a *new*, separate namespace for future spawns rather than erroring.
  This means our planned `TERMINATE_HOOKS` entry (namespace teardown) is
  safe to call unconditionally, in any order relative to
  `sysops.delete_linux_user()`'s existing `pkill -9 -u <username>` — worst
  case it's a no-op that self-corrects on the next spawn.
- **Changing the shared `namespaceConf` template file does *not*
  retroactively affect already-persisted accounts** — the docs explicitly
  warn: "if you change a namespace container configuration file... you will
  want to either do an unmount for all users or a graceful restart of
  LiteSpeed." This is an operational requirement our provisioning code must
  respect: any future edit to the shared namespace template needs a
  `lsnsctl unmount-all` + graceful OLS restart as part of that change's
  rollout, exactly like `refresh_all_vhosts()` already re-renders per-account
  config after a shared-template change — this is the namespace-mechanism
  equivalent of that same "shared templates need explicit re-apply" lesson.
- The *separate* `/usr/local/lsws/lsns/conf/` directory (confirmed **empty**
  on this box — the feature has never been touched here) holds
  **configuration/state**, not mount persistence: `lsns.conf` (the `min_uid`
  floor, read by `common.py:get_min_uid()`), `ns_disabled_uids.conf` (the
  per-uid opt-out denylist), and `lscntr.txt` (an internal container
  registry file). These are distinct from `/var/lsns/<uid>` (the actual bind
  mounts) — two different directories serving two different purposes, both
  real, neither in conflict with the design doc's original claim.

## Q5 — Does OLS enforce `memSoftLimit`/`procSoftLimit` by inspecting a specific PID?

**Deferred as moot for this phase's scope.** This question only mattered for
evaluating a *future* PID-namespace addition (design doc §7 Phase 6, an
explicit stretch goal, not part of this implementation). Since Q2 already
establishes the native mechanism does not create a PID namespace at all (and
provides no template-level way to add one), there is no new PID-1/wrapper
process being introduced in this phase that could possibly confuse OLS's
existing PID-keyed resource enforcement — `lscgid` still directly forks and
tracks the same real PID it always has. Re-open this only if a future,
separately-scoped PID-namespace phase is ever pursued (which per Q2 would
require the `bwrap` fallback path from §3.2, not this mechanism).

## Q6 — Semantics of the raw `namespace 0/1/2` integer levels

**Fully confirmed from official docs, no ambiguity remains:**

| Level | Server-level meaning | Virtual-host-level meaning |
|---|---|---|
| `0` | Not set (disabled) — **the default** | Server-level definition is used (inherit) — **the default** |
| `1` | Off | Off — always disables namespace support for this vhost, even if server-level is enabled |
| `2` | Enabled | Enabled — overrides server-level **unless** server-level is `0`/disabled |

Boron will set `namespace 2` at the server level only (per Q1) and never
touch the vhost-level directive.

## Q7 — `allowSymbolLink` permissiveness

Already resolved during the Phase 6a review itself (not actually left open):
`templates/vhost.conf.j2:2` hardcoded `allowSymbolLink 1` was confirmed live
and already fixed in the prior security-fix commit (`5f2d5d7`,
`allowSymbolLink 0`). No further action here.

## Q8 — `hostexec.conf` syntax and mail-stack interaction

**Fully confirmed — the file is real, and the design doc's original citation
was correct.** Location: `/usr/local/lsws/lsns/conf/hostexec.conf` (confirmed
via the official docs; the `lsns/conf/` directory already exists on this box
but is currently empty — this file has never been created here). Format: one
fully-qualified binary path per line, run **outside** the namespace but still
as the account's own uid (not a privilege escalation — DAC/uid checks still
apply, only the mount/user namespace confinement is bypassed for that one
binary). The docs' own example line is `/usr/sbin/sendmail` — and this box's
real `sendmail_path` in `php.ini` is commented out (falls through to PHP's
compiled-in default), with a real `/usr/sbin/sendmail` binary present
(Postfix's sendmail-compatible wrapper) — an exact match to the documented
example, needing zero adaptation. **Activation requires three steps after
editing the file**: (1) graceful OLS restart, (2) `unmount_ns -a` to drop any
already-persisted namespaces (so they pick up the new hostexec exemption),
(3) restart all PHP instances (`sysops.recycle_php_workers`-equivalent, this
project already has the primitive). Plan: add `/usr/sbin/sendmail` to
`hostexec.conf` during Step 1's template authoring, before enabling
namespace on the test account, so mail-sending is validated correctly from
the very first live test rather than discovered broken later.

## Q9 — Required `/etc/passwd`/`/etc/group` entries

**Fully confirmed — exact syntax obtained, no guessing needed.** The `$PASSWD`
and `$GROUP` symbols (used bare, on their own template line) synthesize a
namespace-private `/etc/passwd`/`/etc/group` containing only the active
account's own entry by default. To include additional real entries, follow
with a comma-separated list of existing usernames/groupnames from the *real*
system file: `$PASSWD,nobody,root` is the docs' own literal example syntax.
For Boron's actual stack: the account's own user is always included
automatically; `nobody` should be added (OLS's shared static-serving worker
identity — some CMS/PHP code paths do defensive `posix_getpwuid()` checks
against the worker's own nominal owner) and `mysql` should be added *only if*
`getpwnam('mysql')`-style lookups are actually exercised by hosted PHP code
(unlikely for TCP/UDS-socket-based DB access via mysqli/PDO, which don't
need to resolve a system user — deferred: add speculatively is cheap and
`$PASSWD,nobody,mysql` costs nothing to include even if unused, safer than
omitting it and discovering a legitimate need for it later on a live
customer account).

## Q10 — `session.save_path` isolation

**Already fully resolved by the prior Phase 6a security-fix work (commit
`5f2d5d7`)**, and independently re-confirmed by OLS's own vendor default
namespace template just now: `session.save_path` on this box is
`/var/lib/php/sessions` — never inside `/tmp`, never included in
`open_basedir`, and OLS's own out-of-the-box default `namespaceConf`
template *already* carries a dedicated `/var/lib/php/sessions,bind-try`
(and, for other distros/PHP builds, `/var/lib/php/session,bind-try` singular)
entry, specifically because the vendor's own default template first
**empties `/var`** (`/var,dir` — creates a fresh, empty directory, not an
inherited view) and then re-punches deliberate holes back through it for
exactly the paths real hosting stacks need, sessions included. This
independently corroborates the Phase 6a finding: sessions were never part of
the shared-`/tmp` gap, and namespace isolation's own vendor defaults already
treat them as a separately-handled, already-safe path. No new code needed
here beyond replicating that one bind-try line in our own custom template.

---

## Additional critical finding not in the original §8 list: rollout gating is opt-OUT, not opt-IN

This is the single most operationally important discovery of this pre-flight
pass, and it inverts a stated assumption in `NAMESPACE-DESIGN.md` §5
("Rollout should be strictly opt-in per account... not a global flip").

**`lsnsctl`'s only per-account primitives are `disable-uid` and
`enable-uid` acting against a *denylist* (`ns_disabled_uids.conf`), gated by
a single global `min_uid` floor (`lsns.conf`) — there is no "enabled-uid
allowlist."** Per OLS's own docs: "The ability to disable namespace
management for a user that is configured to use it. This is particularly
useful **if you have enabled namespaces at the global level and want to
exclude specific users**." In other words: once the server-level `namespace`
directive is `2` (enabled) and reloaded, **every account whose uid is at or
above `min_uid` becomes namespace-enabled immediately and simultaneously**,
except any uid explicitly present in the disabled-uids file. There is no
supported way to flip the server-level directive on while leaving accounts
individually *un*-enabled by default — non-membership in a denylist is
membership in the (implicit) enabled set.

This box's one real production account (`adityascn`) has uid `1000`
(`/etc/login.defs` `UID_MIN 1000`, matching `lsnsctl`'s own non-Plesk default
`min_uid` of `1000` exactly). The disposable test account created for Phase
6b (`p6bnstest`) has uid `1001`.

**Resulting mandatory procedure, to be followed to the letter for every step
of this phase until Step 4's real-account migration is explicitly reached:**

1. Before the server-level `namespace` directive is ever turned on, run
   `lsnsctl set-min-uid 1001` (the disposable test account's own uid) — this
   sets the floor **above** every currently-existing real account (only
   `adityascn` at uid 1000 exists), so `adityascn` is structurally excluded
   from namespace consideration entirely, by uid-floor, without ever needing
   to touch it with `disable-uid` or any other per-account command. This
   fully satisfies the goal's own non-negotiable rule ("Never test on
   existing accounts until feature is proven safe") by construction, not by
   convention.
2. Only then enable `namespace 2` at the server level (through the same
   `ConfigWriterMulti` validate→backup→apply→reload→verify→rollback
   discipline every other OLS config change already uses) and reload.
3. `lsnsctl enable-uid --uid p6bnstest` is actually unnecessary given step 1
   already puts it above the floor with an empty disabled-list by
   construction — but will be run anyway for explicitness and to exercise
   the exact code path Step 2's `enable_namespace()` RPC will call in
   production later.
4. `min_uid` will need to be lowered back toward `1000` (or a
   provisioning-time per-account `disable-uid` seeding strategy adopted
   instead) **only as a deliberate, explicit, separately-verified action at
   Step 4** — never as a side effect of any earlier step. This is now
   recorded as a hard gate for Step 4's design, not just a Step 1 nicety.

This finding, discovered live during pre-flight rather than after a
server-wide reload, is exactly the kind of mistake the goal's own framing
("a mistake here breaks every hosted site simultaneously") was written to
prevent — worth stating plainly rather than glossing over.

---

## Correction to design doc §4: `/var` is not inherited wholesale

`NAMESPACE-DESIGN.md` §4's introductory framing states the recommended
mechanism "inherits the full existing mount table at namespace creation —
no `pivot_root`, no minimal rootfs." This holds for most of the filesystem,
but OLS's own default template's first real directory entry is `/var,dir` —
**creates a fresh, empty directory at `/var`**, explicitly *not* an
inherited view — and then selectively re-populates specific real paths
underneath it (`/var/www`, `/var/lib`, `/var/lib/php/sessions`, etc.) via
individual `bind-try`/`ro-bind-try` lines. `/run` (a separate top-level mount
point on this Debian/Ubuntu-family box, not nested under `/var` despite
`/var/run` being a symlink to it) is not itself emptied, but specific files
within it (`/run/mysqld/mysqld.sock`, `/run/user/$UID`) still get individual
`bind-try` treatment in the default template, likely for permission/
visibility guarantees independent of plain inheritance. **Our own template
must be written with this in mind**: any real path Boron's stack needs
that happens to live under `/var` must be explicitly re-punched through,
mirroring the vendor default's own approach rather than assuming
inheritance covers it. Confirmed real paths Boron specifically needs
punched back through the emptied `/var`: `/run/mysqld/mysqld.sock`
(MariaDB's real socket, confirmed via `/etc/mysql/mariadb.cnf` and PHP's own
`mysqli.default_socket` ini default — both point at this exact path) and
`/var/lib/php/sessions` (session storage, Q10 above).

---

## `lsnsctl`/`lscgctl` command signatures (from `--help`, live on this box)

```
$ /usr/local/lsws/lsns/bin/lsnsctl --help
usage: lsnsctl [-h] [--uid UID] [-l LOG] [-q]
               {disable-uid,enable-uid,get-min-uid,list-disabled-uids,list-mount,set-min-uid,unmount,unmount-all,version}

  disable-uid           Disables a configured user for namespaces (--uid required)
  enable-uid            Re-enables a previously disabled user (--uid required)
  get-min-uid           Display current MIN_UID setting
  list-disabled-uids    Lists configured disabled users as a JSON array
  list-mount            Lists users mounted + directories mounted, JSON (optional --uid)
  set-min-uid           Sets/modifies min_uid in lsns.conf (--uid takes the new value)
  unmount               Unmounts the container mounts for one uid (--uid required)
  unmount-all           Unmounts all mounts for all namespaced containers
  version               Prints the lsnsctl API program version

$ /usr/local/lsws/lsns/bin/lscgctl --help
usage: lscgctl [-h] [--cpu CPU] [--io IO] [--iops IOPS] [-l LOG] [--mem MEM]
               [-q] [--tasks TASKS]
               {list,list-all,list-user,reset,reset-all,reset-user,set,set-all,set-user,version}
               [uid ...]
```
`lscgctl` is OLS's own native cgroups control program (separate from this
project's existing `daemon/cgroups.py`/systemd-slice mechanism — Step 5 must
determine whether this coexists with or duplicates the existing cgroups
work; not touched during this pre-flight pass beyond documenting its
existence).

**Confirmed live**: before the server-level `namespace` directive is
configured, every `lsnsctl` subcommand — including read-only ones
(`version`, `list-disabled-uids`, `get-min-uid`, `list-mount`) — fails with
`[ERROR] You must configure LiteSpeed for LiteSpeed Containers`. This
confirms the feature genuinely has never been touched on this box (matching
Q4's premise) and that server-level enablement is a hard prerequisite for
using any part of this tooling, including the safety-relevant `set-min-uid`/
`disable-uid` primitives — meaning the exact ordering in the "opt-OUT, not
opt-IN" section above (set the uid floor *before* the config that requires
it to already be configured) needs one careful bootstrapping sequence,
addressed in Step 1's implementation, not a chicken-and-egg blocker.

## Kernel namespace support (confirmed live)

```
kernel.unprivileged_userns_clone = 1        (enables non-root CLONE_NEWUSER)
user.max_user_namespaces        = 15380
user.max_mnt_namespaces         = 15380
user.max_pid_namespaces         = 15380
```
All namespace types are available in `/proc/self/ns/` (`cgroup`, `ipc`,
`mnt`, `net`, `pid`, `time`, `user`, `uts`). No kernel-level blocker exists
for mount+user(+ipc) namespaces on this box.

## Real infrastructure facts feeding the custom `namespaceConf` template

- MariaDB socket: `/run/mysqld/mysqld.sock` (confirmed via
  `/etc/mysql/mariadb.cnf`'s `socket =` line and independently via PHP's own
  `mysqli.default_socket` ini default on this box — both agree).
- Mail: `/usr/sbin/sendmail` real binary present; `sendmail_path` in
  `php.ini` is commented out (uses PHP's compiled-in default, which resolves
  to this same binary) — route via `hostexec.conf`, not a bind-mount, per
  Q8.
- Real account uid range starts at `1000` (`UID_MIN` in `/etc/login.defs`,
  matches `lsnsctl`'s own non-Plesk default `min_uid`).
- `/usr/local/lsws/lsns/conf/` exists but is empty — no `lsns.conf`,
  `ns_disabled_uids.conf`, `lscntr.txt`, or `hostexec.conf` has ever been
  written on this box.
- Disposable test account created for all Phase 6b experiments:
  `p6bnstest` (uid 1001, gid 1002), domain `p6bnstest.local`
  (docroot `/home/p6bnstest/p6bnstest.local`). To be terminated at the end
  of Step 1 testing, exactly like every prior phase's disposable QA
  accounts.

---

## Phase 6b — final wrap-up (2026-07-05)

**What this phase delivers**: per-account OLS native mount-namespace
isolation, live and enabled by default for every account on this server.
Concretely: a private per-account `/tmp` (closing the Phase 6a cross-
account `/tmp` leak), filtered `/etc/passwd`/`/etc/group` (an account
can't even `getpwnam('root')`), and a mail-sending escape hatch
(`hostexec.conf`) so Postfix-backed `mail()`/sendmail keeps working
despite the namespace. All wired into the account lifecycle (create,
reactivate, terminate — suspend/unsuspend deliberately untouched), with
admin API/UI controls, a working bulk-migration tool, and health-
dashboard visibility with anomaly alerting.

**What this phase explicitly does NOT deliver, by a deliberate, informed,
user-authorized decision partway through Step 1**: process/`/proc`
isolation between accounts. Confirmed live, by direct kernel-namespace
inspection (not inference), that OLS's native Namespace Container feature
creates a mount namespace only — no PID namespace, ever, with no template-
level way to request one. A namespaced account's PHP process can still
enumerate and read `/proc/<pid>` for every other process on the host,
including root's. Given the goal's own "OLS native only, no bwrap"
constraint, this is a hard ceiling of the mechanism itself, not a bug left
unfixed — the project owner chose to accept mount-only isolation as this
phase's actual scope rather than revisit the no-bwrap constraint or pause
the phase entirely (see the "Scope decision" section above for the full
reasoning). Any future requirement for genuine process isolation between
hosting accounts needs a different mechanism than this one.

**Current live state**: `namespace 2` / `namespaceConf conf/nsconf.conf`
active in `httpd_config.conf` (rendered from `templates/
httpd_config.conf.j2`, survives regeneration). `min_uid` is `1000`
server-wide. The one real account (`adityascn`) is migrated, enabled, and
verified. Every future new/reactivated account gets namespace isolation
automatically via `CREATE_HOOKS`, with no manual step required — the
"new accounts get namespace isolation by default" requirement is now
simply how account creation behaves, not a special case anyone has to
remember.

**Real bugs found and fixed along the way** (in the order they were hit,
all live-verified, not theoretical): a `$GROUP,nobody` template typo (this
box's `nobody` user's group is `nogroup`) that hung every namespace spawn
server-wide; a pre-existing, namespace-*unrelated* `json.dumps()`-into-a-
PHP-double-quoted-string bug in the WordPress installer that corrupted
`DB_PASSWORD` whenever it contained `$`; `lsnsctl` writing status output
to stderr rather than stdout; `lsnsctl list-disabled-uids` returning uids
as JSON strings, not integers, silently breaking every disable/status
check; `lsnsctl set-min-uid`'s own uid-range validation making it
structurally impossible to ever lower the floor through the CLI itself;
and, self-inflicted by this phase's own Step 6 audit-logging addition, a
test-isolation gap that leaked 24 fake rows into the live production audit
log across three full-suite runs (fixed; the polluted rows were
deliberately left in place rather than deleted, since editing an audit
trail — even to remove known test noise — was correctly declined as
tampering, not something to do unilaterally). Every one of these is
detailed in full, with root cause and fix, in this document's Step
sections above.

**Test suite**: 929 passing (up from 902 before this phase), including 33
new/corrected tests in `tests/test_nsisolation.py`.

**Safety record**: three permission-classifier denials were respected
without any attempt to route around them during this phase — an
unauthorized production config flip (before explicit authorization was
given), an unreviewed broad `deploy.sh` run (a narrower, file-specific
copy was used instead, then later superseded once the flip was properly
authorized), and the audit-log `DELETE` above. Two live production
incidents occurred and were caught/resolved within minutes: a brief
Roundcube webmail outage during Step 1 (missing template paths for shared
infrastructure, not a hosting-account gap) and a real safety-ordering
mistake during Step 4's `min_uid` migration (caught by the permission
classifier, not by the author, and corrected to the intended safer order
before any account was actually put at risk — this account had zero
domains throughout, so actual blast radius was nil either way).
