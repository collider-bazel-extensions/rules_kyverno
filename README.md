# rules_kyverno

Bazel rules for [Kyverno](https://kyverno.io), the Kubernetes policy engine. They check policies at build time, run Kyverno's policy tests, check that manifests (including generated ones) comply with policies, and install Kyverno and policies into a cluster.

- **Policies (build time)**
  - `kyverno_policy` — policy files, checked on every build in two ways:
    - against Kyverno's own CRD schemas: unknown fields, wrong types, bad enum values;
    - by the Kyverno CLI loading them: bad CEL, conflicting rule types.

    Kyverno drops a policy with a misspelt field *without any message*, so a typo would otherwise leave a policy that enforces nothing.
  - `kyverno_policy_test` — the same check at test time, expecting a given error (or none).
- **Tests and checks**
  - `kyverno_test` — runs `kyverno test` suites (`kyverno-test.yaml`), with JUnit.
  - `kyverno_apply_test` — fails if any resource violates a policy, naming the policy, rule, resource and source file. Resources can be files, generated files or directories, such as rendered Helm charts.
  - `kyverno_check` — the same as a validation action, so a violation fails `bazel build`.
  - `kyverno_mutate` — writes the resources as the policies mutate them.
- **Cluster install (kind / production-shaped)**
  - `kyverno_install` + `kyverno_health_check` — the pinned `kyverno` Helm chart, pre-rendered, CRDs included.
  - `kyverno_policy_install` + `kyverno_policy_health_check` — policies applied and reported Ready by Kyverno.

No host Kyverno or Helm needed. The CLI is the upstream release for linux and darwin on amd64 and arm64, pinned by SHA-256, at the same version as the image `kyverno_install` deploys (1.19.2). The rules need `python3` on the host. The install needs `kubectl`; rules_kind provides it.

## Install (Bzlmod)

```python
bazel_dep(name = "rules_kyverno", version = "0.1.0")
git_override(
    module_name = "rules_kyverno",
    remote = "https://github.com/collider-bazel-extensions/rules_kyverno",
    tag = "v0.1.0",
)

# rules_kyverno depends on rules_kubectl, which isn't in the BCR either.
bazel_dep(name = "rules_kubectl", version = "0.2.0")
git_override(
    module_name = "rules_kubectl",
    remote = "https://github.com/collider-bazel-extensions/rules_kubectl",
    commit = "81b60ab80df46b64c612f2576ffef0a7af98ccb6",  # v0.2.0
)
```

That's all: rules_kyverno registers its toolchain and fetches the CLI only for the platform a build needs. To pick another pinned version, name it in your own `MODULE.bazel` (only the root module's tag counts):

```python
kyverno = use_extension("@rules_kyverno//:extensions.bzl", "kyverno")
kyverno.version(version = "1.19.2")
```

## Policies

```python
load("@rules_kyverno//:defs.bzl", "kyverno_policy")

kyverno_policy(
    name = "policies",
    srcs = glob(["policies/*.yaml"]),   # any Kyverno kinds, several documents per file
    deps = ["//platform:base_policies"],
)
```

Every build checks each document:

1. **Schema.** It must be a Kyverno policy kind, valid against its CRD's OpenAPI schema. The schema comes from the CRDs in the pinned chart, so it's the one your cluster will use. Errors name the file, line, policy and field:

   ```
   kyverno_policy //platform:policies:
     policies/require_team.yaml:1 (ClusterPolicy require-team-label): spec.validationFailureActon: unknown field 'validationFailureActon' (did you mean 'validationFailureAction'?)
     policies/no_latest.yaml:1 (ValidatingPolicy disallow-latest-tag): spec.validations[0].expresion: unknown field 'expresion' (did you mean 'expression'?)
   ```

   Kinds: `ClusterPolicy`, `Policy`, `ValidatingPolicy`, `MutatingPolicy`, `GeneratingPolicy`, `ImageValidatingPolicy`, `DeletingPolicy` and their `Namespaced…` forms, `PolicyException` (both groups), `CleanupPolicy`, `ClusterCleanupPolicy` and `GlobalContextEntry`, in every version the CRDs serve. Kubernetes `ValidatingAdmissionPolicy` and `MutatingAdmissionPolicy` (and their bindings) are accepted with the load check only, since no Kyverno CRD describes them.
2. **The CLI loads them.** The rule-bearing policies are applied to a resource none of them matches, so Kyverno compiles them. Bad CEL, a rule with two validation types and similar mistakes fail with Kyverno's message, e.g. `Kyverno skipped: … only one of pattern, anyPattern, deny, foreach, cel can be specified`.

The check runs on the pinned CLI with no network (`--sandbox_default_allow_network=false` works). `deps` bundle other `kyverno_policy` targets in front of `srcs`.

### `kyverno_policy_test`

```python
kyverno_policy_test(
    name = "typo_is_caught_test",
    srcs = ["testdata/typo.yaml"],
    expect_error = "unknown field 'validationFailureActon'",   # extended regex
)
```

This passes if the check fails with a matching message, or, with no `expect_error`, if it passes. Use it to pin down that a mistake you care about is caught.

## `kyverno_test`

```python
load("@rules_kyverno//:defs.bzl", "kyverno_test")

kyverno_test(
    name = "policies_test",
    srcs = ["tests/kyverno-test.yaml"],     # any number; each runs in its own directory
    policies = [":policies"],
    data = ["tests/resources.yaml"],        # everything else the suites name
)
```

