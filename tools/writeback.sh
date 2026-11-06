#!/usr/bin/env bash
# Generic writeback shim — copies a bazel-built artifact into a path
# relative to BUILD_WORKSPACE_DIRECTORY (set by `bazel run`).
set -euo pipefail

src="${1:?usage: writeback.sh <src> <dest-relative-to-workspace>}"
rel="${2:?usage: writeback.sh <src> <dest-relative-to-workspace>}"

: "${BUILD_WORKSPACE_DIRECTORY:?must be invoked via \`bazel run\`}"

dest="$BUILD_WORKSPACE_DIRECTORY/$rel"
mkdir -p "$(dirname "$dest")"
cp -f "$src" "$dest"
echo "writeback: $rel"
echo "  sha256: $(sha256sum "$dest" | awk '{print $1}')"
echo "  lines:  $(wc -l < "$dest")"
