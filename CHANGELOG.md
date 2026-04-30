# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
