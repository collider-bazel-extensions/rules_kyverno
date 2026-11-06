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
                   [--values-file F] [--resource-filters F]
                   [--offline-images strip|digest] [--expected-findings F]
                   [--expected-mutations F] [--mutated OUT]
                   [--json OUT] [--junit OUT] [--marker OUT]
      kyverno_apply_test / kyverno_check: every resource must pass every
      policy (fail or error results fail; warn and skip are reported),
      except findings listed in --expected-findings; a listed finding that
      no longer occurs fails. --expected-mutations: the mutated resources
      must equal these.

  kyverno.py mutate --kyverno BIN --label L --policy F... --resource F...
                    [--values-file F] [--resource-filters F]
                    [--offline-images strip|digest] --out OUT
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
# Kubernetes admission policies (evaluated by the API server; the CLI loads
# them too) and their bindings.
ADMISSION_KINDS = {"ValidatingAdmissionPolicy", "MutatingAdmissionPolicy"}
BINDING_KINDS = {k + "Binding" for k in ADMISSION_KINDS}
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


# The CLI maps an admission policy's resource to a GVR before it evaluates
# it, so their probe must be a core kind (#1).
_ADMISSION_PROBE = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: rules-kyverno-load-probe
  namespace: rules-kyverno-load-probe
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
    problems, index, groups = [], [], []
    with tempfile.TemporaryDirectory() as tmp:
        for path in args.files:
            docs = split_docs(open(path).read())
            if not docs:
                problems.append(f"{path}: no YAML documents")
                continue
            rules, admission = [], []
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
                if obj["kind"] in ADMISSION_KINDS | IMAGE_KINDS:
                    admission.append(obj)
                elif obj["kind"] in RULE_KINDS:
                    rules.append(text)
            if rules or admission:
                groups.append((path, rules, admission))
        if not problems and groups:
            problems += _load(args.kyverno, groups, tmp)
    if problems:
        _log(f"kyverno_policy {args.label}:\n" + "\n".join("  " + p for p in problems))
        return 1
    with open(args.index, "w") as f:
        json.dump(index, f, indent=1)
    with open(args.marker, "w") as f:
        f.write(f"{len(index)} policies checked\n")
    return 0


def _load(kyverno, groups, tmp):
    """The CLI loads the policies. One run for all files; if it finds
    problems, one run per file, so each problem names its file (#2). A CEL
    compile error also stops the CLI, hiding the other files' problems."""
    problems = _load_run(kyverno, groups, tmp)
    if not problems:
        return []
    if len(groups) == 1:
        return [f"{groups[0][0]}: {p}" for p in problems]
    per_file = []
    for g in groups:
        per_file += [f"{g[0]}: {p}" for p in _load_run(kyverno, [g], tmp)]
    # Nothing fails on its own: the files conflict with each other.
    return per_file or problems


def _load_run(kyverno, groups, tmp):
    """Problems loading the rule-bearing documents of `groups` [(path, rule
    texts, objects for the ConfigMap probe)]: most Kyverno kinds against a
    resource none matches; Kubernetes admission policies and
    ImageValidatingPolicies against a ConfigMap (see _load_admission)."""
    problems = []
    rules = [t for _, ts, _ in groups for t in ts]
    if rules:
        problems += _load_kyverno(kyverno, rules, tmp)
    admission = [o for _, _, os_ in groups for o in os_]
    if admission:
        problems += _load_admission(kyverno, admission, tmp)
    return problems


def _write(tmp, name, text):
    path = os.path.join(tmp, name)
    with open(path, "w") as f:
        f.write(text)
    return path


def _join(docs):
    return "\n---\n".join(d.rstrip("\n") for d in docs) + "\n"


