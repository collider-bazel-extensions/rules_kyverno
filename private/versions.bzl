"""Kyverno CLI pins.

One release tarball per platform from kyverno/kyverno, holding the single
`kyverno` binary:

    https://github.com/kyverno/kyverno/releases/download/v<ver>/kyverno-cli_v<ver>_<os>_<arch>.tar.gz

(upstream names amd64 `x86_64`). SHA-256s are the release's published
`checksums.txt`. Maintainer flow:

    tools/update_checksums.sh <ver>     # add / re-pin <ver> from checksums.txt
    tools/update_checksums.sh --check   # verify every pin, no writes

//tests:checksums_test fails on an empty or malformed value, and
//tests:version_test if DEFAULT_VERSION differs from MODULE.bazel's
kyverno.version(), the binary or the image the chart deploys.
"""

# The version the `kyverno` extension fetches when the root module names none.
DEFAULT_VERSION = "1.19.2"

KYVERNO_VERSIONS = {
    "1.19.2": {
        "linux_amd64": {
            "url": "https://github.com/kyverno/kyverno/releases/download/v1.19.2/kyverno-cli_v1.19.2_linux_x86_64.tar.gz",
            "sha256": "357eaf1df8bc24ddd5a73979fadeaae6a032e8710f11af264229756e4956bf4a",
        },
        "linux_arm64": {
            "url": "https://github.com/kyverno/kyverno/releases/download/v1.19.2/kyverno-cli_v1.19.2_linux_arm64.tar.gz",
            "sha256": "250f2a02f143d5c6425e686b13c130d3a3ba7372dba6a7210d599d1b0dd12e92",
        },
        "darwin_amd64": {
            "url": "https://github.com/kyverno/kyverno/releases/download/v1.19.2/kyverno-cli_v1.19.2_darwin_x86_64.tar.gz",
            "sha256": "b645654ab8e65bd4aa64de9228e274b388bab19b3404a4e9af70174b3d5ad230",
        },
        "darwin_arm64": {
            "url": "https://github.com/kyverno/kyverno/releases/download/v1.19.2/kyverno-cli_v1.19.2_darwin_arm64.tar.gz",
            "sha256": "7915c03c613c22363a100adb3da8d06f2e2626d9d21a98b97f921795ffdc536f",
        },
    },
}

PLATFORMS = {
    "linux_amd64":  ["@platforms//os:linux", "@platforms//cpu:x86_64"],
    "linux_arm64":  ["@platforms//os:linux", "@platforms//cpu:arm64"],
    "darwin_amd64": ["@platforms//os:osx",   "@platforms//cpu:x86_64"],
    "darwin_arm64": ["@platforms//os:osx",   "@platforms//cpu:arm64"],
}
