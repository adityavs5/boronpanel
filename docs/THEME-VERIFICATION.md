# Theme deployment verification

Both skins were deployed to `/opt/boron` on 2026-09-13 from the local `development` checkout. No GitHub commit, push, tag, release, or update-channel publication was performed.

## Results

| Check | Result |
|---|---|
| Production frontend build, Node 22 | Passed, Vite 5.4.21 |
| Browser suite | 15 passed |
| Existing installer/release/version regressions | 19 passed, 2 skipped |
| Shell syntax / git diff whitespace | Passed |
| Deployed index vs tested build | Identical |
| Health and UI requests | HTTP 200 |
| Admin login / identity | Login 303; whoami 200, admin |
| API, provisioning daemon, FileBrowser services | Active |
| Live Chromium verification, Evolution | Passed |
| Live Chromium verification, Paper Lantern | Passed |
| Uncaught browser errors / failed static assets in live flows | None |

The browser suite covers both roles, both skins, 320px and 390px phones, a 768px tablet, desktop screenshots, filtering, collapse persistence, theme switching without losing form input, dark mode, statistics errors/retry, cross-tab preferences, prepaint old/corrupt/unavailable storage, and keyboard navigation. Customer browser tests use deterministic API fixtures, not a newly provisioned hosting account.

Live checks used the actual administrator session and actual server statistics, opened Accounts, filled but did not submit a password form, changed skin/mode, checked Appearance and reload persistence, and captured phone layouts. The live test initially used an ambiguous password locator; it was corrected to target the input and the complete live run subsequently passed.

Existing API TestClient regression checks stalled in the restricted process sandbox and completed successfully outside it. The two skipped checks are the suite's optional environment-dependent checks. This is focused theme/setup validation, not certification of all hosting operations or the complete repository regression suite.

## Evidence

- `/root/boron-setup/themes-build.log`
- `/root/boron-setup/themes-browser-tests.log`
- `/root/boron-setup/themes-python-tests.log`
- `/root/boron-setup/themes-deploy.log`
- `/root/boron-setup/themes-live-tests.log`
- `/root/boron-setup/theme-live/`: desktop, mobile, Accounts, Appearance, and dark form screenshots for each skin.
- `frontend/playwright-report/`: generated browser report.

The original deployed application was archived to `/root/boron-setup/pre-themes-deployment.tar.gz`, excluding the virtual environment and frontend dependencies. Config, credentials, databases, and hosting data were not changed by the theme work.

Access the new home at `https://104.234.179.66:9443/app/overview` and Appearance at `/app/appearance`. The requested hostname still had no A answer from this server at final verification. The existing self-signed TLS certificate is unchanged.
