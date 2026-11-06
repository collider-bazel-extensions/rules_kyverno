#!/usr/bin/env python3
"""Runs the Kyverno CLI for rules_kyverno. Standard library only; YAML is
converted to JSON by the pinned CLI itself (`kyverno jp query -i FILE @`).

  kyverno.py schemas --kyverno BIN --manifest M --out OUT
      Extracts the OpenAPI v3 schema of every policy CRD (groups kyverno.io
      and policies.kyverno.io, every served version) from the rendered
      chart into OUT: {"<apiVersion>/<Kind>": schema}.

  kyverno.py check --kyverno BIN --schemas S --label L --marker OUT
                   --index OUT FILE...
      kyverno_policy's validation action:
        1. Every document must be a known policy kind, valid against its CRD
           schema: no unknown fields (Kyverno silently drops a policy with
           one, so a typo means the policy enforces nothing), right types,
           enum values, required fields.
        2. The CLI must load the rule-bearing policies (`kyverno apply` on a
           resource no policy matches): bad CEL, conflicting rule types and
           the like fail here, with Kyverno's message.
      Writes OUT (marker) and the index: [{apiVersion, kind, name, namespace}].

  kyverno.py apply --kyverno BIN --label L [--runfiles] --policy F...
                   --resource F... [--exception F...] [--audit-warn]
                   [--json OUT] [--junit OUT] [--marker OUT]
      kyverno_apply_test / kyverno_check: every resource must pass every
      policy (fail or error results fail; warn and skip are reported).

  kyverno.py mutate --kyverno BIN --label L --policy F... --resource F...
                    --out OUT
      kyverno_mutate: the resources as the policies mutate them.

  kyverno.py test --kyverno BIN --label L [--runfiles] [--junit OUT] TEST_FILE...
      kyverno_test: `kyverno test` on each kyverno-test.yaml's directory.

  kyverno.py ready [--runfiles] INDEX...
      kyverno_policy_health_check: exits 0 once every policy in the indexes
      exists in the cluster and, for kinds Kyverno reports on, is Ready.
      Uses $KUBECTL (rules_kind's env file sets it) or kubectl on PATH, and
      $KUBECONFIG.

With --runfiles, paths are runfiles paths, resolved against $RUNFILES_DIR /
$TEST_SRCDIR; otherwise relative to the working directory.
"""

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape, quoteattr

POLICY_GROUPS = ("kyverno.io", "policies.kyverno.io")
# Internal kinds, never written by users.
INTERNAL_KINDS = {"UpdateRequest"}
# Kinds `kyverno apply` evaluates as policies.
RULE_KINDS = {
    "ClusterPolicy", "Policy",
    "ValidatingPolicy", "NamespacedValidatingPolicy",
    "MutatingPolicy", "NamespacedMutatingPolicy",
    "GeneratingPolicy", "NamespacedGeneratingPolicy",
    "ImageValidatingPolicy", "NamespacedImageValidatingPolicy",
    "ValidatingAdmissionPolicy", "MutatingAdmissionPolicy",
}
EXCEPTION_KINDS = {"PolicyException"}
# Kubernetes kinds Kyverno accepts that no Kyverno CRD describes: checked by
# the CLI load only.
NO_SCHEMA = {
    "admissionregistration.k8s.io/v1/ValidatingAdmissionPolicy",
    "admissionregistration.k8s.io/v1/ValidatingAdmissionPolicyBinding",
    "admissionregistration.k8s.io/v1beta1/MutatingAdmissionPolicy",
    "admissionregistration.k8s.io/v1beta1/MutatingAdmissionPolicyBinding",
    "admissionregistration.k8s.io/v1alpha1/MutatingAdmissionPolicy",
    "admissionregistration.k8s.io/v1alpha1/MutatingAdmissionPolicyBinding",
}

_PROBE = """\
apiVersion: rules-kyverno.dev/v1alpha1
kind: LoadProbe
metadata:
  name: rules-kyverno-load-probe
  namespace: default
"""


