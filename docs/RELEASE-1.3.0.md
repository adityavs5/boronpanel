# BoronPanel 1.3.0

This release completes the server-management expansion requested after 1.2.1.
It adds administrator and customer workflows while keeping both Evolution and
Paper Lantern interfaces responsive and consistent.

## Hosting workflow

- Customer navigation now follows the hosting workflow: domains, subdomains,
  FTP, SSL, databases and DNS; email; WordPress; backups; application runtimes;
  then advanced, security and diagnostic tools.
- Hosting packages include ready-made starter templates and simpler selectable
  choices.
- The administrator account backup format is portable and versioned, with a
  component manifest, sizes and SHA-256 verification. Completed archives can be
  downloaded and restored on another Boron server.
- The unified account-migration screen accepts Boron, cPanel and DirectAdmin
  archives, validates extraction paths and expansion limits, and reports each
  imported component.

## Security and server administration

- The file-based malware scanner combines ClamAV where available with Boron's
  WordPress-focused script, signature, obfuscation and integrity checks. It can
  quarantine safely within the affected account and does not scan network
  traffic or ports.
- The firewall screen manages ports and full-access IP/CIDR bypass entries while
  protecting essential panel and recovery access.
- OpenLiteSpeed settings are editable from Boron with validation, rollback and
  password reset support. Administrators can issue SSL certificates for hosted
  and panel service domains directly.
- Multiple-IP management discovers server addresses, marks shared or dedicated
  use, assigns dedicated addresses and controls random or fixed allocation for
  new accounts.
- The server resource view adds disk, network, memory, CPU, inode, mount and
  per-account allocation details.

## Resellers and suspension pages

- Administrators can create reseller plans and reseller logins, assign plans,
  and suspend or activate access. Plans cap account count and total allocated
  disk and supply per-account PHP and resource defaults.
- Resellers receive a dedicated panel for creating and managing only their own
  hosting accounts. Stored ownership is enforced in the API and rechecked by
  the privileged daemon before lifecycle actions.
- Four responsive suspension-page designs can be previewed, branded and applied
  from the Templates screen. Custom heading and message text is HTML-escaped;
  the advanced raw HTML editor remains available.

## Validation and upgrade

The repository contains focused checkpoint evidence for batches A through F in
`docs/CHECKPOINT-expansion-*.md`. The 1.3.0 release gate runs the complete Python
and both-theme Playwright suites, rebuilds the frontend, self-verifies the
release archive and checksum, then exercises the installed panel's protected
self-update and preservation checks.

Install this release through **System → Updates**. Boron verifies the release
checksum, backs up the current installation and database, migrates additively,
restarts and health-checks services, and retains rollback evidence.
