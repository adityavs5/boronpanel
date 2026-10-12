#!/usr/bin/env bash
set -euo pipefail

PYTHON="${PYTHON:-.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON=python3
fi

"$PYTHON" -m ruff check \
  --select E9,F63,F7,F82 \
  api daemon shared scripts tests testsupport
"$PYTHON" scripts/check_schema_migrations.py

# Browser test and build configuration files are plain JavaScript modules.
# The Vite production build performs the JSX/module graph syntax check.
while IFS= read -r -d '' file; do
  node --check "$file"
done < <(find frontend/e2e -maxdepth 1 -type f \( -name '*.js' -o -name '*.mjs' \) -print0)
node --check frontend/vite.config.js
