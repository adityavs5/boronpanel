# Optional restricted SSH/SFTP namespace

## Recommendation

Keep the current Linux UID boundary as the default. Add a future, account-level **Restricted SSH/SFTP** option only after a privileged Ubuntu 24.04 compatibility suite covers interactive SSH, SFTP, Git, cron, Composer, WP-CLI, Node.js and Python deployments. Do not claim that the current OpenLiteSpeed namespace covers SSH or SFTP.

## Proposed boundary

Use the existing reviewed Bubblewrap policy as the source of allowed mounts, but launch a separate session namespace from PAM/`sshd` rather than reusing an OpenLiteSpeed worker namespace. The launcher would:

- enter new mount, PID, IPC and UTS namespaces;
- bind the account home read/write and expose a private `/tmp`;
- mount the minimum read-only runtime paths needed by the selected shell and SFTP subsystem;
- expose `/proc` from inside the private PID namespace;
- drop every capability, set `no_new_privs`, retain the account UID/GID and join the canonical account cgroup;
- reject unsafe environment variables and resolve every bind source from a root-owned allowlist;
- fail closed before starting a shell when the namespace cannot be built.

The SSH forced command and SFTP subsystem must both use the same launcher. Applying it only to interactive shells would leave SFTP outside the promised boundary.

## Compatibility risks

- Customer binaries can depend on libraries or paths omitted from the read-only runtime mounts.
- Git hooks, Composer, WP-CLI, Node.js and Python virtual environments may invoke interpreters through paths that must remain visible.
- SSH agent forwarding, port forwarding, FUSE, Docker, systemd user services and host `/proc` inspection are incompatible with the restricted profile and should remain disabled there.
- An incorrect PAM or `sshd_config` change can lock out hosting users. Root administration must use a separate, tested path and the installer must retain an automatic rollback timer.
- Long-lived sessions need an explicit drain policy when the namespace template changes.

## Rollout

1. Add a dry-run diagnostic that renders the exact mount policy and validates every source without changing `sshd`.
2. Add a disposable test account and exercise the compatibility matrix in the nightly privileged VM workflow.
3. Offer the feature per account, disabled by default, with a timed rollback after the first activation.
4. Record the template version in each active session and show the measured namespace/cgroup state in Filesystem Isolation.
5. Promote it to a server default only after existing-account migration tests and an operator recovery procedure pass.

This design intentionally avoids an always-on per-account daemon. Bubblewrap starts only when an SSH/SFTP session starts, and the existing systemd/logind cgroup remains the resource boundary.
