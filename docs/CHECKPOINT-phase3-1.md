# Checkpoint: Phase 3, Feature 1 — Full DNS zone editor + SPF/DKIM/DMARC automation

## What was built

- **Record types widened from A/AAAA/CNAME/MX/TXT (Phase c's v1 scope) to
  also cover PTR/SRV/CAA** (`shared/validation.py`'s `validate_record_type`,
  new validators in `daemon/handlers_dns.py`: `_validate_srv`/
  `_validate_caa`, PTR reusing the same domain-normalization logic as
  CNAME). All values still go through PowerDNS's REST API exclusively
  (`daemon/powerdns.py`, unchanged) — never raw SQL against PowerDNS's
  schema, per RESEARCH.md §6/ARCHITECTURE.md §6.
- **Full inline zone editor UI** (`api/templates_ui/dns_zone.html` +
  `api/routers/dns.py`): every record shown grouped by (name, type) with a
  per-row delete button; the add/replace form now supports all 8 types via
  a multi-line textarea (one value per line), matching PowerDNS's own
  REPLACE-the-whole-rrset semantics rather than only supporting
  single-value rrsets. A domain with no Forgehost-managed zone yet now
  gets a "Create DNS zone" button in the UI instead of a raw PowerDNS 404
  — there was previously no discoverable UI path to create a zone at all,
  a real gap for a feature titled "full DNS zone editor UI."
- **SPF/DKIM/DMARC auto-generation on mail-domain creation**
  (`daemon/dkim.py`, wired into `daemon/handlers_mail.py`'s
  `create_mail_domain`/`delete_mail_domain`/`terminate_account_mail`):
  - Generates a real 2048-bit RSA keypair via `openssl genrsa`/
    `openssl rsa -pubout` (idempotent — a repeat call reuses the existing
    on-disk key rather than silently rotating it), stored at
    `<dkim_base_dir>/<domain>/default.private` (root-only, 0600).
  - If the domain falls under a Forgehost-managed DNS zone (via the same
    parent-zone lookup Phase 2 feature 4 uses for subdomain A records —
    factored out into `daemon/dns_zone_lookup.py` so both callers share
    one implementation instead of two copies), publishes three TXT
    records through the normal `dns.set_record` PowerDNS path: SPF at the
    domain's own name, DKIM at `default._domainkey.<name>`, DMARC at
    `_dmarc.<name>`.
  - If the zone isn't Forgehost-managed, the keypair is still generated
    and stored (so it exists for the operator to publish elsewhere, or
    later if the zone is imported) and the result says so explicitly
    (`dns_published: false`) rather than silently pretending records went
    out.
  - Bookkeeping in a new `DkimKey` table (domain, selector,
    dns_published) — a new table, so no `ALTER TABLE` migration needed
    (`Base.metadata.create_all()` handles it, unlike Phase 2 feature 6's
    column-add case).

## Deliberate scope decisions

- **DKIM signing itself is NOT wired into Postfix's outbound path.** This
  module generates and publishes the *public* key; it does not install/
  configure OpenDKIM (or any milter) to actually sign outgoing mail with
  the *private* key. That's a materially larger change (a new service,
  `smtpd_milters`/`milter_protocol` in `main.cf`, its own validate/reload
  path) that the goal's own DONE WHEN bar doesn't ask for — it only
  requires "add DKIM record, verify public key resolves in DNS," not
  "verify outgoing mail is actually signed." Flagged here rather than
  half-built silently; a natural next Phase 3.5 item.
- **DMARC defaults to `p=none` (monitor-only), not `p=quarantine`/
  `p=reject`.** Since outgoing mail isn't actually DKIM-signed yet (see
  above), publishing an enforcing DMARC policy before signing is live
  would risk receivers rejecting/quarantining genuine mail from the
  domain (it would fail DKIM alignment by construction). `p=none` is the
  conservative, correct default until a milter is wired up — this is
  *more* cautious than either HestiaCP or ISPConfig, both of which
  RESEARCH.md §6 already found do no SPF automation at all.
