#!/usr/bin/env python3
"""Prove a consumer cannot reach past samvada's public surface.

1.1.1 (A-6). dist/samvada.cyr is private-by-default (src/visibility.cyr),
so a consumer can name only its `public fn`s. surface_check.py reads that
boundary from the bundle's text; this compiles it. Two throwaway
consumers are generated from the bundle itself and built with `cycc`
against the vendored stdlib:

  ok.cyr   calls EVERY `public fn`        -> must compile
  bad.cyr  calls EVERY other bundle fn    -> must fail, and the compiler
                                             must name each one "private
                                             to its file" — no more, no
                                             fewer

so the claim "every internal is unreachable" is checked for all of them,
not a sample. Neither program is ever run: ok.cyr calls take_device.

Usage:  boundary_check.py <repo-root> <manifest>
Exit:   0 clean, 1 boundary broken, 2 usage/IO/toolchain error.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

FN_DECL = re.compile(r'^(public\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)', re.M)
PRIVATE_ERR = re.compile(r"'([A-Za-z_][A-Za-z0-9_]*)' is private to its file")


def die(msg, code=2):
    print(f"boundary_check: {msg}", file=sys.stderr)
    sys.exit(code)


def manifest_names(path):
    names = set()
    for raw in open(path, encoding="utf-8").read().splitlines():
        line = raw.split('#')[0].strip()
        if line:
            names.add(line.split()[0].split('/')[0])
    return names


def call(name, args):
    arity = len([a for a in args.split(',') if a.strip()]) if args.strip() else 0
    return f"    {name}({', '.join(['0'] * arity)});"


def program(calls):
    return ('include "lib/samvada.cyr"\n\nfn main() {\n    alloc_init();\n'
            + "\n".join(calls) + "\n    return 0;\n}\n\nvar r = main();\nsyscall(60, r);\n")


def build(work, name, text):
    with open(os.path.join(work, name), "w", encoding="utf-8") as f:
        f.write(text)
    with open(os.path.join(work, name), "rb") as src:
        p = subprocess.run(["cycc"], stdin=src, cwd=work,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return p.returncode, p.stderr.decode("utf-8", "replace")


def main():
    if len(sys.argv) != 3:
        die(__doc__.strip().splitlines()[-2].strip())
    root, manifest = sys.argv[1], sys.argv[2]
    if shutil.which("cycc") is None:
        die("cycc is not on PATH")
    bundle = open(os.path.join(root, "dist", "samvada.cyr"), encoding="utf-8").read()
    decls = FN_DECL.findall(bundle)
    public = {n: a for p, n, a in decls if p}
    internal = {n: a for p, n, a in decls if not p}
    if not public or not internal:
        die("parsed no public or no internal fns — bundle stale or unparsable?")

    bad = 0
    want = manifest_names(manifest)
    if set(public) != want:
        print(f"PUBLIC SET != MANIFEST: extra {sorted(set(public) - want)}, "
              f"missing {sorted(want - set(public))}")
        bad = 1

    work = tempfile.mkdtemp(prefix="samvada-boundary-")
    try:
        os.makedirs(os.path.join(work, "lib"))
        for f in os.listdir(os.path.join(root, "lib")):
            if f.endswith(".cyr"):
                shutil.copy(os.path.join(root, "lib", f), os.path.join(work, "lib", f))
        shutil.copy(os.path.join(root, "dist", "samvada.cyr"), os.path.join(work, "lib", "samvada.cyr"))

        rc, err = build(work, "ok.cyr", program([call(n, a) for n, a in sorted(public.items())]))
        if rc != 0:
            print(f"A CONSUMER CANNOT REACH THE PUBLIC SURFACE (cycc exit {rc}):")
            print("\n".join(err.splitlines()[:20]))
            bad = 1
        else:
            print(f"  ok  a consumer reaches all {len(public)} public fns")

        rc, err = build(work, "bad.cyr", program([call(n, a) for n, a in sorted(internal.items())]))
        refused = set(PRIVATE_ERR.findall(err))
        if rc == 0:
            print(f"THE BOUNDARY IS OPEN: a consumer calling all {len(internal)} internal fns COMPILED")
            bad = 1
        elif refused != set(internal):
            print(f"BOUNDARY MISMATCH: {len(internal)} internal fns, {len(refused)} refused as private")
            for n in sorted(set(internal) - refused):
                print(f"  reachable: {n}")
            for n in sorted(refused - set(internal)):
                print(f"  refused but not an internal: {n}")
            bad = 1
        else:
            print(f"  ok  all {len(internal)} internal fns are refused as private")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return bad


if __name__ == "__main__":
    sys.exit(main())
