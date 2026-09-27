# Account resource enforcement and isolation plan

Date: 2026-09-27
Status: **P0–P4 implemented; live verification and v3.0.3 release in progress. P5 remains a separate optional phase.**

## 1. Objective and boundaries

Make hosting-account CPU, memory, disk I/O and task limits apply to the workloads the panel says they cover. Preserve working OLS filesystem isolation, report live enforcement accurately, and define an optional jailed customer shell as a separate phase.

This plan follows the live investigation after commit `f411e4c`. It corrects gaps in the resource/isolation work described in `PLAN-mail-dns-resource-security-stack-2026-09-26.md`; the earlier completed status is not evidence that these newly discovered gaps are resolved.

Implementation requires approval of this plan. Publication of a release or GitHub push remains a separate instruction. Planning makes no runtime changes. Existing Evo/Paper layout, icon sizing, domain selection and branding fixes remain acceptance constraints.

## 2. Evidence and known limitations

| Area | Evidence from this server | Required response |
| --- | --- | --- |
| PHP resource placement | Account PHP workers remain in `system.slice/lshttpd.service`; moving them into the parent account slice repeatedly returns `EBUSY`. The destination has enabled domain controllers and a Redis child service. | Use valid leaf service/scope placement and verify aggregate enforcement. |
| PHP startup | The present implementation polls every few seconds after OLS starts workers. | Address execution before placement; polling alone cannot prove uninterrupted limits. |
| File Manager | Its per-account service has its own sandbox and limits, but resides outside the Boron account slice. | Preserve its sandbox and put its resource consumption under the account budget. |
| Terminal/SSH/SFTP | Terminal connects through localhost SSH; PAM includes `pam_systemd`. Terminal code does not explicitly establish Boron account-slice membership. | Verify actual login scopes and unify their aggregate budget with web/app workloads. |
| Redis/apps | Generated services specify account slices and hardening. Redis is under the account resource slice, but an inspected Redis PID shared the host mount namespace. | Distinguish generated configuration from running enforcement; inspect actual service properties and process state. Do not claim private temporary files solely from the unit file. |
| OLS filesystem | A live account PHP worker had a distinct mount namespace, account-specific `/tmp`, and a restricted filesystem view. | Retain and validate this independently of resource controls. |
| Process visibility | Inspected PHP and Redis processes share the host PID namespace. | Report accurately; mount isolation is not PID isolation. |
| Dashboard | Capability flags are hardcoded true; application hardening is inferred from database rows, and the terminal label asserts cgroup coverage. | Replace assertions with measured/configured/unknown states. |
| Self-test | Current test can pass with no second account and checks readability rather than full filesystem visibility. | Report skipped checks; use two disposable accounts for meaningful isolation validation. |

Earlier statements that all Redis hardening was already verified were too strong. Running settings and kernel observations must settle discrepancies before that status becomes green.

## 3. Design decisions

### 3.1 One aggregate budget per account

CPU cores, memory high/max, swap policy, read/write bandwidth, read/write IOPS and task ceilings belong to a single common ancestor. PHP plus Redis plus a shell must not each receive the entire account allowance independently. Descendant limits may be tighter; they cannot expand the aggregate budget.

Use systemd-owned service/scope leaves. Do not place processes directly into an internal slice with domain controllers enabled, hand-create competing cgroups beneath systemd-owned units, or grant customers write access to ancestor cgroups. The kernel's internal-process rule and memory accounting behavior inform this design. [Linux cgroup v2 documentation](https://cdn.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html)

### 3.2 Preferred final hierarchy: native per-UID slices

Prototype a canonical `user.slice/user-<uid>.slice` budget, with:

- OLS-created PHP/CGI session scopes using its native cgroup integration.
- SSH, panel Terminal and SFTP login scopes managed by systemd/logind.
- Boron Node.js, Python, Redis and per-account File Manager services assigned to that same user slice.
- Account-owned scheduled/background jobs classified and brought under the same budget where Boron starts them.

This is a deliberate improvement over moving arbitrary SSH processes into the existing Boron slice. The installed `lscgctl` explicitly targets `user-<uid>.slice`; native OLS support is documented, but its suitability for this server's shared external processors still needs a runtime prototype. [OLS cgroup integration](https://docs.openlitespeed.org/config/advanced/cgroups/)

Do not change external-application UID selection simply to copy documentation examples: the effective UID must remain the domain owner's validated account UID. Preserve shared infrastructure workers such as webmail and phpMyAdmin outside customer budgets.

### 3.3 Immediate PHP repair and fallback

Before migrating the final hierarchy, support a systemd-managed PHP scope beneath the existing `boron-<username>.slice`. Obtain its actual `ControlGroup` from systemd and attach validated workers there. This closes the current `EBUSY` placement failure without placing processes in an internal node.

