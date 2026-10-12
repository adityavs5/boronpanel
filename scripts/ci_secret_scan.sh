#!/usr/bin/env bash
set -euo pipefail

[[ -f .secrets.baseline ]] || {
  printf 'ERROR: .secrets.baseline is missing; generate and audit it before enabling CI.\n' >&2
  exit 2
}

mapfile -d '' files < <(
  find api daemon shared scripts tests testsupport deploy templates docs .github \
    frontend/src frontend/e2e -type f \
    \( -name '*.py' -o -name '*.sh' -o -name '*.js' -o -name '*.jsx' \
       -o -name '*.mjs' -o -name '*.md' -o -name '*.toml' -o -name '*.j2' \
       -o -name '*.service' -o -name '*.socket' -o -name '*.timer' \
       -o -name '*.conf' -o -name '*.cron' -o -name '*.logrotate' -o -name '*.json' \) \
    -not -path '*/__pycache__/*' -print0
  find . -maxdepth 1 -type f \
    \( -name '*.py' -o -name '*.md' -o -name '*.txt' -o -name '*.ini' \
       -o -name '*.toml' -o -name '*.json' \) \
    ! -name '.secrets.baseline' -printf '%P\0'
  printf '%s\0' frontend/package.json frontend/vite.config.js \
    frontend/playwright.config.js frontend/playwright.ci-smoke.config.js
)
result="$(mktemp)"
trap 'rm -f "$result"' EXIT
cp .secrets.baseline "$result"
detect-secrets scan --baseline "$result" --force-use-all-plugins \
  --no-verify "${files[@]}" >/dev/null
python3 - "$result" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
new = []
for filename, findings in report.get("results", {}).items():
    for finding in findings:
        if finding.get("is_secret") is not False:
            new.append((filename, finding.get("line_number", "?"), finding.get("type", "unknown")))
if new:
    print("ERROR: the secret scan found unreviewed potential credentials:", file=sys.stderr)
    for filename, line, kind in new:
        print(f"  {filename}:{line} [{kind}]", file=sys.stderr)
    print("Inspect each match; remove real secrets or audit a false positive into .secrets.baseline.", file=sys.stderr)
    raise SystemExit(1)
print(f"Secret scan passed ({sum(map(len, report.get('results', {}).values()))} reviewed baseline matches).")
PY
