#!/usr/bin/env bash
# kyverno_apply_test failures: a finding not in the list, a stale entry, a
# malformed list, mutations that differ, image verification offline.
set -euo pipefail
fail() { echo "FAIL: $*" >&2; exit 1; }
run() {  # run <target> <want exit> → output in $out, JUnit in $TEST_TMPDIR/junit.xml
  local rc=0
  out=$(TEST_UNDECLARED_OUTPUTS_DIR="$TEST_TMPDIR" XML_OUTPUT_FILE="$TEST_TMPDIR/junit.xml" \
        "$TEST_SRCDIR/_main/$1" 2>&1) || rc=$?
  echo "== $1 (exit $rc)"; echo "$out"
  [[ "$rc" == "$2" ]] || fail "$1: exit $rc, want $2"
}

# The values file puts shop in prod: its Pod fails; dev's doesn't.
run "$1" 1
grep -q "fail  prod-needs-team/team  Pod/shop/unlabelled  (tests/apply/pods.yaml): .*pods in prod need a team label" <<<"$out" \
  || fail "violation not named"
grep -q "Pod/dev/scratch" <<<"$out" && fail "dev Pod reported"

run "$2" 1
grep -q "expected findings:" <<<"$out" || fail "expected findings not listed"
grep -q "expected: shop pods get labels with the Q1 migration" <<<"$out" || fail "reason not shown"
grep -q "stale expected finding (matches nothing): prod-needs-team  Pod/dev/\*  (tests/apply/stale.yaml): no longer true" <<<"$out" \
  || fail "stale entry not named"
grep -q 'failures="1"' "$TEST_TMPDIR/junit.xml" || fail "JUnit: want 1 failure"

run "$3" 2
grep -q "tests/apply/bad_expected.yaml: entry 1: unknown field(s) resourse" <<<"$out" || fail "unknown field"
grep -q "entry 1: resource is required" <<<"$out" || fail "missing resource"
grep -q "entry 1: reason is required" <<<"$out" || fail "missing reason"

run "$4" 1
grep -q "mutated resources differ from tests/apply/wrong_mutated.yaml" <<<"$out" || fail "mutation diff"
grep -Eq -- '^    -  +"team": "payments"' <<<"$out" || fail "diff: want line"
grep -Eq -- '^    \+  +"team": "platform"' <<<"$out" || fail "diff: got line"
grep -q "team: platform" "$TEST_TMPDIR/kyverno-mutated.yaml" || fail "mutated output not written"

run "$5" 1
grep -q 'offline_images = "digest" or "strip"' <<<"$out" || fail "no offline hint"
echo "PASS"
