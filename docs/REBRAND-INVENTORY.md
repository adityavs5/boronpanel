# Rebrand Inventory — Forgehost → Boron Panel

Written before any rebrand edits, per the goal's required approach. Full
case-insensitive sweep: **280 files, ~1,682 occurrences** of "forgehost" in
any casing, excluding `.git/`, `node_modules/`, `__pycache__/`, `.venv/`,
and the built `static/dist/` bundle (regenerated from source, not
hand-edited).

Casing conventions found (and their Boron equivalents used throughout this
rebrand): `forgehost` → `boron` (paths, identifiers, most prose),
`Forgehost` → `Boron` (UI text, sentence-initial prose), `FORGEHOST` →
`BORON` (constants, env var names). No `ForgeHost` camelCase variant
exists. "Forgehost Panel" as a two-word phrase does not appear anywhere in
the current codebase — the product is referred to as bare "Forgehost"
throughout (UI copy, default branding name, prose). This rebrand follows
the same pattern: bare **Boron** everywhere the codebase currently says
bare "Forgehost", and **Boron Panel** specifically for the handful of
formal/full-name contexts named in the goal (browser tab default title,
login hero, installer completion banner, README H1) where a fuller name
reads better than a bare word.

## Category breakdown

| Category | File count | Handling |
|---|---|---|
| Historical checkpoint docs (`docs/CHECKPOINT-*.md`) | 70 | **Left untouched** (filename + content) — explicit goal instruction |
| Historical audit docs (`docs/AUDIT*.md`) | 6 | **Left untouched** — see reasoning below |
| Historical research/design docs | 4 | **Left untouched** — see reasoning below |
| Generated files (`docs/api/openapi.json`, `frontend/package-lock.json`) | 2 | **Regenerated**, not hand-edited |
| `docs/STATUS.md` | 1 | **Special handling** — see below |
| User-visible strings (frontend, branding defaults, emails, error pages, installer output) | ~40 | Renamed |
| Code comments/docstrings (prose only) | ~180 files | Renamed |
| Actual code identifiers (constants, logger names) | 2 (`FORGEHOST_VERSION`, logger name hierarchy) | Renamed, all call sites updated |
| Filesystem paths in config defaults | 1 file (`shared/config.py`), ~25 path constants | Renamed, migration documented |
| systemd units / cron / logrotate (static files) | 9 | Renamed (file + content), migration script provided |
| systemd units (dynamic, per-account: Redis/Node/Python apps, cgroup slices) | 3 code sites (`daemon/redisacct.py`, `daemon/nodeapps.py`, `daemon/pythonapps.py`, `daemon/cgroups.py`) | Naming pattern renamed in code; **live units on this box are NOT renamed by this pass** — migration script + manual steps documented, not auto-executed (see Decision 4 below) |
| Config file (`forgehost.toml`) | referenced in ~5 files | Renamed to `boron.toml`, all readers updated |
| Linux system user (`forgehost-api`) | referenced throughout | Renamed to `boron-api`, migration documented |
| MariaDB identifiers (`forgehost_daemon`, `forgehost_mailro`, `forgehost_mail` schema) | 3 protected strings | **Kept unchanged** — see Decision 1 below |
| README, ARCHITECTURE.md, RELEASING.md | 3 | Renamed (living/forward-facing reference docs) |

## Decisions requiring explicit reasoning

### Decision 1 — MariaDB identifiers stay unchanged internally

`forgehost_daemon` (the MariaDB admin-equivalent user `forgehostd`
authenticates as), `forgehost_mailro` (the SELECT-only user embedded in
Postfix/Dovecot config), and `forgehost_mail` (the schema holding mail
domain/mailbox rows) are **not renamed**. Per the goal's own guidance
("renaming the panel DB on existing servers is risky... prefer keeping the
DB name internally"): these are live credentials/schema names already in
use on this running server. Renaming a MariaDB user requires `RENAME USER`
+ re-issuing/updating the matching password in `/etc/forgehost/secrets.env`
(soon `/etc/boron/secrets.env`) in the same atomic step, and renaming a
schema requires either `RENAME TABLE ... TO new_schema.*` per table (MariaDB
has no single `RENAME DATABASE`) or a full dump/restore — both real,
multi-step operations against a live database backing active mail
delivery, with a much higher blast radius than a filesystem path or
service-name rename if done wrong mid-operation. These are backend
implementation details never surfaced to any user (customer or admin) —
zero user-visible benefit to renaming them justifies that risk in this
pass. `shared/config.py`'s `mariadb_admin_user` default and every
docstring/comment referencing these three exact identifiers are preserved
verbatim (not touched by the rebrand's text substitution — see the
protected-terms list below). Documented as a follow-up if a from-scratch
Boron-branded install is ever done (a fresh install can freely choose
`boron_daemon`/`boron_mailro`/`boron_mail` from day one with no migration
risk at all — see `docs/REBRAND-MIGRATION.md`).

### Decision 2 — Historical checkpoint/audit/research docs are left untouched, not just checkpoint filenames

The goal explicitly says "leave historical checkpoint filenames." This
inventory extends that same reasoning to the **content** of
`docs/CHECKPOINT-*.md` (70 files) and, by the identical logic, to
`docs/AUDIT-FINDINGS.md`, `docs/AUDIT-THREATMODEL.md`,
`docs/AUDIT2-FINDINGS.md`, `docs/AUDIT2-THREATMODEL.md`,
`docs/AUDIT3-FINDINGS.md`, `docs/AUDIT3-THREATMODEL.md`,
`docs/RESEARCH.md`, `docs/NAMESPACE-DESIGN.md`, `docs/NAMESPACE-ANSWERS.md`,
and `docs/PLAN-cloudflare.md`. These are all point-in-time records of what
was actually built, found, or decided under the Forgehost name at a
specific past date — rewriting their prose to retroactively say "Boron"
would misrepresent history (e.g. "Audit 3 found a Critical FileBrowser
Quantum finding" becoming confusingly dated against a doc that now claims
the product was always called Boron) and provides no real value, since
nobody reads these for current branding — they're read for "what happened
and why." Living/current-state docs (`README.md`, `docs/ARCHITECTURE.md`,
`docs/RELEASING.md`) are fully rebranded since they describe the system
*as it exists today*, not a historical event.

