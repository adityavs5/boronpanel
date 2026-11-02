# Boron 3.3.1: real-domain QA report for uptimeways.com

- **Server:** demo.boronpanel.com (80.47.12.101), Boron 3.3.1 full profile, `/opt/boron -> /opt/boron-3.3.1`
- **Date:** 2026-11-02, 03:53 to 05:25 UTC
- **Method:** API driven as the browser does (cookie session with same-origin headers, scripts in `/root/qa/uptimeways/`), public checks from a Windows PC, and a headless Chrome (Playwright) run through 38 customer screens.
- **Line numbers** refer to the deployed code in `/opt/boron-3.3.1`. Local `HEAD` differs by a few lines in some files.
- **Error references** can be found with `journalctl -u borond -u boron-api | grep <ref>`.

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
