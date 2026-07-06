# CHECKPOINT phase8-6 — Email routing (Local / Remote / Backup MX)

**Goal:** per-domain Local/Remote/Backup MX; Remote stops Postfix accepting for
the domain. API: `PATCH /accounts/{u}/domains/{d}/email/routing`.

## What was built

- **Model** `EmailRouting` (domain unique, mode) in the SQLite control plane.
  Default (no row) = `local`.
- **Daemon** `daemon/handlers_email_routing.py` (ops `email_routing.get/set`):
  - **local** → `mail_domain.active = 1` (Postfix accepts + delivers locally).
  - **remote** → `mail_domain.active = 0` — Postfix's `virtual_mailbox_domains`
    SQL map queries `mail_domain WHERE active=1` **live at mail time (no reload)**,
    so this makes Postfix immediately **stop accepting** for the domain; mail
    flows to the external MX per DNS. This is the Done-When mechanism.
  - **backup** → `active = 0` + the domain is written into a Postfix
    `relay_domains` map (`postmap` + `postfix reload`).
  - `mail.set_domain_active(domain, active)` added to `daemon/mail.py`.
- **Cleanup**: removing a domain drops its EmailRouting row and refreshes the
  relay map (`handlers_domain.remove_domain`).
- **API** `api/routers/email_extras.py` — GET/PATCH
  `/accounts/{u}/domains/{d}/email/routing`.
- **Frontend**: a **Routing** tab on the Email page (radio: Local / Remote /
  Backup MX with descriptions).

## Backup MX — documented scope

`backup` mode turns off local mailbox acceptance and writes the domain into
`/etc/postfix/forgehost_relay_domains`. **Full backup-MX delivery additionally
requires `main.cf` to reference that map** (`relay_domains = ... hash:/etc/postfix/
forgehost_relay_domains` + a transport) — this is one-time structural mail
config, like the rest of Phase e's hand-applied Postfix setup. Writing the map
when main.cf doesn't reference it is harmless. Local/Remote are fully mechanized
and need no structural change (the `active` flag toggle is read live).

## Tests

`tests/test_email_routing.py` — 8 tests: default local, remote toggles
acceptance OFF, local toggles ON, backup writes the relay map + leaves it on
change-away, bad-mode & foreign-domain rejection, and no-mail-domain still
records the mode. All green.
