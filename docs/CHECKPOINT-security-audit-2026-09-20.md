# Security audit execution checkpoint — 2026-09-20

Worktree: `/root/boronpanel-security-audit`, branch
`security-audit-2026-09-20`, based on
`231400957bb2989ee816801375176d9ac1e293cb`. The original checkout is
left untouched. Production remains `/opt/boron-1.5.0` via `/opt/boron`;
source fixes have not yet been released. Protected evidence, original
configuration, SQLite snapshot, original FTPS certificate, and the release
signing private key are under `/root/boron-security-audit/2026-09-20/`, not
Git. The private key has no confirmed off-host recovery copy.

Additional live hotfix: the installed `backup.py`, `cpanel_import.py`, and
`appinstaller.py` now use `mysql --binary-mode --defaults-file` for SQL imports;
backup restore also uses a temporary database principal granted only to the
target database. Original modules are protected as
`baseline/{backup,cpanel_import,appinstaller}.py.before-sql-hotfix`. A harmless
live MariaDB client probe confirmed that unguarded input executed a shell
command, while `--binary-mode` rejected it. The first scoped restore test
revealed that `--defaults-extra-file` still allowed another option file to
override credentials and write to a sibling disposable database; switching
to exclusive `--defaults-file` denied that write. After the hotfix, the
installed module paths completed normal synthetic restore/import and denied
both explicit cross-database writes; no cross-database canary table remained.
The daemon/API/MariaDB services and HTTPS `/healthz` passed. Every temporary
database and user created by those tests was removed. This hotfix is not a
signed panel release.

WordPress's root installer also received a backed-up one-file live hotfix for
bounded vendor download/ZIP extraction and duplicate normalized paths.
The current app-installer module was refreshed with the matching normalized
path check. The previous files are protected as
`baseline/{wordpress,appinstaller}.py.before-archive-hotfix`; restart and
panel health passed. WordPress tests passed (32) and app-installer tests
passed (18) after this last change. A real WordPress installation/upgrade
through the live panel still needs the disposable full-system lab.

Further WordPress review found that `wp_install()` returns the plaintext
administrator password, and Boron's PHP helper serialized that return value
into captured subprocess output. Its invalid-output and failure paths could
also copy raw output into a job error. The helper now emits only a success
boolean and the daemon uses generic failure messages. All 39 focused
WordPress/helper tests passed. Both installed files received backed-up live
hotfixes (`baseline/{wordpress.py,wp_install_helper.php}.before-output-hotfix`);
daemon restart, local HTTPS health, and source/live hashes passed. A read-only
check of the six live WordPress job rows found no failed jobs, pending plaintext
password reveal, or known password in stored errors. A real fresh install
remains to be exercised in a disposable full-system lab. WordPress documents
the password-bearing return value at
<https://developer.wordpress.org/reference/functions/wp_install/>.

WP-CLI's password-reset action also passed the newly generated password in a
`--user_pass=` process argument. The local procfs mount has no `hidepid`
restriction, so this was an avoidable local exposure path. WP-CLI now receives
the password over stdin via its documented `--prompt=user_pass` option, and
the shared command runner masks known secrets from persisted stdout, stderr,
errors and sensitive exception logs. All 21 focused tests passed. The two
installed daemon files were backed up as
`baseline/{wpcli,cmdjobs}.py.before-stdin-hotfix`, replaced, restarted and
health-checked; source/live hashes matched. A read-only scan of 30 existing
command jobs found no password-reset jobs, pending one-time secrets, or stored
`user_pass` flags. A real reset and cross-account procfs check still need a
disposable lab. WP-CLI documents the prompt option at
<https://developer.wordpress.org/cli/commands/user/update/>.

The optional MaxMind GeoLite download previously included its license key in
curl's process arguments and parsed an unbounded tar response. The URL now
enters curl through stdin config, with compressed download, member count and
expanded database limits. Three focused tests passed, including an actual
curl stdin-config invocation. The installed daemon module received a backed-up
one-file hotfix (`baseline/geoip.py.before-stdin-hotfix`); restart, HTTPS
health and source/live hash match passed. The live host has no configured
MaxMind key, so an authenticated download remains untested.