def _load_kyverno(kyverno, texts, tmp):
    pols = _write(tmp, "load-policies.yaml", _join(texts))
    probe = _write(tmp, "probe.yaml", _PROBE)
    r = _run([kyverno, "apply", pols, "--resource", probe, "--remove-color"])
    out = r.stdout + "\n" + r.stderr
    problems = _skipped(out) + _error_lines(out)
    m = re.search(r"error: (\d+)", out)
    if not problems and m and int(m.group(1)):
        problems.append("Kyverno reported errors loading the policies:\n" + out.strip()[-1500:])
    return problems


def _skipped(out):
    if "Policies Skipped" not in out:
        return []
    block = out.split("Policies Skipped:", 1)[1].split("-" * 10, 1)[0]
    return ["Kyverno skipped: " + l.strip() for l in block.splitlines() if l.strip()]


def _error_lines(out):
    return [f"Kyverno: {l.strip()}" for l in out.splitlines() if re.match(r"\s*(ERROR|Error)\b", l)]


_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_ERR_LOG = re.compile(r'\bERR\b.*?error="((?:[^"\\]|\\.)*)".*?\bpolicy=(\S+)')


def _load_admission(kyverno, objs, tmp):
    """Kinds the CLI maps to a GVR before evaluating them, against a
    ConfigMap; only compile errors count, since evaluating a policy against
    the probe can fail.
    - ValidatingAdmissionPolicy / MutatingAdmissionPolicy: the CLI compiles
      one only when a binding selects it, and then evaluates it against any
      resource, ignoring its matchConstraints. So every policy gets a
      binding of its own (the files' bindings are left out: their selectors
      could keep the probe out). MutatingAdmissionPolicy compile errors
      appear only in the CLI's log (-v 1).
    - ImageValidatingPolicy: compiled only once it matches, so the staged
      copy matches ConfigMaps. With no images in the probe, nothing is
      fetched."""
    docs = []
    for o in objs:
        name = (o.get("metadata") or {}).get("name", "")
        if o["kind"] in IMAGE_KINDS:
            o = json.loads(json.dumps(o))
            o["spec"]["matchConstraints"] = {"resourceRules": [{
                "apiGroups": [""], "apiVersions": ["v1"], "operations": ["CREATE", "UPDATE"],
                "resources": ["configmaps"]}]}
            docs.append(json.dumps(o))
            continue
        spec = {"policyName": name}
        if o["kind"] == "ValidatingAdmissionPolicy":
            spec["validationActions"] = ["Audit"]
        docs += [json.dumps(o), json.dumps({
            "apiVersion": o["apiVersion"], "kind": o["kind"] + "Binding",
            "metadata": {"name": "rules-kyverno-load-" + name}, "spec": spec})]
    pols = _write(tmp, "load-admission.yaml", _join(docs))
    probe = _write(tmp, "admission-probe.yaml", _ADMISSION_PROBE)
    r = _run([kyverno, "apply", pols, "--resource", probe, "--remove-color", "-v", "1",
              "--policy-report", "--output-format", "json"])
    err = _ANSI.sub("", r.stderr)
    problems = _skipped(r.stdout) + _error_lines(r.stdout + "\n" + err)
    seen = set()
    for res in (_report(r.stdout) or {}).get("results") or []:
        msg = res.get("message") or ""
        if res.get("result") == "error" and "compil" in msg:
            seen.add(res.get("policy"))
            problems.append(f"Kyverno: {res.get('source')} {res.get('policy')}: {msg.strip()}")
    for m in _ERR_LOG.finditer(err):
        msg, policy = re.sub(r'\\(.)', lambda e: "\n" if e.group(1) == "n" else e.group(1), m.group(1)), m.group(2)
        if "compil" in msg and policy not in seen:
            seen.add(policy)
            problems.append(f"Kyverno: {policy}: {msg.strip()}")
    if _report(r.stdout) is None and not problems:
        problems.append(f"kyverno apply failed (exit {r.returncode}):\n"
                        + (err.strip() or r.stdout.strip())[-1500:])
    return problems


# ---- apply / mutate -----------------------------------------------------------------------


