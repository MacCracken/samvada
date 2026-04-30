# 0001 — C-shim-then-pivot for v0.x dbus integration

**Status**: Accepted
**Date**: 2026-04-30

## Context

samvada needs to talk to logind (`org.freedesktop.login1`) over
the system dbus so AGNOS Cyrius applications can receive a DRM
master fd via `TakeDevice`. Two implementations were on the
table at scaffold time:

- **Path A — pure-Cyrius dbus from day one.** Hand-roll the
  marshaller in Cyrius: SASL EXTERNAL auth, header-fields
  array, type-sig encoder/decoder, `recvmsg(2)` + `SCM_RIGHTS`
  cmsg loop, signal listener. Estimated 500–1000 LoC of
  byte-level protocol plumbing.
- **Path B — libsystemd C shim under a Cyrius API.** A
  ~300-line C file (`deps/samvada_main.c`) wrapping `sd_bus_*`
  primitives, presented to Cyrius as a function-pointer table
  via the `fncallN` pattern. Cyrius wrappers above expose a
  small public surface (5 fns).

The first consumer (mabda's `_backend_native_surface_configure_logind`)
was already on a tight roadmap. Spending 3–5 sessions on
protocol marshalling before a single byte flowed to logind
risked stalling mabda's Phase D.

The same trade-off existed in mabda's wgpu-native integration:
mabda v2.0 ships a C shim wrapping wgpu, with the public API
frozen above the shim, and a planned-but-unscoped pivot to a
pure-Cyrius GPU backend at v4.0. The `wgpu_main.c` /
`wgpu_ffi.cyr` pattern is documented in the vidya field note
`ship_now_swap_backend_later_pattern`.

## Decision

samvada v0.x ships **Path B** — a libsystemd C shim presenting
a fn-table to Cyrius, with the public API (`samvada_init`,
`samvada_session_take_device`, `samvada_session_release_device`,
`samvada_pump_signals`, `samvada_release`) frozen above the
shim. v1.0 retires the C shim by either:

- **Path A.1** — replacing it with a pure-Cyrius dbus
  marshaller (~500–1000 LoC, multi-week), or
- **Path A.2** — removing samvada entirely if AGNOS evolves
  to a kernel-level master-delegation path or a different
  session-management primitive.

The decision between A.1 and A.2 happens during mabda v4.0
design. samvada's v0.x line is structured so either exit is
clean — the public API is small (5 fns), the implementation
is isolated in one C shim + one Cyrius FFI module, and the
slot table is forward-compatible (see ADR-0002).

## Consequences

### Positive

- Mabda's Phase D unblocks now. The C shim ships in 1 session;
  the pure-Cyrius marshaller would have taken 3–5+ sessions
  with no consumer touching real bytes during that time.
- libsystemd is the de-facto dbus client on every distro AGNOS
  targets — using it means we get RFC-compliant marshalling,
  SASL auth, and SCM_RIGHTS handling for free, audited by
  thousands of upstream consumers.
- The 5-fn public API is simple enough that any v1.0 backend
  swap doesn't risk surface drift. Consumer code keeps working
  unchanged.
- Tests can pin slot offsets at the FFI table level, catching
  any C-shim-vs-Cyrius mismatch at CPU-test time rather than
  via runtime crash inside libsystemd.

### Negative

- v0.x consumers must `apt install libsystemd-dev` (or the
  distro equivalent) and link `-lsystemd`. A documented
  two-stage build (`docs/guides/consumer-link.md`) makes this
  explicit, but it's still extra work for downstream.
- "Temporary" scaffolding has a way of becoming permanent. The
  longer the libsystemd dep lives, the more institutional
  knowledge accumulates around it, the harder the v1.0 pivot
  becomes. Mitigations:
  - `# @internal` markers on every FFI module + C shim file.
  - Architectural strategy section in README + roadmap §M3
    keeps the retirement on the horizon.
  - The append-after-kind invariant (ADR-0002) ensures forward
    compat across the swap.
- samvada's v0.x is Linux-only. dbus on Linux speaks
  `sd_bus`; macOS / Windows would need different transports.
  Acceptable — AGNOS is a Linux desktop OS.

### Neutral

- Tests in `tests/samvada.tcyr` cover the structural contract
  + null-safety paths only; live `sd_bus` calls are HW-gated
  and exercised through downstream consumers (mabda) rather
  than by samvada's own CI. v0.3+ may add a probe-then-skip
  test group; not blocking for v0.2.0.
- The 5-fn surface is the v0.x stability contract. Adding
  surface (Properties, generic dispatch, session bus) is M2
  scope; doing so doesn't violate this ADR but does enlarge
  what the v1.0 pivot has to re-implement in pure Cyrius.

## Alternatives considered

- **Path A (pure-Cyrius from v0.1)** — rejected as scaffolded.
  Initially the v0.1 plan was pure-Cyrius from day one; revised
  same-day after recognizing the wgpu-native parallel made the
  C shim the obviously-better stop-gap. Recorded in
  `docs/development/roadmap.md` § Notes / decisions
  (2026-04-30 entry).
- **Vendoring libdbus instead of libsystemd** — rejected.
  libdbus is the older client library; modern AGNOS-target
  distros (systemd-based) ship `sd_bus` as the canonical
  client. Pulling in libdbus would mean two transitively
  different dbus client implementations on every consumer
  machine.
- **Shelling out to `busctl`** — rejected. `busctl` is a CLI
  wrapper around `sd_bus`; using it adds a process-spawn
  boundary, breaks fd passing (DRM master can't cross
  `execve`), and embeds shell quoting risks. Strictly worse
  than the library binding.
- **Wait for an in-tree pure-Cyrius dbus implementation in
  another AGNOS repo** — rejected. No such effort exists, and
  spinning one up in parallel competes for the same
  multi-week time budget as Path A.

## References

- [`docs/development/roadmap.md`](../development/roadmap.md) §M3
  — pivot path detail.
- [`docs/architecture/dbus-marshalling.md`](../architecture/dbus-marshalling.md)
  — wire-format slice the v1.0 pure-Cyrius marshaller would
  re-implement.
- vidya field note `ship_now_swap_backend_later_pattern`
  (mabda's wgpu-native shape; same architectural posture).
- mabda's `deps/wgpu_main.c` + `src/wgpu_ffi.cyr` — the
  C-shim-fn-table pattern samvada mirrors.