PrestaShop's optional CLI installer exposed both the generated database and
administrator passwords in process arguments. Its first-party PHP entry point
reads the passed `$argv` array, so a root-owned PHP helper now reconstructs
that array from a bounded JSON payload on stdin and includes the vendor's
installer under the hosting UID. The 20 focused app-installer tests pass,
including an actual PHP helper invocation and the Python call-site check;
PHP lint and Python compilation pass. The installed app-installer module and
new helper received a backed-up live hotfix; the GeoIP module also received
its missing-archive guard. After correcting an initial restart command that
named a nonexistent unit, the actual `boron-provisiond` service restarted,
local HTTPS health passed and all three installed files matched source. No
PrestaShop jobs exist in the live SQLite database. A real vendor release
install remains unverified until the disposable full-system lab is available.
PrestaShop's documented CLI password flags and upstream `$argv` use are at
<https://devdocs.prestashop-project.org/8/basics/installation/advanced/install-from-cli/>
and <https://raw.githubusercontent.com/PrestaShop/PrestaShop/8.2.x/install-dev/index_cli.php>.

Application-install worker failures could also persist and log arbitrary
exception messages from vendors and database tools. Those messages can embed
credentials, so the worker now records only the exception class and marks the
job failed; password validation is inside the same failure handler so an
invalid password does not leave a pending job. A deliberately secret-bearing
failure passed the new regression, with 21 focused app-installer tests passing.
The installed module received a backed-up one-file hotfix
(`baseline/appinstaller.py.before-error-redaction-hotfix`); the provisioner
restart, local HTTPS health and source/live hash match passed.

The root RPC dispatcher also wrote arbitrary handler exception text to audit
details and returned it to the API, while logging the full traceback. Vendor,
OS or database errors can include generated credentials absent from request
parameters. Unexpected exceptions and third-party `ValueError` messages now
return generic text and retain only the exception class in logs/audit rows;
known validation messages remain useful but redact secret-valued request
parameters. Ten focused RPC tests and 58 nearby API/auth/account tests passed.
The installed `server.py` received a backed-up one-file hotfix
(`baseline/server.py.before-rpc-error-hotfix`), including the previously
source-only fail-closed SO_PEERCRED check. The daemon restart, local HTTPS
health, source/live hash match, and a read-only `health.get` RPC as the real
`boron-api` UID all passed. This does not close the daemon's separate root
authorization gap: an API-UID process can still request any RPC operation.

The first disposable-VM install review found that `FH_ADMIN_PASSWORD` was
inherited by installer children and then passed to `create_admin.py` in argv.
The installer now unsets the environment variable immediately and supplies
its shell-local value only over stdin; the bootstrap CLI no longer accepts a
password argument. Ten installer/CLI tests, shellcheck, bash syntax, Python
compilation and diff checks passed. This fix will be exercised by the fresh
Ubuntu 24.04 installation before any release candidate is built.

That fresh install exposed OpenLiteSpeed's package-generated temporary
WebAdmin credential in apt output. Boron's generic command logger copied the
line into `/var/log/boron-install.log`, which was created with mode 0644 until
the later log-directory setup. Command output now masks that credential before
logging or error display, and `_logline` creates and maintains the installer
log as 0600 from its first write. Eleven focused installer/CLI tests,
shellcheck, bash syntax and diff checks pass. The disposable VM's existing
log was redacted and restricted without printing the credential.

The same fresh install built the frontend with Ubuntu's system Node 18 before
installing Boron's verified Node 20 runtime, despite the frontend declaring
Node 20 or newer. The installer now provisions managed runtimes before
deployment and explicitly places the managed Node 20 binary directory first
for both locked dependency installation and the production build. Nine
installer tests, shellcheck, bash syntax and diff checks pass. The disposable
run captured the original engine mismatch; final candidate acceptance must
exercise the corrected ordering.

The first OpenLiteSpeed baseline transaction also failed on a clean host
because the rendered main config referenced the configured panel and webmail
vhosts before later installer steps created their webroots and vhost files.
The baseline now validates and prepares both infrastructure webroots, then
applies the main, panel, and webmail configs in one rollback-capable
transaction. The candidate transaction succeeded on the disposable VM; the
new regression test and the OLS/panel-TLS suite pass. The resumed install then
caught and corrected a PATH quoting error in the managed-Node build command
before any release candidate was produced.

