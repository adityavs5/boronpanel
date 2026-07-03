# Checkpoint: Phase 4 pre-work — critical cross-account authorization bypass (IDOR), found while building Feature 1

The single most severe finding across all four phases of this project. Found
while reading `api/routers/email.py` to mirror its patterns for Feature 1
(SpamAssassin's `PATCH /accounts/{u}/domains/{d}/email/spam-filter`) — the
existing email router's routes checked only
`require_account_access(identity, username)` (does the caller own the
account named in the URL?) and never `require_domain_access(identity, domain)`
(does the `domain` in that same URL actually belong to that account?). A
systematic audit found this same gap, in varying severity, across **8
routers and 2 daemon modules**.

## Why this matters: the daemon does not re-check authorization by design

Per `ARCHITECTURE.md` SS2: *"the daemon does not re-derive authorization, that
already happened in the API layer... the Unix socket's filesystem permission
is the actual trust boundary between 'can call forgehostd at all' and
'cannot'; per-user RBAC happens one layer up, in forgehost-api."* This is the
right architecture, but it means a missing check at the API layer is not
"defense in depth, still caught downstream" — for several of these routes it
was the *only* gate, and it was absent.

## What was found, in descending severity

1. **Email hijack (`api/routers/email.py`, `daemon/handlers_mail.py`)** — a
   customer could set a **catch-all** or **forwarder** on *any other
   tenant's mail domain* by supplying their own valid `username` in the URL
   alongside the victim's `domain`. `handlers_mail.set_catchall`/
   `create_forward`/`set_autoresponder` etc. had **zero** ownership check at
   the daemon layer either — full, silent mail interception for any domain
   on the server, from any authenticated customer account. 16 functions.
2. **DNS hijack (`api/routers/dns.py`, `daemon/handlers_dns.py`)** — the
   JSON API routes (`list_records`/`set_record`/`delete_record`) already
   correctly called `require_domain_access` — but the **UI-facing routes**
   (`ui_zone_records`/`ui_create_zone`/`ui_set_record`/`ui_delete_record`,
   real POST endpoints, not "just for humans") did not. `set_record`/
   `delete_record` at the daemon layer have no ownership check at all — a
   customer could rewrite another tenant's A/MX/TXT records (mail/site
   takeover) or delete their zone, via the UI form-post endpoints
   specifically. 4 functions.
3. **Backup data exposure + unauthorized destructive restore
   (`api/routers/account_backups.py`, `daemon/backup.py`)** — `job_id` is a
   small sequential integer with **no ownership check anywhere**. A customer
   could: browse the file listing inside any other account's backup
   (`backup.job.browse`, information disclosure), and — more seriously —
   **trigger a restore of another account's backup onto that account**
   (`backup.restore.trigger`) with zero authorization, silently reverting a
   competitor/other tenant's live site and database to a stale backup state
   at a time of the attacker's choosing (the restore target is the backup's
   *own* `account_id`, not the attacker's account, so this is unauthorized
   sabotage of the victim, not data exfiltration into the attacker's own
   account — still a serious, real integrity/availability attack). 6
   call sites across `get_job`/`browse_backup`/`trigger_restore`/
   `get_restore_job` (the last not currently wired to any route, fixed
   anyway so it can't become a silent IDOR the moment one is added).
4. **WordPress admin-credential theft
   (`api/routers/wordpress.py`, `daemon/wordpress.py`)** — same `job_id`
   pattern. `WordPressJob` stores the generated admin password
   *transiently*, cleared on the **first** successful read (a deliberate
   one-time-reveal design, see the model's own docstring). With no
   ownership check, any customer polling `job_id=1,2,3,...` could steal
   **every other account's freshly-installed WordPress admin password** the
   moment installation completed — and since the reveal is one-time, the
   attacker's read would also **permanently deny the legitimate owner** from
   ever seeing their own credentials. Credential theft plus denial-of-view
   in one bug.
5. **Redirect defacement/phishing (`api/routers/redirects.py`,
   `daemon/handlers_redirect.py`)** — no ownership check anywhere (redirect
   ops don't even take a `username` param at the daemon layer). A customer
   could point any path on any other tenant's domain at an arbitrary URL.
   8 functions.
6. **Forced SSL issuance abuse (`api/routers/ssl_router.py`,
   `daemon/ssl.py`)** — `issue_certificate` takes only `domain`, no
   ownership check. Since Forgehost's own infrastructure genuinely serves
   the challenge for any domain it hosts, the ACME challenge would actually
   succeed — a customer could force real Let's Encrypt issuance/renewal
   against another tenant's domain at will (rate-limit exhaustion /
   unwanted cert rotation). 3 functions.
7. **Domain deletion / mail routes / WordPress install (`domains.py`,
   `mail.py`'s UI routes, `wordpress.py`'s API+UI routes)** — mixed: some of
   these (`handlers_domain.remove_domain`, `wordpress.install()`'s
   `_account_and_domain`) already had a correct ownership check *at the
   daemon layer*, so were not actually exploitable — but the API-layer gap
   still meant a wrong request surfaced as a raw 500/`RuntimeError` instead
   of a clean 403, and there was no defense-in-depth if the daemon check
   were ever refactored away. Fixed at the API layer anyway for consistency.
   `mail.py`'s `change_mailbox_password` (account-scoped password-manager
   route, Phase 3 feature 10) was a **real, unprotected credential-change**
   IDOR, not just a defense-in-depth gap — a customer could set a new
   password on another tenant's mailbox.

## What was confirmed *not* vulnerable, and why (checked, not assumed)

- `backups.py` (admin-only destination/schedule management) and
  `tokens.py` (API token issue/revoke) both correctly gate every route
  behind `require_admin` — flagged by an initial automated scan as
  "missing `require_domain_access`", but manually confirmed safe: an admin
  is legitimately allowed to act on any destination/token by design.
- `cron.py`'s `job_id` is a UUID scoped by construction to a *search within
  that specific username's own crontab file* (`daemon/cron.py`'s
  `update_job`/`delete_job` call `_read_raw(username)`/`_write_raw(username)`
  exclusively) — a job_id from a different account's crontab could never
  match a line in this account's crontab. No fix needed.