def _stage_policies(paths, tmp, kyverno=None, offline_images=None):
    """Split policy files into what `kyverno apply` evaluates (RULE_KINDS)
    and exceptions. Other kinds (cleanup and deleting policies, global
    context entries) act in a cluster, not on a resource: `apply` would
    report them as failing ("resource did not match"), so they're left out.
    With offline_images, image verification is rewritten first (see
    _offline). Returns ({"policies.yaml"|"exceptions.yaml": path}, notes)."""
    pol, exc, notes = [], [], []
    for path in paths:
        for _, text in split_docs(open(path).read()):
            kind = doc_kind(text)
            if kind in EXCEPTION_KINDS:
                exc.append(text)
            elif kind in RULE_KINDS:
                if offline_images and kind in IMAGE_KINDS | {"ClusterPolicy", "Policy"} \
                        and (kind in IMAGE_KINDS or "verifyImages" in text):
                    obj, note = _offline(to_json(kyverno, text, tmp), offline_images)
                    if note:
                        notes.append(f"{path}: {note}")
                    if obj is not None:
                        pol.append(json.dumps(obj))
                else:
                    pol.append(text)
    files = {}
    for name, docs in (("policies.yaml", pol), ("exceptions.yaml", exc)):
        if docs:
            files[name] = _write(tmp, name, _join(docs))
    return files, notes


# ---- offline image verification ---------------------------------------------------

IMAGE_KINDS = {"ImageValidatingPolicy", "NamespacedImageValidatingPolicy"}


def _offline(obj, mode):
    """Image verification needs a registry: signatures, attestations, and a
    tag's digest. `strip` removes it; `digest` keeps only the check that
    matching images are pinned by digest, which needs no network. Returns
    (policy or None if nothing is left, note)."""
    kind, name = obj["kind"], (obj.get("metadata") or {}).get("name", "?")
    spec = obj.get("spec") or {}
    if kind in IMAGE_KINDS:
        if mode == "strip" or not (spec.get("validationConfigurations") or {}).get("verifyDigest", True):
            return None, f"{kind} {name} left out (offline_images = {mode!r})"
        return _ivp_digest(obj), (f"{kind} {name}: custom image extractors (spec.images) not checked"
                                  if spec.get("images") else None)
    rules = []
    for rule in spec.get("rules") or []:
        entries = rule.pop("verifyImages", None)
        if entries is not None and mode == "digest":
            kept = []
            for e in entries:
                if not e.get("verifyDigest", True):
                    continue
                refs = e.get("imageReferences") or ([e["image"]] if e.get("image") else [])
                kept.append({k: v for k, v in (("imageReferences", refs),
                             ("skipImageReferences", e.get("skipImageReferences")),
                             ("verifyDigest", True), ("mutateDigest", False), ("required", False)) if v is not None})
            if kept:
                rule["verifyImages"] = kept
        if any(k in rule for k in ("validate", "mutate", "generate", "verifyImages")):
            rules.append(rule)
    if not rules:
        return None, f"{kind} {name} left out: no rules left (offline_images = {mode!r})"
    spec["rules"] = rules
    return obj, None


def _glob_re(glob):
    return "^" + re.escape(glob).replace("\\*", ".*").replace("\\?", ".") + "$"


