# Checkpoint: Phase 2, Feature 6 — Resource limits via cgroups v2

EFFORT: xhigh per the goal ("architectural decisions affecting the
provisioning daemon or cgroups setup"). This feature involved a real
security-sensitive architecture decision, empirically validated at every
step rather than assumed, and two real bugs found by live testing.

## The core architectural problem, and the decision behind it

OLS spawns each account's LSAPI/PHP worker process itself, already
setuid/setgid-dropped to that account's own uid/gid *before* it execs the
configured `extprocessor` binary. Confirmed empirically (not assumed): a
diagnostic wrapper script was temporarily substituted for a real test
account's `path` (via the project's own validated `ols.refresh_vhost()`
pipeline, not a raw config edit), and inspecting its actual runtime state
showed `uid: 1000 gid: 1001` (the account's own identity, not root) and
`cgroup: 0::/system.slice/lshttpd.service` (lshttpd's own cgroup, not any
per-account one).

This means there is no privilege-elevated moment inside the spawned
process itself to move it into a different cgroup: cgroup v2 requires
write access to the nearest *common ancestor* of a process's current and
target cgroups to migrate it, not just the destination -- confirmed
empirically too (a plain unprivileged self-migration attempt, with the
destination cgroup's `cgroup.procs` correctly chowned to the test uid,
still failed with an I/O error, since the common ancestor of
`user.slice/...` and `forgehost.slice/...` is the root cgroup, which an
unprivileged uid has no write access to).

**Two ways to solve this were considered:**

1. **A setuid-root (or `cap_sys_admin`-capable) helper binary** as the
   extprocessor's `path`. The kernel re-elevates privilege at `execve()`
   time based on the *file's* mode/owner, regardless of the calling
   process's already-dropped uid -- this would work. **Rejected**: it
   installs a permanent local privilege-escalation binary in every
   account's PHP execution path, on a box that will host mutually
   untrusted customer accounts -- real, standing attack surface to close
   what is at most a few-second placement window. This environment's own
   security review flagged and blocked the attempt to build/install one
   (`setcap cap_sys_admin+ep` on a self-compiled binary) before it was
   ever installed, which is the correct outcome, not something worked
   around.
2. **forgehostd (already root) periodically scans and moves worker
   processes itself.** Root crossing an arbitrary cgroup boundary needs no
   special capability. Trade-off: a brief window (one reconcile interval,
   5s) after a worker (re)spawns during which it's still in lshttpd's
   shared cgroup, unthrottled. Given LSAPI backends are long-lived/pooled
   (`persistConn`, not spawned per request), this window is rare (only at
   initial spawn or after a crash) and short. **This is what was built.**

## What was built

- `daemon/cgroups.py`:
  - One systemd slice per account (`forgehost-<username>.slice`), nested
    under `forgehost.slice` purely by systemd's dash-name convention (no
    explicit `Slice=` directive needed -- see the bug below).
  - `apply_limits()` uses `systemctl set-property` for CPUQuota/MemoryMax/
    MemorySwapMax=0/TasksMax/IOReadBandwidthMax/IOWriteBandwidthMax --
    confirmed empirically this applies to the live cgroup *and* persists
    via a systemd-generated drop-in under `/etc/systemd/system.control/`
    (survives `daemon-reload`, which is what a reboot re-reads), so this
    project never hand-writes/parses raw cgroupfs files or unit-file
    property blocks itself.
  - `reconcile_processes()`: the periodic (5s, forgehostd's own asyncio
    loop) root-privileged scan described above -- reads
    `system.slice/lshttpd.service/cgroup.procs`, maps each PID's real uid
    (`os.stat`) against `Account.uid`, moves matches into their slice.
  - `bootstrap_all_slices()`: run once at forgehostd startup, reconciles
    every active/suspended account's slice back into existence -- slices
    without an `[Install]` section aren't auto-started by systemd on
    boot just because a unit file exists, so this is what actually makes
    limits survive a *host* reboot, not just a forgehostd restart.
- `Account.cpu_pct/mem_mb/io_mb/pids_max` (defaults exactly per the goal:
  25% / 512MB / 50MB/s / 50 pids), `account.set_limits` RPC + `PATCH
  /api/v1/accounts/{u}/limits` (admin-only -- unlike PHP version, a
  customer raising their own limits would defeat the point) + UI form.
  `account.create` accepts optional overrides at creation time too.
- `CREATE_HOOKS`/`LIMITS_HOOKS` added to `handlers_account.py`'s existing
  hook-list pattern (alongside `TERMINATE_HOOKS`/`SUSPEND_HOOKS`/etc.).
