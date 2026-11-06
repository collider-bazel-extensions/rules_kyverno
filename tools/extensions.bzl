"""Maintainer-only chart fetch — fires only when rules_kyverno is the
root module. Consumers don't pull the chart .tgz.
"""

load("//tools:repositories.bzl", "kyverno_chart_repository")

_version_tag = tag_class(attrs = {
    "version": attr.string(mandatory = True),
})

def _impl(mctx):
    for mod in mctx.modules:
        if not mod.is_root:
            continue
        for tag in mod.tags.version:
            kyverno_chart_repository(
                name = "kyverno_chart_" + tag.version.replace(".", "_"),
                version = tag.version,
            )

kyverno_chart = module_extension(
    implementation = _impl,
    tag_classes = {"version": _version_tag},
)
