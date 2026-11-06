#!/usr/bin/env bash
# Fails when any pin in private/versions.bzl lacks a 64-hex SHA-256, or a
# version doesn't cover all 4 platforms. No network.
set -euo pipefail
f="${TEST_SRCDIR}/_main/private/versions.bzl"
urls=$(grep -cE '"url":' "$f" || true)
pinned=$(grep -cE '"sha256":[[:space:]]*"[0-9a-f]{64}"' "$f" || true)
versions=$(grep -cE '^    "[0-9][^"]*": \{' "$f" || true)
echo "versions=$versions urls=$urls pinned=$pinned"
if [[ "$urls" -eq 0 || "$pinned" -ne "$urls" || "$urls" -ne $(( versions * 4 )) ]]; then
  echo "FAIL: every version needs 4 platforms, each with a 64-hex SHA-256 (tools/update_checksums.sh <version>)" >&2
  exit 1
fi
echo "PASS"