class Failed(Exception):
    pass


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def _run(cmd, **kw):
    env = dict(os.environ)
    env.setdefault("HOME", tempfile.gettempdir())
    # The CLI checks for updates and reads a kubeconfig only on request; keep
    # its cache out of the user's home.
    env["XDG_CACHE_HOME"] = env.get("TEST_TMPDIR") or tempfile.gettempdir()
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, errors="replace", env=env, **kw)


def _resolve(path, runfiles):
    if not runfiles or os.path.isabs(path):
        return path
    root = os.environ.get("RUNFILES_DIR") or os.environ.get("TEST_SRCDIR")
    if not root:
        raise SystemExit("rules_kyverno: no RUNFILES_DIR / TEST_SRCDIR")
    cands = [os.path.join(root, "_main", path), os.path.join(root, path)]
    if path.startswith("../"):
        cands.append(os.path.join(root, path[3:]))
    for c in cands:
        if os.path.exists(c):
            return c
    raise SystemExit(f"rules_kyverno: not in runfiles: {path}")


# ---- YAML documents -------------------------------------------------------------

_SEP = re.compile(r"^---[ \t]*(#.*)?$")


def split_docs(text):
    """[(first line number, text)] for each non-empty YAML document."""
    docs, cur, start = [], [], 1
    for i, line in enumerate(text.splitlines(keepends=True), 1):
        if _SEP.match(line.rstrip("\n")):
            docs.append((start, "".join(cur)))
            cur, start = [], i + 1
        else:
            cur.append(line)
    docs.append((start, "".join(cur)))
    return [(n, d) for n, d in docs
            if any(l.strip() and not l.lstrip().startswith("#") for l in d.splitlines())]


def to_json(kyverno, text, tmpdir):
    """One YAML document → Python value, via the CLI's YAML parser."""
    path = os.path.join(tmpdir, "doc.yaml")
    with open(path, "w") as f:
        f.write(text)
    r = _run([kyverno, "jp", "query", "-i", path, "@"])
    if r.returncode != 0:
        raise Failed("not valid YAML: " + " ".join((r.stderr or r.stdout).split())[:300])
    body = "\n".join(l for l in r.stdout.splitlines() if not l.startswith("# "))
    try:
        return json.loads(body)
    except ValueError as e:
        raise Failed(f"not valid YAML (unreadable conversion: {e})")


_KIND = re.compile(r"^kind:\s*['\"]?([A-Za-z0-9]+)", re.M)
_API = re.compile(r"^apiVersion:\s*['\"]?([^\s'\"#]+)", re.M)


def doc_kind(text):
    m = _KIND.search(text)
    return m.group(1) if m else None


# ---- schemas ----------------------------------------------------------------------


def cmd_schemas(args):
    text = open(args.manifest).read()
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        for _, doc in split_docs(text):
            if doc_kind(doc) != "CustomResourceDefinition":
                continue
            if not re.search(r"^\s+group:\s*(" + "|".join(map(re.escape, POLICY_GROUPS)) + r")\s*$",
                             doc, re.M):
                continue
            crd = to_json(args.kyverno, doc, tmp)
            spec = crd["spec"]
            kind = spec["names"]["kind"]
            if spec["group"] not in POLICY_GROUPS or kind in INTERNAL_KINDS:
                continue
            for v in spec.get("versions", []):
                if not v.get("served", True):
                    continue
                schema = (v.get("schema") or {}).get("openAPIV3Schema")
                if schema:
                    out[f"{spec['group']}/{v['name']}/{kind}"] = schema
    if not out:
        raise SystemExit("rules_kyverno: no policy CRDs found in " + args.manifest)
    with open(args.out, "w") as f:
        json.dump(out, f, sort_keys=True)
    return 0


# ---- schema validation ---------------------------------------------------------------

_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "integer": int, "number": (int, float),
}


def _type_ok(value, schema):
    if value is None:
        return bool(schema.get("nullable"))
    if schema.get("x-kubernetes-int-or-string"):
        return isinstance(value, (int, str)) and not isinstance(value, bool)
    t = schema.get("type")
    if not t:
        return True
    if isinstance(value, bool) and t in ("integer", "number"):
        return False
    if t == "integer" and isinstance(value, float):
        return value.is_integer()
    return isinstance(value, _TYPES.get(t, object))


