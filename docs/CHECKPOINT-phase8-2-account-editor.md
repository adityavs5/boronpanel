# CHECKPOINT phase8-2 — Admin account editor (identity + passwords)

**Goal:** admin changes account password, primary domain, username (atomic
rename with rollback), contact email, any mailbox/DB/FTP password — all
without entering the customer panel, all audit-logged.
API: `PATCH /admin/accounts/{u}/identity|passwords`. **Effort: xhigh (rename).**

## What was built

- **Daemon** (`daemon/identity_admin.py`, ops in `server.py`):
  - `account.set_password` — resets the Linux/system password (FTP main login +
    SSH) via chpasswd; re-locks if the account is suspended (chpasswd would
    otherwise silently unlock it).
  - `account.set_contact_email` — sets `AccountNotificationPrefs.customer_email`
    (lazy row; blank clears).
  - `account.set_primary_domain` — renames the primary `Domain` row (docroot
    stays public_html) + `Account.primary_domain`; re-renders vhosts, sweeps the
    old vhost dir.
  - `account.rename` — **the atomic saga**: `usermod -l` + `groupmod -n` +
    `usermod -d -m` (home move) + panel DB rows (`Account.username` and every
    `Domain.docroot` /home/<old> prefix) + `cgroups.remove_slice`/`apply_limits`
    + `ols.refresh_vhost` + PHP-worker recycle. uid/gid are preserved, so
    quotas and namespace (both uid-keyed) need no change.
- **Rename rollback**: each mutating step registers a compensation; on any
  failure every completed step is reversed in LIFO order and OLS is reconciled
  to the restored state. OLS itself isn't on the undo stack — `ConfigWriterMulti`
  self-rolls-back its own config files on a failed apply, and we re-render from
  the restored DB once. Verified by `test_rename_rolls_back_on_ols_failure`.
- **Rename guards**: refuses accounts with NodeJS/Python apps or FTP
  sub-accounts (they embed the absolute home path in systemd EnvironmentFiles /
  Pure-FTPd's PureDB, which a v1 rename does not rewrite) — fails *before* any
  change. Refuses a name already taken by an Account or a non-panel Linux user.
- **Low-level primitives** added to `sysops.py`: `rename_login`, `rename_group`,
  `move_home` (each a single `usermod`/`groupmod`, discrete for step-level
  compensation).
- **API** (`api/routers/identity_admin.py`): `PATCH /admin/accounts/{u}/identity`
  (one of new_username / primary_domain / contact_email) and `PATCH
  /admin/accounts/{u}/passwords` (`kind` ∈ account/panel/mailbox/database/ftp,
  each dispatched to the existing audited change-password op). Both admin-only.
- **Frontend**: new admin **Identity** tab (`AccountIdentity.jsx`) with rename
  (confirm dialog → navigates to the new URL), primary-domain, contact-email,
  and a credential-picker password-reset form.

## "old username 404s" (Done-When)

After rename the old `Account.username` row is gone, the old Linux user no
longer exists (`usermod -l`), and OLS's `<old>_php83` extProcessor is dropped
from the regenerated `httpd_config.conf`, so `GET /api/v1/accounts/<old>`
returns "account not found" and the old vhosts are gone.

## Documented v1 limitations

- Hosted **MariaDB databases keep their original `<old>_` name prefix**: the
  rename does not rename live MariaDB objects (would require a per-DB
  dump+recreate). `DatabaseGrant` rows are deliberately left untouched so they
  keep matching the real objects.
- The customer's **panel login username** (`PanelUser.username`) is a separate
  credential and is not renamed (linked by `account_id`, which is stable).
- Accounts with Node/Python apps or FTP sub-accounts must have those removed
  first (refused up front, no partial change).

## Tests

`tests/test_identity_admin.py` — 13 tests: password reset (+ suspend re-lock +
weak rejection), contact email set/clear, primary-domain rename (+ collision),
and the rename saga (happy path ordering, duplicate/same-name rejection,
node/ftp refusal, and full DB rollback on OLS failure). All green.
