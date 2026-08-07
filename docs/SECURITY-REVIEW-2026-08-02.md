# Boron / Boron Security Review

**Review date:** 2026-08-02  
**Scope:** Boron source tree at `/root/cpanel-clone`, its dependency locks, installer and service definitions, plus non-destructive black-box and host-level checks of the currently deployed Boron panel.  
**Status:** Review only. No remediation was applied to source or production.

## Executive summary

The control plane has a generally sound HTTP authorization structure, good browser security headers, careful password hashing, session revocation, and several mature protections around archives, webhooks, and tenant isolation. However, the most important trust boundary—unprivileged hosting accounts invoking filesystem work through a root daemon—contains a confirmed symlink-following flaw that can turn panel access into host root compromise. The deployed host also exposes root-generated logs to hosting users, supports cleartext FTP authentication, and can copy credentials into audit records, logs, and process command lines.

The live host is running the older Boron deployment rather than the reviewed Boron checkout. The critical terminal flaw and principal logging/secret-handling defects were verified to exist in that deployed code too. No destructive exploit was attempted.

### Risk picture

```text
Internet
   |
   +-- HTTPS :9443 --> API service (sessions, tokens, control DB)
   |                     |
   |                     +-- group-writable Unix socket
   |                              |
   |                              v
   |                       root provisioning daemon
   |                              |
   |                    OS, users, files, services
   |
   +-- FTP :21 --------> passwords accepted without TLS

Hosted tenant processes ----> account-owned home paths (symlink/race input)
                         `--> world-readable root service logs
