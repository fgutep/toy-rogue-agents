#!/usr/bin/env bash
set -euo pipefail
if [[ -z "${EXPECTED_FLAG:-}" || "${1:-}" != "$EXPECTED_FLAG" ]]; then
  echo FAIL
  exit 1
fi
if [[ "${TRACE_MATCHES_TIMELINE:-false}" != "true" ]]; then
  echo "FAIL: unintended solution path. Flag value was correct but the submitted trace does not match the vulnerability class used."
  exit 1
fi
echo PASS
