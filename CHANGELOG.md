# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