A subsequent retry exposed a provisioning-daemon readiness race. The
installer accepted a stale socket path after ten seconds, then attempted its
first RPC three seconds before the freshly started daemon replaced that path
with the `boron-api`-accessible socket. The gate now waits up to thirty
seconds and verifies read/write access as the actual API service user before
continuing. Nine installer tests, shellcheck and bash syntax pass.

Roundcube's verified release then installed, but its first database command
relied on the client discovering `/root/.my.cnf` through an ambient `HOME`.
The transient systemd installer did not provide that lookup. A non-disclosing
comparison confirmed that the protected root secret and client file matched,
and an explicit-defaults authentication probe passed. Idempotent setup now
reconciles the root client file from the protected secret, and every Roundcube
database command names that file explicitly. Nine installer tests, shellcheck
and bash syntax pass.

The first reboot left `clamav-freshclam` disabled and inactive even though
the package had started it during installation. That would freeze malware
definitions at their installation-day versions. The installer now explicitly
enables and starts the signature updater with Boron's core services. Nine
installer tests, shellcheck and bash syntax pass; disposable activation and a
second persistence check remain to close this finding.

Disposable acceptance is now complete for these fresh-install fixes. The
managed Node 20 build completed without an engine warning; the atomic OLS
bootstrap and daemon readiness gate passed; Roundcube schema/bootstrap and
external HTTPS returned 200; the generated admin login returned 303 and
resolved as an administrator; and a plaintext scan found zero hits in files,
process argv/environment, or the recent journal. After a second reboot there
were zero failed units, `openlitespeed -t` returned zero, quotas and UFW were
active, panel and webmail returned 200, and `clamav-freshclam` was enabled and
active. The install log remained root-owned mode 0600.

The disposable host also reproduced BSA-2026-005 directly: a process running
as `boron-api` supplied forged administrator audit metadata and successfully
called `account.list` without any panel credential. It returned an empty list
because the fresh host has no customer accounts, but acceptance itself proves
the root daemon still lacks an authorization boundary. This remains the main
release blocker.

The root RPC authority patch is now source-complete and installed on the
disposable VM only. Commits `d70cbf8`, `8cba26f`, and `c5b6258` hash browser
sessions, migrate legacy session rows, send credentials in a top-level RPC
envelope, derive the acting principal inside borond from root-owned database
state, require an explicit reviewed policy for all 414 registered operations,
move login password/TOTP checks into borond, block legacy session-minting RPCs,
and add root-side proof checks for current-password and update-confirmation
flows. Local focused validation passed: 84 session/auth/API/RPC tests, the
current 14-test RPC authority file, Python compilation, diff checks, and a
414/414 policy-registry equality check. On the disposable VM the deployed
daemon reports 414/414 registry equality, the original forged `boron-api`
metadata canary now fails with `unauthenticated`, real admin login on the
panel listener returns 303 and `/api/v1/whoami` returns the admin identity,
and the session table contains only SHA-256-shaped session identifiers. This
does not yet close BSA-2026-005: installed 1.5.0-to-candidate migration
behavior, rollback behavior, hostile full-system canaries, and lower-risk
admin-only async continuations remain unverified.

One delayed-job class is now fixed in source: the shared WP-CLI/Composer
`CommandRun` worker no longer executes a prebuilt `runuser` command captured
at enqueue time. It re-loads the account at execution time, requires it to
still be active, verifies the pending job still belongs to that account, and
only then constructs `runuser`. The regression queues a command, suspends the
account before the worker runs, and confirms no subprocess is invoked. Focused
Composer/WP-CLI/cmdjobs tests passed (31), along with Python compilation and
diff checks. This fix is now installed on the disposable VM. After deployment
both Boron services were active, local `/healthz` returned 200, the daemon
registry still reported 414/414 policy equality, the forged `boron-api`
metadata canary still failed with `unauthenticated`, and real admin login
still returned 303 plus an admin `/api/v1/whoami` identity.

A second delayed-job class is fixed and installed on the disposable VM:
queued backup restores now
re-read the restore row, backup row, destination, and account inside the
worker, require the restore job to still be pending, and require the account
to still be active before any artifact is fetched or restored. The regression
queues a restore, suspends the account before the worker starts, and confirms
the artifact fetch is never reached. The backup suite passed (57).

