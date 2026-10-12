# Testing Boron

Use the smallest test tier that can answer the question you are asking while
developing, then widen the check as the change settles:

```bash
# While editing one feature
scripts/test.sh targeted tests/test_wordpress.py -k clone

# Re-run only failures retained in pytest's cache
scripts/test.sh failed

# Update-system and recovery boundaries
scripts/test.sh update

# Curated cross-feature regression (about five minutes on the dev server)
scripts/test.sh quick

# Complete release-authorizing suite
scripts/test.sh full
```

`quick` currently runs 640 high-signal tests across authentication,
authorization, account APIs, update/rollback, WordPress, malware scanning,
firewall, SSL, DNS, databases, backups, and resource controls. `targeted`,
`failed`, and `quick` are feedback loops. They do not authorize a release.
`scripts/release.sh` always invokes `scripts/test.sh full` and does not inherit
a developer-selected tier, so a local shortcut cannot weaken the release gate.

Every tier runs through `scripts/test_workspace.py`. The default test root is
the short, root-owned, traversable directory `/srv/bt`; override it with
`BORON_TEST_TMPDIR` when a dedicated test volume is mounted elsewhere. The
directory is marked as test-only and contains only disposable test data.
Shared parents are mode `0755`; tests that need private content create their
own `0700` directories below them. The harness refuses a temporary path over
40 bytes so Dovecot and other Unix socket users remain below the platform
limit. A run is stopped above 8 GiB or below 5 GiB free by default
(`BORON_TEST_TMP_MAX_GB` and `BORON_TEST_MIN_FREE_GB`), and the newest three
runs are retained (`BORON_TEST_KEEP_RUNS`). Full and release runs refuse a
filesystem shared with `/var/lib/boron` or `/var/log/boron`. The
`BORON_TEST_ALLOW_SHARED_FS=1` escape hatch acknowledges the risk but does not
provide the protection of a separate quota-backed volume.

Every run prints skip reasons (`-rs`) and writes `skip-report.json` plus
`skip-report.md` below its artifacts directory. CI uploads the per-shard files
and one combined report.

The panel's self-updater also avoids a redundant full run. A release has
already passed the complete suite against the new code; before downloading it,
the live server runs the focused `update` suite to confirm its transaction,
rollback, RPC, and recovery mechanisms are healthy. These focused checks use
the same bounded workspace. Operators who want the old
behavior can set `update_preflight_full_tests = true` in
`/etc/boron/boron.toml`.
