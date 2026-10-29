#!/usr/bin/env python3
"""Freeze samvada's public API surface mechanically.

CLAUDE.md states two invariants that were, until 1.0.1, enforced by
REVIEW ONLY:

  * "Public API frozen — every exported symbol documented + tested"
  * "Do not expose FFI types in public function signatures"

The 1.0.1 A-6 audit demonstrated that both were unenforced: an agent
added a public fn returning the raw FFI fn-table and another accepting
one, and lint, fmt, vet, distlib, build, smoke, the slot cross-check
and every test assert passed. Review is exactly what AUDIT-4 survived.

This gate reads dist/samvada.cyr — the bundle a `[deps.samvada]`
consumer actually links, not src/ — and diffs its public set against a
checked-in manifest. Adding, removing or re-signing a public fn now
requires editing the manifest, which is what makes "frozen" mean
something.

SCOPE, from 1.1.1 (A-6): the bundle is private-by-default
(src/visibility.cyr), so the public surface is exactly its `public fn`s,
and this gate checks two things about it: that set equals the manifest,
and the bundle really opens with its `private` marker, before any
top-level declaration. That is stricter than any toolchain needs — what
breaks is a marker after a top-level STATEMENT — but it is valid on all
of them, and consumers may pin as low as 6.6.5. Until 1.1.1 Cyrius-level
visibility was unused, every one of the bundle's 204 fns was reachable,
and this gate approximated "public" as `samvada_*` minus the FFI plumbing.
tools/boundary_check.py proves the boundary by compiling against it.

Usage:  surface_check.py <repo-root> <manifest>
Exit:   0 clean, 1 drift, 2 usage/IO error.
"""
import os
import re
import sys

# A public fn declared "clean" must not name a fn-table-ish parameter.
# Word-bounded so a legitimate `slots` or `table_len` does not trip it.
LEAKY_PARAM = re.compile(r'\b(table|fn_table|fntable|slot|fp|fnptr)\b')
PUBLIC_DECL = re.compile(r'^public\s+fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)', re.M)
# A top-level declaration: what must not come before the `private` marker.
TOP_DECL = re.compile(r'^(public\s+)?(fn|var|const|enum|struct|union|impl)\b', re.M)
MARKER = re.compile(r'^private\s*;?\s*$', re.M)


def die(msg, code=2):
    print(f"surface_check: {msg}", file=sys.stderr)
    sys.exit(code)


def parse_bundle(path):
    try:
        src = open(path, encoding="utf-8").read()
    except OSError as e:
        die(f"cannot read {path}: {e}")
    marker = MARKER.search(src)
    if marker is None:
        print("BUNDLE IS NOT PRIVATE: no top-level `private` marker, so every "
              "internal fn is consumer-reachable (src/visibility.cyr missing from [lib] modules?)")
        return None
    first = TOP_DECL.search(src)
    if first is not None and first.start() < marker.start():
        print("`private` IS NOT THE BUNDLE'S FIRST DECLARATION: keep src/visibility.cyr "
              "first in [lib] modules. Measured: after a top-level statement cyrius 6.6.5 and "
              "6.7.5 refuse the marker ('unexpected private') and 6.7.18 refuses every later "
              "`public`; leading the bundle is valid on all of them")
        return None
    found = {}
    for m in PUBLIC_DECL.finditer(src):
        name, args = m.group(1), m.group(2).strip()
        arity = len([a for a in args.split(',') if a.strip()]) if args else 0
        found[f"{name}/{arity}"] = args
    if not found:
        die(f"{path} declared no public fns — bundle stale or unparsable?")
    return found


def parse_manifest(path):
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError as e:
        die(f"cannot read {path}: {e}")
    want = {}
    for lineno, raw in enumerate(lines, 1):
        line = raw.split('#')[0].strip()
        if not line:
            continue
        parts = line.split()
        key = parts[0]
        if '/' not in key:
            die(f"{path}:{lineno}: expected 'name/arity', got {key!r}")
        rule = parts[1] if len(parts) > 1 else 'clean'
        if rule not in ('clean', 'leaks-fn-table'):
            die(f"{path}:{lineno}: unknown rule {rule!r} "
                "(expected 'clean' or 'leaks-fn-table')")
        want[key] = rule
    if not want:
        die(f"{path} lists no entries")
    return want


def main():
    if len(sys.argv) != 3:
        die(__doc__.strip().splitlines()[-2].strip())
    root, manifest_path = sys.argv[1], sys.argv[2]
    bundle = os.path.join(root, 'dist', 'samvada.cyr')
    found = parse_bundle(bundle)
    if found is None:
        return 1
    want = parse_manifest(manifest_path)

    bad = 0
    for key in sorted(set(found) | set(want)):
        if key not in want:
            print(f"NEW PUBLIC EXPORT (not in manifest): fn {key.split('/')[0]}({found[key]})")
            bad = 1
        elif key not in found:
            print(f"REMOVED OR RE-SIGNED PUBLIC EXPORT: {key}")
            bad = 1
        else:
            print(f"  ok  {key:34s} {want[key]}")

    for key, args in sorted(found.items()):
        if want.get(key) == 'clean' and LEAKY_PARAM.search(args):
            print(f"FFI TYPE LEAK: fn {key.split('/')[0]}({args}) exposes the fn-table")
            bad = 1

    if bad:
        print("\nThe public surface drifted. If the change is INTENDED, edit "
              f"{manifest_path} and document it in docs/architecture/public-api.md.")
    else:
        print(f"\npublic surface matches the manifest ({len(want)} fns)")
    return bad


if __name__ == '__main__':
    sys.exit(main())