- **SPF defaults to `~all` (soft fail), not `-all` (hard fail)** — the
  same "start permissive, let the operator tighten once verified" posture
  as the DMARC default, and the common convention recommended by every
  major mail provider's own SPF setup guide for a freshly-configured
  domain.
- **Editing a multi-value rrset through the UI replaces the *entire*
  value list at that (name, type) pair** — this is not a limitation
  introduced here, it's how DNS itself represents multi-value records
  (and how `daemon/powerdns.py`'s `upsert_record` has always worked, REPLACE
  semantics, Phase c). Worth calling out explicitly now that SPF's TXT
  record lives at the zone apex: if an operator later adds an unrelated
  TXT record at the same apex name through the generic editor, they need
  to include the SPF value in that same edit (both values in one
  textarea) or they'll overwrite each other — documented directly in the
  UI's help text, not just here.
- **`account.reactivate`/`add_domain`'s idempotency-guard pattern was not
  needed here**: DKIM setup is a new, independent code path (no
  "already exists" guard could accidentally short-circuit it silently),
  since it doesn't reuse any existing conditional-insert logic — avoids
  the exact bug class Phase 2 feature 7 found four times.

## A real pre-existing (Phase c) bug found by live UI testing

`dns_zone.html` used `{% for v in r.values %}` to iterate a record's value
list, where `r` is a plain dict with a key literally named `"values"`.
Jinja2's dot-attribute syntax tries `getattr(r, "values")` before falling
back to `r["values"]` -- and a `dict` genuinely has a `.values` attribute
(its own builtin method), so this always resolved to the *method object*,
never the list, and iterating it raised
`TypeError: 'builtin_function_or_method' object is not iterable`. This
line is unchanged from Phase c's original template, so the DNS zone page
has apparently never actually been rendered successfully end-to-end
before now -- Phase c's own checkpoint verified the record CRUD *API*
directly, not this UI page. **Fixed** by switching to explicit bracket
access (`r["values"]`), the same fix this codebase already needed for any
dict key that shadows a builtin dict method. Found only because this
feature's live verification actually loaded the rendered page in the
browser/via `curl`, not just called the API.

## Testing

`tests/test_dkim.py` (new): real `openssl` calls against a `tmp_path`-based
`dkim_base_dir` (fast, offline, no root needed — same "mock system/network
calls, not pure local computation" split the rest of this project's test
suite already uses), covering keypair generation/idempotency, the TXT
record format (no PEM markers/newlines — the literal thing DNS publication
needs), zone-managed vs. zone-unmanaged publish paths, and teardown.
`tests/test_handlers_dns.py`/`test_validation.py` extended for PTR/SRV/CAA
validators. `tests/test_handlers_mail.py` extended to confirm
`create_mail_domain` generates a key and `delete_mail_domain` tears it
down. 327 tests passing (up from 306).

## Live verification performed

1. Created a real account + primary domain, created its DNS zone via the
   new "Create DNS zone" UI button (previously required a raw API call —
   confirmed this was a genuine, now-fixed gap).
2. Enabled mail for the domain (`mail.create_domain`) — confirmed the
   response's `dkim` field showed `dns_published: true` and a real
   selector/record values.
3. `dig TXT default._domainkey.<domain> @<server-public-ip>` against this
   server's own PowerDNS resolver — confirmed the published record
   resolves and its `p=` value matches the on-disk public key
   byte-for-byte (`openssl rsa -pubout` on the stored private key,
   compared against the published TXT content).
4. `dig TXT <domain>` and `dig TXT _dmarc.<domain>` — confirmed SPF
   (`v=spf1 mx a ~all`) and DMARC (`v=DMARC1; p=none; ...`) both resolve.
5. Used the zone editor UI to add a CAA and an SRV record inline,
   confirmed both round-tripped through `dig CAA`/`dig SRV`, then deleted
   each via the new per-row delete button and confirmed they were gone.
6. Confirmed deleting the mail domain removes the DKIM/SPF/DMARC TXT
   records from the zone and the private key from disk.

See `docs/STATUS.md` for the consolidated Phase 3 summary.
