#!/usr/bin/env python3
"""Keep docs/development/state.md's derived facts in step with the tree.

state.md is a snapshot: it says what is true now. Its narrative is
written by hand, but the facts that can be read off the tree are
generated into marked blocks and never typed: the version and release
date, the toolchain pin, the CI runners and pinned actions, the public
and internal fn counts, the FFI table, the [lib] modules and stdlib
leaves, the assert count of every suite, the errno-map, fixture and
bench-baseline counts.

    <!-- state-sync:begin NAME -->
    ...generated; an edit here is overwritten...
    <!-- state-sync:end NAME -->

One exception: the tests table's Covers column (and its row order) is
written by hand and kept across regenerations. A new suite gets a row
marked undescribed, which keeps the snapshot stale until a person says
what it covers. Never put a count in a Covers cell; nothing checks it.

The narrative cannot be generated, so it carries an acknowledgement
instead: `<!-- state-sync:narrative X.Y.Z -->`. A person sets it, after
rewriting the hand-written sections for that version. This tool never
does, and reports the snapshot stale while it differs from VERSION. It
cannot tell whether the prose was actually rewritten — only that
someone said so.

Why a generator and not a release post-hook (which CLAUDE.md described
until this tool, and which never existed): release.yml writes nothing
back to the repo, and a commit made after the tag would leave the tag's
own state.md stale. So the refresh runs before the tag, the way `cyrius
distlib` does, and release.yml refuses a tag whose state.md is stale.

It runs every tests/**/*.tcyr suite on its own (`cyrius test <file>`) to
count asserts, so the toolchain must be on PATH, and it reads dist/, so
run `cyrius distlib` first. A failing suite is an error, never a count.

Exit status. With --check nothing is written: 0 current; 1 stale (a
block differs from the tree, a suite is undescribed, or the narrative is
for another version); 2 an error (a suite failed, a fact could not be
read, a block is malformed, anything unexpected). Without --check the
blocks are rewritten, and the exit is 1 only for what a rewrite cannot
fix (an undescribed suite, a narrative for another version), 2 on an
error. ci.yml turns a stale snapshot into a warning, since it is
refreshed per release rather than per push, and fails on an error;
release.yml fails on both.

usage: state_sync.py [--check | --self-test]
"""
import contextlib
import difflib
import glob
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import cyr_scan  # noqa: E402

STATE = os.path.join("docs", "development", "state.md")
BLOCKS = ("version", "glance", "tests")
MARKER = re.compile(r'^<!-- state-sync:(begin|end) ([a-z]+) -->[ \t]*$', re.M)
NARRATIVE = re.compile(r'^<!-- state-sync:narrative (\S+) -->[ \t]*$', re.M)
TALLY = re.compile(r'^(\d+) passed, (\d+) failed \((\d+) total\)[ \t]*$', re.M)
SUITE_ROW = re.compile(r'^\|\s*`([^`]+)`\s*\|[^|]*\|\s*(.*?)\s*\|[ \t]*$')
NULLARY = r'\s*\(\s*\)(?:\s*:\s*\w+)?\s*\{\s*return\s+(\d+)\s*;\s*\}'
UNDESCRIBED = "**(undescribed — say what this suite covers)**"


class Error(Exception):
    pass


def read(root, rel):
    try:
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        raise Error(f"cannot read {rel}: {e.strerror}")
    except UnicodeDecodeError as e:
        raise Error(f"{rel} is not UTF-8 ({e.reason} at byte {e.start})")


def one(pattern, text, what, flags=re.M):
    found = re.findall(pattern, text, flags)
    if len(found) != 1:
        raise Error(f"{what}: expected exactly one, found {len(found)}")
    return found[0]


# ---- facts -----------------------------------------------------------------

def uncomment(line):
    """A cyml/yaml line with its `#` comment cut, outside quotes."""
    q = None
    for i, c in enumerate(line):
        if q:
            q = None if c == q else q
        elif c in "\"'":
            q = c
        elif c == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def cyml_sections(text):
    """{section: body} of cyrius.cyml, comments cut."""
    text = "\n".join(uncomment(l) for l in text.splitlines())
    parts = re.split(r'^\[([A-Za-z0-9_.]+)\][ \t]*$', text, flags=re.M)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def cyml_list(body, key, what):
    inner = one(r'^' + key + r'\s*=\s*\[(.*?)\]', body, what, re.M | re.S)
    items = re.findall(r'"([^"]+)"', inner)
    if not items:
        raise Error(f"{what} is empty")
    return items


