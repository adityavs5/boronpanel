# Expansion release validation — 2026-09-14

This records the final source-tree validation for the BoronPanel 1.3.0
expansion release. The working tree was clean at commit `8d5dbd2` before the
validation note was added.

## Backend

Command:

```text
.venv/bin/pytest -q
```

Result: **2,733 passed**, 7 warnings, in 56 minutes 54 seconds. The warnings
were existing dependency deprecations, duplicate OpenAPI operation-ID notices
for the file-browser proxy, and `fork()` deprecation notices in two snapshot
tests. There were no failures, errors, skips or unexpected exits.

## Browser and themes

Command:

```text
npx --offline --yes node@20 node_modules/@playwright/test/cli.js test
```

Result: **130 passed** in 12 minutes 30 seconds. The run covered administrator,
reseller and customer flows in Evolution and Paper Lantern, including their
light and dark variants where applicable. It included account migration,
portable backups and restores, malware scanning, firewall and OpenLiteSpeed
controls, global SSL, multi-IP allocation, package templates, suspension-page
templates, responsive layouts, fuzzy search and the complete WordPress install,
management, backup and clone workflow.

The four subdomain cases that had exposed a misplaced component effect in the
previous broad run all passed. The WordPress related-search assertions also
passed with the intended DNS Management and Email DNS Records results.

## Release pipeline rehearsal

`scripts/release.sh --dry-run --skip-tests --skip-build` successfully staged
all tracked files, assembled the archive, generated and verified its SHA-256
checksum, checked path confinement and the packaged version, and verified the
required runtime files. The actual 1.3.0 release pipeline must still rerun its
mandatory test and build gates before it creates and publishes the tag.
