# WordPress Manager

Open **WordPress Manager** from the user dashboard. Administrators have **WordPress Installations**, which lists detected sites across hosting accounts. Suspended sites remain visible, but their management actions are disabled.

## Create a website

1. Choose **Install WordPress**, select a domain, and choose HTTP or HTTPS with or without www.
2. Leave the folder empty for the main website, or enter a folder such as `blog`.
3. Set the website name, administrator username and email. A strong password is generated automatically; you can change it or reveal it.
4. Review the address and choose **Install WordPress**. The panel downloads the current stable WordPress release, creates its database and completes setup.
5. Save the credentials shown when installation finishes. Use **Log in** on the site card to open its WordPress dashboard without entering them again.

The selected hostname must point to this server. HTTPS requires a valid certificate covering that hostname. When an installed WordPress site uses HTTPS with www, issuing its certificate from SSL Management includes both the domain and its www name. Installation refuses to overwrite an existing website. Boron's empty provisioning folders are preserved. Manually uploaded WordPress sites are detected too, at the domain root or one subfolder level.

## Manage an existing website

**Scan for installations** discovers uploaded/imported sites and refreshes their registered URLs. A site card’s **Refresh** action updates that site’s metadata. Scans report configuration or database errors per site.

Choose **Manage website** on a site card:

- **Overview:** update WordPress core, clear its object cache, or enable/disable maintenance mode.
- **Plugins / Themes:** inspect installed extensions, activate a theme, activate/deactivate plugins, and update extensions.
- **Backups:** create a private backup of website files and the database; select a restore point to recover the site. Restore requires confirmation and first creates a safety backup. If recovery fails, the worker attempts to restore the pre-operation files and database.
- **Remove:** remove only the panel record while keeping the live website, or permanently remove its files and account-owned database. Permanent removal requires typing the exact installation address and refuses shared databases or shared database users. A scan can rediscover a site whose panel record was removed.
- **Clone site:** defaults to a `staging` folder on the same domain. Choose another empty folder or another domain if preferred, and select the destination’s HTTP/HTTPS and www format. The copy receives a separate database, fresh authentication salts and updated URLs, including serialized WordPress data. Existing WordPress users are retained, and search engine indexing starts disabled.

Backups are local to the hosting account, outside the public website. They are not an off-server disaster-recovery service. Subfolder WordPress installations are handled independently: a parent-site backup excludes them, and restoring the parent preserves them. Symbolic links and special files are excluded from backups and clones. Cloning does not reconfigure external services used by third-party plugins.

**Recent activity** shows installation and management jobs, including operations still running after a page reload. Overlapping WordPress management commands for the same account are rejected to prevent an update from colliding with a restore or clone.

## Navigation and search

The hamburger navigation drawer has been removed. Use the dashboard's tool grid, **Home**, or the keyboard search (`Ctrl+K` / `Cmd+K`). Both search surfaces share case-insensitive matching, aliases, prefix matching and spelling tolerance. Examples:

- `DNS zone edito` → DNS Management
- `MySQL` → Databases
- `Softaculous`, `WP`, or `plugins` → WordPress Manager

Only tools available to the current role appear. Evolution retains the requested Icons Grid dashboard; Evolution and Paper Lantern also theme interior headings, tables, forms and dialogs, including dark and mobile layouts.

## Implementation and operational notes

WordPress management executes as the hosting Linux account, never as root. Database credentials travel over private standard input rather than process arguments. The one-click login handoff is an authenticated same-origin POST with a nonce-based security policy permitting only the selected site's verified HTTP or HTTPS destination. Its token is hashed in a temporary bridge, expires after 90 seconds, and is claimed atomically for single use. Tokens are not placed in URLs. Expired bridge files are cleaned during subsequent logins.

The installer pins WP-CLI 2.12.0 and verifies its SHA-256 before installation at `/usr/local/bin/wp-cli.phar`. PHP CLI and its MySQL extension are installed as dependencies. The API's graceful shutdown is bounded so long-lived file-browser streams cannot indefinitely stall a panel restart.

Development verification artifacts and protected credentials are kept outside the repository under `/root/boron-setup/`. See `WORDPRESS-UX-RESEARCH.md` for the design references.
