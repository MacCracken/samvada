# samvada — Current State

> Refreshed every release. CLAUDE.md is preferences/process/procedures
> (durable); this file is **state** (volatile).

## Version

**0.2.2** — 2026-05-01. P(-1) hardening pass. One HIGH +
one MED correctness defect fixed (HIGH-1: `samvada_init`
double-init leak → `-EBUSY` reject + scratch reuse;
MED-1: `sb_take_device` stale-errno on `fd<0` → explicit
`-EBADF`, errno captured before sd-bus cleanup). Three new
docs land the v1.0 references: `docs/architecture/public-api.md`
(canonical surface map), `docs/sources.md` (consolidated
protocol citations), `docs/benchmarks.md` (perf-history seed,
two runs captured). Audit at `docs/audit/2026-05-01-hardening-review.md`
files the methodology. 38 tests (was 32; +6 in
`test_init_rejects_double_init`). M1 closeout gate unchanged
— mabda 3.0.0-rc.1 ships the consumer body but live-bus e2e
is rc.2-deferred.

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
- Local cyrius bin: `5.7.48` — pin and local match; carried
  forward from the v0.2.0 development cycle, no bump in 0.2.2.

## Source

- `src/main.cyr` — smoke entry point ("hello from samvada").
- `src/lib.cyr` — include chain: samvada_ffi.cyr → samvada.cyr.
- `src/samvada_ffi.cyr` — fn-table layout (9 slots, 72 bytes,
  append-after-kind invariant) + alloc/get/set helpers.
- `src/samvada.cyr` — public API surface (v0.x stable). Full
  surface map in `docs/architecture/public-api.md`.
  - `samvada_version()` → packed u32 (0.2.2).
  - `samvada_init(table)` → 0 | -err (opens bus, looks up
    session). Returns `-EBUSY` (`-16`) on re-init without
    release as of 0.2.2.
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

- `tests/samvada.tcyr` — 38 asserts across 9 groups: smoke,
  FFI slot-offset pin (freezes the C-shim contract), backend
  kinds, alloc/get/set round-trip, get_slot null-safety, init
  null-table rejection, init NULL-kind rejection, release
  idempotency, init double-init rejection (added 0.2.2 for
  HIGH-1), v0.2.2 version triple. All pass via `cyrius test`.
  Live sd_bus calls are HW-gated and not in this suite.
- `tests/samvada.bcyr` — 4 CPU baselines (`ffi_alloc`,
  `ffi_get_slot`, `init_reject_null`, `release_idempotent`).
  History tracked in `docs/benchmarks.md`.
- `tests/samvada.fcyr` — fuzz stub.

## Dependencies

Direct (declared in `cyrius.cyml`):

- stdlib — string, fmt, alloc, io, vec, str, syscalls, assert,
  tagged, fnptr.
  - `tagged` + `fnptr` added during scaffold for the v0.2.0
    Result-type + fn-table plumbing.

## Consumers

- **mabda** — `3.0.0-rc.1` (tagged 2026-04-30) ships the
  consumer body for `gpu_surface_configure_native_logind`
  (`src/surface_v3.cyr:102`) calling
  `samvada_session_take_device(226, 0)` with minor=1
  fallback and full fd-release on every failure path
  (mabda's audit HIGH-2 fix). Pinned via
  `[deps.samvada] tag = "0.2.0"` — bump to `0.2.2` is on
  mabda's `3.0.0-rc.2` punchlist. Live-bus end-to-end
  validation against a real desktop session is **not** on
  rc.2; mabda's own e2e (`programs/native_present_e2e.cyr`)
  stays on the kiosk path through 3.0.0.

## Next

See [`roadmap.md`](roadmap.md). M1 status unchanged —
code-complete, awaiting live-bus e2e through a desktop
session. mabda's rc.2 pulls 0.2.2 but does not schedule the
e2e validation. M2 generalization is unscoped pending a
second AGNOS consumer.

The v1.0 criteria checklist now has one item closed and one
seeded:
- ✅ Public API frozen — every exported symbol documented +
  tested (closed by `docs/architecture/public-api.md`).
- 🟡 Benchmarks captured in `docs/benchmarks.md` — seeded
  with two runs of CPU baselines; live-bus rows still
  pending.
- ⏳ Six-consumer regression sweep, downstream consumer
  green, architectural pivot decided, security audit pass —
  unchanged, gated on M1 closeout / mabda v4.0 design.
