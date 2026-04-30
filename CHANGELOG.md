# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
