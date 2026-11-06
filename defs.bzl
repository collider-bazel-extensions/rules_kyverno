"""Public API for rules_kyverno.

Policies (build time):
  kyverno_policy        — policy files, schema-checked and loaded by the CLI
                          on every build
  kyverno_policy_test   — that check at test time, expecting an error (or none)

Tests and checks:
  kyverno_test          — `kyverno test` suites (kyverno-test.yaml)
  kyverno_apply_test    — resources comply with policies (test)
  kyverno_check         — the same as a validation action (fails `bazel build`)
  kyverno_mutate        — resources as the policies mutate them (output file)

Cluster install:
  kyverno_install / kyverno_health_check — the pinned kyverno chart
  kyverno_policy_install / kyverno_policy_health_check — policies applied
                          and Ready
"""

load("//private:check.bzl", _kyverno_check = "kyverno_check", _kyverno_mutate = "kyverno_mutate")
load(
    "//private:install.bzl",
    _kyverno_health_check = "kyverno_health_check",
    _kyverno_install = "kyverno_install",
    _kyverno_policy_health_check = "kyverno_policy_health_check",
    _kyverno_policy_install = "kyverno_policy_install",
)
load("//private:policy.bzl", _kyverno_policy = "kyverno_policy", _kyverno_policy_test = "kyverno_policy_test")
load("//private:providers.bzl", _KyvernoBinaryInfo = "KyvernoBinaryInfo", _KyvernoPolicyInfo = "KyvernoPolicyInfo")
load("//private:test.bzl", _kyverno_apply_test = "kyverno_apply_test", _kyverno_test = "kyverno_test")

kyverno_policy = _kyverno_policy
kyverno_policy_test = _kyverno_policy_test
kyverno_test = _kyverno_test
kyverno_apply_test = _kyverno_apply_test
kyverno_check = _kyverno_check
kyverno_mutate = _kyverno_mutate
kyverno_install = _kyverno_install
kyverno_health_check = _kyverno_health_check
kyverno_policy_install = _kyverno_policy_install
kyverno_policy_health_check = _kyverno_policy_health_check

KyvernoBinaryInfo = _KyvernoBinaryInfo
KyvernoPolicyInfo = _KyvernoPolicyInfo
