# BoronPanel 1.1.1

Includes the Evolution Icons Grid and Paper Lantern themes introduced in 1.1.0, plus a self-update preflight fix discovered during this server's first update.

The updater now runs its complete regression suite in a bounded temporary systemd service, so tests of setgid filesystem behavior do not inherit the provisioning daemon's RestrictSUIDSGID filter. The main daemon keeps its security restrictions. Test fixtures also avoid assuming a particular amount of free space on their temporary filesystem; a dedicated test verifies the real disk-space rejection.

The normal checksum, backup, staging, restart, health-check and rollback protections remain enabled.
