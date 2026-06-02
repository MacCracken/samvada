# 0001 — v1.0 dbus backend pivot: pure-Cyrius marshaller vs removal

**Status**: Open — decision deferred to mabda v4.0 design
**Date**: 2026-06-02
**Feeds**: a future ADR-0003 (ratifies the chosen path)
**Supersedes nothing; extends**: [ADR-0001](../adr/0001-c-shim-then-pivot.md) §Decision (Path A.1 / A.2)

## Purpose

[ADR-0001](../adr/0001-c-shim-then-pivot.md) committed samvada
to a libsystemd C shim for v0.x and named the v1.0 exit as
*either* a pure-Cyrius dbus marshaller (**A.1**) *or* removal
(**A.2**), with the choice deferred to mabda v4.0 design. That
left the longest-pole v1.0 item — the pivot — completely
un-scoped: "~500–1000 LoC, multi-week" is a guess, not a plan.

This proposal turns A.1 into a concrete implementation plan with
a module map, a per-module LoC estimate grounded in the wire
format samvada already touches, a test strategy that works
**without** dbus hardware, and a risk register — and scopes A.2
(removal) enough to compare. It does **not** pick a path; it
makes the eventual decision a short one.

It is deliberately exhaustive on A.1 because A.1 is the
expensive, uncertain branch. A.2 is cheap and well-understood;
the risk lives entirely in A.1.

## Decision driver (what actually picks A.1 vs A.2)

The pivot is **not** a samvada-internal preference — it is
downstream-demand-driven. One question decides it, answered
during mabda v4.0 design:

> At mabda v4.0 ship, does any AGNOS consumer still need logind
> `TakeDevice`-style DRM-master delegation over dbus?

- **Yes** → **A.1**. samvada must keep working, and the C shim
  must retire (the whole point of v1.0), so the impl below the
  frozen 5-fn API is re-implemented in pure Cyrius.
