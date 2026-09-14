# Initial phase requirement audit — 2026-09-14

The initial implementation is deployed and released as v1.2.0. Actual self-update job 6 is still in preflight; this audit does not claim its version switch has succeeded. The subsequent expansion remains part of the continuing goal and must begin only after initial-phase verification is complete.

Earlier development notes describe historical pending work. This matrix identifies the final evidence for the original requirements; private QA artifacts remain outside the repository because some contain generated credentials.

| Requirement | Evidence and result |
| --- | --- |
| WordPress scan/import, refresh, soft and hard removal | WORDPRESS-VERIFICATION.md records live discovery and login, tenant rejection, retained website after soft removal, rediscovery, and explicitly approved hard-removal job 30 for only the disposable plainhttp clone. Lifecycle/backend/browser regressions passed. |
| HTTP/HTTPS and www selection, including clones and login | WORDPRESS-VERIFICATION.md records real WordPress home/siteurl values, subdirectory handling, HTTP/www and HTTPS/www authenticated dashboards and rejection of reused login tokens. |
| Incremental backup jobs, filters, SSH destinations, notifications, retention and restores | BACKUP-COMPLETION-AUDIT.md maps real restic reuse/restored bytes, isolated SSH with pinned host keys, loopback notifications, MariaDB/IMAP and configuration recovery. Both-theme workflows passed. Cloudflare provider mutation is explicitly unverified without a connected account. |
| Independent domains/subdomains | Subdomain browser coverage and live-subdomain-proof.py verify independent public_html roots, ownership, served probes and local authoritative A records. QA probes were removed. |
| Mailbox creation on an unprovisioned mail domain | mail-provision-proof.log and WORDPRESS-VERIFICATION.md record automatic mail-domain provisioning and actual IMAP authentication. |
| Separate Node.js and Python entries | Dedicated customer routes, dashboard/search entries and creation workflows are covered by the complete browser suite. |
| phpMyAdmin | PHPMYADMIN.md and PRODUCT-EXPANSION-GOAL.md record database-scoped access and issuance/renewal. Final authoritative DNS checks returned 104.234.179.66 and trusted HTTPS reached the sign-in redirect. |
| Panel SSL and renewal | PRODUCT-EXPANSION-GOAL.md records certificate issuance, renewal dry run and deploy hook, plus scheduled renewal. Fresh trusted HTTPS/admin checks passed. |
| Shared default 2222 and configurable separate role ports | PANEL-PORTS.md records persistent jobs 1–3 and real shared/separate/shared transitions with correct-role acceptance and wrong-role rejection. Final authenticated baseline confirms both ports are 2222. |
| Direct, usable management controls | FINAL-REGRESSION-2026-09-14.md records database, SSL, domain, application, FTP, Git, cron, DNS, mailbox and SSH-key controls, keyboard separation, mobile layouts and the complete both-theme browser pass. |
| Configurable BORON terminal welcome | TERMINAL-WELCOME.md and PRODUCT-EXPANSION-GOAL.md record literal admin banner editing and quiet prompts. Live terminal-branding/customer-WebSocket proofs preserve account UID, customer isolation and restored QA branding. |
| PHP defaults, site overrides and limit templates | PHP-CONTROLS.md and PRODUCT-EXPANSION-GOAL.md record Custom default, Lite/Moderate/Max, explicit saves, new-site inheritance and per-site versions. live-php-controls.py and live-php-version-matrix.py verify served PHP runtimes; QA returns to its baseline. |
| Font and dashboard label improvements without added network cost | Complete browser coverage includes local font budgets, no external font requests, both themes and mobile rendering. Deployment verifies cache behavior and locally served assets; typography evidence is indexed in PRODUCT-EXPANSION-GOAL.md. |
| Admin/customer 2FA and reliable clock handling | Live two-factor proofs cover enrollment, login, invalid codes, one-use recovery, disable validation and cleanup. Clock is synchronized with configured chrony/NTS; browser/backend coverage includes health diagnostics. Absolute immunity to host/network failure is not claimed. |
| Build, broad regression, deployment and release | All 114 browser checks passed; real release pipeline passed 2,657 backend tests with no skips, built the frontend and verified the archive. Published v1.2.0 assets were downloaded and matched local files byte-for-byte with a valid SHA256 checksum. |
| Actual self-update and preservation | Pending job 6. verify-release-update.py has captured the healthy 1.1.3 baseline; its after phase must confirm live 1.2.0, service/HTTPS health and preserved configuration/WordPress/backup inventory before this gate can pass. |

The subsequent menu order, malware scanner, firewall, editable OLS administration, admin SSL, package presets, multiple IPs, portable account migration, cPanel/DirectAdmin imports, resource utility, reseller support and suspension templates are preserved in PRODUCT-EXPANSION-GOAL.md. They are not counted as completed by this initial-phase audit.
