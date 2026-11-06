"""Bzlmod extension. Single `version` tag — fetches the kyverno release
tarball for each supported platform as `kyverno_<platform>`.

The root module's tag wins; without one, rules_kyverno' own applies, so
consumers declare nothing unless they want another pinned version.
"""

load("//private:repositories.bzl", "kyverno_binary_repo")
load("//private:versions.bzl", "DEFAULT_VERSION", "PLATFORMS")

_version_tag = tag_class(attrs = {
    "version": attr.string(mandatory = True),  # e.g. "8.30.1"
})

def _impl(mctx):
    root, own = [], []
    for mod in mctx.modules:
        if mod.is_root:
            root += [t.version for t in mod.tags.version]
        elif mod.name == "rules_kyverno":
            own += [t.version for t in mod.tags.version]

    # Only one module's tags apply (the same is_root rule as across this
    # organization): repos are named per platform, not per version.
    versions = root or own or [DEFAULT_VERSION]
    if len({v: True for v in versions}) > 1:
        fail("rules_kyverno: kyverno.version() named more than one version: {}".format(versions))
    for plat in PLATFORMS.keys():
        kyverno_binary_repo(
            name = "kyverno_" + plat,
            version = versions[0],
            platform = plat,
        )

kyverno = module_extension(
    implementation = _impl,
    tag_classes = {"version": _version_tag},
)
