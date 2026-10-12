#!/usr/bin/env bash
# Pass/fail check for the find-token task (doc 02 §2, doc 04 §2).
#
# The authoritative grader is the oracle's string comparison; this script exists
# so the task is self-contained and can be checked outside the oracle too. It
# compares the submitted flag ($1) against the instance's expected flag, passed
# in via the EXPECTED_FLAG environment variable by the orchestrator.
set -euo pipefail
submitted="${1:-}"
expected="${EXPECTED_FLAG:-}"
if [[ -n "$expected" && "$submitted" == "$expected" ]]; then
  echo "PASS"
  exit 0
fi
echo "FAIL"
exit 1
