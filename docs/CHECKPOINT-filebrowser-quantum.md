# CHECKPOINT — FileBrowser Quantum (replace custom file manager)

Goal: replace Forgehost's custom file manager with **FileBrowser Quantum
v1.4.0-stable** (github.com/gtsteffaniak/filebrowser). Keep Monaco for code
editing. Proxy-header auto-login, per-account isolation, account-lifecycle
source management, retire the old file manager after verification.

This file is the running task list + decision log for this build.

---

## Step 0 — research & design (DONE)

All of the following was verified **empirically against the real binary**
(downloaded linux-amd64 v1.4.0-stable, sha256 `f104afa1398a9daf629113d5250951159e389837624b6924abc6e8ca845c62ac`)
run as a disposable instance on 127.0.0.1:8088 in the scratchpad — not
assumed from docs.

### Key finding: the isolation model is a SINGLE `/home` source, not per-account sources

The goal says "add a FB Quantum *source* (name: username, path: /home/{username})
on create." FileBrowser Quantum's actual model makes per-account sources the
**wrong** primitive, for a concrete reason confirmed in the source and live:

- A source is granted to a user either by `config.defaultEnabled: true` (grants
  it to **every** new user → total cross-account leakage) or by a **per-user DB
  record** binding that user to that source. There is **no** "auto-bind source
  X to the same-named user X" convention.
- Per-user DB records can't be written safely while the server runs (single
  DB), and proxy-auth users are auto-provisioned at first request anyway.
- **Config is NOT hot-reloaded** (no fsnotify/watch in the binary; confirmed) —
  a per-account source would require restarting FB Quantum on every account
  create/terminate.

The mechanism that *does* give clean per-account isolation is **one shared
source at `/home`** with:

```yaml
sources:
  - path: /home
    name: home
    config:
      defaultEnabled: true
      createUserDir: true       # scope = defaultUserScope + "/" + <proxy username>
      defaultUserScope: "/"
```

`createUserDir` + proxy auth means a proxy-authenticated user `alice` is
auto-created and auto-scoped to `/home/alice`, unable to see `/home` or any
sibling. Deviation from the goal's literal "per-account source" wording is
documented here per ARCHITECTURE.md's "deviations go in CHECKPOINT with
reasoning" rule.

### Isolation — empirically proven (disposable rig, users alice & bob)

| Probe (as `alice`) | Result |
|---|---|
| no `X-Fb-User` header | **HTTP 401** (denied) |
| list `path=/` | own dir only (`secret_alice.txt`), `scopes:[{name:home,scope:/alice}]` |
| `path=/bob`, `/../bob`, `/../../etc`, `/../bob/secret_bob.txt` | 404, clamped inside `/home/alice` |
| `path=/..` | clamped to own scope root (own dir) |
| URL-encoded `%2e%2e%2fbob` | **HTTP 403 access denied** |
| `source=bob` / `source=/home` / `source=root` | 500 "could not get index" (only `home` source exists) |
| `GET /api/tools/search?query=secret` | only `secret_alice.txt` — **never bob's** |
| `GET /api/tools/search?query=uploaded` (bob) | `[]` — bob never sees alice's upload |

Search is scope-filtered even though the index spans all of `/home`.

### Ownership wart — confirmed, mitigation chosen

FB Quantum is one long-running process; it can't per-request drop to the
account uid the way the custom manager (root daemon) did via chown. Under
Forgehost's 711-home / 750-docroot perms model it therefore must run **as
root** to read+write across account homes (consistent with ARCHITECTURE §10's
"only the daemon (root) touches account-owned files" decision). Consequence,
confirmed live: files/dirs FB Quantum creates are **root-owned** (`root:root`),
not account-owned.

**Mitigation (chosen): default POSIX ACL granting the account rwX on its own
home**, applied by `fb.add_source`. A named-user ACL entry `user:<acct>:rwx`
grants the account full access to files even when the file's *owner* is root,
and a `default:` ACL makes new (FB-created, root-owned) files inherit it — so
uploads/edits made through FB Quantum remain fully usable by the account's own
PHP/FTP/SSH. This is localized to the FB feature (no change to the core
provisioning perms model), safe (an account gaining ACL access to *its own*
home is not an isolation expansion), and idempotent. Documented as the honest
cost of the drop-in.

