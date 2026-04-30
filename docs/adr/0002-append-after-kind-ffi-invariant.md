# 0002 — Append-after-kind FFI table invariant

**Status**: Accepted
**Date**: 2026-04-30

## Context

samvada's C shim (`deps/samvada_main.c`) hands the Cyrius side
a function-pointer table. Cyrius reads it via `load64(table +
offset)` and dispatches with `fncallN`. The slot offsets are
hard-coded on both sides: the C side as `#define
SLOT_TAKE_DEVICE 16` etc., the Cyrius side as
`fn samvada_slot_take_device() { return 16; }`. A test pin in
`tests/samvada.tcyr` asserts both agree.

When v0.3+ adds new slots (likely candidates: Properties.Get,
generic method-call dispatch, session-bus support), the table
grows. Two layout strategies were on the table:

- **Strategy A — pack tightly, accept renumbering.** New slots
  go wherever they fit. If we want logical grouping (all
  signal-related slots together, all property-related slots
  together), we sometimes have to insert in the middle and
  bump every slot after the insertion point.
- **Strategy B — append after a fixed sentinel.** Pin an
  identifier slot (`kind`) at a fixed offset forever. Every
  new slot appends after the existing tail; no slot ever moves.

mabda's `Backend` struct uses Strategy B with `kind` at offset
80 (per the `phase_b5_backend_abstraction` field note). samvada
mirrors the pattern with `kind` at offset 64 (smaller table,
fewer slots in v0.2.0).

The strategic question: **does v0.x → v1.0 backend swap
preserve slot offsets, or is the pure-Cyrius marshaller free
to renumber?**

## Decision

samvada's FFI table follows the **append-after-kind invariant**:

1. `samvada_slot_kind` returns `64` and stays at offset 64
   **forever** — across every minor and major version, including
   the v1.0 backend swap.
2. Every new slot lands at offset `72 + 8*K` (the existing tail).
   `K` = current new-slot index (0-based after `samvada_ffi_size`'s
   current value).
3. `samvada_ffi_size()` is the only fn that bumps when a slot
   is added.
4. No slot is ever **removed** (the offset stays even if the
   underlying fn becomes a no-op stub) and no slot is ever
   **renumbered**.

The kind word's role: a v0 caller scanning a v(N+1)-shaped
table never misreads a fnptr as the kind word, because kind is
at the same offset in both. A v1.0 backend (pure-Cyrius,
post-pivot) populates the same offsets the C shim populates
today; only the implementation underneath changes.

## Consequences

### Positive

- v0.x → v1.0 transition is binary-compatible at the table
  level. mabda's `_backend_native_surface_configure_logind`
  doesn't see the swap — it calls the same Cyrius API, which
  dispatches through the same offsets.
- Adding a slot is a 5-line change: one new `samvada_slot_*`
  fn, one new C wrapper, one new test pin, one bump to
  `samvada_ffi_size`, one append to `samvada_main.c`'s table
  population. No risk of breaking an existing dispatcher.
- The test pin (`tests/samvada.tcyr` `test_ffi_slot_offsets`)
  freezes the contract at CPU-test time. Any reorder fails the
  test before the binary runs.
- Backward compat survives an unbounded number of minor bumps —
  v0.2 → v0.3 → ... → v0.99 all keep kind at +64.

### Negative

- The table grows monotonically. A slot that proves wrong-
  shaped (say, a wrapper signature changes) can't be replaced
  in place; it has to be deprecated (kept as a stub) and a new
  slot appended. Acceptable trade-off: the table is small and
  growth is bounded by what dbus exposes.
- Logical grouping is lost. New slots land at the tail rather
  than next to related ones. Mitigation: a comment block at
  the top of `src/samvada_ffi.cyr` documents groupings even
  when offsets don't reflect them.

### Neutral

- The pattern doesn't preclude *adding* a kind-like sentinel
  for a future v2 layout. If samvada ever needs a wholly
  different table (e.g. async dispatch), a new sentinel at a
  different fixed offset can co-exist with the v1 one.
- Consumers don't see slot offsets — the public API
  (`samvada_session_take_device` etc.) hides them. The
  invariant is internal; consumers couldn't depend on offsets
  even if they wanted to.

## Alternatives considered

- **Strategy A (pack tightly, accept renumbering)** — rejected.
  The cost of every renumber is silent: every C-shim
  `#define` has to update, every Cyrius `samvada_slot_*` fn
  has to update, and any consumer that built against the old
  table sees fnptrs misroute. Even with the test pin catching
  the latter, the former two have to stay in lockstep across
  every minor bump.
- **Single-sentinel-at-offset-0** — rejected. Putting `kind`
  at offset 0 means slot 0 is the kind word, not a fn. The C
  shim populates kind in `main()` rather than as a wrapper, so
  having kind at the start would just shift the same problem
  one byte; "kind at a fixed offset" is the load-bearing
  constraint, and 64 (post-MVP-fns) is as good as 0.
- **Version-tagged tables (kind = struct version)** — partially
  adopted. `kind` already encodes which backend
  (`SAMVADA_BACKEND_KIND_LIBSYSTEMD = 1`,
  `SAMVADA_BACKEND_KIND_PURE_CYRIUS = 2`); we don't separately
  encode "table version". A future need for that would add a
  new sentinel slot, not repurpose `kind`.
- **Symbol table / dynamic dispatch** — rejected. Cyrius has
  no string-keyed dispatch; everything reduces to a fnptr load
  + `fncallN`. The fixed-offset table IS the dispatch
  mechanism. No alternative within Cyrius's primitives.

## References

- [`src/samvada_ffi.cyr`](../../src/samvada_ffi.cyr) — slot
  offset fns + `samvada_ffi_size`.
- [`tests/samvada.tcyr`](../../tests/samvada.tcyr)
  `test_ffi_slot_offsets` — the CPU-test pin.
- [`deps/samvada_main.c`](../../deps/samvada_main.c) — `#define
  SLOT_*` constants that mirror the Cyrius side.
- vidya field note `phase_b5_backend_abstraction` — mabda's
  Backend struct, the parent pattern (kind at offset 80, same
  invariant).
- ADR-0001 — C-shim-then-pivot — the v1.0 retirement that
  this invariant makes safe.
