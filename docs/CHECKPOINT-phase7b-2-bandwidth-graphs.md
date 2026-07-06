# Checkpoint: Phase 7b Feature 2 — Bandwidth graphs

## What was built

Per-account bandwidth charts (daily/weekly/monthly) plus top-5-domains-by-
bandwidth and an admin cross-account ranking, built entirely from data
Phase 2 feature 5 already collects (`daemon/usage.py`'s OLS-access-log
parse) — no new collection.

- `daemon/usage.py`'s `refresh_bandwidth()` extended to also populate a
  new `BandwidthDailyDomain` table (account, domain, date, bytes) from the
  exact same log-parse pass that already fills the pre-existing
  account-level `BandwidthDaily` table — one log read, two granularities.
- `get_bandwidth_report(account, period)`: buckets `BandwidthDaily` rows
  into daily (30-day lookback)/weekly (12-week, ISO week keys)/monthly
  (12-month) buckets, plus the top 5 domains by summed bandwidth over the
  same window.
- `get_bandwidth_ranking(period)`: every account ranked by total bandwidth
  in the period, admin-only.
- **API**: `GET /api/v1/accounts/{u}/bandwidth?period=daily|weekly|monthly`
  (matches the goal's literal spec), `GET /api/v1/admin/bandwidth/ranking`.
- **UI**: `/ui/accounts/{u}/bandwidth` (bar chart + top-domains table),
  `/ui/admin/bandwidth/ranking`, both linked from account detail / admin
  nav respectively.

## A deliberate, disclosed substitution: no Chart.js

The goal's own text names Chart.js as "already available." **Checked
directly against this repo's `static/` directory before writing any
chart code, and that's not true** — only `static/forgehost.css` exists,
no vendored JS at all. This project's CSP (`api/main.py`) is
`script-src 'none'` (Security audit finding F10, Phase 5), and the health
dashboard (Phase 5 feature 1) already solved the *identical* "render a
chart under this CSP" problem with plain, server-rendered inline SVG
(`_svg_polyline`) rather than a client-side charting library. This
feature follows that exact precedent — a new `_svg_bars` helper in
`api/routers/bandwidth.py`, same shape, rects instead of a polyline —
rather than being the first feature in the project's history to either
loosen CSP or introduce a vendored JS dependency for a chart nobody
actually shipped a library for. Same category as this project's own
WP-CLI/`predis` substitutions: a goal-text assumption checked against
reality and found false, then built against what's actually true,
disclosed rather than silently worked around.

## Tests

27 new tests across `tests/test_usage.py` (bandwidth-specific additions)
and `tests/test_handlers_usage.py`: per-domain breakdown populated
alongside (not instead of) the existing account-level total, upsert
(not duplicate) on re-run, daily/weekly/monthly bucket-label computation,
top-5 capping and correct sort order, admin ranking sort order and its
period-window exclusion of old data, and the `handlers_usage.py` wrapper
layer's username-lookup/default-period behavior.

## What's honestly still open

- **"Numbers match OLS log totals independently"** (the goal's own Done-
  When criterion) was not independently re-verified against a real,
  currently-accumulating access log on the live server — the *parsing*
  logic itself is unchanged from Phase 2 feature 5 (already live-verified
  then) and is exercised again here by unit tests against synthetic log
  lines, but this pass's own new bucketing/ranking code was not checked
  against a real multi-day log on this actual VM. Blocked by the same
  live-deployment restriction documented in `CHECKPOINT-phase7b-1`
  (another Claude Code session confirmed concurrently active against this
  server when this phase was built).
- The SVG bar chart renders correctly against synthetic data in a
  browser was not visually confirmed (no live deploy this pass) — the
  geometry math (`_svg_bars`) has a direct unit-testable contract (`x`/
  `y`/`width`/`height` proportional to `bytes_served`/`max`) that IS
  covered, but rendering fidelity in an actual browser is not.
- Weekly bucketing uses Python's `isocalendar()` (ISO 8601 week numbering,
  weeks starting Monday) — not cross-checked against how an operator might
  intuitively expect "this week" to be defined if their own convention
  differs (e.g. Sunday-start weeks).

## Live verification result (2026-07-06): BLOCKED (environment, not a feature defect)
Ran `verify_phase7b_live.py bandwidth`. The reconciliation logic executed
correctly (report total 0 == independent access-log grep total 0), but the
5 test HTTP requests never reached a serving vhost because OLS could not
bring the test vhost up (host's SIGUSR1 reload crash — see STATUS.md).
Comparison logic is sound; a non-zero total needs live traffic to a real
vhost. Re-run once OLS reload is fixed.

## UPDATE (2026-07-06, 2nd live run): PASS
Supersedes the BLOCKED note above. With the OLS reload defect fixed, the
vhost served real traffic: 5 real HTTP requests, `refresh_bandwidth` report
total 135 bytes == independent access-log grep total 135 bytes. Done-When met.
