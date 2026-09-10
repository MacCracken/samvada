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

SCOPE, stated rather than implied: only functions named `samvada_*`
are treated as public, minus the `samvada_ffi_` / `samvada_slot_` /
`samvada_backend_kind_` plumbing. The bundle exports ~198 functions in
total (the `dbus_*` internals among them) because Cyrius has no
visibility mechanism; that larger surface is documented as unstable in
docs/architecture/public-api.md and is NOT frozen here.

Usage:  surface_check.py <repo-root> <manifest>
Exit:   0 clean, 1 drift, 2 usage/IO error.
"""
import os
import re
import sys

# A public fn declared "clean" must not name a fn-table-ish parameter.
# Word-bounded so a legitimate `slots` or `table_len` does not trip it.
LEAKY_PARAM = re.compile(r'\b(table|fn_table|fntable|slot|fp|fnptr)\b')
PLUMBING = re.compile(r'^samvada_(ffi_|slot_|backend_kind_)')
FN_DECL = re.compile(r'^fn\s+(samvada_[A-Za-z0-9_]*)\s*\(([^)]*)\)', re.M)


def die(msg, code=2):
    print(f"surface_check: {msg}", file=sys.stderr)
    sys.exit(code)


def parse_bundle(path):
    try:
        src = open(path, encoding="utf-8").read()
    except OSError as e:
        die(f"cannot read {path}: {e}")
    found = {}
    for m in FN_DECL.finditer(src):
        name, args = m.group(1), m.group(2).strip()
        if PLUMBING.match(name):
            continue
        arity = len([a for a in args.split(',') if a.strip()]) if args else 0
        found[f"{name}/{arity}"] = args
    if not found:
        die(f"{path} declared no samvada_* public fns — bundle stale or unparsable?")
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
