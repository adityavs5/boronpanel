# DirectAdmin source backup compatibility verification

Verified 2026-09-24 against the authorized mercury source using adzwerco.

The source rejected user backups with `User backups have been disabled` and an allowance threshold of 0%. The migration now uses a one-account administrator backup when that specific rejection occurs. It keeps saved backup settings with `write_backup_conf=no` and uses a unique directory beneath the administrator's home. It does not change the source restriction, accounts, or DNS.

Live verification: administrator backup accepted; resulting 211144-byte zstd archive downloaded; candidate pinned HTTPS transport located and transferred the same archive with matching size; original backup location unchanged. Private extraction and normalization passed (one domain, zero databases). No destination account was created by this verification. The source test archive was retained.

Zstd output is bounded before passing through existing tar path/member/type checks. The exact DirectAdmin `domains/<domain>/private_html -> ./public_html` alias is omitted, never followed or materialized; other links remain rejected. Fresh installers include zstd.

Focused checks: 85 backend tests passed together; the added fallback orchestration regression then passed with the 25-test DirectAdmin suite (86 distinct cases covered). Database version compatibility was not exercised by the live sample because it contained no database.

Current limitation: administrator fallback requires the configured local backup location to be beneath `/home[0-9]*/<admin-login>` so the transfer can address the archive through that administrator's File Manager. An external/FTP destination is rejected explicitly rather than guessed.