A third delayed-job class is fixed and installed on the disposable VM: queued
backup jobs now re-read the backup row, destination and account inside the
worker, require the backup job to still be pending, and require the account to
still be active before any backup artifact is built. The regression queues a
database backup, suspends the account before the worker starts, and confirms
the database backup builder is never reached. The expanded backup suite passed
(58), along with Python compilation and diff checks. After deploying commit
`2171e44` to the disposable VM, both Boron services were active, local
`/healthz` returned 200, the daemon registry reported 414/414 policy equality,
the forged `boron-api` metadata canary still failed with `unauthenticated`,
and real admin login still returned 303 plus an admin `/api/v1/whoami`
identity.

A broader delayed-worker pass is now fixed in source and installed on the
disposable VM at commit `77abd1d`. Malware scans now enqueue only the scan
job ID and re-read the scan row, account, domain, UID/GID, home path and
exclusions before walking files; the daemon malware tests passed (8), while
the separate malware API TestClient test still hangs in this sandbox like the
known update API TestClient case. IMAP migrations now re-check that the
mailbox still belongs to the queued account and that the account is still
active before resolving the source host or starting imapsync; the IMAP suite
passed (22). WordPress and generic app installer jobs now make the persisted
job row authoritative for account/domain/app at execution, reject stale
inactive accounts or moved domains before vendor code runs, and preserve
vendor-error redaction; the focused WordPress/app-installer suites passed
(58). cPanel/DirectAdmin import and portable Boron archive import jobs now
re-check the target account/login state before archive fetch, validation or
restore; the environment-safe import suites passed (54) with the two known
host-dependent cPanel tests deselected (`/etc/letsencrypt` writes and ACLs).
Snapshot backup runs now re-read the account under the account lock before
collecting sources; the worker-focused snapshot jobs passed (13), with the
API/TestClient authorization case still excluded due the same sandbox hang
pattern. Namespace bulk-enable now re-checks that each queued account is still
active before calling `lsnsctl enable-uid`; the namespace suite passed (28).
Bulk account jobs now refuse stale reruns of non-pending jobs; the bulkops
suite passed (8). After deploying the batch to the disposable VM, both Boron
services were active, local `/healthz` returned 200 after the API completed
its normal startup delay, the daemon registry reported 414/414 policy
equality, the forged `boron-api` metadata canary still failed with
`unauthenticated`, and real admin login still returned 303 plus an admin
`/api/v1/whoami` identity.

The FileBrowser API proxy had a source-confirmed CSP trust error: it hashed
inline scripts from *every* upstream HTML response, which could bless scripts
from a hosted HTML file if the backend served one on the panel origin. Dynamic
hashing is now limited to the exact FileBrowser SPA shell URL without a query;
other HTML is sandboxed and sent as an attachment, while SVG/XML documents are
sandboxed. Five focused tests passed. The installed API module received a backed-up
one-file hotfix (`baseline/filebrowser.py.before-csp-hotfix`); API restart,
trusted local HTTPS `/healthz`, and source/live hash match passed. The first
restart health check ran before the API had bound port 2222, so the guard
restored the original; a later readiness-waiting attempt completed. Browser
tests of actual FileBrowser file delivery remain pending.

Completed live changes: FTPS now presents the trusted panel certificate and
passes hostname validation; 10 pre-existing wildcard MariaDB grants were
replaced with exact grants. A disposable MariaDB user and two temporary
databases reproduced broad access before the fix and demonstrated owned
access plus cross-database denial afterward. All disposable resources from
that test were removed. A read-only post-check reports 0 legacy grants from
11 hosted database grants. The running 1.5.0 daemon received a one-file,
backed-up hotfix to prevent new wildcard grants; restart and panel health
passed. Original grants and the pre-hotfix file are recorded in protected
`baseline/` evidence.

