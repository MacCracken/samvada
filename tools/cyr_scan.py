#!/usr/bin/env python3
"""Comment- and string-aware scanning for samvada's CI gates.

1.1.2 (P(-1) audit, M5). The surface and boundary gates matched
declarations with line-ANCHORED regexes (`^public fn ...`, re.M), so a
`public fn` written after other code on the same line was invisible to
both: a new consumer-reachable export passed the freeze. The spawn scan
had the opposite problem — it dropped every line starting with `#` or `*`
as a comment, so code on such a line (a C `#define`, a continuation) was
never scanned.

Every gate now reads code through this module instead: comments are
blanked (newlines kept, so line numbers survive), strings are blanked or
kept as the gate needs, and declarations are found anywhere on a line.
The declaration parsers also COUNT: every `fn` and every `public` token
in the code must belong to a declaration they parsed, so a form they do
not understand fails the gate instead of slipping past it.

1.1.2 (P(-1) re-verification). Holes in the first cut, each a control in
--self-test: `pub` is the lexer's synonym for `public` (one token, 73) and
was not counted, so `pub var` / `pub struct` + #derive(accessors) exported
state past both gates; arity was a comma count, so `Result<i64, i64>` was
two parameters; and in C, backslash-newline splices, lone-CR line ends and
C23 digit separators (`1'000`) desynchronised the lexer, hiding the code
between them — splices are removed and CRs normalised before lexing (line
numbers kept), a `'` inside a pp-number is not a quote, and raw strings /
header names are read as gcc reads them.

1.1.2 (final pass). The re-verification cut read any `#ident` as an
ATTRIBUTE and lexed the rest of its line as code, which scans more than
cycc compiles — and "more" is not safe when it includes a quote: `#io("fd")
see "` or `#note: the "` opened a string in this lexer that ran over the
following lines, while cycc compiled them. A public fn hidden that way
passed surface, boundary and security with a consumer calling it. The
Cyrius `#` rule is now cycc 6.7.18's, copied from its source (lex/attr.cyr
LEXATTRWORD and lex/pp/pass.cyr), not approximated:

  `#` + one of the ten ATTRIBUTE words (assert regalloc deprecated must_use
      pure io alloc naked inline pe_import), ending at a blank, CR, LF or
      the end of input — or at `(` for assert, deprecated and pe_import
      only: the word is blanked and the REST OF THE LINE IS CODE, strings
      and all, as cycc's lexer (and its preprocessor, PP_LEXST_AT) reads it.
  `#define` + blank at the start of a line (blanks before it allowed): an
      OBJECT-like define is a flag with a value, never substituted, so the
      whole line is blanked; a FUNCTION-like one (`#define NAME(`) is
      substituted at its call sites, so its body is lexed as code — but
      only to the end of its line, where cycc's copy of it stops, so no
      string can leave it.
  any other `#` — a directive (ifdef, derive, ref, ..), `#@file`, `#TODO`,
      `#io(` / `#must_use(` (the paren is not their syntax), `# ` — is a
      comment to the end of the line: cycc's preprocessor consumes the
      directive lines whole and its lexer skips the rest, with no string
      lexing in either.

Three more desyncs of the same class, found while copying the rule, each a
control below:

  * char literals. cycc's PREPROCESSOR reads `'` as "a char literal until
    the next `'` or the line's end"; its lexer accepts only `'x'` / `'\\x'`.
    In a false `#ifdef` region the lexer never runs, so `x ' "` there was a
    char literal to cycc and an open string here: the lines after the
    `#endif` were compiled by cycc and hidden from every gate (built: a fn
    planted there ran). A `'` that is not a lexer literal is now read the
    preprocessor's way. `'''` and `'<LF>'` are the two literals the lexer
    and the preprocessor read DIFFERENTLY — a HAZARD (see hazards()).
  * `include "name"` at the start of a line is consumed whole by cycc's
    preprocessor: the name is raw (no escapes) to its first quote, and the
    rest of the line is dropped (only blanks, `;` and a `#` comment may
    follow it — and `#assert ..` there IS that comment). Read as code, a
    `#assert 1, "` after the name opened a string here.
  * a string literal still open at the END OF A FILE continues, in cycc,
    into whatever follows that file in the build (the including file's next
    line, the next bundle module), while each file is scanned on its own:
    a HAZARD.

Usage:  cyr_scan.py --self-test     (CI runs it before the gates)
"""
import re
import sys

