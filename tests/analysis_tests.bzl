"""Analysis-time tests: providers, output groups, executables, and the
analysis errors for bad attributes."""

load("@bazel_skylib//lib:unittest.bzl", "analysistest", "asserts")
load(
    "//:defs.bzl",
    "KyvernoPolicyInfo",
    "kyverno_apply_test",
    "kyverno_check",
    "kyverno_install",
    "kyverno_policy",
    "kyverno_policy_health_check",
    "kyverno_test",
)

def _policy_info_impl(ctx):
    env = analysistest.begin(ctx)
    target = analysistest.target_under_test(env)
    info = target[KyvernoPolicyInfo]
    # deps bundle in order: images, then team.
    asserts.equals(env, ["no_latest_cel.yaml", "require_team.yaml"],
                   [f.basename for f in info.files.to_list()])
    asserts.equals(env, 2, len(info.index.to_list()))
    return analysistest.end(env)

_policy_info_test = analysistest.make(_policy_info_impl)

def _validation_impl(ctx):
    env = analysistest.begin(ctx)
    groups = analysistest.target_under_test(env)[OutputGroupInfo]
    asserts.true(env, len(groups._validation.to_list()) >= 1, "expected a validation output")
    return analysistest.end(env)

_validation_test = analysistest.make(_validation_impl)

def _check_outputs_impl(ctx):
    env = analysistest.begin(ctx)
    groups = analysistest.target_under_test(env)[OutputGroupInfo]
    asserts.equals(env, ["rendered_check.kyverno.json", "rendered_check.mutated.yaml"],
                   sorted([f.basename for f in groups.kyverno.to_list()]))
    return analysistest.end(env)

_check_outputs_test = analysistest.make(_check_outputs_impl)

def _has_executable_impl(ctx):
    env = analysistest.begin(ctx)
    target = analysistest.target_under_test(env)
    asserts.true(env, target[DefaultInfo].files_to_run.executable != None,
                 "expected target to expose an executable")
    return analysistest.end(env)

_has_executable_test = analysistest.make(_has_executable_impl)

def _fails_impl(ctx):
    env = analysistest.begin(ctx)
    asserts.expect_failure(env, ctx.attr.message)
    return analysistest.end(env)

_fails_test = analysistest.make(
    _fails_impl,
    expect_failure = True,
    attrs = {"message": attr.string()},
)

def analysis_test_suite(name):
    s = name + "_subject"
    m = ["manual"]
    kyverno_policy(name = s + "_empty_policy", tags = m)
    kyverno_test(name = s + "_empty_suite", tags = m)
    kyverno_apply_test(name = s + "_no_resources", policies = ["//tests/policies:team"], tags = m)
    kyverno_install(name = s + "_install", tags = m)
    kyverno_policy_health_check(name = s + "_policy_health", policies = ["//tests/policies:all"], tags = m)
    tests = {
        "_policy_info": (_policy_info_test, "//tests/policies:all", None),
        "_policy_validation": (_validation_test, "//tests/policies:team", None),
        "_check_validation": (_validation_test, ":rendered_check", None),
        "_check_outputs": (_check_outputs_test, ":rendered_check", None),
        "_install_executable": (_has_executable_test, ":" + s + "_install", None),
        "_policy_health_executable": (_has_executable_test, ":" + s + "_policy_health", None),
        "_empty_policy": (_fails_test, ":" + s + "_empty_policy", "srcs is empty"),
        "_empty_suite": (_fails_test, ":" + s + "_empty_suite", "srcs is empty"),
        "_no_resources": (_fails_test, ":" + s + "_no_resources", "resources is empty"),
    }
    for suffix, (rule, target, message) in tests.items():
        if message:
            rule(name = name + suffix, target_under_test = target, message = message)
        else:
            rule(name = name + suffix, target_under_test = target)
    native.test_suite(name = name, tests = [":" + name + t for t in tests])
