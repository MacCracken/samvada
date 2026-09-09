# 0004 — Session-control lifecycle, and no signal delivery in 0.x

**Status**: Accepted
**Date**: 2026-09-09
**Milestone**: N0 — the first milestone of
[ADR-0003](0003-native-cyrius-dbus.md)'s road to native Cyrius dbus
**Amends**: 0.5.1's CRIT-1 fix, which took session control in the wrong place

## Context

N0 was scoped to answer one question: *how does a consumer learn
that a device was paused or resumed?* Investigating it surfaced a
second, larger one, and the two turned out to have different
answers and very different urgency. This ADR settles both.

### What we found, verified against systemd v261 source

Not from the man page — the man page is wrong on one of these.

1. **`TakeControl` mutes the user's console.**
   `method_take_control` calls
   `session_set_controller(s, sender, force, /* prepare = */ true)`
   (`logind-session-dbus.c`), and the `prepare` branch runs
   `session_prepare_vt()` (`logind-session.c:1609-1628`), which on
   any session with `vtnr >= 1` does:

   ```c
   ioctl(vt, KDSKBMODE, K_OFF);        /* keyboard off   */
   ioctl(vt, KDSETMODE, KD_GRAPHICS);  /* console blanks */
   ioctl(vt, VT_SETMODE, &mode);       /* VT_PROCESS     */
   ```

   The source comment is explicit: *"When setting a session
   controller, we forcibly mute the VT and set it into
   graphics-mode."*

2. **Pause/resume signals are unicast to the controller.**
   `session_device_notify()` gates on
   `if (!sd->session->controller) return 0;` and then calls
   `sd_bus_message_set_destination(m, sd->session->controller)`
   (`logind-session-device.c:47,62`). They are addressed, not
   broadcast.

3. **`PauseDevice("pause")` — the only type requiring an ack — is
   effectively unreachable.** It is emitted only by
   `session_device_try_pause_all()`, whose sole caller is
   `session_activate()` in the branch taken when the seat has *no*
   VTs. On a seat *with* VTs — the normal case — `session_activate`
   returns `chvt()` before reaching it. What a controller actually
   receives on a VT switch is `"force"`, which is an
   after-the-fact notification requiring no reply.

4. **There is no ack timeout.** The man page claims forced signals
   are completed *"after an internal timeout"*; `struct Seat`
   (`logind-seat.h`) carries no `sd_event_source` and no timer at
   all, only `pending_switch`. Nothing waits on the controller on a
   VT seat, because `session_leave_vt()` acknowledges the switch
   unconditionally.

5. **`ResumeDevice` delivers a *new* fd.** Per the API docs: *"You
   should switch to the new descriptor and close the old one. They
   are not guaranteed to have the same underlying open file
   descriptor in the kernel."*

### The regression 0.5.1 introduced

0.5.1 fixed CRIT-1 by calling `TakeControl` inside
`samvada_init()`. That was the right call in the wrong place.
Combined with finding (1), `samvada_init()` blanked the console and
disabled the keyboard on any seated VT session — **before any
device was requested, and even if the subsequent `TakeDevice`
failed.**

