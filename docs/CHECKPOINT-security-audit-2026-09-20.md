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
   Continue negative and positive tests.
5. Finish the signed-update trust bootstrap from the deployed unsigned
   updater, protected off-host signing-key recovery, current dependency
   advisory review, release gate, fresh install, upgrade, rollback and
   deployed acceptance. Do not publish or self-update before this is safe.
6. Plan and validate Ubuntu security package updates, including their
   service restarts and reboot impact, before applying them to production.

The work remains an active audit. There is no security release tag or GitHub
push from this worktree yet. Do not describe any source-only fix as deployed.
