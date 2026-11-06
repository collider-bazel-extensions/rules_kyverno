#!/usr/bin/env bash
# A kyverno_test whose suite expects the wrong result fails, shows the
# mismatch, and writes a JUnit failure.
set -euo pipefail
fail() { echo "FAIL: $*" >&2; exit 1; }
rc=0
out=$(XML_OUTPUT_FILE="$TEST_TMPDIR/junit.xml" "$TEST_SRCDIR/_main/$1" 2>&1) || rc=$?
echo "$out"
[[ "$rc" != 0 ]] || fail "wrong expectation passed"
grep -q "Want pass, got fail" <<<"$out" || fail "mismatch not shown"
grep -q "1 tests failed" <<<"$out" || fail "summary"
grep -q 'failures="1"' "$TEST_TMPDIR/junit.xml" || fail "JUnit: $(head -c 400 "$TEST_TMPDIR/junit.xml")"
echo "PASS"
