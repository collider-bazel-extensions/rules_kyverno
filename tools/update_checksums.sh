#!/usr/bin/env bash
# Maintainer-time script: pin the Kyverno CLI release tarballs in
# private/versions.bzl from the release's published checksums.txt.
#
#   tools/update_checksums.sh <version>   # add / re-pin <version>
#   tools/update_checksums.sh --check     # verify every pinned entry
#
# Every published sum is cross-checked by downloading the tarball and
# hashing it; any mismatch aborts without touching versions.bzl. Bump
# DEFAULT_VERSION and kyverno.version() in MODULE.bazel to match
# (//tests:version_test).
set -euo pipefail

cd "$(dirname "$0")/.."
for tool in curl python3; do
    command -v "$tool" >/dev/null 2>&1 || { echo "update_checksums: needs $tool" >&2; exit 1; }
done

work=$(mktemp -d)
trap 'rm -r -f -- "$work"' EXIT

plats=(linux_amd64 linux_arm64 darwin_amd64 darwin_arm64)
repo="https://github.com/kyverno/kyverno/releases/download"

sha256() { python3 -I -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"; }

# Prints "<platform> <url> <sha256>" for each platform of <version>,
# verified against the downloaded tarball.
published() {
    local ver="$1" plat asset url sum actual base="$repo/v$1"
    curl -fsSL -o "$work/sums" "$base/checksums.txt"
    for plat in "${plats[@]}"; do
        asset="kyverno-cli_v${ver}_${plat/amd64/x86_64}.tar.gz"   # upstream calls amd64 x86_64
        url="$base/$asset"
        sum=$(awk -v a="$asset" '$2 == a {print $1}' "$work/sums")
        [[ "$sum" =~ ^[0-9a-f]{64}$ ]] || { echo "update_checksums: no sum for $asset" >&2; exit 1; }
        curl -fsSL -o "$work/tgz" "$url"
        actual=$(sha256 "$work/tgz")
        [[ "$actual" == "$sum" ]] || { echo "update_checksums: $asset hashes to $actual, published $sum" >&2; exit 1; }
        echo "$plat $url $sum"
    done
}

if [[ "${1:-}" == "--check" ]]; then
    status=0
    for ver in $(python3 -I -c '
import re, sys
print(" ".join(sorted(set(re.findall(r"^    \"([0-9][^\"]*)\": \{", open(sys.argv[1]).read(), re.M)))))
' private/versions.bzl); do
        while read -r plat url sum; do
            if grep -qF "\"sha256\": \"$sum\"" private/versions.bzl; then
                echo "OK    $ver $plat"
            else
                echo "DRIFT $ver $plat: published $sum" >&2; status=1
            fi
        done < <(published "$ver")
    done
    exit "$status"
fi

ver="${1:?usage: tools/update_checksums.sh <version> | --check}"
published "$ver" > "$work/pins"
python3 -I - private/versions.bzl "$ver" "$work/pins" <<'PY'
import re, sys
path, ver, pins = sys.argv[1:]
rows = [l.split() for l in open(pins) if l.strip()]
entry = f'    "{ver}": {{\n'
for plat, url, sha in rows:
    entry += (f'        "{plat}": {{\n'
              f'            "url": "{url}",\n'
              f'            "sha256": "{sha}",\n'
              f'        }},\n')
entry += "    },\n"
text = open(path).read()
pat = re.compile(r'    "' + re.escape(ver) + r'": \{\n.*?\n    \},\n', re.S)
if pat.search(text):
    text = pat.sub(lambda m: entry, text)
else:
    text = text.replace("KYVERNO_VERSIONS = {\n", "KYVERNO_VERSIONS = {\n" + entry, 1)
open(path, "w").write(text)
print(f"pinned {ver} for {len(rows)} platforms in {path}")
PY