### Decision 3 — `docs/STATUS.md` gets a new top entry, not a full rewrite

`STATUS.md` is structurally different from the checkpoint docs — it's the
continuously-updated single "handoff" synthesis, explicitly described in
its own header as "what's done, what's verified, what to check first." Its
2,300+ lines are almost entirely historical narrative of each build phase
("Forgehost update system... DEPLOYED", "the Forgehost daemon was built
to..."), the same nature as the checkpoint docs it summarizes. Rewriting
that entire narrative to say "Boron" throughout would be exactly as
historically misleading as rewriting the checkpoints themselves, for a
similarly enormous edit with no real benefit (nobody re-reads old phase
summaries for current branding). **Decision**: leave the existing 2,300+
lines exactly as historical record, and prepend a new top section (matching
this file's own established "newest entry first" convention) documenting
the rebrand itself — what changed, the decisions above, and a pointer to
this inventory and the migration doc — the same way every other phase's
own top-of-file entry summarizes what it did. Going forward, all *new*
STATUS.md entries use Boron branding.

### Decision 4 — Live per-account systemd units/slices are renamed in code, but NOT auto-migrated on this box

This live server currently has real per-account systemd state carrying the
old naming: `forgehost-redis-adityascn-1.service` (active),
`forgehost-adityascn.slice` / `forgehost-cust1.slice` /
`forgehost-demo2.slice` (active), and `forgehost-node-demo1-1/2.service`
(disabled but present) — confirmed via `systemctl list-units`/
`list-unit-files` before any change was made. The naming *pattern* in code
(`daemon/redisacct.py`, `daemon/nodeapps.py`, `daemon/pythonapps.py`,
`daemon/cgroups.py`'s `PARENT_SLICE`/`_slice_name`) is renamed so every
*new* account/app/Redis instance created after a real deploy gets
`boron-*` units — but this session does **not** stop/rename/recreate the
already-running units and slices on this box. Doing so live means: briefly
interrupting the account's Redis/Node/Python service, recreating the
systemd unit + cgroup slice under the new name, and restarting the
provisioning daemon's cgroup reconciler against the new slice hierarchy —
a genuinely disruptive, multi-service operation against a shared,
production-adjacent box that the active goal did not explicitly direct.
Consistent with this project's standing convention (deploys and live
infrastructure mutations need explicit operator sign-off — see
`docs/AUDIT3-FINDINGS.md` A3-7 for the same principle applied during the
prior audit), the concrete steps to migrate this box's existing units are
written out in full in `docs/REBRAND-MIGRATION.md` for an operator to run,
rather than executed unilaterally here.

## Protected terms (excluded from the text substitution everywhere)

`forgehost_daemon`, `forgehost_mailro`, `forgehost_mail` (word-bounded, so
it does not also protect `forgehost_mailro`'s substring) — see Decision 1.

## Full per-directory file counts (non-excluded files only)

```
docs        84 files (70 excluded as CHECKPOINT/AUDIT/RESEARCH/NAMESPACE/PLAN history; 14 rebranded)
daemon      75 files
tests       35 files
frontend    29 files
scripts     21 files
api         15 files
deploy       9 files (systemd/cron/logrotate — renamed + content updated)
shared       5 files
templates    4 files
version.py, static/forgehost.css, README.md   3 files
```

Full detail on the systemd/path/config-file rename and the live-server
migration steps: `docs/REBRAND-MIGRATION.md`.
