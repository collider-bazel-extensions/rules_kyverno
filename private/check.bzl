"""kyverno_check and kyverno_mutate — build actions.

kyverno_check is kyverno_apply_test as a validation action: `bazel build`
fails if a resource violates a policy. Use it to keep generated manifests
compliant. The policy report (JSON) is in the `kyverno` output group.

kyverno_mutate writes the resources as the policies mutate them (one
multi-document YAML file), e.g. to see or ship what admission would apply.
"""

load(":common.bzl", "TOOLCHAIN", "label_text")
load(":test.bzl", "APPLY_ATTRS", "apply_args")

def _kyverno_check_impl(ctx):
    k = ctx.toolchains[TOOLCHAIN].kyverno
    args, files = apply_args(ctx, False)
    report = ctx.actions.declare_file(ctx.label.name + ".kyverno.json")
    ctx.actions.run_shell(
        command = 'exec python3 "$@"',
        arguments = [ctx.file._driver.path, "apply", "--kyverno", k.kyverno.path,
                     "--json", report.path] + args,
        inputs = files + [ctx.file._driver, k.kyverno],
        outputs = [report],
        use_default_shell_env = True,
        mnemonic = "KyvernoCheck",
        progress_message = "Applying Kyverno policies to %{label}",
    )
    return [
        DefaultInfo(files = depset()),
        OutputGroupInfo(_validation = depset([report]), kyverno = depset([report])),
    ]

kyverno_check = rule(
    implementation = _kyverno_check_impl,
    doc = "Fails `bazel build` if any resource fails (or errors on) any policy.",
    attrs = APPLY_ATTRS,
    toolchains = [TOOLCHAIN],
)

def _kyverno_mutate_impl(ctx):
    k = ctx.toolchains[TOOLCHAIN].kyverno
    args, files = apply_args(ctx, False)
    args = [a for a in args if a != "--audit-warn"]
    out = ctx.actions.declare_file(ctx.attr.out or ctx.label.name + ".yaml")
    ctx.actions.run_shell(
        command = 'exec python3 "$@"',
        arguments = [ctx.file._driver.path, "mutate", "--kyverno", k.kyverno.path,
                     "--out", out.path] + args,
        inputs = files + [ctx.file._driver, k.kyverno],
        outputs = [out],
        use_default_shell_env = True,
        mnemonic = "KyvernoMutate",
        progress_message = "Mutating %{label} with Kyverno policies",
    )
    return [DefaultInfo(files = depset([out]))]

kyverno_mutate = rule(
    implementation = _kyverno_mutate_impl,
    doc = "The resources as the policies mutate them, as one YAML file.",
    attrs = dict(APPLY_ATTRS, out = attr.string(doc = "Output file name (default <name>.yaml).")),
    toolchains = [TOOLCHAIN],
)
