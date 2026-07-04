# Phase 5 feature 8: MySQL slow query viewer

## What was built

- `daemon/slowquery.py`: `get_status` (reads `slow_query_log`/
  `long_query_time`/`log_output` via `SHOW VARIABLES`),
  `bootstrap_slow_query_log` (enables it), `list_slow_queries` (reads
  `mysql.slow_log`, sortable by slowest, searchable by DB or query
  fragment).
- **Enabling the log needed a real design decision, resolved by testing,
  not assuming**: `SET GLOBAL slow_query_log = 'ON'` requires MariaDB's
  SUPER privilege. Tried it live first -- `forgehost_daemon` genuinely
  does not have SUPER (`Access denied; you need ... SUPER privilege`),
  confirmed via a real connection, not inferred from the grants table
  alone. Rather than requesting SUPER (a broad, admin-equivalent SQL
  grant this project has previously and deliberately declined to widen
  for `forgehost_daemon` -- `daemon/mariadb.py`'s `HOSTED_DB_PRIVILEGES`,
  CHECKPOINT-d.md), this feature writes a real MariaDB config file
  (`/etc/mysql/mariadb.conf.d/60-forgehost-slowlog.cnf`) and restarts
  `mariadb.service` -- the exact same "root-owned system config write +
  service restart" trust boundary this project already relies on for
  every other server-wide setting (fail2ban, ModSecurity, spamd), rather
  than a new one. The *reading* side needed no new grant at all --
  `forgehost_daemon`'s existing blanket `SELECT ON *.*` already covers
  `mysql.slow_log`, confirmed live.
- `bootstrap_slow_query_log` requires `confirm=true` (same disruptive-
  action bar Feature 2's service manager already set for
  stop/restart) and validates the change actually took effect after
  restarting -- if `slow_query_log` doesn't come back `ON`, it restores
  the previous config file content (or removes it, if none existed) and
  restarts once more, then raises, rather than silently leaving the
  server in a half-applied state.
- `log_output = TABLE` (not `FILE`) was a deliberate choice: MariaDB's
  own `mysql.slow_log` table (confirmed present and populated correctly
  after enabling) is trivially queryable with real `WHERE`/`ORDER BY`
  SQL, avoiding a second fragile text-log-parsing implementation
  (Feature 3/Feature 5/Feature 7 each already needed one of those, for
  tools with no equivalent structured-query alternative -- this one
  does, so it was used).
- RPC ops `slowquery.status/bootstrap/list`, registered in
  `daemon/server.py` (`status`/`list` added to the F7
  `REPORTING_EXECUTOR` pool).
- `api/routers/slowquery.py` (`/api/v1/mysql/slow-queries`,
  `/ui/slow-queries`, admin-only) + `slow_queries.html` (status +
  enable-with-confirm, search form, sortable table).

## Real bugs / decisions found by live testing

- The SUPER-privilege wall above -- found by actually trying `SET
  GLOBAL` with the daemon's real credentials before writing any
  feature code, not assumed from MariaDB's privilege docs.

## Live verification

- `GET /api/v1/mysql/slow-queries/status` before enabling ->
  `{"enabled": false, "long_query_time": 10.0, "log_output": "FILE",
  "config_managed": false}`, matching this server's real pre-existing
  MariaDB configuration exactly.
- **Goal's own DONE WHEN scenario, run for real**: `POST
  .../bootstrap` with `confirm: true` -> `200`,
  `{"enabled": true, "long_query_time": 1.0, "log_output": "TABLE",
  "config_managed": true}`; independently confirmed via a direct `mysql`
  CLI `SHOW VARIABLES` call (not through the API) that all three
  settings genuinely changed, and that `mariadb.service` was actually
  restarted (not just reported as such). A real manual slow query
  (`SELECT SLEEP(2), 'forgehost-slowquery-test' AS marker`) was then run
  directly against MariaDB (bypassing the panel entirely) and confirmed
  present in `mysql.slow_log` via a direct SQL query
  (`query_time = 00:00:02.001551`); `GET
  /api/v1/mysql/slow-queries?q=forgehost-slowquery-test` then returned
  that exact same row through the real feature's own code path, with
  `query_time_seconds` matching to the microsecond.
- Confirmed the hosted MariaDB instance was fully healthy after the
  restart this required: `SHOW DATABASES` still lists every real schema
  (`forgehost_mail`, `roundcube`, etc.) with no errors.
- Unauthenticated `GET /api/v1/mysql/slow-queries` -> `401`.

## What's untested

- Behavior against a genuinely large `mysql.slow_log` (thousands+ rows)
  -- the `LIMIT`/`ORDER BY` query is straightforward SQL with an obvious
  index-friendly shape, not benchmarked at that scale.
- Rolling back after a *partial* config write (e.g. process killed
  mid-write) -- the rollback path is exercised by two dedicated mocked
  tests (restart failure, setting-didn't-take-effect), not an actual
  interrupted write.
