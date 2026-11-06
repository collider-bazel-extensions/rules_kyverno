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
    - Options: a values file (namespace labels for `namespaceSelector`), a reviewed list of expected findings that fails when an entry goes stale, the Kyverno ConfigMap's `resourceFilters`, offline image verification, and expected mutations.
  - `kyverno_check` — the same as a validation action, so a violation fails `bazel build`.
  - `kyverno_mutate` — writes the resources as the policies mutate them.
- **Cluster install (kind / production-shaped)**
  - `kyverno_install` + `kyverno_health_check` — the pinned `kyverno` Helm chart, pre-rendered, CRDs included.
  - `kyverno_policy_install` + `kyverno_policy_health_check` — policies applied and reported Ready by Kyverno.

No host Kyverno or Helm needed. The CLI is the upstream release for linux and darwin on amd64 and arm64, pinned by SHA-256, at the same version as the image `kyverno_install` deploys (1.19.2). The rules need `python3` on the host. The install needs `kubectl`; rules_kind provides it.

## Install (Bzlmod)

```python
bazel_dep(name = "rules_kyverno", version = "0.2.1")
git_override(
    module_name = "rules_kyverno",
    remote = "https://github.com/collider-bazel-extensions/rules_kyverno",
    tag = "v0.2.1",
)

# rules_kyverno depends on rules_kubectl, which isn't in the BCR either.
bazel_dep(name = "rules_kubectl", version = "0.2.0")
git_override(
    module_name = "rules_kubectl",
    remote = "https://github.com/collider-bazel-extensions/rules_kubectl",
    commit = "81b60ab80df46b64c612f2576ffef0a7af98ccb6",  # v0.2.0
)
```

To pin by archive and integrity instead of a git tag, replace the first `git_override` with:

```python
archive_override(
    module_name = "rules_kyverno",
    urls = ["https://github.com/collider-bazel-extensions/rules_kyverno/archive/refs/tags/v0.2.0.tar.gz"],
    strip_prefix = "rules_kyverno-0.2.0",
    integrity = "sha256-v7cxKVIuNcjxltFWzjQ70qpFDF8tOcFzN17KTKA2lRI=",
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
2. **The CLI loads them.** Kyverno compiles the rule-bearing policies, so bad CEL, a rule with two validation types and similar mistakes fail with Kyverno's message. Each problem names its file:

   ```
   kyverno_policy //platform:policies:
     policies/legacy.yaml: Kyverno skipped: 1. two-validations: path: spec.rules[0].validate..: only one of pattern, anyPattern, deny, foreach, cel can be specified
     policies/vap.yaml: Kyverno: ValidatingAdmissionPolicy max-replicas: compilation error: … Syntax error: …
   ```

   - **Kyverno kinds** are applied to a resource none of them matches.
   - **`ValidatingAdmissionPolicy` / `MutatingAdmissionPolicy` and `ImageValidatingPolicy`** are loaded against a ConfigMap: the CLI needs a real kind for them. Each admission policy gets a binding of its own, so its CEL is compiled whether the files bind it or not. Every expression is compiled: `matchConditions`, `validations` and `mutations` even when the `matchConditions` don't match, and `variables`, `messageExpression` and `auditAnnotations`, whose errors the CLI doesn't report by itself. An error in those three names its field, e.g. `max-replicas (spec.variables[0] (team))`. Only compile errors count, since the policy may fail on the ConfigMap itself. The pinned CLI (1.19.2) evaluates `MutatingAdmissionPolicy` only at `v1alpha1`; a `v1beta1` one loads without its CEL compiled.
   - **Speed:** all files load in one CLI run. Only if that finds problems does each file load on its own, so every problem names its file and one file's compile error doesn't hide another's.

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
  - the test writes JUnit to Bazel, and to its undeclared outputs Kyverno's policy report (JSON, `kyverno-report.json`) and the resources the policies changed (`kyverno-mutated.yaml`);
  - `kyverno_check` puts both in the `kyverno` output group (`<name>.kyverno.json`, `<name>.mutated.yaml`).

### More inputs

```python
kyverno_apply_test(
    name = "rendered_test",
    policies = ["//platform:policies"],
    resources = [":rendered_chart"],
    values_file = "kyverno-values.yaml",            # namespace labels, variables
    expected_findings = "known-findings.yaml",      # reviewed; stale entries fail
    resource_filters = "@rules_kyverno//:kyverno_manifest",  # or your Kyverno ConfigMap
    offline_images = "digest",                      # or "strip"
    expected_mutations = "mutated.yaml",            # what mutate rules must produce
)
```

All of them work on `kyverno_check` too. `values_file`, `resource_filters` and `offline_images` also work on `kyverno_mutate`.

- **`values_file`:** a Kyverno CLI values file (`cli.kyverno.io/v1alpha1` `Value`), passed as `--values-file`. Offline, policies that match on `namespaceSelector` need namespace labels from it:

  ```yaml
  apiVersion: cli.kyverno.io/v1alpha1
  kind: Value
  metadata: {name: values}
  namespaceSelector:
    - name: shop
      labels: {env: prod}
  ```

  Policy and rule variables and `globalValues` go in the same file.
- **`expected_findings`:** a reviewed list of known violations. A finding listed here is reported as expected and doesn't fail. An entry that matches no finding fails as stale, so the list can't silently outlive the problem:

  ```yaml
  - policy: require-team-label
    rule: check-team                # optional; autogen rules are autogen-check-team
    resource: Deployment/shop/legacy-api   # Kind/namespace/name, as failures print it
    reason: owned by the shop team after the Q1 migration (TICKET-123)
  ```

  `*` and `?` work as wildcards in `policy`, `rule` and `resource`. `reason` is required. Unknown fields are refused. Quote a reason that contains ` #` (`reason: "refused (#188)"`): unquoted, YAML reads the rest as a comment.
