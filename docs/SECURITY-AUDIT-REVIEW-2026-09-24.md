# Security review continuation — 2026-09-24

This is evidence for work in progress, not a completed audit.

## Domain ownership after removal/reassignment — BSA-2026-046

Website-domain removal deliberately retains mail storage. Reclaiming the name
through an addon, parked domain, primary-domain change or new account could
therefore bind the current website owner to another account's mail resources.
The root policy previously checked only the website-domain owner. Parked and
primary-domain creation also lacked the addon path's service-hostname and
DNS-zone reservation checks.

All four creation paths now share the mail/DNS/service-name reservation guard.
They serialize with mail mutations. Existing mixed website/mail ownership is
rejected by root account/domain authorization. Lazy mail provisioning refuses
to adopt an external mail domain with missing ownership metadata; that state
requires administrator recovery. Direct mail creation also checks website
ownership. Same-owner reclamation remains supported without deleting mail.

Validation: 85 domain/parked/identity/root-policy tests passed locally; the one
Unix-UID test unavailable in the sandbox passed on the disposable Ubuntu VM.
The VM also passed all 21 new ownership cases. No complete audit/release claim
follows from these checks.

## Git deployment filesystem boundary — BSA-2026-047

The root daemon wrote deployment hooks through mutable repository directories,
read push logs through ordinary root pathname opens, and deleted repositories
through a path with tenant-controlled ancestors. Symlinks could redirect these
operations outside the tenant home. The fix anchors hook writes and repository
deletion to directory descriptors, validates hook syntax over stdin, and reads
only a bounded regular-file log tail without following symlinks at any level.
Failed hook installation no longer commits the requested deployment target.

Validation: all 24 Git/safe-I/O tests passed, including malicious ancestor
symlinks, log symlinks/FIFOs, bounded tails and protected deletion canaries.
Real tenant/systemd integration verification is tracked in the protected VM
evidence directory.

## Application service log privilege boundary — BSA-2026-048

A disposable VM proof confirmed that systemd's `StandardOutput=append:` opened
a tenant-controlled symlink as root and appended to a root-only canary even
with `User=auditweb`. Node, Python and Redis generated units used that pattern.

Generated units now execute a root-owned Python launcher under the service UID.
The launcher refuses root, opens a regular tenant-owned log without following
the final symlink, then executes the application with redirected descriptors.
Application output still goes to the customer's existing log location. Unit
startup errors go to the system journal. Daemon log reads use directory
descriptors, reject symlinks/FIFOs and cap bytes read. Log provisioning checks
every ancestor and rejects hard-linked or special files. Bootstrap renders the
safe unit before checking tenant-controlled log directories, so a malicious
directory cannot prevent replacement of the vulnerable unit definition.

Validation: Node/Python/safe-I/O tests passed (51); Redis's 10 initial failures
were caused by its fixture writing real `/etc/systemd/system`, now corrected
to a temporary unit directory. All 15 Redis tests then passed. Six new log
security tests passed locally and on the disposable VM. Real service checks
and deployment state are recorded separately in the protected evidence ledger.

## Shared filesystem and ACL boundary

The shared safe-I/O directory opener previously rejected symlinks only at the
final directory component. It now walks every component using directory
descriptors and no-follow opens, and rejects parent traversal. File reads use
nonblocking opens so tenant FIFOs cannot hold a root worker indefinitely.
Custom-page and domain ACL changes execute as the owning Unix account; recursive
ACL changes also use physical traversal. Ancestor replacement cannot confer
root authority on these permission changes.

Validation: the focused shared-I/O, custom-page, SSH-key, file-auth, Git and log
suite passed 70 tests. The Unix-UID docroot/ACL integration test passed on the
disposable VM. These changes are hotfixed on the primary development host,
with matching hashes, successful daemon import and a real health RPC.

## Application configuration and terminal trust

