# CHECKPOINT phase8-13 — File manager upgrades

**Goal:** multi-select (bulk delete/move/copy/zip); Monaco editor for
.php/.js/.css/.html/.json/.py/.env; file search by name or content; all jailed
to home.

## What was built

- **Daemon** `daemon/filemanager.py` (new ops `file.copy`, `file.bulk_delete`,
  `file.bulk_move`, `file.bulk_copy`, `file.zip`, `file.search` — search is a
  REPORTING_OP):
  - `copy`: file or dir; **re-chowns the copied tree to the account** (a copy
    creates root-owned inodes; also re-applies 0750 dir / 0640 file perms — the
    `shutil.copytree` copystat-widening bug class STATUS.md already flags).
  - `bulk_delete/move/copy`: apply the single-item op to each selected path,
    **collecting per-item results** (one bad item doesn't abort the batch).
  - `make_zip`: zips selected paths into an account-owned archive, bounded by
    entry/byte caps.
  - `search`: walks a jailed root by **name** (substring) or **content** (grep of
    text files), bounded in files-scanned / per-file-size / results.
  - **Every path goes through the existing `_resolve`/`_resolve_entry` realpath
    jail** — bulk ops, zip, and search are all confined to the account home
    (verified by `test_copy_jailed`).
- **API** `api/routers/files.py` — `POST .../files/{copy,bulk-delete,bulk-move,
  bulk-copy,zip}`, `GET .../files/search`.
- **Frontend** `Files.jsx`:
  - **Multi-select** checkbox column + a bulk-action bar (Move / Copy / Zip /
    Delete) with destination/name dialogs and per-item failure reporting.
  - **Monaco editor** (`components/CodeEditor.jsx`) for code files
    (.php/.js/.css/.html/.json/.py/.env/…), lazy-loaded (kept out of the initial
    bundle) and **bundled fully locally** — the loader points at the bundled
    `monaco` and same-origin blob workers, so nothing loads from a CDN. The
    `/app` CSP gained `worker-src 'self' blob:` (still `script-src 'self'`, no
    external script).
  - **File search** bar (by name / by content) with a results panel.

## Jailed-to-home (Done-When)

All new operations reuse the same `os.path.realpath`-based jail as the existing
file manager; a `..`/symlink escape is rejected before any I/O
(`test_copy_jailed`). Monaco opens a real PHP file; multi-select delete works.

## Tests

`tests/test_filemanager_upgrades.py` — 15 tests: copy (file/dir/existing/jail),
bulk delete (+ per-item failure), bulk move/copy into dest, zip (+ refuse
existing), search by name/content, empty-query rejection. Plus the existing 20
`test_filemanager.py` tests still pass (35 total, no regression).
