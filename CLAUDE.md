# samvada — Claude Code Instructions

> **Core rule**: this file is **preferences, process, and procedures** —
> durable rules that change rarely. Volatile state (current
> version, slot count, source layout, in-flight work, consumers,
> dep gaps) lives in [`docs/development/state.md`](docs/development/state.md),
> bumped every release. Do not inline state here — inlined state
> rots within a minor.
>
> **Template source**: [agnosticos `example_claude.md`](https://github.com/MacCracken/agnosticos/blob/main/docs/development/applications/example_claude.md).
> **Documentation care**: [first-party-standards](https://github.com/MacCracken/agnosticos/blob/main/docs/development/applications/first-party-standards.md) +
> [first-party-documentation](https://github.com/MacCracken/agnosticos/blob/main/docs/development/applications/first-party-documentation.md).

## Project Identity

**samvada** (Sanskrit *saṃvāda* — dialogue) — Cyrius dbus client for the AGNOS suite.

- **Type**: Binary + library bundle (consumers link `dist/samvada.cyr`)
- **License**: GPL-3.0-only
- **Language**: Cyrius (toolchain pinned in `cyrius.cyml [package].cyrius`)
- **Version**: `VERSION` at the project root is the source of truth — do not inline the number here
- **Genesis repo**: [agnosticos](https://github.com/MacCracken/agnosticos)
- **Standards**: [First-Party Standards](https://github.com/MacCracken/agnosticos/blob/main/docs/development/applications/first-party-standards.md) ·
  [First-Party Documentation](https://github.com/MacCracken/agnosticos/blob/main/docs/development/applications/first-party-documentation.md)
- **Reference siblings**: [yukti](https://github.com/MacCracken/yukti) (CI/release gold standard),
  [mabda](https://github.com/MacCracken/mabda) (the C-shim-then-pivot pattern samvada mirrors)

## Goal

Own the dbus IPC layer for AGNOS Cyrius applications. v0.x scope
is the minimum-viable logind subset (`TakeDevice` /
`ReleaseDevice` / `PauseDevice` / `ResumeDevice`) needed by
mabda's Phase D surface present path. Since v1.0 a pure-Cyrius
dbus marshaller is the default backend (ADR-0003); the libsystemd
C shim remains only as a fallback until the roadmap retires it, and
removing samvada's dbus layer (A.2, which ADR-0003 retains) stays
the contingency if AGNOS standardizes on a different
session-management primitive.

## Current State

> Volatile state lives in [`docs/development/state.md`](docs/development/state.md) —
> current version, source layout, test counts, in-flight slots,
> consumers, dep gaps. Refreshed every release.
>
> Roadmap — what is left (the next release, its gate, open items):
> [`docs/development/roadmap.md`](docs/development/roadmap.md).

This file (`CLAUDE.md`) is durable rules.

## Scaffolding

Project was scaffolded with `cyrius init samvada` (greenfield).
**Do not manually create project structure** — use the tools.
If a tool is missing something, fix the tool.

## Quick Start

```sh
# Standalone (no C shim, no libsystemd):
cyrius deps                                 # resolve stdlib
cyrius build src/main.cyr build/samvada     # smoke build
cyrius test                                 # auto-discovers tests/*.tcyr
cyrius distlib                              # regenerate dist/samvada.cyr
cyrius lint src/*.cyr                       # static checks
cyrius vet src/main.cyr                     # dep audit (file arg required)
CYRIUS_DCE=1 cyrius build ...               # release build (release.yml sets it; ci.yml's build does not)

# Consumer build (with C shim + libsystemd) — see docs/guides/consumer-link.md
# for the verified recipe. Three things that are NOT obvious:
#   1. the shim does not define main() (0.5.1+); the consumer calls
#      samvada_shim_init() from its own main(). -DSAMVADA_STANDALONE_MAIN
#      brings the shim's own main() back, for probe binaries only.
#   2. there is NO `--emit-object` flag. `cyrius build` emits a finished
#      executable; a relocatable needs the `object;` directive through cycc.
#      This is the ONE sanctioned direct-cycc invocation.
#   3. `objcopy -L` on the Cyrius object is needed ONLY on a cyrius pin below
#      6.6.2; from 6.6.2 `object;` builds emit the libc-reserved names
#      vis=HIDDEN (consumer-link.md step 3 has the check and the command).
cc -c deps/samvada_main.c \
   $(pkg-config --cflags libsystemd) \
   -o build/samvada_main.o
{ printf 'object;\n'; \
  for m in syscalls string fmt alloc io vec str assert tagged fnptr samvada; do \
      echo "include \"lib/$m.cyr\""; done; \
  cat src/app.cyr; } | cycc > build/app.o
cc -c src/launch.c -o build/launch.o        # your main(), consumer-link.md step 4
cc build/samvada_main.o build/app.o build/launch.o \
   $(pkg-config --libs libsystemd) \
   -o build/myapp
```

## Key Principles

- **Correctness over cleverness** — if it's wrong, the bugs own you
- Test after every change, not after the feature is "done"
- ONE change at a time — never bundle unrelated changes
- Research before implementation — check vidya field notes / sibling repos for existing patterns
- Build with `cyrius build`, never raw `cat file | cycc` — the manifest auto-resolves deps and prepends includes. **One exception**: emitting a relocatable `.o` for the consumer link, which has no `cyrius build` equivalent (there is no `--emit-object` flag) and requires the `object;` directive piped through `cycc` (plus `objcopy -L` symbol localization on a cyrius pin below 6.6.2). That path is documented in `docs/guides/consumer-link.md` and nowhere else
- Source files only need project includes — stdlib auto-resolves from `cyrius.cyml`
- Every buffer declaration is a contract: `var buf[N]` = N **bytes**, not N entries
- **Append-after-kind invariant** — `samvada_slot_kind` stays at offset 64 forever; new slots append after the existing tail so v0 callers reading a v(N+1) table never misread a fnptr as the kind word
- **C wrappers stay ≤6 args** so dispatch is `fncall6`-safe across x86_64 SysV + aarch64 (cyrius's 6-arg fncall convention)
- **A handed-out fd's lifetime is independent of the message** — the shim duplicates with `F_DUPFD_CLOEXEC` (minimum 3) before the dbus message is unref'd; the native backend receives with `MSG_CMSG_CLOEXEC`, moves anything on 0..2 above stdio, and claims the descriptor out of its message's queue. No descriptor is ever handed out on 0..2
- **Errors are negative sd-bus errnos** at the FFI boundary so the C convention passes through unchanged; consumers branch on `< 0`. The native backend's error-name map **is** sd-bus's table: `tests/fixtures/sdbus_errno_map.tsv` is `tools/sdbus_errno_probe.c`'s output from libsystemd, and CI re-checks it — change the map only by regenerating the fixture, never by hand-picking an errno
- **The consumer bundle is private-by-default** — `src/visibility.cyr` leads `[lib] modules`, so a consumer can name only `public fn`s. A new public fn needs `public fn` **and** a `tools/public_surface.txt` row (both gates fail otherwise); never put the marker anywhere but first
- **A descriptor belongs to the message whose bytes it arrived with** — every read is bounded to the head message (`dbus_frame_want` / `dbus_frame_prepare`, via `_dbs_recv`), and `dbus_frame_advance` closes every descriptor that message did not hand out. Never read past the head message, never consume one without `dbus_frame_advance`, and never attribute a descriptor by parsing the message (that is how 1.1.2's HIGH happened). `take_device` claims by the reply's `h` index and checks, by `statx`, that the descriptor **is** the device asked for
- **The security scan allow-lists, it does not pattern-match** (1.1.2) — a new `syscall(` number or `SYS_*` name needs a row in `tools/security_scan.py`'s allow-list, a new stdlib module a row in `tools/stdlib_allowed.txt` (and modules that fork, exec, load code or map executable memory are refused by name), and `#define`, `enum` and `#derive` are refused in the bundle's sources. Grow the list in review; never loosen a check to make a finding go away
- **No `alloc()` on a repeating path** — `alloc()` never frees and the consumer runs for days; scratch is allocated once and reused. The heap tests pin zero growth per call; a new call path gets one
- The C shim lives at the consumer's edge — samvada itself does not link libsystemd

## Rules (Hard Constraints)

- **Read the genesis repo's CLAUDE.md first** — [agnosticos/CLAUDE.md](https://github.com/MacCracken/agnosticos/blob/main/CLAUDE.md)
- **Do not commit or push** — the user handles all git operations
- **NEVER use `gh` CLI** — use `curl` to the GitHub API if needed
- Do not skip tests before claiming changes work
- Do not skip the local gate sweep (`lint`, `fmt --check`, `vet`, `distlib`, `build`, `test`) before pushing
- Do not modify `lib/` files (vendored stdlib / dep symlinks; populated by `cyrius deps`)
- Do not use `sys_system()` / raw execve / raw fork — samvada has no business spawning processes; CI rejects these patterns
- Do not write to `/etc/`, `/bin/`, `/sbin/` from samvada code (CI rejects string literals matching those prefixes)
- Do not trust external data (file content, network input, args) without validation
- Do not hardcode toolchain versions in CI YAML — `cyrius = "X.Y.Z"` in `cyrius.cyml` is the source of truth
- Do not reorder slots in `src/samvada_ffi.cyr` ahead of `samvada_slot_kind` — the C shim hard-codes offsets and the test pin freezes them
- Do not expose FFI types in public function signatures — consumers must never see a fn-table pointer as a parameter (samvada handles it internally)
- Do not link libsystemd from samvada itself — it lives at the consumer's edge so the shim's retirement is a clean swap

## Process

### P(-1): Scaffold / Project Hardening (before any new features)

1. **Cleanliness** — `cyrius lint`, `cyrius fmt --check`, `cyrius vet`, `cyrius distlib` clean; all tests pass
2. **C-shim compile-check** — `cc -Wall -Wextra -Werror -c deps/samvada_main.c` passes
3. **Internal review** — gaps, optimizations, correctness, edge cases (null-table, NULL-kind, double-init, double-release)
4. **External research** — vidya field notes, sibling repo patterns (mabda's `wgpu_main.c` shape, yukti's CI gates)
5. **Security audit** — input handling, syscall usage, buffer sizes, fd-passing dup safety. File findings in `docs/audit/YYYY-MM-DD-audit.md`
6. **Additional tests** from findings — slot-offset pins, null-safety, idempotency
7. **Documentation audit** — ADRs for any choices made during hardening, source citations for protocol bytes
8. **Repeat if heavy** — keep drilling until clean

### Work Loop (continuous)

1. **Work phase** — features, roadmap items, bug fixes
2. **Build check** — `cyrius build src/main.cyr build/samvada`
3. **Test + benchmark additions** for new code
4. **Internal review** — performance, memory, correctness, edge cases
5. **Security check** — any new C wrapper, any new external input path, any new fd path
6. **Documentation** — update CHANGELOG, `docs/development/state.md` (`python3 tools/state_sync.py` regenerates its derived facts), any ADR the change earned
7. **Version sync** — `VERSION`, `cyrius.cyml`, CHANGELOG header
8. **Return to step 1**

### Security Hardening (before every release)

Per [first-party-standards § Security Hardening](https://github.com/MacCracken/agnosticos/blob/main/docs/development/applications/first-party-standards.md#security-hardening-new--required-before-every-release):

1. **Input validation** — every fn accepting consumer-supplied data validates bounds, types, ranges (major/minor/pid)
2. **Buffer safety** — every `var buf[N]` verified; N is **bytes**, max access < N, no adjacent-variable overflow
3. **Syscall review** — every `syscall(...)` validated: args checked, returns handled, error paths complete
4. **Pointer validation** — no raw deref of consumer-supplied or libsystemd-returned pointers without bounds
5. **No command injection** — samvada doesn't exec; CI rejects `sys_system` / raw execve / raw fork
6. **No path traversal** — file paths from external input validated; samvada doesn't open filesystem paths in v0.x
7. **Known CVE review** — check libsystemd / dbus broker / logind CVEs against current versions
8. **Document findings** — `docs/audit/YYYY-MM-DD-audit.md`

Security audits have run since 0.5.1 (a hardening review at 0.2.2) — see `SECURITY.md`
"Audit History", which supersedes the earlier rule that gated the
first pass to v1.0.

### Closeout Pass (before every minor/major bump)

Run as the last patch of the current minor (e.g. `0.2.5` before
`0.3.0`).

1. **Full test suite** — `cyrius test` zero failures
2. **Benchmark baseline** — `cyrius bench tests/samvada.bcyr` (4 CPU baselines wired in v0.2.1: ffi_alloc, ffi_get_slot, init_reject_null, release_idempotent). Append a row to [`docs/benchmarks.md`](docs/benchmarks.md) on each release; the live-bus rows (handshake, signal pump) have been filled since 0.11.0 / 1.0.0 (the `TakeDevice` row waits on a seated session), and receive-path changes are measured with `tools/rxbench.cyr`.
3. **Dead code audit** — remove unused fns; record remaining floor in CHANGELOG
4. **Refactor pass** — consolidate the minor's additions where parallel paths accreted
5. **Code review pass** — walk diffs end-to-end for missed guards, ABI leaks, off-by-ones, silently-ignored errors
6. **Cleanup sweep** — stale comments, dead `#ifdef` branches, unused includes, orphaned files
7. **Security re-scan** — quick grep for new `sys_system`, unchecked writes, unsanitized input, buffer mismatches
8. **Downstream check** — mabda still builds and passes against the new samvada version
9. **Doc sync** — CHANGELOG, roadmap, `docs/development/state.md`, this CLAUDE.md if durable rules changed
10. **Version verify** — `VERSION`, `cyrius.cyml`, CHANGELOG header, intended git tag all match
11. **Full build from clean** — `rm -rf build && cyrius deps && CYRIUS_DCE=1 cyrius build` passes clean

### Task Sizing

- **Low/Medium effort**: batch freely — multiple items per work loop cycle
- **Large effort**: small bites only — break into sub-tasks, verify each before moving to the next
- **If unsure**: treat it as large

### Refactoring Policy

- Refactor when the code tells you to — duplication, unclear boundaries, measured bottlenecks
- Never refactor speculatively. Wait for the third instance
- Every refactor must pass the same test gates as new code
- 3 failed attempts = defer and document — don't burn time in a rabbit hole

## Cyrius Conventions

- All struct fields are 8 bytes (`i64`), accessed via `load64` / `store64` with offset (Cyrius has struct types now; samvada uses none — offset-table-on-heap is canonical here)
- Heap allocation via `alloc()` for long-lived data (FFI tables, scratch buffers); `alloc_init()` must run before any `alloc()`
- Enum-style constants via fns (`samvada_slot_*()` returning literals) — the bundle may hold no `enum`: its members would land in the consumer's scope, which `private` does not cover (`tools/surface_check.py` refuses one)
- `var buf[N]` allocates N **bytes**, not entries — and inside fns it's STATIC data, not stack (consecutive calls share + clobber); use `alloc(N)` for per-call scratch
- `var X;` (uninitialized) is rejected — always `var X = 0;`
- Top-level `var X = expr` is evaluated in source order; a forward ref to a var whose initializer is not a constant silently reads 0 (gotcha; `cyrius lint` warns on a forward ref since cyrius 5.7.32, and CI fails on the warning)
- `var name[N]` requires N to be a constant — an integer literal, a `const`, an enum constant, `sizeof` or a const fn call; a `var` as the size is rejected
- Comparisons in fn args (`==`, `!=`, `<`, `>`) are accepted — the old restriction was found stale in cyrius 5.8.46
- `>>` is **logical**, not arithmetic — sign-extend manually for negative values
- `^` (xor) and binary `-` share one precedence level, grouped left to right (`8 - 3 ^ 1` is 4, `5 ^ 3 - 1` is 5) — parenthesise mixed expressions
- Mixed `&&` / `||` in one expression compiles with C precedence (`&&` binds tighter)
- `match` is reserved — don't use as a variable name
- A bare `return;` compiles in a plain fn (it returns 0) but is refused in a `: bool` fn and in one returning a two-register struct or a vector — write `return 0;`
- `var` declarations are block-scoped: one declared inside an `if` / loop body is undefined after it, and an inner same-named `var` shadows the outer
- `break` in while loops with `var` declarations is unreliable — use flag + `continue`
- Negative literals compile (`var a = -5;`, `f(-3)`, and the sources' own `return -22;`); the older `(0 - N)` spelling is equivalent
- Per-unit variable and function limits are what `cyrius capacity` reports for the pinned toolchain (far above samvada's use); initialized globals have had no separate cap since cyrius 6.6.9
- Initializer rule: a top-level `var` whose initializer folds to a nonzero integer constant (`var x = 42;`, `var m = 1 << 4;`) is baked into the image; every other one (a call or other non-constant expression, `= 0`, a string literal) is a deferred initializer, run once at startup in declaration order, with no cap on how many. See the cyrius guide's **Global Initializers** section (`docs/guides/cyrius-guide.md` in the cyrius repo)
- Continuation lines inside a call get **2 spaces per open paren** (4 is also accepted) — `cyrfmt` enforces this from the 6.6.x line on, and a continuation wrapped at the statement indent is drift
- `println(s)` is single-arg + newline; `print(s, len)` is two-arg explicit-length — there is **no** `print(s)` single-arg form
- `print_num(n)` (string.cyr) and `fmt_int(n)` (fmt.cyr) print integers — interchangeable
- **Never write a native aarch64 syscall number** under `#ifdef CYRIUS_ARCH_AARCH64` (or `#ifndef CYRIUS_ARCH_X86`, `#ifplat aarch64`, an arch `#else`) — ELF-aarch64 builds rewrite x86 numbers through a compat chain that matches a *number*, so a native one is a future silent wrong call. Use the stdlib's `sys_*` / `SYS_*`; where none exists, write the x86 number (`clock_gettime` = `228`, as `lib/chrono.cyr` does). `1000 + N` aliases exist only for natives a row already claims. CI's security job rejects any aarch64 region in `src/` / `tests/`

## CI / Release

- **Toolchain pin**: `cyrius = "X.Y.Z"` in `cyrius.cyml [package]`. **No separate `.cyrius-toolchain` file.** CI and release both read this; no hardcoded version strings in YAML.
- **Tag filter**: release accepts both `v[0-9]+.[0-9]+.[0-9]+` and `[0-9]+.[0-9]+.[0-9]+` tag styles.
- **Version-verify gate**: release asserts `VERSION == git tag` (with optional `v` prefix stripped) before building. Mismatch fails the run.
- **CI gate**: release runs `ci.yml` via `workflow_call` first; release work only proceeds if all CI jobs pass.
- **Workflow layout**:
  - `.github/workflows/ci.yml` — four jobs. **Build & Test**: toolchain install (installer script and toolchain tarball each checked against a SHA-256 pinned in the workflow), `cyrius deps` (with `rm -rf lib && mkdir lib`), lint (warnings → failures), `fmt --check` (drift → failure), `vet`, `distlib` freshness, the public-surface gate (`tools/surface_check.py`), the boundary gate (`tools/boundary_check.py`), the bundle's empty `nm -u`, build, ELF magic verify, smoke run (version triple == `VERSION`), `cyrius test` (plus the errno-map row count), the state-snapshot check (`tools/state_sync.py`: self-test, `--check` as a warning, then a planted stale row that must be reported), bench and the live-bench skip-path smoke. **C Shim Compile-Check**: strict-flag compile in library and standalone modes with the `tools/shim_symbols.txt` symbol gate and the no-syscall-instruction gate, the consumer-owned-`main()` link test against `-lsystemd`, `tools/shim_slot_check.c` and its mutants, the golden-corpus decode, the sd-bus errno map, the C-vs-Cyrius slot-offset cross-check. **Security Scan**: `tools/security_scan.py --self-test` then the scan (allow-listed syscalls, spawners, system paths, ≥64 KB static buffers, the frozen stdlib list) and the no-aarch64-region grep. **Documentation**: required files + version-in-CHANGELOG
  - `.github/workflows/release.yml` — CI gate (`ci.yml` via `workflow_call`) → version gate → `CYRIUS_DCE=1` build → `cyrius test` → `distlib` regen, refused unless byte-identical to the tag's `dist/` → `tools/state_sync.py --check`, refused if `state.md` is stale → artifact bundle (src tarball + `samvada-<tag>.cyr` + `samvada-<tag>.deps` + smoke binary + C shim source + `SHA256SUMS`) → CHANGELOG-extracted release body
- **Concurrency**: CI uses `cancel-in-progress: true` keyed on workflow + ref — only the latest push is tested.
- **Prerelease gate**: 0.x tags ship as GitHub prereleases (surface gates aren't met yet — no live-bus e2e through a consumer).
- **State sync**: `tools/state_sync.py` is the hook. It regenerates the facts in `docs/development/state.md` that can be read off the tree (version, release date, pins, runners, actions, fn and slot counts, stdlib leaves, every suite's asserts) into `<!-- state-sync:begin/end NAME -->` blocks — never hand-edit inside one, except a suite's Covers cell in the tests table, which is hand-written, kept, and never carries a count. The prose around them is written by hand and acknowledged per release with `<!-- state-sync:narrative X.Y.Z -->`, which only a person sets. ci.yml warns on a stale snapshot; release.yml refuses one. It runs before the tag, not after: `release.yml` writes nothing back to the repo, and a commit after the tag would leave the tag's own state.md stale. If the tool can't derive a fact you need, teach it — don't hand-type a number it generates, here or in README / CONTRIBUTING

## Documentation

Per [first-party-documentation.md](https://github.com/MacCracken/agnosticos/blob/main/docs/development/applications/first-party-documentation.md):

- [`docs/adr/`](docs/adr/) — Architecture Decision Records (*why X over Y?*) — created when earned
- [`docs/architecture/`](docs/architecture/) — non-obvious constraints (*what's true about the code?*) — `dbus-marshalling.md` documents the wire-format slice samvada touches
- [`docs/guides/`](docs/guides/) — task-oriented how-tos — `consumer-link.md` for consumer integration (the native one-dependency path, and the retiring C-shim two-stage build)
- [`docs/examples/`](docs/examples/) — runnable examples (empty: holds only `.gitkeep`; the executed recipes live in `docs/guides/consumer-link.md`)
- [`docs/development/state.md`](docs/development/state.md) — live state snapshot, refreshed every release
- [`docs/development/roadmap.md`](docs/development/roadmap.md) — what is left past the current release (forward-facing; shipped milestones are a one-line index)
- [`docs/audit/`](docs/audit/) — security audit and hardening-review reports (`YYYY-MM-DD-*.md`), indexed in `SECURITY.md` "Audit History"

New quirks land in `docs/architecture/` as numbered items
(`NNN-kebab-case.md`). New decisions land in `docs/adr/` using
the project's ADR template. **Never renumber either series.**

## Documentation Structure

```
Root files (required):
  README.md, CHANGELOG.md, CLAUDE.md, CONTRIBUTING.md,
  SECURITY.md, LICENSE, VERSION, cyrius.cyml

docs/ (minimum):
  adr/             - architectural decision records (when earned)
  architecture/    - non-obvious invariants (NNN-*.md)
  guides/          - task-oriented how-tos
  examples/        - runnable examples
  development/
    roadmap.md     - what is left (forward-facing)
    state.md       - live state snapshot (derived facts generated by tools/state_sync.py)

docs/ (when earned):
  audit/           - security audit and hardening-review reports (YYYY-MM-DD-*.md)
  benchmarks.md    - perf history (v1.0 gate)
  proposals/       - pre-ADR design drafts
  sources.md       - protocol / spec citations
```

## .gitignore

```gitignore
# Build
/build/

# Resolved deps (auto-generated by cyrius deps)
/lib/

# Python bytecode (tools/*.py import each other)
__pycache__/

# Release / toolchain artifacts
cyrius-*.tar.gz
SHA256SUMS

# Crash dumps
*.core

# IDE
.claude/
.idea/
.vscode/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db

# Secrets
.env
.env.*
*.pem
*.key
```

**Note**: everything `cyrius distlib` writes into `dist/` is
**tracked** — `dist/samvada.cyr` (the bundled API surface) and
`dist/samvada.deps` (the stdlib-leaf sidecar the 6.6.x distlib
emits alongside it, which `cyrius deps` consumes downstream) —
so `[deps.samvada]` consumers fetch both directly via the
release tag. Do **not** add `/dist/` to `.gitignore`. Siblings
follow the same rule (yukti ships `dist/yukti.deps`, mabda
`dist/mabda.deps`).

## CHANGELOG Format

Follow [Keep a Changelog](https://keepachangelog.com/).

- **Performance** claims must include benchmark numbers (`X ns ± Y` or comparable).
- **Breaking** changes get a `### Breaking` section with a migration guide.
- **Security** fixes get a `### Security` section with CVE references where applicable.
- Each `## [VERSION] — YYYY-MM-DD` heading is what the release workflow's `awk` extractor walks; do not vary the punctuation.

See [first-party-documentation § CHANGELOG](https://github.com/MacCracken/agnosticos/blob/main/docs/development/applications/first-party-documentation.md#changelog) for the full conventions.