The concrete path for the only consumer: mabda calls
`samvada_shim_init()` at startup, and its
`gpu_surface_configure_native_logind` may then return 0 ("logind
path unavailable") and fall back to kiosk. samvada stays
initialized, control stays held, and the console stays dead for the
life of the process.

This was invisible in testing because `session_prepare_vt()` begins
`if (s->vtnr < 1) return 0;` and every session available on the
audit host is seatless (`vtnr == 0`). **The defect could not
reproduce in the only environment we could reach** — the same shape
as CRIT-1 itself.

## Decision

### 1. Session control is scoped to device ownership, not to init

- `samvada_init()` **does not** take control. It validates that the
  `take_control` slot is wired and stops there, so a pre-0.5.1
  backend still fails loudly at init rather than at first use.
- `samvada_session_take_device()` acquires control **lazily**, on
  the first device take.
- A take that **fails** after acquiring control hands control
  straight back. `acquired == 1` implies control was not held on
  entry, which implies no device was held, so this can never revoke
  a live fd.
- `samvada_session_release_device()` drops control when the **last**
  device is released, so logind's `session_restore_vt()` returns the
  console immediately rather than at process exit.
- `samvada_release()` drops control if still held.

A consumer that initializes samvada and never takes a device now
never touches the user's VT.

**No public signature changes.** This is behavioural, inside the
frozen API.

### 2. samvada does not deliver session signals in the 0.x line

`PauseDevice` / `ResumeDevice` are **not** surfaced to consumers,
and this is a documented property rather than a gap. The FFI slots
+48 / +56 remain reserved and unwired.

Reasons, in order of weight:

- **No consumer wants it.** `grep -rn 'samvada_pump_signals'` over
  mabda returns nothing. Its logind path is synchronous: take
  master, configure, release.
- **The obligation is discharged without it.** Finding (3): a VT
  seat receives `"force"`, which requires no reply. Finding (4):
  nothing waits on us. Not delivering signals is degraded, not
  incorrect — and with decision (1), the degradation window is
  bounded by how long a device is actually held.
- **A partial contract would be worse than none.** Finding (5)
  means a consumer that learns only *that* a resume happened is
  still holding a stale fd, with no way to obtain the replacement.
  Any honest delivery contract must carry the new fd, which is a
  materially larger design than "notify me", and one we would be
  designing for a hypothetical consumer.
- **Everything added here must be re-implemented natively.**
  ADR-0001 §Neutral warns precisely that surface added before the
  pivot enlarges what the pivot must reproduce. The native
  marshaller is already ~650–1010 LoC.
- **It cannot be proven here.** No seated session exists, so a
  delivery mechanism would ship exercised only against a mock —
  which is exactly how CRIT-1 survived five releases.

### 3. Consequences we accept, and state plainly

- **VT switching is not supported while a device is held.** The
  consumer is not told the device was paused and will draw to a
  revoked fd until it stops. Documented, not hidden.
- **`ResumeDevice`'s replacement fd is dropped.** After a
  pause/resume cycle the consumer's fd is stale. The supported
  usage is take-hold-release within one session activation.
- `PauseDeviceComplete` stays unwired, which finding (3) makes
  safe.

## Consequences

### Positive

- The console regression is fixed, and pinned by tests that fail if
  it is reintroduced.
- N0 exits with **no new FFI slot**, so the table stays at 11 slots
  / 88 bytes and N1 is unblocked immediately.
- Nothing is added that the native marshaller must reproduce.
- The one mechanism samvada has never executed — a Cyrius fnptr
  invoked as an `sd_bus_message_handler_t` — stays unshipped rather
  than shipped untested.

### Negative

- A compositor-shaped consumer cannot use samvada 0.x across VT
  switches. That is a real limitation and it is now written down in
  `public-api.md` and `SECURITY.md` instead of being implied by
  four documents that claimed the subscription shipped.
- Dropping control on last-release means a
  take → release → take sequence re-runs the VT prepare/restore
  cycle. Judged the better trade against holding the console muted
  for a process that has moved on.

### Neutral

- Finding (2) is recorded for whoever revisits this: because the
  signals are unicast to the controller's unique name, dbus-broker
  routes them **without** any `AddMatch`. A future delivery
  implementation needs only a local filter, not the bus round-trip
  `sd_bus_match_signal()` performs — and can use a **C** filter in
  the shim, sidestepping the unvalidated Cyrius-callback path
  entirely. That materially lowers the cost of revisiting this,
  which is part of why deferring is safe.

## When this is revisited

Any one of these reopens it:

1. A consumer needs to survive VT switching — the trigger is a
   consumer that calls `samvada_pump_signals()` at all.
2. A seated session becomes available for testing, so a delivery
   mechanism could be proven rather than asserted (the CG lane).
3. The native marshaller lands and the fd-handoff question has to
   be answered anyway for `TakeDevice`.

## References

- [ADR-0001](0001-c-shim-then-pivot.md) §Neutral — surface added before the pivot
- [ADR-0002](0002-append-after-kind-ffi-invariant.md) — why no slot was needed
- [ADR-0003](0003-native-cyrius-dbus.md) — the destination this serves
- [`docs/audit/2026-09-09-audit.md`](../audit/2026-09-09-audit.md) — CRIT-1 and MED-7
- [`docs/development/roadmap.md`](../development/roadmap.md) — N0
- systemd v261: `src/login/logind-session.c`, `logind-session-device.c`, `logind-session-dbus.c`, `logind-seat.h`
