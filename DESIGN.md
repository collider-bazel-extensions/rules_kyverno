# rules_kyverno — design notes

rules_kyverno combines the two shapes used across this organization:
- **like rules_gitleaks and rules_opa:** a CLI toolchain with build-time checks and test rules;
- **like rules_valkey, rules_otel and rules_flagd:** a cluster install over a pre-rendered Helm chart, with a kind smoke test.

It has no hermetic per-test server: Kyverno runs only as admission controllers inside a cluster. The CLI evaluates policies without one, so policy tests and manifest checks need no cluster. Admission itself is covered by the kind smoke test.

## Binary and toolchain

- **The binary:** the upstream `kyverno-cli_v<ver>_<os>_<arch>.tar.gz` releases (upstream calls amd64 `x86_64`) for linux and darwin on amd64 and arm64. The SHA-256s come from the release's `checksums.txt`, and `tools/update_checksums.sh` cross-checks them against the downloads.
- **Repo names carry no version:** `kyverno_<platform>`, each declaring its own toolchain. The root module's `kyverno.version()` wins; otherwise rules_kyverno's own tag applies. This is the scheme of rules_otel, rules_gitleaks and rules_flagd.
- **Same version as the install:** `//tests:version_test` keeps `DEFAULT_VERSION`, `MODULE.bazel`, the binary's `version` and every controller image in the rendered chart equal.

## Why `kyverno_policy` checks schemas itself

Exploring the 1.19.2 CLI turned up the central problem: **a policy with any unknown field is dropped silently**. This applies to `spec.rulez`, `validationFailureActon` and a ValidatingPolicy's `validationActionz` alike. `kyverno apply` then reports "Applying 0 policy rule(s)" and exits 0, and `kyverno test` would only fail if a suite expected that policy. A file that isn't YAML at all behaves the same way. So a misspelt policy deploys, and enforces nothing.

The check therefore validates every document against Kyverno's CRD schemas:
- **Where the schemas come from:** `//private:schemas` extracts every served version of every CRD in the `kyverno.io` and `policies.kyverno.io` groups at *build* time. The source is the same pre-rendered chart `kyverno_install` deploys. That's 41 kind/version pairs, about 3 MB of JSON. Extracting them at build time means no second large committed file, and the schemas can't drift from the install.
- **The validation itself:** structural, as the API server does it, minus CEL `x-kubernetes-validations`. It checks types (including int-or-string and nullable), enums and required fields. It refuses unknown fields unless the schema allows them (`additionalProperties`, `x-kubernetes-preserve-unknown-fields`, or an object without `properties`). It suggests the closest known field with `difflib`.
- **Reading YAML:** the standard library has no YAML parser, so the pinned CLI converts each document: `kyverno jp query -i <file> @` prints JSON. It reads only the first document of a file, so the driver splits multi-document files on `---` lines first. This costs about 0.1 s per document.

Then the CLI load: the rule-bearing documents are applied to a synthetic `rules-kyverno.dev/v1alpha1 LoadProbe` that no policy matches. That compiles them without depending on any resource passing. A "Policies Skipped" block, `ERROR`/`Error` lines (bad CEL) or a non-zero `error:` count fail the check with Kyverno's own text.

