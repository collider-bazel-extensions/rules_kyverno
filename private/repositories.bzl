"""Per-platform kyverno repo: downloads the release tarball at the pinned
sha and declares the platform's toolchain implementation, so the repo
name (`kyverno_<platform>`) doesn't change with the version.
"""

load(":versions.bzl", "KYVERNO_VERSIONS")

_BUILD = """\
load("{toolchain_bzl}", "kyverno_toolchain")

package(default_visibility = ["//visibility:public"])

exports_files(["kyverno"])

kyverno_toolchain(
    name = "toolchain_impl",
    version = "{version}",
    kyverno = "kyverno",
)
"""

def _impl(rctx):
    version = rctx.attr.version
    platform = rctx.attr.platform
    if version not in KYVERNO_VERSIONS:
        fail("rules_kyverno: unknown kyverno version '{}'. Known: {} ".format(
            version,
            sorted(KYVERNO_VERSIONS.keys()),
        ) + "(add one with tools/update_checksums.sh <version>)")
    pin = KYVERNO_VERSIONS[version].get(platform)
    if not pin:
        fail("rules_kyverno: platform '{}' missing for version {}".format(platform, version))

    rctx.download_and_extract(url = pin["url"], sha256 = pin["sha256"])
    rctx.file("BUILD.bazel", _BUILD.format(
        toolchain_bzl = str(Label("//toolchain:toolchain.bzl")),
        version = version,
    ))

kyverno_binary_repo = repository_rule(
    implementation = _impl,
    attrs = {
        "version":  attr.string(mandatory = True),
        "platform": attr.string(mandatory = True),
    },
)