The suite is Kyverno's own format (`cli.kyverno.io/v1alpha1` `Test`). Paths in it are relative to the suite file, and Bazel keeps workspace paths, so `policies: [../policies/require_team.yaml]` works if the file is in `policies` or `data`.

- **Output:** the detailed results table goes to the test log, and JUnit (one case per result) to Bazel.
- **Empty suites fail** (`--require-tests`).

## `kyverno_apply_test` and `kyverno_check`

```python
load("@rules_kyverno//:defs.bzl", "kyverno_apply_test", "kyverno_check")

kyverno_apply_test(
    name = "manifests_comply_test",
    policies = ["//platform:policies"],
    resources = [":rendered_chart", "k8s/"],   # files, generated files, directories
    exceptions = ["//platform:exceptions"],    # optional extra PolicyExceptions
)

kyverno_check(   # same attributes; a violation fails `bazel build`
    name = "manifests_check",
    policies = ["//platform:policies"],
    resources = [":rendered_chart"],
)
```

```
kyverno //deploy:manifests_comply_test: pass 14, fail 2, warn 0, error 0, skip 0 (3 resource files)
  fail  require-team-label/check-team  Pod/shop/unlabelled  (deploy/pods.yaml): validation error: label 'team' is required. …
  fail  disallow-latest-tag  Pod/shop/latest  (deploy/pods.yaml): images must be pinned to a tag other than latest
```

- **Failures:** `fail` and `error` results fail the test. `warn` results are listed. `audit_warn = True` reports Audit-mode policies' failures as warnings.
- **Exceptions:** `PolicyException`s in `policies` or `exceptions` apply.
- **Skipped kinds:** cleanup and deleting policies and global context entries act in a cluster, not on a resource, so they're left out of `apply`.
- **Reports:**
  - the test writes JUnit to Bazel, and Kyverno's policy report (JSON) to its undeclared outputs (`kyverno-report.json`);
  - `kyverno_check` puts the report in the `kyverno` output group.

## `kyverno_mutate`

```python
kyverno_mutate(
    name = "mutated",                 # → mutated.yaml (or `out`)
    policies = [":mutations"],
    resources = [":rendered_chart"],
)
```

This writes the resources as the mutating policies change them, in one multi-document YAML file. It's useful to review what admission will do, or to ship pre-mutated manifests.

## Cluster install

```python
load("@rules_kyverno//:defs.bzl",
     "kyverno_health_check", "kyverno_install",
     "kyverno_policy_health_check", "kyverno_policy_install")

kyverno_install(name = "kyverno_bin", tags = ["manual"])
kyverno_health_check(name = "kyverno_health_bin", tags = ["manual"])

kyverno_policy_install(name = "policies_bin", policies = [":policies"], tags = ["manual"])
kyverno_policy_health_check(name = "policies_health_bin", policies = [":policies"], tags = ["manual"])

itest_service(name = "kyverno_svc", exe = ":kyverno_bin", health_check = ":kyverno_health_bin", deps = [":kind_svc"])
itest_service(name = "policies_svc", exe = ":policies_bin", health_check = ":policies_health_bin", deps = [":kyverno_svc"])
```

- **`kyverno_install`:** applies the pinned `kyverno` chart 3.9.2 (Kyverno 1.19.2), CRDs included, server-side, and waits for the admission, background, cleanup and reports controllers.
  - **Render:** with `--no-hooks` (no CRD-migration or webhook-cleanup Jobs) and one replica each (`config/kyverno-values.yaml`).
  - **Namespace:** fixed at `kyverno`, because the chart's objects and service references carry it.
  - **Webhooks:** Kyverno creates its admission webhooks itself at start-up.
- **`kyverno_policy_install`:** applies `kyverno_policy` targets, each policy in its own namespace.
- **`kyverno_policy_health_check`:** passes once every policy exists and Kyverno reports it Ready, so tests start only once policies are enforced.
- **Cluster access:** both health checks and the installs use `$KUBECTL` and `$KUBECONFIG` from rules_kind's env file. Wrap them to source it, as `tests/install_smoke/with_kind_env.sh` does.
- **Smoke test:** `tests/install_smoke/` runs it all in kind. It checks that a ClusterPolicy and a CEL ValidatingPolicy deny and admit Pods at admission, using server-side dry runs.

## Pinned versions

| Component | Version | Source |
|---|---|---|
| Kyverno CLI | `1.19.2` | kyverno/kyverno releases (published `checksums.txt`) |
| Helm chart | `kyverno-3.9.2` (appVersion v1.19.2) | https://kyverno.github.io/kyverno (index digest) |

Maintainer flows:
- **New CLI:** run `tools/update_checksums.sh <version>`. It pins all four platforms, cross-checked against the downloads, and `--check` verifies the pins. Keep `DEFAULT_VERSION`, `kyverno.version()` in `MODULE.bazel` and the chart's images equal (`//tests:version_test`).
- **Chart:** edit `tools/versions.bzl` and `tools/BUILD.bazel`, then run `tools/render_kyverno.sh <version>`. The policy schemas are extracted from the render at build time, so they follow automatically. CI fails if the committed manifest drifts.

## Development

```sh
bazel test //tests/...                                               # no cluster
bazel test //tests/install_smoke:smoke_test --test_output=streamed   # needs Docker or podman
```

See [DESIGN.md](DESIGN.md) for the design and trade-offs, and [CLAUDE.md](CLAUDE.md) for the repo layout and test inventory.
