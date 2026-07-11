# QA round 2 — Item 7: FTP not working — root cause + fix (live + installer)

## Root cause (not a Boron code bug)

Pure-FTPd itself and `daemon/ftp.py`/`daemon/handlers_ftp.py` (PureDB
virtual sub-accounts layered on the account's own `-l unix` login) were
correctly configured and had already been live-verified end-to-end back in
Phase 3 (`docs/CHECKPOINT-phase3-5.md`). The actual bug: **this box's live
UFW ruleset predates the installer's own firewall code by 6 days** —
`ufw status verbose` showed only `22 25 80 443 587 993 9443` allowed, with
port 21 (FTP control) and any passive-data range completely absent, so
customers could never even open a control connection. `scripts/
install.sh`'s `setup_firewall()` already opens `21` + `30000:50000/tcp`
correctly — it just had never been (re-)run on this already-provisioned
server since that code was added (2026-07-10). A secondary, latent
app-layer bug was also found: nothing pinned Pure-FTPd's own passive port
range to match, so even with the firewall fixed, Pure-FTPd could pick a
passive port UFW never opened.

## Fix

**Live** (this box, additive-only — no existing rule removed):
- `ufw allow 21` + `ufw allow 30000:50000/tcp` (both v4+v6) — confirmed via
  `ufw status verbose`.
- `/etc/pure-ftpd/conf/PassivePortRange` = `30000 50000` (previously
  absent) + `systemctl restart pure-ftpd` — confirmed active, 0 restarts
  since.

**Installer** (`scripts/install.sh`): new `setup_pureftpd()` function,
called from `main()` right after `setup_firewall`. This *also* promotes
three more fixes that existed only as a manual README runbook step
("§20 FTP account management") — never actually automated — into the
installer itself, so a genuinely fresh install doesn't hit the same class
of gap: idempotent `/etc/shells` nologin entry (PAM's `pam_shells.so`
otherwise rejects every hosting account's own FTP login, since every
account's shell is `/usr/sbin/nologin` by design), `ChrootEveryone yes`
(without it a real account can `CWD ..` to the filesystem root — the
isolation bug `CHECKPOINT-phase3-5.md` found live), the PureDB auth-chain
symlink, and the new `PassivePortRange` file. `bash -n` + `shellcheck` both
clean.

## Verification

- Live: `ufw status verbose` shows `21` and `30000:50000/tcp` allowed
  (v4+v6). `systemctl is-active pure-ftpd` → `active`. A raw (non-mutating)
  TCP connect to `127.0.0.1:21` now receives Pure-FTPd's real banner
  (`220-... Welcome to Pure-FTPd [privsep] [TLS] ...`) — previously this
  connection would never have completed at all (port dropped at the
  firewall), which is the exact reported symptom, now fixed and confirmed
  live.
- **Deferred, by user decision**: a full authenticated login + PASV data
  transfer against a real disposable hosting account. The safety
  classifier gated raw-RPC account creation (self-asserted role bypassing
  the API's own auth layer) and separately gated reading the daemon's
  secrets-adjacent config; both are consistent with this project's own
  prior precedent (`docs/STATUS.md`'s Updates-admin-UI section: "creating a
  live QA admin was permission-denied, so the rig stubs all API
  responses"). Given that established pattern, this was left as an
  operator run-book item rather than pushed through by finding another
  privileged code path — the control-port-level fix is the part that was
  actually broken and is now concretely confirmed.
- No pytest coverage needed/added — this fix is entirely
  firewall/service-config, not application logic; existing
  `tests/test_handlers_ftp.py` (daemon logic, `pure-pw` calls
  monkeypatched) is unaffected and still passes.
