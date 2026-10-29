#!/usr/bin/env python3
"""samvada's dangerous-pattern scan: no process spawning, no writes to
system paths, no huge static buffers.

1.1.2 (P(-1) audit). This replaces ci.yml's inline grep scan, which could
be walked past. Demonstrated bypasses, each now a positive control in
--self-test:

  * `SYS_execve` / `__NR_execve` — the grep matched only `SYS_EXECVE`,
    and C's <sys/syscall.h> spells it lowercase;
  * `execvpe(`, `posix_spawnp(`, `clone(` — not in the C list;
  * `syscall(0x3b, ...)` — numbers were matched in decimal only;
  * `&sys_execve` — a fn pointer, never followed by `(`;
  * any code on a line starting with `#` or `*` — dropped as a comment,
    so a C `#define RUN execv(` was never scanned;
  * `var buf[0x10000]` — buffer sizes were matched in decimal only.

1.1.2 (P(-1) re-verification). The first cut still RECOGNISED spellings,
and the reviewer walked past it again; every bypass below is a control:

  * Cyrius attribute lines (`#pure fn f() { sys_execve(..) }`) and
    function-like `#define` bodies were comments to the scan — cyr_scan
    now reads them as cycc does (see its docstring).
  * `asm { .. }` emits the syscall instruction itself: refused, in both
    languages (`asm` / `__asm__` in C).
  * `object;` builds turn an undefined Cyrius fn into an external symbol
    (`execve(0, 0, 0)` links libc's execve), so the C spawn words are
    findings in Cyrius too; and the AGNOS stdlib's spawners (`sys_spawn*`,
    `sys_execwait`, `SYS_SPAWN_PATH`, ..) match the spawn family by name.
  * C: splices and digit separators desynchronised the lexer (fixed in
    cyr_scan); a function pointer `= execve;` has no `(`, so the spawn
    family is matched as BARE WORDS in C, with daemon, forkpty, wordexp,
    _Fork, pidfd_spawn(p), the dl* resolvers and `syscall` added; `##` /
    `#x` (pasting and stringizing assemble a name or a path no lexer sees).
    tools/shim_symbols.txt + ci.yml's `nm -u` gate is the backstop: it
    judges the compiled object, whatever the spelling.
  * system paths: escapes are decoded (\\xNN, \\u{..}, C octal), adjacent
    C literals and literal-only macros joined, `//`, `/./` and `..`
    resolved, and every suffix starting at a `/` judged (a literal plus
    an offset is a pointer into it) — so `"\\x2fetc/x"`, `"//etc/x"`,
    `"/e" "tc/x"` and `"XX/etc/x" + 2` are all `/etc/x`.
  * scope: a file the scanner cannot lex (`src/helpers.inc`), a symlink
    (os.walk did not follow one; cycc does), and a Cyrius `include` that
    is absolute, contains `..`, leaves src/ lib/ tests/, is not a Cyrius
    file, names a stdlib module outside [deps].stdlib (+ bench) or a file
    that is not there — each a finding, as is `#ref` (it injects a data
    file's lines as declarations) and `#pe_import`. tests/fixtures/ may
    hold data files; any Cyrius or C file there is scanned all the same.
  * odd input never crashes the scan (`059` did): an unparsable literal
    or a scanner exception is a FINDING.

1.1.2 (final pass). Six more ways past it, each demonstrated end to end
(the planted code compiled and ran) and each now a control:

  * cyr_scan read `#io("fd") .. "` / `#note: the "` as an attribute and
    opened a string the next lines hid in, and an object-like `#define N "`
    body did the same; it now copies cycc's `#` rule exactly (cyr_scan's
    docstring, which also closes a lone `'` in a false #ifdef and an
    include line's `#assert .. "` tail), and every input cycc itself reads
    two ways is a finding (cyr_scan.hazards()). One of those is a string
    left open at the end of a src/ module: cycc reads the bundle's modules
    as ONE text, so the string ran into the next module and a sys_execve
    there compiled into the bundle with each module scanning clean. The
    bundle (dist/samvada.cyr) is now scanned whole as well.
  * a function-like macro's arguments are collected by cycc with no string
    awareness, so `M(") ;` .. `M(");` hid the lines between from this scan
    in tests and src/main.cyr. samvada has no `#define`: every one in a
    Cyrius file is now a finding, wherever the scan sees it (code position,
    or any line that starts with it).
  * the stdlib is not scanned and [deps].stdlib was not frozen: adding
    "process" gave run() / spawn() with a clean scan. cyrius.cyml is now
    judged too (manifest_problems()): [deps].stdlib must equal
    tools/stdlib_allowed.txt, the modules that fork, exec, load code or map
    executable memory are refused by name (DENIED_STDLIB), and nothing else
    may pull code in — no [deps.NAME] / [groups] / [test] table, and
    [build] / [lib] may name only files this scan reads.
  * the C shim could `#include "../tests/fixtures/x.txt"` — an unscanned
    data file holding inline asm, which adds no undefined symbol. A C file
    may include only `<plain/header.h>` system headers (no path, no `..`,
    no macro, no #embed); ci.yml's objdump gate is the compiled backstop.
  * syscall numbers were judged against the SPAWN numbers only, so
    `syscall(SYS_MMAP, 0, 4096, 7, ..)` plus fncall0 ran machine code that
    exec'd. The list is now POSITIVE: the first argument of a `syscall(`
    must be one of SYSCALL_NAMES / SYSCALL_NUMBERS — what samvada uses —
    and the executable-memory and code-injection primitives (mmap,
    mprotect, pkey_mprotect, mremap, PROT_EXEC, process_vm_writev, ptrace)
    are findings by name, however they would be reached.
  * false positives: a parameter named `system` or `daemon` — the libc
    spawn words are findings in Cyrius only where they can link
    (`name(` or `&name`; still bare words in C); `<sys/syscall.h>` — a
    header name is not code; and an unknown `#word` is a comment again.

Usage:  security_scan.py <repo-root>      scan src/ tests/ deps/, dist/samvada.cyr and cyrius.cyml
        security_scan.py --self-test      the positive and negative controls
Exit:   0 clean, 1 findings (or a control failed), 2 usage error.
"""
import os
import posixpath
import re
import stat
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cyr_scan  # noqa: E402

SCOPE = ("src", "tests", "deps")
BUNDLE = "dist/samvada.cyr"                  # also scanned whole: what a consumer compiles
DATA_DIR = "tests/fixtures"                  # data files allowed; code there is still scanned
CYR_EXT = (".cyr", ".tcyr", ".bcyr", ".fcyr")
C_EXT = (".c", ".h")
EXTRA_STDLIB = {"bench"}                     # the bench harness: not a [deps] module, includes only deps ones
STDLIB_ALLOWED = "tools/stdlib_allowed.txt"

W, E = r'(?<![A-Za-z0-9_])', r'(?![A-Za-z0-9_])'
IDENT = cyr_scan.IDENT

# Process creation and run-time symbol resolution. In C as bare words: a
# call, a fn pointer and a macro all name the symbol. In Cyrius only where
# an `object;` build can link the libc one: a call or an address.
SPAWN_WORDS = ("system popen execv execvp execvpe execve execveat fexecve execl execlp execle "
               "fork vfork clone clone3 posix_spawn posix_spawnp pidfd_spawn pidfd_spawnp daemon "
               "forkpty wordexp _Fork dlsym dlvsym dlopen dlmopen syscall").split()
_PFX = r'(?:__libc_|_+)?'
SPAWN_C = re.compile(W + '(' + _PFX + '(?:' + '|'.join(SPAWN_WORDS) + '))' + E)
_EXTERN = '(' + _PFX + '(?:' + '|'.join(w for w in SPAWN_WORDS if w != "syscall") + '))'  # Cyrius `syscall` is the builtin
SPAWN_CYR_EXTERN = re.compile(r'(?:&\s*' + W + _EXTERN + E + '|' + W + _EXTERN + E
                              + r'(?=\s*(?:<[^<>(){};]*>)?\s*\())')