**Kinds the CLI maps to a GVR** before evaluating them fail on the `LoadProbe` with `failed to map gvk to gvr` (#1). These are Kubernetes `ValidatingAdmissionPolicy` / `MutatingAdmissionPolicy` and `ImageValidatingPolicy`; Kyverno's other kinds are fine. They load against a ConfigMap in a namespace nothing uses instead, and the CLI's quirks shape how:
- **A VAP or MAP is compiled only when a binding selects it.** When one does, the CLI evaluates it against any resource and ignores its `matchConstraints`. So the staged copy drops the files' bindings, whose selectors could keep the probe out, and gives every policy a plain binding of its own.
- **Only compile errors count.** A JSON report result of `error` whose message says `compil…` fails. So does a MAP's `compilation failed`, which appears only in the CLI's log (`-v 1`). Evaluation errors against the probe (`no such key: spec`) are expected and ignored.
- **A VAP's or MAP's CEL is compiled only in part (#5).** The CLI compiles `matchConditions` first, and `validations` / `mutations` only when they match, which on the probe they may not. It never reports errors in `variables`, `messageExpression` or `auditAnnotations` (Kubernetes surfaces those at evaluation, and a broken `messageExpression` just falls back to `message`). So the run also loads copies of each such policy, named `<name>--rules-kyverno-cel-<n>`:
  - one without `matchConditions`;
  - one per variable, `messageExpression` and `auditAnnotations` value, with that expression as the copy's only validation (`[\n<expr>\n].size() >= 0`) or mutation (`[JSONPatch{…}].filter(p, [\n<expr>\n].size() < 0)`). The wrapper makes the expression compile whatever its type and evaluate to nothing; the newlines keep a trailing comment in it from swallowing the wrapper. The copy keeps `variables` and `paramKind`, so references to them compile.
  - Problems are mapped back to `<name> (<field path>)`, with the CEL line number corrected for the wrapper's first line, and duplicates dropped.
- **An IVP is compiled only once it matches,** so its staged copy matches ConfigMaps. The probe has no images, so nothing is fetched.
- **A `v1beta1` MAP** is not evaluated by the 1.19.2 CLI at all ("Applying 0 policy rule(s)"). It passes without its CEL compiled.

**Problems name their file (#2).** One CLI run loads every file: about 0.5 s, which matters for a validation action on large `srcs`. Only if that run finds problems does each file load on its own, and every problem is prefixed with its path. A CEL compile error also aborts the CLI run, so the per-file runs are what surface other files' problems. If no file fails alone, the files conflict, and the combined run's problems are reported as they are.

**Which kinds go where:**
- Exceptions and cleanup, deleting and global-context kinds get the schema check only. `apply` doesn't evaluate them as policies, and it reports a cleanup policy as *failing* a resource it doesn't match.
- Kubernetes VAP/MAP kinds have no Kyverno CRD, so they get the load check only.

## Apply, test and mutate

- **`kyverno apply`:** used with `--policy-report --output-format json`, and the report (the JSON after the warnings) is parsed. Results are what decide, not the exit code, which is 1 for *any* failure, including a cleanup policy not matching.
- **Staging:** the driver splits the given policy files by kind. RULE_KINDS go to a `policies.yaml`, and `PolicyException`s to an `exceptions.yaml` passed with `--exception`. Other kinds are left out.
- **Messages:** a failure names `policy/rule`, `Kind/namespace/name` and, best effort, the resource file it came from. The file comes from a regex index of each document's `kind` and `metadata.name`, since the report doesn't say.
- **Resources are read once:** every document of every resource file is converted in one CLI call. The parsed objects serve `resource_filters`, the duplicate check and the mutated-output comparison.
- **Duplicate identities (#7):** the CLI's fake client panics on a second object with the same `apiVersion`, kind, namespace (missing = `default`) and name, naming neither file. The driver refuses them first, naming each identity and every file it's in. Objects with only `generateName` collide as name `""`, so they count too. No keep-first/keep-last switch: dropping one would hide a real conflict between roots.
- **`kyverno test`:** runs on each suite's directory with `--file-name`, `--require-tests` and `--detailed-results`. Runfiles keep workspace paths, so suites' relative paths resolve, and symlinked inputs work, as checked. A second run with `-o junit` produces the XML. The CLI prints it between log lines, so the driver cuts it out from `<?xml` to `</testsuites>`.
- **`kyverno_mutate`:** uses `apply -o <file>` and drops the empty trailing documents the CLI writes. `kyverno_apply_test` and `kyverno_check` also pass `-o`, which leaves the JSON report on stdout unchanged. The CLI's `-o` output holds every input resource (#6), so the driver keeps only those that differ from their input, matched by identity; a namespace filled in as `default` doesn't count as a change. Those go to the outputs and are compared with `expected_mutations`. `kyverno_mutate` keeps every resource: its output is the rendered result. The comparison is of canonical JSON (sorted keys) per document, with the documents converted in one CLI call, as items of one YAML sequence.

## Inputs for real-world manifests (#3)

These came from monok8s, which ran its own driver around the CLI for them.
- **`values_file`** → `--values-file`. Offline, `namespaceSelector` has no namespaces to read, so a policy that uses it matches nothing without one.
- **`expected_findings`:** a reviewed allowlist, checked in the driver after the report is parsed.
  - Matching: a `fail` or `error` result matches an entry by `policy`, optional `rule`, and `resource` as the messages print it (`Kind/namespace/name`), with `*`/`?` wildcards.
  - A matched finding is listed with its reason and passes in JUnit.
  - An entry that matches nothing fails, so the list shrinks as problems are fixed.
  - Why not PolicyExceptions: they're cluster objects, would also apply at admission, and can't fail when unused.
  - Malformed entries (unknown field, missing `reason`) are refused before anything runs.
- **`resource_filters`:** the CLI has no flag for the controller's `resourceFilters`, so the driver applies them, writing filtered copies of the resource files.
  - Kind selectors take Kyverno's forms: `Kind`, `Kind/sub`, `version/Kind`, `group/version/Kind`, `…/sub`.
  - Filters that need a subresource (`Node/?*`) never match a manifest.
  - The ConfigMap can sit in a rendered chart. `//:kyverno_manifest` is the pinned chart, for its defaults.
- **`offline_images`:** opt-in, because rewriting a policy could hide a real gap. Without network, `verifyImages` errors and fails on every matching image ("failed to fetch image", TUF downloads), even when only a digest check is wanted.
  - `strip` drops those rules. A ClusterPolicy left with no rules is dropped too, with a note in the log.
  - `digest` turns each `verifyImages` entry into `{imageReferences, skipImageReferences, verifyDigest: true, mutateDigest: false, required: false}`, which the CLI checks offline, autogen included.
  - An IVP still fetches every matched image even with no attestors, so `digest` converts it to a `ValidatingPolicy`. That policy has the same `matchConstraints`, `matchConditions`, `validationActions`, `failurePolicy` and `autogen`, and one validation: no matched container image without `@`.
    - Globs become anchored regexes. `expression` references run through `cel.bind(ref, image, …)`.
    - The pod spec is found by `has(object.spec.template)` / `has(object.spec.jobTemplate)` / `object.spec`. That works for direct workload matches and survives autogen's rewrite of `object.spec`.
  - When a test fails with registry errors and no `offline_images`, its message suggests the option.

## Install

- **The chart:** `kyverno` 3.9.2, pinned by its index digest and rendered by rules_helm at maintainer time.
  - `--no-hooks` keeps the CRD-migration (post-upgrade) and webhook-cleanup (pre-delete) Jobs out of a manifest that `kubectl apply` would run as plain Jobs. Their values are also turned off.
  - One replica per controller.
  - The render is about 92k lines, almost all CRDs, so `kubectl_apply` runs server-side; client-side apply's last-applied annotation would exceed its size limit.
- **Namespace:** the render carries `namespace: kyverno` and the service references use it, so `kyverno_install` has no `namespace` argument.
- **Webhooks:** Kyverno creates its admission webhooks itself at start-up, so none are rendered. `kyverno_health_check` waits for the four controller Deployments.
- **Policies:** `kyverno_policy_install` is `kubectl_apply` without `-n`, so namespaced policies keep their namespace. Kyverno compiles a policy and registers webhook rules for it after it's created, so "applied" isn't "enforced". `kyverno_policy_health_check` therefore reads each `kyverno_policy`'s index (written by its build-time check) and asks the cluster for each policy. A rule-bearing kind is ready when a `Ready` condition is `True` (or `status.ready` / `conditionStatus.ready`); other kinds when they exist. The smoke test checks admission right after, with server-side dry runs (`kubectl run --dry-run=server`), so no images are pulled.

## Release archive contents (v0.2.2)

`tests/` and `tools/` are `export-ignore` (`.gitattributes`): their packages load dev-only modules (rules_itest, rules_kind, rules_shell, rules_helm), which made `@rules_kyverno//...` unloadable for consumers. `REPO.bazel` `ignore_directories` would also hide them from this repo's own `bazel test`, so it isn't used. A `git_override` clone still contains them. CI's `consumer` job (`.github/consumer/check.sh`) checks the commit's `git archive`: the directories are absent and `@rules_kyverno//...` loads for a consumer.

## Not done

- **Remote policy sources and `--git-branch` suites:** the rules are hermetic, so suites and policies come from the workspace.
- **`--set` variables for `kyverno_apply_test`:** use `values_file`.
- **Image verification against real registries** (`--registry`): no network. `offline_images` reduces it to the digest check or leaves it out.
- **Chart values beyond the test footprint,** e.g. HA replicas, the reports server or policy reporter.
