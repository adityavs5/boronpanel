# v3.1.2 re-review follow-up

This batch addresses the new findings and refines the three reference pages. It does not constitute a full QA sign-off or complete migration of every inner page. Existing accounts and reviewer test data are retained. No release is published.

## Changes

- **H3/N1:** a disabled, stopped Redis instance no longer blocks username rename. Its ID, memory limit and on-disk files are retained; the username-dependent unit and configuration are rewritten and the old configuration removed. Enabled or still-running Redis refuses rename before identity changes. Redis mutations share the existing account-operation lock.
- **N1 UI:** disabled retained instances show Enable Redis, retain their memory setting, and disable Flush. Disable copy explains that files/configuration remain but memory-only cache keys are lost. A stop must be confirmed before persisting the disabled flag.
- **N2 diagnostics:** self-test lists actual PHP worker IDs and observed PID namespace results. A shared host namespace still fails. A vanished worker root or PID namespace is incomplete; a still-live root missing its account home and any visible peer home still fail.
- **N2 enforcement:** tenant external processors explicitly use `runOnStartUp 0`, retaining `autoStart 1`. This prevents eager server-context launch without vhost isolation. Shared PHP, Roundcube and phpMyAdmin settings remain unchanged. Tenant pools follow the web-server lifecycle, so opcode caches warm again after restart. The user approved live deployment and the runtime checks below passed.
- **N2 teardown compatibility:** an exact native `lsnsctl` “User is not mounted” result is now idempotent success. Request-driven Bubblewrap need not leave a cached native mount. Busy, permission and additional-error results still prevent account deletion and retain its UID.
- **N3:** DNS record creation shows the exact resulting hostname. Dotted relative names need explicit confirmation; an absolute name outside the zone is rejected in the form. Legitimate multi-label relative names remain available without changing the DNS API.
- **N4:** Accounts uses styled Radix selects, a column picker, one visible Manage action and contextual row menus that retain account scope.
- **Theme review:** Paper has larger text and controls, a single-column account workspace and top settings tabs; Evo retains compact rows and side settings navigation. Account tabs have stronger text and checkboxes have a clearer styled boundary. Dashboard bodies and branding remain unchanged.

## Live findings and limits

**H2 verified:** a newly created disposable account with a blank password successfully signed in using the returned generated password in a fresh browser. Login, logout and re-login passed twice. Cross-account and administrator API requests returned 403.

**First domain verified:** adding an explicit addon domain to the domainless disposable account made it primary and populated the account primary-domain field through the public API.

**N2 reproduced:** amichi passed the initial inspection, and a fresh account passed six checks across three cold PHP launches. A later disposable-account rename reproduced the reported pattern: home and peer checks pass, but a genuine account-UID lsphp worker under OpenLiteSpeed's lscgid runs in the host PID namespace alongside private workers. Instrumenting the rename traced it to vhost refreshes; three consecutive self-tests failed on that worker. This supersedes the earlier inconclusive first fresh-account assertion.