SPAWN_CYR = re.compile(W + r'(sys_[A-Za-z0-9_]*?(?:exec|fork|clone|spawn|system)[A-Za-z0-9_]*)' + E)
SPAWN_CONST = re.compile(W + r'((?:SYS|__NR)_[A-Za-z0-9_]*?(?i:exec|fork|clone|spawn)[A-Za-z0-9_]*)' + E)
# Executable memory and writing another process's: samvada never maps memory.
MEMEXEC = re.compile(W + r'((?:sys_|SYS_|__NR_)?(?i:mmap|mmap2|mmap64|mprotect|pkey_mprotect|mremap|'
                     r'process_vm_writev|ptrace)|PROT_EXEC|cyr_mmap[A-Za-z0-9_]*)' + E)
# The syscall( first arguments samvada uses — a POSITIVE list (1.1.2 final
# pass): anything else is a finding. A new one needs a row here, in review.
SYSCALL_NAMES = {"SYS_EXIT": "exit (src/main.cyr, src/test.cyr, the live bench)",
                 "SYS_FCNTL": "fcntl F_GETFD (tests/dbus_sys.tcyr: close-on-exec checks)"}
SYSCALL_NUMBERS = {60: "exit (the tests' and benches' last line)",
                   48: "shutdown (tests/dbus_session.tcyr: a peer hangs up)",
                   228: "clock_gettime (src/dbus_session.cyr: the monotonic deadline; "
                        "the stdlib has no SYS_CLOCK_GETTIME)"}
SYSCALL = re.compile(W + r'syscall' + E)
SYS_NAME = re.compile(r'SYS_[A-Z0-9_]+')
SYS_DECL = re.compile(W + r'(?:var|const|fn|enum|struct|union|trait|impl)\s+(SYS_[A-Za-z0-9_]*)')
SYS_ASSIGN = re.compile(W + r'(SYS_[A-Za-z0-9_]*)\s*=(?!=)')
SYS_ANY = re.compile(W + r'SYS_[A-Za-z0-9_]*')
ENUM_OPEN = re.compile(W + r'enum' + E + r'[^{;]*\{')
FN_PARAMS = re.compile(W + r'fn\s*(?:' + IDENT + r')?\s*(?:<[^<>(){};]*>)?\s*\(')
ASM_CYR = re.compile(W + r'asm' + E)
ASM_C = re.compile(W + r'(?:asm|__asm|__asm__)' + E)
INCLUDE = re.compile(W + r'include\s*"')
BUFFER = re.compile(W + r'var\s+(' + IDENT + r')\s*\[([^\]\n]*)\]')
BUFFER_MAX = 65536
SYS_DIRS = ("etc", "bin", "sbin")
CYR_DIRECTIVES_REFUSED = {"ref": "#ref injects a data file's lines as declarations",
                          "pe_import": "#pe_import registers a foreign import",
                          "define": "#define (samvada has none; a function-like macro's arguments "
                                    "are read with no string awareness, and can hide code)"}
# Any line that starts with one of these is a finding, even where cyr_scan
# reads a string: the backstop for a desync it has not met yet.
CYR_LINE_REFUSED = re.compile(r'^[ \t]*#(define|ref)(?![A-Za-z0-9_])', re.M)
# C: the only include form a shim file may use (no path, no `..`, no macro).
C_INCLUDE_LINE = re.compile(r'^[ \t]*(?:#|%:)[ \t]*(include|include_next|import|embed)(?![A-Za-z0-9_])(.*)$', re.M)
C_SYSTEM_HEADER = re.compile(r'[ \t]*<([A-Za-z0-9_][A-Za-z0-9_+.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_+.-]*)*\.h)>[ \t]*')

# Stdlib modules that fork, exec, load code or map executable memory — or
# include one that does. Derived from cycc 6.7.18's ~/.cyrius/lib by
# scanning each module (cyr_scan-stripped) for sys_execve / sys_fork /
# sys_spawn* / clone(2) / PROT_EXEC / fdlopen / dlopen and closing over its
# `include "lib/..."` lines; plus the network clients (net and its
# includers) and cffi, which samvada has no use for.
DENIED_STDLIB = frozenset("""
    async async_agnos async_macos audit_walk callback dynlib fdlopen freelist mabda mmap pam patra
    process process_agnos process_win regression regression_agnos sandhi sankoch sigil
    thread thread_agnos thread_local thread_macos thread_win tls tls_native vani ws_server yantra yukti
    cffi net http ws
""".split())
MANIFEST_SECTIONS = {"package", "build", "lib", "deps"}
BUILD_KEYS = {"entry", "test", "output"}


def cyr_int(tok):
    """An integer literal by cycc's rules, or None: 0x / 0o / 0b (lower
    case), `_` anywhere after the first digit, a leading 0 is DECIMAL."""
    for pfx, base, digits in (("0x", 16, "0123456789abcdefABCDEF"), ("0o", 8, "01234567"), ("0b", 2, "01")):
        if tok.startswith(pfx):
            body = tok[2:]
            if not body.replace("_", "") or any(c not in digits + "_" for c in body):
                return None
            return int(body.replace("_", ""), base)
    if re.fullmatch(r'[0-9][0-9_]*', tok):
        return int(tok.replace("_", ""), 10)
    return None


def spawn_number(v):
    n = (v & 0xFFFFFFFF) & ~X32_BIT          # what the kernel dispatches on
    return n in SPAWN_NUMBERS


def _close(code, k, o="(", c=")"):
    depth = 0
    for j in range(k, len(code)):
        if code[j] == o:
            depth += 1
        elif code[j] == c:
            depth -= 1
            if depth == 0:
                return j
    return -1


def _first_arg(code, k):
    """The text of the first argument of the call whose `(` is code[k]."""
    depth = 0
    for j in range(k, len(code)):
        ch = code[j]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return code[k + 1:j]
        elif ch == "," and depth == 1:
            return code[k + 1:j]
    return None


SIMPLE_CYR = {"n": "\n", "r": "\r", "t": "\t", "0": "\0", "\\": "\\", '"': '"', "'": "'",
              "a": "\a", "b": "\b", "f": "\f", "v": "\v", "\n": "\n"}
SIMPLE_C = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"', "'": "'", "?": "?",
            "a": "\a", "b": "\b", "f": "\f", "v": "\v", "e": "\x1b", "E": "\x1b"}
HEX = "0123456789abcdefABCDEF"