IDENT = r'[A-Za-z_][A-Za-z0-9_]*'
PUB = r'(?:pub|public)'
FN_HEAD = re.compile(r'(?<![A-Za-z0-9_&.])(' + PUB + r'\s+)?fn\s+(' + IDENT + r')\s*\(')
FN_TOKEN = re.compile(r'(?<![A-Za-z0-9_&.])fn(?![A-Za-z0-9_])')
PUBLIC_TOKEN = re.compile(r'(?<![A-Za-z0-9_])' + PUB + r'(?![A-Za-z0-9_])')
TOP_DECL = re.compile(r'(?<![A-Za-z0-9_])(' + PUB + r'\s+)?(fn|var|const|enum|struct|union|impl)(?![A-Za-z0-9_])')
MARKER = re.compile(r'^[ \t]*private[ \t]*;?[ \t]*$', re.M)
ENUM_TOKEN = re.compile(r'(?<![A-Za-z0-9_])enum(?![A-Za-z0-9_])')

# cycc 6.7.18 src/frontend/lex/attr.cyr, LEXATTRWORD: the ten `#` attribute
# words, in its order, with `ap` — whether `(` may end the word.
ATTR_WORDS = (("assert", True), ("regalloc", False), ("deprecated", True), ("must_use", False),
              ("pure", False), ("io", False), ("alloc", False), ("naked", False),
              ("inline", False), ("pe_import", True))
# `#` + this is the word a directive or comment starts with (reported by
# directives(); hyphens allowed so `#skip-lint` is one word).
DIRECTIVE = re.compile(IDENT + r'(?:-[A-Za-z0-9_]+)*')
OPEN, CLOSE = "([{<", ")]}>"


def _blank(s):
    return "".join(c if c == "\n" else " " for c in s)


def attr_word(src, q, n=None):
    """The attribute word the `#` at src[q] spells, or None — LEXATTRWORD
    and LEXATTRBOUND: the buffer must hold two bytes past the `#`, the word
    starts lower-case, and it ends at the end of input, a blank, CR or LF,
    or `(` where `ap` allows it."""
    n = len(src) if n is None else n
    if q + 2 >= n or not "a" <= src[q + 1] <= "z":
        return None
    for w, ap in ATTR_WORDS:
        e = q + 1 + len(w)
        if e > n or not src.startswith(w, q + 1, e):
            continue
        if e >= n or src[e] in " \t\r\n" or (ap and src[e] == "("):
            return w
    return None


def _macro_body(src, i, eol, n):
    """For a `#define` the preprocessor acts on at src[i] (the line's first
    non-blank byte): the offset its FUNCTION-like body starts at, or None
    for an object-like define (or no define). PP_PASS: `#define` + a blank,
    the name runs to `(`, a blank or the line's end; the parameter list to
    the first `)`; the body to the end of the line."""
    if not src.startswith("#define", i) or i + 8 >= n or src[i + 7] not in " \t":
        return None
    j = i + 8
    while j < eol and src[j] in " \t":
        j += 1
    while j < eol and src[j] not in "( \t":
        j += 1
    if j >= eol or src[j] != "(":
        return None
    e = src.find(")", j, eol)
    return eol if e < 0 else e + 1


def normalize_c(src):
    """What gcc's translation phases 1-2 make of C source, line count kept:
    CRLF and lone CR become LF (gcc ends a line, and a // comment, at a
    lone CR), and backslash-newline splices — including gcc's tolerated
    backslash-blanks-newline — are removed. Each removed newline is put
    back after the next real one, so a line keeps its number."""
    src = src.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    pending = 0
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "\\":
            j = i + 1
            while j < n and src[j] in " \t\f\v":
                j += 1
            if j < n and src[j] == "\n":
                pending += 1
                i = j + 1
                continue
        out.append(c)
        if c == "\n" and pending:
            out.append("\n" * pending)
            pending = 0
        i += 1
    out.append("\n" * pending)
    return "".join(out)


