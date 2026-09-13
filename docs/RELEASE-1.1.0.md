# BoronPanel 1.1.0

This release adds two selectable panel themes: Evolution with the Icons Grid dashboard, and classic Paper Lantern. Both use Boron branding and support administrator and customer navigation, light/dark mode, mobile layouts, tool search, and persistent preferences.

- New administrator overview and Appearance page with theme previews.
- Theme switching preserves unsaved form input and synchronizes preferences between browser tabs.
- Installer fixes ensure OpenLiteSpeed bootstrap runs with the correct service identity and successful installations return exit status zero.
- Development deployment resolves the checkout location automatically.
- Release version bumps keep npm lockfile metadata synchronized.
- Self-update staging makes application files and virtual environments readable by the API service under the daemon's restrictive umask.
- Source-only build checks are skipped in installed release archives; runtime regression checks remain enabled for self-updates.

Install this release through **System → Updates**. The updater verifies the release archive checksum, backs up the database and panel configuration, prepares the new version, then restarts and health-checks services with rollback support.