- **`resource_filters`:** a file holding the Kyverno ConfigMap, alone or in a rendered chart. Resources its `resourceFilters` match are left out, as the admission controller skips them.
  - **Which ConfigMap:** the one named `kyverno`, or the only one with `resourceFilters`.
  - **Defaults:** `@rules_kyverno//:kyverno_manifest` is the pinned chart, with its default filters (kube-system, kube-public, kyverno, Events, ReplicaSets, …).
  - **Matching:** offline, a manifest has no subresource, and a missing namespace counts as `''`.
- **`offline_images`:** image verification needs a registry. As written, `verifyImages` rules and `ImageValidatingPolicy`s reach for one, even for digest checks, and fail in a sandbox without network. The test's message suggests this option when it sees that.
  - `"strip"` leaves them out.
  - `"digest"` keeps only the check that matching images are pinned by digest:
    - a `verifyImages` entry keeps its `imageReferences` and `skipImageReferences`, with attestors, attestations and digest mutation removed;
    - an `ImageValidatingPolicy` becomes a `ValidatingPolicy` with the same match rules (`matchImageReferences` globs and expressions, `matchConstraints`, `matchConditions`, autogen). It checks every container's image as written. Custom image extractors (`spec.images`) aren't checked.
- **`expected_mutations`:** YAML of the resources the policies change, as they should come out. Any difference fails, with a diff. They're compared as data, so formatting and key order don't count.
  - **Only changed resources:** the CLI writes every input resource, so the rule keeps those that differ from their input. Resources no policy matched, or that a policy left as they were, are neither written nor compared. A namespace the CLI fills in as `default` isn't a change, but a changed resource is written with it.
  - To update the file, copy `kyverno-mutated.yaml` from the test's outputs.
- **Duplicate resources:** two input resources with the same `apiVersion`, kind, namespace and name (a missing namespace counts as `default`) fail before the CLI runs, naming every file they're in. The CLI can hold each only once, and panics (`… already exists`) otherwise. This applies to `kyverno_mutate` too; resources `resource_filters` leaves out don't count.

## `kyverno_mutate`

```python
kyverno_mutate(
    name = "mutated",                 # → mutated.yaml (or `out`)
    policies = [":mutations"],
    resources = [":rendered_chart"],
)
```

This writes every resource as the mutating policies leave it, changed or not, in one multi-document YAML file. It's useful to review what admission will do, or to ship pre-mutated manifests. `values_file`, `resource_filters` and `offline_images` work as on `kyverno_apply_test`.

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
