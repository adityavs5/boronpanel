# QA remediation — 30 September 2026

Input: user-supplied merged v3.1.0 QA report (Claude C / Sol S), 58 report IDs. Baseline code: v3.1.1 / a4fd288. Live baseline: v3.1.0.

Each ID remains separate and requires evidence. Statuses: pending, reproduced, fixed, verified, already fixed, informational, blocked with an explicit reason. Passing read-only screens do not prove writes work. Preserve Evo/Paper spacing, domain context, icons, branding and mobile behavior.

## Execution and release discipline

- Start with read-only live/code triage, then focused implementation and regression checks per batch. Use disposable accounts/domains/data for destructive checks; do not delete existing QA or customer data indiscriminately.
- Prove real generated-password login, account lifecycle, backup queue/recovery and application process transitions; mocked UI responses alone do not close these findings.
- Preserve SSH/panel recovery access during firewall work. Never reboot or replace stack versions on the live server as a QA side effect.
- Collect failures with redaction and correlation IDs; never return credentials, raw unrestricted tracebacks or foreign-account details.
- Run one final full suite/build and focused browser/live acceptance checks once all batches are implemented. Track any environment-bound validation separately from passes.
- Publication is deferred until the remediation and validation gates are complete. Do not claim future reviews can discover no additional defects.

## B1 — Critical write paths and safe diagnostics

| ID | Acceptance | Status / evidence |
| --- | --- | --- |
| H1 | On-demand account backups queue or return an actionable validation error | fixed — fallback protected local archive destination; real customer file backup #1 completed; archive restore recovered the original bytes and preserved an unrelated file |
| H2 | Generated account credentials authenticate in a real customer session | fixed — canonical customer login provisioned; generated passwords for qafix30 and qafix30b authenticated over HTTP; both sessions denied cross-account and firewall access |
| H3 | Username rename succeeds safely; collisions and unsupported prerequisites are clear | fixed — real qafix30b → qafix30new rename preserved UID and generated-password customer login; collision returned clear HTTP 400; unsupported app dependencies rejected before mutation |
| H4 | Promote an owned addon domain to primary with consistent state and rollback | fixed — owned addon promotion passed live and retained both document roots; promotion/rollback regression passed |
| H5 | Valid firewall additions succeed while protected ports remain protected | fixed — recovery executable installed with rollback support; live TEST-NET canary rule added, confirmed, deleted and confirmed; panel access preserved |
| H6 | Terminated resources disappear from operational inventories; historical data remains historical | fixed — real disposable termination removed the account and maintenance entry from operational inventories; historic data retained |
| M17 | Errors have a safe correlation ID and useful redacted diagnostic details | fixed — safe operation reference, exception class/return code and bounded repo frame locations; live Python failure carried a reference that identified its bounded provisioning step; diagnostic API and root-authorization regressions passed |

## B2 — Accounts, resource controls and administration