def _cyr_char_end(src, i, n):
    """(end, hazard) for the `'` at src[i]. The lexer's literals `'x'` and
    `'\\x'` are read as it reads them; `'''` and `'<LF>'` are lexer literals
    the preprocessor reads differently (it closes `''` and reopens; it ends a
    char literal at a LF), a hazard. Anything else the lexer refuses, so it
    can only stand in a region cycc does not compile — read the
    preprocessor's way (PP_LEXST states 4/5): to the next unescaped `'`, or
    up to the end of the line."""
    if i + 2 < n and src[i + 1] != "\\" and src[i + 2] == "'":
        return i + 3, src[i + 1] in "'\n"
    if i + 3 < n and src[i + 1] == "\\" and src[i + 2] != "\n" and src[i + 3] == "'":
        return i + 4, False
    j = i + 1
    while j < n:
        if src[j] == "\n":
            return j, False
        if src[j] == "\\":
            if j + 1 < n and src[j + 1] == "\n":
                return j + 1, False
            j += 2
            continue
        if src[j] == "'":
            return j + 1, False
        j += 1
    return n, False


def _string_end(src, i, lang, n):
    """(end, closed) for the string or char literal opening at src[i]:
    the index just past it, and whether its closing quote was found."""
    q = src[i]
    j = i + 1
    while j < n and src[j] != q:
        if src[j] == "\\":
            j += 1
        elif src[j] == "\n" and lang == "c":
            return j, False         # C literals end at the line; never hide the next one
        j += 1
    return (j + 1, True) if j < n else (n, False)


def _c_digit_sep(src, i):
    """A C23 digit separator: a `'` inside a pp-number (`1'000`, `0x3'b`)."""
    if i + 1 >= len(src) or not (src[i + 1].isalnum() or src[i + 1] == "_"):
        return False
    k = i - 1
    while k >= 0 and (src[k].isalnum() or src[k] in "_.'"):
        k -= 1
    start = k + 1
    if start >= i:
        return False
    s = src[start]
    return s.isdigit() or (s == "." and start + 1 < i and src[start + 1].isdigit())


RAW_PREFIX = re.compile(r'(?:u8|u|U|L)?R$')
HEADER = re.compile(r'[ \t]*#[ \t]*(?:include|include_next|import)[ \t]*<')


def _c_raw_end(src, i):
    """If src[i] is the `"` of a C raw string R"d(...)d", the index past it."""
    m = RAW_PREFIX.search(src, max(0, i - 3), i)
    if m is None or (m.start() > 0 and (src[m.start() - 1].isalnum() or src[m.start() - 1] == "_")):
        return None
    k = src.find("(", i + 1, i + 18)
    if k < 0:
        return None
    delim = src[i + 1:k]
    if any(c in delim for c in ' \\)\t\n"'):
        return None
    e = src.find(")" + delim + '"', k + 1)
    return None if e < 0 else e + len(delim) + 2


class _Rec:
    """What a walk saw besides its text."""
    def __init__(self):
        self.found = []        # (pos, word): each Cyrius `#word` reached in code
        self.strings = []      # (start, end, raw): each string literal
        self.hazards = []      # (pos, why): input cycc itself reads two ways