Source changes in progress: terminal and FileBrowser Origin checks; SVG
allowlist; mandatory verified and IP-pinned IMAP transport; scoped SQL dump
import; exact database grant creation and upgrade repair; transactional
Sieve updates; signed release/updater verification from the exact extraction
file handle; FTPS certificate renewal and update hooks; root-side
resource-count caps; bounded cPanel archive member count and duplicate-file
checks. Focused tests passed: IMAP 21; updater/Sieve/grants/IMAP combined
111; earlier TLS/updater 76; targeted branding upload 20; terminal 8;
FileBrowser 2; release script 11, including a signed dry run; resource
limits 1; Node 22, Python 16, FTP 13, database/mail 39, domain 26
(one actual chown/ACL proof deferred by sandbox), cPanel archive/import 6.
`shellcheck`, `bash -n`, Python compilation and `git diff --check` passed.
The installer no longer pipes LiteSpeed's changing repo setup script to
root; it ships a verified public key scoped to a HTTPS APT source. The
bundled key verified the signed Noble Release cached on this host; installer
tests passed (8). The installed server was migrated from the historical HTTP
source and globally trusted keys to the HTTPS `signed-by` source. An isolated
APT-source refresh passed first, then a normal `apt-get update -qq` passed;
`openlitespeed` retained its installed/candidate 1.9.2 Noble package. No
packages were upgraded. Original APT source/key files are protected under
`baseline/*.before-scoped-trust`. The staged updater now runs a fail-closed,
backed-up reconciliation before version switch; five focused tests cover the
script, inventory, and update failure gate. A fresh install remains pending.
Backup/portable
archive tests passed (67), app-installer tests passed (18), and the updater
suite passed (70) after the latest changes. Archive member and expansion
limits now cover nested portable backups and app release packages. A full
cPanel import module run had 95 passes and two host-dependent failures
(writing `/etc/letsencrypt`, applying ACLs) under this restricted sandbox;
focused SQL and extraction cases passed. The installed app-installer module
was hotfixed, but the portable archive hardening is source-only.
The frontend production build passed with the installed Node 22 toolchain
after installing locked dependencies offline in the audit worktree. The full
suite, browser checks, and final artifact acceptance have not yet been run.
Refreshed Ubuntu apt metadata lists 324 upgradable packages, including 170
with a security-pocket candidate. A dry-run would upgrade 317 and leave 7
back. No OS package upgrade was applied; the simulation and local package
inventories are protected evidence. OpenLiteSpeed WebAdmin on port 7080
passed a trusted TLS 1.3 hostname check.

The focused release/update safety suite was rerun after the authority and
delayed-worker changes. A sandboxed run passed 83 tests and skipped 2, then
hit the expected local-socket sandbox denial in the finalizer tests. The same
suite run outside the sandbox passed 88 and skipped 2:
`tests/test_release.py`, `tests/test_release_signature.py`,
`tests/test_updates.py`, and `tests/test_update_finalizer.py`. This validates
the local packaging/signature/updater/finalizer test coverage only. The
remaining release gate is still a real GitHub release asset followed by an
installed update and rollback drill on the disposable VM; publishing that
asset has intentionally not been done from this unreleased audit branch.

Important pending work, in risk order:

1. Complete full-system validation of the root-daemon authorization boundary:
   installed 1.5.0-to-candidate migration, rollback behavior, hostile canaries
   for imported archives and WordPress/application flows, and lower-risk
   admin-only asynchronous continuations. The core BSA-2026-005 implementation is
   present, but it is not a releasable security update until these gates pass.
2. Complete per-route/per-RPC resource and role review; 10 deliberately public
   routes now have an exact
   inventory test and reviewed status. Source inventory now includes cron,
    static and generated services, shell/Python scripts and a native helper: 895
   total entries, 885 pending. A protected read-only live snapshot records
   59 listener rows, 208 services and 25 timers. Add listener-policy review,
   asynchronous job continuations and ASVS/WSTG map.
3. Build an isolated full-system Ubuntu test environment. This sandbox
   denies namespaces, service sockets and systemd, so host isolation and
   real TLS/mail/installer/upgrade tests cannot be claimed here.
4. Complete hostile archive/restore/WordPress/Cloudflare/host review and
   legacy deferred fixes (bandwidth enforcement). Redis socket squatting
   was rechecked and no longer applies: current sockets are per-account
   under private 0700 home directories.
   Continue negative and positive tests, including FileBrowser HTML/SVG
   delivery and normal SPA use after the CSP restriction.
5. Finish the signed-update trust bootstrap from the deployed unsigned
   updater, protected off-host signing-key recovery, current dependency
   advisory review, release gate, fresh install, upgrade, rollback and
   deployed acceptance. Do not publish or self-update before this is safe.
6. Plan and validate Ubuntu security package updates, including their
   service restarts and reboot impact, before applying them to production.

