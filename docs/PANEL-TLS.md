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
