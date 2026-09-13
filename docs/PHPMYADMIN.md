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
Live verification passed on 2026-09-13: trusted HTTPS, certificate renewal with
its deployment hook, and actual customer WordPress table access from both themes.
Consumed tokens returned HTTP 403 on replay. Evidence: `pma-live-setup.log` and
`pma-live-success.log` under `/root/boron-setup`.

Ubuntu package integration: bootstrap reads the literal `configFile` declaration
from `libraries/vendor_config.php` and writes that configuration location. It adds
the token directory to the existing OLS namespace configuration in the same
rollback-capable config transaction. The root:www-data 0770 directory continues
to exclude hosting account identities. phpMyAdmin alone permits root-managed
package symlinks outside its docroot, as Debian serves shared JavaScript that way.
Customer vhosts retain restrained mode. On an existing persisted service namespace,
refresh it with LiteSpeed’s `unmount_ns -u 33` and recycle the www-data PHP workers;
never lower the customer namespace minimum. See the upstream
[namespace documentation](https://docs.litespeedtech.com/lsws/namespaces/).

The final OLS/phpMyAdmin regression run passed 91 checks (`pma-assets-tests.log`).
