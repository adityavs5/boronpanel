# BoronPanel 1.2.1

This patch fixes panel self-updates on servers where Dovecot briefly reports a reload transition after the mailbox recovery guard is activated. Version 1.2.0 required the service to report its steady `running` substate immediately after `doveadm reload`, so a normal transient `reloading` state could stop the update safely before the live-version switch.

The activation check now keeps the initial requirement strict—Dovecot must already be running—then waits for a bounded 20 seconds after reload for the service to return to its steady running state. A failure or non-transitional service state still aborts activation and restores the previous configuration.

The fix passed the mailbox-guard and updater regression suites (81 tests), a real Dovecot guard activation/reload on the development server, and the complete release suite (2,659 tests). The actual 1.1.3-to-1.2.1 panel self-update remains the final deployment gate.

Version 1.2.1 includes all WordPress, backup, theme, usability, PHP, panel-port, terminal and two-factor authentication improvements documented in the 1.2.0 release notes.
