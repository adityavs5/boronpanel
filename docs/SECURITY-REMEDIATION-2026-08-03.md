# Security remediation status

The source-level remediation pass for the 2026-08-02 review is complete for the controls that can be enforced by this repository. The changes are intentionally fail-closed where a secure external prerequisite cannot be verified.

Implemented:

- Terminal, FTP directory creation, account temp directories, file-auth files, and archive writes now use no-follow descriptor-safe primitives; a regression test covers a pre-planted `.ssh` symlink.
- Audit sanitization recursively redacts nested dictionaries/lists. Rclone credentials are written atomically to the root-only config instead of being placed in command-line arguments.
- Root log files and directories are restricted; daemon/API logging applies restrictive umasks and modes.
- Login, 2FA, and password-change form posts reject cross-origin Origin/Referer values.
- cPanel URL imports require HTTP(S), reject private/link-local/loopback/reserved destinations, pin the checked address, reject redirects, and enforce size limits.
- API bearer tokens expire after 90 days. TOTP seeds are encrypted at rest with the daemon secret key, with backward-compatible reads for legacy plaintext rows.
- The provisioning socket checks Linux peer credentials and accepts only the dedicated API UID. Service units add restrictive umasks and safe systemd controls.
- Pure-FTPd installer configuration requires TLS before authentication. A trusted certificate still must be installed by the operator; the bootstrap certificate is encryption-only and not a public trust solution.
- WP-CLI and imapsync no longer download and execute mutable remote files automatically; missing tools fail closed and must be installed from pinned, verified packages/artifacts.
- Deployment log setup no longer makes logs group/world accessible to hosted accounts, and the deploy script avoids unsafe word splitting.
- Frontend dependency metadata is clean under the offline npm audit available in this environment; the production build was rerun.

Remaining operational actions (not safely solvable by a source edit):

1. Deploy the reviewed Boron commit/artifact to the host currently running the legacy Boron units, after validating the migration and taking a fresh off-host backup.
2. Install a publicly trusted certificate for the panel and FTP service; replace the bootstrap/self-signed certificate and restrict administrative access to VPN/allowlisted addresses.
3. Require administrator TOTP in the live control panel and rotate any credentials that may have appeared in historical logs, audit rows, or old process listings.
4. Install WP-CLI/imapsync from pinned, independently verified releases before enabling those features.
5. Rotate/expire old logs and apply the restrictive permissions to existing files, not only newly created files. Disable plaintext FTP on the running service and verify `AUTH TLS`/mandatory TLS externally.
6. Configure a signed update verification key and enforce signature verification in the release pipeline before enabling unattended updates. The current checksum remains an integrity check, not publisher authentication.

Verification performed after changes:

- Python modules compile successfully with `python -m compileall`.
- Safe-I/O tests pass (`6 passed`).
- Frontend `npm audit --offline` reports zero vulnerabilities with the installed lockfile and the Vite production build was started successfully.
- Existing long-running integration tests are environment-sensitive and were not used to claim complete remediation; remaining failures/limitations must be resolved in the deployment environment before production rollout.

“Completely secure” cannot be established from source changes alone: certificate trust, live service identity, historical secret rotation, firewall state, package provenance, and deployment drift all require host-level verification after rollout.
