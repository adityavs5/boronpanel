# Phase 5 feature 4: firewall UI (UFW)

**XHIGH effort per the goal** -- misconfiguring a live firewall can lock
out SSH/the panel itself or break every hosted account's live traffic, so
this feature got extra scrutiny at every step (see "Deliberately NOT done
live" below).

## What was built

- `daemon/firewall.py`: CRUD over UFW rules via `ufw`'s own CLI (never
  raw iptables, never `ufw`'s internal rule-storage format -- the CLI is
  the documented stable interface, matching the same "wrap the target
  tool's own stable interface" principle Feature 3 applied to Postfix's
  `mailq`).
  - `list_rules`: parses `ufw show added` (not `ufw status numbered` --
    confirmed live that `ufw status`/`status numbered` show **no rules
    at all** while UFW is inactive, only `Status: inactive`; `ufw show
    added` is the one subcommand that reports the actual configured
    rule set regardless of whether UFW is currently enforcing it).
  - `add_rule`/`delete_rule`: build the exact `ufw allow|deny [from <ip>
    to any port <port> [proto <proto>]] [comment '...']` argv Postfix's
    own CLI expects, validated first (port range, protocol allowlist,
    `shared.validation.validate_ip_or_cidr` for the source, a
    conservative comment charset). Rules are identified by a stable
    synthetic `rule_id` (a hash of action/port/protocol/from) computed
    at parse time, not by `ufw status numbered`'s rule numbers -- those
    shift every time a rule is added/removed, an unstable identifier to
    hand back to a UI that lists then later acts on one row.
  - `enable_firewall`/`disable_firewall`: both require an explicit
    `confirm=true`, enforced server-side (goal's "toggle UFW on/off with
    confirmation").
- **Hard-protect enforcement, in the daemon, not just the UI**:
  - `add_rule` rejects any `deny` rule targeting a hard-protected port
    (real SSH port -- read from `/etc/ssh/sshd_config`, default 22;
    the panel's own bind port; 80/443; 25/587/993).
  - `delete_rule` additionally refuses to delete the **last remaining
    allow rule** for a hard-protected port -- deleting it wouldn't
    create an explicit deny, but once UFW is active its default `DROP`
    policy (confirmed in `/etc/default/ufw`) would achieve the identical
    outcome by omission. The goal's literal ask ("cannot be blocked via
    UI") is about outcome, not mechanism, so this closes the same door
    a pure "reject explicit deny" check would leave open.
  - `enable_firewall` calls a new `_ensure_baseline_allow_rules()` first
    -- adds an allow rule for every hard-protected port that doesn't
    already have one, *before* ever flipping UFW active. Not asked for
    verbatim in the goal, but a direct consequence of "pick conservative/
    secure, document why": enabling UFW with its real default-DROP
    policy and zero allow rules yet in place would lock out SSH/the
    panel/live hosted traffic the instant it took effect.
- RPC ops `firewall.list/add/delete/status/enable/disable`, registered in
  `daemon/server.py` (`firewall.list` added to the F7 `REPORTING_EXECUTOR`
  pool).
- `api/routers/firewall.py` (`/api/v1/firewall/rules`, `/status`,
  `/enable`, `/disable`, `/ui/firewall`, admin-only) + `firewall.html`
  (status + enable/disable behind a required confirm checkbox, add-rule
  form, rule table with a "protected" badge and a delete button per row).

## Real bugs / decisions found by live testing

- **`ufw status`/`ufw status numbered` report NOTHING while UFW is
  inactive** -- confirmed by adding a real rule (`ufw allow 54321/tcp`)
  and finding `ufw status numbered` still printed only `Status:
  inactive` with no rule lines at all, even though the rule genuinely
  existed (confirmed via `ufw show added` and the raw
  `/etc/ufw/user.rules` file). Had the list feature been built against
  `status numbered` (the more commonly-documented approach, and this
  project's first instinct) without checking this first, the entire
  rule list would have silently appeared empty on any server where an
  operator hadn't yet clicked "Enable" -- exactly backwards from what a
  firewall *configuration* UI needs (you configure rules, then enable
  enforcement, not the other way around). `ufw show added` was found by
  reading `ufw`'s own built-in subcommand list, then confirmed correct
  live before committing to it as the parsing target.
- **`postfix.service`-style "the obvious unit name is a decoy" pattern
  doesn't recur here** -- `ufw` has no such indirection, one real CLI,
  confirmed directly.

## Live verification

- **Goal's own DONE WHEN scenario, run for real, against the actual
  system `ufw` binary**: `POST /api/v1/firewall/rules` (allow, port
  54321/tcp, comment `phase5-test-rule`) -> `200`; independent `ufw show
  added` (run directly, outside the API) showed the exact rule just
  added, comment included; `DELETE
  /api/v1/firewall/rules/{rule_id}` -> `200`; a second independent `ufw
  show added` confirmed `(None)` -- the rule was really gone, not just
  reported gone.
- Hard-protection, live: `POST .../rules` with `{"action": "deny",
  "port": 22}` / `443` / `25` each returned `400` with the expected
  message, and `ufw show added` confirmed **no rule was ever created**
  for any of the three attempts -- the rejection happens before the
  `ufw` subprocess is ever invoked, not after a rule is added and then
  rolled back.
- `GET /api/v1/firewall/rules` / `/status` against the real, currently-
  inactive UFW installation -> `{"rules": [], "active": false}` and
  `{"active": false, "raw": "Status: inactive\n"}`, matching a direct
  `ufw status` run independently.

## Deliberately NOT done live: actually enabling UFW on this server

This is the one explicit scope decision this feature's build made, and
it's worth stating plainly rather than burying it. The goal's DONE WHEN
for this feature is: *"add test rule, confirm via ufw status, delete
it"* -- every word of that was run for real, above, **without** UFW
itself ever being switched to `active`. Actually enabling UFW on this
box is a materially different, higher-blast-radius action: this server
fronts live hosted-account traffic across many ports this feature's
hard-protected set does *not* cover (DNS 53, FTP 21 + Pure-FTPd's passive
range, plain IMAP 143, MariaDB 3306) -- enabling enforcement without
first also covering those would silently break real, already-functioning
hosting features (DNS resolution, FTP, IMAP) the instant it took effect,
even with SSH/panel/web/mail correctly protected. `_ensure_baseline_allow_rules()`
was written and unit-tested to make a *future* real enable safe for the
ports this goal explicitly named, but actually invoking `firewall.enable`
against this specific shared production server -- which the goal's own
DONE WHEN never asked for -- was judged out of scope for an unattended
build to take on its own initiative, matching this project's standing
policy of pausing on hard-to-reverse, wide-blast-radius infrastructure
actions the goal didn't explicitly call for. The enable/disable code
path itself is fully implemented and covered by 4 dedicated mocked
tests (confirm-required, baseline-rules-added-first, already-covered-
ports-skipped, disable-requires-confirm) -- only the literal "flip it on
for real on this box" step was deliberately deferred, pending an
operator's explicit go-ahead.

## What's untested

- Actually enabling UFW live (see above).
- IPv6-scoped rules (`ufw allow from <ipv6> ...`) -- `validate_ip_or_cidr`
  already accepts IPv6 (it wraps Python's `ipaddress` module, which
  handles both families), and the rule-spec builder has no v4-specific
  logic, but no IPv6 rule was independently added/verified against real
  `ufw` this pass.
- Very large rule sets (hundreds of entries) -- parsing is a simple
  single pass with no obvious scaling cliff, not stress-tested at that
  size.