Environment files now use atomic private-file replacement and explicit systemd
value quoting. Invalid variable names and control characters are rejected
before replacing an existing file. Node entrypoints reject unit-file syntax
characters. This hardens configuration integrity; no claim is made that an
environment-file exposure was reproduced on this server.

The browser terminal pins the loopback SSH connection to the server's locally
installed public host keys and refuses non-loopback destinations. The VM
verified a successful pinned SSH connection, environment-value round trips
through systemd, and private environment-file permissions. Regression checks
cover missing/invalid keys, remote destinations and unsafe configuration.

## Queued RPC authorization

Credential validity is checked before queueing and again inside the worker.
Resource authorization now runs in the worker immediately before the handler,
so revocation and reassignment during queue wait deny the operation. Update
second factors are consumed once. This closes queue staleness; it does not
claim to make every handler's later resource access transactionally atomic.

Validation: 71 tests passed across root RPC authority/error handling, terminal,
safe-I/O, custom pages and application log/configuration helpers, including
revocation and username reassignment before worker execution.

## Suspended-account credentials

Customer session and bearer-token resolution now requires an active account
at both HTTP and root RPC boundaries. Reseller HTTP authentication also checks
the active profile, matching the root policy. Administrator recovery and
scoped administrator impersonation remain available. This concerns panel
credentials; comprehensive suspension of already-running tenant processes,
SSH connections and services remains a separate lifecycle review item.

Validation: 40 authentication/impersonation tests passed, including both proof
types across suspension/termination and subsequent reactivation. These
changes are source-only pending the complete authorization migration.

## Dependency candidate — not yet deployed

The initial Python dependency scan reported seven advisories in four packages.
AnyIO, cryptography, setuptools and soupsieve were updated; pyOpenSSL also
needed an update for compatibility. Resolution produced 89 packages and a
scan of that complete resolved set found no known advisories. This is a
database-based dependency check, not proof that the application is secure.
The isolated candidate environment installed successfully and `pip check`
reported no broken requirements. All 62 focused crypto, release-signature,
TLS, terminal and account-state tests passed against it. All 12 previously
unpinned transitive packages are now pinned to this audited resolution.
The running panel's dependency environment has not been replaced; full
candidate deployment remains a release gate.

## Git, error-page, notes and onboarding surface review

Reviewed account/domain lookup, parameter validation, root policy, file sinks,
query scoping and frontend output handling in these four features. Forty-three
feature authorization/behavior tests passed outside sandbox restrictions,
including owner success and denial before RPC for foreign accounts. Five RPC
error/attribution tests passed; note authors are now stamped from the verified
root principal rather than accepted from the request. Legacy HTML routes were
checked for denial before dispatch, not for successful template rendering.

Custom error-page preview previously opened a same-origin HTML blob. The
panel CSP mitigates inline scripts, but the preview lacked document isolation.
It now renders in an iframe with an empty sandbox permissions list and no
referrer. Chromium checks passed both with normal CSP and with CSP bypassed:
the page renders, scripts cannot execute, parent DOM access fails, forms
cannot submit, and no extra window opens. This is defense in depth; no bypass
of the original panel CSP was established. The frontend build passed.

Coverage now records 902 entry points: 158 reviewed-fixed, five reviewed-tested,
11 reviewed-public and 728 pending. Entries are not closed merely because a
shared authorization test exists.

## Native mail-restore gate

The native mail-restore gate was reviewed for fd-3 parsing bounds, return-code
semantics, marker paths, ownership checks and deployment privileges. The helper
is installed root-owned mode 0755, without setuid, and never authenticates a
password. Dovecot configuration verification enforces its failure/delegation
semantics. All 39 gate/config tests passed on the disposable VM, with the test
binary built under undefined-behavior sanitization and 359 deterministic
binary-input cases added. Real isolated Dovecot tests verified normal lookup,
mailbox-specific temporary denial and recovery; no production mail was sent.

