#!/usr/bin/env bash
# Bounded full backend run for CI. A wedged test is a named failure instead of
# an indefinitely occupied runner.
set -euo pipefail

readonly LIMIT="${BORON_TEST_TIMEOUT:-30m}"
readonly TEST_LIMIT="${BORON_TEST_ITEM_TIMEOUT:-180}"
readonly PYTHON="${PYTHON:-python3}"
pytest_args=(-q -rs --tb=short --timeout="$TEST_LIMIT" --timeout-method=signal)

if [[ -n "${BORON_TEST_SHARD:-}" || -n "${BORON_TEST_SHARDS:-}" ]]; then
  pytest_args+=(-p testsupport.pytest_shard)
fi

set +e
"$PYTHON" scripts/test_workspace.py --require-separate -- \
  timeout --foreground --signal=TERM --kill-after=30s "$LIMIT" \
  "$PYTHON" -m coverage run --source=api,daemon,shared,scripts,testsupport \
  --parallel-mode -m pytest "${pytest_args[@]}" "$@"
status=$?
set -e

if [[ "$status" -eq 124 || "$status" -eq 137 ]]; then
  printf 'ERROR: backend test suite exceeded %s and was terminated. Check the last reported test for a blocking startup task, socket call, or worker that did not stop.\n' "$LIMIT" >&2
  exit 124
fi
exit "$status"
