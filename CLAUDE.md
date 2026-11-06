# rules_kyverno

Bazel rules for Kyverno:
- build-time policy checks (CRD schema + CLI load);
- `kyverno test` suites, apply tests and checks over manifests, mutation;
- cluster install of Kyverno and policies over the pinned, pre-rendered chart.

See DESIGN.md.

## Commit requirements

- All tests must pass before any commit with code changes: `bazel test //tests/...`. Run the kind smoke `//tests/install_smoke:smoke_test` too when install code changes.
- All documentation (`README.md`, `DESIGN.md`, `CLAUDE.md`) must be updated to reflect any code changes before committing: new rules, changed attributes, new public API surface, behaviour changes.

## Repo layout

```
rules_kyverno/
├── MODULE.bazel              # module; kyverno extension + toolchains; rules_kubectl; dev deps (helm, itest, kind)
├── defs.bzl                  # public API re-exports
├── extensions.bzl            # `kyverno.version()` → kyverno_<plat> repos (root tag, else ours)
├── config/kyverno-values.yaml  # chart render values (one replica each, hooks off)
├── private/
│   ├── versions.bzl          # DEFAULT_VERSION, KYVERNO_VERSIONS (sha256 pins), PLATFORMS
│   ├── repositories.bzl      # per-platform repo: tarball + its kyverno_toolchain impl
│   ├── providers.bzl         # KyvernoBinaryInfo, KyvernoPolicyInfo (files, index)
│   ├── kyverno.py            # driver: schemas, check, apply, mutate, test, ready
│   ├── schemas.bzl           # kyverno_schemas (//private:schemas, from the rendered chart)
│   ├── policy.bzl            # kyverno_policy (+ validation), kyverno_policy_test
│   ├── test.bzl              # kyverno_test, kyverno_apply_test (+ run.sh.tmpl)
│   ├── check.bzl             # kyverno_check, kyverno_mutate
│   ├── install.bzl           # kyverno_install/_health_check, kyverno_policy_install/_health_check
│   └── manifests/kyverno.yaml  # rendered chart (committed; do not hand-edit)
├── toolchain/                # toolchain_type, per-platform toolchains, :current
├── tools/                    # update_checksums.sh; chart pin + render flow
└── tests/
    ├── BUILD.bazel, analysis_tests.bzl
    ├── policies/             # good policies: ClusterPolicy, CEL ValidatingPolicy, mutation, exception + cleanup, VAP/MAP
    ├── bad/                  # kyverno_policy_test per failure class; typo_top (manual) for CI must-fail
    ├── apply/                # values file, expected findings, resource filters, offline images, expected mutations
    ├── suite/                # kyverno-test.yaml (+ wrong-test.yaml), pods.yaml
    ├── resources/            # compliant, legacy (exempted), a genrule "rendered" manifest
    ├── *_failure_test.sh, mutate_test.sh, checksums_test.sh, version_test.sh
    └── install_smoke/        # kind → kyverno_install → kyverno_policy_install → admission checks
```

## Key concepts

