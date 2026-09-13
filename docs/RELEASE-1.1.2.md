# BoronPanel 1.1.2

Includes Evolution Icons Grid, Paper Lantern, and the self-update fixes from 1.1.0–1.1.1.

The update finalizer now performs its daemon health probe using the API service account, matching the daemon's Unix-socket peer authentication. It restores its original identity before writing update state or performing rollback. A real-socket regression test checks the peer UID and completion of the subsequent privileged work.

This fixes false health-check failures and automatic rollbacks when both panel services are actually healthy. Full preflight tests, checksums, backups and rollback remain enabled.