The tenant processor template omitted `runOnStartUp`; upstream defaults that field to detached/eager mode 3. A server-context launch uses the server isolation policy, while request workers use the domain policy. Explicit request-driven startup fixes the shared tenant rendering boundary and covers every effective PHP version. [OLS launch source](https://github.com/litespeedtech/openlitespeed/blob/master/src/extensions/localworker.cpp), [startup configuration](https://github.com/litespeedtech/openlitespeed/blob/master/src/extensions/localworkerconfig.cpp), [attached/detached lifecycle](https://docs.openlitespeed.org/config/php/detached/).

**H3/N1 live evidence:** after the startup fix, the disposable account completed a rename round trip. UID/GID, marker bytes, Redis ID and its 96 MB limit were retained; old units/configurations were removed. Redis could be re-enabled and disabled at each name. PHP served and all runtime isolation checks passed at each name. A fresh generated-password browser login also passed after rename.

**N2 live verification:** two real OLS reloads produced no eager tenant PHP workers before a request. Requests launched private workers in the account cgroup. A 10-second PHP response survived graceful reload. Domain PHP 8.2 override and return to inherited PHP 8.3 both served the expected version and passed isolation. The original rename/reload failure no longer reproduced. After cleanup, amichi passed with seven observed PHP workers, all private; its homepage and uncached PHP returned HTTP 200, webmail returned 200, and phpMyAdmin its normal 302.

**Cleanup:** only this run's disposable account, qarev01a (temporarily qarev01new), was terminated. Its Linux users/homes and Redis units/configurations were removed; other account IDs/names were preserved. The ordinary termination audit tombstone remains. Existing reviewer data, old redirects and DNSB configuration were not changed.

Other items marked not re-tested in the supplied report are not newly certified by this focused batch. No hard OLS restart under traffic, exhaustive load test or SSH/SFTP namespace change is claimed.

## Validation and deployment

- Production frontend build and DNS owner Node contracts passed.
- Account/Redis/isolation/plan/domain daemon run: 129 passed, one sandbox UID/chown restriction. That ACL case passed on the real host: 130 distinct cases passed.
- `pytest -q tests/test_ols.py tests/test_nsisolation.py`: 135 passed. After diagnostic review, `pytest -q tests/test_nsisolation.py`: 44 passed. After native-cache compatibility correction, the same namespace suite passed 49 cases, including absent-cache, busy and permission outcomes. Four pytest temporary-directory cleanup warnings did not fail tests. These runs overlap.
- Browser suite: 71 passed initially; four isolation-display cases had an incomplete API fixture. All four passed after correcting it: 75 distinct cases passed. Both themes/modes, three viewport widths, keyboard navigation, layout, request contracts and scoped controls are covered.
- Live reference inspection: 24 views passed across three pages, both themes/modes, desktop and mobile, with no page overflow, failed requests, runtime errors or configuration writes. Screenshots were inspected visually.
- Eight live Accounts views and eight live Redis views passed in explicitly asserted theme/mode contexts at desktop/mobile widths: 40 live UI views in total. The first Redis captures used a mounted preference store incorrectly and were replaced by the verified fresh-context matrix; they are not counted twice.

Protected UI/runtime evidence and credentials remain outside Git under `/root/boron-setup/re-review-20261001`. Security evidence uses the Codex Security target-bound artifact collection; no scan or release is implied.

**Deployment boundary:** UI, Redis/rename fixes, per-worker diagnostics, teardown compatibility and the tenant startup template are installed on the current v3.1.2 server. The user explicitly approved graceful OLS reload and narrowly scoped retirement of confirmed legacy tenant workers sharing the host PID namespace. None remained at the deployment retirement step, so no legacy worker was killed. All live harness gates passed. No GitHub push, tag or release is published.

The original static preview backup remains under `/root/boron-setup/ui-refresh-20261001/static-before-preview`. The three daemon files and original OLS template have a separate backup under `/root/boron-setup/re-review-20261001/backend-before-review`. Restoring daemon files requires restarting boron-provisiond and verifying an actual RPC operation, since database-only account reads can succeed before the daemon is ready. Template recovery requires validated OLS regeneration/reload; reverting the old startup policy would reintroduce the reported defect. Installation checks source hashes and compilation and rolls back on readiness failure. No release version or API route changed.

The [isolation fix report](/root/.codex/state/plugins/codex-security/scans/boronpanel-security-audit/artifacts-5b29d34f576adb030fc5db7258d0d8444efd5692f3a54e1b649c633298442196/artifacts/03_remediation/fix-report.md) records the invariant, reproduction, independent review, changed files, ordered test gates and retained evidence.

## Updated screenshot samples

These server-local images show the current deployed preview; the original review gallery records the earlier shell batch.

| Page | Evo | Paper |
|---|---|---|
| Account detail, light desktop | [Evo](/root/boron-setup/re-review-20261001/live-account-evolution-light-1280.png) | [Paper](/root/boron-setup/re-review-20261001/live-account-paper-lantern-light-1280.png) |
| Panel settings, dark desktop | [Evo](/root/boron-setup/re-review-20261001/live-settings-evolution-dark-1280.png) | [Paper](/root/boron-setup/re-review-20261001/live-settings-paper-lantern-dark-1280.png) |
| Domains, light mobile | [Evo](/root/boron-setup/re-review-20261001/live-domains-evolution-light-375.png) | [Paper](/root/boron-setup/re-review-20261001/live-domains-paper-lantern-light-375.png) |
| Accounts, light desktop | [Evo](/root/boron-setup/re-review-20261001/live-accounts-evolution-light-1280.png) | [Paper](/root/boron-setup/re-review-20261001/live-accounts-paper-lantern-light-1280.png) |
| Redis, dark mobile | [Evo](/root/boron-setup/re-review-20261001/live-redis-evolution-dark-375.png) | [Paper](/root/boron-setup/re-review-20261001/live-redis-paper-lantern-dark-375.png) |
