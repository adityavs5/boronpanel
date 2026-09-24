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

## Remaining scope

The primary has selected fixes, not the complete new root-authorization
protocol. Full migration, remaining entry-point/resource review, exact
candidate installation/update/rollback and publication remain open. The
coverage ledger still has substantial pending review and must not be inferred
complete from a passing policy-registry or regression suite.
