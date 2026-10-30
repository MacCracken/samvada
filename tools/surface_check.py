#!/usr/bin/env python3
"""Freeze samvada's public API surface mechanically.

Two invariants (the first a v1.0 criterion of the pre-1.0 roadmap, the
second a CLAUDE.md rule) were, until 1.0.1, enforced by REVIEW ONLY:

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
of them, and consumers may pin as low as 6.6.13. Until 1.1.1 Cyrius-level
visibility was unused, every one of the bundle's 201 fns was reachable,
and this gate approximated "public" as `samvada_*` minus the FFI plumbing.
tools/boundary_check.py proves the boundary by compiling against it.

1.1.2 (P(-1), M5): the bundle is read through tools/cyr_scan.py, with
comments and strings blanked and declarations matched anywhere on a line.
The regexes here were anchored at line start, so a `public fn` written
after other code on the same line was not seen at all — a new export
passed the freeze. cyr_scan also counts every `fn` and `public` token
against the declarations it parsed, so an unfamiliar form fails here.

1.1.2 (P(-1) re-verification): two more ways past this gate, both closed
in cyr_scan and both planted by ci.yml after this gate's clean run:
`#must_use public fn x() {..}` — cycc compiles the rest of an attribute
line, cyr_scan read it as a comment — and `pub`, the lexer's synonym for
`public`: `pub fn` counted as internal, and `pub var` / `pub struct` were
not counted at all, so internal state was consumer-writable while this
gate printed "matches the manifest". Arity now counts parameters, not
commas (`Result<i64, i64>` is one), so a re-signed export cannot keep its
name/arity key by adding a generic.

1.1.2 (final pass): three more, each planted by ci.yml after this gate's
clean run and each demonstrated with a consumer that compiled and called
the planted name:
  * a `#io("fd") .. "` or `#note: the "` comment, and an object-like
    `#define NAME "`, opened a string in cyr_scan that hid the next lines
    — a `public fn` there passed this gate, the boundary gate and the
    security scan. cyr_scan now copies cycc's `#` rule exactly.
  * `#derive(Serialize)` on an enum makes cycc emit `public fn
    _cy_enum_name_eq` whatever the file's visibility, an export with no
    `fn` token in the bundle's text; and an enum's members are names in
    the consumer's scope (visibility covers fns and vars only). The bundle
    may now hold no `#derive`, no `enum` and no `#define`
    (cyr_scan.bundle_problems()), and nothing cycc reads two ways
    (cyr_scan.hazards()) — so "the public surface is exactly the bundle's
    `public fn`s" is a claim this gate can check.

Usage:  surface_check.py <repo-root> <manifest>
Exit:   0 clean, 1 drift, 2 usage/IO error.
"""
import os
import re
import sys

sys.dont_write_bytecode = True      # a gate leaves no tools/__pycache__ behind
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cyr_scan  # noqa: E402

# A public fn declared "clean" must not name a fn-table-ish parameter.
# Word-bounded so a legitimate `slots` or `table_len` does not trip it.
LEAKY_PARAM = re.compile(r'\b(table|fn_table|fntable|slot|fp|fnptr)\b')


def die(msg, code=2):
    print(f"surface_check: {msg}", file=sys.stderr)
    sys.exit(code)


def parse_bundle(path):
    try:
        src = open(path, encoding="utf-8").read()
    except OSError as e:
        die(f"cannot read {path}: {e}")
    code = cyr_scan.strip(src)
    why = cyr_scan.private_first(code)
    if why is not None and why.startswith("no "):
        print("BUNDLE IS NOT PRIVATE: no top-level `private` marker, so every "
              "internal fn is consumer-reachable (src/visibility.cyr missing from [lib] modules?)")
        return None
    if why is not None:
        print("`private` IS NOT THE BUNDLE'S FIRST DECLARATION: keep src/visibility.cyr "
              "first in [lib] modules. Measured: after a top-level statement cyrius 6.6.5 and "
              "6.7.5 refuse the marker ('unexpected private') and 6.7.18 refuses every later "
              "`public`; leading the bundle is valid on all of them")
        return None
    try:
        decls = cyr_scan.fn_decls(code)
    except ValueError as e:
        print(f"UNPARSABLE BUNDLE: {e}")
        return None
    problems = cyr_scan.bundle_problems(src)
    if problems:
        for ln, why in problems:
            print(f"BUNDLE DECLARES WHAT NO MANIFEST ROW COVERS: dist/samvada.cyr:{ln}: {why}")
        return None
    found = {}
    for public, name, args in decls:
        if public:
            found[f"{name}/{cyr_scan.arity(args)}"] = args.strip()
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