| ID | Acceptance | Status / evidence |
| --- | --- | --- |
| M1 | Every prebuilt plan has valid GB steps and can be saved | fixed — GB fields preserve integer MB and template soft memory uses valid steps; all four templates saved in Chromium on Evo and Paper (8-check browser batch passed) |
| M2 | Admin domain management preserves the target account context | fixed — explicit account-scoped admin domain routes; Chromium account-domain route checks passed in both themes |
| M3 | Legacy CPU allocations reconcile with host capacity and effective enforcement | fixed — API, cgroup policy and dashboard reconcile legacy CPU with host capacity; live amichi display and cpu.max both report 12 cores on the 12-core host |
| M4 | Account SSL tab scopes certificates to the selected account | fixed — account-scoped SSL excludes panel/global certificates; both-theme browser scoping checks passed; real customer sessions and scoped tokens deny foreign-account/admin access |
| M5 | First domain becomes primary when the account has none | fixed — first owned main domain becomes primary; handler regression and real first-domain primary assignment passed |
| M6 | Applying a plan immediately refreshes resource forms | fixed; frontend resource-management regression passed, account query invalidated after plan application |
| M7 | Creation contact email appears in Identity | fixed — contact email lookup repaired; 63 account regressions passed, including absent/present email; live account details returned successfully |
| M8 | Terminated account controls close or become read-only | fixed; terminated account redirects to Accounts and editable controls are hidden; disposable termination passed live |
| M13 | Memory allocations expose host capacity and overcommit warning | fixed; capacity and overcommit explanation rendered in plan/account/resource forms; resource-management browser suite passed |
| M14 | Validation errors use user-facing resource names and GB units | fixed — resource validation copy uses GB and user-facing labels; 180 validation/reseller regression tests passed |
| M18 | File Manager shows the account quota rather than filesystem capacity | fixed — FileBrowser source stats expose account quota and own usage rather than host filesystem; live FileBrowser source quota passed; initial and sourceUpdate event adaptation regressions passed |
| M23 | Customer API tokens require an account; scopes and explicit expiry enforced | fixed — required active customer account, read-only/full scopes and 1–90 day expiry at API/root authorization boundaries; focused authorization/lifecycle regressions passed |
| L3 | Confirm lifecycle/peer changes and disable suspended-account entry actions | fixed; lifecycle/peer confirmation and suspended-entry guards; reseller suspension and DNS-peer enablement verified live |
| L5 | Live username/email creation hints and clear validation | fixed; inline rules and submit gating implemented; account creation and both-theme form regressions passed |
| L6 | Empty reseller-plan name gives visible feedback | fixed; explicit reseller-plan name validation and inline feedback; reseller/template browser suite passed |
| L9 | Explain bulk namespace enablement and its effects | fixed; bulk-isolation button explains affected runtimes and preservation of files; resource/security browser suite passed |
| L10 | Safe administrator delete/password/2FA reset with last-admin/self safeguards | fixed — guarded administrator removal/password/2FA reset with session revocation and history preservation; focused authorization/lifecycle regressions passed |

## B3 — Domains, DNS, mail and destinations

| ID | Acceptance | Status / evidence |
| --- | --- | --- |
| M10 | DNS CNAME/name/zone errors are clear validation responses | fixed — apex/CNAME coexistence and out-of-zone/name validation before provider mutation; DNS regression passed |
| M11 | SRV and CAA creation/edit/validation supported end to end | fixed — SRV/CAA API and editor support; SRV/CAA raw preview/apply and cleanup passed against live PowerDNS |
| M12 | DNS editor explicitly adds or edits RRsets without silent value loss | fixed — explicit add/replace RRset modes preserve existing values; DNS append regression passed |
| M20 | Destination paths validated on save; removal confirmed | fixed; protected paths rejected before saving and removal confirmed; destination backend and Evo/Paper backup browser regressions passed |
| M21 | Backup account include/exclude grids load; one explicit destination and timezone picker; fresh-account paths ready | fixed — account grids, destination defaults, timezone picker, private default public_html and actionable missing-path errors; real freshly created QA account on-demand backup and byte-accurate file restore passed |
| M22 | Webhook signing secrets auto-generate; localhost/private endpoints rejected early | fixed; blank signing secrets generated and local/private endpoints rejected before saving; webhook backend and browser regressions passed |
| M24 | Redirect loops rejected and correct URL validation copy | fixed — direct self-redirect rejection and correct URL validation copy; real 302, self-loop HTTP 400 and maintenance HTTP 503 passed on the public QA domain |
| M25 | Mailbox quota editing available in account management | fixed; quota editor, SQL per-mailbox quota and Dovecot count enforcement; real IMAP quota advertised, oversized QA APPEND denied, original quota restored |
| M26 | PHP/document-root links match their actual controls | fixed; document-root link targets domain Advanced controls; scoped domain browser regressions passed |
| M30 | Forwarders work for a new owned domain or show clear prerequisites | fixed — owned mail domain provisioned when creating forwarder/catchall; empty reads explain no accounts; real mailbox quota edit, new-domain forwarder auto-provisioning and IMAP login passed |
| L7 | Empty mail queue actions disabled; delete-all confirmed | already fixed in baseline; empty queue actions disabled and delete-all confirmed; mail operations browser suite passed |
| L8 | Consistent strong mailbox/account password policy | fixed; mailbox minimum 12 characters matches accounts; validation and mail backend regressions passed |
| L12 | Validate intended authoritative mail/MX defaults and diagnostics | verified configuration; canonical webmail host is intentional MX target and mail alias. Trusted HTTPS, SMTP STARTTLS and IMAP TLS with actual self-delivery passed; existing certificates retained |
| L20 | Finished restore releases account locks promptly; busy state gives retry guidance | fixed; file/configuration/database recovery terminal status published after locks and staging release; 43 focused recovery checks, live retention and file/DB/mail/full-account recovery passed |

