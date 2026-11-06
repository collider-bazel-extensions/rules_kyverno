"""Providers exposed by rules_kyverno."""

KyvernoBinaryInfo = provider(
    doc = "The Kyverno CLI toolchain binary.",
    fields = {
        "version": "string — pinned Kyverno version",
        "kyverno": "File — the kyverno executable",
    },
)

KyvernoPolicyInfo = provider(
    doc = "Checked Kyverno policies (kyverno_policy).",
    fields = {
        "files": "depset of File — policy files, deps first",
        "index": "File — JSON list of {kind, name, namespace} for every policy, " +
                 "written by the check (kyverno_policy_install waits on these)",
    },
)
