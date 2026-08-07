#!/usr/bin/env bash
# This repository now contains only the Boron namespace. Historic-name
# migrations must be performed from a versioned backup created before this
# namespace cutover, never by keeping obsolete service/path names executable
# in the current control-plane source tree.
set -euo pipefail

printf '%s\n' "This migration helper has been retired. Restore the matching historical backup in an isolated maintenance environment, then install the current Boron release."
exit 1