def _ivp_digest(obj):
    """An ImageValidatingPolicy as a ValidatingPolicy that only checks that
    the images it matches carry a digest. The CLI fetches every matched
    image, even with no attestors, so the IVP itself can't run offline. Pod
    specs are found in Pods, workloads and CronJobs alike; with autogen,
    Kyverno's rewrite of `object.spec` lands on the right branch too."""
    spec = obj["spec"]
    match = []
    for ref in spec.get("matchImageReferences") or []:
        if ref.get("glob"):
            match.append(f"i.matches({json.dumps(_glob_re(ref['glob']))})")
        elif ref.get("expression"):
            match.append(f"cel.bind(ref, i, ({ref['expression']}))")
    pod = "variables.rulesKyvernoPodSpec"
    out = {k: spec[k] for k in ("validationActions", "matchConstraints", "matchConditions",
                                "failurePolicy", "autogen") if k in spec}
    out["variables"] = [
        {"name": "rulesKyvernoPodSpec", "expression":
            "has(object.spec.template) ? object.spec.template.spec : "
            "(has(object.spec.jobTemplate) ? object.spec.jobTemplate.spec.template.spec : object.spec)"},
        {"name": "rulesKyvernoUnpinned", "expression":
            f"({pod}.?containers.orValue([]) + {pod}.?initContainers.orValue([]) + "
            f"{pod}.?ephemeralContainers.orValue([])).map(c, c.image)"
            f".filter(i, !i.contains('@') && ({' || '.join(match) or 'true'}))"},
    ]
    out["validations"] = [{
        "expression": "size(variables.rulesKyvernoUnpinned) == 0",
        "messageExpression": "'images must be pinned by digest: ' + variables.rulesKyvernoUnpinned.join(', ')",
    }]
    return {"apiVersion": obj["apiVersion"], "kind": obj["kind"].replace("ImageValidating", "Validating"),
            "metadata": obj["metadata"], "spec": out}


# ---- several documents at once ------------------------------------------------------


def docs_to_json(kyverno, docs, tmp):
    """YAML documents → Python values with one CLI call: the documents become
    the items of one YAML sequence."""
    if not docs:
        return []
    items = []
    for d in docs:
        lines = [l for l in d.splitlines() if not l.startswith("%")]
        while lines and (not lines[0].strip() or lines[0].lstrip().startswith("#")):
            lines.pop(0)
        items.append("- " + "\n  ".join(lines))
    out = to_json(kyverno, "\n".join(items) + "\n", tmp)
    if not isinstance(out, list) or len(out) != len(docs):
        raise Failed("could not read the documents")
    return out


# ---- resourceFilters ----------------------------------------------------------------


def _wild(pat, s):
    return re.fullmatch(re.escape(pat).replace("\\*", ".*").replace("\\?", "."), s) is not None


def _parse_filters(kyverno, path, tmp):
    """`resourceFilters` of the Kyverno ConfigMap in `path` (a file that may
    hold other documents too, e.g. a rendered chart): the one named
    `kyverno`, or the only one with resourceFilters."""
    found = []
    for _, doc in split_docs(open(path).read()):
        if doc_kind(doc) == "ConfigMap" and "resourceFilters" in doc:
            obj = to_json(kyverno, doc, tmp)
            rf = (obj.get("data") or {}).get("resourceFilters")
            if rf is not None:
                found.append(((obj.get("metadata") or {}).get("name"), rf))
    named = [rf for n, rf in found if n == "kyverno"]
    if len(found) != 1 and len(named) != 1:
        raise Failed(f"{path}: no single Kyverno ConfigMap with data.resourceFilters "
                     f"(found {len(found)}; name it 'kyverno')")
    filters = []
    for f in re.findall(r"\[([^\]]*)\]", named[0] if len(named) == 1 else found[0][1]):
        parts = [x.strip() for x in f.split(",")]
        if len(parts) != 3:
            raise Failed(f"{path}: resourceFilters entry [{f}]: want [Kind,Namespace,Name]")
        k = parts[0].split("/")
        g, v, sub = "*", "*", ""
        if len(k) == 2:
            if k[0][:1].isupper():
                kind, sub = k
            else:
                v, kind = k
        elif len(k) == 3:
            if k[1][:1].isupper():
                v, kind, sub = k
            else:
                g, v, kind = k
        elif len(k) == 4:
            g, v, kind, sub = k
        else:
            kind = k[0]
        filters.append((g, v, kind, sub, parts[1], parts[2]))
    return filters