## Frontend dependency candidate

The initial npm scan reported ten affected entries (including dependency
parents). Patched the router to 7.18.4, Vite to 7.3.6 with its compatible React
plugin, Monaco to 0.56.0 with DOMPurify pinned to 3.4.16, and compatible
transitives. The final locked graph reports zero known npm advisories.
The production build passed under managed Node 20.19.6. All 23 Chromium tests
passed across both themes, admin/customer navigation, internal forms, mobile
layouts and the sandboxed preview. This does not establish that every original
package advisory was exploitable in the deployed configuration.

## Root log-directory ownership boundary

The shared log directory was writable by the API service group, allowing that
identity to replace a root cron/daemon log filename with a symlink. A protected
VM canary reproduced a root append outside the log directory through that
replacement. Root now owns directory entries without group write access; the
API owns only its precreated access/error files. No-follow regular-file checks
prevent startup from following malicious daemon/API log links. The installer
uses the same preparation function rather than recreating mode 2770.

All 23 daemon/request logging tests passed. The VM reproduced the old write,
then verified the fixed permissions deny API name replacement while preserving
both API append and normal root logging, with the protected canary unchanged.
Live deployment and sustained service checks are recorded separately.

## Remaining scope

The primary has selected fixes, not the complete new root-authorization
protocol. Full migration, remaining entry-point/resource review, exact
candidate installation/update/rollback and publication remain open. The
coverage ledger still has substantial pending review and must not be inferred
complete from a passing policy-registry or regression suite.


## Redis management and failed service cleanup

Redis FLUSHALL and INFO clients now drop supplementary groups and run as the
hosting account UID/GID. A tenant-controlled socket symlink must not let a
root management client bypass another account's private socket permissions.
The disposable VM reproduced a root connection through that link, rejected
it under the tenant identity, and accepted the tenant's own socket command.
The 66 Redis/Node/Python/service-teardown tests passed, including UID selection,
failed-stop/disable handling, retained recovery metadata and normal teardown.

Node/Python/Redis teardown now retains database rows until service removal
succeeds. A failed stop cannot erase its unit/environment files. Account
termination keeps the Unix identity and quotas reserved when cleanup hooks
fail, while the existing authentication revocation still blocks panel access.
This prevents premature name/UID reuse and preserves a retry path.


The account lifecycle regression suite also passed (54 tests). The Redis UID
fix was installed on the primary development server with a protected rollback
copy; both panel services, trusted HTTPS and the real Redis INFO memory probe
passed. The remaining lifecycle changes await the unified release.

## Site log viewer descriptor safety

The PHP/OLS log viewer previously checked a pathname and then reopened it as
root, with an unbounded readlines call. It now uses the shared descriptor-based
bounded tail reader, rejecting symlinked ancestors, symlinked leaf files and
nonregular files. Tests swap either the parent or leaf after resolution and
verify that a protected canary is not returned; normal logs and oversized tails
remain functional. All 29 log/viewer safety tests passed.


## Bounded tenant command output and disk inspection

The shared process runner now drains stdout/stderr incrementally for commands
run under a tenant UID or through runuser. Its combined four-MiB limit applies
before buffering; overflow and timeout kill the dedicated process group,
including children holding pipes open. This closes the root daemon memory
exhaustion path where package hooks could emit unbounded output before a caller
truncated it. Input pumping avoids pipe deadlock. Root backup dumps retain their
existing separate behavior; this is not a claim that every root subprocess has
bounded output.

Disk-tree du/find probes now run as the account user, so path swaps cannot
use root privileges to list private peer files. NUL-delimited records preserve
filenames containing newlines instead of interpreting them as new entries.
The ten disk-tree tests and forty process/command/Composer/WP-CLI tests passed.


## Application, domain-tool and telemetry entry-point disposition