- **No** (kernel-level master delegation lands, or AGNOS
  standardizes on a different session-management primitive, or
  mabda's compositor path absorbs it) → **A.2**. samvada is
  deprecated; consumers' `gpu_surface_configure_native_logind`
  paths are revised in mabda v4.0 to call the new mechanism
  directly.

The `project_phase_d_master_logind_blocker` (mabda) is the
live evidence that the logind path is currently load-bearing —
which today tilts toward A.1. But that can change before v4.0,
which is exactly why the decision waits.

## Scope is fixed by what v0.x already shipped

A.1 does **not** re-implement dbus. It re-implements the exact
slice samvada's frozen API exercises — no more. From
[`dbus-marshalling.md`](../architecture/dbus-marshalling.md):

- **Transport**: system bus only (unix socket at
  `/run/dbus/system_bus_socket`). No session bus.
- **Auth**: SASL `EXTERNAL` (uid credential) + `NEGOTIATE_UNIX_FD`
  + `Hello`. That's the whole connect handshake.
- **Calls**: exactly 3 method calls — `GetSessionByPID` (`u`→`o`),
  `TakeDevice` (`uu`→`hb`), `ReleaseDevice` (`uu`→ ∅).
- **Signals**: 2 subscriptions — `PauseDevice` (`uus`),
  `ResumeDevice` (`uuh`).
- **Types needed**: `u` (uint32), `o`/`s` (string), `g`
  (signature), `b` (boolean-as-uint32), `h` (unix_fd via
  `SCM_RIGHTS`). **Not** needed: `y n q i x t d a (...) v` —
  defer until a consumer needs them.
- **Explicitly deferred** (ADR-0001 §Neutral, confirmed by the
  marshalling doc §"does NOT touch"): Properties.Get/Set/GetAll,
  Introspectable, session bus, generic `dbus_call_method`. None
  are on the v1.0 critical path.

This bounded scope is what makes A.1 a few hundred LoC rather
than a general dbus library.

## A.1 — pure-Cyrius marshaller: module map + LoC estimate

The C shim today is one ~300-line file calling 9 `sd_bus_*`
entry points (`default_system`, `call_method`, `message_read`,
`message_unref`, `match_signal`, `process`, `slot_unref`,
`unref`, `error_free`). The replacement keeps the **same fn-table
shape** (the 8 slots in `samvada_ffi.cyr` + `kind`), only now
the table is populated by Cyrius fns with `kind = PURE_CYRIUS`
(`2`) instead of by C with `kind = LIBSYSTEMD` (`1`). The public
API and the FFI offsets do not move — ADR-0002's append-after-kind
invariant guarantees it.

| Module (proposed) | Responsibility | Est. LoC | Confidence |
|---|---|---|---|
| `src/dbus_socket.cyr` | connect `AF_UNIX` to the system bus socket; blocking read/write helpers | 60–90 | High — `sys_socket`/`sys_connect` already in stdlib |
| `src/dbus_auth.cyr` | SASL `EXTERNAL` line protocol: NUL byte, `AUTH EXTERNAL <hex(ascii(uid))>`, `NEGOTIATE_UNIX_FD`, `BEGIN` | 70–110 | High — tiny line-based ASCII protocol |
| `src/dbus_marshal.cyr` | encode header + fields array + body for `u`/`s`/`o`/`g`/`b`; alignment + padding | 150–230 | Medium — alignment is fiddly but fully specified |
| `src/dbus_unmarshal.cyr` | decode `METHOD_RETURN` / `ERROR` / signal: read fields, correlate `REPLY_SERIAL`, pop body values | 130–200 | Medium — endian + variable-length reads |
| `src/dbus_fd.cyr` | `recvmsg(2)` + `cmsghdr`/`SCM_RIGHTS` walk; `dup` the popped fd before buffer reuse | 80–130 | **Low** — `cmsg(3)` macro layout is the riskiest piece |
| `src/dbus_session.cyr` | the 3 logind calls + 2 signal subs wired to the public fns; serial counter; signal dispatch loop (replaces `sd_bus_process`) | 120–180 | Medium |
| FFI populator (replace shim) | build the table with the above as slots; `kind = PURE_CYRIUS`; delete `deps/samvada_main.c` | 40–70 | High |
| **Total** | | **~650–1010** | matches ADR-0001's 500–1000 guess |

Notes on the estimate:

- The stdlib already ships the socket syscalls samvada needs
  (`sys_socket`, `sys_connect`, `sys_recvmsg`, `sys_recvfrom`,
  `sys_setsockopt`, plus `lib/net.cyr`), so `dbus_socket.cyr`
  is thin. Sends use plain `write()` — no `sendmsg` needed,
  because samvada never *sends* an fd (the only `h` is on the
  `TakeDevice` *reply*, received).
- `dbus_fd.cyr` is the genuine risk. `cmsg(3)` alignment
  (`CMSG_ALIGN`/`CMSG_SPACE`/`CMSG_DATA`) has no Cyrius helper;
  it must be hand-rolled against the kernel ABI. This is where
  a spike pays off (see below).
- All wrappers stay ≤6 args (the `fncall6` constraint) — the
  fn-table dispatch is unchanged, so consumers and the FFI
  tests don't move.

## A.1 — test strategy (most of it needs **no** dbus)

The reason A.1 is tractable to verify is that the marshaller is
a pure byte transform — it can be unit-tested against captured
wire bytes on CI, with no bus:

1. **Codec round-trips (CPU, CI-green).** Encode each call
   (`GetSessionByPID`/`TakeDevice`/`ReleaseDevice`) and assert
   the bytes match a golden buffer captured from libsystemd
   (`busctl --verbose` / a `recvmsg` capture). Decode the
   `TakeDevice` reply from the captured `METHOD_RETURN` bytes in
   [`dbus-marshalling.md` §walk-through](../architecture/dbus-marshalling.md#takedevice-reply-walk-through)
   and assert `(fd_index, active)` parse correctly. Alignment
   bugs surface here, deterministically.
2. **SASL handshake over a socketpair (CPU, CI-green).** Drive
   `dbus_auth.cyr` against a scripted fake server on the other
   end of `sys_socketpair` — assert the exact `AUTH EXTERNAL …`
   / `NEGOTIATE_UNIX_FD` / `BEGIN` byte sequence.
3. **`SCM_RIGHTS` over a socketpair (CPU, CI-green).** Send a
   real fd through a `socketpair` with a hand-built `cmsghdr`,
   receive it via `dbus_fd.cyr`, assert the received fd refers
   to the same file (`fstat` dev/ino match). This exercises the
   riskiest module **without** logind.
4. **Live e2e (HW-gated).** The existing
   [`tests/samvada_live.bcyr`](../../tests/samvada_live.bcyr)
   scaffold already drives the 3 paths through whichever backend
   the table carries — point it at a `kind = PURE_CYRIUS` table
   and it measures the marshaller end-to-end on real hardware,
   same as it would the C shim. No new harness needed.

Items 1–3 mean the bulk of A.1 lands behind deterministic CI
gates; only the final integration needs the desktop session
that already gates M1.

## A.1 — risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `cmsg`/`SCM_RIGHTS` layout wrong → fd lost or garbage | Med | High | Spike it first (below); socketpair test #3 catches it pre-logind |
| Header-fields alignment off-by-N → bus drops message | Med | Med | Golden-byte codec test #1; libsystemd rejects locally too, easy to diff |
| Endianness / variable-length read bugs in unmarshal | Med | Med | Round-trip tests; pin LE explicitly (sd-bus writes LE) |
| Serial correlation races on out-of-order replies | Low | Med | logind serializes per session; still correlate by `REPLY_SERIAL` not arrival |
| Multi-week effort overruns the v4.0 window | Med | High | Bounded scope (above) + spike de-risks the unknowns before committing |
| New `syscall(...)` surface trips the CI security scan / audit | High (expected) | Low | All are socket/`recvmsg`/`dup` — benign; document in the v1.0 audit (`SECURITY.md` gates it to v1.0 anyway) |

## A.1 — recommended de-risking spike

Before committing the full multi-week build, a **~1 session
spike** collapses most of the uncertainty: connect + SASL
`EXTERNAL` + `Hello` + one `GetSessionByPID` round-trip, printing
the returned session path. It touches socket, auth, the simplest
marshal (`u`) and unmarshal (`o`) — but **not** fd passing. If
that talks to a real bus, the only remaining unknown is
`dbus_fd.cyr`, which test #3 isolates. The spike result feeds the
A.1-vs-A.2 decision with real evidence instead of an estimate.

## A.2 — removal: scope

If the driver question answers "no":

- Delete `deps/samvada_main.c`, `src/samvada_ffi.cyr`,
  `src/samvada.cyr`'s dbus body; keep `samvada_version()` (or
  archive the repo with a deprecation notice).
- mabda v4.0 revises `gpu_surface_configure_native_logind` to
  call the replacement mechanism directly.
- A final `0.x` → archived/`1.0.0`-deprecation release documents
  the removal and points consumers at the new path.
- Effort: trivial (hours), mostly CHANGELOG + a migration note.

A.2's only cost is downstream migration of mabda's one call
site — small, because the public surface is 5 fns.

## Why this is clean either way

ADR-0001's structural bets hold under both exits:

- **5-fn frozen API** — A.1 keeps it byte-for-byte (only the
  impl below swaps); A.2 removes it with one documented consumer
  migration.
- **One C shim + one FFI module** isolates the impl — A.1
  replaces the shim with Cyrius modules behind the same table;
  A.2 deletes both.
- **Append-after-kind invariant (ADR-0002)** — A.1's
  `kind = PURE_CYRIUS` table is forward-compatible with v0
  callers by construction.

## Open questions (for mabda v4.0 design)

1. Does any consumer need logind dbus at v4.0 ship? *(picks
   A.1 vs A.2)*
2. If A.1: does the spike talk to a real bus in one session? *(go/no-go
   on the full build)*
3. If A.1: do any M2 surface additions (Properties, session bus)
   land before v1.0, enlarging the re-implementation? *(scope
   creep guard — ADR-0001 §Neutral)*
4. If A.2: is samvada archived, or kept as a version-probe stub
   for consumers that still `[deps.samvada]`-pin it?

## References

- [ADR-0001 — C-shim-then-pivot](../adr/0001-c-shim-then-pivot.md)
- [ADR-0002 — append-after-kind FFI invariant](../adr/0002-append-after-kind-ffi-invariant.md)
- [`dbus-marshalling.md`](../architecture/dbus-marshalling.md) — the wire-format slice A.1 re-implements
- [`public-api.md`](../architecture/public-api.md) — the frozen surface both paths preserve/retire
- [`sources.md`](../sources.md) — protocol citations [S-1..S-16]
- [`roadmap.md` §M3](../development/roadmap.md) — the pivot milestone
- [`tests/samvada_live.bcyr`](../../tests/samvada_live.bcyr) — the live-bus harness both backends share