def _filtered(obj, filters):
    """Whether a resource matches a filter (the admission controller skips
    it). Manifests have no subresource; a missing namespace is ''."""
    api = obj.get("apiVersion") or ""
    g, v = api.rsplit("/", 1) if "/" in api else ("", api)
    meta = obj.get("metadata") or {}
    return any(_wild(fg, g) and _wild(fv, v) and _wild(fk, obj.get("kind") or "") and _wild(fs, "")
               and _wild(fn, meta.get("namespace") or "") and _wild(fm, meta.get("name") or "")
               for fg, fv, fk, fs, fn, fm in filters)


def _apply_filters(kyverno, resources, filters, tmp):
    """Copies of the resource files without the filtered documents.
    Returns (files, number of documents left out)."""
    out, skipped = [], 0
    for i, path in enumerate(resources):
        docs = [d for _, d in split_docs(open(path).read())]
        try:
            objs = docs_to_json(kyverno, docs, tmp)
        except Failed as e:
            raise Failed(f"{path}: {e}")
        keep = [d for d, o in zip(docs, objs) if not (isinstance(o, dict) and _filtered(o, filters))]
        skipped += len(docs) - len(keep)
        if keep:
            d = os.path.join(tmp, "filtered", str(i))
            os.makedirs(d, exist_ok=True)
            out.append(_write(d, os.path.basename(path), _join(keep)))
    return out, skipped


# ---- expected findings --------------------------------------------------------------

_EXPECTED_KEYS = {"policy", "rule", "resource", "reason"}


def _expected(kyverno, path, tmp, shown):
    """The reviewed list of known findings: a YAML list of {policy, rule
    (optional), resource (Kind/namespace/name, as the failures print it),
    reason}. `*` and `?` match any characters."""
    docs = split_docs(open(path).read())
    entries = to_json(kyverno, docs[0][1], tmp) if docs else []
    if not isinstance(entries, list):
        raise Failed(f"{shown}: want a YAML list of {{policy, rule, resource, reason}}")
    problems = []
    for i, e in enumerate(entries):
        where = f"{shown}: entry {i + 1}"
        if not isinstance(e, dict):
            problems.append(f"{where}: not a mapping")
            continue
        extra = sorted(set(e) - _EXPECTED_KEYS)
        if extra:
            problems.append(f"{where}: unknown field(s) {', '.join(extra)} "
                            f"(fields: policy, rule, resource, reason)")
        for k in ("policy", "resource", "reason"):
            if not isinstance(e.get(k), str) or not e[k].strip():
                problems.append(f"{where}: {k} is required")
    if problems:
        raise Failed("\n".join(problems))
    return entries


def _matches(entry, policy, rule, ref):
    return (_wild(entry["policy"], policy or "") and _wild(entry["resource"], ref)
            and ("rule" not in entry or _wild(str(entry["rule"]), rule or "")))


# ---- mutated output -----------------------------------------------------------------


def _clean_mutated(path):
    """The CLI's -o output without the empty documents it writes."""
    if not os.path.exists(path):
        return ""
    docs = [d for _, d in split_docs(open(path).read())]
    return "---\n".join(d if d.endswith("\n") else d + "\n" for d in docs)


def _mutation_diff(kyverno, got_text, want_path, tmp, shown):
    """Differences between the mutated resources and the expected ones,
    compared as data (formatting and key order don't count)."""
    def canon(text):
        return [json.dumps(o, indent=2, sort_keys=True).splitlines()
                for o in docs_to_json(kyverno, [d for _, d in split_docs(text)], tmp)]
    got, want = canon(got_text), canon(open(want_path).read())
    if got == want:
        return []
    flat = lambda ds: [l for i, d in enumerate(ds) for l in [f"--- document {i + 1}"] + d]
    return list(difflib.unified_diff(flat(want), flat(got), shown, "mutated", lineterm="", n=2))


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
_FLOW_NAME = re.compile(r"^metadata:\s*\{[^}]*?\bname:\s*['\"]?([^\s'\",}#]+)", re.M)
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
            k, n = doc_kind(doc), _NAME.search(doc + "\n") or _FLOW_NAME.search(doc)
            if k and n:
                out.setdefault((k, n.group(1)), label)
    return out