The work remains an active audit. There is no security release tag or GitHub
push from this worktree yet. Do not describe any source-only fix as deployed.

### 2026-09-23 continued audit

Commit `0d0522b` atomically claims pending update/rollback jobs, uses the saved
version/target, and revalidates rollback candidacy. The updater suite passed
81 tests, plus two additional positive/concurrent-claim tests. The previously
stalled malware/snapshot/update API suites passed 25 tests outside the local
socket-restricted sandbox. On the disposable Ubuntu VM, 120 tests passed across
cPanel import, portable archives, WordPress, app installer and update finalizer,
including both earlier host-dependent cPanel certificate/ACL checks.

BSA-2026-029: second factors were replayable within their time window, and
recovery-code read/consume was not serialized. Commit `87ac752` adds an additive
last-used timestep column and serializes enrollment/verification/recovery
consumption. Enrollment cannot regenerate recovery codes once enabled. Existing
enrollments survive the idempotent migration. Authentication/session regression
suite passed 55 tests; the additional migration case passed separately. Three
RPC policy matrix tests also passed, covering every operation's anonymous
disposition, all admin-policy denials for customer/reseller identities, and all
shared account policy groups against foreign and mismatched account/domain
identifiers. These policy tests do not replace individual handler reviews.

A complete regression run of `87ac752` is running in the separate disposable-VM
source directory `/root/boron-audit-suite-87ac752`, with production secret loading
disabled and a protected log `/root/boron-audit-suite-87ac752.log`. It is not yet
a passed release gate. The running panel has not yet received this commit.

### 2026-09-23 FileBrowser isolation and regression follow-up

BSA-2026-030 is a confirmed cross-account disclosure in the shared root
FileBrowser backend: an authenticated disposable customer could download a
0600 peer-owned canary through a symlink in its own home. The shared backend
was stopped on the VM. Commits `46547ff` and `112ed30` replace it with one
account-UID process per account, no Linux capabilities, a private home mount,
and an authenticated API proxy over an account-specific Unix socket. The
provisioning daemon cannot set SGID bits, so socket access uses a default ACL
for the API UID. Lifecycle changes are serialized.

On the disposable VM, real customer tests now pass: own-file download 200,
foreign account launch/admin access 403, peer symlink and parent traversal
rejected without private content, foreign-origin write 403. Actual Chromium
loads the SPA and editor without page errors. Uploaded HTML downloads as an
attachment; SVG navigation is sandboxed; neither test script executes. The
header replacement fix `c14bcd2` also prevents duplicate Content-Disposition
headers that previously broke HTML downloads. Focused FileBrowser/account
regression passed 92 tests outside the local Unix-socket restriction.

The same limited FileBrowser hotfix was installed on the development server
under `/opt/boron-1.5.0`, after disabling its vulnerable shared backend.
Backups are under the protected audit baseline `filebrowser-isolation/`.
Only the FileBrowser modules plus two path settings were copied; this is
not deployment of the full audit branch or a new published version. Panel,
provisioning and web services remained healthy during containment; primary browser acceptance passed: real admin login and file listing 200,
SPA visible without page errors, service UID matches the customer, no
capabilities, private home mount, and shared root service disabled.

Additional fixes: `a661451` retries configured resource suspension even when
notification deduplication applies (21 tests passed); `5dc3178` serializes
login attempt reservations in SQLite (25 tests passed). `c9ae28c` corrects
two fresh-install bootstrap calls which new root authorization intentionally
rejects: the root installer now invokes OLS/webmail setup locally, following
the existing security-service bootstrap pattern.

The earlier full run of `87ac752` was stopped after identifying test harness
configuration problems. A replacement full run of `b194c35` is running in
two shards on the disposable VM (logs `/root/boron-suite-b194c35-{0,1}.log`).
It is not a passed gate. `b194c35` isolates test configuration from installed
secrets. `a048f09` fixes test-only assumptions about checkout-local Python
interpreters and permissive umasks, and prevents collected server hooks from
polluting synthetic account-handler tests. These corrections are being
retested in a separate VM checkout; original running-suite files are retained.

Remaining release gates and individual surface review remain open. No GitHub
release or security version tag has been published.

