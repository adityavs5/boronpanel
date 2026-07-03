# Checkpoint: Phase 4 feature 7 — Disk usage visualizer (treemap)

## A correction to the goal's own stated premise, found before writing any code

The goal says this feature is *"built from cached du output, already
collected every 15min from Phase 2."* On inspection, that's only partly
true: Phase 2 feature 5 (`daemon/usage.py`) only ever runs `du -sb <home>`
— **one scalar total per account**, no directory-level breakdown at all,
since that's all its own resource-usage dashboard ever needed. There is no
cached *tree* anywhere to read. Rather than silently building something
different from what the goal describes, or forcing a misleading "cached"
label onto data that isn't, this is documented explicitly and the feature
was built to do the honest thing: **reuse the one real cached number that
does exist** (the account's total, for the root node only) and **compute
the actual per-directory breakdown live, on demand**.

## What was built

- **`du --max-depth=1 -b <dir>`** for immediate subdirectory sizes, paired
  with **`find <dir> -maxdepth 1 -type f -printf '%s\t%f\n'`** for
  immediate files — confirmed empirically first that `du --max-depth`
  reports subdirectory subtotals only, never individual file sizes at
  that depth, which is why a second command is needed at all.
- **A genuinely interactive drill-down**, not a single eager recursive
  walk: each click into a subdirectory issues one fresh, shallow
  `du`/`find` pair scoped to just that directory — never an unbounded
  walk of a potentially huge tree the customer may never look at. This is
  the actual reason the feature is designed this way rather than
  precomputing a full tree up front: it scales to a large account home
  without a slow, wasted initial recursive scan.
- **The root node's total reuses Phase 2's real cached snapshot**
  (`UsageSnapshot.disk_home_bytes`, specifically the account-home figure,
  not the combined mail+DB total, since mail Maildirs and hosted database
  files live outside the tree this feature walks) when one exists and is
  still fresh within its own 15-minute window; falls back to a live
  `du -sb` otherwise (a brand new account, or one whose snapshot has
  aged out).
- **"Top 10 largest files" is a global, whole-account list** (`find
  <home> -type f | sort -rn | head -10`), shown alongside the treemap
  rather than scoped to whatever subdirectory is currently being viewed —
  matches how every real disk-usage tool presents this (the biggest
  offenders anywhere in the account, not just the current folder).
- **UI**: plain server-rendered links (`?path=...`) driving the
  drill-down, not htmx — ARCHITECTURE.md's stack table names htmx as part
  of the frontend approach, but a check across every existing template in
  this project found **zero actual uses of it anywhere** (`hx-get`, etc.
  don't appear in a single file) — every prior feature's UI, across four
  build phases, uses plain forms/links with full-page loads. Matching
  that actual, consistent practice was judged more valuable than
  introducing the first-ever use of a documented-but-never-adopted stack
  element for one feature. A real visual treemap (proportional-width bars
  per entry, CSS only, vendored/no-CDN per the same stack table) renders
  the size breakdown.

## Testing

`tests/test_disktree.py` (new, 9): a real directory tree with known file
sizes (not mocked `du`/`find` output — the correctness of this module
rests entirely on correctly parsing real command output, so real commands
are exercised), covering root listing, sort order, drill-down into a
subdirectory, path-traversal rejection (reusing
`daemon/filemanager.py`'s own jail check), top-10 global file ranking and
its 10-item cap, and both branches of the cached-vs-live root total (a
fresh `UsageSnapshot` row is used verbatim; no row falls back to a live
`du -sb`). **A real near-miss caught during test-writing**: the very
first draft of several tests didn't request the `isolated_db` fixture,
which would have made `get_disk_tree`'s unconditional cached-snapshot
lookup query this machine's *real* production database during routine
test runs — the exact class of mistake already caught once this phase
(`docs/CHECKPOINT-phase4-0b-cross-account-idor.md`). Fixed before any
test ran, by folding `isolated_db` into the shared `account_tree` fixture
so every test using it is safe by construction. 664 tests passing (up
from 655 after Feature 6).

## Live verification performed (the real Definition of Done)

Real account, a real file tree with known, distinct sizes (two "big"
files, one medium image, one tiny HTML file, spread across two
subdirectories), verified against **independently-run** `du`/`find`
commands (not Forgehost's own code, run separately by hand):

| Check | Independent `du`/`find` | Feature's real RPC output | Match |
|---|---|---|---|
| Account root total | `du -sb` → `10490569` | `10490569` | exact |
| `public_html` total | `du --max-depth=1` → `10485771` | `10485771` | exact |
| `public_html/uploads` | `8388608` | `8388608` | exact |
| `public_html/images` | `2097152` | `2097152` | exact |
| Top 3 files, in order | `bigfile1.zip`, `bigfile2.zip`, `photo.jpg` | same three, same order, same byte counts | exact |

Every figure matches byte-for-byte — the goal's DONE WHEN bar
(*"renders, top files match du output independently"*) confirmed
directly, not by trusting the code's own self-reported numbers. The
`disk_tree.html` template was also rendered directly through the real
Jinja2 environment with this exact data to confirm no template errors and
correct proportional bar-width computation (99.95% / 0.036%, matching the
real size ratio).

## What's untested / explicitly out of scope

- No full browser/HTTP-session walk-through of the UI page was performed
  (no convenient admin credential available in this pass) — the template
  was verified by direct Jinja2 rendering instead, and the underlying
  data (the actually novel, hard-to-get-wrong part) was independently
  cross-checked at the RPC layer, which the UI calls through the same
  `call_daemon` path every other feature's UI already uses.
- Very large trees (tens of thousands of files in one directory) were not
  stress-tested for `du --max-depth=1`/`find`'s real-world latency at
  scale — a documented timeout (30s for the per-level calls, 60s for the
  whole-account top-files scan) bounds worst-case behavior rather than
  letting a pathological account hang a request indefinitely, but the
  actual ceiling wasn't empirically found.
- Symlinks within an account's tree are followed by `du`/`find`'s own
  default behavior (not specially handled or excluded) — consistent with
  how this project's file manager (Phase g) already treats symlinked
  content for read operations.
