# Checkpoint: Phase g — file manager

## What was built

- `daemon/filemanager.py` — `file.list`/`file.read`/`file.write`/
  `file.mkdir`/`file.delete`/`file.move` RPC ops. Per ARCHITECTURE.md §10,
  this lives entirely in `forgehostd` (root), not the unprivileged
  `forgehost-api` process — "only the daemon touches account-owned files"
  stays absolute, and every file action is audited for free through the
  existing `dispatch()` wrapper.
- Two path-resolution functions with deliberately different symlink
  semantics:
  - `_resolve()` — for read/write/mkdir, which actually access file
    *content*: fully resolves symlinks (`os.path.realpath`) and rejects
    anything that escapes the account's home dir, including through a
    symlink pointing outside it.
  - `_resolve_entry()` — for delete/move, which act on a directory *entry*
    rather than its content: resolves and jail-checks the parent
    directory, but does not follow the final path component if it's a
    symlink. Added after real testing caught the obvious case this avoids:
    deleting/renaming a symlink an account itself created (even one
    pointing outside the jail) only ever touches that directory entry,
    never the target, so it must be allowed — the first version wrongly
    rejected it.
- `daemon/audit.py` gained value truncation (`MAX_PARAM_VALUE_LEN = 500`)
  so `file.write`/`file.read` calls don't write up to 10MB of raw file
  content into every audit log row.
- 19 new unit tests covering both normal CRUD and the jail itself
  (`..` traversal, absolute-path escape, symlinked-file escape, symlinked-
  *directory* escape, size limits, binary-content base64 fallback), 133
  total passing.

## Real end-to-end verification performed — including live attack attempts,
not just unit tests against a fake tree

1. Real `file.list`/`file.write`/`file.read`/`file.mkdir`/`file.move`
   against an actual account's home dir on this VM — confirmed files are
   created with correct ownership (`fmtest:fmtest`) and inherit Phase b's
   `nobody` ACL (visible as the `+` in `ls -la`'s permission string),
   meaning OLS can still serve files dropped in `public_html` through the
   file manager exactly like ones uploaded any other way.
2. **Three live escape attempts against the real running daemon**, not
   just the unit-test fake tree: `../fmtest2/public_html/secret.txt`
   (cross-account dotdot traversal), an absolute path to `/etc/shadow`,
   and a real symlink (`ln -s` as the `fmtest` Linux user) pointing at a
   second account's file — all three correctly rejected with "path escapes
   the account's home directory" (the absolute-path case resolves harmlessly
   *inside* the jail instead, since a leading `/` is stripped before
   joining — confirmed by the resulting "is not a file" error rather than
   an escape error, which is the intended defanging behavior, not a
   different bug).
3. The symlink-delete bug above was caught while writing this exact test
   sequence: cleaning up the escape-attempt symlink (deleting one's own
   file) failed until `_resolve_entry()` was added. Re-verified live after
   the fix: created a real symlink to `/etc/passwd`, deleted it through
   `file.delete`, confirmed the link is gone and `/etc/passwd` itself is
   untouched.
4. `file.delete` on the account's own home directory (empty path) is
   explicitly refused.
5. `account.terminate` continues to clean up the entire home directory as
   it always has (Phase a) — no file-manager-specific state to leak, since
   this phase added no new database table.

## What's untested / explicitly deferred

- No HTTP/REST exposure yet — that's Phase h's job (a web-based file
  manager needs a browser UI, which doesn't exist until the admin UI is
  built). This phase delivered the daemon-side primitives Phase h will
  call.
- No upload-by-multipart-HTTP path (the RPC `file.write` takes inline
  content, fine for text editing and small files; Phase h will decide
  whether large binary uploads need a streaming path instead of base64-
  in-one-RPC-call, given the 10MB ceiling here).
- No chmod/chown RPC op — v1 scope doesn't call for letting customers
  change permissions on their own files beyond what create/write already
  set; not built.

## What to review first on wake-up

- The two-resolver split (`_resolve` vs `_resolve_entry`) is the one
  subtle piece of this phase — worth a second pair of eyes given it's
  exactly the kind of distinction that's easy to get backwards (e.g.
  accidentally using `_resolve_entry` for `write_file`, which would let an
  account overwrite a symlink's *target* outside the jail instead of just
  the link itself). The unit tests pin both directions, but the reasoning
  is worth re-deriving independently rather than just trusting the tests.
