# Boron 1.6.0 — Evo and Paper interface refresh, DirectAdmin server migration

Evo and Paper now follow the supplied hosting-panel references across dashboards,
tables, forms, dialogs, and settings pages. Evo uses blue section headers, centered
icon grids and original multicolor hosting icons. Paper uses horizontal tool links,
lightweight headings and open form sections. Both retain readable dark modes,
full-width dashboard search, responsive layouts and persistent appearance settings.

Database creation is available directly on the database page with the account
prefix shown. SSL management separates installed certificates from domains needing
a certificate. Evo puts resource statistics at the top of the dashboard side column.
Existing hosting actions and confirmation dialogs remain connected to their APIs.

Administrators can connect to a DirectAdmin server using an administrator login
(over verified HTTPS) or root SSH (with a verified host-key fingerprint), select
accounts, and queue direct backup transfers into Boron. Database preflight checks
destination capabilities before account creation. Optional compatibility adjustments
handle explicitly supported collation mappings and database object definers;
unsupported SQL and failed items are reported rather than silently ignored.

WordPress sites sharing an imported database now reuse the same generated database
credentials. Source accounts and backups are retained, and DNS cutover is manual.

Real DirectAdmin source-server acceptance remains a separate operator test; the
new transport is covered with offline fixtures and mocked browser flows. Cross-
version database preflight cannot guarantee every custom application's SQL is
compatible. Review the migration report and verify the sites before DNS cutover.