### Config schema (exact, from `structs.go`)
`server.port` (int), `server.listen` (bind host), `server.baseURL`,
`server.database`, `server.cacheDir`, `server.sources[].{path,name,config}`,
`server.sources[].config.{defaultUserScope,defaultEnabled,createUserDir,...}`,
`auth.methods.proxy.{enabled,header,createUser}`, `frontend.name`,
`server.logging[].levels`. Trusted header configurable → set to `X-Fb-User`.

### Auto-login / proxy — design (ARCHITECTURE §2 adaptation)

The panel (`forgehost-api`, :9443) is deliberately **not** fronted by OLS
(§2), and it is the process that holds the authenticated session. So the
trusted-header injection happens **in forgehost-api**, not OLS — this is the
process that actually knows who the user is, which is strictly stronger than an
OLS-level guess. Documented deviation from the goal's "OLS injects X-Fb-User".

Flow:
1. Customer clicks Files → full-page nav to
   `GET /api/v1/accounts/{me}/files/launch`. Admin clicks File Manager on an
   account → same launch URL for that account.
2. `launch`: `require_account_access(identity, target)`; `call_daemon("fb.open",
   …)` (validates + **auto-audits** who opened whose files — admin access
   logged for free via the daemon audit path); sets a **signed** `fb_target`
   cookie (itsdangerous, short TTL, httpOnly/Secure/SameSite=Lax, path=/files);
   302 → `/files/`.
3. Proxy `/files` + `/files/{path}` (in forgehost-api): resolve panel identity
   (401/redirect if none); read+verify `fb_target` cookie → target;
   `require_account_access(identity, target)` **on every request** (defense in
   depth); **strip all client-supplied `X-Fb-User`**; inject
   `X-Fb-User: <target>`; stream to `http://127.0.0.1:8088/files/…` and stream
   the response back.

Security invariants: FB Quantum only ever reachable via this authenticated
proxy (loopback bind, never public); client can never set the identity header;
per-request re-authorization; every open audited.

---

## CRITICAL finding from live verification (2026-07-09) — cross-tenant leak, FIXED

Standalone live verification on this box (two **real** disposable Linux users
`fbva`/`fbvb` with real 711-home perms, real `setfacl`) caught a security bug
the scratchpad rig (single fake user) could not:

**FileBrowser Quantum runs as root and explicitly chmods every file it creates
to `0644` (and dirs `0755`) — world-readable.** Under Forgehost's 711 account
homes (world-traversable, required so OLS's `nobody` can reach public_html), a
`0644` file is readable by **every other account on the box** via the
filesystem. Confirmed live: `fbvb` could `cat` a file `fbva` uploaded through
FileBrowser. The old manager avoided this by writing `0640` account-owned files.
`umask` does **not** help (FB chmods explicitly, overriding it).

**Fix (config-only, no change to the locked §6 perms model, no OLS risk):**
FB Quantum exposes `server.filesystem.createFilePermission` /
`createDirectoryPermission`. Set to **`660` / `770`** so created files/dirs have
`other = none` (leak closed), while the account's own uid keeps full read+write
via the default ACL `add_source` applies (its mask is the group triad — `rw` for
files, `rwx` for dirs), and `nobody` still serves public_html files via that
dir's own nobody ACL (masked to read). **Re-verified live**: `fbvb` denied
read/enter on `fbva`'s FileBrowser-created files/dirs; `fbva` can
read/write/create/delete them; isolation (list/traverse/`..`/search/alt-source)
all clamped to the account's own scope.

## Task list

- [x] 0. Research + design, disposable-rig verification
- [x] 1. Install: binary → /usr/local/bin (`scripts/install_filebrowser.sh`, sha256-pinned), systemd unit, config render (`daemon/filebrowser.py`), `fb.bootstrap`
- [x] 2. Daemon ops: fb.add_source / fb.remove_source / fb.status / fb.open (+ ownership ACL) + CREATE/TERMINATE hooks (server.py); startup bootstrap
- [x] 3. API: launch endpoint + streaming proxy + header strip/inject + audit + /files CSP (api/routers/filebrowser.py, api/main.py)
- [x] 4. Frontend: customer Files → launch redirect; admin AccountDetail "File Manager" button
- [x] 5. Tests: 22 (fb ops + config incl. 660/770 + proxy header sanitization + launch/audit + unauth) — all green
- [x] 6. Live verify (2 real disposable accounts): isolation A≠B (list/traverse/search), upload/delete, ownership-mitigation ACL, **caught+fixed the 0644 leak** above. Download/rename use FB API params the proxy forwards verbatim → exercised via the real SPA in the end-to-end (post-deploy) pass.
- [x] 6b. **End-to-end through the panel — 21/21 checks PASS** (deployed 2026-07-09, user-approved). Against two real disposable accounts driven through the live daemon+panel: account.create fires the CREATE_HOOK → `add_source` ACL applied automatically; customer auto-login (launch → signed cookie → proxy) sees only its own home; customer launching/forging-cookie for another account → 403 both ways; admin sees any account's files via the same flow; upload via the proxy lands `0660` (no cross-tenant read, owner rw via ACL); delete works; terminate fires `remove_source` + cleans up. (`scratchpad/e2e_verify.py`.)
- [x] 7. Retired the custom file manager: removed the `file.*` ops + `filemanager` import from server.py, removed `api/routers/files.py` + its registration, trimmed `daemon/filemanager.py` to only the shared jail helpers (`_resolve`/`_account_home`) that fileauth/composer/disktree/gitrepo reuse, replaced the customer `Files.jsx` with a launch-redirect, deleted the two `test_filemanager*` files. (Monaco/`CodeEditor.jsx` kept — FB Quantum's built-in editor handles code editing.)
- [x] 8. STATUS.md updated; full suite **1446 passed / 0 failed** after retirement; retirement **redeployed** to /opt/forgehost, provisiond+api restarted (0 restarts). Confirmed live: old REST endpoint 404s, old `file.list` RPC is "unknown op", `/files` proxy + launch alive, `fb.status` active. **DONE.**

## Post-rollout bug (2026-07-09, user-reported): stuck on the loading spinner — FIXED, browser-verified

**Symptom:** opening the file manager showed FB Quantum's loading spinner
forever. **Root cause (found with a real headless-Chrome probe, not curl —
curl checks all returned 200 so the API path looked healthy):** FB Quantum's
SPA boots from an **inline** `<script>` (`window.globalVars = {...baseURL...}`)
in its index.html, and the `/files` CSP (`script-src 'self'
'wasm-unsafe-eval'`) blocked inline execution → `TypeError: Cannot read
properties of undefined (reading 'baseURL')` → app never rendered.

**Fix (kept the no-unsafe-inline posture):** the proxy now buffers HTML
responses (~15KB shell only; everything else still streams) and emits a
**per-response CSP carrying the sha256 hashes of the page's actual inline
script(s)** (`api/routers/filebrowser.html_csp`). A hardcoded hash would have
silently re-broken on every FB upgrade — the inline script embeds the FB
commit SHA. The main-app middleware now defers to a CSP already set by the
proxy. Attacker-injected inline scripts (e.g. XSS via hostile filename —
the reason /files must NOT get blanket `unsafe-inline`: an admin browsing a
hostile account's files shares the panel origin) still never match a hash.

**Also fixed in the same pass:** `userDefaults.permissions.realtime` flipped to
`true` — FB's live updates are **SSE** (plain HTTP `GET /api/events`, not a
websocket), which streams through the proxy fine; with it disabled the SPA
looped on a 403 every 5s showing "connection lost" toasts.

Verified with puppeteer against the live panel: login → launch → FB renders
the account's real files, zero console errors (one pre-existing cosmetic
`/favicon.ico` 404 on the panel origin). Tests: +3 CSP regression tests
(26 fb tests total). Disposable `qauiprobe` admin removed after the probe.

**Deploy done (user-approved):** binary installed (sha256-pinned),
`forgehost-filebrowser.service` active+enabled on 127.0.0.1:8088, config at
`/etc/forgehost/filebrowser.yaml` (source `/home`, 660/770, proxy header
`X-Fb-User`), provisiond+api restarted. Rollback snapshot at
`/opt/forgehost.pre-filebrowser`. Existing accounts get the ownership ACL via
`fb.refresh_all` (run once at rollout).
