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

**Which kinds go where:**
- Exceptions and cleanup, deleting and global-context kinds get the schema check only. `apply` doesn't evaluate them as policies, and it reports a cleanup policy as *failing* a resource it doesn't match.
- Kubernetes VAP/MAP kinds have no Kyverno CRD, so they get the load check only.

## Apply, test and mutate

- **`kyverno apply`:** used with `--policy-report --output-format json`, and the report (the JSON after the warnings) is parsed. Results are what decide, not the exit code, which is 1 for *any* failure, including a cleanup policy not matching.
- **Staging:** the driver splits the given policy files by kind. RULE_KINDS go to a `policies.yaml`, and `PolicyException`s to an `exceptions.yaml` passed with `--exception`. Other kinds are left out.
- **Messages:** a failure names `policy/rule`, `Kind/namespace/name` and, best effort, the resource file it came from. The file comes from a regex index of each document's `kind` and `metadata.name`, since the report doesn't say.
- **`kyverno test`:** runs on each suite's directory with `--file-name`, `--require-tests` and `--detailed-results`. Runfiles keep workspace paths, so suites' relative paths resolve, and symlinked inputs work, as checked. A second run with `-o junit` produces the XML. The CLI prints it between log lines, so the driver cuts it out from `<?xml` to `</testsuites>`.
- **`kyverno_mutate`:** uses `apply -o <file>` and drops the empty trailing documents the CLI writes.

## Install

- **The chart:** `kyverno` 3.9.2, pinned by its index digest and rendered by rules_helm at maintainer time.
  - `--no-hooks` keeps the CRD-migration (post-upgrade) and webhook-cleanup (pre-delete) Jobs out of a manifest that `kubectl apply` would run as plain Jobs. Their values are also turned off.
  - One replica per controller.
  - The render is about 92k lines, almost all CRDs, so `kubectl_apply` runs server-side; client-side apply's last-applied annotation would exceed its size limit.
- **Namespace:** the render carries `namespace: kyverno` and the service references use it, so `kyverno_install` has no `namespace` argument.
- **Webhooks:** Kyverno creates its admission webhooks itself at start-up, so none are rendered. `kyverno_health_check` waits for the four controller Deployments.
- **Policies:** `kyverno_policy_install` is `kubectl_apply` without `-n`, so namespaced policies keep their namespace. Kyverno compiles a policy and registers webhook rules for it after it's created, so "applied" isn't "enforced". `kyverno_policy_health_check` therefore reads each `kyverno_policy`'s index (written by its build-time check) and asks the cluster for each policy. A rule-bearing kind is ready when a `Ready` condition is `True` (or `status.ready` / `conditionStatus.ready`); other kinds when they exist. The smoke test checks admission right after, with server-side dry runs (`kubectl run --dry-run=server`), so no images are pulled.

## Not done (v0.1)

- **Remote policy sources and `--git-branch` suites:** the rules are hermetic, so suites and policies come from the workspace.
- **Variables and values files for `kyverno_apply_test`** (`--values-file`, `--set`): `kyverno test` suites support them.
- **Image verification against real registries** (`--registry`): no network.
- **Chart values beyond the test footprint,** e.g. HA replicas, the reports server or policy reporter.
