#!/usr/bin/env bash
# kyverno_policy_test fails when the outcome is not the expected one.
set -euo pipefail
fail() { echo "FAIL: $*" >&2; exit 1; }
check() {  # check <target path> <expected message>
  local rc=0 out
  out=$("$TEST_SRCDIR/_main/$1" 2>&1) || rc=$?
  [[ "$rc" != 0 ]] || fail "$1 passed: $out"
  grep -qF "$2" <<<"$out" || fail "$1: want '$2' in: $out"
  echo "ok: $1 fails ($2)"
}
check "$1" "FAIL: the check accepted require_team.yaml; expected an error matching: anything"
check "$2" "FAIL: the check refused typo_top.yaml"
check "$3" "FAIL: the error does not match: a message that is not there"
echo "PASS"
