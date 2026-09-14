# Expansion Batch E — portable accounts and imports

Completed 2026-09-14. Deployment remains grouped with the expansion release.

## Portable Boron archive

Full account backups now identify themselves as `boron-account-archive`
version 1. The manifest records every component's relative path, byte size and
SHA-256 digest. The archive contains account limits and PHP settings, domains,
home files, database dumps, mail, cron and DNS data already collected by the
existing full-backup engine.

Administrators can download a completed full backup as `<username>.boron.tar`
from an account's Backups tab and upload it on another Boron server from Account
Migrations. Import rejects username mismatches, missing, duplicate, extra or
tampered components, path traversal, unsafe links and special files. Both outer
and nested expansion are bounded. The API upload is copied through an
`O_NOFOLLOW` descriptor into root-only staging before validation and restore,
then all temporary copies are removed. Newly recreated Linux/panel credentials
are available through one job-detail response and immediately cleared.

Existing in-place full restores remain compatible with older Boron backup
artifacts that predate the version/checksum fields.

## cPanel and DirectAdmin migrations

The cPanel importer is now a unified external account migration engine. Existing
cPanel job rows gain an additive `panel='cpanel'` field and an optional one-time
credential field without rewriting the table.

DirectAdmin user backups are recognized by their documented `backup/`,
`domains/` and `imap/` layout. The adapter reads `backup/user.conf` and
`backup/domains.list`, maps primary and additional website document roots,
database SQL dumps, Maildirs, cron, DNS zone, certificate/key and FTP metadata
into the same normalized pipeline used by cPanel. The pipeline then creates the
account and reports each domain, file, database, WordPress rewrite, DNS, SSL,
mailbox, FTP and cron action independently, allowing supported items to finish
when another item cannot be imported.

The React administrator page at `/app/import/accounts` combines cPanel,
DirectAdmin and Boron jobs, uses format/source dropdowns, polls active work,
shows component or per-item results, and shows generated credentials once. The
legacy `/app/import/cpanel` URL redirects to the new page.

## Verification

- `116 passed`: backup, portable archive, cPanel/DirectAdmin, admin authorization
  and API schema tests.
- `40 passed`: focused external importer suite with a synthetic DirectAdmin
  archive using the documented directory layout.
- `60 passed`: portable archive plus backup safety/regression suite after private
  staging hardening.
- `npm run build`: production bundle passed.
- Playwright `account-imports.spec.js`: 2 passed, Evolution and Paper Lantern,
  including completed-job credentials, format selection and phone-width overflow.

No real customer account was imported and no live service was changed in this
batch. A real DirectAdmin-produced archive remains a release-candidate live
fixture requirement when one becomes available; the parser is based on
DirectAdmin's published backup layout and synthetic behavioral coverage.
