#!/usr/bin/env bash
# A kyverno_apply_test over violating resources fails, naming policy, rule,
# resource and source file; JUnit and the JSON report are written.
set -euo pipefail
fail() { echo "FAIL: $*" >&2; exit 1; }
out_dir="$TEST_TMPDIR/out"; mkdir -p "$out_dir"
rc=0
out=$(TEST_UNDECLARED_OUTPUTS_DIR="$out_dir" XML_OUTPUT_FILE="$TEST_TMPDIR/junit.xml" \
      "$TEST_SRCDIR/_main/$1" 2>&1) || rc=$?
echo "$out"
[[ "$rc" == 1 ]] || fail "exit status $rc, want 1"
grep -q "fail  require-team-label/check-team  Pod/shop/unlabelled  (tests/suite/pods.yaml): .*label 'team' is required" <<<"$out" \
  || fail "team violation not named"
grep -q "fail  disallow-latest-tag  Pod/shop/latest  (tests/suite/pods.yaml): images must be pinned" <<<"$out" \
  || fail "CEL violation not named"
grep -q "Deployment/shop/checkout" <<<"$out" && fail "a compliant resource was reported"
grep -q "fail 2" <<<"$out" || fail "summary"
grep -q 'failures="2"' "$TEST_TMPDIR/junit.xml" || fail "JUnit"
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["summary"]["fail"] == 2, r["summary"]' \
  "$out_dir/kyverno-report.json" || fail "JSON report"
echo "PASS"