- **Silent drops.** Kyverno drops a policy with any unknown field (or a non-YAML file) and exits 0. Hence the schema check against the CRDs (`//private:schemas`, extracted from the committed render at build time).
- **YAML → JSON** via the pinned CLI: `kyverno jp query -i <file> @`. First document only, so the driver splits on `---`.
- **Load check.**
  - Rule kinds are applied to a `LoadProbe` no policy matches. "Policies Skipped", ERROR lines or `error: N>0` fail.
  - VAP/MAP and ImageValidatingPolicy need a real GVR (#1), so they go against a ConfigMap probe instead:
    - each VAP/MAP gets its own synthetic binding (the files' bindings are dropped);
    - each IVP is retargeted to match ConfigMaps;
    - only compile errors count (JSON report, plus the `-v 1` log for MAP).
  - One CLI run for all files; on problems, one run per file, prefixed `<file>:` (#2).
- **Apply staging.** RULE_KINDS → policies; PolicyException → `--exception`; cleanup, deleting and global-context kinds are left out (apply would report them as failing). Results are judged from the JSON policy report, not the exit code.
- **Apply inputs (#3).**
  - `values_file` → `--values-file`.
  - `expected_findings`: an allowlist matched in the driver; stale entries fail.
  - `resource_filters`: the Kyverno ConfigMap's filters, applied in the driver.
  - `offline_images`: `strip`, or `digest` (verifyImages reduced to a digest check; an IVP becomes a digest-check ValidatingPolicy).
  - `expected_mutations`: the `-o` output, compared as canonical JSON.
- **Install.** Namespace fixed (`kyverno`); server-side apply (huge CRDs); `--no-hooks`. Policies are Ready per `kyverno_policy_health_check` (index from the build-time check, `$KUBECTL get`).
- **Toolchain.** Unversioned `kyverno_<plat>` repos; keep `DEFAULT_VERSION`, `kyverno.version()` in `MODULE.bazel` and the chart images equal (`//tests:version_test`).

## Public API

```python
load("@rules_kyverno//:defs.bzl",
    "kyverno_policy",              # srcs, deps
    "kyverno_policy_test",         # srcs, expect_error
    "kyverno_test",                # srcs (kyverno-test.yaml), policies, data
    "kyverno_apply_test",          # policies, resources, exceptions, audit_warn, values_file,
                                   # expected_findings, resource_filters, offline_images,
                                   # expected_mutations
    "kyverno_check",               # same as kyverno_apply_test (validation action)
    "kyverno_mutate",              # policies, resources, exceptions, values_file,
                                   # resource_filters, offline_images, out
    "kyverno_install",             # wait_timeout (+ kubectl_apply kwargs)
    "kyverno_health_check",
    "kyverno_policy_install",      # policies, wait_timeout
    "kyverno_policy_health_check", # policies
    "KyvernoBinaryInfo", "KyvernoPolicyInfo",
)
```

## Development

```sh
bazel test //tests/...                                  # no cluster
systemd-run --scope --user --property=Delegate=yes -- \
    bazel test //tests/install_smoke:smoke_test --test_output=streamed   # kind (podman)
bazel build //tests/bad:typo_top                        # must fail: unknown field
bazel build //tests:violations_check                    # must fail: violations
bazel build //tests/apply:stale_check                   # must fail: stale expected finding
tools/update_checksums.sh 1.19.2                        # re-pin; --check verifies
tools/render_kyverno.sh 3.9.2                           # re-render the chart
```

### Test results (last full run: 2026-11-06)

All 39 test targets pass on Linux x86_64 (Kyverno 1.19.2): 38 in `//tests/...` and the kind smoke on rootless podman. Other checks:
- `//tests/...` passes with network blocked in the sandbox (`--sandbox_default_allow_network=false`). The offline-image tests are tagged `block-network` regardless.
- The three must-fail builds fail with the expected messages (CI checks them).
- All four platform archives fetch, with the binary at their root.
- A scratch consumer module ran `kyverno_test`, `kyverno_policy_test`, `kyverno_apply_test` and `kyverno_check` and built the install targets.

| Test target | What it verifies | Result |
|---|---|---|
| `//tests/bad:*_test` (14) | typo at top level and nested (with suggestion), wrong type, bad CEL, two validation types, not YAML, not a policy kind; bad CEL in an unbound VAP, a MAP and an IVP; with several files each problem names its file and a CEL compile error hides none; good policies (VAP/MAP among them) pass | PASSED |
| `//tests:suite_test` | `kyverno test` suite: ClusterPolicy + CEL ValidatingPolicy, pass and fail expectations | PASSED |
| `//tests:suite_failure_test` | a wrong expectation fails, shows "Want pass, got fail", JUnit failure | PASSED |
| `//tests:compliant_test` | a Deployment and a generated manifest comply | PASSED |
| `//tests:exception_test` | a PolicyException exempts a namespace (cleanup policy in the same file left out) | PASSED |
| `//tests:apply_failure_test` | violations named with policy/rule, resource and source file; JUnit; JSON report | PASSED |
| `//tests:mutate_test` | kyverno_mutate adds the default label only where missing | PASSED |
| `//tests:policy_test_failure_test` | kyverno_policy_test fails on each wrong outcome | PASSED |
| `//tests/apply:values_test` | values file labels namespaces for namespaceSelector; the violation is an expected finding | PASSED |
| `//tests/apply:filters_test` | the chart's resourceFilters leave a kube-public Pod out | PASSED |
| `//tests/apply:offline_digest_test`, `offline_strip_test` | no network: verifyImages + IVP reduced to a digest check (autogen too) / left out | PASSED |
| `//tests/apply:expected_mutations_test` | mutated output equals the expected YAML (as data) | PASSED |
| `//tests/apply:expected_failure_test` | an unlisted finding fails (source file named); a stale entry fails (JUnit); a malformed list is refused; mutations differing show a diff; online image verification fails with the offline_images hint | PASSED |
| `//tests:checksums_test`, `//tests:version_test` | pins well-formed; DEFAULT_VERSION = MODULE.bazel = binary = chart images | PASSED |
| `//tests:analysis_tests_*` (9) | provider bundling + index, validation outputs, kyverno_check's report + mutated output group, executables, empty srcs / suite / resources | PASSED |
| `//tests/install_smoke:smoke_test` | kind: Kyverno + policies Ready; ClusterPolicy and CEL ValidatingPolicy deny, compliant Pod admitted | PASSED |

## Known limitations

- `python3` is required on the host (the driver).
- No remote policies or suites, no `--set` for apply tests, no registry access for image verification (`offline_images` reduces or strips it).
- The 1.19.2 CLI doesn't evaluate `v1beta1` MutatingAdmissionPolicy: such a policy's CEL isn't compiled by the load check.
- The install's namespace is fixed (`kyverno`); one replica per controller.
