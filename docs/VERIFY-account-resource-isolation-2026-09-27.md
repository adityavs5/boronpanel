# Account resource enforcement and isolation verification

Date: 2026-09-27  
Scope: P0–P4 from `PLAN-account-resource-isolation-2026-09-27.md`  
Target: Ubuntu 24.04 development server, OpenLiteSpeed 1.9.2, systemd 255, unified cgroup v2

## Result

The canonical resource parent is `user.slice/user-<uid>.slice`. Native OpenLiteSpeed startup placement, PAM/logind sessions, Boron application units, File Manager, and Boron-launched account commands share that parent. The live isolation dashboard now reports measured states instead of hard-coded success.

P5, the optional jailed customer shell, was deliberately excluded from this release. OpenLiteSpeed provides verified mount isolation and private temporary files for PHP. It does not provide a private PID namespace, and the dashboard reports that capability as unavailable.

Root-only pre-change evidence is retained outside the repository at `/root/boron-security-audit/2026-09-27/isolation/`.

## Live evidence

Two disposable accounts were created and removed through Boron's normal lifecycle. No destructive or stress probe used the existing customer account.

| Control | Evidence | Result |
| --- | --- | --- |
| PHP startup placement | A first request started `lsphp` directly in `/user.slice/user-1001.slice/litespeed-exec.scope`; no post-start PID move was used. | Passed |
| Aggregate CPU | Two simultaneous account jobs under a 0.25-core parent increased `nr_throttled` by 38 during a 3.83-second bounded probe. | Passed |
| Memory | A 96 MiB disposable allocation beneath a 64 MiB parent produced one `oom_kill` and 19 `memory.events:max` events; the other account and server services remained healthy. | Passed |
| Tasks | A bounded child fixture under `TasksMax=12` was denied at 10 children with `EAGAIN`, then reaped every child. | Passed |
| Disk I/O | An 8 MiB direct write beneath a 2 MB/s parent took 4.75 seconds. The probe found and fixed the old hard-coded `/dev/vda` assumption; Boron now resolves the filesystem's actual major:minor and atomically replaces stale device rules. | Passed |
| Panel Terminal | The real ephemeral-key Terminal path entered `session-*.scope` beneath `user-1001.slice`. The key existed only in memory and its authorized-key marker was removed. | Passed |
| SSH/SFTP/SCP | Interactive SSH, command SSH, SFTP, modern SCP, and legacy SCP used the account parent. While SFTP was open, zero processes owned by the account UID were outside the parent. Temporary test keys and files were removed. | Passed |
| Filesystem isolation | Each account could see its own home but not a root-owned peer canary or `/etc/shadow`. PHP used a mount namespace distinct from PID 1 and a private `/tmp` mount. | Passed |
| PID namespace | PHP shares the host PID namespace. The dashboard reports `unavailable`, rather than claiming process isolation. | Accurate limitation |
| Application services | Live Redis and File Manager units for the existing account were under `user-1000.slice` with `PrivateTmp=yes`, `NoNewPrivileges=yes`, and `ProtectSystem=strict`. | Passed |
| Short-lived work | The trusted launcher ran below the account slice before executing customer-owned commands. WordPress, app installer, Git, disk-tree, Node, Python and command-job paths use this launcher. | Passed |
| Lifecycle | Suspend/unsuspend, a username rename round-trip, limit changes, daemon restarts, account termination, and policy cleanup completed. | Passed |
| Namespace teardown | A live test exposed an unmount-before-disable race. Teardown now disables first, unmounts second, reports helper failure, and retains the Linux identity on incomplete cleanup. A fresh persisted namespace was then removed successfully during termination. | Passed after fix |
| Existing account convergence | The final dashboard measured two PHP workers in `user-1000.slice`; Redis, File Manager, resource parent, mount isolation and private temporary files all reported `verified`. | Passed |

## Regression evidence

Focused suites covered cgroup policy/resolution, resource management, namespace state and teardown, account execution, Terminal/SSH authorization, File Manager, Node/Python units, WordPress/app-installer launch paths, installer/upgrade behavior, account/domain provisioning, and OpenLiteSpeed configuration. The focused checks passed after the two live-found defects were repaired.

OpenLiteSpeed configuration validation and `sshd -t` passed. `boron-api`, `boron-provisiond`, and `lsws` remained active. The disposable Linux users, homes, vhosts, namespace trees, test credentials, files, and cgroup policy drop-ins were removed. Their database rows remain terminated audit tombstones by design.

The repository-wide test, production frontend build, signed archive verification, tag, and GitHub release are executed once by `scripts/release.sh --patch`; publication must stop if any gate fails.
