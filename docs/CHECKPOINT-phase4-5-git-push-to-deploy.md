# Checkpoint: Phase 4 feature 5 — Git version control (push-to-deploy)

## What was built

- **`git init --bare ~/repos/<name>.git`**, run via `runuser -u <account>`
  (the same identity model every other per-account operation in this
  project uses — ARCHITECTURE.md SS6's suEXEC-equivalent isolation; a
  bare repo's contents are owned by the account, not root).
- **Classic push-to-deploy `post-receive` hook**, generated and installed
  by `daemon/gitrepo.py`'s `set_deploy_target`: on a push to `main` or
  `master`, `git --work-tree=<target> --git-dir=<repo> checkout -f
  <branch>` checks the new tree out directly into the configured deploy
  directory, then appends a line to a push log inside the bare repo.
  Pushes to any other branch are recorded (skipped, not deployed) rather
  than silently ignored. **The exact hook script was built and verified
  against a real, standalone git repo before being wired into the
  feature** — a real `git push` (including an update push and a
  non-main-branch push) was tested directly against a hand-written
  version of the hook first, confirming the checkout/branch-filter logic
  actually works, before the verified script became
  `daemon/gitrepo.py`'s `POST_RECEIVE_TEMPLATE`.
- **WP-CLI-gated cache flush**: the hook only calls `wp cache flush` when
  both a `wp-config.php` exists in the deploy target *and* `wp` is
  actually on `PATH` (`command -v wp`) — WP-CLI is deliberately not
  installed server-wide (Phase 3 feature 2's own finding: this
  environment's permission classifier denied installing it from an
  agent-chosen source, and that denial was respected, not worked around,
  same standing rule this project has followed since). If an operator
  installs WP-CLI manually later, deploys pick it up with zero
  Forgehost-side change; if not, that one step is silently skipped
  rather than failing the whole deploy.
- **`GitRepo`** (account_id, name, deploy_target) is the only DB state —
  push history lives in the repo's own on-disk log file, read fresh on
  each `git.repo.push_log` call, not duplicated into the database.
- **Validated before install, same discipline as every config-writing
  feature in this project** (ARCHITECTURE.md SS7): the rendered hook
  script is checked with a real `bash -n` before being moved into place,
  so a template bug would be caught immediately rather than silently
  breaking every future push.
- **API**: full CRUD (`/accounts/{u}/git`) plus a `/deploy-target` PATCH
  and a `/push-log` GET. UI: list repos, create, configure deploy target,
  view recent push log, delete.

## Testing

`tests/test_validation.py` (+13): repo-name charset (lowercase only, no
dots/slashes — a name can never itself look like a path segment).
`tests/test_gitrepo.py` (new, 10): repo create/list/delete, duplicate/
invalid-name rejection, deploy-target hook generation (asserts the
rendered hook is actually executable and references the real resolved
path), missing-deploy-dir auto-creation, and empty-push-log-before-any-
push. The hook-generation test exercises a **real** `bash -n` syntax
check (not mocked) — fast, offline, and the actual validation gate this
feature relies on. 635 tests passing (up from 612 after Feature 4).

## Live verification performed (the real Definition of Done)

Real account, real bare repo, real deploy target, through the actual
feature's RPC ops (not a re-run of the pre-implementation hook probe,
which was against a throwaway scratch repo, cleaned up before any real
feature code was written):

1. `git.repo.create` + `git.repo.set_deploy_target` — confirmed the real
   bare repo and the real, executable `post-receive` hook on disk,
   correctly referencing the resolved absolute deploy path.
2. **Real `git push`**, as the account's own Linux user (`runuser -u
   p4gittest`, exactly how a real SSH-based push would run once Feature
   6 wires up SSH login) — a real commit, pushed to `main`.
3. Confirmed the pushed file appeared in the deploy target, owned by the
   account's own user, with the exact pushed content — matches the goal's
   DONE WHEN bar directly: *"push deploys files to docroot."*
4. A second push (a content update) — confirmed the deploy target picked
   up the new content, proving redeploy-on-update works, not just
   first-push.
5. Confirmed via `git.repo.push_log` (the real API, not a direct file
   read) that both pushes were recorded correctly.

## What's untested / explicitly out of scope

- **Real SSH-based push end-to-end** was not tested in this checkpoint —
  that requires Feature 6 (SSH key management, next) to actually enable
  login for hosting accounts (still `/usr/sbin/nologin` as of this
  checkpoint). The local `runuser`-based push tested here exercises the
  *identical* server-side mechanism (git invokes the same `post-receive`
  hook regardless of transport — local filesystem, SSH, or git-daemon all
  converge on the same server-side hook once objects are received), so
  this is a faithful test of the actual deploy mechanism, not a
  substitute pending a real SSH confirmation once Feature 6 lands.
- `wp cache flush`'s gated code path was not live-tested (would require a
  real WordPress deploy target and WP-CLI installed, neither in scope
  here) — the `command -v wp` guard itself was read-reviewed, not
  exercised.
- No repo size limits or push-size throttling — not asked for in the
  goal.
- Deleting a repo that's mid-push (a push still writing to the bare repo
  when `git.repo.delete` runs) — not tested; low-risk edge case
  consistent with this project's existing posture on concurrent
  operations against the same resource.
