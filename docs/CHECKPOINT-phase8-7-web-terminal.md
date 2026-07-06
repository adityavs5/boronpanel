# CHECKPOINT phase8-7 — Web terminal (xterm.js + WebSocket → SSH)

**Goal:** xterm.js + WebSocket → SSH as the account user (never root); ephemeral
keypair per session injected into authorized_keys at open, removed at
close/timeout; private key never stored; 30-min idle timeout; valid panel
session + ownership; max 3 concurrent per account; audit-logged.
WS: `/ws/accounts/{u}/terminal`. **Effort: xhigh.**

## Architecture

- **Daemon (root)** `daemon/terminal.py` — `terminal.open/close/list`:
  - `open_session`: validates the account is active, generates an **ephemeral
    Ed25519 keypair in memory**, injects the PUBLIC key into
    `~/.ssh/authorized_keys` (marker `forgehost-terminal-<sid>-<epoch>`,
    no-forwarding options, pty allowed), and **returns the PRIVATE key over the
    RPC socket**. The private key is never written to disk. Ensures a login
    shell (like the SSH-keys feature). `fcntl` lock serializes authorized_keys
    edits.
  - **Max 3 concurrent**: counted from the live authorized_keys markers (survives
    an API restart). **Stale markers** (>12h — a session whose close never ran)
    are reaped on the next open, bounding key leakage.
  - `close_session`: removes the session's marker line; reverts the shell to
    nologin only if the account then has zero keys (symmetric with sshkeys).
- **API (unprivileged)** `api/routers/terminal.py` — WebSocket
  `/ws/accounts/{u}/terminal`:
  - Authenticates via the same session cookie (`_identity_from_session_cookie`)
    and ownership check (admin any, customer own).
  - Calls `terminal.open`, then connects to sshd on **127.0.0.1 as the account
    user** with the in-memory private key (paramiko, key loaded from a
    `StringIO`, never a file), opens a pty, and pumps bytes.
  - JSON client protocol (`{"t":"i"|"r",...}`), raw output frames. **30-min idle
    timeout** on client input. On any exit → `terminal.close` (removes the key).
- **Frontend** `Terminal.jsx` (nav + `/terminal` route): xterm.js + fit addon,
  resize forwarding, reconnect, status indicator.
- **Audit**: `terminal.open`/`terminal.close` are audit-logged by `dispatch()`.
- **Dependency**: `paramiko==5.0.0` added to requirements.txt.

## LIVE end-to-end verification (this server's real sshd)

A disposable Linux user was created, an ephemeral key injected via the real
`terminal` helpers, and paramiko connected over real SSH:
- `id -un` → the account user; `id -u` → **1002 (NOT root)**.
- `sudo -n true` → "a password is required", rc=1 (**no sudo**).
- After removing the key line, reconnect → **AuthenticationException** (**key
  correctly revoked after the session**).

All four Done-When criteria (connects as account user, no sudo, key removed
after session) passed live. Then the disposable user was removed.

## Tests

`tests/test_terminal.py` — 13 tests: keypair paramiko round-trip, stale-reaping
+ user-key preservation, per-session removal, open/close/list lifecycle
(max-concurrent enforcement, shell upgrade/revert, suspended rejection), and the
WebSocket client-message protocol parser. All green.

## Honestly still open

- The WebSocket pump itself (paramiko↔WS bridging) is covered by the live SSH
  test + the protocol-parser unit tests, not by an automated in-process
  WebSocket test (would need a live sshd in CI).
- Opening a terminal upgrades the account's shell to `/bin/bash` (same grant the
  SSH-keys feature makes); it reverts only when the account has zero keys.
