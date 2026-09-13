# Panel certificate automation (development)

The live panel was inspected at the start of this batch: its certificate was
self-signed for boron.sitecountry.com, and HTTP requests for that hostname returned
404 without a matching vhost. Trusted issuance is not yet complete.

New `panel_hostname` and `panel_acme_webroot` settings define a dedicated static
HTTP-01 challenge vhost. It does not replace the API listener, enable PHP, or share
customer document roots. Bootstrap applies its vhost and main mapping together
through the existing validated OLS configuration transaction. Hostname collisions
with customer sites and other panel infrastructure are rejected. The customer
creation handler also reserves configured infrastructure names.

`daemon.panel_tls.issue_certificate(email)` bootstraps the route and invokes certbot
with a webroot, explicit certificate name, keep-until-expiring and a deploy hook at
`/opt/boron/scripts/panel_ssl_deploy.py`. The stable /opt/boron link prevents renewal
configuration from depending on a pruned release directory. Certbot persists hook
configuration for its existing renewal cron. See the [Certbot user guide](https://eff-certbot.readthedocs.io/en/latest/using.html).

The standalone deploy hook accepts only the expected certificate lineage, verifies
exact hostname coverage, validity and matching private key, stages group-readable
files for boron-api, restarts its service, and verifies that the local TLS listener
serves the expected leaf certificate. Failure restores the prior pair and restarts
the previous configuration. No customer web server stop is used for issuance.

Remaining: integrate settings/issuance into the admin access configuration, deploy
and verify the HTTP challenge route, issue the real certificate, verify public trust
and renewal, and implement the requested separate configurable panel ports (default
2222). This is preparation, not completion of the panel SSL requirement. The hook's
listener check currently uses api_bind_port and must follow the forthcoming port
configuration work. A successful certbot command may keep an existing certificate;
the installed listener still needs explicit verification during integration.

Validation: 105 OLS/domain/certificate regression checks passed. A final focused
four-test run also passed after adding the issuance helper, verifying hostname,
validity/key matching, rollback, HTTP-only route rendering and renewal-hook command
construction. Logs: `/root/boron-setup/panel-tls-tests.log` and
`/root/boron-setup/panel-tls-final-tests.log`. These are development tests; real OLS
route deployment and ACME issuance remain pending.

## Live ownership compatibility fix

The first real OLS challenge-vhost validation rejected the root-owned document
root; ConfigWriterMulti restored the previous configuration. OLS checks the
owner even for this static, script-disabled vhost. Bootstrap now uses a dedicated
`boron-acme` system identity with a nologin shell for the read-only document root.
The `.well-known` and challenge directories remain root-owned and readable by the
worker, without giving hosting accounts or the web worker write access. Existing
unsafe service identities are rejected. The corrected vhost passed real OLS
validation and its challenge was fetched through the public hostname.

Focused OLS/TLS regressions passed 84 tests, including the service identity checks.
Trusted production certificate issuance and public HTTPS trust verification passed
on port 9443. Renewal rehearsal and subsequent port migration are being recorded
in `/root/boron-setup/panel-tls-live.log` and the live port checks.

## Live issuance and renewal verified

Production issuance, system-trusted HTTPS and the renewal dry run with
`--run-deploy-hooks` succeeded. Certbot's log confirms both initial and rehearsal
calls to `/opt/boron/scripts/panel_ssl_deploy.py --hostname boron.sitecountry.com`.
The final listener is now 2222, still serving the trusted certificate (issuer
Let's Encrypt YE1, expires 2026-12-12). Administrator login and both live theme
browser checks passed with normal TLS verification. The existing renewal cron
runs at 03:17 and 15:17 UTC. Evidence: `/root/boron-setup/panel-tls-live.log` and
`/root/boron-setup/panel-access-live-browser-final.log`.