def _walk(src, lang, keep_strings, rec, lo=0, hi=None):
    out = []
    n = len(src) if hi is None else hi
    i = lo
    bol = lo == 0 or src[lo - 1] == "\n"       # only blanks so far on this line
    while i < n:
        c = src[i]
        if lang == "cyr" and c == "#":
            eol = src.find("\n", i, n)
            eol = n if eol < 0 else eol
            w = attr_word(src, i, n)
            if w is not None:                   # an attribute: the rest of the line is code
                rec.found.append((i, w))
                out.append(_blank(src[i:i + 1 + len(w)]))
                i += 1 + len(w)
                bol = False
                continue
            m = DIRECTIVE.match(src, i + 1, eol)
            if m is not None:
                rec.found.append((i, m.group(0)))
            body = _macro_body(src, i, eol, n) if bol and hi is None else None
            if body is not None:                # a function-like #define: its body, to the line's end
                out.append(_blank(src[i:body]))
                out.append(_walk(src, lang, keep_strings, rec, body, eol))
            else:                               # a directive or a comment: the line is not code
                out.append(_blank(src[i:eol]))
            i = eol
            continue
        if lang == "cyr" and c == "i" and hi is None and (i == 0 or src[i - 1] == "\n") \
                and src.startswith('include "', i):
            # PP_PASS / PP_IFDEF_PASS (ISINCLUDE, column 0): the name is raw to
            # its first quote and the rest of the line is dropped unread.
            eol = src.find("\n", i)
            eol = n if eol < 0 else eol
            q = src.find('"', i + 9)
            if q < 0 or q > eol:
                rec.hazards.append((i, "an include whose file name does not close on its line"))
                out.append(_blank(src[i:eol]))
            else:
                rec.strings.append((i + 8, q + 1, True))
                lit = src[i + 8:q + 1] if keep_strings else '"' + _blank(src[i + 9:q]) + '"'
                out.append("include " + lit + _blank(src[q + 1:eol]))
            i = eol
            continue
        if lang == "c":
            h = HEADER.match(src, i) if bol else None
            if h is not None:
                e = src.find("\n", i)
                e = n if e < 0 else e
                k = src.find(">", h.end(), e)
                k = e if k < 0 else k + 1
                inner = src[h.end():k - 1] if src[k - 1:k] == ">" else src[h.end():k]
                # A header name is not a literal; nor is it code (`<sys/syscall.h>`).
                out.append(src[i:h.end()] + (inner if keep_strings else _blank(inner)) + src[h.end() + len(inner):k])
                i = k
                bol = False
                continue
            if src.startswith("//", i):
                j = src.find("\n", i)
                j = n if j < 0 else j
                out.append(_blank(src[i:j]))
                i = j
                continue
            if src.startswith("/*", i):
                j = src.find("*/", i + 2)
                j = n if j < 0 else j + 2
                out.append(_blank(src[i:j]))
                i = j
                continue
        if lang == "cyr" and c == "'":
            j, hz = _cyr_char_end(src, i, n)
            if hz:
                rec.hazards.append((i, "a char literal cycc's preprocessor and lexer read differently "
                                       "(`'''` or a quoted raw newline): write '\\'' or '\\n'"))
            out.append(src[i:j] if keep_strings else _blank(src[i:j]))
            i = j
            bol = False
            continue
        if c == '"' or (c == "'" and lang == "c" and not _c_digit_sep(src, i)):
            j = _c_raw_end(src, i) if (lang == "c" and c == '"') else None
            raw = closed = j is not None
            if j is None:
                j, closed = _string_end(src, i, lang, n)
            if c == '"':
                rec.strings.append((i, j, raw))
                if lang == "cyr" and not closed:
                    rec.hazards.append((i, "a string literal still open at the end of its "
                                        + ("file: cycc continues it into whatever follows the file"
                                           if hi is None else
                                           "#define line: each call site opens a string there")))
            if keep_strings:
                out.append(src[i:j])
            else:
                out.append(c + _blank(src[i + 1:j - 1 if closed else j]) + (c if closed else ""))
            i = j
            bol = False
            continue
        out.append(c)
        if c == "\n":
            bol = True
        elif c not in " \t":
            bol = False
        i += 1
    return "".join(out)


def _rec(src, lang="cyr", keep_strings=False):
    if lang == "c":
        src = normalize_c(src)
    r = _Rec()
    text = _walk(src, lang, keep_strings, r)
    return text, r, src


def strip(src, lang="cyr", keep_strings=False):
    """Blank comments (and, unless keep_strings, string/char contents).

    lang "cyr": cycc's `#` / include / literal rules, in the module
                docstring.
    lang "c":   `//` and `/* */`; preprocessor lines are CODE, header names
                are kept only with keep_strings. The source is
                normalize_c()'d first, so offsets index THAT text.
    Newlines are always kept, so offsets map to the same line numbers.
    """
    return _rec(src, lang, keep_strings)[0]


