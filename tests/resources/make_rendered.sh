#!/usr/bin/env bash
# Stands in for a Helm render: writes two compliant Pods to $1.
set -euo pipefail
for i in 1 2; do
  printf -- '---\napiVersion: v1\nkind: Pod\nmetadata:\n  name: worker-%s\n  namespace: shop\n  labels: {team: payments}\nspec:\n  containers: [{name: w, image: "registry.example/worker:3.1.%s"}]\n' "$i" "$i"
done > "$1"
