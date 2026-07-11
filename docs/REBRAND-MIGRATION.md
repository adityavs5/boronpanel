# Rebrand Migration — Forgehost → Boron Panel

Companion to `docs/REBRAND-INVENTORY.md` (read that first for the full
categorization and the reasoning behind each decision below). This doc is
for **an existing, already-running Forgehost install** — a fresh install
never needs any of this, `scripts/install.sh` already produces a
Boron-named install from scratch.

**This has not been run against this box.** Per this project's standing
convention (live infrastructure mutations need explicit operator
authorization — see `docs/AUDIT3-FINDINGS.md` A3-7 for the same principle
applied during the prior security audit), the code, the migration script,
and this document are all ready, but executing the migration against the
real running server is left to the operator, not auto-run by this rebrand
pass.

## What changes, and why it's not just a find-and-replace

| Old | New |
|---|---|
| systemd: `forgehost-api.service`, `forgehost-provisiond.service`, `forgehost-filebrowser.service` | `boron-api.service`, `boron-provisiond.service`, `boron-filebrowser.service` |
| systemd (dynamic, per account): `forgehost-redis-<user>-<id>.service`, `forgehost-node-<user>-<id>.service`, `forgehost-python-<user>-<id>.service`, `forgehost-<user>.slice`, `forgehost.slice` | `boron-redis-...`, `boron-node-...`, `boron-python-...`, `boron-<user>.slice`, `boron.slice` |
| Paths: `/opt/forgehost`, `/etc/forgehost`, `/var/log/forgehost`, `/var/lib/forgehost`, `/var/lib/forgehost-pma-tokens`, `/opt/forgehost-nodejs`, `/var/backups/forgehost`, `/run/forgehost` | `/opt/boron`, `/etc/boron`, `/var/log/boron`, `/var/lib/boron`, `/var/lib/boron-pma-tokens`, `/opt/boron-nodejs`, `/var/backups/boron`, `/run/boron` |
| Config file `forgehost.toml` | `boron.toml` |
| SQLite DB `forgehost.db` | `boron.db` |
| System user/group `forgehost-api` | `boron-api` |
| Env vars `FORGEHOST_CONFIG`/`FORGEHOST_SECRETS`/`FORGEHOST_API_SECRETS` | `BORON_CONFIG`/`BORON_SECRETS`/`BORON_API_SECRETS` |
| Postfix map `/etc/postfix/forgehost_relay_domains` | `/etc/postfix/boron_relay_domains` |
| **Unchanged, deliberately** (Decision 1, `docs/REBRAND-INVENTORY.md`): MariaDB user `forgehost_daemon`, MariaDB user `forgehost_mailro`, MariaDB schema `forgehost_mail` | *(no change — same live credentials/schema)* |

The systemd rename is the genuinely hard part: this box (checked before
writing this doc) has **live, active state** under the old names —
`forgehost-redis-adityascn-1.service` (running), `forgehost-adityascn.slice`
/ `forgehost-cust1.slice` / `forgehost-demo2.slice` (active), and
`forgehost-node-demo1-1/2.service` (disabled but present). These aren't
static files to `mv` — they're systemd units backing real running processes
(a customer's Redis instance, cgroup resource limits currently constraining
real accounts).

**The reason this is tractable at all**: `forgehostd`'s (soon `borond`'s)
own startup already self-heals this exact category of state. Four
functions already run on every daemon start
(`daemon/server.py:719,724,728,732`):
`cgroups.bootstrap_all_slices`, `nodeapps.bootstrap_all_node_apps`,
`pythonapps.bootstrap_all_python_apps`, `redisacct.bootstrap_all_redis` —
each reads the DB's current account/app state and recreates the
corresponding systemd unit + cgroup slice if missing, using whatever naming
the *current code* uses. Once the daemon binary is the Boron-renamed
version, these functions write `boron-*` units, not `forgehost-*` ones. So
the migration script does **not** hand-craft new unit files for every
account — it only needs to **stop and remove the old units**, then let a
restarted, newly-renamed daemon recreate the equivalent new ones from DB
state, the same self-healing property this project already relies on for
host reboots.

## Running the migration

```
sudo bash scripts/migrate_to_boron.sh
```

Read the script (`scripts/migrate_to_boron.sh`) before running it — it's
short and heavily commented. Summary of what it does, in order:

1. **Stops the panel and every per-account app**: `forgehost-api` and
   `forgehost-filebrowser` first (external-facing), then every
   `forgehost-{redis,node,python}-*.service` unit (enumerated live via
   `systemctl list-units`, not hardcoded), then every
   `forgehost*.slice`, then `forgehost-provisiond` last. Old unit files are
   removed after stopping.
2. **Renames the system user/group** (`usermod -l` / `groupmod -n`) —
   preserves the uid/gid, so file ownership across the moved directories
   below stays correct with no `chown` needed.
3. **Moves filesystem paths** (`mv`, same-filesystem, atomic): `/etc`,
   `/var/log`, `/var/lib`, the PMA token dir, the Node.js runtime dir, the
   optional backups dir. Renames `forgehost.toml` → `boron.toml` and
   `forgehost.db` (+ any `-wal`/`-shm`/`-journal` sidecars) → `boron.db`
   inside their new parent directories. Renames the Postfix relay_domains
   map if present, fixes the one `main.cf` reference, and reloads Postfix.
