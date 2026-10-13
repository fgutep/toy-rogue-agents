#!/usr/bin/env bash
set -euo pipefail
[[ -n "${EXPECTED_FLAG:-}" && "${1:-}" == "$EXPECTED_FLAG" ]] && { echo PASS; exit 0; }
echo FAIL
exit 1
