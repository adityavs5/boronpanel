# Checkpoint — security follow-up (2026-08-06)

No live deployment or host mutation was performed.

## FileBrowser A3-7

- `daemon/filebrowser.py` inserts the `boron-api` owner ACCEPT rule at
  `OUTPUT` position 1 and the port REJECT rule at position 2.
- Rules are checked with `iptables -C` first and therefore remain idempotent.
- `daemon/server.py` calls `filebrowser.bootstrap()` during every
  `boron-provisiond` start, re-applying the restriction after reboot or rule
  loss. The provision daemon unit permits `AF_NETLINK` for the iptables/nft
  backend.

## GeoLite2

- Interactive installs prompt for an optional MaxMind license key; the
  `FH_MAXMIND_LICENSE_KEY` environment variable remains available for
  automation.
- `geoipupdate` is installed with the base packages. A supplied key fetches
  GeoLite2-Country and creates `/etc/cron.d/boron-geoip` with a root-only key
  file for weekly refreshes.
- An empty key leaves the panel functional and reports that only
  top-countries statistics are unavailable.

## PHP hardening

The installer writes the shared `daemon/phpdirectives.DEFAULT_DISABLE_FUNCTIONS`
list (including `exec`, `system`, `shell_exec`, `passthru`, `proc_open`, and
`popen`) into every installed lsphp php.ini. Admin-only overrides are validated,
stored per account/domain, and rendered into the corresponding OLS vhost;
customer-facing PHP settings cannot change this override table.

## Verification

- `bash -n scripts/install.sh`
- focused installer/FileBrowser/PHP tests
- The FileBrowser bootstrap test now mocks the host systemd-unit write, so
  CI/container runs with read-only `/etc` remain hermetic; production
  bootstrap behavior is unchanged.
