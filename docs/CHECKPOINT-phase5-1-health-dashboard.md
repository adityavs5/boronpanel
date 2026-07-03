# Phase 5 feature 1: server health dashboard

## What was built

- `shared/models.py`'s new `HealthSnapshot` table -- one row per ~60s
  tick, host-wide (no account scoping), storing CPU%, 1/5/15m load, RAM,
  a JSON list of per-mount disk usage, and cumulative network
  rx/tx bytes.
- `daemon/health.py`: `get_live()` (instant psutil sample: CPU%, load,
  memory, per-mount disk usage, cumulative network counters, uptime),
  `take_snapshot()` (writes a `HealthSnapshot` row, prunes anything older
  than 48h), `get_history(hours)` (reads snapshots in-window, computes
  per-interval network in/out as the delta between consecutive rows).
- `scripts/health_snapshot.py` + `/etc/cron.d/forgehost-health` (every
  minute, root) -- same pattern as `usage_snapshot.py`/`forgehost-usage`.
- RPC ops `health.get`/`health.history`/`health.snapshot`, registered in
  `daemon/server.py`'s `OP_TABLE`, plus added to `REPORTING_EXECUTOR`'s op
  set (Security audit finding F7's dedicated small pool) since this is a
  polling/dashboard endpoint that samples CPU/disk, not different in kind
  from `usage.get`/`disktree.get`, which already got that treatment.
- `api/routers/health.py`: admin-only (`/api/v1/health`, `/api/v1/health/history`,
  `/ui/health`) -- host-wide infra telemetry is not a per-account
  resource, same admin-only posture as `backups.py`/`tokens.py`.
- `api/templates_ui/health.html`: live metrics table, a per-mount disk
  table, and 4 inline SVG polyline graphs (CPU%, memory%, net in, net
  out) rendered server-side from precomputed points -- **no client-side
  JS charting library**, deliberately: the security audit's CSP
  (`script-src 'none'`, `docs/AUDIT-FINDINGS.md` F10) assumes zero
  `<script>` tags anywhere in this app, confirmed true by grep at the
  time it was written. Auto-refresh is a plain
  `<meta http-equiv="refresh" content="10">` for the same reason --
  ARCHITECTURE.md's stated frontend stack names htmx, but no template
  anywhere actually uses it (grep-confirmed before choosing this
  approach), so introducing the first bit of client JS in the entire
  project -- and having to loosen the just-hardened CSP to allow it --
  was out of scope for "add a dashboard feature." Meta-refresh + inline
  SVG gets the goal's literal requirements (10s auto-refresh, 24h graphs)
  without either change.
- Nav link added to `base.html` ("Health", admin-only).

## Real bugs / decisions found by live testing

- None -- this is a pure read-only reporting feature (get_live samples
  psutil, get_history reads its own table), so there was no state machine
  or system-mutation edge case to get wrong the way e.g. the disk-tree or
  cgroups features had. The one design decision worth recording is the
  no-client-JS choice above, made explicitly to avoid quietly
  reintroducing a CSP gap the security audit had just closed.

## Live verification

- `GET /api/v1/health` via a real HTTPS request (admin session cookie,
  created+revoked directly around the test, never printed to any log):
  returned `mem_total=4106289152`, matching `free -b`'s total exactly;
  disk total `52721094656` matched `df -B1 /` exactly; disk used and
  mem used were within ~40MB/16KB of independent `free`/`df` runs taken
  moments later -- expected sampling-order variance, not a discrepancy
  (same tolerance Phase 2's own usage-figures verification accepted).
  `load1`/uptime differed between the two tools by the amount of wall-clock
  time that passed between taking each measurement -- both are
  continuously-changing values by nature, confirmed reading the same
  underlying kernel source (`/proc/loadavg`, `/proc/uptime`) via psutil.
- Unauthenticated `GET /api/v1/health` -> `401`.
- The real system cron (`/etc/cron.d/forgehost-health`) fired on its own
  during this verification window (not manually triggered) and appended
  real rows to `health_snapshots`, confirmed by querying the live DB
  directly -- the async/background half of this feature works
  unattended, not just when invoked interactively.

## What's untested

- A real 24h+ window of accumulated snapshots (verified the schema/query
  logic with a handful of live rows plus the full pytest suite's
  synthetic-timestamp cases; did not wait 24 real hours for a full
  graph's worth of history).
- Multi-NIC / multi-block-device hosts: this VM has one network
  interface's counters aggregated by `psutil.net_io_counters()`'s default
  (all-NICs) mode and one primary disk (`/dev/vda1`) -- correct behavior
  on a host with several physically distinct disks or NICs the operator
  wants broken out individually isn't exercised here.
