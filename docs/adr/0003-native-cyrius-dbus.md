# 0003 — Native Cyrius dbus is the v1.0 path

**Status**: Accepted
**Date**: 2026-09-09
**Decides**: [ADR-0001](0001-c-shim-then-pivot.md) §Decision — the
deferred choice between Path A.1 and Path A.2
**Ratifies**: [proposal 0001](../proposals/0001-v1-dbus-backend-pivot.md),
which scoped A.1 but deliberately did not pick it

## Context

[ADR-0001](0001-c-shim-then-pivot.md) committed samvada's v0.x
line to a libsystemd C shim and named two possible v1.0 exits:

- **A.1** — replace the shim with a pure-Cyrius dbus marshaller.
- **A.2** — remove samvada entirely, if AGNOS moved to a
  kernel-level master-delegation path or a different
  session-management primitive.

The choice was deferred to "mabda v4.0 design", on the reasoning
that the pivot is downstream-demand-driven: it depends on whether
any AGNOS consumer still needs logind `TakeDevice`-style DRM-master
delegation when v4.0 ships.

[Proposal 0001](../proposals/0001-v1-dbus-backend-pivot.md) then
turned A.1 into a concrete plan — a seven-module map, per-module
LoC estimates with confidence levels, a test strategy that works
without dbus hardware, a risk register, and a recommended
one-session de-risking spike — while explicitly declining to pick
a path. Its stated purpose was to *"make the eventual decision a
short one"*.

That deferral has cost more than it saved. Six documents defer to
"the decision", so none of them can be finished. The roadmap's
longest-pole item has no schedule. And the deferral is a large
part of why the 2026-09-09 audit found what it found: the C shim
was treated as disposable scaffolding not worth investing in, so
it was never exercised against a real bus, and
`samvada_session_take_device()` shipped broken for five releases
(audit CRIT-1). "We'll decide later" became "we'll look at it
later", which became "nobody looked at it".

## Decision

**samvada v1.0 is native dbus in Cyrius. Path A.1 is adopted;
A.2 is retired as the plan of record.**

The libsystemd C shim is a transitional backend that will be
deleted at the 1.0.0 tag. Everything below the frozen public API
is re-implemented in Cyrius: socket transport, SASL `EXTERNAL`
auth, message marshalling and unmarshalling, `SCM_RIGHTS` fd
passing, and the logind session layer.

Three things follow directly, and are binding:

1. **The public API stays frozen.** `samvada_init`,
   `samvada_session_take_device`,
   `samvada_session_release_device`, `samvada_pump_signals`,
   `samvada_release`, `samvada_main` and `samvada_version` keep
   their signatures and error-code contracts across the pivot.
   Consumer code compiled against 0.5.1 runs against 1.0.0.
2. **The FFI table shape is preserved.** The native backend
   populates the *same* slot offsets the shim populates today,
   with `kind = PURE_CYRIUS (2)` instead of `LIBSYSTEMD (1)`.
   [ADR-0002](0002-append-after-kind-ffi-invariant.md)'s
   append-after-kind invariant continues to hold: `kind` stays at
   +64 forever and slots only ever append.
3. **The two backends coexist during the transition.** The shim
   is kept compiling, linking and passing CI until the native
   backend reaches parity. It is the reference implementation to
   differentially test against, and the fallback if the
   marshaller stalls. It is deleted at 1.0.0, not before.

### Why now, and why not "wait for mabda v4.0"

The decision driver in proposal 0001 was: *does any AGNOS
consumer still need logind dbus at v4.0 ship?* Two things settle
it in favour of A.1 without waiting.

**The logind path is load-bearing today.** mabda's Phase D
surface-present path calls
`samvada_session_take_device(226, N)` and is blocked on a
hardware gate, not on a design question. Nothing in AGNOS has
replaced it, and no alternative primitive is proposed anywhere.

**Deferral has a demonstrated cost.** A backend nobody has
committed to is a backend nobody tests. The audit is the
evidence. Choosing removes that failure mode: a backend we intend
to ship gets exercised.

If the premise later collapses — AGNOS standardizes on something
else, or kernel-level delegation lands — A.2 remains available and
cheap, because the surface is five functions with one consumer.
That is a contingency, recorded in the roadmap's kill criteria,
not a pending decision.

### Scope fence

Native dbus re-implements exactly the slice the frozen API
exercises, and no more. From
[`dbus-marshalling.md`](../architecture/dbus-marshalling.md):

