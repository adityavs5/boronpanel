# Shared and separate panel listeners (development)

The API service now starts through `python -m api.serve`. Its default admin port is
2222 (`api_bind_port`); the customer port (`api_customer_port`) inherits it unless
configured separately. Shared ports bind once. Separate ports use two sockets in
one API process, avoiding a duplicate application instance. Ports must be integers
from 1024 through 65535, matching the unprivileged service. A failed socket bind
closes all listeners already created before propagating the error.

On separate listeners, authenticated access checks the actual ASGI server socket
port rather than the Host header. Guards cover password login, 2FA completion,
session/token dependencies and terminal WebSockets. An admin impersonating a
customer remains on the admin listener while retaining customer-level application
permissions. Shared-listener behavior is unchanged. Public pages remain reachable
on either port. A wrong-listener response names the correct role and port.

Firewall protection includes both ports. Fresh-installer required/open ports and
completion links now use 2222. Update health checks already follow api_bind_port.
The panel certificate deploy hook verifies the expected leaf certificate on both
configured listeners after restart, before treating deployment as successful.

The live server remains on 9443. The administrator UI, persisted jobs, conflict
checks, firewall admission and rollback are now implemented in development as
described below. TLS issuance and live migration/verification are still required.

Validation: the first 44 listener/firewall/terminal/TOTP checks passed. Final focused
and installer/update regression checks passed 74 tests with one optional test skipped.
Tests include real sockets, cleanup after partial binding failure, forged Host
headers, session/token role checks, customer-port firewall protection and TLS
verification on both ports. Evidence: `/root/boron-setup/panel-port-tests.log` and
`/root/boron-setup/panel-port-final-tests.log`. Live port migration is not yet performed.

## Port transaction foundation

`daemon/panel_config.py` now provides the change worker's transaction: strict port
validation, real bind conflict probes, a private configuration backup and a file
lock against concurrent changes. It preserves unrelated TOML and file ownership
and permissions. New TCP ports are admitted through UFW before restarting the API.
Both new listeners must return a healthy response with the exact installed TLS
certificate. Failed restart or health checks restore the previous configuration
through ConfigWriter, restart it and check the previous listeners. The surviving
daemon's runtime settings change only after success.

Existing firewall rules are never removed. New admissions are retained after a
failed change; this avoids removing a rule another administrator could depend on.
External/provider firewalls remain outside this local transaction. Bind probes
reduce conflicts but cannot reserve a port throughout the service restart; the
post-restart checks cover that race with rollback.

The asynchronous job/API/UI integration and process-interruption recovery are
implemented below. Live migration and external connectivity checks remain pending.
No live settings were changed by this work. Validation includes
real local socket conflicts, TLS health/certificate rejection, serialization,
metadata preservation, firewall failures and rollback; evidence is in
`/root/boron-setup/panel-config-final-tests.log`.

## Administrator workflow

The administrator-only Panel Settings page now offers independent admin/customer
ports, address previews, an explicit restart confirmation, a link to the new
administrator address and recent change history. It is available in both theme
catalogs and dashboard search (including port/listener synonyms). Customer
navigation does not expose it; both read and mutation endpoints require admin.
Polling stops after the requested job reaches a terminal state. Cross-port
navigation uses an explicit link, so browser same-origin protections remain intact.

`panel_config_jobs` stores requested ports, initiating administrator, status,
result and timestamps. The API queues work and returns 202; a daemon worker owns
the restart. A second request is rejected while a job is pending or running.
Before mutating configuration the worker saves a root-private recovery journal;
the API-readable database never stores its contents. At daemon startup, queued
jobs resume, and interrupted running jobs inspect actual configuration and HTTPS
health. A verified requested configuration is recorded as complete; otherwise the
journal restores the previous configuration using ConfigWriter. Recovery begins
after the RPC socket is available. Failed recovery is reported explicitly.

The focused backend suite passed 48 checks, including admin/customer API isolation,
strict request validation, job lifecycle, duplicate-worker prevention, startup
recovery, real bind conflicts, TLS certificate/health rejection and transaction
rollback. Four initial browser checks passed across both themes/light-dark modes,
including request payloads, failure feedback, new-address links and mobile width.
The page is code-split and adds no dependencies.

Development only: live deployment, real service restart/recovery exercises,
external access verification, the change to port 2222, and trusted TLS issuance
are still pending. Source tests do not prove those deployment outcomes.

Final verification after mobile heading/polling polish: production build passed
(PanelSettings chunk 2.17 kB gzip), and all four theme/browser cases passed again.
Logs: `/root/boron-setup/panel-jobs-final-tests.log`,
`/root/boron-setup/panel-settings-final-build.log`, and
`/root/boron-setup/panel-settings-browser-final.log`. The initial sandboxed API
runner was terminated after stalling in TestClient setup; the final complete suite
ran successfully with local socket access and isolated databases.

## Live verification

The development build is deployed. Successful persistent jobs 1, 2 and 3 exercised
9443 → shared 2222 → admin 2222/customer 2223 → shared 2222. Real password login
checks accepted each intended role and rejected the wrong role on separate
listeners. Final settings and the actual listening socket both show shared 2222;
WordPress inventory remained accessible. The test-only 2223 firewall opening was
removed. Both live themes rendered the expected settings/history with trusted TLS.
Evidence: `/root/boron-setup/panel-ports-live-resume.log` and
`/root/boron-setup/panel-access-live-browser-final.log`. Runtime failure rollback
and interruption are covered by isolated tests; they were not induced on the live
panel. The OLS challenge configuration did exercise real rollback on its initially
rejected ownership setting before correction.
