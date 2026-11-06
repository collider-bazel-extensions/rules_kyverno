"""kyverno_test and kyverno_apply_test — test rules.

kyverno_test runs `kyverno test` on kyverno-test.yaml suites: each lists
policies, resources and the expected result per policy / rule / resource.
Paths in a suite are relative to its file, so the files it names go in
`data` (or `policies`) under the same relative paths.

kyverno_apply_test applies policies to resources, for example rendered
manifests, and fails if any result is `fail` or `error`, naming the policy,
rule, resource and the file it came from.
"""

load(":common.bzl", "TOOLCHAIN", "label_text", "shell_quote")
load(":providers.bzl", "KyvernoPolicyInfo")

def _wrapper(ctx, mode, args, reports):
    driver = ctx.file._driver.short_path
    out = ctx.actions.declare_file(ctx.label.name + ".sh")
    ctx.actions.expand_template(
        template = ctx.file._tmpl,
        output = out,
        substitutions = {
            "__DRIVER__": driver,
            "__DRIVER_EXT__": driver[3:] if driver.startswith("../") else driver,
            "__MODE__": mode,
            "__ARGS__": " ".join([shell_quote(a) for a in args]),
            "__REPORTS__": "\n".join(reports),
        },
        is_executable = True,
    )
    return out

def policy_files(targets):
    return depset(transitive = [t[KyvernoPolicyInfo].files for t in targets]).to_list()

def _kyverno_test_impl(ctx):
    k = ctx.toolchains[TOOLCHAIN].kyverno
    if not ctx.files.srcs:
        fail("{}: srcs is empty: give the kyverno-test.yaml file(s).".format(ctx.label))
    args = ["--kyverno", k.kyverno.short_path, "--label", label_text(ctx.label)]
    args += [f.short_path for f in ctx.files.srcs]
    wrapper = _wrapper(ctx, "test", args, [])
    runfiles = ctx.runfiles(
        files = [wrapper, ctx.file._driver, k.kyverno] + ctx.files.srcs + ctx.files.data +
                policy_files(ctx.attr.policies),
    )
    for d in ctx.attr.data:
        runfiles = runfiles.merge(d[DefaultInfo].default_runfiles)
    return [DefaultInfo(executable = wrapper, runfiles = runfiles)]

kyverno_test = rule(
    implementation = _kyverno_test_impl,
    test = True,
    doc = "Runs `kyverno test` on kyverno-test.yaml suites (JUnit to Bazel).",
    attrs = {
        "srcs": attr.label_list(
            allow_files = [".yaml", ".yml"],
            doc = "kyverno-test.yaml files (any name); each runs in its own directory.",
        ),
        "policies": attr.label_list(
            providers = [KyvernoPolicyInfo],
            doc = "kyverno_policy targets the suites name (checked at build time too).",
        ),
        "data": attr.label_list(
            allow_files = True,
            doc = "Everything else the suites name: resources, values, variables.",
        ),
        "_driver": attr.label(default = "//private:kyverno.py", allow_single_file = True),
        "_tmpl": attr.label(default = "//private:run.sh.tmpl", allow_single_file = True),
    },
    toolchains = [TOOLCHAIN],
)

def apply_args(ctx, runfiles):
    path = (lambda f: f.short_path) if runfiles else (lambda f: f.path)
    pols = policy_files(ctx.attr.policies)
    if not pols:
        fail("{}: policies is empty.".format(ctx.label))
    if not ctx.files.resources:
        fail("{}: resources is empty: nothing to check.".format(ctx.label))
    args = ["--label", label_text(ctx.label)]
    for f in pols:
        args += ["--policy", path(f)]
    for f in ctx.files.resources:
        args += ["--resource", path(f)]
    exc = policy_files(ctx.attr.exceptions)
    for f in exc:
        args += ["--exception", path(f)]
    if ctx.attr.audit_warn:
        args.append("--audit-warn")
    return args, pols + exc + ctx.files.resources

APPLY_ATTRS = {
    "policies": attr.label_list(
        providers = [KyvernoPolicyInfo],
        mandatory = True,
        doc = "kyverno_policy targets to apply (PolicyExceptions among them apply too).",
    ),
    "resources": attr.label_list(
        allow_files = True,
        doc = "Resource files, generated files or directories (e.g. rendered Helm output).",
    ),
    "exceptions": attr.label_list(
        providers = [KyvernoPolicyInfo],
        doc = "kyverno_policy targets holding extra PolicyExceptions.",
    ),
    "audit_warn": attr.bool(
        doc = "Report Audit-mode policies' failures as warnings instead of failures.",
    ),
    "_driver": attr.label(default = "//private:kyverno.py", allow_single_file = True),
}

def _kyverno_apply_test_impl(ctx):
    k = ctx.toolchains[TOOLCHAIN].kyverno
    args, files = apply_args(ctx, True)
    wrapper = _wrapper(ctx, "apply", ["--kyverno", k.kyverno.short_path] + args,
                       ['reports+=(--json "$out/kyverno-report.json")'])
    return [DefaultInfo(executable = wrapper, runfiles = ctx.runfiles(
        files = [wrapper, ctx.file._driver, k.kyverno] + files,
    ))]

kyverno_apply_test = rule(
    implementation = _kyverno_apply_test_impl,
    test = True,
    doc = "Fails if any resource fails (or errors on) any policy. JUnit to " +
          "Bazel; the policy report (JSON) to the test's undeclared outputs.",
    attrs = dict(APPLY_ATTRS, _tmpl = attr.label(default = "//private:run.sh.tmpl", allow_single_file = True)),
    toolchains = [TOOLCHAIN],
)