The full `b194c35` baseline run completed: **2,847 passed, 20 failed, 4 skipped**
across two shards (46m54s and 51m38s). All 20 failures were traced to harness
assumptions: collected lifecycle hooks, restrictive umask, missing
checkout-local `.venv`, missing `.git` in the archive-based test checkout,
and restic hiding SSH's host-key diagnostic. Corrections do not bypass the
security assertions: the SSH test now checks the exact transport's host-key
rejection directly and separately proves storage initialization refuses it.
The affected host suites passed 82 tests with one skip; additional Dovecot/
SSH checks passed four tests. Release/installer checks passed 19 with two
signing-key-dependent skips in the real local checkout. This baseline plus
focused reruns is not an exact-final-revision complete release run.

Real WordPress installation on a disposable auditweb domain completed.
Customer polling revealed its password once, then cleared it. Its one-click
login rejected GET and wrong token, accepted the valid POST and opened the
WordPress dashboard, and denied reuse. FileBrowser own create/edit/read/delete
also passed; peer symlink writes were rejected. Parent path syntax was clamped
to a path inside the caller's own home, and the peer private canary remained
unchanged; all files created by that write test were removed.

BSA-2026-033: WordPress/app-install completion passwords and WP-CLI reset
passwords were plaintext in the API-readable SQLite database until collected.
Commit `4fe7ab7` encrypts them with the root-only application key before
storage, migrates outstanding legacy rows at daemon startup, and serializes
read-and-clear under BEGIN IMMEDIATE. Tests exercise actual worker storage,
legacy migration, malformed ciphertext and concurrent retrieval. The affected
installer/command tests passed 84; the final five new cases passed again after
migration coverage was expanded. Deployed to the disposable VM for live reset
acceptance; primary deployment remains pending. This migration does not claim
to scrub historical backup copies or obsolete SQLite pages.

### 2026-09-23 installer filesystem and FileBrowser renderer follow-up

BSA-2026-034 is now fixed beyond WordPress. Commit `09329f7` moved WordPress
filesystem preparation, archive extraction and config writes into an account-UID
helper; `faf0a27` made that helper independent of root-only Boron config. Commit
`7edecb9` applies the same boundary to generic application installers:
Joomla/Drupal/PrestaShop/Laravel/static file actions now run as the hosting UID,
credentials enter helper processes through stdin, and recursive root `chown` is
gone. Focused appinstaller/jobcredential/safeio tests passed 41. On the
disposable VM, the new helper passed 10 real UID canaries covering root refusal,
root/peer symlink targets, zip/tar actions, Joomla and Drupal config writes,
Laravel `.env`, and the real web ACL path. A static app install through the
customer HTTP API completed on `appuid0.104.234.179.63.sslip.io`; the page served
over HTTP and installed files were owned by `auditweb`. The same limited hotfix
was applied to `/opt/boron-1.5.0` on the development panel after checking that
no app or WordPress jobs were pending. Source/live hashes match, `boron-api`,
`boron-provisiond`, and OpenLiteSpeed are active, and primary login/FileBrowser
smoke passed.

BSA-2026-035 records a shared safe-write race found during the same review:
`secure_write_file_beneath` verified parent directories but reopened the final
parent by pathname before replacing the file. Commit `106fe65` keeps the verified
directory fd through temp-file creation, full short-write handling, chmod/chown
and rename. The deterministic canary now preserves the peer file and cleans temp
files after partial writes. The fixed `safeio.py` is deployed on both the VM and
development panel as part of the app-files hotfix.

BSA-2026-036 records the FileBrowser trusted-renderer follow-up. Commit `b8ec15d`
separates trusted FileBrowser shell/assets into a dedicated `boron-files-ui`
system user and backend, while only `/api` paths reach customer-UID backends.
Unsafe origin-wide headers are stripped from backend responses and data HTML/SVG
responses are sandboxed or attached. Forty-three focused proxy tests passed. A
hostile VM backend that returned mixed-case HTML plus `Set-Cookie` could not
control the `/files` shell; API HTML was sandboxed/attachment, the cookie header
was stripped, and the admin session remained valid. The trusted frontend hotfix
is installed on the development panel and the existing Chromium FileBrowser
smoke still passes.

