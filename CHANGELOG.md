# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.2.2] — 2026-05-01

P(-1) hardening pass on top of 0.2.1. One HIGH and one MEDIUM
correctness defect fixed, three new docs file the v1.0
preparatory references, audit doc lands the methodology used.
No public API change — `samvada_version()` packed triple bumps
`(0,2,2)` and the new `-EBUSY` (`-16`) return code is an
*addition* to the negative-errno catalog, not a behavior change
on any existing path.

### Added
- `docs/architecture/public-api.md` — canonical map of every
  exported symbol: signature, pre/post-conditions, full
  error-code table per fn, test-coverage map, and the v0.x
  stability contract. Closes one of the six open v1.0 criteria
  in `docs/development/roadmap.md` ("public API frozen — every
  exported symbol documented + tested").
- `docs/sources.md` — consolidated protocol citations [S-1]
  through [S-16]: D-Bus spec, logind interface,
  `sd_bus_*(3)` man pages, `unix(7)` /
  `recvmsg(2)` / `cmsg(3)` / `dup(2)` for fd-passing, DRM
  major number, errno header refs, cyrius `lib/fnptr.cyr` /
  `lib/syscalls_x86_64_linux.cyr`. The v1.0 pure-Cyrius
  marshaller has every reference in one place.
- `docs/benchmarks.md` — perf-history seed. Run 1 captured at
  commit `4c7ada9` against samvada 0.2.1: `ffi_alloc` 56 ns,
  `ffi_get_slot` 9 ns, `init_reject_null` 6 ns,
  `release_idempotent` 6 ns. Method, variance, and graduation
  criteria documented inline; each release appends a column.
  Live-bus rows (handshake latency, `TakeDevice` round-trip,
  signal-pump throughput) are the v1.0 gate work.
- `docs/audit/2026-05-01-hardening-review.md` — internal
  hardening review (NOT the formal v1.0 audit, which
  `SECURITY.md` §"Audit History" gates). Methodology, four
  findings (1 HIGH / 1 MED / 2 LOW), disposition for each, and
  scope notes on what the audit explicitly does *not* cover
  (live-bus behavior, signal callback safety, multi-thread,
  bus-pump DoS).
- `tests/samvada.tcyr` — new `test_init_rejects_double_init`
  group (6 asserts) pinning HIGH-1's fix: re-init without
  release returns `-EBUSY`; release clears the guard.

### Fixed
- **HIGH-1** — `samvada_init` is now double-init safe.
  Previously a second `samvada_init` without an intervening
  `samvada_release` overwrote `_samvada_table` / `_samvada_bus` /
  `_samvada_sess` / `_samvada_outs` without releasing the
  previous values, leaking the dbus connection + scratch bytes
  on every retry. Now: returns `-16` (`-EBUSY`) when
  `_samvada_table != 0`. The legitimate re-init-after-release
  path reuses the already-allocated scratch buffers (they
  survive `samvada_release` per the bump-allocator contract)
  rather than re-allocating, which would leak under any
  allocator that doesn't bump-pack identically.
- **MEDIUM-1** — `sb_take_device` now returns `-EBADF`
  explicitly when the message read succeeded but delivered a
  negative fd, instead of returning `-errno` from a syscall
  that was never made. Also captures `errno` *before*
  `sd_bus_message_unref` / `sd_bus_error_free` so a future
  libsystemd that touches errno on cleanup paths cannot clobber
  the dup-failure code. Reachability is narrow (libsystemd's
  `sd_bus_message_read("hb")` is documented to return `fd >= 0`
  on success), but the C-side defensive contract was wrong.

### Changed
- `CLAUDE.md` work-loop step 2 now points at
  `docs/benchmarks.md` for per-release row appends, replacing
  the placeholder note that history "arrives at the v1.0 gate."

### Notes
- The new `-EBUSY` return code joins the error catalog in
  `docs/architecture/public-api.md`. Consumers that ignored
  unknown negative returns (the documented "branch on `< 0`,
  log, abort" pattern) need no migration. Consumers that
  retried `samvada_init` on failure now hit a deterministic
  reject instead of silently leaking.
- The C-shim recompile passed strict-flag rebuild
  (`-Wall -Wextra -Werror -Wshadow -Wconversion -Wcast-qual
  -Wformat=2`) clean against libsystemd 260; the audit's
  posture check did not require code changes.

## [0.2.1] — 2026-04-30

Polish patch on top of 0.2.0. No public API change — every
addition is internal tooling, docs, or test scaffolding.
samvada_version() bumps `(0,2,1)` so consumer version-gates
can pin the exact tooling generation.

### Added
- `docs/adr/0001-c-shim-then-pivot.md` — formalizes the v0.x
  libsystemd-C-shim choice and the v1.0 retirement plan
  (pure-Cyrius marshaller or removal). Borrowed from vidya's
  `ship_now_swap_backend_later_pattern` field note; mirrors
  mabda's wgpu-native pattern.
- `docs/adr/0002-append-after-kind-ffi-invariant.md` —
  documents why `samvada_slot_kind` sits at offset 64 forever.
  Load-bearing for v0→v1 forward-compat across the C-shim
  retirement.
- `docs/adr/README.md` — index updated with both ADRs.
- `tests/samvada.bcyr` — real bench harness replacing the
  no-op `bench("noop", ...)` stub. Four measurements (CPU-only,
  no dbus): `ffi_alloc` (54 ns avg), `ffi_get_slot` (9 ns avg —
  the dispatch hot path), `init_reject_null` (6 ns avg),
  `release_idempotent` (7 ns avg). Numbers are
  developer-machine baseline; `docs/benchmarks.md` will
  capture proper history once v1.0's bench-history.sh shape
  lands.
- `CONTRIBUTING.md` — dev workflow, local gate sweep, FFI-slot
  addition recipe. Was missing from 0.2.0; the docs CI gate
  now requires it.
- `SECURITY.md` — threat model, supported versions, response
  timeline, banned-pattern policy enforced by CI.

### Changed
- `.github/workflows/ci.yml` — full rewrite to match yukti's
  quality bar. Toolchain step uses `curl -sfLO` (fail-fast on
  404) instead of silent download. Adds lint, fmt-check, vet,
  distlib-freshness, ELF magic, smoke-output regex check,
  bench harness smoke run, C shim compile + link test against
  `libsystemd-dev`, security scan (raw execve / fork /
  sys_system / writes-to-{etc,bin,sbin} / large stack
  buffers), docs check (required files + version-in-CHANGELOG).
  Smoke gate matches `ffi slots = N (N*8 bytes)` as a regex
  with cross-checked arithmetic so adding slots in v0.3+
  doesn't silently break.
- `.github/workflows/release.yml` — full rewrite. Accepts
  both `vX.Y.Z` and `X.Y.Z` tag styles. Gates on CI via
  `workflow_call`. Ships 5 artifacts (src tarball,
  `dist/samvada.cyr` renamed, smoke binary, C shim source,
  SHA256SUMS). Extracts the matching `## [VERSION]` block from
  CHANGELOG.md as the release body. 0.x tags ship as
  prereleases.
- `CLAUDE.md` — rewritten to align with agnosticos's
  `example_claude.md` template. Adds P(-1) scaffold-hardening,
  Closeout Pass, full Cyrius Conventions block (18 language
  gotchas), CI/Release section, Documentation Structure,
  reference `.gitignore`, CHANGELOG format. Volatile state
  delegated to `docs/development/state.md`.
- `.gitignore` — regrouped + commented to match the
  first-party template. Adds `cyrius-*.tar.gz`, `SHA256SUMS`,
  `Thumbs.db`. Explicit note that `dist/` stays tracked.
- `src/samvada.cyr` + `tests/samvada.tcyr` — applied
  `cyrius fmt`; minimum-indent on continuation lines (was
  aligned-with-paren). Behavior unchanged.

### Notes
- The three issues that landed during 0.2.0 work (stale dist
  bundle, missing CONTRIBUTING/SECURITY, fmt drift) are now
  hard CI gates; the original workflow would have caught none
  of them.
- mabda's e2e integration against `0.2.0` is the M1 closeout
  gate — `0.2.1` polish does not move M1's status.
- Bench numbers are developer-machine baseline (single laptop,
  not pinned hardware); CI runs `cyrius bench` as a smoke for
  harness wiring but does not gate on the numbers.

## [0.2.0] — 2026-04-30

### Added
- `src/samvada_ffi.cyr` — FFI fn-table layout (9 slots, 72 bytes,
  kind pinned at offset 64 forever per the append-after-kind
  invariant). Slot offsets exposed as fns to dodge the
  global-init-order silent-zero gotcha.
- `deps/samvada_main.c` — libsystemd C-shim entry point.
  `_cyrius_init` + `alloc_init` preamble, then populates the
  fn-table with `sd_bus_*`-backed wrappers and calls
  `samvada_main(table)`. Compiles clean under
  `cc -Wall -Wextra -Werror` against libsystemd 260. Coverage:
  open_system_bus, get_session_path (login1 GetSessionByPID),
  take_device (TakeDevice + dup'd fd), release_device,
  pump_signals (sd_bus_process loop), close_bus,
  subscribe_pause_resume (sd_bus_match_signal), unsubscribe.
- `src/samvada.cyr` graduated from placeholder to real public
  surface: `samvada_init` / `samvada_session_take_device` /
  `samvada_session_release_device` / `samvada_pump_signals` /
  `samvada_release` / `samvada_main`. All errors are negative
  sd-bus errnos so the C convention passes through unchanged.
- 7 new test groups (32 asserts) in `tests/samvada.tcyr`:
  slot-offset pin (freezes the C-shim contract), backend-kind
  pins, alloc/get/set round-trip, null-safety, init rejection
  paths, release idempotency, v0.2.0 version triple.
- `samvada_version()` bumped 0.1.0 -> 0.2.0 packed triple.

### Notes
- Live `sd_bus` calls are NOT exercised by `cyrius test` — they
  need a running system dbus + logind. CPU coverage is the
  structural contract + null-safety paths. The end-to-end
  validation gate is mabda's
  `_backend_native_surface_configure_logind` once that fills.
- samvada itself does not link libsystemd — the consumer builds
  `deps/samvada_main.c` and links libsystemd. See
  `docs/guides/consumer-link.md` for the build recipe.

## [0.1.0] — 2026-04-30

### Added
- Initial project scaffold via `cyrius init samvada`.
- Project identity + roadmap to v1.0 documented in `README.md`
  + `docs/development/roadmap.md`.
- `src/lib.cyr` include chain stub.
- `src/samvada.cyr` API surface placeholder — `samvada_version()`
  returns the 0.1.0 version triple as a u32 (signals the package
  shape exists; real API lands in v0.2.0).
- `cyrius.cyml` populated with description, repository,
  `${file:VERSION}` substitution, expanded stdlib deps for the
  v0.2.0 implementation work (`tagged` for Result types, `fnptr`
  for the fn-table dispatch pattern).
- `tests/samvada.tcyr` extended with a version-triple round-trip
  assertion so the smoke gate exercises the placeholder fn.

### Notes
- No protocol code, no C shim, no real dbus calls. v0.1.0 is the
  baseline-of-work commit so consumers (mabda v3.0) can already
  reference `[deps.samvada]` against a known package shape.
- Architectural strategy committed in `README.md`: C-shim era
  through v0.x mirroring mabda's wgpu-native pattern; both C-shim
  deps retire together at samvada v1.0 / mabda v4.0.
