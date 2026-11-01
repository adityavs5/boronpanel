# Boron administrator guide

This guide covers the recurring operator tasks in the React panel. Sign in at
`https://<panel-host>:9443/app` with an **admin** account. Customer accounts
cannot see the admin navigation or another account's data.

## Accounts

1. Open **Accounts → Create account**.
2. Enter a Linux username (lowercase letters/digits, 1–16 characters), the
   primary domain, PHP version, quota, and contact email. Select a plan when a
   plan should provide the limits automatically.
3. Save the generated initial password, or supply a strong password, and give
   the customer only the credentials intended for their account.
4. Open the account detail page to add domains, inspect usage, change admin-
   controlled resource limits, enable namespace isolation, review alerts, or
   open the account log.

Account usernames become Linux users, OLS vhost names, FTP scopes, and the
prefix for database names. Do not reuse a system/reserved username.

### Suspend, unsuspend, and terminate

- **Suspend** locks the account login and serves the static suspended page for
  every domain. DNS and mail remain in place. Verify the live site in a fresh
  browser because caches are purged as part of the suspension hooks.
- **Unsuspend** restores the vhost and unlocks the account. Re-test HTTP and
  FTP before notifying the customer.
- **Terminate** is irreversible: it removes the account's vhosts, databases,
  mail/DNS state, certificates, Linux user, and home directory. Take/export a
  backup first and confirm the username carefully.

## Plans and limits

Open **Plans** to create or edit reusable templates. A plan can define CPU,
memory, I/O, process, disk, bandwidth, database, mailbox, subdomain, FTP,
application, and Redis limits. Apply a plan from the account editor or create
an account with a plan selected. Check the resulting limits on the account
detail page; changing a plan does not silently terminate existing resources.

## Monitoring and operations

- **Server Health**: inspect service status, resource health, and monitoring
  history. Configure the monitoring email/cooldown in the admin settings.
- **Services**: inspect or restart supported services. Treat repeated restart
  failures as an incident and read `/var/log/boron` plus the service journal.
- **Bandwidth** and **Site Statistics**: review account/domain rankings,
  pageviews, bandwidth, and (when GeoLite2 is configured) top countries.
- **Audit Log**, **Account Log**, and **Error Log**: use these to correlate an
  operator action, a provisioning event, and a 5xx response before changing
  configuration.
- **Mail Queue**, **DB Monitor**, **Slow Queries**, **Fail2ban**, and **WAF**:
  investigate delivery backlogs, database pressure, hostile addresses, and
  blocked requests without editing generated files by hand.
- **Backups**: create a destination (local or rclone-backed), set retention,
  and assign an account/server schedule. The root hourly job starts due
  backups; customers can trigger their own scoped backup jobs.

## License

Boron Panel needs a license from [boronpanel.com](https://boronpanel.com). Add
the key during installation (`bash install.sh --license BRN-XXXX-XXXX-XXXX-XXXX`)
or later on **License**, which also shows the tier, account limit, bound IP and
expiry, and can refresh the license on demand. The panel confirms the license
twice a day. If it cannot reach the license server, it keeps working for 14 days
on its last signed license and shows a banner after three days without a
successful check.

Without a valid license (missing, expired, suspended or bound to another IP)
the admin area shows only **License**. Customer and reseller logins and all
hosted websites, mail, DNS and databases keep working. Creating accounts beyond
the tier's account limit is refused. If the server's public IP changes, ask
support to rebind the license.

## Updates and rollback

Open **Updates** and use **Check now** to ask the license server
(`license.boronpanel.com`) for the newest release of the selected channel
(Stable or Beta). Updates need a valid license: without one, the page asks you
to add a key on **License** instead of offering an update. Downloads come from
`download.boronpanel.com` through short-lived links issued to this server only.
Applying an update requires an explicit confirmation and a browser
session; if admin TOTP is enabled, enter a fresh TOTP code. Boron downloads a
SHA256-verified release, backs up the database and `/etc/boron`, runs its
pre-flight, swaps the versioned `/opt/boron` tree, and health-checks the two
panel services. A failed health check automatically swaps back. Use **Rollback**
from the same page during the configured rollback window. Hosted sites and
OpenLiteSpeed are not restarted by the panel update.

The update servers are `update_server_url = "https://license.boronpanel.com"`
and `update_download_prefix = "https://download.boronpanel.com/protected/"` in
`/etc/boron/boron.toml`; leave the defaults unless support asks otherwise. An
old `update_github_repo` line is ignored and can be removed. Never replace
production code with an unverified checkout or disable the checksum,
signature or TOTP gates.

## IP blocking and allowlists

- **IP Bans** is the admin-wide permanent ban list. Add an address or CIDR for
  a hostile client and record the reason; remove it only after the incident is
  understood.
- **Firewall** manages host-level UFW rules. Every panel change remains pending
  for 120 seconds and is reverted unless a fresh reachable session confirms it.
  If the browser, API, or daemon is unavailable, run
  `sudo /usr/local/sbin/boron-firewall-recover` from the provider console to
  restore the most recent unconfirmed change. If Boron-managed bans or
  Cloudflare-only origin filtering must also be removed while keeping UFW
  enabled, run `sudo /usr/local/sbin/boron-firewall-recover --disable-boron-blocks`.
  Operator-created port rules remain untouched. Keep
  SSH, panel, web,
  mail, FTP 21, and passive FTP 30000–30100 open as appropriate. The installer
  already opens these and preserves SSH before enabling UFW.
- **IP Whitelist** is for trusted administrative sources and controlled
  exceptions. Do not whitelist broad ranges casually.
- A customer can use a domain's **Security** tab for a domain-specific IP
  blocking rule. This does not replace an admin host ban.
- When Cloudflare proxying is enabled, keep Cloudflare edge ranges refreshed
  and use the Cloudflare page to manage zones/tokens; do not ban the edge IPs
  that carry legitimate customer traffic.

## Cloudflare and GeoLite2

PowerDNS is the default and needs no third-party account. To use Cloudflare,
store a scoped `CLOUDFLARE_API_TOKEN` in `/etc/boron/secrets.env`, set the
Cloudflare account/provider in **Cloudflare**, and enable it per zone only
after registrar nameservers and the zone status are correct. The installer
does not ask for this token so it cannot accidentally force a third-party
credential into a fresh install.

For top-country statistics, enter a MaxMind license key during install or add
it later from the site-statistics configuration. If omitted, all other site
statistics continue to work.

## Safe operating habits

- Test account creation, suspension, restore, and update flows on a disposable
  account before applying them to a paying customer.
- Keep `/etc/boron/secrets.env`, database backups, and release signatures
  root-readable only. Do not paste secrets into tickets or shell history.
- After any infrastructure change, check `systemctl is-active`, the health
  page, and the audit log, then make a small real request through the panel.
