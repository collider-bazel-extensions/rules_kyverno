#!/usr/bin/env bash
# kyverno_mutate: the unlabelled Pod gets team=platform; labelled Pods keep theirs.
set -euo pipefail
fail() { echo "FAIL: $*" >&2; exit 1; }
f="$TEST_SRCDIR/_main/$1"
cat "$f"
[[ $(grep -c '^kind: Pod' "$f") == 3 ]] || fail "want 3 Pods"
python3 - "$f" <<'PY' || fail "labels"
import re, sys
docs = [d for d in re.split(r"^---\s*$", open(sys.argv[1]).read(), flags=re.M) if d.strip()]
team = {re.search(r"name: (\S+)", d).group(1): re.search(r"team: (\S+)", d).group(1) for d in docs}
assert team == {"old-cron": "platform", "worker-1": "payments", "worker-2": "payments"}, team
PY
echo "PASS"