4. **Moves `/opt/forgehost` → `/opt/boron`**, then runs
   `scripts/deploy.sh` to sync the actual Boron-branded source over it
   (preserves the existing venv rather than reinstalling every dependency
   from scratch).
5. **Installs the renamed systemd/cron/logrotate files** from
   `deploy/boron-*` and removes the old `forgehost-*` ones from
   `/etc/systemd/system`, `/etc/cron.d`, `/etc/logrotate.d`.
6. **Starts `boron-provisiond` first** — this is the step that triggers
   the four bootstrap functions above, recreating every account's
   slice/Redis/Node/Python units under the new names. Then starts
   `boron-filebrowser` and `boron-api`.
7. **Verifies**: service `is-active` for all three main units, lists what
   `boron*` units/slices now exist, confirms no `forgehost*` units remain,
   and hits `/healthz`.

## Manual verification checklist (after running the script)

Don't consider the migration done just because the script exited 0 — per
this project's own "verify, don't assume it worked" convention (the exact
lesson from `docs/AUDIT3-FINDINGS.md` A3-7's own live-testing surprise),
check these for real:

- [ ] `systemctl status boron-api boron-provisiond boron-filebrowser` — all
      `active (running)`, `0` restarts.
- [ ] For every account that had a Redis instance before migration
      (`forgehost-redis-*` in your pre-migration `systemctl list-units`
      output — recorded automatically in the script's own log output):
      confirm `boron-redis-<user>-<id>.service` is active and the account's
      app can actually reach its Redis socket again (a real PHP/app-level
      round trip, not just "the unit exists").
- [ ] Same for every Node/Python app: confirm the new
      `boron-node-*`/`boron-python-*` unit is in the same enabled/disabled
      state it was before, and a running app is actually reachable through
      its OLS reverse proxy again.
- [ ] Every account's cgroup slice (`boron-<user>.slice`) exists and
      `systemd-cgtop`/`systemctl status boron-<user>.slice` shows the
      expected resource limits — compare against the account's
      `cpu_pct`/`mem_mb`/`io_mb`/`pids_max` DB values, don't just assume.
- [ ] `GET /healthz` → 200, `GET /api/v1/whoami` (authenticated) resolves
      identity correctly, one real admin action round-trips through the
      RPC socket successfully (e.g. the account list loads).
- [ ] If any domain uses "Remote" email routing mode: confirm mail still
      routes correctly through the renamed
      `/etc/postfix/boron_relay_domains` map — a real test message, not
      just `postfix reload` exiting 0.
- [ ] `grep -ril forgehost /etc/systemd/system /etc/cron.d
      /etc/logrotate.d` returns nothing.
- [ ] Once everything above is confirmed working (give it a real
      day of normal traffic before doing this): the old, now-orphaned
      `/opt/forgehost.pre-filebrowser`-style rollback snapshots or any
      other pre-existing `*forgehost*`-named backup directories can be
      cleaned up at the operator's discretion — this migration does not
      touch or delete any of those automatically.

## What this migration deliberately does NOT do

- **Does not touch MariaDB** (`forgehost_daemon`, `forgehost_mailro`,
  `forgehost_mail`) — see Decision 1 in `docs/REBRAND-INVENTORY.md`. If a
  fully-renamed database layer is ever wanted, that's a separate, carefully
  planned follow-up (MariaDB `RENAME USER` + a coordinated
  `secrets.env` credential update in the same atomic step, and a schema
  migration via `RENAME TABLE ... TO new_schema.*` per table since MariaDB
  has no single `RENAME DATABASE`) — not attempted here.
- **Does not rotate any secrets** — `SESSION_SECRET`, the MariaDB
  passwords, the PowerDNS/Cloudflare API keys, etc. all carry over
  unchanged in the moved `secrets.env`/`api-secrets.env` files (only their
  parent directory path changed, `/etc/forgehost` → `/etc/boron`).
- **Does not delete `/opt/forgehost.pre-filebrowser`** or any other
  historical rollback snapshot that predates this rebrand — those are left
  in place under their original names as-is; only the *live* paths this
  rebrand renamed are touched.
- **Does not reboot the host.** All cgroup slice recreation happens live,
  in-process, via the daemon's own reconciliation — no reboot is needed or
  expected.

## Rollback

If the migration fails partway (the script uses `set -euo pipefail`, so it
stops at the first real error rather than continuing in a half-migrated
state): the safest recovery is to restore from a filesystem-level snapshot
taken before running the script (recommended precondition — take one; the
script does not take it for you). Because most steps are plain `mv`
operations, most individual steps are also manually reversible by `mv`-ing
back (e.g. `mv /etc/boron /etc/forgehost`) and reinstalling the original
`forgehost-*` systemd unit files from git history
(`git show HEAD~1:deploy/forgehost-api.service`, etc.) — but a full,
verified snapshot restore is the recommended path over manual reversal,
since the per-account dynamic units add real complexity to reasoning about
partial state.