Treat this as an interim repair: post-start migration does not retroactively transfer all memory charges and can miss short-lived workloads. If native placement passes the prototype, deliver the canonical hierarchy in the same implementation batch. If it fails, retain the repair with a visible degraded/startup-gap state and document the failing compatibility case. Do not mark full enforcement complete or introduce a new setuid/capability helper to conceal that limitation.

## 4. Ordered work packages

### P0 — Baseline, ownership and compatibility prototype

1. Capture current account policy, effective kernel limits, process membership, unit definitions/drop-ins, OLS configuration and namespace template. Store snapshots root-only without exposing credentials.
2. Introduce a central account-to-resource-container resolver used by limit application, service generation, monitoring and lifecycle operations. Use validated account IDs/UIDs and systemd unit escaping.
3. Inspect installed OLS and systemd versions and native cgroup configuration; the server currently runs systemd 255. Verify required controllers, block-device mapping and any parent limits.
4. Use two disposable local accounts to prototype native OLS placement and a regular SSH session under the same per-UID slice. Verify PHP is placed before user code executes and descendants inherit placement.
5. Verify account services survive the last SSH logout, logind/user-manager restarts and daemon restarts. Configure lifecycle dependencies deliberately; do not globally change `KillUserProcesses` or enable lingering for all users.
6. Record the prototype evidence and final hierarchy decision before account migration. Preserve a working administrator session and a tested automatic rollback path for authentication-related changes.

**Acceptance:** one documented canonical parent works for PHP and login sessions, or a precise compatibility blocker is recorded. No inference from config files alone.

### P1 — Repair PHP placement and lifecycle

1. Implement valid PHP leaf scopes for the interim repair, then native startup placement if P0 passes. Reconcile against the selected canonical parent rather than a fixed filesystem path.
2. Root-side placement must verify account state, UID, process identity/start time and permitted source workload. Handle PID reuse, exiting workers, descendants, empty scopes and concurrent reconciliation. Prefer pidfds where supported; revalidate identity at placement boundaries.
3. Never move the OLS master, infrastructure workers, unrelated UIDs or administrator processes. A customer must not be able to submit an arbitrary PID, unit name or cgroup path to the privileged layer.
4. Apply limits before admission, rate-limit repeated error logging, and expose placement failures and uncovered workload counts in health status.
5. Gracefully replace preexisting PHP workers during migration so new allocations occur in the correct group. Moving their PIDs alone is insufficient proof of memory coverage. Account for inherited/shared allocations in the report.
6. Preserve account rename, suspend/reactivate, termination, UID-reuse cleanup, policy expiry and restart behavior. Do not drop limits during a failed update.

**Acceptance:** no recurring placement `EBUSY`; all canary PHP descendants are in the intended leaf hierarchy; native startup placement is proven or explicitly reported as incomplete.

### P2 — Aggregate applications, File Manager and usage reporting

1. Update Node.js, Python, Redis and File Manager unit generators, checked-in templates and upgrade paths to use the resolver. File Manager retains its private home view, Unix-socket access controls and existing tighter limits.
2. Validate actual running hardening, including Redis `PrivateTmp` and mount-state discrepancy; identify stale service state or configuration overrides before restarting anything.
3. Migrate services one account at a time with readiness checks and targeted restarts. A `Slice=` change does not move an already-running service automatically.
4. Point resource samples and faults at the canonical parent. Prevent double counting during migration and handle CPU-counter resets without artificial usage spikes. Missing controllers or unreadable metrics must be unknown/unavailable, never zero usage or unlimited success.
5. Inventory cron, WP-CLI, Composer, Git, migration helpers and customer-triggered jobs. Put account-executed workloads under the account budget through trusted launch paths. Explicitly distinguish shared administrative backup/migration infrastructure and shared MariaDB/mail services; their cost cannot honestly be attributed using customer UID cgroups alone.
6. Preserve plan inheritance and account overrides. Keep task/thread limits, concurrent web entry requests, Redis data limits and database governance distinct; they measure different things.

**Acceptance:** concurrent workloads share one ceiling; File Manager and enabled app services have verified membership; coverage exclusions are visible and documented.

### P3 — Terminal, SSH and SFTP coverage