def cyr_unescape(body):
    """A Cyrius string body as cycc stores it; ValueError where cycc refuses."""
    out, i, n = [], 0, len(body)
    while i < n:
        ch = body[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        e = body[i + 1] if i + 1 < n else ""
        if e in SIMPLE_CYR and e:
            out.append(SIMPLE_CYR[e])
            i += 2
        elif e == "x":
            h = body[i + 2:i + 4]
            if len(h) != 2 or any(c not in HEX for c in h):
                raise ValueError("bad \\x escape")
            out.append(chr(int(h, 16)))
            i += 4
        elif e == "u":
            if body[i + 2:i + 3] == "{":
                k = body.find("}", i + 3)
                h = body[i + 3:k] if k > 0 else ""
                i = k + 1
            else:
                h = body[i + 2:i + 6]
                i += 6
            if not 1 <= len(h) <= 6 or any(c not in HEX for c in h) or int(h, 16) > 0x10FFFF:
                raise ValueError("bad \\u escape")
            out.append(chr(int(h, 16)))
        else:
            raise ValueError(f"unknown escape \\{e}")
    return "".join(out)


def c_unescape(body):
    """A C string body's characters (gcc: an unknown escape is the char)."""
    out, i, n = [], 0, len(body)
    while i < n:
        ch = body[i]
        if ch != "\\" or i + 1 >= n:
            out.append(ch)
            i += 1
            continue
        e = body[i + 1]
        if e in "01234567":
            j = i + 1
            while j < n and j < i + 4 and body[j] in "01234567":
                j += 1
            out.append(chr(int(body[i + 1:j], 8)))
            i = j
        elif e == "x":
            j = i + 2
            while j < n and body[j] in HEX:
                j += 1
            out.append(chr(int(body[i + 2:j] or "0", 16) % 0x110000))
            i = j
        elif e in "uU":
            w = 4 if e == "u" else 8
            h = body[i + 2:i + 2 + w]
            out.append(chr(int(h, 16) % 0x110000) if len(h) == w and all(c in HEX for c in h) else e)
            i += 2 + (w if len(h) == w and all(c in HEX for c in h) else 0)
        else:
            out.append(SIMPLE_C.get(e, e))
            i += 2
    return "".join(out)


def system_path(s):
    """The /etc, /bin or /sbin path `s` reaches, or None. Every NUL-separated
    piece, and every suffix of one that starts at a `/` (a literal plus an
    offset points there), is normalised (`//`, `/./`, `..`) and judged.

    EVERY suffix, on purpose (1.1.2 final pass, documented rather than
    narrowed): "/usr/bin/busctl" is flagged, because `"/usr/bin/x" + 4` is
    "/bin/x". That is broader than CLAUDE.md's `/bin/` PREFIX rule, and a
    path samvada legitimately needs under /usr/bin would need a reviewed
    exception here. samvada opens no such path."""
    for seg in s.split("\0"):
        seg = re.sub(r'/+', '/', seg)
        for i, ch in enumerate(seg):
            if ch != "/":
                continue
            p = posixpath.normpath(seg[i:])
            parts = p.split("/")
            if len(parts) > 1 and parts[1] in SYS_DIRS:
                return p
    return None


def _c_strings(src, code):
    """[(line_offset, value)] for every C string run: adjacent literals
    (and literal-only object macros) joined, as translation phase 6 does.
    `src` is normalize_c()'d, `code` its strip(); offsets agree."""
    lits = cyr_scan.literals(src, "c")
    if not lits:
        return []

    def value(s, e, raw):
        t = src[s:e]
        if raw:
            k = t.find("(")
            d = t[1:k]
            return t[k + 1:len(t) - len(d) - 2]
        return c_unescape(t[1:-1] if t.endswith('"') and len(t) > 1 else t[1:])

    items = [(s, e, value(s, e, raw)) for s, e, raw in lits]
    macros = {}
    for _ in range(2):                                   # a macro of macros, one level deep
        for m in re.finditer(r'^[ \t]*#[ \t]*define[ \t]+(' + IDENT + r')[ \t]+(.*)$', code, re.M):
            parts, rest, pos = [], "", m.start(2)
            for s, e, v in items:
                if m.start(2) <= s < m.end(2):
                    rest += code[pos:s]
                    parts.append(v)
                    pos = e
            rest += code[pos:m.end(2)]
            for name in re.findall(IDENT, rest):
                if name in macros and name not in ("L", "u", "U", "u8", "R"):
                    parts.append(macros[name])
                    rest = rest.replace(name, "", 1)
            if parts and re.fullmatch(r'(?:\s|u8|u|U|L|R)*', rest):
                macros[m.group(1)] = "".join(parts)
    for name, v in macros.items():
        for m in re.finditer(W + re.escape(name) + E, code):
            line = code[code.rfind("\n", 0, m.start()) + 1:m.start()]
            if not re.match(r'[ \t]*#[ \t]*define[ \t]+$', line):
                items.append((m.start(), m.end(), v))
    items.sort()
    runs, cur = [], [items[0]]
    for prev, it in zip(items, items[1:]):
        if re.fullmatch(r'\s*(?:u8|u|U|L)?R?', code[prev[1]:it[0]]):
            cur.append(it)
        else:
            runs.append(cur)
            cur = [it]
    runs.append(cur)
    out = [(s, v) for s, _, v in items]
    out += [(r[0][0], "".join(v for _, _, v in r)) for r in runs if len(r) > 1]
    return out


def scan_text(path, src, stdlib, exists=None):
    """[(line, label, text)] for one file's contents. `stdlib` is the set
    of lib/ modules a Cyrius include may name; `exists(relpath)` says
    whether an included src/ or tests/ file is a scanned file."""
    lang = lang_of(path)
    if lang is None:
        return []
    if lang == "c":
        src = cyr_scan.normalize_c(src)
    code = cyr_scan.strip(src, lang=lang)                       # no comments, no strings
    lits = cyr_scan.strip(src, lang=lang, keep_strings=True)   # no comments
    lines = src.splitlines()
    hits = []

    def add(pos, label):
        ln = src.count("\n", 0, pos) + 1
        hits.append((ln, label, lines[ln - 1].strip() if ln <= len(lines) else ""))

    for m in SPAWN_CONST.finditer(code):
        add(m.start(), f"spawn syscall constant {m.group(1)}")
    for m in MEMEXEC.finditer(code):
        add(m.start(), f"executable-memory / code-injection primitive {m.group(1)} (samvada never maps memory)")

    if lang == "c":
        for m in SPAWN_C.finditer(code):
            add(m.start(), f"C spawn or resolver symbol {m.group(1)}")
        for m in ASM_C.finditer(code):
            add(m.start(), "inline asm")
        for m in re.finditer(r'^[ \t]*(?:#|%:)?(.*)$', code, re.M):
            k = re.search(r'#|%:', m.group(1))
            if k is not None:
                add(m.start(1) + k.start(), "preprocessor paste/stringize (`##` / `#x`) assembles names no scan sees")
        for m in C_INCLUDE_LINE.finditer(lits):
            why = c_include_problem(m.group(1), m.group(2))
            if why:
                add(m.start(), f"#{m.group(1)}: {why}")
        for pos, v in _c_strings(src, code):
            p = system_path(v)
            if p is not None:
                add(pos, f"string literal reaches {p}")
        return hits

    # ---- Cyrius ----
    for ln, why in cyr_scan.hazards(src):
        hits.append((ln, f"cycc reads this two ways: {why}", lines[ln - 1].strip() if ln <= len(lines) else ""))
    for m in SPAWN_CYR.finditer(code):
        add(m.start(), f"process spawn helper {m.group(1)}")
    for m in SPAWN_CYR_EXTERN.finditer(code):
        add(m.start(), f"spawn symbol {m.group(1) or m.group(2)} (an `object;` build links libc's)")
    for m in ASM_CYR.finditer(code):
        add(m.start(), "asm block (raw instructions: a syscall no scan judges)")
    for m in SYSCALL.finditer(code):
        k = m.end()
        while k < len(code) and code[k] in " \t\n":
            k += 1
        if k >= len(code) or code[k] != "(":
            add(m.start(), "syscall not called directly (`syscall(NR, ..)` only)")
            continue
        a = (_first_arg(code, k) or "").strip()
        if SYS_NAME.fullmatch(a):
            if a not in SYSCALL_NAMES:
                add(m.start(), f"syscall {a} is not on the allow-list ({', '.join(sorted(SYSCALL_NAMES))})")
        elif re.fullmatch(r'[0-9][0-9A-Za-z_]*', a):
            v = cyr_int(a)
            if v is None:
                add(m.start(), f"syscall number {a!r} is not a Cyrius integer literal")
            elif v not in SYSCALL_NUMBERS:
                add(m.start(), f"syscall number {a} (= {v}) is not on the allow-list "
                               f"({', '.join(str(n) for n in sorted(SYSCALL_NUMBERS))})")
        else:
            add(m.start(), f"syscall number {a!r} is not a SYS_* name or an integer literal")
    for m in SYS_DECL.finditer(code):
        add(m.start(), f"declares {m.group(1)} (a SYS_* name must be the stdlib's)")
    for m in SYS_ASSIGN.finditer(code):
        add(m.start(), f"assigns {m.group(1)} (a SYS_* name must be the stdlib's)")
    for rx, what in ((ENUM_OPEN, "an enum member"), (FN_PARAMS, "a parameter")):
        for m in rx.finditer(code):
            k = m.end() - 1
            e = _close(code, k, code[k], "}" if code[k] == "{" else ")")
            body = code[k:e if e > 0 else len(code)]
            for s in SYS_ANY.finditer(body):
                add(k + s.start(), f"declares {s.group(0)} as {what} (a SYS_* name must be the stdlib's)")
    for m in BUFFER.finditer(code):
        size = m.group(2).strip()
        v = cyr_int(size)
        if v is None:
            add(m.start(), f"static buffer size {size!r} is not a Cyrius integer literal")
        elif v >= BUFFER_MAX:
            add(m.start(), f"static buffer of {v} bytes")
    seen = set()
    for ln, word in cyr_scan.directives(src):
        if word in CYR_DIRECTIVES_REFUSED:
            seen.add((ln, word))
            hits.append((ln, f"#{word}: {CYR_DIRECTIVES_REFUSED[word]}",
                         lines[ln - 1].strip() if ln <= len(lines) else ""))
    for m in CYR_LINE_REFUSED.finditer(src):
        ln = src.count("\n", 0, m.start()) + 1
        if (ln, m.group(1)) not in seen:
            add(m.start(), f"#{m.group(1)} at the start of a line: {CYR_DIRECTIVES_REFUSED[m.group(1)]}")
    strings = cyr_scan.literals(src, "cyr")
    ends = {s: (e, raw) for s, e, raw in strings}
    for m in INCLUDE.finditer(code):
        q = m.end() - 1
        e, raw = ends.get(q, (None, False))
        try:
            body = lits[q + 1:e - 1] if e else None
            inc = body if (raw or body is None) else cyr_unescape(body)
        except ValueError:
            inc = None
        why = include_problem(inc, stdlib, exists)
        if why:
            add(m.start(), f"include {inc!r}: {why}")
    for s, e, raw in strings:
        body = src[s + 1:e - 1] if e - s >= 2 and src[e - 1] == '"' else src[s + 1:e]
        try:
            v = body if raw else cyr_unescape(body)
        except ValueError as x:
            add(s, f"string literal cycc would refuse ({x})")
            continue
        p = system_path(v)
        if p is not None:
            add(s, f"string literal reaches {p}")
    return hits


def c_include_problem(word, rest):
    """Why a C `#include`-family line is refused, or None. 1.1.2 (final
    pass): a quoted include reads a file from the tree — tests/fixtures/
    holds data no scan lexes — so only a plain `<system/header.h>` is
    allowed: no absolute path, no `..`, no macro, and no #embed."""
    if word == "embed":
        return "#embed puts a file's bytes into the object, unscanned"
    m = C_SYSTEM_HEADER.fullmatch(rest)
    if m is None:
        return "only a plain `<system/header.h>` is allowed (a quoted or computed include reads a file no scan judges)"
    if ".." in m.group(1).split("/"):
        return "a `..` in a header name leaves the system include path"
    return None


def include_problem(inc, stdlib, exists):
    if inc is None:
        return "not a plain string literal"
    if inc.startswith("/"):
        return "absolute path"
    if ".." in inc:
        return "contains `..`"
    if not inc.startswith(("src/", "lib/", "tests/")):
        return "outside src/ lib/ tests/"
    if not inc.endswith(CYR_EXT):
        return "not a Cyrius file the scan reads"
    if inc.startswith("lib/"):
        mod = inc[4:].rsplit(".", 1)[0]
        if "/" in mod or mod not in stdlib:
            return "not a [deps].stdlib module"
    elif exists is not None and not exists(inc):
        return "no such scanned file"
    return None


def lang_of(path):
    if path.endswith(C_EXT):
        return "c"
    if path.endswith(CYR_EXT):
        return "cyr"
    return None


def _cyml_entries(text):
    """(sections, entries) of cyrius.cyml: [(line, name)] for each table
    header and [(line, section, key, value)] for each key, a multi-line
    array joined. ValueError on a line it cannot read: an unread line is
    one the judgement below would miss."""
    def uncomment(s):
        out, q = [], False
        for ch in s:
            if ch == '"':
                q = not q
            if ch == "#" and not q:
                break
            out.append(ch)
        return "".join(out)
    sections, entries, section = [], [], None
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        ln = i + 1
        s = uncomment(lines[i]).strip()
        i += 1
        if not s:
            continue
        if s.startswith("["):
            m = re.fullmatch(r'\[\s*([A-Za-z0-9_.-]+)\s*\]', s)
            if m is None:
                raise ValueError(f"line {ln}: a table header the scan cannot read: {s!r}")
            section = m.group(1)
            sections.append((ln, section))
            continue
        m = re.fullmatch(r'([A-Za-z0-9_-]+)\s*=\s*(.*)', s)
        if m is None:
            raise ValueError(f"line {ln}: a line the scan cannot read: {s!r}")
        value = m.group(2)
        while value.startswith("[") and value.count("[") > value.count("]") and i < len(lines):
            value += " " + uncomment(lines[i]).strip()
            i += 1
        entries.append((ln, section, m.group(1), value))
    return sections, entries


def _cyml_list(value):
    """The names of a `["a", "b"]` list, or None if it is not one."""
    if not re.fullmatch(r'\[\s*(?:"[^"\\]*"\s*(?:,\s*"[^"\\]*"\s*)*,?\s*)?\]', value):
        return None
    return re.findall(r'"([^"\\]*)"', value)


def stdlib_allowed(root):
    """tools/stdlib_allowed.txt: the module names it lists, or None."""
    try:
        text = open(os.path.join(root, STDLIB_ALLOWED), encoding="utf-8").read()
    except OSError:
        return None
    return [ln.split()[0] for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def manifest_problems(root, exists):
    """(stdlib, findings) for cyrius.cyml. 1.1.2 (final pass): the manifest
    decides what code a build prepends with no include line to see, so it
    is judged like source: only [package] [build] [lib] [deps] tables;
    [deps] holds `stdlib` alone, naming exactly tools/stdlib_allowed.txt
    and nothing in DENIED_STDLIB; [build] entry / test and [lib] modules
    name files this scan reads. `stdlib` is what includes may name."""
    bad = []
    try:
        text = open(os.path.join(root, "cyrius.cyml"), encoding="utf-8").read()
        sections, entries = _cyml_entries(text)
    except (OSError, ValueError) as e:
        return set(EXTRA_STDLIB), [f"cyrius.cyml: {e} — what a build pulls in cannot be judged"]
    names = [s for _, s in sections]
    for ln, s in sections:
        if s not in MANIFEST_SECTIONS:
            bad.append(f"cyrius.cyml:{ln}: [{s}] — a table the scan does not judge "
                       "([deps.NAME], [groups] and [test] all pull code into a build)")
        elif names.count(s) > 1:
            bad.append(f"cyrius.cyml:{ln}: [{s}] appears twice")
    keys = {}
    for ln, sec, key, value in entries:
        if (sec, key) in keys:
            bad.append(f"cyrius.cyml:{ln}: {key} appears twice in [{sec}]")
        keys[(sec, key)] = (ln, value)
        if sec is None:
            bad.append(f"cyrius.cyml:{ln}: {key} outside any table")
        elif sec == "deps" and key != "stdlib":
            bad.append(f"cyrius.cyml:{ln}: [deps] {key} — [deps] may hold `stdlib` only")
        elif sec == "build" and key not in BUILD_KEYS:
            bad.append(f"cyrius.cyml:{ln}: [build] {key} — not one of {sorted(BUILD_KEYS)}")
    stdlib = set(EXTRA_STDLIB)
    allowed = stdlib_allowed(root)
    if ("deps", "stdlib") not in keys:
        bad.append("cyrius.cyml: no [deps].stdlib list")
    else:
        ln, value = keys[("deps", "stdlib")]
        mods = _cyml_list(value)
        if mods is None:
            bad.append(f"cyrius.cyml:{ln}: [deps].stdlib is not a plain list of names")
            mods = []
        stdlib |= set(mods)
        for d in sorted({x for x in mods if mods.count(x) > 1}):
            bad.append(f"cyrius.cyml:{ln}: [deps].stdlib names {d!r} twice")
        for d in sorted(set(mods) & DENIED_STDLIB):
            bad.append(f"cyrius.cyml:{ln}: [deps].stdlib names {d!r}, a stdlib module that forks, execs, "
                       "loads code or maps executable memory (DENIED_STDLIB)")
        if allowed is None:
            bad.append(f"{STDLIB_ALLOWED}: missing — [deps].stdlib cannot be judged")
        elif set(mods) != set(allowed) or len(allowed) != len(set(allowed)):
            bad.append(f"cyrius.cyml:{ln}: [deps].stdlib is not {STDLIB_ALLOWED}: "
                       f"added {sorted(set(mods) - set(allowed))}, removed {sorted(set(allowed) - set(mods))}"
                       + (" (the allow-list repeats a name)" if len(allowed) != len(set(allowed)) else ""))
    for (sec, key), (ln, value) in sorted(keys.items(), key=lambda kv: kv[1][0]):
        if (sec, key) in (("build", "entry"), ("build", "test")):
            files = [value[1:-1]] if re.fullmatch(r'"[^"\\]*"', value) else None
        elif (sec, key) == ("lib", "modules"):
            files = _cyml_list(value)
        else:
            continue
        if files is None:
            bad.append(f"cyrius.cyml:{ln}: [{sec}] {key} is not a plain string / list")
            continue
        for f in files:
            top = ("src/",) if sec == "lib" else ("src/", "tests/")
            if f.startswith("/") or ".." in f.split("/") or not f.startswith(top) or not exists(f):
                bad.append(f"cyrius.cyml:{ln}: [{sec}] {key} names {f!r}, not a Cyrius file under "
                           f"{' or '.join(top)} that this scan reads")
    return stdlib, bad


def scan_tree(root):
    findings = []

    def rel(p):
        return os.path.relpath(p, root).replace(os.sep, "/")

    def exists(inc):
        p = os.path.join(root, inc)
        return os.path.isfile(p) and not os.path.islink(p) and lang_of(p) == "cyr"

    stdlib, findings = manifest_problems(root, exists)
    # 1.1.2 (final pass): the bundle too, as ONE file. Each src/ module is
    # scanned on its own, but cycc reads the bundle's modules as one text: a
    # string left open at the end of one module (a hazard in that module)
    # continued into the next, and a sys_execve there compiled into the
    # bundle with every module scanning clean. Here it is plain code.
    b = os.path.join(root, BUNDLE)
    if os.path.lexists(b):
        if os.path.islink(b) or not os.path.isfile(b):
            findings.append(f"{BUNDLE}: not a regular file")
        else:
            try:
                src = open(b, encoding="utf-8", errors="replace").read()
                for ln, label, text in scan_text(b, src, stdlib, exists):
                    findings.append(f"{BUNDLE}:{ln}: {label}: {text}")
            except Exception as e:
                findings.append(f"{BUNDLE}: the scanner failed on this file ({e!r})")
    for top in SCOPE:
        base = os.path.join(root, top)
        if os.path.islink(base):
            findings.append(f"{top}: symlink in scope (cycc follows it; the scan would not)")
            continue
        for d, dirs, files in os.walk(base):
            for name in sorted(dirs):
                if os.path.islink(os.path.join(d, name)):
                    findings.append(f"{rel(os.path.join(d, name))}: symlink in scope (cycc follows it; the scan would not)")
            for f in sorted(files):
                p = os.path.join(d, f)
                r = rel(p)
                st = os.lstat(p)
                if stat.S_ISLNK(st.st_mode):
                    findings.append(f"{r}: symlink in scope (cycc follows it; the scan would not)")
                    continue
                if not stat.S_ISREG(st.st_mode):
                    findings.append(f"{r}: not a regular file")
                    continue
                if lang_of(p) is None:
                    if not (r + "/").startswith(DATA_DIR + "/"):
                        findings.append(f"{r}: a file type the scan does not lex (anything in scope can be included)")
                    continue
                try:
                    src = open(p, encoding="utf-8", errors="replace").read()
                    for ln, label, text in scan_text(p, src, stdlib, exists):
                        findings.append(f"{r}:{ln}: {label}: {text}")
                except Exception as e:                     # odd input is a finding, never a pass
                    findings.append(f"{r}: the scanner failed on this file ({e!r})")
    return findings


def self_test():
    STD = {"syscalls", "string", "alloc", "fmt", "bench"}
    must_flag = [
        ("a.cyr", 'fn f() { sys_execve(p, a, e); return 0; }', "sys_execve("),
        ("a.cyr", 'fn f() { var p = &sys_execve; return p; }', "&sys_execve (no call)"),
        ("a.cyr", 'fn f() { return syscall(SYS_EXECVE, 0, 0, 0); }', "SYS_EXECVE"),
        ("a.cyr", 'fn f() { return syscall(SYS_execve, 0, 0, 0); }', "SYS_execve, lowercase"),
        ("a.cyr", 'fn f() { return syscall(59, 0, 0, 0); }', "syscall(59"),
        ("a.cyr", 'fn f() { return syscall(0x3b, 0, 0, 0); }', "syscall(0x3b — hex"),
        ("a.cyr", 'fn f() { return syscall(221, 0, 0, 0); }', "aarch64 execve 221"),
        ("a.cyr", 'fn f() { return sys_fork(); }', "sys_fork"),
        ("a.cyr", 'fn f() { var s = "/etc/passwd"; return s; }', '"/etc/ literal'),
        ("a.cyr", 'fn f() { var s = "/sbin/x"; return s; }', '"/sbin/ literal'),
        ("a.cyr", 'fn f() { var big[65536]; return 0; }', "var big[65536]"),
        ("a.cyr", 'fn f() { var big[0x10000]; return 0; }', "var big[0x10000] — hex"),
        # attribute lines and define bodies are code (cyr_scan)
        ("a.cyr", '#must_use fn spawn_a(p, a, e) { return sys_execve(p, a, e); }', "#must_use fn .. sys_execve"),
        ("a.cyr", '#pure fn spawn_b() { return syscall(59, 0, 0, 0); }', "#pure fn .. syscall(59"),
        ("a.cyr", '#assert 1 == 1; var big[1048576];', "#assert ..; var big[1 MiB]"),
        ("a.cyr", '#io fn path_c() { return "/etc/shadow"; }', '#io fn .. "/etc/shadow"'),
        ("a.tcyr", '#define RUN(q1, q2, q3) sys_execve(q1, q2, q3)\nfn never() { return RUN(0, 0, 0); }',
         "a function-like #define body"),
        # syscall numbers: a positive allow-list
        ("a.cyr", "fn f() { return syscall(';', 0, 0, 0); }", "syscall(';' — a char literal is 59"),
        ("a.cyr", "fn f() { return syscall(0o73, 0, 0, 0); }", "syscall(0o73 — octal"),
        ("a.cyr", "fn f() { return syscall(5_9, 0, 0, 0); }", "syscall(5_9 — separator"),
        ("a.cyr", "fn f() { return syscall(0x3_b, 0, 0, 0); }", "syscall(0x3_b"),
        ("a.cyr", "fn f() { return syscall(059, 0, 0, 0); }", "syscall(059 — leading zero is decimal"),
        ("a.cyr", "fn f() { return syscall(58 + 1, 0, 0, 0); }", "syscall(58 + 1 — an expression"),
        ("a.cyr", "fn f() { var n = 57; return syscall(n, 0); }", "syscall(n — a variable"),
        ("a.cyr", "fn f() { return syscall((59), 0, 0, 0); }", "syscall((59) — parenthesised"),
        ("a.cyr", "fn f() { return syscall(0x10000003b, 0, 0, 0); }", "syscall(0x10000003b — low 32 bits"),
        ("a.cyr", "fn f() { return syscall(0x40000039); }", "syscall(x32 bit | fork)"),
        ("a.cyr", "fn f() { return syscall(0x10000003c, 0); }", "syscall(0x10000003c — exit's low bits, not 60"),
        ("a.cyr", "fn f() { return syscall<i64>(59, 0); }", "syscall<i64>( — not a direct call"),
        ("a.cyr", "fn f() { return syscall(0X3B, 0); }", "syscall(0X3B — not a Cyrius literal"),
        ("a.cyr", "fn f() { return syscall(1, 1, \"x\", 1); }", "syscall(1 — write, not on the list"),
        ("a.cyr", "fn f() { return syscall(SYS_WRITE, 1, \"x\", 1); }", "syscall(SYS_WRITE — not on the list"),
        ("a.cyr", "fn f() { var code = syscall(SYS_MMAP, 0, 4096, 7, 34, 0 - 1, 0); store8(code, 0xC3); "
                  "return fncall0(code); }", "the finding's RWX mmap + fncall0"),
        ("a.cyr", "fn f() { return syscall(9, 0, 4096, 7, 34, 0 - 1, 0); }", "syscall(9 — mmap by number"),
        ("a.cyr", "fn f() { return syscall(10, p, 4096, 7); }", "syscall(10 — mprotect by number"),
        ("a.cyr", "fn f() { return sys_mmap(0, 4096, 7, 34, 0 - 1, 0); }", "sys_mmap("),
        ("a.cyr", "fn f() { return sys_mprotect(p, 4096, PROT_EXEC); }", "sys_mprotect / PROT_EXEC"),
        ("a.cyr", "fn f() { var p = &sys_pkey_mprotect; return p; }", "&sys_pkey_mprotect"),
        ("a.cyr", "fn f() { return wrap(SYS_MPROTECT, p); }", "SYS_MPROTECT outside syscall("),
        ("a.cyr", "fn f() { return sys_process_vm_writev(1, 0, 0, 0, 0, 0); }", "sys_process_vm_writev"),
        ("a.cyr", "fn f() { asm { 0x48; 0xC7; 0xC0; 0x3B; 0x00; 0x00; 0x00; 0x0F; 0x05; } return 0; }",
         "an asm block"),
        ("a.cyr", "var SYS_FOO = 59;\nfn f() { return syscall(SYS_FOO, 0); }", "var SYS_FOO = 59"),
        ("a.cyr", "const SYS_FOO = 59;", "const SYS_FOO"),
        ("a.cyr", "fn SYS_FOO() { return 59; }", "fn SYS_FOO"),
        ("a.cyr", "enum Ev { A0 = 58; SYS_FOO; }\nfn f() { return syscall(SYS_FOO, 0); }", "an auto-numbered enum SYS_ member"),
        ("a.cyr", "fn f(SYS_FOO) { return syscall(SYS_FOO, 0); }", "a SYS_ parameter"),
        ("a.cyr", "fn f() { return syscall(SYS_SPAWN_PATH, p, n, 0, 0); }", "AGNOS SYS_SPAWN_PATH"),
        ("a.cyr", "fn f() { return sys_spawn_path(p, n); }", "AGNOS sys_spawn_path"),
        ("a.cyr", "fn f() { return execve(0, 0, 0); }", "an object-mode extern execve"),
        ("a.cyr", "fn f() { return fork(); }", "an object-mode extern fork"),
        ("a.cyr", "fn f() { return system (0); }", "an object-mode extern system (a space before the paren)"),
        ("a.cyr", "fn f() { var p = &execve; return fncall3(p, 0, 0, 0); }", "&execve"),
        # buffers parsed by cycc's rules
        ("a.cyr", "var b[0o200000];", "var b[0o200000] — octal"),
        ("a.cyr", "var c[6_5536];", "var c[6_5536] — separator"),
        ("a.cyr", "var d[065536];", "var d[065536] — leading zero is decimal"),
        ("a.cyr", "var e[N];", "var e[N] — not a literal"),
        # scope: includes, #ref, #define
        ("a.cyr", 'include "src/helpers.inc"', "include of a file the scan does not lex"),
        ("a.cyr", 'include "/abs/x.cyr"', "absolute include"),
        ("a.cyr", 'include "src/../x.cyr"', "include with .."),
        ("a.cyr", 'include "other/x.cyr"', "include outside src/ lib/ tests/"),
        ("a.cyr", 'include "lib/process.cyr"', "include of a stdlib module outside [deps]"),
        ("a.cyr", 'include "tests/fixtures/x.bin"', "include of a fixture"),
        ("a.cyr", 'include "src/missing.cyr"', "include of a file that is not in the tree"),
        ("a.cyr", 'include "src/dbus_frame.cyr\\" # .cyr"', "an include name cycc reads raw (`\\` is not an escape)"),
        ("a.cyr", '#ref "tests/fixtures/k.txt"', "#ref"),
        ("a.cyr", '#pe_import("k32", "WinExec")', "#pe_import"),
        ("a.cyr", '#define SAMVADA_X 1', "an object-like #define"),
        ("a.cyr", '  #define\tM(a) 0', "an indented, tab-separated #define"),
        ("a.tcyr", '#define M(a) 0\nvar hid = M(") ;\nfn planted_exec() { return 0; }\nvar hid2 = M(");',
         "the finding's macro-argument wrapper (#5)"),
        ("a.cyr", 'var s = "x\n#define M(a) 0\n";', "a #define line inside a string (the raw-line backstop)"),
        # cyr_scan's desyncs (final pass): each hides the spawn that follows
        ("a.cyr", '#io("fd") note: reads one byte, see "\nfn planted_attr_evil() { return syscall(59, 0, 0, 0); }\n# ")',
         "the finding's `#io(\"fd\") .. \"` comment (#3)"),
        ("a.cyr", '#note: the "\nfn e() { return sys_execve(0, 0, 0); }\n# "', "a `#note: the \"` comment"),
        ("a.cyr", '#define SAMVADA_FFI_PLANT "\nfn planted_evil() { return syscall(59, 0, 0, 0); }\n}\n# "',
         "the finding's object-like `#define NAME \"` (#4)"),
        ("a.cyr", '#ifdef NOPE\nx \' "\n#endif\nfn e() { return sys_fork(); }\n# "', "a lone `'` in a false #ifdef"),
        ("a.cyr", 'include "lib/syscalls.cyr" #assert 1, "\nfn e() { return sys_fork(); }\n# "',
         "an include line's `#assert ..\"` tail"),
        ("a.cyr", "fn f() { return '''; }", "`'''` (the preprocessor and lexer disagree)"),
        ("a.cyr", 'fn f() { return 0; }\nvar s = "', "a string open at the end of the file"),
        # paths: decoded, normalised, joined
        ("a.cyr", r'fn f() { return "\x2fetc/shadow"; }', r'"\x2fetc/shadow"'),
        ("a.cyr", 'fn f() { return "//etc/shadow"; }', '"//etc/shadow"'),
        ("a.cyr", 'fn f() { return "/./sbin/init"; }', '"/./sbin/init"'),
        ("a.cyr", r'fn f() { return "\u{2f}bin/sh"; }', r'"\u{2f}bin/sh"'),
        ("a.cyr", r'fn f() { return "\u002fbin/sh"; }', r'"\u002fbin/sh"'),
        ("a.cyr", 'fn f() { return "/x/../etc/passwd"; }', '"/x/../etc/passwd"'),
        ("a.cyr", 'fn f() { return "/etc"; }', '"/etc" itself'),
        ("a.cyr", 'fn f() { return "XX/etc/shadow" + 2; }', '"XX/etc/shadow" + 2 — an offset'),
        ("a.cyr", 'fn f() { return "/usr/bin/busctl"; }', '"/usr/bin/busctl" — every suffix is judged, on purpose'),
        ("a.cyr", r'fn f() { return "/tmp\0/etc/x"; }', r'"/tmp\0/etc/x" — past a NUL'),
        ("a.cyr", r'fn f() { return "/q\/etc"; }', "an escape cycc refuses"),
        # C
        ("a.c", 'int f(void) { return execvpe("x", 0, 0); }', "execvpe("),
        ("a.c", 'int f(void) { return posix_spawnp(0, "x", 0, 0, 0, 0); }', "posix_spawnp("),
        ("a.c", 'int f(void) { return clone(0, 0, 0, 0); }', "clone("),
        ("a.c", 'long f(void) { return syscall(__NR_execve, 0, 0, 0); }', "__NR_execve"),
        ("a.c", 'long f(void) { return syscall(SYS_execve, 0, 0, 0); }', "C SYS_execve"),
        ("a.c", '#define RUN execv(\n', "code on a `#` line"),
        ("a.c", 'int x = 1,\n *p = (int *)fork();\n', "code on a `*` line"),
        ("a.c", 'static const char *p = "/bin/sh";', '"/bin/ literal in C'),
        ("a.c", 'int f(void) { return exe\\\ncve(0, 0, 0); }', "a spliced execve"),
        ("a.c", 'long f(void) { return syscall(SYS_exec\\\nve, 0); }', "a spliced SYS_execve"),
        ("a.c", "long n = 1'000; int r(void) { return execve(0, 0, 0); } long m = 2'000;", "execve between digit separators"),
        ("a.c", "int a; // c\rint r(void) { return execve(0, 0, 0); }", "execve after a lone CR"),
        ("a.c", '#define CAT(a, b) a##b\nint r(void) { return CAT(exe, cve)(0, 0, 0); }', "## token pasting"),
        ("a.c", '#define S(x) #x\nconst char *p = S(execve);', "# stringizing"),
        ("a.c", 'int (*f)(const char *, char *const *, char *const *) = execve;', "an execve fn pointer"),
        ("a.c", 'long f(void) { return syscall(59L, 0); }', "syscall(59L"),
        ("a.c", 'int f(void) { return daemon(0, 0); }', "daemon("),
        ("a.c", 'int f(void) { return wordexp("x", 0, 0); }', "wordexp("),
        ("a.c", 'int f(void) { return forkpty(0, 0, 0, 0); }', "forkpty("),
        ("a.c", 'void *f(void) { return dlsym(RTLD_DEFAULT, "system"); }', "dlsym("),
        ("a.c", 'int f(void) { return _Fork(); }', "_Fork("),
        ("a.c", 'int f(void) { return __clone(0, 0, 0, 0); }', "__clone("),
        ("a.c", 'int f(void) { return pidfd_spawnp(0, 0, 0, 0, 0, 0); }', "pidfd_spawnp("),
        ("a.c", 'void f(void) { __asm__ volatile ("syscall"); }', "__asm__"),
        ("a.c", 'void *f(void) { return mmap(0, 4096, 7, 34, -1, 0); }', "mmap( in C"),
        ("a.c", 'const char *p = "/e" "tc/passwd";', '"/e" "tc/passwd"'),
        ("a.c", 'const char *p = "\\x2f" "bin/sh";', '"\\x2f" "bin/sh"'),
        ("a.c", 'const char *p = "\\057etc/x";', "octal escape"),
        ("a.c", 'const wchar_t *p = L"/etc/x";', "a wide literal"),
        ("a.c", 'const char *p = R"x(/etc/x)x";', "a raw literal"),
        ("a.c", '#define E "/e"\nconst char *p = E "tc/passwd";', "a literal macro joined to a literal"),
        ("a.c", 'const char *p = "/e"\n  /* c */ "tc/passwd";', "literals joined across a comment"),
        # C includes (final pass, #7)
        ("a.c", '#include "../tests/fixtures/capture_notes.txt"', "the finding's quoted include of a fixture"),
        ("a.c", '#include "samvada_main.h"', "any quoted include"),
        ("a.c", '#include </home/runner/work/x/tests/fixtures/x.txt>', "an absolute angle include"),
        ("a.c", '#include <../../tmp/x.h>', "a `..` angle include"),
        ("a.c", '#define H "x.h"\n#include H', "a computed include"),
        ("a.c", '%:include "x.h"', "a digraph include"),
        ("a.c", 'const unsigned char b[] = {\n#embed "../tests/fixtures/x.bin"\n};', "#embed"),
        ("a.c", '#include_next "x.h"', "#include_next with quotes"),
    ]
    must_pass = [
        ("a.cyr", '# sys_execve(p) in a comment\nfn f() { return 0; }', "a Cyrius comment"),
        ("a.cyr", 'fn f() { var s = "sys_execve("; return s; }', "a string mentioning sys_execve("),
        ("a.cyr", 'fn f() { var t = "/run/dbus/system_bus_socket"; return t; }', "the real socket path"),
        ("a.cyr", 'fn f() { var t = "/dev/null"; var u = "/org/freedesktop/login1/session/_31"; return t; }',
         "test and dbus object paths"),
        ("a.cyr", 'fn f() { var b[8192]; return syscall(228, 1, b); }', "8192 bytes, syscall 228"),
        ("a.cyr", 'fn f() { var b[65535]; var c[0xFFFF]; var d[6_4]; return 0; }', "buffers under the cap"),
        ("a.cyr", 'fn f() { return syscall(SYS_FCNTL, 3, 1, 0); }', "syscall(SYS_FCNTL"),
        ("a.cyr", 'fn f() { syscall(60, 0); syscall(48, 0, 2); syscall(0x3c, 0); syscall(SYS_EXIT, 0); return 0; }',
         "exit / shutdown"),
        ("a.cyr", 'fn f() { assert_eq(SYS_RECVMSG, 47, "x"); return 0; }', "a SYS_ name read as a value"),
        ("a.cyr", 'fn f() { return my_sys_execve_count; }', "an identifier containing the name"),
        ("a.cyr", 'include "lib/syscalls.cyr"\ninclude "src/dbus_frame.cyr"\ninclude "lib/bench.cyr"',
         "includes of deps modules and tree files"),
        ("a.cyr", '#ifdef CYRIUS_ARCH_X86\nfn f() { return 0; }\n#endif', "a preprocessor block"),
        ("a.cyr", 'fn f() { var c = \'"\'; var s = "#x"; return c; }', "a quote char literal and a # string"),
        # 1.1.2 (final pass) false positives (#12)
        ("a.cyr", 'fn f(system) { return system; }', "a parameter named system"),
        ("a.cyr", 'fn f() { var daemon = 0; var fork_count = daemon; return fork_count; }', "a local named daemon"),
        ("a.cyr", '#XXX never call sys_execve here\nfn f() { return 0; }', "`#XXX ..` is a comment"),
        ("a.cyr", '#TODO: make this public later\n#note: the fn table is internal\nfn f() { return 0; }',
         "`#TODO` / `#note` comments"),
        ("a.cyr", 'fn f() { return 0; } # see #define and #ref in the docs', "#define / #ref named in a comment"),
        ("a.c", '/* execve( */ // fork(\nint x;', "C comments"),
        ("a.c", 'int f(void) { return my_fork_count(); }', "an identifier ending in fork"),
        ("a.c", 'int f(int fd) { return fcntl(fd, F_DUPFD_CLOEXEC, 3) + sd_bus_open_system(0); }',
         "fcntl and sd_bus_open_system"),
        ("a.c", '#define LOGIND_MGR "/org/freedesktop/login1"\nconst char *m = LOGIND_MGR "/session";',
         "the shim's literal macros"),
        ("a.c", "long n = 1'000; char c = 'x'; char q = '\"';", "digit separators and char literals"),
        ("a.c", '#include <systemd/sd-bus.h>\n#include <errno.h>\n#define X 1\n#ifdef X\n#endif', "C directives"),
        ("a.c", '#include <sys/syscall.h>', "`<sys/syscall.h>` (a header name is not code)"),
    ]
    exists = {"src/dbus_frame.cyr"}.__contains__
    bad = 0
    for name, src, what in must_flag:
        try:
            ok = bool(scan_text(name, src, STD, exists))
        except Exception as e:
            ok = False
            what += f" (scanner crashed: {e!r})"
        print(("  ok   flags " if ok else "  FAIL misses ") + what)
        bad |= not ok
    for name, src, what in must_pass:
        try:
            hits = scan_text(name, src, STD, exists)
        except Exception as e:
            hits = [f"scanner crashed: {e!r}"]
        print(("  ok   clean " if not hits else "  FAIL flags ") + what + ("" if not hits else f": {hits}"))
        bad |= bool(hits)
    for n in ("0x", "0o8", "0X3B", "1e2", "59L", "0b2", "0x_"):
        ok = cyr_int(n) is None
        print(("  ok   refuses " if ok else "  FAIL parses ") + f"the non-literal {n!r}")
        bad |= not ok
    for n, v in (("059", 59), ("0_59", 59), ("59_", 59), ("0x_3b", 59), ("0o_73", 59), ("1__0", 10), ("0b111011", 59)):
        ok = cyr_int(n) == v
        print(("  ok   parses " if ok else "  FAIL misparses ") + f"{n!r} as {v}")
        bad |= not ok
    bad |= _tree_controls()
    print("\nsecurity_scan self-test " + ("FAILED" if bad else "passed"))
    return int(bad)


GOOD_CYML = ('[package]\nname = "t"\ncyrius = "6.7.18"\n\n[build]\nentry = "src/ok.cyr"\n'
             'test = "src/ok.cyr"\noutput = "t"\n\n[lib]\nmodules = [\n    "src/ok.cyr", # c\n    "src/b.cyr",\n]\n\n'
             '[deps]\nstdlib = ["syscalls", "string"]\n')
GOOD_ALLOWED = "# the frozen list\nsyscalls   the wrappers\nstring     strings\n"


def _tree_controls():
    """scan_tree's own refusals, on throwaway trees (removed after)."""
    import shutil
    import tempfile
    global scan_text
    bad = 0

    def tree(files, links=()):
        t = tempfile.mkdtemp(prefix="samvada-scan-")
        for rel, text in files.items():
            os.makedirs(os.path.dirname(os.path.join(t, rel)) or t, exist_ok=True)
            with open(os.path.join(t, rel), "w", encoding="utf-8") as f:
                f.write(text)
        for target, at in links:
            os.symlink(target, os.path.join(t, at))
        return t

    def run(files, want, what, links=()):
        nonlocal bad
        t = tree(files, links)
        try:
            got = scan_tree(t)
        finally:
            shutil.rmtree(t, ignore_errors=True)
        for frag, w in want:
            ok = any(frag in g for g in got)
            print(("  ok   flags " if ok else "  FAIL misses ") + f"in a tree: {w}")
            bad |= not ok
        if not want:
            ok = not got
            print(("  ok   clean " if ok else "  FAIL flags ") + f"in a tree: {what}" + ("" if ok else f": {got}"))
            bad |= not ok
        return got

    base = {"cyrius.cyml": GOOD_CYML, STDLIB_ALLOWED: GOOD_ALLOWED,
            "src/ok.cyr": 'include "lib/syscalls.cyr"\ninclude "src/b.cyr"\nfn f() { return 0; }\n',
            "src/b.cyr": "fn g() { return 0; }\n", "tests/fixtures/data.bin": "\0\1binary",
            "deps/shim.c": "#include <errno.h>\n#include <systemd/sd-bus.h>\nint x;\n"}
    run(base, [], "the good manifest, includes and fixture data")

    real = scan_text

    def boom(path, src, stdlib, exists=None):
        if path.endswith("odd.c"):
            raise RuntimeError("planted")
        return real(path, src, stdlib, exists)
    scan_text = boom
    try:
        run(dict(base, **{"src/helpers.inc": "fn h() { return sys_execve(0, 0, 0); }\n",
                          "src/inc.cyr": 'include "src/nope.cyr"\n',
                          "extra/z.cyr": "fn z() { return sys_execve(0, 0, 0); }\n",
                          "tests/fixtures/evil.cyr": "fn e() { return sys_fork(); }\n",
                          "deps/odd.c": "int x;\n"}),
            [("src/more: symlink", "a directory symlink"), ("src/link.cyr: symlink", "a file symlink"),
             ("src/helpers.inc: a file type", "a file type the scan does not lex"),
             ("src/inc.cyr:1: include 'src/nope.cyr': no such scanned file", "an include of a missing file"),
             ("tests/fixtures/evil.cyr:1: process spawn helper", "code under tests/fixtures/"),
             ("deps/odd.c: the scanner failed", "a scanner exception")],
            "", links=(("../extra", "src/more"), ("../extra/z.cyr", "src/link.cyr")))
    finally:
        scan_text = real

    # cyrius.cyml (final pass, #6): what a build pulls in with no include line.
    proc = GOOD_CYML.replace('["syscalls", "string"]', '["syscalls", "string", "process"]')
    run(dict(base, **{"cyrius.cyml": proc}), [("[deps].stdlib is not tools/stdlib_allowed.txt: added ['process']",
                                               "a stdlib module added to cyrius.cyml alone (the finding's \"process\")")], "")
    run(dict(base, **{"cyrius.cyml": proc, STDLIB_ALLOWED: GOOD_ALLOWED + "process  run()\n"}),
        [("names 'process', a stdlib module that forks", "\"process\" added to both lists (DENIED_STDLIB)")], "")
    for extra, frag, what in (('\n[deps.evil]\ngit = "https://example.invalid/evil"\ntag = "1.0"\n', "[deps.evil] — a table",
                               "a [deps.NAME] git dependency"),
                              ('\n[ deps.evil ]\npath = "../evil"\n', "[deps.evil] — a table", "a spaced [ deps.NAME ] header"),
                              ('\n[groups]\nnetstack = ["net"]\n', "[groups] — a table", "a [groups] table"),
                              ('\n[test]\nstdlib = ["process"]\n', "[test] — a table", "a [test] stdlib"),
                              ('\n[deps]\nstdlib = ["process"]\n', "[deps] appears twice", "a second [deps] table")):
        run(dict(base, **{"cyrius.cyml": GOOD_CYML + extra}), [(frag, what)], "")
    for old, new, frag, what in (
            ('test = "src/ok.cyr"', 'test = "tests/fixtures/data.bin"', "[build] test names 'tests/fixtures/data.bin'",
             "[build].test naming a fixture"),
            ('    "src/b.cyr",', '    "src/b.cyr", "tests/x.tcyr",', "[lib] modules names 'tests/x.tcyr'",
             "a [lib] module outside src/"),
            ('output = "t"', 'output = "t"\ndefines = ["X"]', "[build] defines", "an unknown [build] key"),
            ('stdlib = ["syscalls", "string"]', 'stdlib = ["syscalls", "string"]\npath = "x"', "[deps] path",
             "a second [deps] key"),
            ('stdlib = ["syscalls", "string"]', 'stdlib = ["syscalls", "string", "syscalls"]', "names 'syscalls' twice",
             "a repeated module"),
            ('[deps]', '[deps] junk', "a table header the scan cannot read", "an unreadable header")):
        run(dict(base, **{"cyrius.cyml": GOOD_CYML.replace(old, new, 1)}), [(frag, what)], "")
    run({k: v for k, v in base.items() if k != STDLIB_ALLOWED}, [(f"{STDLIB_ALLOWED}: missing", "a missing allow-list")], "")

    # deps/ (final pass, #7): the shim includes only system headers.
    run(dict(base, **{"deps/shim.c": '#include <errno.h>\n#include "../tests/fixtures/capture_notes.txt"\n',
                      "tests/fixtures/capture_notes.txt": 'long r(void) { __asm__ volatile ("syscall"); return 0; }\n'}),
        [("deps/shim.c:2: #include: only a plain", "a quoted include of an unscanned fixture")], "")
    # the bundle (final pass): a string open across two modules hides nothing in it.
    run(dict(base, **{"dist/samvada.cyr": 'fn a() { return 0; }\nvar _s = "\n# --- b ---\n";\n'
                                          'fn _planted_x() { return sys_execve(0, 0, 0); }\n# "\n'}),
        [("dist/samvada.cyr:5: process spawn helper sys_execve", "a spawn in the bundle, scanned as one file")], "")
    # tests (final pass, #5): a #define anywhere.
    run(dict(base, **{"tests/zz_plant.tcyr": '#define M(a) 0\nvar hid = M(") ;\n'
                                             'fn planted_exec() { return syscall(59, 0, 0, 0); }\nvar hid2 = M(");\n'}),
        [("tests/zz_plant.tcyr:1: #define", "the finding's macro wrapper in a test")], "")
    return int(bad)


def main():
    if sys.argv[1:] == ["--self-test"]:
        return self_test()
    if len(sys.argv) != 2:
        print("\n".join(__doc__.strip().splitlines()[-3:-1]), file=sys.stderr)
        return 2
    findings = scan_tree(sys.argv[1])
    for f in findings:
        print("FAIL: " + f)
    print(f"security scan complete: {len(findings)} finding(s) in {', '.join(s + '/' for s in SCOPE)}, "
          f"{BUNDLE} and cyrius.cyml")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
