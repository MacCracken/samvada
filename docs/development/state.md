# samvada — Current State

> Refreshed every release. CLAUDE.md is preferences/process/procedures
> (durable); this file is **state** (volatile).

## Version

**0.1.0** — scaffolded 2026-04-30 via `cyrius init`. Baseline
commit; no protocol code yet.

## Toolchain

- **Cyrius pin**: `5.7.48` (in `cyrius.cyml [package].cyrius`)
- Local cyrius bin: `5.7.48` — pin and local match; toolchain
  is current as of the v0.2.0 development cycle.

## Source

- `src/main.cyr` — smoke entry point ("hello from samvada").
- `src/lib.cyr` — include chain (currently just samvada.cyr).
- `src/samvada.cyr` — public API surface.
  - `samvada_version()` → packed u32 of (major << 16) |
    (minor << 8) | patch. Placeholder; real API lands v0.2.0.
- `src/test.cyr` — top-level test entry referenced by
  `cyrius.cyml [build].test`.

## Tests

- `tests/samvada.tcyr` — primary suite (smoke + math + version-
  triple round-trip). Passes via `cyrius test`.
- `tests/samvada.bcyr` — benchmark stub (no-op).
- `tests/samvada.fcyr` — fuzz stub.

## Dependencies

Direct (declared in `cyrius.cyml`):

- stdlib — string, fmt, alloc, io, vec, str, syscalls, assert,
  tagged, fnptr.
  - `tagged` + `fnptr` added during scaffold for the v0.2.0
    Result-type + fn-table plumbing.

## Consumers

- **mabda** (planned) — v3.0 commits the public stub
  `gpu_surface_configure_native_logind`; v3.x will fill the
  body via samvada once v0.2.0 ships.

## Next

See [`roadmap.md`](roadmap.md). M1 (v0.2.0) is the libsystemd
C shim + minimum-viable logind subset; estimated 3–5 sessions.
