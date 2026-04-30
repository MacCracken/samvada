# samvada — Current State

> Refreshed every release. CLAUDE.md is preferences/process/procedures
> (durable); this file is **state** (volatile).

## Version

**0.2.1** — 2026-04-30. Polish patch on top of 0.2.0 — no
public API change, every addition is internal tooling, docs,
or test scaffolding. ADR-0001 + ADR-0002 filed. Real bench
harness replaces the no-op stub (4 CPU baselines: ffi_alloc
54ns, ffi_get_slot 9ns, init_reject_null 6ns,
release_idempotent 7ns). CI gate set hardened to yukti
parity. mabda is integrating against 0.2.0 — M1 closeout gate
unchanged.

**0.2.0** — 2026-04-30. C-shim FFI scaffold complete: fn-table
layout pinned, `deps/samvada_main.c` populating sd_bus wrappers,
`src/samvada.cyr` exposing the v0.x stable public surface (init /
take_device / release_device / pump_signals / release).
Live-bus end-to-end validation pending mabda's
`_backend_native_surface_configure_logind` consumer.

## Toolchain

- **Cyrius pin**: `5.7.48` (in `cyrius.cyml [package].cyrius`)
- Local cyrius bin: `5.7.48` — pin and local match; toolchain
  is current as of the v0.2.0 development cycle.

## Source

- `src/main.cyr` — smoke entry point ("hello from samvada").
- `src/lib.cyr` — include chain: samvada_ffi.cyr → samvada.cyr.
- `src/samvada_ffi.cyr` — fn-table layout (9 slots, 72 bytes,
  append-after-kind invariant) + alloc/get/set helpers.
- `src/samvada.cyr` — public API surface (v0.x stable).
  - `samvada_version()` → packed u32 (0.2.0).
  - `samvada_init(table)` → 0 | -err (opens bus, looks up session).
  - `samvada_session_take_device(major, minor)` → fd | -err.
  - `samvada_session_release_device(major, minor)` → 0 | -err.
  - `samvada_pump_signals()` → events | -err.
  - `samvada_release()` → 0 (idempotent).
  - `samvada_main(table)` → 0 | -err (C-shim entry point).
- `src/test.cyr` — top-level test entry referenced by
  `cyrius.cyml [build].test`.
- `deps/samvada_main.c` — libsystemd C shim. Not linked by
  `cyrius build`; consumers build it and link libsystemd.

## Tests

- `tests/samvada.tcyr` — 32 asserts across 8 groups: smoke,
  FFI slot-offset pin (freezes the C-shim contract), backend
  kinds, alloc/get/set round-trip, get_slot null-safety, init
  null-table rejection, init NULL-kind rejection, release
  idempotency, v0.2.0 version triple. All pass via `cyrius test`.
  Live sd_bus calls are HW-gated and not in this suite.
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

See [`roadmap.md`](roadmap.md). M1 (v0.2.0) shipped today as a
buildable scaffold; live-bus validation rolls into the next
session once a consumer (mabda) drives a real
`samvada_session_take_device` end-to-end. M2 generalization is
unscoped pending a second AGNOS consumer.
