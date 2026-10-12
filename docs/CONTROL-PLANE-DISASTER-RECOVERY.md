# Control-plane disaster recovery

The control-plane archive contains the SQLite panel database and the complete
`/etc/boron` tree, including the panel's encryption keys and certificates. It
does not contain customer home directories, mailboxes, MariaDB databases or
backup repositories. Restore those from account backups after restoring the
control plane.

Create a recovery key file as root and keep a copy outside the server:

```sh
install -m 0600 /dev/null /root/boron-recovery-key
openssl rand -base64 48 > /root/boron-recovery-key
chmod 0600 /root/boron-recovery-key
```

Export an encrypted archive:

```sh
/opt/boron/.venv/bin/python /opt/boron/scripts/control_plane_recovery.py export \
  --output /root/boron-control-plane.boroncp \
  --recovery-key-file /root/boron-recovery-key \
  --panel-version "$(cat /opt/boron/VERSION 2>/dev/null || true)"
```

On a fresh Ubuntu 24.04 host, install the same or a newer compatible Boron
release first. Copy the encrypted archive and recovery-key file to the host.
Validate the archive and inspect UID conflicts without changing the host:

```sh
/opt/boron/.venv/bin/python /opt/boron/scripts/control_plane_recovery.py restore \
  --archive /root/boron-control-plane.boroncp \
  --recovery-key-file /root/boron-recovery-key \
  --dry-run
```

Stop `boron-api` and `boron-provisiond`, then restore. The explicit overwrite
flag is required whenever a panel database already exists:

```sh
systemctl stop boron-api boron-provisiond
/opt/boron/.venv/bin/python /opt/boron/scripts/control_plane_recovery.py restore \
  --archive /root/boron-control-plane.boroncp \
  --recovery-key-file /root/boron-recovery-key \
  --overwrite-live
systemctl start boron-provisiond boron-api
```

The restore authenticates the encrypted archive, verifies every manifest
hash, runs SQLite integrity checks, detects username/UID conflicts, recreates
missing Linux account identities with their original UIDs, preserves a rescue
copy of the previous database and `/etc/boron`, and installs the recovered
database with root/`boron-api` ownership. A wrong recovery key or modified
archive is rejected before the live files are touched.

After restoring, run Diagnostics and restore the customer account archives.
Verify panel login, hosted sites, DNS, mail and certificates before removing
the rescue copies reported by the command.