## B4 — Applications, operations and reporting

| ID | Acceptance | Status / evidence |
| --- | --- | --- |
| M9 | Migration result distinguished from current account state; clear failure details and supported retry | fixed; historic migration outcome separated from current destination account state; failed-item detail/retry and migration-progress browser checks passed; no live source migration performed |
| M15 | Updater never offers installation of its current version | fixed; current or older versions never offered as updates; updater regressions passed; final candidate reports v3.1.2 |
| M16 | Security Center distinguishes UFW rules, managed rules and WAF activation | fixed — distinguish discovered UFW rules, Boron-managed rules and actual WAF state; live firewall reports 15 configured rules and WAF separately disabled |
| M19 | Wizard accurately reports completed/current steps and dependencies | fixed; required first-incomplete step and completed count replace stale maximum; server-setup backend and browser checks passed |
| M27 | Repair supported OLS systemd configuration and keep logs useful | fixed; supported /run PID path and KillMode=mixed drop-in with rollback; runtime regressions passed and active unit checked. Full live OLS restart deliberately not exercised against customer traffic |
| M28 | Isolation self-test reports failed checks with matching state and toast | fixed; real PHP mount/home/private-PID canaries passed; inactive PHP is incomplete/informational with untested checks, never a false pass; two 39-check isolation runs passed |
| M29 | Node/Python state reflects real process/code readiness; Stop works visibly | fixed; actual unit/process readiness and visible stop state. Uploaded Node/Python code served through OLS; stop closed listeners and removed registration. Preparation deadlines bounded under account CPU quota |
| M31 | Audit result filter matches stored failed values | fixed; error filter resolves failed result; audit regressions passed |
| L2 | Read-only calls do not bury write events in the audit view | fixed; successful explicit read-only RPC calls omitted from new audit entries; historic read filter optional; audit/RPC regressions passed |
| L11 | Systemd service states have friendly labels | fixed; friendly runtime/enabled states; services browser and backend checks passed |
| L13 | Lifecycle source-IP attribution follows one trusted-proxy policy | verified — request peer IP is passed through the authenticated RPC envelope to audit/lifecycle events; uvicorn proxy_headers=False ignores forged forwarding headers. Loopback API tests and public browser requests correctly have different peer IPs |
| L14 | WordPress installation URL follows certificate readiness | fixed; default protocol follows certificate readiness; actual WordPress install and clone served trusted HTTPS |
| L15 | New cron uses a safer schedule default | fixed; daily 03:00 default; cron/browser suite passed |
| L17 | WordPress plugin inventory reports loaded rather than saved | fixed; inventory-loaded status replaces changes-saved copy; real plugin inventory completed and WordPress browser suite passed |
| L18 | Node/Python domain conflicts produce visible feedback | fixed; conflicting Node/Python domain/name raises actionable validation and toast; backend and app-browser regressions passed |
| L19 | Malware latest-scan scope comes from the stored scan | fixed; latest-scan label comes from persisted scan scope; malware browser suite passed |

## B5 — UI, headers, compatibility and final verification

| ID | Acceptance | Status / evidence |
| --- | --- | --- |
| L1 | Branding title has no default-title flash and supports page titles | fixed; branded initial HTML title and page-title preservation; public browser reloads and branded-title tests passed |
| L4 | Domain/maintenance mutations show pending state and domain validation | fixed; domain client hints and disabled/pending mutation controls; actual domain/maintenance changes and browser regressions passed |
| L16 | Dark-theme functional text/status contrast meets WCAG AA | fixed — darker light semantic colors and readable dark text variants; computed browser contrast checks passed in both themes, light/dark modes |
| L21 | No reported defect: retain and broaden mobile/responsive coverage | informational — original report found no defect; 52 live customer visits and 120 live administrator visits passed across both themes/light/dark/desktop/mobile |

## Additional supplied requirements and observations

