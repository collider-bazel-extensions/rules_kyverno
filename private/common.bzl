"""Shared pieces of the rules_kyverno rules."""

TOOLCHAIN = "//toolchain:kyverno"

def label_text(label):
    s = str(label)
    return s[2:] if s.startswith("@@//") else s

def shell_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"