def validate(value, schema, path, errs):
    """Structural validation as the API server does it, minus CEL rules."""
    if not schema:
        return
    if not _type_ok(value, schema):
        want = "int or string" if schema.get("x-kubernetes-int-or-string") else schema.get("type")
        got = "null" if value is None else type(value).__name__.replace("dict", "object").replace("list", "array").replace("str", "string")
        errs.append(f"{path}: expected {want}, got {got}")
        return
    if value is None:
        return
    enum = schema.get("enum")
    if enum is not None and value not in enum:
        errs.append(f"{path}: {json.dumps(value)} is not one of {', '.join(json.dumps(e) for e in enum)}")
    if isinstance(value, dict):
        props = schema.get("properties")
        extra = schema.get("additionalProperties")
        for req in schema.get("required", []):
            if req not in value:
                errs.append(f"{path}: missing required field {req!r}")
        if props is None and extra is None:
            return  # free-form (or preserve-unknown-fields) object
        for k, v in value.items():
            p = f"{path}.{k}" if path else k
            if props is not None and k in props:
                validate(v, props[k], p, errs)
            elif isinstance(extra, dict):
                validate(v, extra, p, errs)
            elif extra is True or schema.get("x-kubernetes-preserve-unknown-fields"):
                continue
            else:
                near = difflib.get_close_matches(k, list(props or {}), n=1, cutoff=0.75)
                errs.append(f"{p}: unknown field {k!r}" + (f" (did you mean {near[0]!r}?)" if near else ""))
    elif isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{i}]", errs)


# ---- check ----------------------------------------------------------------------------


def cmd_check(args):
    with open(args.schemas) as f:
        schemas = json.load(f)
    problems, index, rule_files = [], [], []
    with tempfile.TemporaryDirectory() as tmp:
        for path in args.files:
            docs = split_docs(open(path).read())
            if not docs:
                problems.append(f"{path}: no YAML documents")
                continue
            has_rules = False
            for line, text in docs:
                where = f"{path}:{line}"
                try:
                    obj = to_json(args.kyverno, text, tmp)
                except Failed as e:
                    problems.append(f"{where}: {e}")
                    continue
                if not isinstance(obj, dict) or "kind" not in obj or "apiVersion" not in obj:
                    problems.append(f"{where}: not a Kubernetes object (needs apiVersion and kind)")
                    continue
                key = f"{obj['apiVersion']}/{obj['kind']}"
                meta = obj.get("metadata") or {}
                name = meta.get("name", "?")
                where = f"{where} ({obj['kind']} {name})"
                if key in schemas:
                    errs = []
                    validate(obj, schemas[key], "", errs)
                    problems += [f"{where}: {e}" for e in errs]
                elif key not in NO_SCHEMA:
                    known = sorted({k.rsplit("/", 1)[1] for k in schemas})
                    problems.append(f"{where}: {key} is not a Kyverno policy kind "
                                    f"(known: {', '.join(known)})")
                    continue
                if not meta.get("name"):
                    problems.append(f"{where}: metadata.name is required")
                index.append({"apiVersion": obj["apiVersion"], "kind": obj["kind"],
                              "name": name, "namespace": meta.get("namespace", "")})
                has_rules |= obj["kind"] in RULE_KINDS
            if has_rules:
                rule_files.append(path)
        if not problems and rule_files:
            problems += _load(args.kyverno, rule_files, tmp)
    if problems:
        _log(f"kyverno_policy {args.label}:\n" + "\n".join("  " + p for p in problems))
        return 1
    with open(args.index, "w") as f:
        json.dump(index, f, indent=1)
    with open(args.marker, "w") as f:
        f.write(f"{len(index)} policies checked\n")
    return 0


def _load(kyverno, files, tmp):
    """The CLI loads the policies: apply them to a resource none matches."""
    probe = os.path.join(tmp, "probe.yaml")
    with open(probe, "w") as f:
        f.write(_PROBE)
    r = _run([kyverno, "apply", *files, "--resource", probe, "--remove-color"])
    out = r.stdout + "\n" + r.stderr
    problems = []
    if "Policies Skipped" in out:
        block = out.split("Policies Skipped:", 1)[1].split("-" * 10, 1)[0]
        problems += ["Kyverno skipped: " + l.strip() for l in block.splitlines() if l.strip()]
    errors = [l.strip() for l in out.splitlines() if re.match(r"\s*(ERROR|Error)\b", l)]
    problems += [f"Kyverno: {l}" for l in errors]
    m = re.search(r"error: (\d+)", out)
    if not problems and m and int(m.group(1)):
        problems.append("Kyverno reported errors loading the policies:\n" + out.strip()[-1500:])
    return problems