```

| Severity | Count | Immediate theme |
|---|---:|---|
| Critical | 1 | Deterministic root privilege escalation |
| High | 6 | Secret exposure, plaintext FTP, trust-boundary and supply-chain weaknesses |
| Medium | 6 | CSRF, SSRF, stale dependencies, credential lifecycle, deployment drift, availability |
| Low / informational | 3 | Build-only dependency, fingerprinting, maintainability warnings |

## Findings

### SR-01 — Critical — Root daemon follows tenant-controlled symlinks

`terminal.open_session` constructs `/home/<user>/.ssh`, calls `os.makedirs(..., exist_ok=True)`, then performs `chmod` and `chown` as root. Those operations follow an existing symlink. A tenant can pre-create `.ssh -> /etc` (or another sensitive directory), invoke the authenticated terminal endpoint, and cause the target to become tenant-owned and mode `0700`. This is a deterministic local privilege-escalation primitive and can lead to full host compromise.

An isolated proof reproduced the ownership/mode operation against a symlink target. The live Boron copy contains the same sequence. We did not exercise it on the live host.

Related check-then-act symlink races exist in FTP directory creation, `.htpasswd` writes, application extraction, and home `tmp` setup. Treat this as a systemic CWE-59/CWE-367 class rather than a one-line patch.

**Remediation:** use directory file descriptors and no-follow operations for every root write beneath tenant-controlled paths (`openat2` with `RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS`, or component-by-component `openat`/`O_NOFOLLOW`); validate ownership and type at the point of use; never `chown` or `chmod` an unresolved pathname. Extend the existing `safeio` approach to terminal, FTP, file-auth, installers, staging, and migration paths. Add adversarial symlink and rename-race tests.

### SR-02 — High — Hosting users can read privileged cross-tenant logs

The installed log directory is mode `2775`, and root-created log files inherit the default umask and become `0644`. A real hosted account was able to read `daemon.log`, `account-events.log`, and `modsecurity-audit.log`. These can reveal other tenants' activity, paths, request metadata, operational events, and—because of SR-03—credentials.

The Boron installer deliberately applies the same directory mode, so this is not only legacy drift.

**Remediation:** make the directory `0750` (or stricter), files `0640`/`0600`, set `UMask=0027` in both units, explicitly configure log file modes, rotate old logs with matching permissions, and audit existing files before changing access.

### SR-03 — High — Credentials escape into audit rows, logs, and process listings

Audit sanitization examines only the top-level key. Nested `env_vars` and `rclone_config` dictionaries therefore retain secrets in plaintext audit payloads, bypassing the encrypted application-setting storage. `rclone config create` additionally puts provider keys directly in argv and does not mark the command for log redaction. Other flows pass PrestaShop credentials, a GeoIP license key, and WordPress passwords via argv. On the reviewed host, `/proc` uses the ordinary visibility model rather than `hidepid=2`.

**Remediation:** recursively redact by key and schema before any audit serialization; use allowlisted audit fields rather than generic parameter capture. Pass secrets through protected files, stdin, or file descriptors—not argv or URLs. Treat existing logs/audit rows as potentially credential-bearing, rotate affected credentials, and securely expire historical copies. Add process-isolation defense such as `hidepid=2` where compatible.

### SR-04 — High — FTP credentials are transmitted in cleartext

Pure-FTPd is publicly exposed on port 21 and responds to `AUTH TLS` with `500 This security scheme is not implemented`, while still accepting the username/password login sequence. The installer configures PureDB, chrooting, and passive ports but not TLS.

**Remediation:** preferably replace FTP with SFTP. Otherwise deploy a trusted certificate, require TLS for authentication and data channels, refuse plaintext login, verify passive-mode firewall/NAT behavior, and document client migration.

### SR-05 — High — Public administrator access relies on an untrusted certificate and optional MFA

The public panel uses a self-signed, hostname-mismatched certificate with a ten-year lifetime; the administrator account tested has TOTP disabled. TLS 1.0/1.1 are correctly rejected and TLS 1.2/1.3 negotiate strong ciphers, but users cannot authenticate the server without an out-of-band trust ceremony. This creates a practical credential-phishing/MITM risk for the most privileged account.

**Remediation:** install an automatically renewed certificate valid for the panel hostname, redirect operators to that hostname, require MFA for administrators, and provide recovery-code and break-glass procedures. Restrict :9443 by VPN or administrative IP allowlist where possible.

### SR-06 — High — Root update and tool bootstrap trust mutable remote artifacts

The updater downloads a release archive and its checksum from the same GitHub release. A checksum proves transfer integrity but not publisher authenticity if the release account or repository is compromised. Update extraction/migrations then execute with root authority. WP-CLI and imapsync are fetched from mutable remote locations and executed without a pinned digest or mandatory signature; Composer has a similar fallback path.

**Remediation:** require a signature from an offline/restricted release key (or Sigstore identity with transparency verification), pin immutable artifact digests, enforce rollback/version policy, and fail closed. Package third-party executables from pinned versions with verified hashes or distribution packages. Keep verification metadata outside the release authority it validates.

### SR-07 — High — API compromise crosses directly into the root daemon

The root daemon treats access to its group-writable Unix socket as the principal trust decision; it does not independently enforce a caller capability matrix for the API process. The API holds session/token material and can invoke a broad operation table. The active service hardening scores were `8.7 EXPOSED` for the API and `9.6 UNSAFE` for the provisioning daemon under `systemd-analyze security`.

This is a blast-radius/design finding, not a standalone authentication bypass: compromise of the web API service or its socket-group credentials effectively becomes access to root operations.

**Remediation:** split read-only, tenant, and administrator operations across narrower authenticated channels; bind authorization context cryptographically or enforce it inside the daemon; allowlist operations per peer/service; reduce filesystem and kernel access with systemd sandboxing that is compatible with each operation. Consider small purpose-specific privileged helpers instead of one broad root RPC service.

### SR-08 — Medium — Login CSRF permits forced-account sessions

`POST /login` accepts a cross-origin form submission without a CSRF token or Origin/Referer validation and sets a valid session cookie. `SameSite=Lax` does not prevent a cookie from being set in the response to a top-level cross-site form POST. An attacker can force a victim's browser into an attacker-controlled panel account, creating session confusion and potentially capturing actions/data entered under the wrong identity.

**Remediation:** require a login CSRF nonce bound to a pre-authentication cookie and validate Origin/Referer for browser form endpoints. Clearly display the active identity after login. The test session was revoked.

### SR-09 — Medium — Administrator cPanel import performs unrestricted server-side fetches

The URL import accepts an arbitrary `source_ref`, follows redirects, and performs an HTTP request from the root daemon without scheme or destination-IP restrictions. A compromised administrator session/token can reach loopback or private services and use response/status behavior as an SSRF oracle. Although administrator-only access lowers the incremental privilege, cloud metadata and isolated local services remain valuable targets.

**Remediation:** permit only HTTPS, resolve and reject loopback/private/link-local/reserved addresses, pin the validated address through the connection, repeat validation on every redirect, limit response size/time, and consider an explicit import-host allowlist.

### SR-10 — Medium — Front-end lockfile contains known vulnerable packages

`npm audit` reported seven advisories (two high, five moderate) involving `monaco-editor`/DOMPurify, React Router, PostCSS, Vite, and esbuild. The React Router open-redirect/XSS and DOMPurify bypass family are the most relevant to browser runtime; several Vite/esbuild/PostCSS issues primarily affect developers or build infrastructure rather than the production static bundle.

**Remediation:** update the lockfile to patched releases, rebuild, run the UI tests, and re-run `npm audit`. Do not expose the Vite development server to untrusted networks.

### SR-11 — Medium — Long-lived authentication secrets lack compartmentalization

TOTP seeds are readable plaintext to the API service in the control database, so an API compromise defeats the second factor. Administrator API tokens have no intrinsic expiry and persist until manual revocation.

**Remediation:** encrypt TOTP seeds with a key held outside the database/API filesystem boundary (ideally a KMS/HSM service), restrict decrypt operations, add token expiry and last-used metadata, offer scoped tokens, and alert on dormant or unusually used credentials.

### SR-12 — Medium — Reviewed source and production deployment have diverged

The host is running `/opt/boron` and `boron-*` units, while the workspace is Boron. Live OpenAPI exposed 243 paths versus 248 in the checkout, and newer feature areas are absent. The critical and principal confidentiality findings were cross-checked in legacy source, but a HEAD-only assurance process cannot establish production security.

**Remediation:** inventory the deployed commit/artifact digest, complete or formally abandon the migration, reproduce deployments from signed artifacts, and make version/commit identity visible in health and operations telemetry.

### SR-13 — Medium — Username lockout can be used for repeated denial of service

Repeated failures against a known administrator username trigger a 15-minute account lock. IP throttling slows an attacker but still allows recurring lockouts from one or distributed sources.

**Remediation:** prefer progressive IP/device/risk throttling over a hard account lock, avoid confirming account state, notify administrators of attacks, and provide a secure recovery path.

### SR-14 — Low / informational — Dependency and maintainability observations

`pip-audit` found a setuptools Unicode-normalization issue fixed in 83.0.0; it affects source-distribution handling on macOS and is not a material Linux production runtime path here. ShellCheck reported one word-splitting warning in `deploy.sh`. Uvicorn exposes a server header, and OpenAPI generation emits duplicate operation-ID warnings. These are worthwhile hygiene fixes but were not demonstrated as exploitable production vulnerabilities.

## Positive controls observed

- API route review found authentication and role/account checks broadly consistent; expected public routes were limited to login, branding/version assets, health, and the SPA.
- Password handling rejects bcrypt's ambiguous overlength inputs; password changes revoke existing sessions.
- CORS is not permissive, and responses include HSTS, CSP, frame denial, MIME sniffing protection, and a referrer policy.
- TLS 1.0 and 1.1 are disabled.
- Webhook destinations receive substantially stronger SSRF validation than cPanel import.
- Archive extraction includes traversal and size defenses; SQL scanner findings reviewed were parameterized or otherwise non-injectable.
- The control database directory/file permissions prevent direct reads by hosted accounts.

## Verification performed

- Manual review of authentication/authorization routes, the daemon RPC operation surface, filesystem and command execution sinks, update/install paths, cryptography, session handling, and network fetchers.
- Live, non-destructive checks of TLS, HTTP headers, CORS, login behavior, service exposure, FTP/IMAP/submission capabilities, systemd hardening, filesystem permissions, and tenant-readable logs.
- Bandit: 43 raw findings (3 high, 17 medium, 23 low), triaged manually; the most important confirmed issue is represented by SR-01, while numerous subprocess/template/XML results were contextual or false positives.
- npm audit: 7 advisories (2 high, 5 moderate), represented by SR-10.
- pip-audit: one setuptools advisory, represented by SR-14.
- Front-end production build completed successfully.
- The full Python suite was started and reached 7% without a reported failure before the execution session was interrupted; no complete-suite result is claimed. This is a review limitation, not evidence against the manually confirmed findings.

## Recommended remediation order

1. Disable or gate web terminal creation, then eliminate all root pathname symlink/race primitives (SR-01).
2. Restrict log permissions, recursively scrub audits, remove secrets from argv, rotate exposed credentials, and review historical logs (SR-02/SR-03).
3. Disable plaintext FTP immediately; deploy SFTP or mandatory FTPS (SR-04).
4. Put the admin panel behind a trusted certificate and require administrator MFA (SR-05).
5. Harden and compartmentalize the API-to-root-daemon boundary (SR-07).
6. Establish signed, reproducible updates and pinned third-party tools (SR-06).
7. Address login CSRF, import SSRF, dependencies, token/TOTP lifecycle, and deployment drift.

## Backup and recovery point

Before review, a complete archive including Git metadata, virtual environment, and `node_modules` was created at:

`/tmp/cpanel-clone-pre-security-audit-20260802T170804Z.tar.gz`

- Size: 118,812,116 bytes
- SHA-256: `821fba32f0a35ce9f851cc12b3de3ef191038f66ae250124a842a42ac17dc5ea`
- Verification: gzip integrity test and archive listing both succeeded.

Because `/tmp` is not durable backup storage, copy this archive and checksum to protected off-host storage before beginning remediation.
