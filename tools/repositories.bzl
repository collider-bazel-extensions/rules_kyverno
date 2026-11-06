"""Maintainer-only: download + extract the Kyverno Helm chart .tgz at
the sha pinned in tools/versions.bzl. Consumers never materialize this —
the extension is dev_dependency-gated in MODULE.bazel.
"""

load("//tools:versions.bzl", "KYVERNO_CHART_VERSIONS")

_BUILD = """\
package(default_visibility = ["//visibility:public"])

filegroup(
    name = "files",
    srcs = glob(["**/*"]),
)
"""

def _impl(rctx):
    version = rctx.attr.version
    if version not in KYVERNO_CHART_VERSIONS:
        fail("rules_kyverno: unknown chart version '{}'. Known: {}".format(
            version, sorted(KYVERNO_CHART_VERSIONS.keys()),
        ))
    pin = KYVERNO_CHART_VERSIONS[version]
    rctx.download_and_extract(
        url = pin["chart_url"],
        sha256 = pin["chart_sha256"],
    )
    rctx.file("WORKSPACE", "workspace(name = \"{}\")\n".format(rctx.name))
    rctx.file("BUILD.bazel", _BUILD)

kyverno_chart_repository = repository_rule(
    implementation = _impl,
    attrs = {
        "version": attr.string(mandatory = True),
    },
)
