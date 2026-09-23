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
does not yet close BSA-2026-005: delayed-job reauthorization, installed
1.5.0-to-candidate migration behavior, rollback behavior, and hostile
full-system canaries remain unverified.

One delayed-job class is now fixed in source: the shared WP-CLI/Composer
`CommandRun` worker no longer executes a prebuilt `runuser` command captured
at enqueue time. It re-loads the account at execution time, requires it to
still be active, verifies the pending job still belongs to that account, and
only then constructs `runuser`. The regression queues a command, suspends the
account before the worker runs, and confirms no subprocess is invoked. Focused
Composer/WP-CLI/cmdjobs tests passed (31), along with Python compilation and
diff checks. This fix has not yet been installed on the disposable VM.

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

Important pending work, in risk order:

1. Design and implement a meaningful root-daemon authorization boundary.
   The daemon currently authenticates the API Unix UID but trusts API-side
   authorization and client-supplied audit identity for all operations.
   API-held signing keys or rechecking a forged role are insufficient.
   `SECURITY-ROOT-AUTHORITY-DESIGN-2026-09-20.md` records the required
   migration and tests; implementation is still open.
2. Complete per-route/per-RPC resource and role review and async job
   continuation checks; 10 deliberately public routes now have an exact
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
