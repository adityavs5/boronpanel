# WordPress Manager and theme verification

Verified on the Ubuntu 24 development server on 2026-09-13.

## Automated checks

- Full panel regression run: **1,880 passed, 2 optional skips**. The suite includes release artifact verification. Three pre-existing dependency/OpenAPI warnings remain.
- Final focused WordPress regressions after live integration fixes: **60 passed**. These cover account ownership, private inventory/activity, login handoff CSP, token construction, safe archive paths, rollback after failed restore, separate clone credentials, nested-site preservation, and conflicting-operation prevention.
- Browser suite: **22 passed** across Evolution and Paper Lantern, admin/customer dashboards, light/dark interior pages, mobile layouts, forms, keyboard navigation, search aliases/typos, and installation/management workflows.
- Production frontend build, Python compilation, shell syntax and whitespace checks passed.

The broad suite and focused suite overlap; these counts should not be added together. Final live fixes were followed by the affected WordPress regression suite and browser checks.

## Real-server verification

A disposable `wpdevqa` hosting account was created through the panel API. Tests used real OpenLiteSpeed, account-scoped PHP, MariaDB, WP-CLI and WordPress 7.1, rather than mocks.

Verified:

- API installation and a complete customer browser installation wizard, including successful credential display and site discovery.
- WordPress served HTTPS after subfolder installation; static CSS assets were readable too.
- Admin inventory across accounts and customer inventory restricted to the current hosting account.
- Customer denial of all-account inventory and another account's login operation.
- One-click login reached the real authenticated WordPress dashboard.
- Invalid tokens and consumed-token replay were rejected; an unused token was rejected after its actual 90-second expiry.
- Plugin/theme discovery, plugin activation/deactivation, theme switching, the core-update command, cache flushing, and maintenance on/off.
- Private file/database backup creation and database-value restoration.
- Cloning to another domain and into a same-domain staging folder with an independent database, rewritten URLs and disabled search indexing.
- Changing clone data left the original database unchanged.
- Parent-site backup/restore preserved separately installed subfolder sites and their independent databases.
- Parent, staging and customer-installed pages, plus static assets, returned HTTPS 200 after restore.

The development domains were resolved locally in tests and used the server's development certificate. Tests did not assert public DNS or a publicly trusted certificate for those disposable names.

## Integration fixes found during testing

- Installed PHP's MySQL extension and a checksum-verified, pinned WP-CLI at the configured path; included the dependencies in the installer.
- Kept administrator/database passwords out of process arguments.
- Added a same-origin POST login handoff with a narrowly scoped nonce-based CSP, preserving the main panel's `form-action 'self'` policy.
- Preserved the SSL-validation and empty error-page folders created for new domains.
- Restored OpenLiteSpeed's read/traverse ACL as the hosting account after installation, restore and cloning.
- Used the configured MariaDB socket in cloned WordPress configuration and guarded the ABSPATH definition.
- Protected independent subfolder installations during parent backup/restore.
- Bounded API graceful shutdown so open file-browser streams cannot hold a restart indefinitely.
- Corrected themed dropdown backgrounds so their arrow image does not tile.

## Evidence and deployment

Protected server logs, screenshots and the pre-change deployment backup are outside the repository under `/root/boron-setup/`. Relevant files include `wp-final-full-tests.log`, `wp-final-wordpress-regressions.log`, `wp-final-browser-tests.log`, `wp-live-browser.log`, `wp-live-security.log`, `wp-live-wizard.log`, and `wp-live-final-restore.log`.

User instructions: [WordPress Manager](WORDPRESS-MANAGER.md). Design sources: [UX research](WORDPRESS-UX-RESEARCH.md).

The QA account was retained: automatic approval review required explicit permission before permanently deleting its sites and databases.

## Discovery and interior-page follow-up

The follow-up deployment adds explicit scan/import and per-site refresh, panel-record removal, confirmed permanent removal, and directly accessible database/SSL management dialogs.

- Full regression run: **1,895 passed, 2 optional skips, 3 existing warnings** (`wp-polish-full-tests.log`).
- Browser suite: **22 passed** in both themes, including discovery/refresh controls, permanent-removal confirmation, installation, extensions, backups, cloning, search, mobile layouts, and database/SSL dialogs (`wp-lifecycle-browser.log`).
- Focused backend run: **98 passed**; additional callback, removal-transaction and inventory-ownership checks passed. These overlap the broad suite and should not be added to its count.
- Final ownership review verifies that metadata from a domain's previous account cannot appear in the current account's inventory. Seven affected regressions passed after that adjustment (`wp-inventory-isolation-tests.log`).
- Live scanning refreshed six existing QA installations; individual refresh, authenticated one-click login and consumed-token rejection passed. Customer whole-server/cross-account scans and cross-account refresh were rejected.
- Permanent-removal tests exercise filesystem staging/rollback, nested-site preservation, database ownership/shared-database refusal and database-cleanup callback ordering. They do not delete existing live user installations.
- Production build, Python compilation and whitespace checks passed. The pre-deployment code backup is `wp-polish-pre-deploy.tar.gz` under the protected setup directory.

The final deployed services and login were checked after restart. Live evidence is in `wp-polish-live.log`, `wp-polish-live-browser.log` and `wp-scan-access-check.log` under `/root/boron-setup/`.