# ---- apply / mutate -----------------------------------------------------------------------


def _stage_policies(paths, tmp):
    """Split policy files into what `kyverno apply` evaluates (RULE_KINDS)
    and exceptions. Other kinds (cleanup and deleting policies, global
    context entries) act in a cluster, not on a resource: `apply` would
    report them as failing ("resource did not match"), so they're left out."""
    pol, exc = [], []
    for path in paths:
        for _, text in split_docs(open(path).read()):
            kind = doc_kind(text)
            if kind in EXCEPTION_KINDS:
                exc.append(text)
            elif kind in RULE_KINDS:
                pol.append(text)
    files = {}
    for name, docs in (("policies.yaml", pol), ("exceptions.yaml", exc)):
        if docs:
            p = os.path.join(tmp, name)
            with open(p, "w") as f:
                f.write("\n---\n".join(d.rstrip("\n") for d in docs) + "\n")
            files[name] = p
    return files


def _resource_files(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            for d, _, names in sorted(os.walk(p, followlinks=True)):
                out += [os.path.join(d, n) for n in sorted(names)
                        if n.endswith((".yaml", ".yml", ".json"))]
        else:
            out.append(p)
    return out


_NAME = re.compile(r"^metadata:\s*\n(?:[ \t]+.*\n)*?[ \t]+name:\s*['\"]?([^\s'\"#]+)", re.M)
_NS = re.compile(r"^metadata:\s*\n(?:[ \t]+.*\n)*?[ \t]+namespace:\s*['\"]?([^\s'\"#]+)", re.M)


def _origins(files, labels):
    """(kind, name) → file it came from, for messages (best effort)."""
    out = {}
    for f, label in zip(files, labels):
        try:
            text = open(f).read()
        except OSError:
            continue
        for _, doc in split_docs(text):
            k, n = doc_kind(doc), _NAME.search(doc + "\n")
            if k and n:
                out.setdefault((k, n.group(1)), label)
    return out


def _report(stdout):
    i = stdout.find("{")
    if i < 0:
        return None
    try:
        return json.loads(stdout[i:])
    except ValueError:
        return None


def _junit(path, suite, cases):
    fails = sum(1 for _, m in cases if m)
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<testsuites><testsuite name={quoteattr(suite)} tests="{len(cases)}" failures="{fails}">']
    for name, msg in cases:
        if msg:
            out.append(f"<testcase name={quoteattr(name)}><failure message={quoteattr(msg.splitlines()[0])}>"
                       f"{escape(msg)}</failure></testcase>")
        else:
            out.append(f"<testcase name={quoteattr(name)}/>")
    out.append("</testsuite></testsuites>")
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")


def cmd_apply(args):
    policies = [_resolve(p, args.runfiles) for p in args.policy]
    labels = list(args.resource)
    resources = _resource_files([_resolve(p, args.runfiles) for p in args.resource])
    if not resources:
        _log(f"kyverno {args.label}: no resource files to check")
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        staged = _stage_policies(policies + [_resolve(p, args.runfiles) for p in args.exception], tmp)
        if "policies.yaml" not in staged:
            _log(f"kyverno {args.label}: no policies")
            return 2
        cmd = [args.kyverno, "apply", staged["policies.yaml"], "--remove-color",
               "--policy-report", "--output-format", "json"]
        for r in resources:
            cmd += ["--resource", r]
        if "exceptions.yaml" in staged:
            cmd += ["--exception", staged["exceptions.yaml"]]
        if args.audit_warn:
            cmd.append("--audit-warn")
        r = _run(cmd)
    report = _report(r.stdout)
    if report is None:
        _log(f"kyverno {args.label}: kyverno apply failed (exit {r.returncode}):\n"
             + (r.stderr.strip() or r.stdout.strip())[-3000:])
        return 2
    rel = [os.path.relpath(f, _resolve(".", args.runfiles)) if args.runfiles else f for f in resources]
    origin = _origins(resources, rel)
    bad, warn, cases = [], [], []
    for res in report.get("results") or []:
        result = res.get("result")
        rule = f"{res.get('policy')}" + (f"/{res['rule']}" if res.get("rule") else "")
        msg = " ".join((res.get("message") or "").split())
        for obj in res.get("resources") or [{}]:
            ref = "/".join(x for x in (obj.get("kind"), obj.get("namespace"), obj.get("name")) if x)
            src = origin.get((obj.get("kind"), obj.get("name")))
            line = f"{result:<5} {rule}  {ref}" + (f"  ({src})" if src else "") + (f": {msg}" if msg else "")
            if result in ("fail", "error"):
                bad.append(line)
            elif result == "warn":
                warn.append(line)
            cases.append((f"{rule} {ref}", line if result in ("fail", "error") else None))
    summary = report.get("summary") or {}
    head = (f"kyverno {args.label}: " + ", ".join(f"{k} {summary.get(k, 0)}" for k in
            ("pass", "fail", "warn", "error", "skip")) + f" ({len(resources)} resource files)")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=1)
    if args.junit:
        _junit(args.junit, f"kyverno {args.label}", cases or [("no policy applied", None)])
    if warn:
        _log(head + "\nwarnings:\n" + "\n".join("  " + w for w in warn))
    if bad:
        _log(head + "\n" + "\n".join("  " + b for b in bad))
        return 1
    if args.marker:
        with open(args.marker, "w") as f:
            f.write(head + "\n")
    print(head)
    return 0


