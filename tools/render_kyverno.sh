#!/usr/bin/env bash
# Maintainer flow:
#   1. Edit tools/versions.bzl::KYVERNO_CHART_VERSIONS (chart_url + chart_sha256).
#   2. Update the helm_template + sh_binary blocks in tools/BUILD.bazel.
#   3. tools/render_kyverno.sh <version>   (e.g. 3.9.2)
#   4. bazel run //tools:extract_schemas
#
# Host helm is NOT required — helm comes from rules_helm.
set -euo pipefail

VERSION="${1:?usage: tools/render_kyverno.sh <chart-version>}"
TARGET="//tools:render_writeback_$(echo "$VERSION" | tr '.' '_')"

echo "[render_kyverno] $TARGET"
exec bazel run "$TARGET"
