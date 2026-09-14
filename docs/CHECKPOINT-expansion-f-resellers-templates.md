# Expansion checkpoint F — resellers and suspension pages

Completed 2026-09-14. This batch is committed for the grouped expansion release
and has not yet been deployed to the live panel.

## Delivered behavior

- A third `reseller` panel identity runs on the administrator listener and lands
  on a dedicated reseller dashboard.
- Administrators can create, edit and delete reseller plans; issue reseller
  logins; assign plans; and activate or suspend reseller access. Generated
  initial credentials are returned once and only password hashes are stored.
- Plans cap active account count and total allocated hard quota. They also hold
  the PHP, disk, CPU, memory, I/O and process defaults used for new accounts.
  Plan edits and assignments are rejected when they cannot contain a reseller's
  existing allocation.
- Each reseller-created hosting account has one explicit owner. Account and
  domain access helpers resolve this ownership from the database, and the daemon
  rechecks ownership before suspend, unsuspend or termination.
- The reseller dashboard shows plan and allocation use, creates customer
  accounts with a one-time credential, and controls owned-account lifecycle.
- Administrators can preview and apply four responsive suspension designs:
  Clean notice, Modern gradient, Classic hosting and Minimal. Heading, message
  and brand color are customizable. Text is HTML-escaped and colors are strict
  six-digit hex values. Existing atomic writes and versioned page backups remain
  in use; advanced raw HTML editing is still available.

## Security and failure boundaries

- A disabled or suspended reseller cannot authenticate or satisfy ownership
  checks.
- Account quotas are checked under a process lock before provisioning, and the
  privileged daemon receives the authenticated reseller username from the API
  rather than accepting an arbitrary owner from the browser.
- Reseller ownership does not grant administrator endpoints. A customer does not
  gain reseller endpoints, and reseller identities are rejected on the customer
  listener.
- Suspension design previews are sandboxed iframes. Generated pages contain no
  scripts and escape administrator-supplied heading and message text.

## Validation

- The focused reseller, template, authentication and listener-role suite passes
  all 65 tests. It covers limits, plan downgrade/reassignment rejection,
  ownership, role boundaries, failed-provision compensation, one-time
  credentials and output sanitization.
- The production frontend build passes.
- All four focused Playwright flows pass: administrator and reseller behavior
  in both Evolution and Paper Lantern at desktop and phone widths.

The final full backend/browser suite and live deployment proof belong to the
grouped release gate rather than this checkpoint.
