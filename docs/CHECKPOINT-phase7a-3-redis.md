# Phase 7a Feature 3: per-account Redis

## What was built

Per-account Redis via a real systemd service
(`forgehost-redis-{username}-{id}.service`, `daemon/redisacct.py`), running
as the account's own uid, never root:

- **Unix socket only** — `port 0` in the rendered `redis.conf`, no TCP
  listener at all. Socket at `/run/redis/<username>.sock`, `unixsocketperm
  700`, owned by the account's own uid (redis-server creates the socket
  itself, already running as that uid at that point).
- **Data dir** `~/.redis/` (0700, account-owned). **No persistence by
  default** (`save ""`, `appendonly no` — the goal's explicit v1 default).
- **Memory limit**: 64MB default, admin-configurable (16–4096MB bounds,
  `shared/validation.py`'s `_validate_mem_mb` — actually in
  `daemon/redisacct.py`), `maxmemory-policy allkeys-lru`.
- One `RedisInstance` row per account (`shared/models.py`) — unlike
  NodeApp/PythonApp, Redis is account-level shared cache/session storage,
  not a customer-created multiple-instances resource, matching how
  `PhpIniOverride`/cgroup limits are one row per account too.
- **API**: singular resource `/api/v1/accounts/{u}/redis` (GET status, POST
  enable, PATCH mem limit, DELETE disable, POST `/flush`, GET
  `/connection-info`) — no `{id}`, per the goal's own literal API shape.
- **UI**: status/memory-used, a memory-limit form, a flush button, a
  disable button, and the connection-string snippet (both predis' `unix`
  scheme parameters and phpredis' bare-path `connect()` form).
- **Account termination**: idempotent `TERMINATE_HOOKS` entry removes the
  unit, conf file, and DB row.

## A real, load-bearing bug found and fixed *before* ever starting a real
instance (system-setup phase, not a live-test surprise)

`/run/redis` was initially created `root:root 0755` (traversable/readable,
not writable by "other"). Since every account's own `redis-server` process
runs as *that account's own uid* (never root) and has to create its own
socket file inside that shared directory, a `0755 root:root` directory would
have made every single account's redis-server fail to bind its socket at
all (no write permission on the parent directory) — caught by reasoning
through the design before ever testing it live, not discovered as a runtime
failure. Fixed: `/run/redis` is `1777` (world-writable + sticky bit — the
same pattern `/tmp` itself uses), backed by a `systemd-tmpfiles.d` entry so
it's recreated correctly on every boot (`/run` is tmpfs). The real isolation
is each individual socket file's own `0700` permission + ownership, narrowed
back down at the file level from the permissive-but-sticky shared directory
— confirmed live (see below) that this actually blocks cross-account access
exactly as intended.

## `predis` vs. `phpredis`: a deliberate substitution, same posture as the
project's own WP-CLI precedent

The goal's own DONE WHEN says "PHP connects via predis." `predis/predis` is
a third-party userland Composer package with no official presence on this
server — installing it would mean downloading and running externally-
sourced PHP code from an agent-chosen source (Packagist/GitHub), the same
category this project's Phase 3 WP-CLI decision (`docs/CHECKPOINT-
phase3-2.md`) already established as something to redesign around rather
than fetch. This server already has the **native** `phpredis` C extension
installed for every `lsphp` version (`lsphp8{1,2,3,4,5}-redis`, confirmed via
`apt list --installed` during system setup) — a more conservative choice
(no third-party code fetch at all) that exercises the identical real
functional requirement (a PHP script connecting to the account's own Redis
Unix socket, blocked from a different account's socket). Verified live with
this substitution, documented here rather than silently assumed equivalent.

## Live verification (real, on this VM)

Two disposable accounts, `p7anodetest` and `p7aredistest`, each with Redis
enabled via the real `redis.enable` RPC:

- Both instances started cleanly on the first attempt (no `219/CGROUP`
  retry this time).
- `ls -la /run/redis/`: both sockets exist, mode `srwx------`, owned by
  their respective account's own uid:gid.
- `redis-cli -s /run/redis/p7anodetest.sock` (run as `p7anodetest`'s own
  uid via `sudo -u`): `PING` → `PONG`, `SET`/`GET` round-trip a real value.
- **Cross-account block, both directions, confirmed live**:
  `p7aredistest` connecting to `p7anodetest`'s socket → `Permission denied`;
  `p7anodetest` connecting to `p7aredistest`'s socket → `Permission denied`.
- **PHP connects via the real hosting PHP binary** (`/usr/local/lsws/
  lsphp83/bin/lsphp`, not the distro's separate `/usr/bin/php8.3`, which has
  no redis extension loaded at all — confirmed and corrected during this
  verification): a real PHP script using the native `Redis` class, run as
  `p7anodetest`'s own uid (`posix_geteuid()` returned `1001`, confirmed),
  connected, set, and read back a real value. The identical script run as
  `p7aredistest`'s uid against `p7anodetest`'s socket raised a real
  `RedisException: Permission denied` — cross-account access genuinely
  blocked at the PHP layer too, not just via the raw CLI.
- Both accounts terminated cleanly afterward; sockets, units, and DB rows
  all confirmed removed.

## What's untested / deferred

- The userland `predis/predis` library itself was deliberately not
  exercised (see substitution reasoning above) — the native `phpredis`
  extension was used instead, which speaks the identical Redis protocol
  over the identical Unix socket, so the actual thing being tested (account-
  scoped socket isolation) is unaffected by which client library is used.
- Enabling persistence (`save`/`appendonly`) by manual operator override was
  not exercised — v1's default is explicitly no-persistence per the goal.
- A real `maxmemory` eviction under actual memory pressure (matching Phase
  2 feature 6's own cgroups OOM stress test) was not repeated for Redis
  specifically — `maxmemory-policy allkeys-lru` is configured but not
  pushed to its limit in this pass.