204 grouped tests passed for explicit HTTP/legacy route denial before RPC,
owner/peer/mixed-domain root policies, and normal redirect/forwarding/hotlink/
IP-block/maintenance/health behavior. Manual source review traced resource
binding, validator-to-OLS interpolation, escaped maintenance HTML, custom-page
writes and cron authority. Node/Python/Redis review additionally traced app ID
ownership, tenant command execution, environment encryption and root-owned
unit configuration, shared port allocation inside BEGIN IMMEDIATE transactions,
and lifecycle cleanup. Desired domain settings remain in the database if an
OLS application returns an error; this review does not assert transactional
rollback of those settings or runtime suspension beyond the defined hooks.

The VM passed 34 updated process/disk/log tests and the separate real disk UID
canary (after correcting its initial use of a reserved system username). The
primary received the tested log/process/disk modules; Redis INFO and PHP-log
RPCs passed after daemon startup completed, with both services active and
trusted HTTPS returning 200. Protected backups retain the previous modules.


## Vendor release metadata and full-suite fixture correction

Joomla, Drupal and PrestaShop release metadata now streams with a one-MiB cap
before parsing. Drupal accepts ordinary UTF-8 XML, comments and CDATA but
rejects DTD/entity declarations, NUL/alternate-encoding tricks and malformed
XML. Five negative/positive metadata tests passed. This is defense in depth
for vendor HTTPS input, not a demonstrated remote takeover.

The full VM suite exposed an outdated cPanel mode-restoration fixture: it
simulated a tenant with UID 0, which the hardened ACL subprocess correctly
rejects. The mode-restoration unit test now records its ACL invocation;
separate real-UID safe-I/O tests verify that boundary. The repaired test and
five metadata cases passed together. Production privilege checks were retained.


## Administrative network and service controls

The fail2ban panel jail used a hard-coded 9443 even on this host's 2222
listener. It now derives configured listener ports and includes failed second
factor submissions. Startup reconciles existing managed jail definitions.
The isolated VM regex check matched eight failed IPv4/IPv6 login/2FA requests
and ignored four successful requests. Both hosts received the updated jail;
the primary live nftables action reports port 2222, with HTTPS and services
healthy. No customer traffic was used to trigger bans.

Firewall activation now refuses to continue after a failed prerequisite allow
rule, and reads sshd's effective configuration (including drop-ins/multiple
ports). Cloudflare unlock retains existing scoped rules if restoring general
allows fails. This does not claim discovery of arbitrary systemd socket port
overrides. Failed IP unban preserves its database record for retry; root RPC
stamps the ban actor from the authenticated principal. All 81 grouped network
regressions and 23 reconciliation/dispatch tests passed. The compatible
network modules are hotfixed on the primary; the root server protocol changes
await the complete release. No live UFW activation was attempted here.

An explicit matrix of administrative route modules denies both customer and
reseller callers before RPC/database access, covering API and legacy routes.
The matrix plus service/whitelist tests passed 247 cases. Source review traced
service allowlists, fixed argv, CIDR validation, OLS settings/password controls,
root-only credential storage and non-cacheable reveal responses, package
validation, suspension template interpolation and sandboxed preview, IP
allocation ownership, and mail queue identifier/command validation. Fifty-seven
mail queue/template/IP/OLS behavioral tests also passed. This evidence does not
stand in for reviewing unrelated privileged handlers.


## Quota failures during provisioning

Creation/reactivation no longer swallow Unix password/quota errors and return
an active account. Before any hosting credential row is created, failure locks
and removes the newly created identity while preserving home contents. Failed
cleanup is logged for administrator recovery. Applying a package now reports
quota failure instead of success; desired database limits may already have
changed, so this does not claim a transaction spanning the kernel and SQLite.
Regression coverage checks failed creation/reactivation, retained terminated
state, preserved home contents in rollback argv, and normal lifecycle behavior.

All 77 current account/package/sysops tests passed. These changes are not yet
deployed on the primary.
