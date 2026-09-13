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

The live server remains on 9443. Administrator configuration UI, persisted change
jobs, conflict checks, firewall admission, rollback after failed listener changes,
TLS issuance and live migration/verification are still required. Do not interpret
this startup/authorization foundation as the finished configurable-port feature.

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

This is not yet an exposed admin feature. The asynchronous job/API/UI integration,
process-interruption recovery, live migration and external connectivity checks
remain pending. No live settings were changed by this work. Validation includes
real local socket conflicts, TLS health/certificate rejection, serialization,
metadata preservation, firewall failures and rollback; evidence is in
`/root/boron-setup/panel-config-final-tests.log`.
