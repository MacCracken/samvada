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

1.1.2 (P(-1), M5): declarations are read through tools/cyr_scan.py —
comments and strings blanked, matched anywhere on a line, every `fn`
token accounted for — so an internal fn declared mid-line is called by
bad.cyr like any other, instead of being skipped by a line-anchored regex.

1.1.2 (P(-1) re-verification): a `pub fn` (the lexer's `public`) and a
fn declared after an attribute on the same line (`#must_use public fn`)
are public to cyr_scan now, so ok.cyr calls them and the manifest
comparison names them; each call passes cyr_scan.arity()'s parameter
count (commas inside `<..>`, `(..)` defaults are not parameters).

1.1.2 (final pass): a fn hidden from cyr_scan behind a `#io("fd") .. "`
comment was called by NEITHER consumer, so this printed "all 233 internal
fns are refused as private" with a public one in the bundle. cyr_scan now
reads `#` as cycc does, so that fn is public to it: ok.cyr calls it and the
manifest comparison names it (ci.yml plants it). Input cycc itself reads
two ways (cyr_scan.hazards()) makes the bundle unparsable here too.

Usage:  boundary_check.py <repo-root> <manifest>
Exit:   0 clean, 1 boundary broken, 2 usage/IO/toolchain error.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True      # a gate leaves no tools/__pycache__ behind
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cyr_scan  # noqa: E402

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
    return f"    {name}({', '.join(['0'] * cyr_scan.arity(args))});"


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
    hz = cyr_scan.hazards(bundle)
    if hz:
        die("unparsable bundle: " + "; ".join(f"line {ln}: {why}" for ln, why in hz), 1)
    try:
        decls = cyr_scan.fn_decls(cyr_scan.strip(bundle))
    except ValueError as e:
        die(f"unparsable bundle: {e}", 1)
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