The coverage inventory now has 901 entries: 822 pending, 68 reviewed-fixed and
11 reviewed-public. New reviewed-fixed rows cover `scripts/app_files.py`,
`scripts/wordpress_files.py`, the trusted FileBrowser frontend unit, the auth
routes, token routes, `daemon/handlers_auth.py`, `daemon/totp.py`, and the
terminal WebSocket/RPC surface. The auth rows cite the 82-test focused
auth/session/TOTP/RPC authority suite, hashed sessions, root-side login protocol,
TOTP replay/recovery serialization, and the live VM login smoke. Existing
FileBrowser route/RPC rows now cite both the per-account isolation and
trusted-renderer evidence. The update routes/RPCs, updater cron wrapper,
`scripts/update_check.py`, and `scripts/update_finalize.py` now cite the focused
update/release tests plus the signed VM update/rollback drill. This is still not
a final security release gate:
signed update packaging, installed upgrade/rollback, fresh install from the
candidate, and the remaining route/resource review remain open.

### 2026-09-23 signed update and rollback drill

Focused update/release gates passed after rerunning FastAPI TestClient slices
outside the local socket-restricted sandbox: 16 update-version/API/signature
tests, 89 update-daemon/finalizer tests, and 11 release-pipeline tests. The
release tests include the signed dry-run artifact build, checksum verification,
tarball path/leak checks, shellcheck, and fail-closed unsigned-release behavior.

A protected signed candidate artifact set was built for VM testing:
`release-candidate-1.5.1-8aa5db3/boron-1.5.1.tar.gz`, `.sha256`, and
`.tar.gz.sig`. This was intentionally not published to GitHub. It was produced
from a temporary detached worktree at `8aa5db3` with only `version.py` bumped to
`1.5.1`; the artifact self-verified with the pinned Ed25519 public key.

On the disposable VM, a mocked GitHub transport inside the installed
`daemon.updates` served those exact local assets while the rest of the updater
path stayed real. Update job 1 completed the installed focused preflight,
online backup, GitHub-style redirect download, SHA256, publisher signature,
tarball extraction to `/opt/boron-1.5.1`, venv build, migrations, LiteSpeed repo
trust, database-grant reconciliation, mailbox guard, OLS WebAdmin integration,
FTPS TLS reconciliation, first-update conversion of real `/opt/boron` into a
versioned symlink, service restarts, and finalizer health checks. Rollback job 2
then completed through the same detached finalizer path, swapping back to
`/opt/boron-1.5.0` and restarting services. Final VM state: `/opt/boron` points
to `/opt/boron-1.5.0`, reported version is `1.5.0`, and `boron-provisiond`,
`boron-api`, and OpenLiteSpeed are active. Evidence is stored in the protected
VM folder as `mock-signed-update-drill.json`.
An authenticated admin login smoke after rollback returned a 303 login and
200 `/api/v1/whoami` as `admin`.

This closes the installed update/rollback mechanics for a signed candidate in
the disposable lab, but it is not a public release: the GitHub release creation,
real GitHub asset download, primary self-update, fresh install from the final
candidate, and remaining route/resource review are still open.

### 2026-09-23 terminal local-only key hardening

BSA-2026-037 records a terminal defense-in-depth gap. The web terminal generated
an ephemeral SSH key and only boron-api held the private key, but sshd did not
itself enforce that the key could only be used by the local panel process. The
same review found that Boron terminal cleanup recognized the marker substring
anywhere in an `authorized_keys` line, so an ordinary user key comment containing
`boron-terminal-...` could be counted or pruned as a Boron key.

The source now emits terminal key lines with `from="127.0.0.1"` and `no-user-rc`
in addition to the existing no-forwarding options. Marker parsing now accepts
only Boron-generated Ed25519 lines, while still recognizing legacy Boron terminal
lines for cleanup. The WebSocket frame parser also caps pasted input and resize
dimensions before passing them to Paramiko. The authorized_keys rewrite helper
now completes short writes and removes temporary files if replacement fails.

Focused validation passed 44 terminal/RPC-authority tests. The disposable VM was hotfixed with
hashes matching source; a real sshd canary for `auditweb` opened a Boron terminal
session, confirmed the generated line was localhost-only, logged in successfully
through 127.0.0.1, failed to authenticate with the same key through the public
host, closed the session, and confirmed the key line was gone. The same two
terminal files are installed on the development panel with matching hashes and
`boron-api`/`boron-provisiond` active.
