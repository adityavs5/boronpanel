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
