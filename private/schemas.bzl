"""kyverno_schemas — the policy CRDs' OpenAPI schemas, extracted at build
time from the pre-rendered chart (//private/manifests:kyverno.yaml), so
the schema check always matches the CRDs kyverno_install deploys."""

load(":common.bzl", "TOOLCHAIN")

def _kyverno_schemas_impl(ctx):
    k = ctx.toolchains[TOOLCHAIN].kyverno
    out = ctx.actions.declare_file(ctx.label.name + ".json")
    ctx.actions.run_shell(
        command = 'exec python3 "$@"',
        arguments = [ctx.file._driver.path, "schemas", "--kyverno", k.kyverno.path,
                     "--manifest", ctx.file.manifest.path, "--out", out.path],
        inputs = [ctx.file._driver, ctx.file.manifest, k.kyverno],
        outputs = [out],
        use_default_shell_env = True,
        mnemonic = "KyvernoSchemas",
        progress_message = "Extracting Kyverno policy schemas",
    )
    return [DefaultInfo(files = depset([out]))]

kyverno_schemas = rule(
    implementation = _kyverno_schemas_impl,
    attrs = {
        "manifest": attr.label(mandatory = True, allow_single_file = True),
        "_driver": attr.label(default = "//private:kyverno.py", allow_single_file = True),
    },
    toolchains = [TOOLCHAIN],
)
