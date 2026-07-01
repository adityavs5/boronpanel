# Checkpoint: Phase 3, Feature 5 — FTP account management

## What was built

- **FTP sub-accounts**, scoped (chrooted) to a path within their hosting
  account's own home directory. Implemented as Pure-FTPd **virtual**
  users (the `PureDB` backend), not additional real Linux accounts --
  PureDB users are inherently chrooted to their own configured home
  directory (confirmed live), which is exactly the per-subdirectory
  isolation a real Linux/unix-auth user can't give us without a separate
  per-user chroot mechanism this project doesn't otherwise need.
  Enabled as an *additional* auth source
  (`/etc/pure-ftpd/auth/30pdb`, priority before the existing
  `65unix`/`70pam`) -- the account's own existing system-user FTP login
  (ARCHITECTURE.md/RESEARCH.md SS4's locked `-l unix` decision) is
  unchanged and still works exactly as before.
- Files created by a sub-account are owned by the **hosting account's own
  uid/gid** (`pure-pw useradd -u/-g`), so disk usage/quota accounting for
  a sub-account's uploads is identical to the account's own uid, not a
  separate identity to track.
- Sub-account naming: `<account_username>_<label>` (same convention as
  hosted databases, `daemon/handlers_database.py`), so logins are
  globally unique across all hosting accounts without needing a
  cross-account uniqueness check beyond the DB's own `UNIQUE` column.
- Path validation reuses the exact same symlink-aware jail-check pattern
  as `daemon/filemanager.py` (Phase g) -- the path restriction *is* the
  entire point of this feature, so it gets the same defense-in-depth
  treatment (checked in the daemon, the actually-privileged process, not
  just trusted from the API layer).
- New `FtpAccount` SQLite table: owning account, login, path -- **never
  the password**, which lives only in PureDB's own `pureftpd.pdb` (same
  "passwords never stored in the panel DB" rule as every other
  credential in this project).
- API: full CRUD at `/accounts/{u}/ftp` (per the goal), plus
  `PATCH .../ftp/{f}/password` (shared with Phase 3 feature 10's
  password-manager surface -- same endpoint, no duplication).

## Two real, pre-existing (Phase 1) bugs found by live testing -- both fixed, not worked around

Live-testing this feature's own DONE WHEN bar ("connect with sub-account,
confirm restricted to assigned path") led directly to testing the
*account's own* FTP login too, for comparison -- which is where both of
these were found. Neither is new to Phase 3; both have been present
since Phase 1's original Pure-FTPd setup and are squarely within this
feature's scope ("uses existing Pure-FTPd... install from Phase 1" only
makes sense if that existing install actually works).

1. **A hosting account's own FTP login has never actually worked.**
   `/etc/pam.d/pure-ftpd` includes `auth required pam_shells.so`, which
   rejects any account whose shell isn't listed in `/etc/shells` --
   every Forgehost hosting account uses `/usr/sbin/nologin`
   (ARCHITECTURE.md SS5's deliberate "no interactive SSH" choice), which
   was never in that list. Confirmed live: `pure-ftpd` logged
   `Authentication failed for user [<account>]` for a definitely-correct
   password. **Fixed** by adding `/usr/sbin/nologin` to `/etc/shells` --
   the standard, minimal fix real hosting panels use for exactly this
   "FTP/PAM-authenticated but no interactive shell" case: `/etc/shells`
   is only consulted as an *allowlist for authentication purposes* (by
   `pam_shells.so`, `chsh`, etc.) -- it does not change what `nologin`
   itself does when actually exec'd (still immediately prints a message
   and exits), so SSH/local interactive login remains exactly as
   blocked as before. This does not weaken anything Phase 1 intended.
