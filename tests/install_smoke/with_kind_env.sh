#!/usr/bin/env bash
# Runs a binary (its runfiles path is $1) with the kind cluster's env file
# (KUBECONFIG, KUBECTL) sourced; exits 1 until the cluster is up.
set -euo pipefail
if [[ -z "${RUNFILES_DIR:-}" ]]; then
  if [[ -d "${0}.runfiles" ]]; then RUNFILES_DIR="${0}.runfiles"
  elif [[ -d "$(dirname "$0").runfiles" ]]; then RUNFILES_DIR="$(dirname "$0").runfiles"
  fi
  export RUNFILES_DIR
fi
bin="${RUNFILES_DIR}/_main/${1:?usage: with_kind_env.sh <binary> [args]}"
shift
env_file="$TEST_TMPDIR/cluster.env"
[[ -f "$env_file" ]] || { echo "with_kind_env: no $env_file yet" >&2; exit 1; }
set -a
# shellcheck disable=SC1090
source "$env_file"
set +a
exec "$bin" "$@"