def literals(src, lang="cyr"):
    """[(start, end, raw)] for every string literal (not char literals)
    strip() reads as one: src[start:end] is the literal from its opening
    quote; `raw` is a C raw string R"d(..)d", or a Cyrius include's file
    name (no escapes). For lang "c" the offsets index normalize_c(src)."""
    return _rec(src, lang, True)[1].strings


def directives(src):
    """[(line, word)] for every Cyrius `#word` — attribute, directive or
    comment — that opens in code, not inside a string or a comment."""
    r = _rec(src, "cyr")[1]
    return [(src.count("\n", 0, pos) + 1, name) for pos, name in r.found]


def hazards(src):
    """[(line, why)] for Cyrius input cycc itself reads two ways (its
    preprocessor one way, its lexer the other), so no scan can say which
    code it compiles: refused by every gate."""
    r = _rec(src, "cyr")[1]
    return [(src.count("\n", 0, pos) + 1, why) for pos, why in r.hazards]


DERIVE_LINE = re.compile(r'^[ \t]*#derive(?![A-Za-z0-9_])', re.M)
DEFINE_LINE = re.compile(r'^[ \t]*#define(?![A-Za-z0-9_])', re.M)


def bundle_problems(src):
    """[(line, why)] for what the CONSUMER BUNDLE may not contain, beyond
    fn_decls()'s own refusals. 1.1.2 (final pass): the surface gate's claim
    is that the bundle's public surface is exactly its `public fn`s, and
    three constructs reach a consumer without one:
      `#derive(..)`  on an enum, cycc emits `public fn _cy_enum_name_eq`
                     whatever the file's visibility (lex/pp/derive.cyr) —
                     an export no manifest row covers, and no `fn` token in
                     the bundle's text;
      `enum`         its members are names in the consumer's scope
                     (visibility covers fns and vars only);
      `#define`      a flag set in every consumer's compilation (it can flip
                     the consumer's own #ifdef arms);
    and every hazards() entry. The raw-line checks see a `#derive` /
    `#define` line wherever it is — even inside a string — which only ever
    refuses more."""
    def line(pos):
        return src.count("\n", 0, pos) + 1
    out = [(line(m.start()), "#derive (on an enum it emits an always-public helper)")
           for m in DERIVE_LINE.finditer(src)]
    out += [(line(m.start()), "#define (a flag set in every consumer's compilation)")
            for m in DEFINE_LINE.finditer(src)]
    code = strip(src)
    out += [(line(m.start()), "an enum (its members reach the consumer; visibility covers fns and vars only)")
            for m in ENUM_TOKEN.finditer(code)]
    out += hazards(src)
    return sorted(out)