1. With the preferred hierarchy, retain PAM/logind's session scopes beneath the per-UID parent. Verify actual membership for panel Terminal, interactive SSH, command-only SSH, SFTP and SCP modes.
2. Ensure the account policy exists before a customer session is admitted. Use a narrow account-specific admission check only if the existing ordering cannot guarantee this. Scope it to Boron-managed hosting UIDs, never root or arbitrary server users.
3. Preserve the installed PAM stack and normal logind ownership of scopes. Do not use a generic `pam_exec` script that moves an sshd parent or scans arbitrary processes after login as the main enforcement mechanism.
4. Test forked/background jobs, reconnects, concurrent sessions, daemon failure and logout. Customer attempts to change unit properties or escape through a user systemd manager must remain bounded by the parent.
5. Validate `sshd -t` and effective Match behavior before any SSH configuration reload. Keep administrator recovery access available; stage an account-scoped rollback timer before changing authentication/session wiring.
6. If enforceable admission cannot be established, show a coverage failure and prevent new customer sessions from being represented as fully limited. Existing sessions require explicit migration/reconnect status, not a blanket termination.

**Acceptance:** every supported customer login path shares the same aggregate budget; administrator recovery access works; unknown/error states never silently become unrestricted success.

### P4 — Evidence-based isolation dashboard

1. Replace boolean assertions with `configured`, `verified`, `degraded`, `disabled`, `unavailable`, and `not applicable`, including last-check time and a concise reason. Idle accounts with no worker are configured, not failed or runtime-verified.
2. Report filesystem visibility, temporary files, process visibility/PID namespace and resource coverage separately for Web/PHP, Terminal, SSH/SFTP, apps and File Manager.
3. Inspect OLS effective settings, actual account UID, namespace identity/mounts and cgroup paths. Compare runtime systemd properties with expected hardening. A different mount namespace alone does not prove that other homes or secrets are hidden.
4. Cache bounded metadata checks and debounce refresh; do not start workloads or create namespaces merely because someone views the page. Keep process arguments, secrets and another customer's files out of API responses.
5. Rework the explicit self-test to report individual outcomes, including skipped peer-account checks. Use root-owned canary metadata and disposable fixtures; never expose customer file contents.
6. Add recovery guidance to degraded states. Rebuild must describe whether existing workers need replacement and must not report immediate success from eligibility alone.
7. Use existing Evo/Paper components and responsive tables; retain dashboard spacing, typography, logo behavior and icon consistency.

**Acceptance:** deliberately disabled isolation or misplaced workers produce a clear degraded state. No hardcoded Active badges remain. OLS-only PID isolation is accurately shown as unavailable.

### P5 — Optional jailed customer SSH/SFTP (separate activation phase)

After P0–P4 pass, design and prototype an opt-in account setting: Disabled access, SFTP-only jail, or Jailed shell + SFTP. Upgrades retain current shell behavior until an administrator enables the selected mode.

- Prefer standard OpenSSH internal-SFTP with a root-owned chroot for SFTP-only mode. The jail root must not be the customer-writable home; expose a writable account directory inside it.
- Prototype full shells with a maintained namespace/container mechanism invoked through an authenticated, root-controlled entry point. Do not ship a new broadly privileged setuid launcher. Final runtime choice requires a compatibility/threat-model review before activation.
- Define explicit bind mounts, read-only runtime files, private temporary directories, devices, DNS/TLS trust and required sockets. Namespace entry and mount construction must resist customer-controlled symlinks and races.
- Treat private PID namespaces as a separate capability. Chroot alone does not provide them. If supplied, include a correctly mounted procfs, child reaping, signal forwarding and terminal job-control behavior.
- Verify Bash, SSH commands, modern and legacy SCP behavior, SFTP, Git, rsync, Composer, WP-CLI, supported PHP/Node/Python tools, database sockets and account Redis access. State any deliberate unsupported feature before enabling it.
- Preserve the aggregate cgroup budget inside the jail. Provide preview, canary enablement, rollback and recovery without altering root SSH access.

**Acceptance:** cross-account and host-sensitive filesystem probes are denied in disposable fixtures; advertised tool workflows pass; enabling/disabling the jail does not damage account data. Do not describe this as CageFS-equivalent without separately proving that breadth of coverage.

## 5. Tests and proof of completion

Run focused tests during implementation; avoid repeated full-suite runs. Mocked cgroup files cannot reproduce kernel placement rules, so a small real cgroup integration test is mandatory.