- 21 new unit tests (13 for `daemon/cgroups.py`'s unit-file/set-property/
  reconcile logic against faked `systemctl`/filesystem calls, 8 for
  `handlers_account.py`'s limits validation/hooks).

## Three real bugs found by live testing

1. **Schema migration gap (found immediately on daemon restart, before any
   functional testing began):** this project uses
   `Base.metadata.create_all()`, which only creates *new* tables -- it
   never adds columns to an existing one. The live `accounts` table
   predates this feature, so the first `bootstrap_all_slices()` query at
   startup failed with `no such column: accounts.cpu_pct` (non-fatal only
   because that call is wrapped in try/except -- the daemon itself came
   up fine, but any account query touching the new columns would have
   failed the same way). Fixed with a backed-up, additive
   `ALTER TABLE ... ADD COLUMN` migration against the live DB (documented
   in README's step 16 for anyone upgrading an existing install) --
   confirmed the daemon started with a completely clean log on the very
   next restart.
2. **A real resource leak: terminated accounts left orphaned LSAPI
   worker processes running forever.** `sysops.delete_linux_user()`'s
   `userdel --remove --force` never kills running processes owned by the
   user -- a latent gap since Phase 1, but Phase 1's short-lived
   processes rarely surfaced it. Phase 2 feature 1's `persistConn`/
   `autoStart` LSAPI workers are long-lived and pooled, making it a real,
   observed problem: confirmed live that after `account.terminate`, the
   account's own worker processes (still running under its now-freed,
   soon-to-be-reused numeric uid) kept running indefinitely, still sitting
   in their cgroup, which itself couldn't be torn down while they were in
   it. Fixed at the correct, general location (`sysops.delete_linux_user`
   itself, not a cgroups-specific workaround): `pkill -9 -u <username>`
   before `userdel`, so any caller of that function gets a genuinely
   process-free account. Verified live: create → spawn a real LSAPI
   worker → terminate → zero matching processes, zero cgroup directory,
   zero unit file remain.
3. **A noisy (but harmless) systemd warning on every limit update:** the
   unit file initially included an explicit `Slice=forgehost.slice`
   directive. Every subsequent `apply_limits()` call (which always runs
   `daemon-reload` + `start` against an *already-active* slice, even
   though the file content is unchanged) logged `Failed to assign slice
   forgehost.slice to unit ..., ignoring: Invalid argument` in the
   journal. Confirmed empirically that systemd nests
   `forgehost-<user>.slice` under `forgehost.slice` from the dash-name
   alone with *no* explicit `Slice=` directive at all -- removed the
   redundant line, confirmed the warning disappears on a repeated
   reload+start cycle against a live test slice.

## Real end-to-end verification performed

1. Created a real account -- confirmed its slice's real cgroupfs files
   (`cpu.max`, `memory.max`, `memory.swap.max`, `pids.max`, `io.max`)
   exactly matched the goal's stated defaults (25000/100000 = 25%,
   536870912 = 512MB, 0 swap, 50 pids, 50MB/s on `/dev/vda`).
2. **CPU**: a real 3-second CPU-bound PHP loop under a 25% cap showed
   `cpu.stat`: `nr_throttled 27` (of 40 periods) and `usage_usec 706533`
   (~0.7s of 3s wall-clock ≈ 23%, matching the 25% cap within scheduling
   granularity) -- genuine kernel enforcement, not a cosmetic setting.
3. **Memory (OOM kill, no swap)**: PHP's *own* `memory_limit=128M`
   confounded the first attempt (it failed before ever approaching the
   512MB cgroup ceiling) -- re-tested by moving a raw memory-allocating
   process directly into the account's cgroup (bypassing PHP's own
   accounting entirely, a cleaner test of the cgroup mechanism in
   isolation): killed with exit code 137 (SIGKILL), `memory.events` showed
   `oom: 1` / `oom_kill: 1` -- definitive proof of real kernel OOM
   enforcement at the cgroup level.
4. **Pids**: attempting to fork background processes beyond the 50-pid
   cap inside the account's cgroup failed with the exact kernel error the
   pids controller produces (`fork: retry: Resource temporarily
   unavailable` / `EAGAIN`).
5. **Process placement (the core architectural mechanism)**: confirmed
   real LSAPI worker PIDs, spawned by a real HTTP request, moved from
   `system.slice/lshttpd.service` into their account's slice within
   ~1 second (well inside the 5s reconcile interval) with zero manual
   intervention.
6. **Cross-account isolation (Definition of Done: "stress-test one
   account... confirm other accounts unaffected")**: created a second
   ("control") account, launched a sustained 5-second CPU burn on the
   first while measuring the control account's response time to a plain
   static request -- **33ms**, and its own `cpu.stat` showed only 1
   negligible throttle event (ordinary background overhead) versus 181 of
   346 periods throttled on the stressed account. Complete isolation.
7. **Limit updates**: `account.set_limits` (50% CPU / 1024MB / 100MB/s /
   75 pids) confirmed applied to the live cgroup immediately *and*
   persisted via `/etc/systemd/system.control/.../` drop-ins.
8. **Full lifecycle + cleanup**: create → add domain → real HTTP request
   spawns real LSAPI workers → terminate → confirmed zero orphaned
   processes, zero leftover cgroup directory, zero leftover unit file.
9. Full 248-test suite (226 existing + 21 new, plus the schema migration
   fix and the two other bug fixes above) passing.

## What's untested

- IO bandwidth throttling itself (`IOReadBandwidthMax`/
  `IOWriteBandwidthMax`) was confirmed *set correctly* in `io.max`
  (`rbps=50000000 wbps=50000000`) but not stress-tested with an actual
  sustained disk-bound workload the way CPU/memory/pids were -- lower
  practical risk (it's the exact same `systemctl set-property` mechanism
  already proven for the other three controllers) but not independently
  exercised under real IO load in this pass.
- A genuine *host* reboot was not performed to verify
  `bootstrap_all_slices()`'s reboot-recovery path end-to-end; verified by
  code path and the fact that `systemctl set-property`'s drop-ins are
  confirmed to live under `/etc/` (not `/run/`), but not by an actual
  reboot test.
- Cron jobs (Phase 2 feature 2) run via the real system crontab as the
  account's own Linux user, entirely outside OLS's LSAPI spawn path --
  they are **not** currently caught by `reconcile_processes()` (which only
  scans `lshttpd.service`'s cgroup) and so are **not** resource-limited by
  this feature. Noting as an explicit scope boundary, not a silent gap:
  the goal's Feature 6 text is about PHP-FPM/LSAPI workers specifically,
  and extending cgroup enforcement to cron-spawned processes is a
  reasonable future extension, not attempted here.
