# Independent domain and subdomain sites

New addon domains and subdomains use
`/home/<account>/<full-domain>/public_html`. The primary site retains
`/home/<account>/public_html`. Existing Domain.docroot values are preserved; this
change does not move any existing files. Each site uses its own existing OLS vhost,
with the account default PHP runtime unless the site has an override.

The Add domain dialog offers Domain or Subdomain. Subdomain creation selects an
owned parent and a name such as blog, previews the complete hostname, and submits
an ordinary domain resource of kind subdomain. The backend checks parent ownership
independently of the UI. A managed DNS zone receives an A record using the configured
server public IP. External DNS still requires a record at its provider. Matching
zones are selected most-specific first; creation is rejected if that zone belongs
to another account. Existing DNS compensation behavior remains in place.

Nested directories keep private account ownership. An explicit traverse ACL on
containing directories lets the OLS worker reach the document root; the root itself
retains web-server read/default ACLs. Real filesystem testing verified that UID 65534
can read a site file and unrelated UID 65532 cannot.

Validation: 20 existing domain tests passed, plus four new ownership/root/zone checks
and one real ACL isolation test. Production build passed. Browser artifacts use
`/root/boron-setup/subdomain-*`; backend/build evidence uses
`/root/boron-setup/domain-roots-*`. Live deployment and serving/DNS verification are
still pending as part of the full product goal.

Final browser verification passed all four theme/light-dark combinations, including
mobile dialog rendering, parent selection, hostname preview and the submitted
subdomain payload. Evidence: `/root/boron-setup/subdomain-browser-final.log` and
`/root/boron-setup/subdomain-ui-final`. The earlier test locator failure was corrected
to include the field's accessible required-label suffix; no application bypass was
introduced to make the test pass.
