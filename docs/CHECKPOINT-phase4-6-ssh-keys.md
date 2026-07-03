# Checkpoint: Phase 4 feature 6 — SSH key management

## What was built

- **Supersedes ARCHITECTURE.md SS5's original v1 scope explicitly**
  ("no interactive shell... FTP/web/mail access only") — adding the
  *first* SSH key upgrades an account's shell from `/usr/sbin/nologin` to
  `/bin/bash` (`daemon/sysops.py`'s new `set_shell`/`get_shell`); removing
  the *last* key reverts it. An account with zero configured keys can
  never SSH in even if something else were to go wrong — the toggle isn't
  a separate flag, it's derived from whether `authorized_keys` is
  non-empty.
- **A real login shell, not a jailed/restricted one** — a deliberate
  choice, documented in `daemon/sysops.py`: the goal's own DONE WHEN bar
  ("added key allows SSH login") describes ordinary shell access, and
  every account is already isolated by ordinary Linux DAC permissions (no
  sudo/root capability, home dir mode 711) — the same isolation model a
  real multi-user Unix system already relies on.
- **Keys validated with a real `ssh-keygen -lf -` call** (reads the
  public key from stdin, prints its fingerprint) rather than a
  hand-rolled format regex — same "real system tool over reimplemented
  parsing" choice this project already makes for IP/CIDR
  (`ipaddress`) and Sieve scripts (`sievec`). This also means a **private
  key can never be accepted**: `ssh-keygen -lf -` fails on private-key
  input exactly like it fails on any other garbage, with no special-case
  code needed to distinguish them.
- **`~/.ssh` (0700) / `authorized_keys` (0600)**, both owned by the
  account's own uid/gid — satisfies sshd's `StrictModes` requirement with
  no `sshd_config` changes needed (confirmed this server's own config has
  no `AllowUsers`/`DenyUsers` restricting who may connect, and
  `PubkeyAuthentication` is already on by default).
- **No database table at all** — `authorized_keys` on disk is the sole
  source of truth for listing, matching Feature 4's "credentials never
  duplicated into the panel DB" posture (SSH public keys aren't secret,
  but there's still no reason to maintain a second copy that could drift).
- **A genuinely new RBAC primitive**: `require_customer_self_access`
  (`api/security.py`) — the goal's own explicit scoping, *"Customer panel
  only -- admin cannot see account SSH keys"*, is a deliberate exception
  to every other resource in this project, where an admin can always act
  on any account. Every route in `api/routers/sshkeys.py` uses this new
  check instead of `require_account_access`, and the account page's own
  link to this feature is hidden from admin's view too (defense in depth
  — not relying on the backend check alone for UX correctness).

## Testing

`tests/test_validation.py` (+7): SSH key text pre-checks (single-line
only, NUL byte, length ceiling). `tests/test_api_security.py` (+3): the
new `require_customer_self_access` — allows the owning customer, rejects
a different customer, and **rejects an admin identity** (the one check in
this whole project where that's correct behavior).
`tests/test_sshkeys.py` (new, 10): real `ssh-keygen`-generated keys for
add/list/delete, permission verification (0700/0600), shell upgrade on
first key and revert on last-key removal, duplicate detection (including
when only the comment differs), and — the one that most directly proves
"no private key can ever be accepted" — feeding a real generated private
key's own first line into `add_key` and confirming it's rejected exactly
like any other malformed input. 655 tests passing (up from 635 after
Feature 5).

## Live verification performed (the real Definition of Done)

Real account, a real `ssh-keygen`-generated ed25519 key pair, a real
`ssh` client against this server's real `sshd` — not a mock or a direct
RPC-only check:

1. Before adding any key: `ssh -i <key> p4sshtest@127.0.0.1` — **denied**
   (`Permission denied (publickey,password)`), account still on
   `/usr/sbin/nologin`.
2. `sshkeys.add` — confirmed the account's shell changed to `/bin/bash`.
3. **Real SSH login with the added key**: `whoami`/`id`/`pwd` over a real
   SSH session returned exactly `p4sshtest`, the account's real
   uid/gid, and its real home directory — matches the goal's DONE WHEN
   bar directly: *"added key allows SSH login."*
4. `sshkeys.delete` — confirmed the shell reverted to
   `/usr/sbin/nologin`, and the identical SSH command that just succeeded
   was **denied again** — *"deleted key rejected,"* the other half of the
   DONE WHEN bar.
5. **Bonus: closed a gap Feature 5's own checkpoint explicitly flagged as
   untested** — with the key re-added, a real `git push` over
   `ssh://p4sshtest@127.0.0.1/...` (not the local-filesystem push tested
   in Feature 5, a genuine SSH-transport push) correctly triggered the
   `post-receive` hook and deployed the pushed file, confirming the full,
   real, end-to-end push-to-deploy pipeline this phase set out to build
   across two features.
6. Terminated the test account; all test artifacts (local key files, work
   dirs) cleaned up.

## Known, pre-existing interaction (not a decision made here)

This server's `sshd_config.d/50-cloud-init.conf` already sets
`PasswordAuthentication yes` server-wide (present before this feature,
part of this VM's own cloud-init bootstrap, not something Forgehost
manages). Once an account's shell allows login, that pre-existing setting
means the account's own password (set at creation via `chpasswd`, per
ARCHITECTURE.md SS5) can *also* be used for SSH login, not just the SSH
key this feature manages. This is an existing server-wide sshd setting,
not a new attack surface this feature introduces — flagged here for
visibility, not changed (out of this feature's scope, and changing global
`PasswordAuthentication` would affect the operator's own SSH access to
the box too).

## What's untested / explicitly out of scope

- Key types other than ed25519 were not each individually round-tripped
  through a real SSH login (RSA/ECDSA are validated the same way by the
  same `ssh-keygen -lf -` call and would behave identically — judged
  sufficient given the validation path doesn't branch on key type at
  all).
- No key-count limit per account — not asked for in the goal.
- `git-shell`/restricted-shell alternatives were considered and
  deliberately not used (see "What was built" above) — not something to
  revisit without an explicit request to tighten this feature's scope.