2. **Far more serious: a hosting account's own FTP login was not
   chrooted to its home directory at all.** No `ChrootEveryone` setting
   existed in `/etc/pure-ftpd/conf/` (confirmed: the directory listing
   from the very start of this feature's work showed only `AltLog`,
   `FSCharset`, `MinUID`, `NoAnonymous`, `PAMAuthentication`, `PureDB`,
   `TLSCipherSuite`, `UnixAuthentication` -- no chroot setting of any
   kind), despite ARCHITECTURE.md's SS4 explicitly stating the locked
   decision as "Pure-FTPd, `-l unix`, **chroot to home**." Confirmed live
   with a real FTP session (`ftplib`, not just `curl`): logging in as a
   real hosting account and issuing `CWD ..` twice reached the server's
   real filesystem root, with a full directory listing of `/etc`,
   `/root`, other accounts' home directories, etc. -- a genuine, severe
   isolation failure between hosting accounts (and towards the host
   system itself) that predates this feature but was never exercised or
   caught until this feature's live testing did the same "log in and
   look around" test PureDB's virtual users get by construction.
   **Fixed** with `ChrootEveryone yes` in `/etc/pure-ftpd/conf/` --
   confirmed live afterward: the same session now shows `pwd` as `/`
   (the virtualized chroot root) immediately on login, `CWD ..` cannot
   move above it, and only the account's own home directory contents are
   listed. Re-confirmed the PureDB sub-account feature above still works
   identically afterward (PureDB users were always chrooted regardless,
   so this is a no-op for them) and that a second account cannot reach a
   first account's home directory via any relative-path trick.

Both fixes are two lines of system configuration, not application code
changes -- flagged here prominently (not buried) since bug #2
specifically means every account created before this fix was live had
been running with no FTP-level isolation from the rest of the server the
entire time this project has existed. No historical accounts existed on
this server outside of this session's own test accounts (all since
terminated), so there is no real customer exposure window to remediate
retroactively here, but this is exactly the kind of finding that would
matter enormously on a server with real hosting customers.

## Live verification performed

1. Enabled PureDB (`/etc/pure-ftpd/auth/30pdb` symlink), confirmed
   `pure-ftpd` restarts cleanly with the new auth chain.
2. Created a real account + an FTP sub-account scoped to `public_html`,
   connected with a real FTP client (`curl ftp://`, then Python's
   `ftplib` for more precise `CWD`/`PWD` control), confirmed: file
   read succeeds, directory listing shows only `public_html`'s own
   contents, and `CWD ../logs` (an existing sibling directory in the
   account's real home) is rejected with "no such file or directory" --
   the DONE WHEN bar ("restricted to assigned path") confirmed at the
   actual FTP-protocol level, not just via the daemon's own return value.
3. Found and fixed both bugs above via the same kind of real-session
   testing.
4. Confirmed password change: old password rejected, new password
   accepted, for the same login.
5. Confirmed path change: `ftp.set_path` moves the chroot root to a new
   subdirectory, re-verified via a fresh FTP session.
6. Confirmed deletion removes the login from PureDB (`pure-pw show`
   fails with "unable to fetch info") and the SQLite row.
7. Confirmed `account.terminate` removes every FTP sub-account
   automatically (no orphaned PureDB entries left behind).
8. Exercised the full UI page (`/ui/accounts/{u}/ftp`) through real
   HTTP: create, list, and confirmed the rendered login/path.

## Testing

`tests/test_handlers_ftp.py` (new, 13 tests): happy-path creation,
missing-path auto-creation, path-escape rejection (both a plain `../`
attempt and a symlink-based escape, mirroring `test_filemanager.py`'s own
jail tests), duplicate-label rejection, list/set-path/change-password/
delete, account-termination cleanup, and a same-label-different-account
non-collision check -- all with `pure-pw`/filesystem calls mocked
(this project's established "mock system calls, not pure logic"
convention; no test host actually needs Pure-FTPd installed to run the
suite). 389 tests passing (up from 376).

## What's untested / explicitly out of scope

- FTPS/TLS-encrypted FTP sessions specifically for sub-accounts (Pure-
  FTPd's existing TLS config, `TLSCipherSuite`, applies uniformly
  regardless of auth backend -- not separately exercised here).
- Per-sub-account bandwidth/connection-count limits (`pure-pw useradd`
  supports `-t`/`-T`/`-y` etc.; not exposed in this feature's API/UI,
  matching the goal's own scope which only asked for path restriction).
- Real concurrent `pure-pw`/`mkdb` writes from two simultaneous FTP
  account operations (each call does its own `-m` auto-mkdb; not
  stress-tested for a race between two overlapping admin actions).