| Item | Required handling | Status / evidence |
| --- | --- | --- |
| Admin per-domain management | DNS, PHP, SSL, redirects accessible with explicit account/domain scope; preserve API authorization | fixed; explicit account/domain DNS/PHP/SSL/redirect routes; scoped API and Chromium account/domain checks passed |
| DNS templates / raw zone editing | Safely validated zone templates and raw editor with preview, CNAME/RRset protections and rollback | fixed — provider-native atomic writes, fingerprint/typed confirmation, protected records, three additive templates, and root-private prior state; 71 backend and four Chromium checks passed; live SRV/CAA/TXT edits and stale-state rejection passed |
| Bulk operations | Confirmation, per-item progress and failure reporting for supported account/domain actions | fixed; typed/explicit confirmation, per-item progress and failure reporting; accounts/imports browser suites passed; no destructive bulk action on existing accounts |
| Security headers | Add Permissions-Policy, suppress unnecessary server header, review style CSP without breaking current themes; document any retained inline-style requirement | fixed — live Permissions-Policy present and Server header absent; style unsafe-inline retained for React/Radix dynamic styles |
| DNSB cluster peer | Verify peer identity/state then restore its enabled state as requested in the report, without touching unrelated remote zones | verified — peer #1 DNSB/directadmin/dns.b.scnservers.net enabled through its configuration API; no sync-all or foreign-zone edits |
| Admin 2FA / whitelist / WAF / sender / MaxMind | Improve readiness guidance and verify defaults; never silently enroll 2FA, restrict administrator access, change mail sender identity or buy/license services | verified as operator configuration; readiness distinguishes disabled/unconfigured controls. No forced enrollment, network lockout, sender identity or license change |
| SSH brute-force activity | Inspect managed Fail2ban coverage and safe recovery/bypass behavior; controlled validation | verified configuration — active sshd, boron-panel-login, dovecot, postfix, pure-ftpd and ols-scan jails; real panel login rate limit observed and respected; safe firewall canary lifecycle passed |
| anpadhprofessor.com 404s | Inspect metadata/configuration/log symptoms; distinguish customer-site content from panel defects | inspected — active account/domain, document root, index.php, .htaccess and active certificate exist. Percentage alone does not establish a panel bug; customer routing/content left intact |
| Customer and reseller authorization | Real sessions, cross-account denials, role boundaries, token scopes/expiry, lockout, 2FA, mailbox/FTP creation | verified; independent generated-password sessions, cross-account/role denials, scoped token creation/use/revocation, real QA 2FA/recovery cycle and rate limiting, mailbox/FTP lifecycle |
| Public-domain integration | Controlled PHP, redirects, custom errors, maintenance, TLS/renewal chain, delivery/webmail/IMAP/FTP; identify any unavailable external prerequisite | verified on qa.boron.sitecountry.com; PHP, redirects, maintenance, trusted certificate/chain, SMTP self-delivery, IMAP, customer/admin webmail SSO and TLS FTP. Renewal/hooks covered by tests; no artificial ACME renewal |
| Backup integration | Remote destination fixtures, schedules/retention, altered-file, DB/mail/full-account recovery with integrity proof | verified local live altered-file, database, mailbox and full-account byte recovery plus retention; remote-provider fixtures passed. Actual S3/Drive/SSH transfer awaits operator credentials and is not claimed tested |
| App integration | Deploy code, verify running/stopped process, Git push, cron, DB import/rename/restore, WP clone/update | verified uploaded Node/Python processes, proxy routes and stop; real WordPress install/plugins/backup/clone/one-use login. Git/cron/import/update contracts tested; every external operation not repeated against production |
| Operational integration | Service lifecycle, failure recovery, reseller edit/suspend, branding/settings/templates, read-only source migration inspection, disposable account purge | verified reseller lifecycle, disposable account purge, configuration and recovery regressions. No live source-account migration or whole-stack version upgrade/reboot undertaken; source protections preserved |
| Accessibility / enforcement | Evo/Paper responsive/contrast, bounded CPU/memory/I/O enforcement and truthful isolation coverage | verified sampled Evo/Paper desktop/mobile and semantic contrast; actual legacy CPU cgroup cap and PHP filesystem/private PID canaries. Full accessibility certification and production CPU/memory/I/O saturation not claimed |