def _report(stdout):
    i = stdout.find('{"kind"')
    if i < 0:
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


_REGISTRY_HINT = re.compile(r"GET https?://|dial tcp|failed to fetch image|lookup .* on ")


def _stage(args, tmp):
    """Policies, exceptions and resource files for apply and mutate, with
    offline_images and resource filters applied. Returns (staged, resources,
    files, notes) or raises Failed: `resources` are the given files (for
    messages), `files` what the CLI reads."""
    res = lambda ps: [_resolve(p, args.runfiles) for p in ps]
    staged, notes = _stage_policies(res(args.policy + args.exception), tmp,
                                    args.kyverno, args.offline_images)
    if "policies.yaml" not in staged:
        raise Failed("no policies" + (" left: " + "; ".join(notes) if notes else ""))
    resources = _resource_files(res(args.resource))
    if not resources:
        raise Failed("no resource files to check")
    files = resources
    if args.resource_filters:
        filters = _parse_filters(args.kyverno, _resolve(args.resource_filters, args.runfiles), tmp)
        files, skipped = _apply_filters(args.kyverno, resources, filters, tmp)
        notes.append(f"resourceFilters left out {skipped} document(s)")
        if not files:
            raise Failed("resourceFilters left no resources")
    return staged, resources, files, notes


def _apply_cmd(args, staged, files):
    cmd = [args.kyverno, "apply", staged["policies.yaml"], "--remove-color"]
    for r in files:
        cmd += ["--resource", r]
    if "exceptions.yaml" in staged:
        cmd += ["--exception", staged["exceptions.yaml"]]
    if args.values_file:
        cmd += ["--values-file", _resolve(args.values_file, args.runfiles)]
    return cmd