def parse_workflow(text, name="workflow"):
    """(runners, {action: {version}}) of one workflow's text. A runs-on
    this cannot read as one plain runner label is an error, not a skip."""
    runners, actions = set(), {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = uncomment(raw).rstrip()
        m = re.match(r'^\s*runs-on:(.*)$', line)
        if m:
            v = m.group(1).strip()
            if not re.fullmatch(r'[A-Za-z0-9][\w.-]*', v):
                raise Error(f"{name}:{n}: runs-on {v or '(a block)'} is not one plain runner label — "
                            "teach tools/state_sync.py this form")
            runners.add(v)
        m = re.match(r'^\s*(?:-\s+)?uses:\s*(\S+)', line)
        if m:
            value = m.group(1).strip("\"'")
            if value.startswith("./"):
                continue
            ver = re.search(r'#\s*(\S+)', raw[len(line):])
            act, _, ref = value.partition("@")
            if re.fullmatch(r'[0-9a-f]{40}', ref):
                actions.setdefault(act, set()).add(ver.group(1) if ver else f"@{ref[:12]}")
            else:
                actions.setdefault(act, set()).add(f"@{ref or '(none)'} (not SHA-pinned)")
    return runners, actions


def parse_ffi(code):
    """(slots, kind offset, table size) of src/samvada_ffi.cyr's comment-free code."""
    slots = dict(re.findall(r'\bfn\s+samvada_slot_(\w+)' + NULLARY, code))
    declared = re.findall(r'\bfn\s+samvada_slot_\w+', code)
    if len(declared) != len(slots):
        raise Error(f"src/samvada_ffi.cyr: {len(declared)} samvada_slot_* fns but {len(slots)} read as "
                    "`{ return N; }` — teach tools/state_sync.py this form")
    if "kind" not in slots:
        raise Error("src/samvada_ffi.cyr: no samvada_slot_kind()")
    size = int(one(r'\bfn\s+samvada_ffi_size' + NULLARY, code, "src/samvada_ffi.cyr samvada_ffi_size()"))
    if len(slots) * 8 != size:
        raise Error(f"src/samvada_ffi.cyr: {len(slots)} slots but samvada_ffi_size() is {size}")
    return len(slots), int(slots["kind"]), size


def parse_tally(out, rc, rel):
    tallies = [tuple(map(int, t)) for t in TALLY.findall(out)]
    if not tallies:
        raise Error(f"{rel}: printed no tally line (exit {rc}) — fix the suite first")
    passed, failed, total = tallies[-1]
    if rc != 0 or any(t[1] for t in tallies) or passed != total or not total:
        raise Error(f"{rel}: {passed} passed, {failed} failed of {total} (exit {rc}) — fix the suite first")
    return total


def run_suite(root, rel):
    try:
        p = subprocess.run(["cyrius", "test", rel], cwd=root, capture_output=True,
                           text=True, errors="replace", timeout=900)
    except FileNotFoundError:
        raise Error("cyrius is not on PATH")
    except subprocess.TimeoutExpired:
        raise Error(f"{rel}: timed out")
    return parse_tally(p.stdout + p.stderr, p.returncode, rel)


def gather(root, runner=run_suite):
    f = {"version": read(root, "VERSION").strip()}
    if not re.fullmatch(r'\d+\.\d+\.\d+', f["version"]):
        raise Error(f"VERSION is not x.y.z: {f['version']!r}")
    f["date"] = one(r'^## \[' + re.escape(f["version"]) + r'\] — (\d{4}-\d{2}-\d{2})[ \t]*$',
                    read(root, "CHANGELOG.md"), f"CHANGELOG.md heading '## [{f['version']}] — YYYY-MM-DD'")

    sec = cyml_sections(read(root, "cyrius.cyml"))
    for s in ("package", "lib", "deps"):
        if s not in sec:
            raise Error(f"cyrius.cyml has no [{s}]")
    f["pin"] = one(r'^cyrius\s*=\s*"([^"]+)"', sec["package"], "cyrius.cyml [package] cyrius")
    f["modules"] = cyml_list(sec["lib"], "modules", "cyrius.cyml [lib] modules")
    f["stdlib"] = cyml_list(sec["deps"], "stdlib", "cyrius.cyml [deps] stdlib")

    f["runners"], f["actions"], f["workflows"] = set(), {}, []
    paths = glob.glob(os.path.join(root, ".github", "workflows", "*.yml"))
    paths += glob.glob(os.path.join(root, ".github", "workflows", "*.yaml"))
    for path in sorted(paths):
        rel = os.path.relpath(path, root)
        runners, actions = parse_workflow(read(root, rel), rel)
        f["workflows"].append(os.path.basename(path))
        f["runners"] |= runners
        for name, vs in actions.items():
            f["actions"].setdefault(name, set()).update(vs)
    if not f["runners"]:
        raise Error("no runs-on in .github/workflows/")

    try:
        decls = cyr_scan.fn_decls(cyr_scan.strip(read(root, "dist/samvada.cyr")))
    except ValueError as e:
        raise Error(f"dist/samvada.cyr: {e}")
    f["public"] = sum(1 for p, _, _ in decls if p)
    f["internal"] = len(decls) - f["public"]

    f["slots"], f["kind_at"], f["ffi_size"] = parse_ffi(cyr_scan.strip(read(root, "src/samvada_ffi.cyr")))

    f["leaves"] = [l.strip() for l in read(root, "dist/samvada.deps").splitlines()
                   if l.strip() and not l.lstrip().startswith("#")]
    f["errno_rows"] = sum(1 for l in read(root, "tests/fixtures/sdbus_errno_map.tsv").splitlines()
                          if l.strip() and not l.lstrip().startswith("#"))

    rows = re.findall(r'^\|\s*`([^`]+\.bin)`\s*\|\s*\d+\s*\|\s*\*\*(REAL|SYNTHETIC)\*\*\s*\|',
                      read(root, "tests/fixtures/dbus/MANIFEST.md"), re.M)
    disk = sorted(os.path.basename(p) for p in glob.glob(os.path.join(root, "tests", "fixtures", "dbus", "*.bin")))
    if sorted(n for n, _ in rows) != disk:
        raise Error("tests/fixtures/dbus/MANIFEST.md's table and the *.bin files there disagree")
    f["fixtures"], f["real"] = len(rows), sum(1 for _, k in rows if k == "REAL")

    f["benches"] = len(re.findall(r'\bbench_new\s*\(', cyr_scan.strip(read(root, "tests/samvada.bcyr"))))

    rels = sorted(os.path.relpath(p, root)
                  for p in glob.glob(os.path.join(root, "tests", "**", "*.tcyr"), recursive=True))
    if not rels:
        raise Error("no tests/**/*.tcyr suites")
    f["suites"] = [(r, runner(root, r)) for r in rels]
    return f


# ---- rendering ---------------------------------------------------------------

def render(name, f, old):
    if name == "version":
        return f"**{f['version']}** — released {f['date']}, on cyrius `{f['pin']}`.\n"
    if name == "glance":
        runners = ", ".join(f"`{r}`" for r in sorted(f["runners"]))
        if len(f["runners"]) == 1:
            runners += f" — every job in {', '.join(f'`{w}`' for w in f['workflows'])}"
        loose = sum(1 for vs in f["actions"].values() for v in vs if "not SHA-pinned" in v)
        actions = " · ".join(f"`{a}` {', '.join(sorted(vs))}" for a, vs in sorted(f["actions"].items()))
        if actions:
            actions += f" ({loose} not SHA-pinned)" if loose else " (by commit SHA)"
        total = sum(n for _, n in f["suites"])
        rows = [
            ("Version", f"{f['version']}, released {f['date']} (`VERSION`, `CHANGELOG.md`)"),
            ("Toolchain pin", f"cyrius `{f['pin']}` (`cyrius.cyml [package].cyrius`)"),
            ("CI runners", runners),
            ("Pinned actions", actions or "none"),
            ("Public surface", f"{f['public']} `public fn`s in `dist/samvada.cyr` (`tools/public_surface.txt`)"),
            ("Internal fns", f"{f['internal']}, every one private to the bundle (`tools/boundary_check.py`)"),
            ("FFI table", f"{f['slots']} slots, {f['ffi_size']} bytes; `kind` at `+{f['kind_at']}` "
                          "(`src/samvada_ffi.cyr`)"),
            ("`[lib] modules`", f"{len(f['modules'])}, `{f['modules'][0]}` first"),
            ("stdlib leaves", f"{len(f['stdlib'])} declared (`cyrius.cyml [deps]`): {', '.join(f['stdlib'])}; "
                              f"the bundle needs {len(f['leaves'])} (`dist/samvada.deps`): {', '.join(f['leaves'])}"),
            ("Tests", f"{total} asserts in {len(f['suites'])} suites, none failing (§ Tests)"),
            ("sd-bus errno map", f"{f['errno_rows']} rows (`tests/fixtures/sdbus_errno_map.tsv`)"),
            ("Wire fixtures", f"{f['fixtures']} captures, {f['real']} REAL and {f['fixtures'] - f['real']} "
                              "SYNTHETIC (`tests/fixtures/dbus/MANIFEST.md`)"),
            ("CPU bench baselines", f"{f['benches']} (`tests/samvada.bcyr`)"),
        ]
        return "| | |\n|---|---|\n" + "".join(f"| **{k}** | {v} |\n" for k, v in rows)
    if name == "tests":
        covers, order = {}, []
        for line in old.splitlines():
            m = SUITE_ROW.match(line)
            if m and m.group(1) != "File":
                if m.group(1) in covers:
                    raise Error(f"{STATE}: {m.group(1)} is listed twice in the tests table")
                covers[m.group(1)] = m.group(2)
                order.append(m.group(1))
        counts = dict(f["suites"])
        files = [r for r in order if r in counts] + [r for r, _ in f["suites"] if r not in order]
        total = sum(counts.values())
        out = f"**{total} asserts**, in {len(files)} suites, none failing.\n\n"
        out += "| File | Asserts | Covers |\n|---|---|---|\n"
        for r in files:
            out += f"| `{r}` | {counts[r]} | {covers.get(r) or UNDESCRIBED} |\n"
        return out
    raise Error(f"no renderer for block {name}")


def blocks(text):
    """{name: (body_start, body_end)} for every block, or Error if malformed."""
    found, open_ = {}, None
    for m in MARKER.finditer(text):
        kind, name = m.groups()
        if name not in BLOCKS:
            raise Error(f"{STATE}: unknown block `{name}` (known: {', '.join(BLOCKS)})")
        if kind == "begin":
            if open_:
                raise Error(f"{STATE}: block `{open_[0]}` is not closed before `{name}` begins")
            if name in found:
                raise Error(f"{STATE}: block `{name}` appears twice")
            open_ = (name, m.end() + 1)
        else:
            if not open_ or open_[0] != name:
                raise Error(f"{STATE}: `end {name}` without its begin")
            found[name] = (open_[1], m.start())
            open_ = None
    if open_:
        raise Error(f"{STATE}: block `{open_[0]}` is never closed")
    missing = [b for b in BLOCKS if b not in found]
    if missing:
        raise Error(f"{STATE}: missing block(s) {', '.join(missing)}")
    return found


def sync(text, f):
    """(new_text, problems): every block regenerated; problems are what keeps
    the snapshot stale even after a regeneration."""
    if "\r" in text:
        raise Error(f"{STATE} has CR line endings; it is LF-only")
    marks = NARRATIVE.findall(text)
    if len(marks) != 1:
        raise Error(f"{STATE}: expected one `<!-- state-sync:narrative X.Y.Z -->`, found {len(marks)}")
    new = text
    for name, (a, b) in sorted(blocks(text).items(), key=lambda kv: -kv[1][0]):
        new = new[:a] + render(name, f, text[a:b]) + new[b:]
    problems = []
    if marks[0] != f["version"]:
        problems.append(f"the hand-written sections are acknowledged for {marks[0]}, VERSION is "
                        f"{f['version']}: rewrite them, then set `<!-- state-sync:narrative {f['version']} -->`")
    a, b = blocks(new)["tests"]
    for line in new[a:b].splitlines():
        m = SUITE_ROW.match(line)
        if m and m.group(2) == UNDESCRIBED:
            problems.append(f"say what {m.group(1)} covers in the tests table")
    return new, problems


def run(argv, root=ROOT, runner=run_suite):
    if argv not in ([], ["--check"]):
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    check = argv == ["--check"]
    try:
        text = read(root, STATE)
        new, problems = sync(text, gather(root, runner))
        if new != text and not check:
            with open(os.path.join(root, STATE), "w", encoding="utf-8") as fh:
                fh.write(new)
    except Error as e:
        print(f"state_sync: error: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # an unexpected failure is an error, never "stale"
        print(f"state_sync: error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if new != text:
        if check:
            sys.stdout.writelines(difflib.unified_diff(text.splitlines(True), new.splitlines(True),
                                                       f"a/{STATE}", f"b/{STATE}"))
            print(f"STALE: {STATE}'s generated blocks disagree with the tree — "
                  "run `python3 tools/state_sync.py`")
        else:
            print(f"rewrote the generated blocks of {STATE}")
    for p in problems:
        print(f"STALE: {p}")
    if new == text and not problems:
        print(f"{STATE} is current")
    return 1 if (problems or (check and new != text)) else 0


# ---- self-test ---------------------------------------------------------------

def self_test():
    bad = 0

    def check(cond, what):
        nonlocal bad
        print(("  ok  " if cond else "  FAIL ") + what)
        if not cond:
            bad = 1

    def refused(fn, what):
        try:
            fn()
            check(False, what)
        except Error:
            check(True, what)

    real = gather(ROOT, lambda root, rel: 10)
    check(real["public"] > 0 and real["internal"] > 0 and real["slots"] > 0 and real["suites"],
          "every fact parses on this tree (suite runs faked)")

    f = dict(real, suites=[("tests/a.tcyr", 3), ("tests/b.tcyr", 4)])
    doc = ("# s\n<!-- state-sync:narrative " + f["version"] + " -->\n\nprose one\n"
           "<!-- state-sync:begin version -->\nold\n<!-- state-sync:end version -->\n\nprose two\n"
           "<!-- state-sync:begin glance -->\n<!-- state-sync:end glance -->\n"
           "<!-- state-sync:begin tests -->\n| File | Asserts | Covers |\n|---|---|---|\n"
           "| `tests/b.tcyr` | 1 | bee things |\n| `tests/a.tcyr` | 2 | ay things |\n"
           "| `tests/gone.tcyr` | 9 | removed |\n<!-- state-sync:end tests -->\ntail\n")
    new, problems = sync(doc, f)
    again, _ = sync(new, f)
    check(new != doc and again == new, "a stale document is rewritten, and a rewrite is a fixed point")
    check(not problems, "no problems once regenerated and described")
    check(all(p in new for p in ("prose one\n", "prose two\n", "tail\n")), "prose outside the blocks is untouched")
    check("**7 asserts**" in new and "| `tests/a.tcyr` | 3 | ay things |" in new,
          "counts are regenerated and each suite keeps its description")
    check(new.index("tests/b.tcyr") < new.index("tests/a.tcyr"), "the table keeps its hand-chosen order")
    check("gone.tcyr" not in new, "a suite that no longer exists loses its row")
    check(sync(new.replace("| 3 |", "| 30 |"), f)[0] == new, "a hand-edited count is detected and reverted")
    refused(lambda: sync(new.replace("| `tests/a.tcyr` | 3 | ay things |\n",
                                     "| `tests/a.tcyr` | 3 | ay things |\n| `tests/a.tcyr` | 3 | again |\n"), f),
            "a suite listed twice is an error")
    check(any("tests/a.tcyr" in p for p in sync(new.replace(" ay things ", " "), f)[1]),
          "an emptied Covers cell is a problem")
    check("every job" in render("glance", f, "") and "every job" not in
          render("glance", dict(f, runners={"ubuntu-24.04", "ubuntu-26.04"}), ""),
          "'every job' is claimed only for a single runner")
    loose = render("glance", dict(f, actions={"a/b": {"v1"}, "c/d": {"@v2 (not SHA-pinned)"}}), "")
    check("(1 not SHA-pinned)" in loose and "(by commit SHA)" not in loose,
          "an unpinned action is counted, not covered by '(by commit SHA)'")

    grown = dict(f, suites=f["suites"] + [("tests/c.tcyr", 5)])
    new2, problems = sync(new, grown)
    check(UNDESCRIBED in new2 and any("tests/c.tcyr" in p for p in problems),
          "a new suite is added undescribed, and stays a problem until described")

    stale = new.replace("narrative " + f["version"], "narrative 0.0.1")
    check(any("0.0.1" in p for p in sync(stale, f)[1]), "a narrative acknowledged for another version is a problem")
    refused(lambda: sync(new.replace("<!-- state-sync:narrative " + f["version"] + " -->\n", ""), f),
            "a missing narrative marker is an error")
    refused(lambda: sync(new + "<!-- state-sync:narrative 1.0.0 -->\n", f), "two narrative markers are an error")
    refused(lambda: sync(new.replace("<!-- state-sync:end glance -->\n", ""), f), "an unclosed block is an error")
    refused(lambda: sync(new.replace("begin tests", "begin testz").replace("end tests", "end testz"), f),
            "an unknown block is an error")
    refused(lambda: sync(new + "<!-- state-sync:begin glance -->\n<!-- state-sync:end glance -->\n", f),
            "a duplicated block is an error")
    refused(lambda: sync(new.split("<!-- state-sync:begin tests -->")[0] + "\n", f), "a missing block is an error")
    refused(lambda: sync(new.replace("\n", "\r\n"), f), "a CRLF state.md is an error")

    check(parse_tally("x\n5 passed, 0 failed (5 total)\n", 0, "t") == 5, "a clean tally is counted")
    check(parse_tally("2 passed, 0 failed (2 total)\n5 passed, 0 failed (5 total)\n", 0, "t") == 5,
          "a suite's last tally is its count")
    refused(lambda: parse_tally("4 passed, 1 failed (5 total)\n", 1, "t"), "a failing suite is an error, not a count")
    refused(lambda: parse_tally("1 passed, 1 failed (2 total)\n5 passed, 0 failed (5 total)\n", 0, "t"),
            "a failure in an earlier tally is an error")
    refused(lambda: parse_tally("5 passed, 0 failed (5 total)\n", 1, "t"), "a non-zero exit is an error")
    refused(lambda: parse_tally("compile error\n", 1, "t"), "a suite that printed no tally is an error")

    runners, actions = parse_workflow(
        "jobs:\n  a:\n    runs-on: ubuntu-26.04  # note\n    steps:\n"
        "      - uses: actions/checkout@" + "a" * 40 + "  # v7.0.1\n"
        "      - uses: \"q/quoted@" + "b" * 40 + "\"  # v3\n      - uses: bare/sha@" + "c" * 40 + "\n"
        "      # - uses: evil/commented@v1\n      - uses: loose/action@v2\n      - uses: short/sha@abc1234\n"
        "  b:\n    uses: ./.github/workflows/ci.yml\n")
    check(runners == {"ubuntu-26.04"}, "runs-on is read without its comment")
    check(actions == {"actions/checkout": {"v7.0.1"}, "q/quoted": {"v3"}, "bare/sha": {"@cccccccccccc"},
                      "loose/action": {"@v2 (not SHA-pinned)"}, "short/sha": {"@abc1234 (not SHA-pinned)"}},
          "a 40-hex ref is pinned (quoted, or with no version comment); a tag or a short SHA is not")
    for form in ("runs-on:\n      - ubuntu-24.04\n", "runs-on: [ubuntu-24.04]\n", "runs-on: ${{ matrix.os }}\n"):
        refused(lambda: parse_workflow("jobs:\n  a:\n    " + form),
                f"a runs-on of `{form.splitlines()[0]}` is an error, not a skip")
    ffi = "fn samvada_ffi_size() { return 16; }\nfn samvada_slot_a() { return 0; }\n"
    check(parse_ffi(ffi + "fn samvada_slot_kind(): i64 { return 8; }\n") == (2, 8, 16),
          "a slot with a return type is counted")
    refused(lambda: parse_ffi(ffi + "fn samvada_slot_kind() { return 8; }\nfn samvada_slot_b() { return 8 + 8; }\n"),
            "a slot the tool cannot read is an error, not a skip")
    refused(lambda: parse_ffi(ffi + "fn samvada_slot_kind() { return 8; }\nfn samvada_slot_b() { return 16; }\n"),
            "a slot count that disagrees with samvada_ffi_size() is an error")
    check(cyml_list(cyml_sections('[lib]\nmodules = [\n    "a.cyr",   # first [really]\n#   "x.cyr",\n'
                                  '    "b.cyr",\n]\n')["lib"], "modules", "t") == ["a.cyr", "b.cyr"],
          "cyml lists ignore inline comments (even with a `]`) and commented-out items")

    # The whole run on a temporary tree: exit codes and the write path.
    with tempfile.TemporaryDirectory() as tmp:
        for rel in ("VERSION", "CHANGELOG.md", "cyrius.cyml", "dist/samvada.cyr", "dist/samvada.deps",
                    "src/samvada_ffi.cyr", "tests/samvada.bcyr", "tests/fixtures/sdbus_errno_map.tsv"):
            os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
            shutil.copy(os.path.join(ROOT, rel), os.path.join(tmp, rel))
        shutil.copytree(os.path.join(ROOT, ".github", "workflows"), os.path.join(tmp, ".github", "workflows"))
        shutil.copytree(os.path.join(ROOT, "tests", "fixtures", "dbus"),
                        os.path.join(tmp, "tests", "fixtures", "dbus"))
        os.makedirs(os.path.join(tmp, "tests", "sub"))
        os.makedirs(os.path.join(tmp, "docs", "development"))
        for rel in ("tests/a.tcyr", "tests/sub/b.tcyr"):
            open(os.path.join(tmp, rel), "w").close()
        counts = {"tests/a.tcyr": 3, "tests/sub/b.tcyr": 4}
        state = os.path.join(tmp, STATE)
        with open(state, "w", encoding="utf-8") as fh:
            fh.write(doc.replace("tests/b.tcyr", "tests/sub/b.tcyr"))

        def go(*argv, runner=lambda root, rel: counts[rel]):
            with contextlib.redirect_stdout(io.StringIO()) as o, contextlib.redirect_stderr(io.StringIO()) as e:
                rc = run(list(argv), tmp, runner)
            return rc, o.getvalue() + e.getvalue()

        def boom(root, rel):
            raise KeyError(rel)

        before = open(state, encoding="utf-8").read()
        rc, out = go("--check")
        check(rc == 1 and open(state, encoding="utf-8").read() == before and "STALE" in out,
              "--check on a stale tree is 1 and writes nothing")
        rc, out = go()
        after = open(state, encoding="utf-8").read()
        check(rc == 0 and after != before and "| `tests/sub/b.tcyr` | 4 |" in after,
              "a rewrite returns 0, writes the file, and finds a nested suite")
        check(go("--check")[0] == 0, "--check after a rewrite is 0")
        extra = os.path.join(tmp, ".github", "workflows", "zz.yaml")
        with open(extra, "w") as fh:
            fh.write("jobs:\n  z:\n    runs-on: ubuntu-24.04\n")
        rc, out = go()
        check(rc == 0 and "`ubuntu-24.04`, `ubuntu-26.04`" in open(state, encoding="utf-8").read(),
              "a .yaml workflow is read too")
        os.remove(extra)
        go()
        check(go("--check", runner=boom)[0] == 2, "an unexpected exception is an error (2), not stale (1)")
        extra = os.path.join(tmp, "tests", "fixtures", "dbus", "zz-extra.bin")
        open(extra, "w").close()
        check(go("--check")[0] == 2, "a fixture with no MANIFEST row is an error")
        os.remove(extra)
        with open(state, "ab") as fh:
            fh.write(b"\xff\n")
        check(go("--check")[0] == 2, "a state.md that is not UTF-8 is an error")
    return bad


def main():
    if sys.argv[1:] == ["--self-test"]:
        return self_test()
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
