#!/usr/bin/env bash
# The CLI the rules run is the Kyverno kyverno_install deploys:
# DEFAULT_VERSION, MODULE.bazel's kyverno.version(), the binary and every
# controller image in the rendered chart agree.
#
# Argv: <versions.bzl> <MODULE.bazel> <kyverno.yaml> <kyverno>
set -euo pipefail
fail() { echo "FAIL: $*" >&2; exit 1; }
r="$TEST_SRCDIR/_main"
default=$(sed -n 's/^DEFAULT_VERSION = "\(.*\)"/\1/p' "$r/$1")
module=$(sed -n 's/^kyverno.version(version = "\(.*\)")/\1/p' "$r/$2")
binary=$("$r/$4" version | sed -n 's/^Version: //p')
images=$(grep -oE 'image: "?reg.kyverno.io/kyverno/[a-z-]+:v[^" ]+' "$r/$3" | sed 's/.*:v//' | sort -u)
echo "DEFAULT_VERSION=$default MODULE.bazel=$module binary=$binary images=$(echo $images)"
[[ -n "$default" && "$module" == "$default" && "$binary" == "$default" && "$images" == "$default" ]] \
  || fail "versions differ (re-pin with tools/update_checksums.sh, or re-render the chart)"
echo "PASS"
