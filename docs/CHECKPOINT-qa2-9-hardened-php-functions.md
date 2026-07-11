# QA round 2 — Item 9: hardened PHP default + per-account/per-domain function control

## What existed already (per investigation)

A per-account PHP directive editor (`daemon/phpdirectives.py` / `daemon/
handlers_php_ini.py` / `api/routers/php_ini.py`) already existed, writing
into that account's OLS vhost `phpIniOverride` block (the exact mechanism
the goal asks for) — but it's **customer-editable**
(`require_account_access`, not `require_admin`), and `disable_functions`
was explicitly absent from its registry, actively rejected by
`validate()`. No hardened default `disable_functions` existed anywhere in
`scripts/install.sh` or any template — the real system lsphp `php.ini`
files ship with `disable_functions =` empty.

This distinction matters: naively adding `disable_functions` to the
existing customer-facing registry would let a customer re-enable
`exec`/`shell_exec`/etc. for their own account, defeating the entire point
of a hardening default. The goal explicitly asks for an **admin** UI, not
a customer one.

## Fix

**Hardened default** (`scripts/install.sh`, new `setup_php_hardening()`,
called after `deploy_app`): writes `disable_functions = <hardened list>`
directly into both `/usr/local/lsws/lsphp81/etc/php/8.1/litespeed/php.ini`
and the 8.3 equivalent, then restarts `lshttpd`. The list itself
(`daemon/phpdirectives.DEFAULT_DISABLE_FUNCTIONS` — `exec`, `system`,
`shell_exec`, `passthru`, `popen`, `proc_open`, `proc_get_status`,
`proc_close`, `proc_terminate`, `proc_nice`, `pcntl_exec`) is defined once
in Python and read by the installer via the same `python -c` bootstrap-call
pattern `system.bootstrap_ols` already uses, so the installer and the
per-scope override mechanism below can never drift apart.

**Per-account/per-domain override, admin-only**: new `PhpFunctionOverride`
model (`account_id`, nullable `domain` — NULL means account-wide, a
specific domain takes precedence over the account-wide row for that one
domain), new `daemon/phpfunctions.py` (`get_overrides`/`set_override`/
`delete_override`/`effective_disable_functions`), new admin-only router
`api/routers/php_functions.py`
(`/api/v1/admin/accounts/{u}/php-functions`, `require_admin` — a
completely separate surface from the customer-facing `php_ini.*`
endpoints). Wired into rendering: `daemon/ols.py`'s `_apply_targets` now
computes the effective `disable_functions` **per domain** (a genuine
per-domain change — every other PHP-ini directive in this codebase is
account-only, computed once and reused across all of an account's
domains) and layers it onto that domain's `phpIniOverride` block via a new
`_with_disable_functions` helper — absent an override anywhere, nothing
is rendered and the system php.ini's hardened default silently applies
(same "absence means default" convention every other feature here
follows).

Admin UI: new "PHP Functions" tab on the admin `AccountDetail` page (not
the customer-facing PHP settings) — pick a scope (account-wide or a
specific domain from that account's own domain list), edit the
comma-separated function list, save or clear. Deliberately not added
anywhere reachable by a customer session.

## A real bug caught by actually running `--dry-run` (not just reading the diff)

The first version of `setup_php_hardening()` computed the hardened
function list via direct command substitution
(`funcs="$("${VENV}/bin/python" -c ...)"`) with no dry-run guard —
`--dry-run` never actually deploys the app, so `${VENV}/bin/python`
doesn't exist yet at that point, and the failing command substitution
under `set -e` killed the entire script (exit 127) partway through,
silently, with no error message reaching the summary. Fixed with the same
dry-run early-return `create_admin()` already uses. `bash -n` and
`shellcheck` both stayed clean throughout — neither would have caught
this; only actually running `--dry-run` did.

## Tests

`tests/test_phpdirectives.py` (new, 9 tests): the hardened set contains
the expected functions, `validate_disable_functions` accepts both
comma-string and list input, dedupes, accepts an explicit empty list
(clearing every function is a real, distinct-from-"no override" request),
rejects injection-shaped/space-containing names, rejects non-list/string
input, caps the list length.

`tests/test_phpfunctions.py` (new, 10 tests): default-only when nothing
set, account-wide set/upsert (not duplicate rows), per-domain set, unknown
domain rejected, account-wide and per-domain override coexisting
independently, **domain-scope wins over account-wide** in
`effective_disable_functions` while an unrelated domain still falls back
to the account-wide row, `None` when nothing is set at any scope,
delete + delete-missing.

`tests/test_ols.py` (+5): the merge helper's absence-preserving/minimal-
php_ini-construction/append-not-duplicate behavior, and a full
`render_vhost_conf` assertion that the resulting `phpIniOverride` block
actually contains `php_admin_value disable_functions "exec,shell_exec"`.

`python3 -m pytest tests/test_phpdirectives.py tests/test_phpfunctions.py tests/test_ols.py tests/test_handlers_php_ini.py tests/test_phpext.py tests/test_apidocs.py -q`
→ 130 passed. Route registration confirmed via `TestClient` (401, not
404). `npm run build` — clean, new `AccountDetail` chunk size increase
confirms the new tab compiled in. `bash -n` + `shellcheck` clean;
`--dry-run` runs end to end with exit 0 (after the fix above).

## What's still open

The actual `sed -i` against this box's live lsphp php.ini files was
**not** run — this changes PHP behavior for every hosted account on a
shared box, a meaningfully different risk class from the additive-only
UFW/Pure-FTPd changes applied live for item 7. Deferred to the operator's
own install/upgrade run, consistent with this batch's treatment of every
other system-behavior-changing (as opposed to purely additive) change.
