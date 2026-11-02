# Boron 3.3.1: real-domain QA report for uptimeways.com

- **Server:** demo.boronpanel.com (80.47.12.101), Boron 3.3.1 full profile, `/opt/boron -> /opt/boron-3.3.1`
- **Date:** 2026-11-02, 03:53 to 05:25 UTC
- **Method:** API driven as the browser does (cookie session with same-origin headers, scripts in `/root/qa/uptimeways/`), public checks from a Windows PC, and a headless Chrome (Playwright) run through 38 customer screens.
- **Line numbers** refer to the deployed code in `/opt/boron-3.3.1`. Local `HEAD` differs by a few lines in some files.
- **Error references** can be found with `journalctl -u borond -u boron-api | grep <ref>`.
- **Retest on 3.3.3 (2026-11-02, 11:08 to 11:25 UTC):** see [Retest on 3.3.3](#retest-on-333) at the end. 15 of the 17 checklist items are fixed, and the regression pass is clean. Still open: H1 (the certificate now includes www, but a reissued certificate is not loaded until some unrelated change reloads the web server) and H8 (phpMyAdmin is installed but cannot be used until an admin sets a hostname). New: N1 (deleting a reseller returns 502 although the delete succeeds).

## Summary

| Severity | Count | Items |
|---|---|---|
| Critical | 2 | C1 customers cannot add any domain or subdomain; C2 all authenticated outgoing mail bounces |
| High | 8 | H1 www missing from the SSL certificate; H2 staging always fails; H3 autoresponders and spam-to-Junk never run; H4 full restore does not recreate deleted mailboxes; H5 a terminated account cannot be restored from its backup; H6 Python apps crash-loop; H7 SSH still works while an account is suspended; H8 phpMyAdmin is not installed or configured |
| Medium | 8 | M1–M8 |
| Low / info | 10 | L1–L10 |

## DNS records added (Cloudflare zone uptimeways.com, all DNS only)

The A record for `uptimeways.com` and the wildcard A `*.uptimeways.com` pointing to 80.47.12.101 were already there. Because of the wildcard, `www`, `mail`, `staging`, `clone`, `app` and `py` all resolve to the server.

| Type | Name | Content | TTL |
|---|---|---|---|
| MX | `uptimeways.com` | `mail.uptimeways.com`, priority 10 | Auto |
| TXT | `uptimeways.com` | `v=spf1 mx a ~all` | Auto |
| TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:postmaster@uptimeways.com` | Auto |
| TXT | `default._domainkey` | `v=DKIM1; k=rsa; p=MIIBIjANBgkqh…QIDAQAB` (the panel-generated key for uptimeways.com) | Auto |

I did not add an explicit A record for `mail`, because the wildcard covers it. I made no changes to the `boronpanel.com` zone (see M5).

## Test matrix

Status key: PASS, FAIL, BLOCKED, PART (partly works). "(UI)" means it was also checked in the real SPA.

### 1. Hosting account and SSL
| Test | Result |
|---|---|
| Admin creates plan `uw-standard` and account `uwcust` with primary domain uptimeways.com | PASS on the 2nd try (first try failed, see M1) |
| Site serves over HTTP from the public internet (welcome page) | PASS |
| Let's Encrypt for the apex: issued, `https://uptimeways.com` serves a valid LE certificate | PASS |
| Let's Encrypt covers www: `https://www.uptimeways.com` | **FAIL, H1** |
| http:// redirects to https:// after issuing | not offered (L3) |
| SSL for the subdomain `clone.uptimeways.com` (15 s) | PASS |
| GET of a non-existent account returns 404 | FAIL, L1 (returns 502) |

### 2. WordPress
| Test | Result |
|---|---|
| Install through the WordPress installer (https, custom admin user), 7 s | PASS |
| WordPress listed under installs and in the manager page (UI) | PASS |
| One-click SSO into wp-admin; the login link works once only (reuse returns 404) | PASS |
| Create a post through wp-admin (REST with a nonce) | PASS |
| Install the classic-editor plugin from wp-admin (direct filesystem, no FTP prompt) | PASS |
| Install the Twenty Twenty-One theme from wp-admin | PASS |
| Panel: plugin_check_updates and theme_check_updates detect outdated versions | PASS |
| Panel: plugin_update (classic-editor 1.6.5 to 1.7.0) and theme_update (2.0 to 2.9) | PASS |
| Panel: activate, deactivate, cache_flush, core_check_update, search_replace preview, refresh | PASS |
| Validation: unknown action, bad slug, unknown plugin | PASS (400 / failed job) |

### 3. Cloning and staging
| Test | Result |
|---|---|
| One-click staging (`POST …/staging`) | **FAIL, H2** |
| Add a subdomain as the customer, to use as the clone target | **FAIL, C1** (done as admin instead) |
| WordPress Manager clone to a fresh subdomain | FAIL first (M3), PASS after deleting `welcome.html` |
| Clone independence: separate DB (`uwcust_wp9a7b`), separate files, post on the clone not on the original | PASS |
| Clone gets SSL automatically even though the target URL is https | no (L4) |
| Push or sync staging back | BLOCKED by H2 (no staging environment) |

### 4. Backups and restore
| Test | Result |
|---|---|
| Customer full backup (118 MB, 9 s); manifest and archive contents checked (home, both DBs with the canary post, maildirs) | PASS |
| Partial backups: file (public_html), single database, all databases, single mailbox | PASS |
| Backup of a database owned by another account is refused | PASS (but returns 502, L2) |
| Delete post, file and mailbox, then full restore: post and file come back | PASS |
| Full restore brings back a deleted mailbox | **FAIL, H4** |
| Partial restores: DB (post back), file (canary back), mailbox (messages back), all DBs | PASS |
| Customer downloads their own (legacy) backup | FAIL, M4 (admin only); admin portable-archive download PASS |
| Snapshot system: local destination, initialize, policy (daily, retention), manual run (346 MB in 3 s) | PASS |
| Snapshot: customer browses files, DBs and mailboxes; download (117 MB tar.gz, ready in 20 s) | PASS |
| Snapshot selective restore: file, database (with safety snapshot), mailbox | PASS |
| Snapshot restore of the DNS configuration | **FAIL, M6** |
| Restore a terminated account from its panel backup | **FAIL, H5** |
| Portable archive import (Admin > Import Boron) recreates the terminated account: files, DB, mailbox, site | PASS (same username only, L9) |
| Remove a test snapshot destination and policy afterwards | FAIL, L8 |

### 5. Email
| Test | Result |
|---|---|
| Create mailboxes info, sales and support; weak password, bad local part and foreign domain are rejected | PASS |
| Duplicate mailbox gives a clean error | FAIL, M7 (502) |
| Forwarder, autoresponder (API), spam settings, DMARC, catch-all and routing reads | PASS (API) |
| IMAP 993 and 143 STARTTLS login; wrong password rejected | PASS |
| POP3 (110/995) and SMTPS 465 | not offered (L5) |
| Send with SMTP AUTH on 587 to a local mailbox | **FAIL, C2** (bounced) |
| Sender spoofing (From another mailbox) rejected with 553 | PASS |
| Unauthenticated relay to an external domain rejected with 454 (RCPT only, nothing sent) | PASS |
| Inbound on port 25: plain message, forwarder hello@ to sales@, unknown user rejected with 550 | PASS |
| SpamAssassin tags GTUBE (score 998.5, `[SPAM]` subject) | PASS |
| Spam moved to Junk | **FAIL, H3** |
| Autoresponder reply sent | **FAIL, H3** |
| DKIM signing of outgoing mail | BLOCKED by C2 |
| Webmail SSO (Roundcube, inbox loads) when `webmail.demo.boronpanel.com` is resolved locally | PASS |
| Webmail and mail hostname reachable publicly with a valid certificate | **FAIL, M5** |
| Email DNS readiness reflects the public DNS | FAIL, M2 |
| No mail sent to external addresses | n/a |

### 6. Site tools
| Test | Result |
|---|---|
| DNS editor: add and delete A and TXT (checked with `dig @127.0.0.1`); invalid IP, bad type, CNAME at apex and foreign zone rejected | PASS |
| Advanced zone view | PASS |
| Redirects: 301 works publicly; javascript: URL and code 200 rejected | PASS (`/../../x` path accepted, L6) |
| Domain forwarding with keep_path (302), then removal | PASS |
| Parked domain serves the target site; unpark | PASS. Parking a foreign domain returns 502 (L2) |
| Addon domain or subdomain added by the customer | **FAIL, C1** (UI toast "domain target unavailable") |
| PHP per-domain version (clone on 8.2, primary on 8.3); invalid version rejected | PASS |
| PHP ini (memory_limit, upload_max_filesize, post_max_size) takes effect; out-of-range and unsupported directives rejected | PASS |
| Databases: create, user, grant; MySQL login works; cross-account access denied; export; check | PASS. Duplicate DB returns 502 (L2) |
| phpMyAdmin | **FAIL, H8** |
| File manager: opens, lists the home folder, shows quota (UI) | PASS |
| FTP over TLS: login and upload, file served over HTTP; plain FTP refused; path traversal rejected; limit of 5 enforced | PASS |
| Cron: add (written to crontab), delete | PASS. Invalid schedule and 200 KB command return 502 (L2) |
| SSH key: add and validate; SSH login and SFTP; jail shows only the account's own /home | PASS (process list visible, L7) |
| Web terminal: xterm over WebSocket, `whoami` round-trip (UI) | PASS |
| Git: create repo, clone over SSH, push, push-to-deploy into a subfolder, push log | PASS. Bad deploy target returns 502 (L2) |
| Node.js app: create, start (HTTP and HTTPS 200 "node ok v20.19.6"), stop (503), logs | PASS |
| Python app | **FAIL, H6** |
| All 38 customer pages render with no JS errors or 5xx (headless Chrome) | PASS, except the add-domain flow |

### 7. Admin side
| Test | Result |
|---|---|
| Suspend: every hostname shows "Account Suspended"; panel session revoked; FTP 530; crontab removed | PASS (HTTP status is 200, L10) |
| Suspend: SSH access blocked | **FAIL, H7** |
| Suspend: IMAP still works | info: mail is not suspended. Is that by design? |
| Customer logs in while suspended | PART: gets a cookie and is sent to /app, then every call is 401, with no "suspended" message (L10) |
| Unsuspend: site and customer login back | PASS |
| Per-domain suspend and unsuspend (customer) | PASS |
| Plan limits: usage-limits reported; FTP limit enforced; customer cannot change limits or apply plans (403) | PASS |
| Reseller: plan, reseller, dashboard; creates 2 accounts; suspend and unsuspend its own account; cannot touch foreign accounts; bad action rejected | PASS |
| Reseller cannot create an account on a subdomain of another account's zone | PASS (correct isolation) |
| Reseller account limit gives a clean message | FAIL, M8 (502) |
| Impersonation: token, redeem, whoami shows impersonating; return restores the admin session; token single use; audited | PASS |
| Audit log records create, update and impersonation events with actor, IP and duration | PASS |
| Services list, update status, preflight, history (no update run) | PASS |
| License GET and state | PASS |
| Readiness | PASS: correctly flags webmail DNS and certificate (M5) and the missing notification sender |
| Terminate account (task) | PASS |
| Delete a reseller | not possible (no endpoint); left `uwres` suspended (L8) |
| Delete a reseller plan that is in use | 502 (L2) |

### 8. Error paths
Good validation throughout, with consistent 400/403/422 and `reference` IDs: SQL-ish identifiers, bad redirect paths, foreign domains (WordPress, SSL, DNS, mail), password policy, quota bounds, login failure (401, same message for unknown users). The unmapped-exception class (L2) is the main gap: several input errors surface as `502 internal operation failure`.

---

## Findings

### C1 (critical): Customers cannot add any domain, subdomain or addon domain
- **Reproduce:** Log in as a customer. Domains > Add subdomain `uiblog`, or `POST /api/v1/task-operations/accounts/uwcust/domains {"domain":"clone.uptimeways.com","kind":"subdomain","parent_domain":"uptimeways.com"}`.
- **Observed:** `403 {"detail":"domain target unavailable"}` (refs f365f388765a3319, 4a3022c6020e8139). The UI toast reads "domain target unavailable (reference: …)".
- **Expected:** 202 and the domain is added. The same call as admin works.
- **Root cause:** `daemon/rpc_authority.py:283` passes `allow_new_domain=op in ("domain.add", "mail.create_domain")`, but the SPA (`frontend/src/pages/customer/Domains.jsx:170`) and the router (`api/routers/tasks.py:63`) use the op `taskops.domain.add`. Its not-yet-existing domain then fails `_target_account_id` at `rpc_authority.py:143`.

### C2 (critical): Every authenticated outgoing message hard-bounces as "user unknown"
- **Reproduce:** SMTP AUTH on mail.uptimeways.com:587 as info@, then send to sales@uptimeways.com (or via the hello@ forwarder).
- **Observed:** The message is accepted, then bounced to the sender. From `/var/log/mail.log` 04:32:18, queue id 6FFCB80550:

  ```
  relay=boron-sign … dsn=5.1.1, status=bounced (user unknown. Command output: /dev/null: configuration schema was migrated from v0 to v1 DKIM signing service is unavailable)
  ```

  Running the filter by hand as `opendkim` with `PATH=/usr/bin:/bin` gives `FileNotFoundError: 'opendkim-testmsg'`.
- **Expected:** The message is DKIM-signed and delivered.
- **Root cause:** `daemon/mail_submission.py:184` runs the bare name `opendkim-testmsg`, but the binary is `/usr/sbin/opendkim-testmsg`, and the Postfix `pipe` PATH does not include `/usr/sbin`. Also, `scripts/mail_sign_filter.py:33` returns 67 (EX_NOUSER) for service errors, which turns a transient fault into a permanent "user unknown" bounce. Transient errors should use 75 (EX_TEMPFAIL). The stderr line "configuration schema was migrated from v0 to v1" comes from loading `BORON_CONFIG=/dev/null` and pollutes every bounce.
- **Impact:** No customer can send mail from mail clients or webmail, to local or external recipients.

### H1 (high): The certificate covers the apex only, so `https://www.<domain>` fails
- **Reproduce:** Issue SSL for uptimeways.com (Domains > SSL, or `POST …/domains/uptimeways.com/ssl/issue {}`).
- **Observed:** Certbot runs with `-d uptimeways.com` only, and the SAN is `DNS:uptimeways.com`. The vhost serves `www` over HTTP (a `www` CNAME exists in the zone), but `https://www.uptimeways.com` gives `SEC_E_WRONG_PRINCIPAL` / a name mismatch.
- **Expected:** The apex and www are on the same certificate whenever www resolves to the server.
- **Root cause:** `daemon/ssl.py:261-267` adds `-d www.<domain>` only when a WordPress site's `site_url` starts with `https://www.`.

### H2 (high): One-click staging always fails and leaves orphan files
- **Reproduce:** `POST /api/v1/accounts/uwcust/domains/uptimeways.com/staging {}` on a WordPress site.
- **Observed:** `400 "Database work directory must be private and owned by the backup service"` (refs 7015726ad7abe0ce, 2c0377ec1abbf665). `/home/uwcust/staging.uptimeways.com` (123 MB copy) is left behind, with no domain row.
- **Root cause:** `daemon/staging.py:216` creates the dump with `tempfile.mkstemp()` in `/tmp`. `backup._dump_database` (`daemon/backup.py:518`) passes `output_path.parent` (`/tmp`, mode 1777) as the work directory, and `daemon/snapshot_databases.py:35-37` rejects any directory with group or other bits set. Staging needs a private 0700 work directory (as backups use), and should roll back the copied files on failure.

### H3 (high): Autoresponders never fire, and spam is never moved to Junk
- **Reproduce:** Create an autoresponder on support@ and send to support@. Separately, send a GTUBE message to sales@.
- **Observed:** The vacation script exists (`/var/vmail/uptimeways.com/support/.dovecot.sieve`) but no reply is generated. GTUBE is tagged `X-Spam-Flag: YES`, score 998.5, but stays in INBOX, and no Junk folder exists.
- **Root cause:** Dovecot LMTP has no `sieve` plugin. `doveconf -n` shows `protocol lmtp { lmtp_rcpt_check_quota = yes }` and `/etc/dovecot/conf.d/20-lmtp.conf` has an empty `protocol lmtp {}`. `daemon/spamfilter.py:497` (`_ensure_dovecot_sieve_wiring`) only adds `sieve_before`, and `scripts/upgrade_runtime.py:472` / `install.sh` never set `mail_plugins = $mail_plugins sieve` for LMTP.

### H4 (high): Full restore on an active account does not recreate deleted mailboxes
- **Reproduce:** Take a full backup (job 3). Delete mailbox sales@. Restore job 3.
- **Observed:** The restore completes and the maildir files are written back (4 messages), but the mailbox is not in the list and IMAP login fails with `AUTHENTICATIONFAILED`. Files, DBs and posts are restored.
- **Root cause:** `daemon/backup.py:1208`: the whole account, domain, DB and mailbox recreation block runs only when `account_status in ("terminated","error")`. An active account skips `handlers_mail.create_mailbox` (around line 1268). Snapshot mail restore handles this case correctly.

### H5 (high): A terminated account cannot be restored from its own panel backup
- **Reproduce:** Full backup of uwrc2 (job 8). Terminate uwrc2. `POST /api/v1/accounts/uwrc2/backups/8/restore`.
- **Observed:** The restore job fails with `cannot run restore for an account in status 'terminated'`.
- **Expected:** The account is recreated, which the code at `backup.py:1208-1230` is written to do.
- **Root cause:** The worker guard at `daemon/backup.py:1503-1504` rejects any status other than `active`, which makes the terminated branch dead code. Workaround: download the archive and use Admin > Import Boron with the same username (works).

### H6 (high): Python apps never start and crash-loop forever
- **Reproduce:** Create a Python WSGI app on `py.uptimeways.com` (entry `app:application`) with a valid `app.py`, then start it.
- **Observed:** Gunicorn exits with `socket.fromfd … OSError: [Errno 9] Bad file descriptor`. The unit restarts every 2 s (NRestarts above 24), and the site gives no response. `systemctl show … -p Sockets` is empty.
- **Root cause:** `daemon/pythonapps.py:158` runs `gunicorn --bind fd://3` (uvicorn uses `--fd 3`), but the unit written at `pythonapps.py:174-200` has no `Sockets=`/`Requires=` for its `.socket`. `daemon/appunits.py:96` (`enable_start_app`) starts the service directly, so fd 3 is never passed. There is also no `StartLimitBurst`, so the crash loop is unbounded. Node apps (TCP port) work.

### H7 (high): Suspended accounts keep SSH and SFTP access
- **Reproduce:** Add an SSH key, suspend the account, then `ssh uwcust@server id`.
- **Observed:** Login succeeds (`uid=1016(uwcust)`) while the web, panel, FTP and cron are blocked.
- **Root cause:** `daemon/sysops.py:183-185` (`lock_user`) runs `usermod -L`, which locks the password only, so key authentication still works. It should also expire the account (`usermod --expiredate 1`) or deny it in sshd or PAM, and close the terminal WebSocket.

### H8 (high): phpMyAdmin is not available on a full-profile install
- **Reproduce:** Databases > Open phpMyAdmin.
- **Observed:** `pma-token` returns `pma_url: null`, and the UI says "phpMyAdmin is not set up". There is no `/usr/share/phpmyadmin`, and `pma_hostname` is empty.
- **Root cause:** `daemon/pma.py:267` returns unavailable because `pma_hostname` and the docroot are never provisioned by `install.sh` or the full profile.

### M1 (medium): A global DB mutation lock makes unrelated operations fail with a misleading message
- **Observed:** While another tenant was adding a domain, `account.create` (task 12, ref e353b1a4d438d6e3) and staging failed with "A database backup or management operation is in progress". The account-create task shows only "Operation failed", so the real message is hidden.
- **Root cause:** `daemon/database_operations.py:15` uses a non-blocking server-wide lock (`mutation_lock(blocking=False)`), and `daemon/control_tasks.py` stores no user-safe error text. Suggest waiting with a timeout for background tasks, scoping the lock per account, and surfacing ValidationError text in the task.

### M2 (medium): Email DNS readiness reports "present" from the local zone, not the real DNS
- **Observed:** Before I added any records, `GET …/email/domains/uptimeways.com/dns-readiness` reported MX, SPF and DKIM as `present`, although the authoritative NS (Cloudflare) had none. `managed: true, provider: local`.
- **Expected:** When the domain's NS are not this server, check public DNS, or warn that the local zone is not authoritative.

### M3 (medium): WordPress clone to a freshly created subdomain always fails "destination must be empty"
- **Root cause:** Provisioning puts `welcome.html` in every new docroot, and `daemon/wpmanager.py:90` only ignores `.well-known` and an empty `error_pages`. The installer tolerates the welcome page, but clone does not.

### M4 (medium): Customers cannot download their own panel backups
Only `/api/v1/admin/account-archives/{id}/download` exists, and `Backups.jsx` shows it only to admins. Snapshot downloads work for customers, but only after an admin configures a snapshot destination.

### M5 (medium, environment): The mail and webmail hostname does not resolve, and the mail TLS certificate is self-signed
`webmail.demo.boronpanel.com` (used for MX guidance, the webmail URL and the mail certificate) is NXDOMAIN publicly. IMAP and submission present `CN=boron-default` (self-signed). The webmail button opens a non-resolving host. Admin readiness does flag this correctly. Fix: add the A record in the boronpanel.com zone, then issue the service certificate. I left this alone because it is outside the uptimeways.com zone. Postfix `myhostname` is `henchkin-node-a` (not an FQDN), which will hurt deliverability (HELO / bounce sender `MAILER-DAEMON@henchkin-node-a`).

### M6 (medium): Snapshot DNS restore is impossible for any domain with DKIM
- **Observed:** Run configuration shows `dns_available: false, "Saved DNS record content is not valid for its type"`.
- **Root cause:** The panel-generated DKIM TXT is stored as a single string of more than 255 characters, and `daemon/snapshot_dns.py:186` (`dns.rdata.from_text`) rejects it ("string too long"). Store DKIM TXT values as 255-character chunks, or chunk them before validating.

### M7 (medium): A duplicate mailbox returns 502
`handlers_mail.py:188` raises RuntimeError (ref 88794b279e7b1eac). It should be a 409 "mailbox already exists".

### M8 (medium): The reseller account or disk limit returns 502 instead of a clear message
`daemon/resellers.py:414` raises `ResellerError("reseller account limit reached")`, which is not mapped to 4xx (ref 64e352f58533ad37). The same applies to deleting an in-use reseller plan (`resellers.py:219`, ref c508ce4bc631f6ff).

### Low / info
- **L1:** `GET /api/v1/accounts/<missing>` returns 502 (`handlers_account.py:437` RuntimeError, ref 58cc78e8c389de05). Should be 404.
- **L2:** Further unmapped exceptions return 502 instead of 400/409:
  - parked domain owned by another account (`parked.py:51-53`, b57b785e552415c3)
  - duplicate DB (`handlers_database.py:229`, 89d17c17683820ab)
  - invalid cron schedule (`cron.py:104` CronError, 2f6d4525f410abac)
  - 200 KB cron command (2acbca632144a6bd)
  - git deploy-target traversal (c77f43b4b8c2274e)
  - backup of a foreign DB (`backup.py:303` BackupError, 3f439057edbf434f)
- **L3:** There is no "force HTTPS" option, so http:// keeps serving 200 after SSL is issued.
- **L4:** A clone with an https target URL is not given a certificate automatically (SSO to the clone fails until SSL is issued).
- **L5:** No POP3 (110/995) and no implicit-TLS submission (465). Only 143, 993, 587 and 25. Document this, or offer them.
- **L6:** A redirect path of `/../../x` is accepted (other unsafe paths are rejected).
- **L7:** SSH users can list every host process (`ps aux` shows root, mysql and daemon processes). Consider `hidepid=2` on /proc.
- **L8:** Cleanup gaps:
  - snapshot policies have no DELETE route, and a destination cannot be deleted while a policy references it (left `uw-policy` disabled and `uw-local-snap` in place);
  - there is no endpoint to delete a reseller (left `uwres` suspended);
  - a failed staging run leaves its files behind.
- **L9:** Portable import refuses a different target username ("must match the username stored in this archive"), so you cannot restore onto a new account name.
- **L10:** Suspension UX:
  - the suspended page returns HTTP 200 (503 is better for search engines);
  - a suspended customer's login "succeeds" (303 to /app with a cookie), then every call is 401 with no explanation;
  - an account plan with `cpu_cores 1.0` is stored on the account as `cpu_cores 0.25` (the cgroup shows 1.0), and new accounts start with `inode_enforcement_state: warning`.

## Left in place (for the owner to inspect)

- **Account `uwcust`** (plan `uw-standard`), with the password in `/root/qa/uptimeways/uwcust.pw` (mode 600):
  - WordPress at https://uptimeways.com (admin `uwadmin`, password in `uwcust_wpadmin.pw`);
  - the clone at https://clone.uptimeways.com;
  - subdomains `app` (Node app, stopped) and `py` (Python app deleted);
  - mailboxes info, sales and support (passwords in `mbx_*.pw`);
  - forwarder hello@ to sales@, the support@ autoresponder;
  - FTP `uwcust_uwftp`, Git repo `uwsite`, SSH key `uw-qa`.
- **Orphan staging copy** `/home/uwcust/staging.uptimeways.com` (evidence for H2).
- **Disabled snapshot policy** `uw-policy` and its destination `uw-local-snap` (`/var/backups/uw-snap`), which cannot be deleted (L8).
- **Suspended reseller `uwres`**, its plan `uw-res-plan` (cannot be deleted while in use), and the terminated accounts `uwrc1` and `uwrc2`.
- **Helper scripts** in `/root/qa/uptimeways/` (`lib.py`, `t*.py`).

## Retest checklist (after fixes)
C1 (add a subdomain from the UI), C2 (587 send to local, with a DKIM-Signature header), H1, H2 (staging create, change, sync), H3 (autoresponder and Junk), H4, H5, H6, H7, H8, M2, M3, M6, M7, M8, L1, L2. After those, run a quick regression of SSL, WordPress SSO, backups and restore, and suspension.

## Retest on 3.3.3

- **Build:** `/opt/boron -> /opt/boron-3.3.3`, installed through the panel updater at about 11:00 UTC.
- **Time:** retest 11:08 to 11:25 UTC.
- **Method:** same as round 1. Scripts `r01.py` to `r12.py` in `/root/qa/uptimeways/`. Their output is in `r*.out` in the same folder.
- **C1 in the UI:** checked again with headless Chrome.
- **Line numbers** in this section refer to `/opt/boron-3.3.3`.

### Results

| Item | Result | Evidence |
|---|---|---|
| C1 customer adds a domain or subdomain | **Fixed** | `POST /api/v1/task-operations/accounts/uwcust/domains {"domain":"uwc1.uptimeways.com","kind":"subdomain","parent_domain":"uptimeways.com"}` returns 202, task 35 completes and the site answers 200. In the real UI, Subdomains > Add subdomain `uiblog333` shows the toast "Subdomain added" and the new row. |
| C2 SMTP-AUTH send | **Fixed** | 587 info@ to sales@, and to the hello@ forwarder: both delivered (`relay=boron-sign … status=sent (delivered via boron-sign service)`) with `DKIM-Signature: d=uptimeways.com`. No bounces since 3.3.3. |
| H1 www on the certificate | **Still open** (partly fixed) | See [H1 retest](#h1-retest-the-reissued-certificate-is-not-loaded) below. The SAN is now `uptimeways.com, www.uptimeways.com`, but the web server kept serving the old apex-only certificate after the reissue. |
| H2 staging create, change, sync, delete | **Fixed** | Create returns 200 in 13 s (`staging.uptimeways.com`, db `uwcust_stg`, siteurl rewritten, HTTPS 200). A staging-only change (option and file) is not pushed to live. Sync (live to staging) wipes the staging change and brings in a new live file. Delete drops `uwcust_stg`. Over a leftover folder, create returns a clear 409 ("…already contains files. Move or delete them…") instead of failing halfway. Minor: delete leaves an empty `staging.uptimeways.com/error_pages` folder. |
| H3 autoresponder and Junk | **Fixed** | LMTP now has `mail_plugins = " quota sieve"`. Dovecot logs `vacation action: sent vacation response to <info@uptimeways.com>`, and info@ received "Out of office" with `Auto-Submitted: auto-replied (vacation)`. GTUBE scored 999 and was filed by `fileinto action: stored mail into mailbox 'Junk'`, and a Junk folder now exists. (Note for testers: SpamAssassin 4.0 uses the new GTUBE string `…NSBN3*2IDNEN*GTUBE…`. The old string scores 0.) |
| H4 full restore recreates a deleted mailbox | **Fixed** | Backup 9, delete sales@, restore: the mailbox is back in the list. The restore finishes "completed with warnings: Recreated mailboxes have new random passwords (backups never store passwords); set new ones for: sales@uptimeways.com". After `PATCH /api/v1/accounts/uwcust/email/sales/password`, IMAP works with all 7 INBOX messages and 1 Junk message. This matches the documented behaviour. |
| H5 restore a terminated account | **Fixed** | `POST /api/v1/accounts/uwrc2/backups/8/restore` on terminated uwrc2: the account is active again, with its Linux user, `fresh.txt` served on its domain, database `uwrc2_fdb`, and mailbox boss@. |
| H6 Python app | **Fixed** | Created `uwpy2` on py.uptimeways.com and started it. `boron-python-uwcust-1.socket` and `.service` are active, gunicorn listens on `unix:/run/boron-apps/python-1.sock`, `NRestarts=0`, `StartLimitBurst=5`, and http://py.uptimeways.com/ returns "python ok" with 200. |
| H7 suspension blocks SSH | **Fixed** | While suspended, SSH key login and SFTP get "Connection closed", the account expires on 1970-01-02, IMAP and FTP are refused, the site and wp-login return 503, and the panel login returns 403 "This hosting account is suspended…". After unsuspend, all of these work again. |
| H8 phpMyAdmin | **Still open** (needs admin configuration) | See [H8 retest](#h8-retest-installed-but-not-published) below. |
| M2 email DNS readiness | **Fixed** | Every record now reports `"source": "public"`. Minor: the MX row says `present` while `expected` is `10 webmail.demo.boronpanel.com.` and `actual` is `10 mail.uptimeways.com.`. Both names reach this server, but the UI could explain why it accepts a different host. |
| M3 WordPress clone to a fresh subdomain | **Fixed** | Clone to `uwc1.uptimeways.com` (its docroot held `welcome.html`) completes in 3 s, "Clone ready". The installs list shows WordPress 7.1.4. |
| M6 snapshot DNS restore with DKIM | **Fixed** | New snapshot run 3: `dns_available: true`, zone uptimeways.com available with 16 records. |
| M7 duplicate mailbox | **Fixed** | 409 `mailbox 'info@uptimeways.com' already exists`. |
| M8 reseller limit and in-use plan | **Fixed** | Plan delete returns 409 "plan is assigned to 1 reseller(s)…". Going over the account limit returns 409 "Reseller account limit reached: your plan allows 2 account(s) and all are in use…". |
| L1 missing account | **Fixed** | 404 `account 'uwnosuchacct' not found`. |
| L2 unmapped 502s | **Fixed** | Parking a domain owned by another active account returns 409 "already belongs to another account" (adding it as an addon does the same). Duplicate DB returns 409. Bad cron schedule returns 400. A 200 KB cron command returns 400 "exceeds 1000 characters". Git `../../etc` returns 400. Foreign-DB backup returns 400 "choose a database owned by this account". |

**Other round-1 low items that changed:**
- **L3:** a force-HTTPS switch now exists. When enabled, http:// gives a 301 to https.
- **L4:** a clone now gets a valid certificate automatically.
- **L8:** a snapshot policy, a destination and an in-use-free reseller plan can now be deleted. Deleting a reseller hits N1.
- **L10:** the suspended page is now 503, and a suspended customer's login gets a clear 403.

### Regression (all pass)

- **SSL issuance:** uptimeways.com (apex and www), uwwp.uptimeways.com (with www), and the clone's automatic certificate all verify with a public CA.
- **WordPress:**
  - a fresh install on `uwwp.uptimeways.com` completes in 8 s;
  - SSO works into both uwwp and uptimeways.com: the bridge returns 302 to wp-admin, the dashboard renders, REST `users/me` returns 200, and a reused token gets 404.
- **Backups and restore:**
  - a full legacy backup and restore on an active account (H4);
  - a terminated-account restore (H5);
  - snapshot run 3, then a selective file restore of `public_html/uw-canary.txt` ("Selected files restored; unrelated files retained").
- **Suspension:** see H7. The site, SSH, SFTP, IMAP, FTP, panel and wp-login are all blocked, then all restored on unsuspend.

### H1 retest: the reissued certificate is not loaded

- **Request:** `POST /api/v1/accounts/uwcust/domains/uptimeways.com/ssl/issue {}` at 11:09:02.
- **Response:** `200 {"domain":"uptimeways.com","challenge":"http-01","status":"issued","names":["uptimeways.com","www.uptimeways.com"]}`.
- **On disk:** `/etc/letsencrypt/live/uptimeways.com/cert.pem` has `DNS:uptimeways.com, DNS:www.uptimeways.com` (notBefore 10:10). The deploy hook logged `applied new certificate for uptimeways.com (account uwcust, wildcard=False)` at 11:09:15.
- **Served:** `openssl s_client -servername uptimeways.com` still returned the round-1 certificate, `DNS:uptimeways.com` only (notBefore 03:03). `curl https://www.uptimeways.com/` failed verification (`ssl_verify_result=1`) for several minutes.
- **What fixed it:** at 11:20, adding a subdomain made the panel reload OpenLiteSpeed. Only then did www verify (`301 https://uptimeways.com/`, verify 0).
- **Root cause:**
  - `scripts/ssl_deploy_hook.py:106` calls `ols.refresh_vhost`. That re-renders the vhost and hands it to `ConfigWriterMulti`.
  - `daemon/configtx.py:237-239` returns early ("unchanged") when every rendered file matches what is already on disk. The reload in `ols._reload` (`ols.py:1226`) is then skipped.
  - A reissued or renewed certificate keeps the same `/etc/letsencrypt/live/<domain>/` paths, so the config never changes and lshttpd keeps the old certificate in memory.
  - Round 1 did not show this, because the first issue changed `ssl_status` (none to active) and therefore the vhost text.
- **Impact:** the same path runs on every automatic renewal, so renewed certificates are not served until something else reloads the web server. Expired certificates can be served even though the renewal succeeded.
- **Expected:** the deploy hook always reloads OpenLiteSpeed after it deploys a certificate, even when the config text is unchanged. For example, add a `force_reload` option to the writer, or call `ols.graceful_reload` from the hook.

### H8 retest: installed but not published

- **Requests:**
  - `GET /api/v1/admin/phpmyadmin` returns `200 {"installed": true, "published": false, "hostname": null, "target_hostname": null, "provisioning": "installed", "last_error": null, "last_source": "upgrade", …}`.
  - The customer call `POST /api/v1/accounts/uwcust/databases/uwcust_wp/pma-token` returns `200 {"token": null, "db_name": "uwcust_wp", "pma_url": null, "available": false, "expires_in_seconds": 0}`.
- **What changed:** the 3.3.3 upgrade now installs phpMyAdmin, so the round-1 "not installed" part is fixed.
- **Why it is still open:** it is published only after an admin sets a phpMyAdmin hostname in Server Setup (`api/routers/server_setup.py:23`, `daemon/pma.py:634-653`), and the readiness check reports "installed but not published: no phpMyAdmin hostname is configured". On this server, customers still get "phpMyAdmin is not set up".
- **Not tested:** I did not set the hostname, because it is a global setting (and needs a DNS name outside uptimeways.com). So the full end-to-end flow is untested.
- **Suggestion:** default the hostname to `pma.<panel host>` when the installer or upgrader can, or make the customer message tell the customer to ask the admin.

### N1 (new, medium): Deleting a reseller returns 502 although the delete succeeds

- **Request:** `DELETE /api/v1/admin/resellers/3` (uwres, suspended; its accounts uwrc1, uwrc2 and uwrc4 were all terminated, so the reseller owned no live account).
- **Response:** `502 {"detail": "internal operation failure (reference: 7da6c7a2aff27e87)"}`. The audit log records `reseller.delete … result: failed, detail: CgroupError`.
- **Actual state:**
  - The reseller is gone from `GET /api/v1/admin/resellers`.
  - Its login was disabled and renamed.
  - Its plan then deleted with 200, so the delete itself had committed.
- **Daemon frames:** `resellers.py:414 (delete_reseller) -> resellers.py:27 (_reconcile_account) -> resource_manager.py:258 -> cgroups.py:210 (apply_policy) -> cgroups.py:180 -> cgroups.py:166 -> cgroups.py:157 (_resolve_uid)`.
- **Root cause:**
  - `daemon/resellers.py:393` collects `released_ids` from every reseller link, including links to terminated accounts.
  - After the transaction commits, `resellers.py:413-414` reconciles cgroups for each of them.
  - A terminated account has no Linux user, and its uid may already be reused (uwrc4 got uid 1018, which uwrc2 had before). So `_resolve_uid` raises `CgroupError` ("no resolvable uid" / "uid does not match the Linux identity").
  - That turns a completed delete into a 502 and a "failed" audit entry.
- **Expected:**
  - Only reconcile accounts that are not terminated (the `live` list).
  - Catch reconcile errors after the commit and log them.
  - Return 200 with `released_accounts`.

### Not retested in this round

- **Not in the checklist:** M1 (global DB lock), M4 (customer download of legacy backups), L5–L7 and L9.
- **Environment:** M5 (webmail host DNS and certificate), and the known webmail-certificate ACME preflight failure, which you said is already fixed for the next release.

### Observations

- **Domain delete keeps folders:** deleting a subdomain removes the vhost and DNS but leaves `/home/<user>/<domain>/` on disk (seen for `uwc1`, `uwwp`, `uiblog333` and `uwpark`). This may be intended (cPanel does the same). If so, say it in the confirmation dialog.
- **Customers can park unowned names under the panel's domain:** the park check refuses names owned by an active account. But a customer could park `mail1.qa.boronpanel.com` (its owner account had been removed by the other tester), and the panel created a local DNS zone for it. Consider refusing names under the panel or server hostname's own domain. Not tested further, to avoid touching panel hostnames. I removed the park straight away, and its zone went with it.

### State after the retest

- **Kept:**
  - account `uwcust` with WordPress at https://uptimeways.com (now with a www certificate), the clone at https://clone.uptimeways.com, and the Node app on `app`;
  - the Python app `uwpy2` on https://py.uptimeways.com (running, shows that H6 is fixed);
  - mailboxes info, sales and support (sales@ password reset to the stored value in `mbx_sales.pw`), the forwarder and the autoresponder;
  - FTP, Git and the SSH key.
- **Removed:**
  - temporary subdomains `uwc1`, `uwwp` and `uiblog333`, their databases and folders;
  - the staging site;
  - the round-1 orphan staging folder (H2 evidence, no longer needed);
  - reseller `uwres` and its plan;
  - snapshot policy `uw-policy` and destination `uw-local-snap` (configuration only; the files remain in `/var/backups/uw-snap`);
  - accounts `uwrc2` (re-terminated after the H5 restore) and `uwrc4` (terminated).
- **Cloudflare:** no DNS records added or changed in this round.
