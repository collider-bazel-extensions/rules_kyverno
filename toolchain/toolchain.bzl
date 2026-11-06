"""kyverno_toolchain — exposes the platform-resolved `kyverno` binary
as a `ToolchainInfo`, so rules resolve it via
`ctx.toolchains["@rules_kyverno//toolchain:kyverno"]`.

`current_kyverno_toolchain` forwards the resolved binary as a plain target
(files + runfiles) for `data = [...]` use in tests.
"""

load("//private:providers.bzl", "KyvernoBinaryInfo")

KYVERNO_TOOLCHAIN_TYPE = Label("//toolchain:kyverno")

def _toolchain_impl(ctx):
    info = KyvernoBinaryInfo(
        version = ctx.attr.version,
        kyverno = ctx.file.kyverno,
    )
    return [
        platform_common.ToolchainInfo(kyverno = info),
        DefaultInfo(
            files = depset([ctx.file.kyverno]),
            runfiles = ctx.runfiles(files = [ctx.file.kyverno]),
        ),
    ]

kyverno_toolchain = rule(
    implementation = _toolchain_impl,
    attrs = {
        "version": attr.string(mandatory = True),
        "kyverno": attr.label(allow_single_file = True, mandatory = True),
    },
)

def _current_impl(ctx):
    info = ctx.toolchains[KYVERNO_TOOLCHAIN_TYPE].kyverno
    return [
        info,
        DefaultInfo(
            files = depset([info.kyverno]),
            runfiles = ctx.runfiles(files = [info.kyverno]),
        ),
    ]

current_kyverno_toolchain = rule(
    implementation = _current_impl,
    toolchains = [KYVERNO_TOOLCHAIN_TYPE],
    doc = "The resolved kyverno binary, as files and runfiles.",
)
