# DirectAdmin migration fidelity

## Implementation order

1. Preserve all recent migration/progress fixes on master (completed).
2. Parse native backup/domain metadata, nested home archives, subdomain lists and document-root overrides; preserve relative paths instead of flattening sites.
3. Resolve actual PHP versions from source metadata/runtime inventory. Never reinterpret selector slot numbers as versions or silently upgrade unsupported versions. Apply explicit per-domain versions after ownership checks.
4. Preserve supported mail password hashes, empty mailboxes, quota, mail folders, forwarders and catchall. Report unsupported filter/vacation/list settings before cutover.
5. Inventory Passenger/CloudLinux Node.js and Python applications, entrypoints, runtime versions, app roots and environment. Recreate supported apps through Boron's unprivileged application handlers; block unsupported runtime/path configurations before account creation.
6. Show a per-component preflight report, keep passwords/environment values out of progress logs, and run SQL compatibility checks for uploaded DA archives as well as remote imports.
7. Run fixtures for real native layouts, ownership/path safety, mixed PHP, mail credentials, app detection and orchestration. Test real source examples when available, inspect UI, then deploy and commit on master. No release/tag/push until requested.

## Sources

- https://docs.directadmin.com/directadmin/backup-restore-migration/
- https://docs.directadmin.com/webservices/php/multiple-php.html
- https://docs.directadmin.com/changelog/version-1.60.4.html
- https://docs.directadmin.com/custombuild/customize-everything.html
- https://docs.cloudlinux.com/cloudlinuxos/command-line_tools/
- https://docs.cloudlinux.com/cloudlinuxos/control_panel_integration/

DA's php1_select identifies a CustomBuild slot, not a PHP version. Subdomain overrides contain URL-encoded settings and are relative to the account home. Native backups include mail account credentials separately from mailbox data. CloudLinux application environments need reconstruction for the destination OS; copying a virtualenv or node_modules does not establish runtime compatibility.

## Acceptance boundaries

No silent default-version substitution, lost mailbox credentials, hidden per-item failures or source DNS cutover. Block configurations whose runtime or document root cannot be established safely; provide actionable findings. Source accounts remain intact. Backend tests and mocked browser tests are distinguished from real-source acceptance. Arbitrary hand-managed daemons, external services, binary extensions and undocumented plugins require explicit review, not a claim of universal migration support.

## Verified source layout and current limits (2026-09-24)

Read-only HTTPS inspection of the user-selected Ozone account confirmed the account-level `.cl.selector/node-selector.json` format, a Node 20 application rooted at `nodeapps/backend`, `server.js`, root Passenger routing, and `process.env.PORT`. An anonymized fixture covers that schema, including application mode and state. No Ozone backup, app start, account mutation, or database operation was performed. This is inventory validation, not a completed live account migration.

The PHP API may return only `php1_ver` while showing all actual versions in `php1_select` option text. Inventory resolves each slot from that text, including a selected sixth slot, instead of using the first version as a fallback.

Implemented paths include native nested home archives, domain/subdomain document roots, installed PHP version matching and declared Composer extensions, SHA-256/SHA-512 mailbox credential preservation, Maildir data, quota, forwarding, single-destination catchall, grouped DNS values, and certificate chains. Supported root-mounted Node/WSGI apps are recreated through customer application handlers with dependencies rebuilt as the customer. Analyze-only still creates a source backup and is explicitly labelled accordingly.

Conservative preflight blockers remain for application database configurations outside the verified Node mysql2 / DB_* dotenv layout (framework-specific credentials are not guessed), renamed application accounts, unsupported runtime versions, path-mounted/multiple applications on one hostname, unrecognized selector formats, separate HTTPS content, system mailbox messages without verified credential mapping, and unconverted mail policies. The archive reader continues to reject symlinks except the known DA private_html alias; backups containing other links need an explicit safe conversion before import. These are reported failures, not claims of complete migration support. Customer scheduled jobs and external service connections make starting copied production apps unsafe without a separate acceptance plan.

The inspected Node app also confirmed the supported `src/config/database.js` / `DB_*` dotenv layout. Its source files were checked privately without executing the app or opening a database connection. A matching local dump is required. Restore rewrites only the destination app copy and its managed environment to the newly provisioned database credentials; failures to restore that database prevent app startup. External databases and ambiguous dotenv syntax are not automatically changed.

Validation: 133 focused backend tests passed, covering native normalization, PHP selection, mail fidelity, Node/Python compatibility, database mapping, rollback, primary-domain reuse, archive safety and remote flow. Four focused Playwright scenarios passed (analysis mode, retry, and progress recovery in Evo/Paper). Production frontend build passed. No live Ozone account migration or production application execution was performed.
