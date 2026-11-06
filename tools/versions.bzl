"""Maintainer-side: Kyverno Helm chart pin for the helm-render flow.

Consumers don't see this — it's loaded only by the dev-only chart-fetch
extension under tools/. The committed manifest at
`//private/manifests:kyverno.yaml` is what consumers apply, and
`//private/schemas:schemas.json` (the policy CRDs' OpenAPI schemas) is
extracted from it.

The chart is the official `kyverno` chart (https://kyverno.github.io/kyverno);
the sha256 is the digest from the chart repository's index.yaml.

Update flow:
    1. Add an entry (chart_url + chart_sha256).
    2. Update the `helm_template` + writeback blocks in tools/BUILD.bazel.
    3. `tools/render_kyverno.sh <version>`; then `bazel run //tools:extract_schemas`.
"""

KYVERNO_CHART_VERSIONS = {
    "3.9.2": {
        # appVersion v1.19.2 (matches the pinned CLI).
        "chart_url": "https://kyverno.github.io/kyverno/kyverno-3.9.2.tgz",
        "chart_sha256": "f6fe7184544d5e2ee33a77808c09e67aa603ce6560cc197038fde31d727c861e",
    },
}
