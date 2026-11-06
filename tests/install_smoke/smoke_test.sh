#!/usr/bin/env bash
# kind + kyverno_install + kyverno_policy_install: the policies are Ready
# and enforced at admission (server-side dry runs, so no images are pulled).
set -euo pipefail
fail() { echo "FAIL: $*" >&2; exit 1; }
set -a; source "$TEST_TMPDIR/cluster.env"; set +a
k() { "$KUBECTL" --kubeconfig "$KUBECONFIG" "$@"; }

k get clusterpolicy require-team-label -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' | grep -qx True \
  || fail "ClusterPolicy not Ready"
echo "ok: policies Ready"

k create namespace shop --dry-run=client -o yaml | k apply -f - >/dev/null

out=$(k run unlabelled -n shop --image=registry.example/app:1.4.2 --dry-run=server 2>&1) \
  && fail "unlabelled Pod admitted: $out"
grep -qE "label '+team'+ is required" <<<"$out" || fail "denied for another reason: $out"
echo "ok: ClusterPolicy denies a Pod without a team label"

out=$(k run latest -n shop --image=registry.example/app:latest --labels=team=payments --dry-run=server 2>&1) \
  && fail "latest-tag Pod admitted: $out"
grep -q "images must be pinned" <<<"$out" || fail "denied for another reason: $out"
echo "ok: ValidatingPolicy (CEL) denies an image tagged latest"

k run good -n shop --image=registry.example/app:1.4.2 --labels=team=payments --dry-run=server >/dev/null \
  || fail "compliant Pod denied"
echo "ok: a compliant Pod is admitted"
echo "PASS"