def _close_paren(code, k):
    """Index of the `)` matching the `(` at code[k], or -1."""
    depth = 0
    for j in range(k, len(code)):
        if code[j] == "(":
            depth += 1
        elif code[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    return -1


def fn_decls(code):
    """[(is_public, name, args)] for every fn declaration in comment-free
    code, or raise ValueError if some `fn`/`pub`/`public` token is not one."""
    decls = []
    for m in FN_HEAD.finditer(code):
        k = m.end() - 1
        e = _close_paren(code, k)
        if e < 0:
            raise ValueError(f"fn {m.group(2)}: unbalanced parameter list")
        decls.append((bool(m.group(1)), m.group(2), code[k + 1:e]))
    nfn = len(FN_TOKEN.findall(code))
    if nfn != len(decls):
        raise ValueError(f"{nfn} `fn` tokens but {len(decls)} parsed declarations — "
                         "a declaration form the gate does not understand")
    npub = len(PUBLIC_TOKEN.findall(code))
    nmarked = sum(1 for d in decls if d[0])
    if npub != nmarked:
        raise ValueError(f"{npub} `pub`/`public` tokens but {nmarked} `public fn` declarations — "
                         "something other than a fn is marked public")
    return decls


def private_first(code):
    """None if a lone `private` leads the code's declarations, else why not."""
    marker = MARKER.search(code)
    if marker is None:
        return "no top-level `private` marker"
    first = TOP_DECL.search(code)
    if first is not None and first.start() < marker.start():
        return "a declaration comes before the `private` marker"
    return None


def arity(args):
    """Top-level parameters in a parameter list's text: commas count only
    outside (), [], {} and <> (so `x: Result<i64, i64>` and `x = f(1, 2)`
    are one each)."""
    depth, n, seg = 0, 0, ""
    for c in args:
        if c in OPEN:
            depth += 1
        elif c in CLOSE:
            depth = max(0, depth - 1)
        if c == "," and depth == 0:
            n += bool(seg.strip())
            seg = ""
        else:
            seg += c
    return n + bool(seg.strip())


def _self_test():
    bad = 0

    def check(cond, what):
        nonlocal bad
        print(("  ok  " if cond else "  FAIL ") + what)
        if not cond:
            bad = 1

    def refused(src, what):
        try:
            fn_decls(strip(src))
            check(False, what)
        except ValueError:
            check(True, what)

    def names(src):
        return {(p, n) for p, n, _ in fn_decls(strip(src))}

    # Same-line declarations are found (the M5 miss).
    got = names('private\nfn a() { return 0; } public fn evil(x) { return x; }\n'
                'public fn ok() { return 0; }\n')
    check((True, "evil") in got, "a `public fn` after code on the same line is found")
    check((False, "a") in got, "and so is the internal fn ahead of it")

    # Comments and strings are not code.
    got = {n for _, n in names('private\n# public fn ghost(a) { }\nvar s = "public fn str(b) {";\n'
                               'fn f() { return 0; } # fn g() trailing\npublic fn ok() { return 0; }\n')}
    check(got == {"f", "ok"}, "commented-out and quoted declarations are not counted")

    # The ten attribute words: the rest of the line is code (cycc compiles it).
    for attr in ("#must_use", "#pure", "#io", "#alloc", "#inline", "#regalloc", "#naked",
                 '#deprecated("x")', "#deprecated", "#assert 1 == 1;", '#assert 1 == 1, "a;b";',
                 '#pe_import("k32", "x")', "#assert(1 == 1)", "#io\t"):
        got = names(f'private\n{attr} public fn evil_attr() {{ return 7; }}\n')
        check((True, "evil_attr") in got, f"`{attr} public fn` on one line is a public fn")
    got = names('private\n#assert 1 == 1, "a\nb"; pub fn evil_ml() { return 7; }\n')
    check((True, "evil_ml") in got, "code after an #assert whose message spans lines is found")
    got = names('private\n#deprecated("x\n) fn hidden() {") pub fn evil_dep() { return 7; }\n')
    check((True, "evil_dep") in got, "an attribute argument's string is a string, wherever it ends")
    got = names('private\nfn a() { return 0; } #must_use public fn evil_mid() { return 7; }\n')
    check((True, "evil_mid") in got, "an attribute mid-line is an attribute (cycc has no line-start rule)")

    # 1.1.2 (final pass): every other `#` is a comment to the end of its line.
    # The skeptic's probes: a quote in such a comment must not hide the next lines.
    for opener in ('#io("fd") note: reads one byte, see "', '#note: the "', '#must_use("', '#io("',
                   '#note("', '#TODO: handle the "', '#ioctl "', '#pure(ly) "', '#inline(always) "',
                   '#XXX "', '#ifdef X "', '#derive(Serialize) "', '#@file "x', '#define N "',
                   '#ref "a" "'):
        got = names(f'private\n{opener}\npublic fn planted_attr_evil() {{ return 7; }}\n# ")\n')
        check((True, "planted_attr_evil") in got, f"`{opener}` is a comment: the next line is code")
    for d in ("#ifdef X", "#ifndef X", "#if 1", "#elif 1", "#else", "#endif", "#ifplat x86",
              "#endplat", "#skip-lint", '#ref "k.txt"', "#host_only", '#@file "a" 1', "#TODO",
              "#note:", "#io(fd)", "#must_use(result)", "#ioctl", "#derive(accessors)", "#define N 7"):
        got = {n for _, n in names(f'private\n{d} public fn ghost() {{ }}\npublic fn ok() {{ return 0; }}\n')}
        check(got == {"ok"}, f"`{d}` blanks the rest of its line")
    code = strip('#define RUN(q1, q2) sys_execve(q1, q2, 0)\n#define N 7 sys_fork()\n')
    check("sys_execve(q1, q2, 0)" in code and "RUN" not in code and "sys_fork" not in code,
          "a function-like #define body is code; an object-like define's line is not")
    code = strip('#define Q(a) "\npublic fn after_q() { return 0; }\n')
    check((True, "after_q") in {(p, n) for p, n, _ in fn_decls(code)},
          "a function-like body is lexed only to its line's end")
    code = strip('  #define F(a) sys_execve(a)\nx = 1; #define G(a) sys_fork(a)\n')
    check("sys_execve" in code and "sys_fork" not in code,
          "a #define is a directive at a line's start (after blanks), a comment mid-line")
    check([w for _, w in directives('fn f() { var s = "#ref x"; }\n#ref "k"\n#must_use fn g() { }\n'
                                     '#note: see #ref\n')] == ["ref", "must_use", "note"],
          "directives() sees code directives, not quoted or commented ones")

    # Char literals: the lexer's, the preprocessor's where the lexer cannot reach.
    src = '#ifdef NOPE\nx \' "\n#endif\npublic fn after_sq() { return 7; }\n# "\n'
    check((True, "after_sq") in {(p, n) for p, n, _ in fn_decls(strip('private\n' + src))},
          "a lone `'` (the preprocessor's char literal, to the line's end) hides nothing")
    src = 'private\nfn f() { var a = \'"\'; var b = \'\\\'\'; var c = \'#\'; return 0; } pub fn after_ch() { }\n'
    check((True, "after_ch") in {(p, n) for p, n, _ in fn_decls(strip(src))},
          "lexer char literals ('\"', '\\'', '#') are literals")
    check(hazards("var q = ''';\n") and hazards("var q = '\n';\n"),
          "`'''` and a quoted raw newline are hazards")
    check(not hazards("var q = '\\'';\nvar r = 'a';\n"), "'\\'' and 'a' are not")

    # include lines are the preprocessor's: raw name, the tail dropped.
    src = 'private\ninclude "lib/syscalls.cyr" #assert 1 == 1, "\npublic fn after_inc() { return 7; }\n# "\n'
    check((True, "after_inc") in {(p, n) for p, n, _ in fn_decls(strip(src))},
          "an include line's tail is dropped, `#assert` and all")
    lit = literals('include "src/a\\" # .cyr"\n')
    check(lit == [(8, 16, True)], "an include's file name is raw, to its first quote")
    check([s for s, _, _ in literals('  include "x"\n')] == [10], "an indented include is ordinary code")

    # Strings open at the end of a file are hazards.
    check(hazards('fn f() { return 0; }\nvar s = "\n'), "a string open at end of file is a hazard")
    check(hazards('var s = "a\\"'), "and so is one whose last quote is escaped")
    check(not hazards('var s = "a\nb";\n'), "a closed multi-line string is not")

    # `pub` is `public`; neither may mark anything but a fn.
    check(fn_decls(strip('private\npub fn evil_pub() { return 7; }\n')) == [(True, "evil_pub", "")],
          "`pub fn` is a public fn")
    refused('private\npublic var x = 1;\npublic fn ok() { return 0; }\n', "`public var` is refused")
    refused('private\npub var evil_var = 7;\n', "`pub var` is refused")
    refused('private\npub var evil_arr[16];\n', "`pub var x[16]` is refused")
    refused('private\n#derive(accessors)\npub struct Evil { a; b; }\n',
            "`pub struct` + #derive(accessors) is refused")
    refused('private\npublic struct Evil { a; }\n', "`public struct` is refused")

    # A fn token the parser cannot read is a failure, not silence.
    refused('private\nfn\nweird() { return 0; }\nfn (x) { }\n', "an unparsable `fn` is refused")

    # fn-pointer and fncallN spellings are not declarations.
    code = strip('private\nfn a() { var p = &fn_x; fncall2(p, 0, 0); return 0; }\n')
    check([n for _, n, _ in fn_decls(code)] == ["a"], "`&fn_x` and `fncall2` are not `fn` tokens")

    # The bundle's own refusals (#derive, enum, #define, hazards).
    for src, what in (('#derive(Serialize)\nenum PlantedKind { PK_A; PK_B; }\n', "#derive(Serialize) enum"),
                      ('enum PlantedKind { PK_A; }\n', "a bare enum"),
                      ('  #derive(accessors)\nstruct S { a; }\n', "an indented #derive"),
                      ('#define SAMVADA_FLAG 1\n', "an object-like #define"),
                      ("fn f() { return '''; }\n", "a hazard")):
        check(bool(bundle_problems(src)), f"bundle_problems refuses {what}")
    check(not bundle_problems('# an enum-style constant, see #derive docs\nfn samvada_enum_x() { return 1; }\n'),
          "bundle_problems ignores comments and identifiers containing `enum`")

    # Arity counts parameters, not commas.
    for args, want in (("", 0), ("a", 1), ("a, b, c", 3), ("x: Result<i64, i64>", 1),
                       ("x = cf(1, 2), y", 2), ("m: Map<i64, Vec<i64, i64>>, z", 2), ("a,", 1)):
        check(arity(args) == want, f"arity({args!r}) == {want}")
    code = strip('private\npublic fn take(major: Result<i64, i64>) { return 0; }\n')
    check([(n, arity(a)) for _, n, a in fn_decls(code)] == [("take", 1)],
          "a generic parameter type is one parameter")

    # The marker must lead.
    check(private_first(strip('fn a() { }\nprivate\n')) is not None, "a decl before `private` is refused")
    check(private_first(strip('pub fn a() { }\nprivate\n')) is not None, "a `pub` decl before `private` is refused")
    check(private_first(strip('# header\nprivate\nfn a() { }\n')) is None, "a leading `private` passes")

    # C comments, and a `#define` kept as code.
    c = strip('/* execve( */ int x; // fork(\n#define RUN execv(\n', lang="c")
    check("execve" not in c and "fork" not in c, "C comments are blanked")
    check("execv(" in c, "a C `#define` line is code, not a comment")
    c = strip('int a; // c \\\nint hidden_by_splice;\nint b = exe\\\ncve(0);\n', lang="c")
    check("hidden_by_splice" not in c and "execve(0)" in c, "C splices are joined before lexing")
    check(c.count("\n") == 4, "splices keep the line count")
    c = strip('int a; // c\rint evil_cr = execve(0);\n', lang="c")
    check("execve(0)" in c, "a lone CR ends a C // comment, as in gcc")
    c = strip("long n = 1'000; int r = execve(0); long m = 2'000;\n", lang="c")
    check("execve(0)" in c, "C23 digit separators are not quotes")
    c = strip("switch (x) { case'a': break; } int r = execve(0);\n", lang="c")
    check("execve(0)" in c and "case' '" in c, "a char literal after an identifier is still a literal")
    c = strip('const char *p = R"x(a" fork( )x"; int r = execve(0);\n', lang="c")
    check("execve(0)" in c and "fork(" not in c, "a C raw string is read whole")
    c = strip("#include <it's.h>\nint r = execve(0);\n", lang="c")
    check("execve(0)" in c, "a header name is not a char literal")
    c = strip("char c = '\nint r = execve(0);\n", lang="c")
    check("execve(0)" in c, "an unterminated C literal ends at its line")
    c = strip("#include <sys/syscall.h>\n", lang="c")
    k = strip("#include <sys/syscall.h>\n", lang="c", keep_strings=True)
    check("syscall" not in c and "<sys/syscall.h>" in k, "a header name is blanked as code, kept with the strings")

    # Strings kept when asked, and line numbers preserved.
    s = strip('var p = "/etc/x"; # "/bin/y"\nfn a() { }\n', keep_strings=True)
    check('"/etc/x"' in s and "/bin/y" not in s, "keep_strings keeps literals, drops commented ones")
    check(s.count("\n") == 2, "newlines are preserved")

    print("\ncyr_scan self-test " + ("FAILED" if bad else "passed"))
    return bad


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(_self_test())
    print(__doc__.strip().splitlines()[-1])
    sys.exit(2)
