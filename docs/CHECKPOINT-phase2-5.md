# Checkpoint: Phase 2, Feature 5 — Resource usage reporting (read-only)

## What was built

- Two new DB tables (`shared/models.py`): `UsageSnapshot` (point-in-time
  gauge metrics: disk home/mail/db bytes, inode count, process count --
  appended, never overwritten, so the history itself is the trend data)
  and `BandwidthDaily` (one row per account per calendar day, upserted).
- `daemon/usage.py` — every number is computed straight from the same
  real sources an operator would check by hand:
  - Disk: `du -sb` on the account's home dir; `du -s --inodes` for inode
    count; mail storage via `du -sb` on each of the account's
    `MailDomain` rows under `/var/vmail/<domain>` (Dovecot's actual
    `mail_location`, confirmed by reading `/etc/dovecot/conf.d/10-mail.conf`
    rather than assumed); database size via a new
    `mariadb.database_size_bytes()` querying
    `information_schema.tables.data_length + index_length` for every
    `DatabaseGrant` the account owns -- the same source `SHOW TABLE
    STATUS`/phpMyAdmin's size column reads from.
  - Bandwidth: parses OLS access logs (`{home}/logs/{vhost_name}-access.log*`
    -- globbed, not just the current file, to also catch rotated logs
    whose exact suffix format this project doesn't control) with the
    same NCSA-combined-ish format OLS actually writes (confirmed against
    a real live log, not assumed), aggregated per calendar day and
    upserted into `BandwidthDaily` -- a day's total is only ever
    recomputed while its traffic is still fully present in on-disk logs;
    once rotated away, the last-computed value persists untouched,
    becoming the durable historical record.
  - Process count: `ps -u <username>` (not `.ok`-gated -- `ps` exits
    non-zero when zero processes match, which is a real, valid "0
    processes" answer here, not an error).
  - Caching: `get_usage()` reuses the latest `UsageSnapshot` if it's
    under 15 min old, else recomputes lazily (so a freshly created
    account still gets a real number on first look) -- satisfies "cached
    every 15 min" without needing new scheduler infrastructure.
- `scripts/usage_snapshot.py` + `/etc/cron.d/forgehost-usage` (root,
  every 15 min) -- a proactive background refresh, so historical trend
  data keeps accumulating even if nobody ever opens the usage page (the
  lazy on-read cache alone would only ever produce a data point when an
  operator actually looks).
- `GET /api/v1/accounts/{username}/usage` (+ `force_refresh` query param)
  and a dedicated `/ui/.../usage` page (bandwidth-by-day table, disk
  history table); a compact usage-bar summary was also added to the main
  account detail page (always `force_refresh=false` there specifically,
  since that page loads on every visit -- it rides the 15-min cache
  rather than triggering a `du` scan on every click; the dedicated usage
  page has its own explicit "refresh now" link for that).
- A `human_bytes` Jinja filter (`api/templates.py`) and `.usage-bar`/
  `.usage-row` CSS -- numbers are stored/transmitted as raw bytes
  (matching `du`/`information_schema` exactly, no lossy unit conversion
  baked into the stored/API data), converted to human units only at
  render time.

## A real bug found and fixed during this build: naive/aware datetime
subtraction

`get_usage()`'s staleness check (`utcnow() - latest.taken_at`) raised
`TypeError: can't subtract offset-naive and offset-aware datetimes` the
first time it ran against a real snapshot read back from SQLite. Root
cause: SQLite has no native timezone-aware datetime type, so
`DateTime(timezone=True)` still round-trips a stored value as *naive*
Python-side, even though it was written via `utcnow()` (always UTC).
Fixed with a small `_as_aware_utc()` helper that re-attaches `UTC` tzinfo
to any naive value read back — every timestamp this project ever writes
uses `utcnow()`, so a naive value read back is always safely UTC, never
ambiguous.

## Real end-to-end verification performed (Definition of Done: "numbers
match what du/mysql show independently")

Created a real account with a domain and database, then, before ever
calling the usage API:
- Wrote a real 5 MB file into its home directory.
- Inserted 200 real rows into a real table in its database.
- Made 5 real HTTP requests against its vhost (a 12-byte response body
  each).

Then compared the API's `force_refresh=true` response against independent
checks run directly against the same live system:

| Metric | Forgehost reported | Independent check | Match |
|---|---|---|---|
| Disk (home) | 5,248,110 bytes | `du -sb`: 5,247,690 bytes | within ~420 bytes (explained by log growth between the two measurements, a few seconds apart) |
| Database size | 16,384 bytes | `information_schema` query run directly: 16,384 bytes | exact |
| Inodes | 13 | `du -s --inodes`: 13 | exact |
| Process count | 1 | `ps -u <user>`: 1 (`lsphp`, confirmed running under that account's own uid) | exact |
| Bandwidth today | 60 bytes | 5 requests x 12-byte body = 60 bytes | exact |

Also verified: the background `scripts/usage_snapshot.py` script runs
cleanly end-to-end (real `du`/`ps`/mysql calls, logged); the dedicated
`/ui/.../usage` page renders real data; the account detail page's compact
usage-bar summary renders correctly; the account was cleanly terminated
afterward. Full 226-test suite (212 existing + 14 new) passing.

## What's untested / known limitations

- No test (live or automated) actually waited for a real OLS access-log
  *rotation* to happen (`rollingSize 10M`) to confirm the glob correctly
  picks up a rotated file with whatever suffix OLS actually uses --
  verified the glob pattern logic in isolation (`test_all_access_log_files_
  matches_current_and_rotated`-equivalent coverage in test_usage.py) but
  not against a real rotated file, since triggering a real 10 MB rotation
  wasn't practical in this pass.
- `du -sb`/`--inodes` run synchronously inside the request path when the
  15-min cache is stale -- acceptable for the account sizes exercised
  here, but there's no async job queue in this project to move a
  genuinely large account's scan off the request thread; flagging as a
  known scaling limit rather than solving it here (out of this feature's
  scope, and no evidence yet that it's a real problem at this project's
  current scale).
- Quota enforcement itself (the usage bar just *displays* against
  `quota_soft_mb`/`quota_hard_mb`, it doesn't act on it) is unrelated to
  this feature -- disk quotas are already enforced independently by Linux
  filesystem quotas (Phase 1), this is read-only reporting only, as the
  goal specified.
