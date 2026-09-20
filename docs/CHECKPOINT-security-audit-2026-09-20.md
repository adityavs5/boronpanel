# Security audit execution checkpoint — 2026-09-20

Worktree: `/root/boronpanel-security-audit`, branch
`security-audit-2026-09-20`, based on
`231400957bb2989ee816801375176d9ac1e293cb`. The original checkout is
left untouched. Production remains `/opt/boron-1.5.0` via `/opt/boron`;
source fixes have not yet been released. Protected evidence, original
configuration, SQLite snapshot, original FTPS certificate, and the release
signing private key are under `/root/boron-security-audit/2026-09-20/`, not
Git. The private key has no confirmed off-host recovery copy.

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
2. Complete per-route/per-RPC resource and role review and async job
   continuation checks; the 842 inventory rows remain pending individual
   disposition. Add scheduled/helper/listener inventory and ASVS/WSTG map.
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