- `logs_router.py`'s `get_log` (Phase 3 feature 9) already independently
  verifies `_domain_belongs_to_account` at the daemon layer, exactly as its
  own Definition of Done claimed — confirmed correct, left as-is (matching
  API-layer check would only improve the error code, not close a real gap).
- `domains.py`'s `add_domain`/`ui_add_domain` correctly need no domain check
  at all — the domain doesn't exist as anyone's yet, and `Domain.domain`
  carries a real DB-level `UniqueConstraint`, so claiming an
  already-in-use domain fails at the database regardless.

## The fix

Two shapes, matching where the real gate belongs:

1. **Where a `domain` parameter exists**: add
   `require_domain_access(identity, domain)` at the API layer (the
   project's own already-correct, already-tested primitive in
   `api/security.py` — it just wasn't called everywhere it needed to be).
   40 call sites across `domains.py`, `email.py`, `dns.py`, `mail.py`,
   `redirects.py`, `ssl_router.py`, `wordpress.py`.
2. **Where only a bare `job_id` exists** (WordPress jobs, backup jobs/restore
   jobs — no `domain` in scope at all): the fix has to live in the
   **daemon handler itself**, since there's nothing for an API-layer domain
   check to check. `daemon/wordpress.py`'s `get_job` and
   `daemon/backup.py`'s `get_job`/`browse_backup`/`trigger_restore`/
   `get_restore_job` now require a `username` param and cross-check the
   job's own `account_id` against that username's account, raising the
   *same* "not found" error for a nonexistent job_id and one that exists but
   belongs to someone else (so the endpoint can't be used to enumerate
   which job IDs belong to other accounts either). The API routers were
   updated to actually thread `username` through to these ops.

## Testing

`tests/test_cross_account_authorization.py` (new, 11 tests): calls the
router functions **directly** (FastAPI's route decorators return the plain
function unchanged, so no TestClient/HTTP stack is needed) with a crafted
cross-account `Identity`, and monkeypatches `call_daemon` in each router
module to raise if it's ever reached — proving the rejection happens at the
authorization check itself, not somewhere downstream. Covers the highest-
severity representative case from each fixed router (email catchall,
redirects, DNS record UI route, mailbox password change, SSL issue,
WordPress install trigger, domain removal) plus direct daemon-layer tests
for the `job_id`-based fixes (WordPress job, backup job, backup restore),
including a positive-path assertion that the *rightful* owner's access still
works (no false-positive lockout). 515 tests passing (up from 499 after the
password-audit checkpoint).

**A bug in the tests themselves, caught and fixed before it did any harm**:
the first draft of three daemon-layer tests omitted the `isolated_db`
fixture, meaning `write_session()` used the *real* `settings.db_path` — i.e.
these tests briefly wrote real rows (`bkown1`, `bkown2`, `wpown1`, `wpown2`
accounts, a `test-local` backup destination) into this VM's actual
production `/var/lib/forgehost/forgehost.db`. Caught immediately by a
second failure (a `UNIQUE constraint failed` on a re-run, which shouldn't
happen against a supposedly-isolated per-test DB), diagnosed, and both the
test bug and the resulting production-DB pollution were fixed/cleaned up
before moving on — the exact "verify against the real system, don't just
trust the mock" discipline this project's own checkpoints repeatedly credit
for catching real bugs, this time catching a bug in the test harness itself.

## Live verification performed

1. Real two-account setup (`p4vicowner` owning `p4victim.example`,
   `p4attacker` as a separate account), a real customer login issued for
   the attacker account, a real authenticated session cookie via
   `POST /login`.
2. **Real HTTP attack**: `POST /api/v1/accounts/p4attacker/domains/
   p4victim.example/email/catchall` with the attacker's own real session
   cookie — **`403 {"detail":"not authorized for this domain"}`**, deployed
   fix confirmed live, not just in the test suite.
3. **Positive-path check (no false-positive lockout)**: the legitimate
   owner's real session, same endpoint, own domain — passed authorization
   cleanly (`400 mail domain not provisioned`, an unrelated and correct
   downstream error, not a 403) — confirms the fix doesn't block legitimate
   same-account access.
4. Deployed to `/opt/forgehost`, both `forgehost-provisiond` and
   `forgehost-api` restarted and confirmed active.
5. Throwaway accounts/customer logins terminated and their leftover
   `panel_users` rows removed after verification.

## What's still worth reviewing

- `account.terminate` does not clean up the terminated account's
  `PanelUser` (customer login) row(s) — noticed incidentally while cleaning
  up this checkpoint's own test accounts (their login rows survived
  termination, pointing at a now-terminated account). Not a security
  vulnerability by itself (a terminated account's operations already fail
  their own status checks), but a real lifecycle gap worth a follow-up,
  not fixed here (unrelated to this checkpoint's scope).
- This audit covered every router function taking a `domain` or a bare
  `*_id` parameter (systematically, via an AST scan of `api/routers/*.py`,
  not just the files that happened to look suspicious). It did not extend
  to non-HTTP surfaces (e.g. whether any daemon-internal call path other
  than the RPC dispatch table could reach these handlers with an
  attacker-controlled `params` dict) — believed not to exist (the daemon's
  only external entry point is the RPC socket per ARCHITECTURE.md SS2), not
  independently re-verified beyond that architectural guarantee.