## Batch notes

- Initial code triage: H4 is reachable in `daemon/identity_admin.py::set_primary_domain`: an already-owned addon is explicitly rejected instead of promoted. H2 investigation: `account.create` returns a Linux password but the inspected account-create path has no mandatory `PanelUser` creation; verify and repair the root-owned login lifecycle.

- Focused baseline checks: 73 authentication checks passed; critical batch 178 passed with 2 stale test-contract failures corrected; second batch 211 passed with webhook expectation and sandbox ownership issues identified. Follow-up host-access suites passed. These do not replace final candidate browser/live acceptance.
- A sandbox-only ASGI TestClient startup stall was diagnosed from thread stacks. The isolated host-access rerun passed (1 test); no stalled run is counted as passing.
- Public integration domain authorized by user: `qa.boron.sitecountry.com`. Preserve existing accounts and QA data.

- Additional validation: 180 validation/reseller tests passed; 27 Cloudflare zone/provider tests passed, including pooled-token dispatch and context cleanup. Original focused browser batch: 18 passed and 4 subdomain checks caught a newly introduced parked-domain mutation-name error. After correction and rebuilding, all 8 subdomain/template/account-scope checks passed. Failures were corrected, not counted as passes.
- Deployment probe correction: the root daemon intentionally rejects RPC socket peers other than the API UID. The deployment probe now connects through the boron-api account and verifies unauthenticated operations are denied. The automatic rollback protected the baseline during the incorrect probe.