- **Transport**: system bus only, unix socket. No session bus.
- **Auth**: SASL `EXTERNAL` + `NEGOTIATE_UNIX_FD` + `Hello`.
- **Calls**: `GetSessionByPID` (`u`→`o`), `TakeControl` (`b`→∅),
  `ReleaseControl` (∅→∅), `TakeDevice` (`uu`→`hb`),
  `ReleaseDevice` (`uu`→∅), `PauseDeviceComplete` (`uu`→∅).
- **Signals**: `PauseDevice` (`uus`), `ResumeDevice` (`uuh`).
- **Types**: `u`, `s`/`o`, `g`, `b`, `h`. Not `y n q i x t d a (...) v`.

Note this fence is **wider than proposal 0001's**, which listed
three method calls. The audit's CRIT-1 established that
`TakeControl` is mandatory, and `ReleaseControl` and
`PauseDeviceComplete` come with it. Proposal 0001's estimate was
built on an incomplete protocol model; the roadmap carries the
corrected one.

Explicitly deferred past 1.0: `Properties.Get/Set/GetAll`,
`Introspectable`, session bus, generic method dispatch. These are
the M2 wishlist and remain gated on a second AGNOS consumer.

## Consequences

### Positive

- The longest-pole v1.0 item becomes schedulable. Six documents
  that deferred to "the decision" can be finished.
- The libsystemd dependency leaves the consumer's edge entirely.
  Consumers stop needing `libsystemd-dev`, `pkg-config` and a
  two-stage link — which, per audit CRIT-2, has never worked as
  documented anyway.
- samvada stops being systemd-only: a wire-protocol client works
  against any dbus daemon, including on non-systemd distros where
  `elogind` speaks the same protocol.
- The backend gets tested because it is the backend we are
  keeping.

### Negative

- **The audit surface grows substantially.** ~650–1010 LoC of
  hand-rolled byte parsing replaces libsystemd's parser, which is
  validated by thousands of upstream consumers. samvada takes on
  responsibility for alignment, endianness, bounds and fd handling
  that it currently delegates. This is the single largest cost and
  it earns its own audit pass before 1.0.
- **`cmsg`/`SCM_RIGHTS` is genuinely risky.** Proposal 0001 rates
  it the only Low-confidence module: `CMSG_ALIGN`/`CMSG_SPACE`/
  `CMSG_DATA` have no Cyrius helper and must be hand-rolled
  against the kernel ABI. The roadmap isolates it behind a
  socketpair test that needs no logind.
- Multi-week effort against a consumer whose own e2e is
  hardware-blocked, so the final integration cannot be scheduled
  freely.
- Two backends must be kept green simultaneously during the
  transition.

### Neutral

- The version triple and release cadence are unaffected; native
  dbus lands across several 0.x minors before the 1.0.0 tag.
- The C shim's own defects still get fixed while it lives — it is
  the fallback, so letting it rot would remove the safety net.
  0.5.1 does exactly this.

## Alternatives considered

- **Keep deferring to mabda v4.0.** Rejected: see "Why now".
  Deferral produced an untested backend and a stalled roadmap.
- **A.2 (removal) now.** Rejected: the logind path is load-bearing
  for the only consumer and nothing replaces it. Retained as a
  contingency with explicit kill criteria in the roadmap.
- **Vendor a C dbus library other than libsystemd** (libdbus,
  basu). Rejected for the same reason ADR-0001 rejected libdbus,
  plus it does not achieve the actual goal — removing the C
  dependency from consumers.
- **Fix the C shim and ship 1.0 on libsystemd.** Tempting after
  0.5.1, since the shim now works. Rejected because it inverts
  ADR-0001's whole architecture: the shim was adopted *as* a
  stop-gap, and freezing it at 1.0 would make the libsystemd
  dependency permanent for every consumer, on every platform,
  forever.

## References

- [ADR-0001 — C-shim-then-pivot](0001-c-shim-then-pivot.md)
- [ADR-0002 — append-after-kind FFI invariant](0002-append-after-kind-ffi-invariant.md)
- [proposal 0001 — v1.0 dbus backend pivot](../proposals/0001-v1-dbus-backend-pivot.md) — the module map and risk register this ADR ratifies
- [`docs/development/roadmap.md`](../development/roadmap.md) — the milestone sequencing
- [`docs/architecture/dbus-marshalling.md`](../architecture/dbus-marshalling.md) — the wire-format slice being re-implemented
- [`docs/audit/2026-09-09-audit.md`](../audit/2026-09-09-audit.md) — CRIT-1, which corrected the protocol scope
