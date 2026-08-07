# Boron customer guide

Sign in at `https://<panel-host>:9443/app` with the customer credentials your
hosting operator supplied. Every page is scoped to your account; an admin must
perform server-wide actions such as suspension, plan changes, and updates.

## Domains and DNS

1. Open **Domains** to review the primary domain and add an addon domain or
   subdomain. The operator must ensure the domain's DNS points to this server.
2. Open a domain to manage its document root, PHP version, redirects, custom
   error pages, maintenance mode, caching, and WordPress installation.
3. Use **DNS** to create or edit records when the zone is managed by Boron.
   If the zone is on Cloudflare or another provider, make the change there or
   ask the operator.
4. Use **SSL** to request/renew a certificate after the DNS or HTTP challenge
   prerequisites are ready. A new install may show a self-signed certificate
   until the operator completes this step.

## Email

Open **Email**, choose a domain, and create mailboxes with strong passwords.
The same page manages mailbox quotas, forwarders, catch-all delivery,
autoresponders, spam filtering, and local/remote/backup-MX routing where the
operator has enabled it. Use the configured IMAP/SMTP hostnames and TLS ports
provided by the operator; never send a mailbox password over plain text.

## Databases and phpMyAdmin

1. Open **Databases → Create database**, choose a suffix, and save the
   one-time database/user/password details.
2. Use the database row's **phpMyAdmin** action to open the database tool in a
   new tab. The operator must have configured a phpMyAdmin hostname first.
3. Do not share database credentials with another customer. A database name
   is prefixed with your account username and is scoped to your account.

## WordPress

1. Open **Domains → <domain> → WordPress**.
2. For a new site, ensure the document root is empty, enter the site title,
   WordPress admin username/email/password, and start the install. Boron
   creates a separate database and runs the job as your account.
3. For another site, provide a relative subdirectory such as `blog`; it must
   be empty and is tracked separately from the root install.
4. Once detected, the management card can update WordPress core/plugins/themes,
   activate/deactivate extensions, reset a WordPress admin password, flush
   cache, toggle maintenance mode, and run a preview-first search/replace.

Keep WordPress, plugins, and themes patched. Take a backup before upgrades or
search/replace operations.

## Files and FTP

- **Files** opens the Boron file manager in a new tab. It is scoped to your
  account home; use it for uploads, edits, permissions, and downloads. Do not
  try to browse a parent path to reach another account.
- **FTP** creates a scoped login and optional path inside your home. Connect to
  port 21 with explicit TLS and passive mode. Passive data connections use
  TCP ports 30000–30100. Each login is chrooted and cannot leave its scope.
- **Disk Usage** and **Logs** help find large files and application errors.
  **Terminal**, Git, SSH keys, cron, and application tools are available only
  when the operator's plan permits them; the account shell remains restricted.

## Backups and restore

Open **Backups** to create a background backup point. Choose a full account
(files, databases, mail, DNS, and configuration) or a single file,
directory, database, or mailbox. Select a destination made available by the
operator and wait for the job to show `completed` before relying on it.

Completed backups can be browsed and restored from the same page. A restore
overwrites current data, so use a disposable item/account for a first test and
confirm the backup timestamp and scope before accepting the restore.

## PHP and applications

Use **PHP** to choose an available PHP version and manage permitted per-domain
settings. The server's hardened defaults remain in force unless the operator
has explicitly allowed an override. **Applications** manages supported Node.js
and Python apps; **Redis** manages the account's isolated Redis service when
the plan includes it. **Cron Jobs** runs scheduled account tasks—keep commands
and output paths inside your account home.

## Security and support

Change your panel password from the account menu and enable two-factor
authentication if offered. Use **Security** for domain IP blocking and review
the account audit/log pages when troubleshooting. If the site shows the
suspended page, FTP stops working, or a resource limit is reached, contact the
operator; customers cannot unsuspend themselves or change server-wide firewall,
DNS-provider, plan, or update settings.