def cmd_apply(args):
    with tempfile.TemporaryDirectory() as tmp:
        try:
            staged, resources, files, notes = _stage(args, tmp)
            expected = _expected(args.kyverno, _resolve(args.expected_findings, args.runfiles), tmp,
                                 args.expected_findings) \
                if args.expected_findings else []
        except Failed as e:
            _log(f"kyverno {args.label}: {e}")
            return 2
        mutated = os.path.join(tmp, "mutated.yaml")
        cmd = _apply_cmd(args, staged, files) + ["--policy-report", "--output-format", "json", "-o", mutated]
        if args.audit_warn:
            cmd.append("--audit-warn")
        r = _run(cmd)
        report = _report(r.stdout)
        if report is None:
            _log(f"kyverno {args.label}: kyverno apply failed (exit {r.returncode}):\n"
                 + (r.stderr.strip() or r.stdout.strip())[-3000:])
            return 2
        mutated_text = _clean_mutated(mutated)
        mutation_diff = []
        if args.expected_mutations:
            try:
                mutation_diff = _mutation_diff(args.kyverno, mutated_text,
                                               _resolve(args.expected_mutations, args.runfiles), tmp,
                                               args.expected_mutations)
            except Failed as e:
                mutation_diff = [f"could not compare the mutated resources: {e}"]
    if args.mutated:
        with open(args.mutated, "w") as f:
            f.write(mutated_text)
    rel = [os.path.relpath(f, _resolve(".", args.runfiles)) if args.runfiles else f for f in resources]
    origin = _origins(resources, rel)
    bad, warn, known, cases = [], [], [], []
    used = [False] * len(expected)
    registry = False
    for res in report.get("results") or []:
        result = res.get("result")
        rule = f"{res.get('policy')}" + (f"/{res['rule']}" if res.get("rule") else "")
        msg = " ".join((res.get("message") or "").split())
        for obj in res.get("resources") or [{}]:
            ref = "/".join(x for x in (obj.get("kind"), obj.get("namespace"), obj.get("name")) if x)
            src = origin.get((obj.get("kind"), obj.get("name")))
            line = f"{result:<5} {rule}  {ref}" + (f"  ({src})" if src else "") + (f": {msg}" if msg else "")
            hit = None
            if result in ("fail", "error"):
                hit = next((i for i, e in enumerate(expected)
                            if _matches(e, res.get("policy"), res.get("rule"), ref)), None)
            if hit is not None:
                used[hit] = True
                known.append(f"{line}\n      expected: {expected[hit]['reason']}")
                cases.append((f"{rule} {ref}", None))
                continue
            if result in ("fail", "error"):
                bad.append(line)
                registry |= bool(_REGISTRY_HINT.search(msg))
            elif result == "warn":
                warn.append(line)
            cases.append((f"{rule} {ref}", line if result in ("fail", "error") else None))
    for e, u in zip(expected, used):
        if not u:
            line = (f"stale expected finding (matches nothing): {e['policy']}"
                    + (f"/{e['rule']}" if e.get("rule") else "") + f"  {e['resource']}"
                    + f"  ({args.expected_findings}): {e['reason']}")
            bad.append(line)
            cases.append((f"expected {e['policy']} {e['resource']}", line))
    if mutation_diff:
        line = "mutated resources differ from " + str(args.expected_mutations) + ":\n" + \
               "\n".join("    " + l for l in mutation_diff[:300])
        bad.append(line)
        cases.append(("expected mutations", line))
    elif args.expected_mutations:
        cases.append(("expected mutations", None))
    summary = report.get("summary") or {}
    head = (f"kyverno {args.label}: " + ", ".join(f"{k} {summary.get(k, 0)}" for k in
            ("pass", "fail", "warn", "error", "skip")) + f" ({len(resources)} resource files)"
            + (f"; {len(known)} expected" if expected else ""))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=1)
    if args.junit:
        _junit(args.junit, f"kyverno {args.label}", cases or [("no policy applied", None)])
    for n in notes:
        _log(f"note: {n}")
    block = [head]
    if warn:
        block += ["warnings:"] + ["  " + w for w in warn]
    if known:
        block += ["expected findings:"] + ["  " + k for k in known]
    if bad:
        block += (["failures:"] if warn or known else []) + ["  " + b for b in bad]
        if registry and not args.offline_images:
            block.append("(image verification needs registry access; offline_images = "
                         "\"digest\" or \"strip\" checks without it)")
        _log("\n".join(block))
        return 1
    if len(block) > 1:
        _log("\n".join(block))
    if args.marker:
        with open(args.marker, "w") as f:
            f.write(head + "\n")
    print(head)
    return 0


def cmd_mutate(args):
    with tempfile.TemporaryDirectory() as tmp:
        try:
            staged, _, files, notes = _stage(args, tmp)
        except Failed as e:
            _log(f"kyverno_mutate {args.label}: {e}")
            return 2
        out = os.path.join(tmp, "out.yaml")
        r = _run(_apply_cmd(args, staged, files) + ["-o", out])
        text = r.stdout + r.stderr
        if not os.path.exists(out) or re.search(r"error: [1-9]", text) or "Policies Skipped" in text:
            _log(f"kyverno_mutate {args.label}: kyverno apply failed (exit {r.returncode}):\n"
                 + text.strip()[-3000:])
            return 1
        with open(args.out, "w") as f:
            f.write(_clean_mutated(out))
    return 0


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
        a.add_argument("--values-file")
        a.add_argument("--resource-filters")
        a.add_argument("--offline-images", choices=("strip", "digest"))
        if mode == "apply":
            a.add_argument("--audit-warn", action="store_true")
            a.add_argument("--expected-findings")
            a.add_argument("--expected-mutations")
            a.add_argument("--mutated")
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
