# QA round 2 — Item 6: cron templates + human-readable descriptions

## What existed already

`daemon/cron.py` + `frontend/src/pages/customer/Cron.jsx` already had a
5-field schedule *builder* (not a raw-string-only form) plus a raw-override
field. No template/preset picker and no English description existed
anywhere — the UI's only "translation" of the composed cron string was
echoing it back verbatim.

## Fix

**Backend** (`daemon/cron.py`): new `describe_schedule(schedule) -> str`,
pure and side-effect-free. Recognizes: every minute, every N minutes
(`*/N`), hourly (on-the-hour or at a fixed minute), daily/weekly/monthly/
yearly at a fixed HH:MM, and croniter's `@nickname` forms
(`@daily`/`@hourly`/`@weekly`/`@monthly`/`@yearly`/`@annually`/`@midnight`).
Anything else (lists, ranges, multi-value fields) — deliberately falls back
to `"Custom schedule"` rather than guessing wrong. Wired into the dict
every one of `list_jobs`/`add_job`/`update_job` returns as a `description`
field — single source of truth, flows straight through
`daemon/handlers_cron.py` (passthrough) to the API with no other file
touched.

**Frontend** (`Cron.jsx`):
- Template picker (`SCHEDULE_TEMPLATES`): Every minute / Every 5, 15, 30
  minutes / Hourly / Daily / Weekly / Monthly / Custom — each sets the
  5-field builder directly (no raw-string round-trip), highlighted active
  when the current field values match a preset exactly. "Custom" just
  clears the raw override and leaves the fields as-is.
- The list table's Schedule column now shows the human-readable
  description (from the API's `description` field) above the raw cron
  string, satisfying "show human-readable description alongside raw cron
  syntax" for saved jobs.
- The create/edit dialog's live preview (before save, no round-trip yet)
  needed the same description text instantly as the user edits fields —
  added `describeScheduleLocally()`, a deliberate small JS mirror of the
  same rule set (this repo has no frontend test runner at all — Python
  pytest is the only tested surface per `ARCHITECTURE.md §12` — so the
  *authoritative*, tested version lives in Python; the JS copy is instant-
  UX-only and falls back to "Custom schedule" on anything it doesn't
  recognize, same as the backend).

## Tests

`tests/test_cron.py`: +18 (`TestDescribeSchedule` class covering every
matched shape + 3 fallback cases + all 5 nicknames) + 1 (`add_job` includes
`description` in its return). `python3 -m pytest tests/test_cron.py
tests/test_handlers_cron.py tests/test_api_cron_router.py -q` → 53 passed.

`npm run build` — clean.
