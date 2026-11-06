"""kyverno_policy and kyverno_policy_test.

kyverno_policy checks policy files on every build (a validation action):

  1. Each document is a Kyverno policy kind and valid against its CRD's
     OpenAPI schema, taken from the pinned chart: no unknown fields, right
     types, enum values, required fields. Kyverno itself drops a policy
     with an unknown field without a word, so a typo such as
     `validationFailureActon` would leave the policy enforcing nothing.
  2. The Kyverno CLI loads the rule-bearing policies, so a bad CEL
     expression or a rule with two validation types fails with Kyverno's
     message.

kyverno_policy_test runs the same check at test time and expects it to
fail (or pass), so a project can test its own policy hygiene.
"""

load(":common.bzl", "TOOLCHAIN", "label_text", "shell_quote")
load(":providers.bzl", "KyvernoPolicyInfo")

_SCHEMAS = "//private:schemas"
_YAML = [".yaml", ".yml"]

def _kyverno_policy_impl(ctx):
    k = ctx.toolchains[TOOLCHAIN].kyverno
    if not ctx.files.srcs and not ctx.attr.deps:
        fail("{}: srcs is empty.".format(ctx.label))
    files = depset(ctx.files.srcs, transitive = [d[KyvernoPolicyInfo].files for d in ctx.attr.deps])
    own_index = []
    validation = []
    if ctx.files.srcs:
        marker = ctx.actions.declare_file(ctx.label.name + ".checked")
        index = ctx.actions.declare_file(ctx.label.name + ".policies.json")
        outputs = [marker, index]
        own_index = [index]
        validation = [marker]
        args = ctx.actions.args()
        args.add(ctx.file._driver)
        args.add("check")
        args.add_all(["--kyverno", k.kyverno, "--schemas", ctx.file._schemas,
                      "--label", label_text(ctx.label), "--marker", marker, "--index", index])
        args.add_all(ctx.files.srcs)
        ctx.actions.run_shell(
            command = 'exec python3 "$@"',
            arguments = [args],
            inputs = ctx.files.srcs + [ctx.file._driver, ctx.file._schemas, k.kyverno],
            outputs = outputs,
            use_default_shell_env = True,
            mnemonic = "KyvernoCheckPolicy",
            progress_message = "Checking Kyverno policies %{label}",
        )
    return [
        DefaultInfo(files = files),
        KyvernoPolicyInfo(
            files = files,
            index = depset(own_index, transitive = [d[KyvernoPolicyInfo].index for d in ctx.attr.deps]),
        ),
        OutputGroupInfo(_validation = depset(validation)),
    ]

kyverno_policy = rule(
    implementation = _kyverno_policy_impl,
    doc = "Kyverno policy files, schema-checked and loaded by the CLI on every build.",
    attrs = {
        "srcs": attr.label_list(
            allow_files = _YAML,
            doc = "Policy files (any Kyverno policy kind, PolicyExceptions, " +
                  "cleanup policies; several documents per file are fine).",
        ),
        "deps": attr.label_list(
            providers = [KyvernoPolicyInfo],
            doc = "Other kyverno_policy targets, bundled in front of srcs.",
        ),
        "_driver": attr.label(default = "//private:kyverno.py", allow_single_file = True),
        "_schemas": attr.label(default = _SCHEMAS, allow_single_file = True),
    },
    toolchains = [TOOLCHAIN],
)

def _kyverno_policy_test_impl(ctx):
    k = ctx.toolchains[TOOLCHAIN].kyverno
    script = ctx.actions.declare_file(ctx.label.name + ".sh")
    files = ctx.files.srcs
    ctx.actions.write(script, """\
#!/usr/bin/env bash
# kyverno_policy_test: the policy check, expected to {want}.
set -uo pipefail
out=$(python3 {driver} check --kyverno {kyverno} --schemas {schemas} --label {label} \\
      --marker "$TEST_TMPDIR/marker" --index "$TEST_TMPDIR/index.json" {files} 2>&1)
rc=$?
printf '%s\\n' "$out"
expect={expect}
if [[ -z "$expect" ]]; then
  [[ $rc == 0 ]] || {{ echo "FAIL: the check refused {names}" >&2; exit 1; }}
  echo "PASS: the check accepted {names}"
  exit 0
fi
[[ $rc != 0 ]] || {{ echo "FAIL: the check accepted {names}; expected an error matching: $expect" >&2; exit 1; }}
grep -Eq -- "$expect" <<<"$out" || {{ echo "FAIL: the error does not match: $expect" >&2; exit 1; }}
echo "PASS: refused as expected ($expect)"
""".format(
        want = "fail matching " + ctx.attr.expect_error if ctx.attr.expect_error else "pass",
        driver = shell_quote(ctx.file._driver.short_path),
        kyverno = shell_quote(k.kyverno.short_path),
        schemas = shell_quote(ctx.file._schemas.short_path),
        label = shell_quote(label_text(ctx.label)),
        files = " ".join([shell_quote(f.short_path) for f in files]),
        expect = shell_quote(ctx.attr.expect_error),
        names = " ".join([f.basename for f in files]),
    ), is_executable = True)
    return [DefaultInfo(executable = script, runfiles = ctx.runfiles(
        files = files + [ctx.file._driver, ctx.file._schemas, k.kyverno],
    ))]

kyverno_policy_test = rule(
    implementation = _kyverno_policy_test_impl,
    test = True,
    doc = "Runs kyverno_policy's check on files at test time: passes if the " +
          "check fails with a message matching `expect_error` (or, with no " +
          "`expect_error`, if it passes).",
    attrs = {
        "srcs": attr.label_list(allow_files = _YAML, mandatory = True),
        "expect_error": attr.string(
            doc = "Extended regex the check's error output must match. Empty: expect success.",
        ),
        "_driver": attr.label(default = "//private:kyverno.py", allow_single_file = True),
        "_schemas": attr.label(default = _SCHEMAS, allow_single_file = True),
    },
    toolchains = [TOOLCHAIN],
)
