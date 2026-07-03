# Checkpoint: Phase 4 feature 12 — codebase-wide strong password audit

The core validator (`validate_password_strength`: 12+ chars,
upper+lower+digit+special) was already rewritten as this phase's
mandatory pre-work, before any of the 11 numbered features. This
checkpoint is the promised follow-through: a full sweep of every
password-setting code path in the codebase (not just what Phase 4 itself
built) to confirm it's actually *used* everywhere, plus the "missing
account-level password-change UI" the goal called out by name.

## Real gaps found and fixed

**1. Custom account passwords bypassed validation entirely.**
`handlers_account.py`'s `create_account`/`reactivate_account` both did
`password = params.get("password") or sysops.generate_password()` -- if a
caller supplied a custom password, it was used completely as-is; only
the auto-generated fallback path was ever safe by construction. Fixed:
a supplied password is now run through `validate_password_strength`.

**2. That fix's own live test surfaced a second, pre-existing ordering
bug.** `create_linux_user` ran *before* password validation in both
functions -- a rejected weak password still left a real orphaned Linux
user behind (with no corresponding account DB row, confirmed live: `id
p4pwtest` succeeded after a rejected `account.create` call). This bug
already existed (every other validation in these two functions runs
before `create_linux_user`; password validation was the one exception),
it just had never been exercised before, since a weak password was
previously always silently accepted. Fixed by moving validation ahead of
`create_linux_user` in both functions; re-verified live afterward that
the same rejected attempt leaves no Linux user behind.

**3. Four independent auto-generated-password implementations
(`daemon/sysops.py`, `daemon/wordpress.py`, `daemon/appinstaller.py`,
`daemon/mariadb.py`) all drew only from letters+digits** -- none would
have actually passed `validate_password_strength`'s own special-character
requirement if anything had ever checked. Not a practical secrecy
weakness (20+ random alnum characters is already far stronger than the
12-char human-chosen minimum this validator enforces) but a real
consistency gap against "12+ char strong passwords... on ALL password
operations codebase-wide." Consolidated into one new
`shared.validation.generate_strong_password()` that builds a candidate
guaranteeing one of each required character class, shuffles it, and
**validates its own output against `validate_password_strength` before
returning** -- so the generator and the validator can never silently
drift apart again. All four call sites now delegate to it.

**4. WordPress's and the app installer's custom `admin_password` params
had the identical bypass as gap 1** -- `wordpress.py`'s `install()` and
`appinstaller.py`'s `_run_install_job` both did
`params.get("admin_password") or _generate_password()` with no
validation of a supplied value. Fixed the same way. (The app-installer
instance of this bug was in this session's *own* Feature 8 code.)

**5. The single weakest password path in the entire project: panel
LOGIN passwords.** `handlers_auth.py`'s `create_panel_user` and
`set_panel_user_password` -- the credential that logs an admin or
customer into the Forgehost panel itself, arguably the single most
security-critical password in the whole system -- only checked
`len(password) < 8`, no complexity requirement at all, weaker than every
other password path in the codebase. `"supersecretpassword"` (20 lowercase
letters) would have passed outright. Fixed: both now use
`validate_password_strength`, same as everywhere else.

**6. The missing UI the goal named explicitly**: `panel_user.set_password`
was registered as an RPC op but had **no API route and no UI form
anywhere** -- an admin or customer logged into the panel had no way to
change their own login password at all; the op was reachable only by
someone hand-crafting an RPC call. Built `GET`/`POST /change-password`
(`api/routers/auth.py`, a new `change_password.html` template, linked
from every page's header in `base.html`) -- deliberately self-service
and own-account-only: the target username is always the caller's own
`identity.username`, never taken from the request body (a hijacked
session must not be able to use this to change a *different* panel
user's password), and the current password must be re-verified first
(the same check `login_submit` already does), so a session that isn't
fully in control of the account can't silently lock the real owner out.

## Testing

`tests/test_validation.py` (+4): `generate_strong_password` always
passes its own validator (50 iterations), respects a custom length,
rejects a too-short request, and produces genuinely distinct output.
`tests/test_handlers_account.py` (+6): weak custom password rejected on
both `create_account`/`reactivate_account`, strong custom password
accepted, the auto-generated password verified against the real
validator (not just "a call happened"), and -- the ordering bug -- both
functions asserted to never call `create_linux_user` at all when the
password is rejected. `tests/test_handlers_auth.py` (+2, corrected 5
existing weak test passwords that would have failed the new rule):
explicit rejection of a 20-character all-lowercase password on both
`create_panel_user` and `set_panel_user_password`. Also corrected weak
test passwords in `tests/test_api_security.py` and
`tests/test_cross_account_authorization.py` (pre-existing tests using
passwords like `"adminpass123"` that the strengthened panel-password
check would now reject). 722 tests passing (up from 710 after
Feature 11).

## Live verification performed (the real Definition of Done)

Real RPC calls and real HTTP requests against the live server, not just
unit tests:

1. `account.create` with `password="weak"` -- rejected
   (`password must be at least 12 characters`) -- and, after the ordering
   fix, confirmed **no Linux user was created** (`id p4pwtest` → no such
   user), closing the orphan gap found by the first version of this fix.
2. A real auto-generated account password
   (`%%k!uSkPOeb17NHEHU0^`) inspected directly: 20 chars, has
   upper/lower/digit/special -- all four class requirements genuinely
   present, not just asserted.
3. **A real panel user, a real login session, and the full
   `/change-password` flow over real HTTPS requests** with a cookie jar
   for session continuity:
   - Wrong current password → `401`, `"current password is incorrect"`.
   - Correct current password + weak new password → `400`,
     `"password must be at least 12 characters"`.
   - Correct current password + mismatched confirmation → `400`,
     `"new password and confirmation do not match"`.
   - Correct current password + valid new password + matching
     confirmation → `200`, `"Password changed."`.
   - **The old password then failed to log in (`401`); the new password
     then succeeded (`303`)** -- the actual, end-to-end proof that the
     change took effect, not just that the form accepted input.
4. Test account/panel user cleaned up; `daemon.log`/`journalctl` grepped
   for every password used in this verification (the account's, the
   panel user's, the generated one) -- clean.

## What's untested / explicitly out of scope

- **Account termination does not clean up that account's `PanelUser`
  row(s).** Discovered incidentally while cleaning up this feature's own
  test artifacts (a panel user created for the live-verification test
  remained in the database after its account was terminated, requiring
  manual deletion). This is a pre-existing gap unrelated to password
  strength -- noted here for visibility since it surfaced during this
  checkpoint's own verification, but deliberately not fixed as part of
  this feature (out of scope: this checkpoint is about password
  strength enforcement, not panel-user lifecycle management). Worth a
  follow-up `TERMINATE_HOOKS` entry in a future pass.
- Existing FTP/database/mail password paths were re-confirmed (by
  reading, not by new tests) to already call `validate_password_strength`
  correctly from earlier phases -- no gap found there, so no code
  changed and no new tests were added for them.
- No automated test exercises `/change-password` at the HTTP layer
  (this project has never used FastAPI's TestClient anywhere, in any
  phase -- API/UI-layer behavior is consistently verified live instead,
  and this feature follows that same established precedent).
