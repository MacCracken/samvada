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
mabda's Phase D surface present path; v1.0 retires the
libsystemd C shim alongside mabda's wgpu-native retirement,
replaced by a pure-Cyrius dbus marshaller or removed entirely
if AGNOS standardizes on a different session-management
primitive.

## Current State

> Volatile state lives in [`docs/development/state.md`](docs/development/state.md) —
> current version, source layout, test counts, in-flight slots,
> consumers, dep gaps. Refreshed every release.
>
> Roadmap to v1.0 (milestones, gates, criteria):
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
CYRIUS_DCE=1 cyrius build ...               # release build (CI default)

# Consumer build (with C shim + libsystemd):
cc -c deps/samvada_main.c \
   $(pkg-config --cflags libsystemd) \
   -o build/samvada_main.o
cyrius build src/main.cyr build/samvada.o --emit-object
cc build/samvada_main.o build/samvada.o \
   $(pkg-config --libs libsystemd) \
   -o build/myapp
```

## Key Principles

- **Correctness over cleverness** — if it's wrong, the bugs own you
- Test after every change, not after the feature is "done"
- ONE change at a time — never bundle unrelated changes
- Research before implementation — check vidya field notes / sibling repos for existing patterns
- Build with `cyrius build`, never raw `cat file | cc5` — the manifest auto-resolves deps and prepends includes
- Source files only need project includes — stdlib auto-resolves from `cyrius.cyml`
- Every buffer declaration is a contract: `var buf[N]` = N **bytes**, not N entries
- **Append-after-kind invariant** — `samvada_slot_kind` stays at offset 64 forever; new slots append after the existing tail so v0 callers reading a v(N+1) table never misread a fnptr as the kind word
- **C wrappers stay ≤6 args** so dispatch is `fncall6`-safe across x86_64 SysV + aarch64 (cyrius's 6-arg fncall convention)
- **Fd-passing is dup-safe** — every fd from `SCM_RIGHTS` gets `dup`'d before the dbus message is unref'd, so the consumer's fd lifetime is independent of dbus message lifetime
- **Errors are negative sd-bus errnos** at the FFI boundary so the C convention passes through unchanged; consumers branch on `< 0`
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
- Do not link libsystemd from samvada itself — it lives at the consumer's edge so the v1.0 retirement is a clean swap

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
6. **Documentation** — update CHANGELOG, `docs/development/state.md`, any ADR the change earned
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

The first audit pass is gated to v1.0 — see `SECURITY.md`
"Audit History" for the rationale (C-shim era is pre-audit
because the surface is intentionally transitional).

### Closeout Pass (before every minor/major bump)

Run as the last patch of the current minor (e.g. `0.2.5` before
`0.3.0`).

1. **Full test suite** — `cyrius test` zero failures
2. **Benchmark baseline** — `cyrius bench tests/samvada.bcyr` (when populated; bench harness is currently a no-op)
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

- All struct fields are 8 bytes (`i64`), accessed via `load64` / `store64` with offset (Cyrius has no struct types — offset-table-on-heap is canonical)
- Heap allocation via `alloc()` for long-lived data (FFI tables, scratch buffers); `alloc_init()` must run before any `alloc()`
- Enum-style constants via fns (`samvada_slot_*()` returning literals) — don't consume `gvar_toks` slots (256 initialized globals limit per compilation unit)
- `var buf[N]` allocates N **bytes**, not entries — and inside fns it's STATIC data, not stack (consecutive calls share + clobber); use `alloc(N)` for per-call scratch
- `var X;` (uninitialized) is rejected — always `var X = 0;`
- Top-level `var X = expr` is evaluated in source order; forward refs silently resolve to 0 (gotcha; fix slotted for cyrius 5.7.32)
- `var name[N]` requires N to be an integer literal; identifier-as-size is rejected at parse
- No comparisons in fn args (`==`, `!=`, `<`, `>` all rejected) — hoist to a `var`
- `>>` is **logical**, not arithmetic — sign-extend manually for negative values
- `^` (xor) binds tighter than binary `-` — parenthesise mixed expressions
- No mixed `&&` / `||` in one expression — nest `if` blocks instead
- `match` is reserved — don't use as a variable name
- `return;` without value is invalid — always `return 0;`
- All `var` declarations are function-scoped — no block scoping
- `break` in while loops with `var` declarations is unreliable — use flag + `continue`
- No negative literals — write `(0 - N)` not `-N`
- Max limits per compilation unit: 4,096 variables, 1,024 functions, 256 initialized globals
- `println(s)` is single-arg + newline; `print(s, len)` is two-arg explicit-length — there is **no** `print(s)` single-arg form
- `print_num(n)` (string.cyr) and `fmt_int(n)` (fmt.cyr) print integers — interchangeable

## CI / Release

- **Toolchain pin**: `cyrius = "X.Y.Z"` in `cyrius.cyml [package]`. **No separate `.cyrius-toolchain` file.** CI and release both read this; no hardcoded version strings in YAML.
- **Tag filter**: release accepts both `v[0-9]+.[0-9]+.[0-9]+` and `[0-9]+.[0-9]+.[0-9]+` tag styles.
- **Version-verify gate**: release asserts `VERSION == git tag` (with optional `v` prefix stripped) before building. Mismatch fails the run.
- **CI gate**: release runs `ci.yml` via `workflow_call` first; release work only proceeds if all CI jobs pass.
- **Workflow layout**:
  - `.github/workflows/ci.yml` — toolchain install (`curl -sfLO` fail-fast), `cyrius deps` (with `rm -rf lib && mkdir lib`), lint (warnings → failures), `fmt --check` (drift → failure), `vet`, `distlib` freshness, build, ELF magic verify, smoke run, `cyrius test`, separate jobs for C-shim compile-check (`-Wall -Wextra -Werror` + link-test against `-lsystemd`), security scan (raw execve / fork / sys_system / writes-to-{etc,bin,sbin} / large stack buffers), docs check (required files + version-in-CHANGELOG)
  - `.github/workflows/release.yml` — version gate → CI gate → build → `distlib` regen → artifact bundle (src tarball + `dist/samvada.cyr` + smoke binary + C shim source + `SHA256SUMS`) → CHANGELOG-extracted release body
- **Concurrency**: CI uses `cancel-in-progress: true` keyed on workflow + ref — only the latest push is tested.
- **Prerelease gate**: 0.x tags ship as GitHub prereleases (surface gates aren't met yet — no live-bus e2e through a consumer).
- **State sync**: release post-hook bumps `docs/development/state.md`. If the hook doesn't, fix the hook — don't hand-maintain state.

## Documentation

Per [first-party-documentation.md](https://github.com/MacCracken/agnosticos/blob/main/docs/development/applications/first-party-documentation.md):

- [`docs/adr/`](docs/adr/) — Architecture Decision Records (*why X over Y?*) — created when earned
- [`docs/architecture/`](docs/architecture/) — non-obvious constraints (*what's true about the code?*) — `dbus-marshalling.md` documents the wire-format slice samvada touches
- [`docs/guides/`](docs/guides/) — task-oriented how-tos — `consumer-link.md` for the two-stage build
- [`docs/examples/`](docs/examples/) — runnable examples
- [`docs/development/state.md`](docs/development/state.md) — live state snapshot, refreshed every release
- [`docs/development/roadmap.md`](docs/development/roadmap.md) — milestones through v1.0
- [`docs/audit/`](docs/audit/) — security audit reports (`YYYY-MM-DD-audit.md`) — populated when the v1.0 audit pass runs

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
    roadmap.md     - milestones through v1.0
    state.md       - live state snapshot (volatile; release-hook-bumped)

docs/ (when earned):
  audit/           - security audit reports (YYYY-MM-DD-audit.md)
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

**Note**: `dist/samvada.cyr` is **tracked** so `[deps.samvada]`
consumers fetch the bundled API surface directly via the
release tag. Do **not** add `/dist/` to `.gitignore`.

## CHANGELOG Format

Follow [Keep a Changelog](https://keepachangelog.com/).

- **Performance** claims must include benchmark numbers (`X ns ± Y` or comparable).
- **Breaking** changes get a `### Breaking` section with a migration guide.
- **Security** fixes get a `### Security` section with CVE references where applicable.
- Each `## [VERSION] — YYYY-MM-DD` heading is what the release workflow's `awk` extractor walks; do not vary the punctuation.

See [first-party-documentation § CHANGELOG](https://github.com/MacCracken/agnosticos/blob/main/docs/development/applications/first-party-documentation.md#changelog) for the full conventions.
