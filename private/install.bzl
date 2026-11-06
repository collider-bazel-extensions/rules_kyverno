"""Cluster install: Kyverno itself, and policies.

kyverno_install applies the pinned, pre-rendered `kyverno` chart (CRDs
included, rendered with --no-hooks) with rules_kubectl and waits for the
four controllers. The chart is rendered for namespace `kyverno`: its
objects and service references carry it, so the namespace is fixed.

kyverno_policy_install applies kyverno_policy targets (each policy keeps
its own namespace); kyverno_policy_health_check passes once every policy
exists and Kyverno reports it Ready, so a test starts only after the
policies are being enforced.
"""

load("@rules_kubectl//:defs.bzl", "kubectl_apply", "kubectl_apply_health_check")
load(":common.bzl", "shell_quote")
load(":providers.bzl", "KyvernoPolicyInfo")

KYVERNO_NAMESPACE = "kyverno"
KYVERNO_DEPLOYMENTS = [
    "kyverno-admission-controller",
    "kyverno-background-controller",
    "kyverno-cleanup-controller",
    "kyverno-reports-controller",
]
_MANIFEST = Label("//private/manifests:kyverno.yaml")

def kyverno_install(name, wait_timeout = "300s", **kwargs):
    """Apply Kyverno (CRDs + controllers) into namespace `kyverno`; waits
    until the four controller Deployments are Available, then idles.
    Drops into `itest_service.exe`."""
    kubectl_apply(
        name = name,
        manifests = [_MANIFEST],
        namespace = KYVERNO_NAMESPACE,
        create_namespace = True,
        # The CRDs are too large for client-side apply's annotation.
        server_side = True,
        wait_for_deployments = KYVERNO_DEPLOYMENTS + kwargs.pop("wait_for_deployments", []),
        wait_timeout = wait_timeout,
        **kwargs
    )

def kyverno_health_check(name, **kwargs):
    """Readiness probe paired with kyverno_install."""
    kubectl_apply_health_check(
        name = name,
        namespace = KYVERNO_NAMESPACE,
        wait_for_deployments = KYVERNO_DEPLOYMENTS + kwargs.pop("wait_for_deployments", []),
        **kwargs
    )

def kyverno_policy_install(name, policies, wait_timeout = "120s", **kwargs):
    """Apply kyverno_policy targets (each document keeps its namespace).
    Pair it with kyverno_policy_health_check, and make the itest_service
    depend on kyverno_install's."""
    kubectl_apply(
        name = name,
        manifests = policies,
        namespace = "",
        apply_namespace_flag = False,
        server_side = True,
        wait_timeout = wait_timeout,
        **kwargs
    )

def _kyverno_policy_health_check_impl(ctx):
    indexes = depset(transitive = [p[KyvernoPolicyInfo].index for p in ctx.attr.policies]).to_list()
    script = ctx.actions.declare_file(ctx.label.name + ".sh")
    driver = ctx.file._driver.short_path
    ctx.actions.write(script, """\
#!/usr/bin/env bash
# kyverno_policy_health_check: every policy exists and is Ready.
set -euo pipefail
if [[ -z "${{RUNFILES_DIR:-}}" ]]; then
  if [[ -n "${{TEST_SRCDIR:-}}" ]]; then RUNFILES_DIR="$TEST_SRCDIR"
  elif [[ -d "${{0}}.runfiles" ]]; then RUNFILES_DIR="${{0}}.runfiles"
  fi
  export RUNFILES_DIR
fi
driver=""
for c in "$RUNFILES_DIR/_main/{driver}" "$RUNFILES_DIR/{driver_ext}"; do
  [[ -e "$c" ]] && {{ driver="$c"; break; }}
done
[[ -n "$driver" ]] || {{ echo "kyverno_policy_health_check: not in runfiles: {driver}" >&2; exit 2; }}
exec python3 "$driver" ready --runfiles {indexes}
""".format(
        driver = driver,
        driver_ext = driver[3:] if driver.startswith("../") else driver,
        indexes = " ".join([shell_quote(i.short_path) for i in indexes]),
    ), is_executable = True)
    return [DefaultInfo(executable = script, runfiles = ctx.runfiles(files = [ctx.file._driver] + indexes))]

kyverno_policy_health_check = rule(
    implementation = _kyverno_policy_health_check_impl,
    executable = True,
    doc = "Exits 0 once every policy exists and Kyverno reports it Ready.",
    attrs = {
        "policies": attr.label_list(providers = [KyvernoPolicyInfo], mandatory = True),
        "_driver": attr.label(default = "//private:kyverno.py", allow_single_file = True),
    },
)
