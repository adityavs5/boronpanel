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
`scripts/release.sh` always invokes the complete suite directly and does not
inherit a test tier, so a local shortcut cannot weaken the release gate.

The panel's self-updater also avoids a redundant full run. A release has
already passed the complete suite against the new code; before downloading it,
the live server runs the focused `update` suite to confirm its transaction,
rollback, RPC, and recovery mechanisms are healthy. Operators who want the old
behavior can set `update_preflight_full_tests = true` in
`/etc/boron/boron.toml`.
