# CHECKPOINT phase8-10 — Process manager

**Goal:** live ps by account uid (PID/command/CPU%/memory/runtime); admin kills
any, customer kills own; auto-refresh 10s; strictly scoped.
API: `GET /accounts/{u}/processes`, `DELETE /accounts/{u}/processes/{pid}`.

## What was built

- **Daemon** `daemon/procmanager.py` (ops `processes.list/kill`, list is a
  REPORTING_OP): uses psutil to list processes whose **real uid == the account's
  uid**, with a ~0.12s CPU sample so CPU% is meaningful; returns
  PID/command/cpu_pct/memory_bytes/runtime_seconds.
  - **Strict scoping**: `kill_process` re-confirms the target pid is owned by the
    account's exact uid before signalling (SIGTERM → SIGKILL fallback); a
    system uid (<1000) is refused outright.
- **API** `api/routers/processes.py`: both endpoints use `require_account_access`
  (admin → any account, customer → own); the daemon uid-scoping is the real
  boundary. So "admin kills any / customer kills own" falls out of the existing
  RBAC + uid scoping.
- **Frontend** `Processes.jsx`: customer page (nav `/processes`) + admin
  AccountDetail **Processes** tab (via `embedded`), **auto-refreshing every 10s**,
  sortable, kill-with-confirm.

## LIVE verification (this server) — Done-When met

A `sleep 300` was started as a disposable account user; the manager listed it
with real PID/command/memory/runtime, killed it (**gone from the list**), and —
critically — **refused to kill root's pid 1** ("not owned by account"),
confirming strict scoping.

## Tests

`tests/test_procmanager.py` — 6 tests: uid guards (missing account, system-uid
refusal), kill refuses a process owned by another uid, and kills an owned one.
All green.