def cmd_mutate(args):
    resources = _resource_files(args.resource)
    with tempfile.TemporaryDirectory() as tmp:
        staged = _stage_policies(args.policy + args.exception, tmp)
        if "policies.yaml" not in staged:
            _log(f"kyverno_mutate {args.label}: no policies")
            return 2
        out = os.path.join(tmp, "out.yaml")
        cmd = [args.kyverno, "apply", staged["policies.yaml"], "--remove-color", "-o", out]
        for r in resources:
            cmd += ["--resource", r]
        if "exceptions.yaml" in staged:
            cmd += ["--exception", staged["exceptions.yaml"]]
        r = _run(cmd)
        text = r.stdout + r.stderr
        if not os.path.exists(out) or re.search(r"error: [1-9]", text) or "Policies Skipped" in text:
            _log(f"kyverno_mutate {args.label}: kyverno apply failed (exit {r.returncode}):\n"
                 + text.strip()[-3000:])
            return 1
        # Drop the trailing empty documents the CLI writes.
        docs = [d for _, d in split_docs(open(out).read())]
        with open(args.out, "w") as f:
            f.write("---\n".join(d if d.endswith("\n") else d + "\n" for d in docs))
    return 0


# ---- test -------------------------------------------------------------------------------------


def _junit_from(stdout):
    i, j = stdout.find("<?xml"), stdout.rfind("</testsuites>")
    return stdout[i:j + len("</testsuites>")] + "\n" if i >= 0 and j > i else None


def cmd_test(args):
    status, suites = 0, []
    for t in args.tests:
        path = _resolve(t, args.runfiles)
        d = os.path.dirname(path)
        name = os.path.basename(path)
        _log(f"=== kyverno test {t}")
        r = _run([args.kyverno, "test", d, "--file-name", name, "--remove-color",
                  "--require-tests", "--detailed-results"])
        sys.stderr.write(r.stdout + r.stderr)
        if r.returncode != 0:
            status = 1
        if args.junit:
            j = _run([args.kyverno, "test", d, "--file-name", name, "--remove-color",
                      "-o", "junit"])
            x = _junit_from(j.stdout)
            if x:
                suites.append(x)
    if args.junit and suites:
        if len(suites) == 1:
            body = suites[0]
        else:  # merge: keep each file's <testsuite> elements
            inner = [re.sub(r"^.*?<testsuites[^>]*>|</testsuites>\s*$", "", s, flags=re.S) for s in suites]
            body = '<?xml version="1.0" encoding="UTF-8"?>\n<testsuites>' + "".join(inner) + "</testsuites>\n"
        with open(args.junit, "w") as f:
            f.write(body)
    if status:
        _log(f"kyverno_test {args.label}: failed")
    return status


