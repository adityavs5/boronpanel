# Checkpoint: Phase 4 feature 4 — Directory privacy (AuthUserFile)

## What was built

- **OLS's native `realm`/`userDB` mechanism** for per-directory Basic Auth
  — the real block syntax (`realm <name> { userDB { location <path> } }`,
  referenced from a context via `realm <name>`) isn't fully spelled out
  in this server's own installed docs (they describe the *fields*, not
  the block layout), so it was **confirmed empirically**: built a real
  minimal realm/context block by hand, validated it with a real
  `openlitespeed -t`, reloaded, and tested with real `curl` — before
  writing any of the actual feature code. That same live test also
  settled a real open question (this project's docs describe the field
  only as "crypt() encrypted password", ambiguous between glibc crypt,
  Apache's apr1-MD5, and bcrypt): **all three hash formats
  (`htpasswd -B`/bcrypt, `-m`/apr1, `-d`/classic crypt) verified
  correctly** against a real protected path. Bcrypt is used going
  forward since there's no compatibility reason left to prefer a weaker
  format once all three are confirmed to work.
- **`FileAuthDir`** (account_id, path relative to account home, realm
  name) — deliberately the *only* thing this feature stores in
  Forgehost's own database. Per the goal's explicit requirement,
  **usernames and password hashes are never recorded in the panel DB at
  all** — they live solely in the protected directory's own
  `.htpasswd` file, managed entirely through the real `htpasswd` CLI
  with every password piped via stdin (`-i`), never a command-line
  argument (this project's hard "passwords never logged anywhere" rule
  — `daemon/procutil.py`'s `run()` logs every command's argument list in
  full; a `-b`-style CLI password would have violated it immediately).
- **Reuses `daemon/filemanager.py`'s own realpath-based jail check**
  (`_resolve`) rather than reimplementing directory-traversal safety — a
  security-critical invariant worth sharing, not duplicating.
- **Docroot-root rejected explicitly, with a clear error**: protecting a
  domain's docroot itself would collide with the vhost template's
  already-existing `context / { }` block (can't have two `context / {}`
  entries for the same path) — caught and rejected at `enable_protection`
  time with an explanation, rather than generating a config that would
  fail `openlitespeed -t` days later. Directories outside any of the
  account's own domains' docroots are also rejected (protecting them
  would silently have no effect, since that's the only tree OLS ever
  serves).
- **API**: full CRUD as the goal's literal shape (`/accounts/{u}/file-auth`)
  plus a `/users` sub-resource for add/list/delete. UI: a new page,
  linked from the account page, that lists protected directories with
  their users and links out to the *existing* file manager
  (`/ui/accounts/{u}/files`) to browse the tree rather than duplicating a
  directory browser.

## Testing

`tests/test_validation.py` (+18): htpasswd username charset (rejects the
`:` field separator, whitespace, non-ASCII, empty, over-length),
directory-path slash-stripping and NUL-byte rejection.
`tests/test_ols.py` (+5): realm/context block rendering (present/absent),
suspended state omits both entirely, and `_protected_dirs_for_domain`'s
account-home-relative-to-docroot-relative rebasing (including that a
`FileAuthDir` row under a *different* domain's tree is correctly
excluded). `tests/test_fileauth.py` (new, 11): enable/disable against a
real temp filesystem (real `.htpasswd` file creation, not mocked),
duplicate/docroot-root/outside-docroot/nonexistent-dir rejection, and a
**real add/list/delete `htpasswd` cycle** confirming the plaintext
password never appears in the resulting file. 612 tests passing (up
from 581 after Feature 3).

## Live verification performed (the real Definition of Done)

Real account, real domain, a real subdirectory with real content, through
the **actual feature's RPC ops** (not the manual pre-implementation probe
above, which was reverted before any real code was written):

1. Baseline, before protection: `200`.
2. `fileauth.enable` + `fileauth.user.add` (password piped via stdin) —
   confirmed the real `.htpasswd` file was created with a bcrypt hash,
   and grepped `daemon.log` + the systemd journal for the plaintext
   password afterward: **zero matches in either**.
3. Real `curl` requests over HTTPS:
   - No credentials: **`401`**.
   - Wrong password: **`401`**.
   - Correct password: **`200`**.
   - The domain's own root path (outside the protected directory):
     **`200`**, no auth prompt — confirms the protection doesn't leak
     beyond the one directory.
4. `fileauth.disable` — confirmed the directory serves again with **no
   credentials at all** (`200`), and that its `.htpasswd` file was left
   on disk untouched (content-preservation, matching this project's
   "don't destroy customer content on a routing/config change" stance).
5. Every row of this table matches the goal's DONE WHEN bar directly:
   *"protected dir prompts auth, wrong password rejected."*

## What's untested / explicitly out of scope

- Group-based access (`.htpasswd`'s optional group column, OLS's
  `required group ...`) — not asked for; every protected directory in
  this feature just needs *any* valid user from its own `.htpasswd`.
- A directory-tree picker UI (checkbox/expand browser) was not built;
  the existing file manager is linked to instead and the protect-a-
  directory form takes a typed path. Judged sufficient for this
  feature's scope, matching how every other plain-text-input form in
  this project's UI already works (no client-side JS anywhere).
- Concurrent enable/disable or user-add calls against the same directory
  — not stress-tested (same low-risk-for-v1 posture already documented
  for other per-resource concurrent-RPC scenarios in this project).
