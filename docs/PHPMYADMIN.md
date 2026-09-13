# phpMyAdmin integration

phpMyAdmin uses its documented [signon authentication](https://docs.phpmyadmin.net/en/latest/setup.html#signon-authentication-mode).
Boron creates a temporary, database-scoped SQL identity and a short-lived token.
The signon script consumes the token under an inode lock and requires successful
atomic unlink before accepting it, so concurrent requests and stale open file
handles cannot both redeem the token. Session cookies are secure, HttpOnly and
SameSite Strict; session IDs rotate, and signon responses disable caching and
referrer forwarding. Temporary SQL grants escape underscore wildcard semantics.
The reserved SQL-login prefix cannot collide with normal hosting account prefixes.

Failed token persistence removes the temporary login and token file. Expired
credential cleanup retains failed revocations for retry. The existing five-minute
cleanup cron drops temporary SQL identities, including already-redeemed sessions.

The service has its own hostname/webroot challenge plan and certificate deployment
branch. Renewal hooks now use the stable `/opt/boron/scripts/ssl_deploy_hook.py`
path. Bootstrap gives the package directory a dedicated non-login metadata owner
because OLS rejects root docroot UIDs; the PHP worker does not own the package
directory or its root-managed code. Challenges remain root-owned. Hostname
collisions and unsafe document roots are rejected.

The database details button opens a tab during the user gesture, then navigates it
after token creation. A blocked popup falls back to the current tab. Failed token
requests close the temporary tab and show an error.

Validation before live enablement: 65 backend checks passed, including real
concurrent PHP redemption and a temporary MariaDB instance proving allowed database
access while denying an underscore-wildcard match and a foreign database. Four
browser cases passed across both themes/light-dark modes. Production build passed.
Evidence: `/root/boron-setup/pma-final-tests.log`, `pma-browser.log`, `pma-build.log`.
Live vhost/certificate/signon verification remains required.