- Live acceptance evidence: first-domain primary assignment, addon promotion with preserved document roots, Linux rename with stable UID and working customer login, username collision HTTP 400, termination maintenance cleanup, firewall add/confirm/delete/confirm, archive restore byte integrity, public PHP 8.3.33, 302 redirects/self-loop rejection and 503 maintenance response passed.
- A real account-detail request detected a missing AccountNotificationPrefs import introduced in the contact-email fix. Corrected, linted, and covered by new absent/present-contact regressions; the full account module passed 63 checks.
- Public TLS attempt exposed a pre-existing challenge-selection defect: a local managed zone was assumed publicly authoritative even when its parent uses external DNS. Fix now checks public NS delegation before local DNS-01 and uses HTTP-01 for ordinary undelegated hosted domains. Wildcards still require authoritative DNS control. Certificate/deployment regressions and trusted public QA TLS passed. References: [Let’s Encrypt challenge types](https://letsencrypt.org/docs/challenge-types/), [Certbot webroot documentation](https://eff-certbot.readthedocs.io/en/stable/using.html#webroot).

## Extended candidate acceptance

- Real customer 2FA enrollment, recovery-code login and subsequent QA enrollment removal passed. Read-only customer tokens read account/DNS/mailbox inventory, deny writes/admin access, and stop authenticating after revocation.
- Real reseller-generated customer credentials, reseller ownership boundaries, customer suspend/unsuspend, reseller company edit/suspension and disposable customer termination passed. Canonical customer login is reused once by reseller and import provisioners; 62 import and 99 reseller/Node/legacy-backup regressions passed.
- Public browser acceptance: 52 customer and 120 administrator visits, both Evo/Paper themes, light/dark, desktop/mobile; zero page errors and no tested global horizontal overflow. Actual screenshots inspected; branding and icon-grid design preserved.
- Live FileBrowser displayed the account quota. Pinned upstream `sourceUpdate` events were also found to update the quota independently; these now use the same account adapter. [Pinned upstream event implementation](https://github.com/gtsteffaniak/filebrowser/blob/v1.4.0-stable/frontend/src/notify/events.js).
- Additional recovery checks: 56 FileBrowser/database metadata/restore regressions, two last-added ownership/event checks, 34 Python/RPC/timeout checks, 56 SSL/deployment/updater checks, and 62 final legacy backup checks passed.
- Python preparation under the default 0.25-core quota exceeded 60 seconds. Its two preparation commands are now individually bounded at 180 seconds; its authenticated API RPC deadline covers both commands. Timeouts roll back the application registration and return actionable validation copy. Quotas are preserved.
- SMTP presented the old self-signed certificate despite trusted webmail HTTPS. Retained certificates now repair the canonical mail deployment even when Certbot skips its hook, and updater rollback covers the pair plus Postfix/Dovecot configuration. The existing trusted certificate was applied via validated reloads; no forced certificate renewal.
- Legacy retention now serializes against restore admission, preserves pending/running restore artifacts, retains referenced completed history as expired, and does not discard the storage pointer when remote deletion fails.
- Configuration observations remain explicit: notification sender unset, panel IP whitelist empty and WAF disabled. These are operator choices; no identity, enrollment, lockout or protection-mode change was made implicitly.
- GitHub authentication confirmed through a network-enabled read-only check. Publication remains behind the final regression/acceptance gates.

## Final acceptance additions and bounded coverage

- SMTP/IMAP certificate drift was repaired without forced renewal. Existing OpenDKIM keys were preserved byte-for-byte; directory traversal was repaired and signer-owned mode 0600 prevents the Postfix socket group from reading private keys. Twelve key-safety regressions and actual signed self-delivery passed.
- Temporary webmail credentials now have a separate passdb that falls through to the normal mailbox password database. Customer/admin trusted-HTTPS SSO, one-use replay denial and normal IMAP/SMTP authentication while SSO is active passed.
- Mailbox limits previously existed only in metadata. Installer/updater now enables Dovecot SQL per-user quota and IMAP reporting. All three existing active mailboxes were below their configured quotas before deployment. Seventy mail/runtime/installer regressions and real quota reporting/rejection passed; no existing mail was removed.
- Full-account recovery exposed standard Python venv interpreter links. Both archive and snapshot workers now permit only validated root-owned, non-writable system Python interpreter symlinks in the exact venv bin locations; arbitrary external links/hardlinks remain rejected. Twelve security cases plus 24 checks across both workers passed, and the same failed archive restored successfully with original file bytes, PHP HTTPS and mailbox login preserved.
- Snapshot completion and legacy retention now coordinate with account/repository locks and active restores. Live retention preserved three ready archives and marked old referenced history expired; terminal recovery statuses are visible only after locks/staging are released.
- Existing Evo/Paper icons, columns and typography were retained. A 64-pixel Evo customer metrics gap was fixed with a narrow padding override. Final dashboard/IP/template/typography batch: 25 passes. Original full browser run: 150 passes, eight failures corrected and rechecked; failures were not counted as passing.
- Actual WordPress installation, plugin inventory, backup, clone, trusted HTTPS and one-click login/replay denial all passed on the authorized QA domain.
- Remote backup service credentials, live DirectAdmin account migration, full-stack updates, external mail reputation/delivery and production saturation remain outside the verified live coverage. Fixtures and local checks do not replace those external acceptance tests.

## Final regression gates

- Full backend run: 3,970 passed, 11 skipped, six failures in 2h03m. Four were the earlier app-validation exception expectations; two version checks had imported v3.1.1 before version.py changed to v3.1.2 during that run. Fresh final-source rerun of all three affected modules: 50 passed, including all six failed cases. No unresolved failure remains from that run. The full run is not represented as a clean uninterrupted final-source run.
- Later changes were independently checked: mail/runtime/installer 70 passed; both archive workers 24 passed; venv-link safety 12 passed; idle isolation 39 passed; lock/recovery 43 passed; packaging/runtime/namespace/key checks 70 passed, two environment skips. These overlapping suites are not added together as unique test counts.
- Full frontend run: 150 passed, eight failures. All eight affected cases corrected and rerun; final dashboard/IP/template/typography suite 25 passed. Production build succeeded. Final stable public customer browser repeat: 52 route/layout checks, both themes/light/dark/desktop/mobile, no page errors or tested overflow; semantic text contrast at least 4.5:1. Administrator coverage: 120 visits.
- All changed runtime Python files match the deployed candidate; 89 changed Python files parsed, git diff --check passed, and pip check reported no broken requirements.
- Existing credentials, customer content, source DirectAdmin servers and prior backups were preserved. Disposable QA resources and protected recovery copies remain for review.

- Final packaging review hardened runtime configuration backup/rollback temporary writes: private mode from file creation, unpredictable names, fsync and atomic publication with restored owner/mode. Twelve runtime regressions passed, including permissive-umask exposure and hostile predictable-symlink preservation.