| Validation | Required proof |
| --- | --- |
| Kernel regression | An internal account slice with Redis/app descendants accepts PHP through a proper leaf; no `EBUSY`. |
| Aggregate CPU | Bounded PHP plus shell/app CPU load shares the configured total core allowance; kernel throttling counters increase. |
| Memory | Disposable allocations exercise high/max boundaries inside an independently capped test parent; inspect charge placement, reclaim/OOM events and other-account survival. Never stress live customer memory limits. |
| Tasks | Bounded fork/thread fixture reaches the test account ceiling without a fork bomb or affecting administrator login. |
| I/O | Small fixed-size direct-I/O fixture on the actual home backing device validates bandwidth/IOPS where supported; unsupported/cache-only measurements are reported honestly. |
| Startup and descendants | New PHP workers are already in the correct hierarchy before customer code; forks, short-lived commands and respawns remain covered. |
| Sessions | Panel Terminal, interactive/noninteractive SSH, SFTP, SCP, reconnect and background process coverage verified. |
| Lifecycle | Account create/rename/suspend/terminate, daemon/OLS/service restarts, last logout, policy update/expiry and upgrade retry are idempotent. Reboot coverage requires a disposable environment or scheduled reboot, not an unannounced live reboot. |
| Filesystem | Two-account canaries cover foreign homes, sensitive host paths, private tmp and process visibility separately. |
| Regression | Existing sites, Redis, File Manager, webmail one-click login, phpMyAdmin and application launch remain usable. |
| UI | Evo/Paper desktop/mobile/light/dark checks cover configured, verified, degraded, idle and unavailable states without changing unrelated page layouts. |

Each live fixture has a time limit, explicit CPU/memory/task cap, cleanup manifest and an outer safety limit. Do not mutate or use live customer accounts for destructive tests. Keep evidence metadata-only. Record unrun validations and do not count them as passed.

Relevant suites include `test_cgroups.py`, `test_resource_manager.py`, `test_resource_limits.py`, `test_nsisolation.py`, `test_terminal.py`, `test_terminal_authz.py`, `test_filebrowser_accounts.py`, account/app lifecycle tests and the resource/isolation browser scenarios. Add only tests that exercise meaningful failure boundaries or contracts.

## 6. Rollout and rollback

1. Save configuration and runtime-policy snapshots; build a per-account migration inventory and rollback manifest.
2. Implement observation/reporting and validate disposable-account placement before enforcing changed session paths.
3. Apply the immediate PHP repair if needed, then migrate one canary account to the canonical parent. Apply effective limits to the destination before any workload enters it.
4. Migrate apps and refresh PHP workers in a controlled sequence. Report mixed old/new hierarchy as migration-in-progress; separate old and new parents are not proof of a shared aggregate ceiling.
5. Observe admission, service readiness and kernel coverage. Stop expansion on failure and restore saved config/policies with targeted service recovery. Never delete a cgroup while it still contains customer processes.
6. Persist the migration state and resolver so bootstrap cannot accidentally restore the old hierarchy. Remove obsolete unit/drop-in state only after it is empty and rollback no longer needs it; sanitize old UID policy before UID reuse.
7. Update installer, self-update integration and deployment documentation together. Verify fresh-install and upgrade configuration paths with focused fixtures.
8. Deploy approved work on this development server, report evidence and limitations, and keep release publication pending the user's instruction. Activate optional jail behavior only after its separate compatibility gate.

## 7. Files and deliverables

- Resource enforcement/resolver: `daemon/cgroups.py`, `daemon/resource_manager.py`, account lifecycle and daemon bootstrap/reconciliation.
- PHP startup: `daemon/ols.py`, OLS configuration templates and installed-version integration.
- Workload coverage: `daemon/appunits.py`, `daemon/nodeapps.py`, `daemon/pythonapps.py`, `daemon/redisacct.py`, `daemon/filebrowser_accounts.py`, `deploy/boron-filebrowser@.service`, terminal/session and account job launch paths.
- Reporting: `daemon/nsisolation.py`, `api/routers/isolation.py`, resource APIs, `frontend/src/pages/admin/FilesystemIsolation.jsx`, related resource views and API contract.
- Persistence: `scripts/install.sh`, `scripts/upgrade_runtime.py`, migration bookkeeping and rollback documentation.
- Delivery report: changed behavior, selected hierarchy, per-entry-point coverage, test results, service restarts, unresolved limitations and rollback location.

## 8. Research references

- [Kernel cgroup v2 documentation](https://cdn.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html): internal-process constraint, hierarchy, migration and controller/accounting semantics.
- [OpenLiteSpeed cgroups](https://docs.openlitespeed.org/config/advanced/cgroups/): native CGI integration; local `/usr/local/lsws/lsns/bin/lscgctl` confirms the per-UID slice path.
- [OpenLiteSpeed namespace containers](https://docs.openlitespeed.org/config/advanced/namespaces/): namespace lifecycle and configuration behavior. Local process namespace inspection remains the evidence for actual PID isolation.
- Installed systemd 255 `pam_systemd(8)` and unit manuals: version-matched session ownership and per-user slice behavior. Online latest/versioned man pages returned access errors during research; use the installed manuals as implementation authority rather than assuming newer features exist.

## 9. Approval scope

Recommended first batch: **P0–P4 plus focused validation and development-server deployment**. P5 is fully scoped here but should follow as an opt-in jail phase after the core enforcement work passes. Approval of the first batch does not imply a release, a live reboot, or enabling customer jails by default.
