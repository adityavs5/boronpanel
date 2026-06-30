# Checkpoint: Phase a — provisioning daemon + account CRUD

## What was built

- `shared/` — config loader (`/etc/forgehost/forgehost.toml` + `secrets.env`),
  SQLAlchemy models for the full control-plane schema (accounts, domains,
  DNS zones, database grants, mail domains/users cache, panel users,
  sessions, API tokens, audit log — later phases fill in the tables beyond
  `accounts`), input validation (`shared/validation.py`), and the RPC framing
  protocol (`shared/rpc.py`).
- `daemon/` — `forgehostd`, the root-only provisioning daemon:
  - `procutil.py`: the single chokepoint for shelling out, argument-list
    only, never `shell=True` (CyberPanel CVE countermeasure, ARCHITECTURE.md §9).
  - `sysops.py`: Linux user lifecycle (`useradd`/`usermod -L/-U`/`userdel`/
    `chpasswd`/`setquota`).
  - `configtx.py`: the shared validate→backup→apply→reload→verify→rollback
    engine (ARCHITECTURE.md §7) — built now, exercised by unit tests now,
    will be reused unchanged by Phase b (OLS) and Phase e (Postfix/Dovecot).
  - `handlers_account.py`: create/get/list/suspend/unsuspend/terminate, with
    `TERMINATE_HOOKS`/`SUSPEND_HOOKS`/`UNSUSPEND_HOOKS` extension points later
    phases register into rather than editing this file.
  - `audit.py` + `server.py`: asyncio Unix-socket RPC server, audits every
    call (success or failure) to the `audit_log` table.
- `forgehost-provisiond.service` systemd unit, installed and running as root,
  socket at `/run/forgehost/provisiond.sock` (mode 0660, group
  `forgehost-api`, no TCP listener).
- 59 pytest unit tests (`tests/`) covering validation, RPC framing, the
  configtx state machine (including rollback paths), and account-handler
  logic (sysops calls mocked, real SQLite via a per-test isolated DB
  fixture). `pytest.ini` sets `pythonpath = .` so tests run without an
  install step.

## Real end-to-end verification performed (not just unit tests)

Ran against this actual VM, through the real Unix socket, with the real
daemon running as root under systemd:

1. `account.create` → real `useradd`, home dir `/home/testacct1` created
   mode 750, owner `testacct1:testacct1`, real 5GB/6GB ext4 quota row
   (`repquota` confirmed).
2. `account.suspend` → `passwd -S testacct1` showed `L` (locked).
3. `account.unsuspend` → `passwd -S testacct1` showed `P` (active) again.
4. `account.terminate` → `id testacct1` → no such user; `/home/testacct1`
   gone; quota row gone; no orphaned state.
5. `audit_log` table has one row per call, all `result=ok`.

## What's untested

- Concurrent/racing RPC calls (e.g. two simultaneous `account.create` calls
  for the same username) — SQLite's unique constraint will reject the loser,
  but the resulting partial Linux-user-created-but-DB-row-rejected state
  isn't handled (no compensating cleanup). Low risk for a single-admin v1
  panel; worth a follow-up if multi-admin concurrent use is expected.
- `configtx.py`'s rollback path has only been unit-tested with fake
  validate/reload/verify callables, not yet against a real subsystem (that
  happens in Phase b, next).
- No load/stress testing of the asyncio socket server under many concurrent
  connections.

## Decisions made without stopping (per project goal's autonomy instruction)

- Enabled ext4 `usrquota,grpquota` on the root filesystem live (fstab edit +
  `mount -o remount /` + `quotacheck`/`quotaon`) before any account code ran,
  since quota enforcement requires this and it's cleaner to do once,
  deliberately, than lazily on first account creation. Verified via
  `findmnt` and `repquota` — reversible by removing the fstab options and
  remounting again, nothing destructive was done to existing data.
- Account Linux users get `/usr/sbin/nologin` as their shell (no SSH in v1,
  matches locked scope — SSH access was never in the v1 feature list).
- Initial account password is system-generated (20-char random) when the
  caller doesn't supply one, returned once in the `account.create` response
  body (`initial_password`) — never stored in plaintext, never logged (the
  audit log redacts any param key containing "password").

## What to review first on wake-up

- `daemon/handlers_account.py`'s terminate ordering — currently only tears
  down the Linux user/quota since Phases b–e (vhost/DNS/DB/mail) don't exist
  yet; confirm the hook-registration pattern actually gets used cleanly once
  those phases land rather than needing a rewrite.