def _ready(obj):
    """(ready, why) for a policy object from the API server."""
    st = obj.get("status") or {}
    conds = st.get("conditions") or (st.get("conditionStatus") or {}).get("conditions") or []
    for c in conds:
        if c.get("type") == "Ready":
            return c.get("status") == "True", c.get("message") or c.get("reason") or "not ready"
    for holder in (st, st.get("conditionStatus") or {}):
        if "ready" in holder:
            return bool(holder["ready"]), holder.get("message") or "not ready"
    if obj.get("kind") in RULE_KINDS:
        return False, "no status yet"
    return True, ""  # exceptions, cleanup policies, ...: existing is enough


def cmd_ready(args):
    kubectl = os.environ.get("KUBECTL") or shutil.which("kubectl")
    if not kubectl:
        _log("kyverno_policy_health_check: kubectl not found ($KUBECTL or PATH)")
        return 2
    pending = []
    for idx in args.indexes:
        with open(_resolve(idx, args.runfiles)) as f:
            entries = json.load(f)
        for e in entries:
            group = e["apiVersion"].split("/")[0] if "/" in e["apiVersion"] else ""
            res = e["kind"].lower() + (f".{group}" if group else "")
            cmd = [kubectl, "get", res, e["name"], "-o", "json"]
            if e.get("namespace"):
                cmd += ["-n", e["namespace"]]
            r = _run(cmd)
            ref = f"{e['kind']}/{e['name']}" + (f" (namespace {e['namespace']})" if e.get("namespace") else "")
            if r.returncode != 0:
                # Skip kubectl's deprecation warnings: the error is the reason.
                err = " ".join(l for l in r.stderr.splitlines() if not l.startswith("Warning:"))
                pending.append(f"{ref}: {' '.join(err.split())[:200]}")
                continue
            ok, why = _ready(json.loads(r.stdout))
            if not ok:
                pending.append(f"{ref}: {why}")
    if pending:
        _log("kyverno_policy_health_check: not ready:\n" + "\n".join("  " + p for p in pending))
        return 1
    return 0


def main(argv):
    p = argparse.ArgumentParser(prog="kyverno.py")
    sub = p.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("schemas")
    s.add_argument("--kyverno", required=True)
    s.add_argument("--manifest", required=True)
    s.add_argument("--out", required=True)
    c = sub.add_parser("check")
    for a in ("--kyverno", "--schemas", "--marker", "--index"):
        c.add_argument(a, required=True)
    c.add_argument("--label", default="")
    c.add_argument("files", nargs="+")
    for mode in ("apply", "mutate"):
        a = sub.add_parser(mode)
        a.add_argument("--kyverno", required=True)
        a.add_argument("--label", default="")
        a.add_argument("--runfiles", action="store_true")
        a.add_argument("--policy", action="append", default=[])
        a.add_argument("--resource", action="append", default=[])
        a.add_argument("--exception", action="append", default=[])
        if mode == "apply":
            a.add_argument("--audit-warn", action="store_true")
            a.add_argument("--json")
            a.add_argument("--junit")
            a.add_argument("--marker")
        else:
            a.add_argument("--out", required=True)
    rd = sub.add_parser("ready")
    rd.add_argument("--runfiles", action="store_true")
    rd.add_argument("indexes", nargs="+")
    t = sub.add_parser("test")
    t.add_argument("--kyverno", required=True)
    t.add_argument("--label", default="")
    t.add_argument("--runfiles", action="store_true")
    t.add_argument("--junit")
    t.add_argument("tests", nargs="+")
    args = p.parse_args(argv)
    if args.mode == "ready":
        return cmd_ready(args)
    if getattr(args, "runfiles", False):
        args.kyverno = _resolve(args.kyverno, True)
    args.kyverno = os.path.abspath(args.kyverno)
    return {"schemas": cmd_schemas, "check": cmd_check, "apply": cmd_apply,
            "mutate": cmd_mutate, "test": cmd_test}[args.mode](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
