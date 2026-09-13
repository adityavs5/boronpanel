# PHP versions and limit templates

The PHP page provides an account default and per-site selectors. New domains inherit
the default unless explicitly overridden. Clearing a site's override follows future
account default changes. Selectors use the server's configured PHP version list.
Existing backend handlers validate ownership and versions, regenerate configuration,
and recycle PHP workers.

Custom is the default limits selection. Choosing a template fills the editable form;
no server changes happen until Save. Editing any field switches the selection back
to Custom. Templates change resource limits only and preserve error configuration,
timezone and other unrelated directives. Saved values use the existing validated
PHP settings endpoint; preset names are convenience labels, not a second settings
store. After reloading, the saved values appear in Custom.

| Limit | Lite | Moderate | Max |
| --- | --- | --- | --- |
| Memory per request | 128M | 256M | 512M |
| Single uploaded file | 32M | 64M | 256M |
| Total POST body | 40M | 80M | 320M |
| Execution time | 30 s | 120 s | 300 s |
| Input parsing time | 60 s | 120 s | 300 s |
| Input variables | 1,000 | 3,000 | 5,000 |
| Files per upload request | 20 | 20 | 50 |

These are Boron presets, not PHP defaults or vendor-prescribed values. Lite targets
small sites; Moderate provides room for typical WordPress plugins; Max accommodates
larger imports/stores at a higher potential per-request cost. They do not increase
account-level CPU/memory allocations. POST limits leave overhead beyond a single
file's upload size, and memory limits exceed POST limits, following the relationships
in the [PHP core directives manual](https://www.php.net/manual/en/ini.core.php).

Development implementation; live deployment and served-PHP verification are pending.

Backend verification passed 97 checks covering PHP directive validation/persistence,
presets, account defaults and domain overrides. Presets use the same configuration
and worker-recycling path as custom edits. Production build passed without adding
frontend dependencies. Logs are retained under `/root/boron-setup/php-controls-`.

The final four browser checks passed in both themes and light/dark modes. They verify
Custom as the initial selection, template values, manual edits returning to Custom,
explicit save payloads, per-site overrides and returning to the account default.
Desktop/mobile screenshots were captured under `/root/boron-setup/php-controls-ui-proof`;
the mobile heading layout and form rendering were visually reviewed. The final PHP
page chunk is 5.25 KB gzip. Live deployment and real served-runtime checks remain pending.
