# samvada — the road to Native DBus in Cyrius

**Identity.** Cyrius dbus client for the AGNOS suite. First
consumer: [mabda](https://github.com/MacCracken/mabda)'s Phase D
surface present (`gpu_surface_configure_native_logind`).

**Destination.** samvada 1.0.0 speaks dbus natively, in Cyrius,
over a raw unix socket — no libsystemd, no C shim, no
`pkg-config`, nothing for a consumer to link. The path is settled:
[ADR-0003](../adr/0003-native-cyrius-dbus.md) adopts Path A.1 and
retires the A.1-vs-A.2 deferral that has blocked six documents
since 0.2.0.

**Posture.** The libsystemd C shim is a *transitional* backend,
kept green and shipping until the native backend reaches parity,
then deleted at the 1.0.0 tag. Both backends coexist through the
transition: the shim is the differential reference and the
fallback, not dead weight.

**What the 2026-09-09 audit changed about this plan.** Two things,
and they are load-bearing:

1. **The protocol scope was wrong.** samvada never sent
   `TakeControl`, so `TakeDevice` failed in every environment for
   five releases ([audit CRIT-1](../audit/2026-09-09-audit.md)).
   The native marshaller's call fence is therefore **six method
   calls, not three**. A plan built on the old three-call fence
   would have shipped a 1.0.0 that cannot take a device.
2. **Structural pins are not evidence.** Every gate was green
   while the product did not work, because nothing had ever been
   executed against a real bus. Every milestone below therefore
   ends with **bytes that reached a real dbus daemon, or bytes
   captured from one** — never with "the module is written".

State (what is in tree right now) lives in
[`state.md`](state.md). This file is the sequencing.

---

## v1.0.0 definition of done

Rewritten from the pre-0.5.1 checklist. Prior items are carried,
not quietly dropped — where one changed, the change is stated.

- [x] **Public API frozen** — every exported symbol documented +
  tested. Closed 0.4.0. **Re-asserted as a standing invariant,
  not a closed box**: it was certified against the C-shim
  implementation, so every milestone below re-checks that
  `dist/samvada.cyr`'s exported symbol set is unchanged.
- [x] **CHANGELOG complete from v0.1.0 onward.** Verified 0.4.0;
  CI enforces per release.
- [ ] **Native dbus backend reaches parity with the C shim** —
  `kind = PURE_CYRIUS` populates the same slots, and every
  behaviour the shim provides is provided natively. *(New; the
  substance of this file.)*
- [ ] **The C shim is deleted** — `deps/samvada_main.c` gone,
  libsystemd absent from every consumer build. *(Replaces the old
  "architectural pivot decided", which [ADR-0003](../adr/0003-native-cyrius-dbus.md)
  closes.)*
- [ ] **Benchmarks captured in `docs/benchmarks.md`.** *(Carried,
  and now easier: a native backend can be driven from samvada's
  own binary against any running system bus. Split into three
  rows — handshake latency and signal-pump drain move into the N
  lane; only the `TakeDevice` round-trip stays hardware-gated.)*
- [ ] **Security audit pass over the native marshaller**
  (`docs/audit/YYYY-MM-DD-audit.md`). *(Carried. The 0.5.1 pass
  covered the C-shim surface; ~650–1010 LoC of hand-rolled byte
  parsing replaces libsystemd's validated parser and earns its
  own pass.)*
- [ ] **Downstream consumer green** — mabda builds, pins and runs
  against the native backend from a seated session. *(Carried.
  Lives in the quarantined CG lane below and does **not** block
  any N milestone.)*
- [ ] ~~Six-consumer regression sweep~~ → **every AGNOS consumer
  that pins `[deps.samvada]` builds and tests cleanly (today:
  mabda).** *(The number was a template artifact from the
  agnosticos standard; exactly one consumer exists and M2 is
  gated on a hypothetical second. The intent is kept, the
  fictional count is retired.)*

---

## Two lanes

Everything below runs in one of two lanes, and the split is the
point.

**The N lane (native dbus)** — N0 through N7. Buildable and
completable with **zero mabda involvement and zero special
hardware**. Nothing in this lane may be gated on the consumer.

**The CG lane (consumer green)** — externally gated on mabda's
logind-master-retention hardware gate. It runs in parallel, it
**never blocks an N milestone**, and only the 1.0.0 tag itself
depends on it.

This quarantine is deliberate. M1 has read *"code-complete,
awaiting consumer e2e"* since 0.2.0 — five releases in which a
hardware gate on someone else's repo silently became samvada's
critical path, and a guaranteed-fail bug sat undiscovered behind
it.

---

## Shipped (historical)

| Milestone | Version | Status |
|---|---|---|
| **M0** — Scaffold | 0.1.0 | ✅ 2026-04-30. `cyrius init`, docs tree, placeholder `samvada_version()`. |
| **M1** — libsystemd C shim + logind subset | 0.2.0 | ⚠️ Shipped, but **not correct**: `TakeDevice` could never succeed (audit CRIT-1). Fixed in 0.5.1. |
| — Hardening review | 0.2.2 | ✅ HIGH-1 double-init leak, MED-1 stale errno. |
| — Toolchain moves | 0.3.0, 0.4.1, 0.5.0 | ✅ cyrius 5.7.48 → 6.0.40 → 6.2.6 → 6.6.1. |
| — Live-bus bench scaffold + v1.0 pivot proposal | 0.4.0 | ✅ HW-gated harness; [proposal 0001](../proposals/0001-v1-dbus-backend-pivot.md). |
| **P(-1) audit** | 0.5.1 | ✅ 2026-09-09. 2 CRITICAL, 10 MEDIUM, 16 LOW fixed; tests 38 → 114. |
| — Toolchain patch | 0.5.2 | ✅ cyrius 6.6.2, carrying samvada's own upstream symbol-visibility fix. |
| **N0** — signal-visibility contract | 0.6.0 | ✅ 2026-09-09. ADR-0004; `TakeControl` scoped to device ownership (0.5.1 console regression); no new slot. |
| **N1** — SCM_RIGHTS fd passing | 0.7.0 | ✅ 2026-09-09. `src/dbus_sys.cyr`; fd proven across a socketpair; surplus-fd leak found and fixed. |
| **N2** — golden byte corpus | 0.7.1 | ✅ 2026-09-09. 15 fixtures + tap/decoder tools; corrected our own SASL docs. Second-host capture outstanding. |
| **N3** — transport, auth, framing | 0.8.0 | ✅ 2026-09-09. Native SASL against the real bus; framer proven by drip-feed. `Hello` round-trip deferred to N4 (needs the marshaller). |
| **N4** — marshal / unmarshal | 0.9.0 | ✅ 2026-09-09. `Hello` byte-identical to libsystemd's and ACCEPTED by the bus; reply decoded; closes N3's deferred criterion. |

**M2 — "generalize beyond logind"** (Properties, Introspectable,
session bus, generic method dispatch, async variants) is
**deferred past 1.0**, unchanged. It was always gated on a second
AGNOS consumer, none exists, and widening the surface before the
pivot would enlarge exactly what the pivot has to re-implement
(ADR-0001 §Neutral). Retained as the v1.1+ wishlist at the foot
of this file.

---

## The N lane

### N0 — Decide the signal-visibility contract (0.6.0) — ✅ SHIPPED

**Why first.** The answer may require an additive FFI slot, and
that decision propagates through every milestone below. It is
also answerable this week, with no hardware.

**The question as posed.** Slots 48/56 were populated by the shim
and dispatched by no Cyrius code, so `PauseDevice`/`ResumeDevice`
were never delivered ([audit MED-7](../audit/2026-09-09-audit.md)),
and `PauseDeviceComplete` was unwired. The frozen API gives
`samvada_pump_signals()` a single integer return and no callback
registration — so even a working signal path would tell the
consumer *nothing*, while the consumer's whole reason to care about
a pause is to stop drawing to a revoked DRM fd.

**The answer**: not delivered in 0.x, ratified as a property. The
slots stay reserved. See ADR-0004 for the evidence.

> **Renumbered 2026-09-09.** N0 was originally scoped at 0.5.2;
> that version was consumed by the cyrius 6.6.2 toolchain patch
> (which carried samvada's own upstream symbol-visibility fix), so
> the whole N lane shifts up one minor. N0 ships as **0.6.0**.

- **Deliverables**: read mabda's actual call site
  (`_backend_native_surface_configure_logind`,
  `src/surface_v3.cyr`, `src/backend_native.cyr:2553`) — it costs
  nothing and needs no hardware; decide how a consumer observes a
  pause; ratify as **ADR-0004**.
- **Exit**: ✅ [ADR-0004](../adr/0004-session-control-lifecycle-and-signal-visibility.md)
  accepted. **No slot was needed** — the table stays at 11 slots /
  88 bytes, so N1 starts against an unchanged ABI.
- **What it actually found**: the larger half of N0 was not the
  signal question but a console regression from 0.5.1 —
  `samvada_init()` called `TakeControl`, which runs logind's
  `session_prepare_vt()` (`KDSKBMODE=K_OFF`,
  `KDSETMODE=KD_GRAPHICS`), blanking the console and killing the
  keyboard on any seated VT session before a device was requested.
  Control is now scoped to device ownership. Signal delivery is
  ratified as **not provided in 0.x**, on evidence that a VT seat
  only ever receives `PauseDevice("force")` (no reply required)
  and that nothing in logind waits on the controller.
- **Also settle here**: `samvada_release()` now issues
  `ReleaseControl`, and **logind revokes every device taken via
  `TakeDevice` when session control is released**. An fd the
  consumer still holds becomes invalid. This was already true via
  the bus close, but `public-api.md` tells the caller *"the fd's
  lifetime is the caller's"*. Document the real contract.

### N1 — SCM_RIGHTS fd passing, before any dbus byte (0.7.0) — ✅ SHIPPED

**Why first among the build milestones.** It is the one module
proposal 0001 rates Low-confidence, and the fallback is cheap
*only* while nothing is built on top of it.

**Risk re-rated Low → Medium on evidence.** Proposal 0001 assumed
no in-language `cmsg` reference exists. That is false, and the
references were read:

| Reference | What it ships |
|---|---|
| `cyrius-doom/src/platform/wayland/client.cyr:206-228` | hand-built `msghdr`(56) + `cmsghdr`(24) `SCM_RIGHTS` **send** |
| `kybernet/src/lib/notify.cyr:182-235` | **receive** walk with `MSG_CTRUNC` handling + `cmsg_len` bounds checks |
| `argonaut/src/notify.cyr:176-207` | receive-side walk with `MSG_DONTWAIT` |

Diff against these rather than deriving the alignment macros from
the kernel ABI. `dbus_sys.cyr` drops from ~120–180 LoC to ~40–60;
the milestone drops from 2–3 sessions to ~1.

- **Traps to carry** (each already cost someone else a bug):
  - `cyrius-doom` hardcodes `SYS_SENDMSG = 46`, which is
    **`ftruncate` on aarch64**. Arch-select behind the same
    `CYRIUS_ARCH_X86` / `CYRIUS_ARCH_AARCH64` split
    `lib/syscalls.cyr` uses (46 / 211; `recvmsg` is 47 / 212).
  - `MSG_DONTWAIT` is **per-call, not per-socket**. Setting
    `O_NONBLOCK` on the socket instead would silently break every
    request/reply.
  - `var buf[N]` inside a fn is **STATIC**, so the fd path is
    non-reentrant by construction. State it; do not discover it.
  - The test-only `sendmsg` helper goes in `tests/`, not `src/` —
    samvada never *sends* an fd in production and the exported
    surface must not grow for a harness.
- **Tests (no hardware, no bus)**: send a real fd through a
  `socketpair`, receive it, assert `fstat` dev/ino match. Pin the
  layout constants (`iovec`=16, `msghdr`=56, `cmsghdr`=16,
  `CMSG_LEN(4)`=20, `CMSG_SPACE(4)`=24). Assert `F_GETFD` shows
  `FD_CLOEXEC` actually set rather than trusting
  `MSG_CMSG_CLOEXEC`. Run the round-trip 1000× and assert the
  process descriptor count is unchanged — an fd-leak pin.
- **Exit**: ✅ an fd crosses a socketpair and is provably the same
  open file (`fstat` dev/ino); layout pins green; `FD_CLOEXEC`
  asserted, not trusted; 200 round trips leak nothing.
  `src/dbus_sys.cyr` + `tests/dbus_sys.tcyr` (63 asserts).
  Bundle unchanged — still 26 exported fns, `dbus_sys` excluded
  until N6.
- **What it found**: `CMSG_SPACE(1 fd)` and `CMSG_SPACE(2 fds)` are
  both 24 bytes, so a two-fd message fits a one-fd buffer with no
  `MSG_CTRUNC` — the first draft leaked the surplus descriptor.
  Fixed and pinned.
- **Risk re-rating confirmed**: Low → Medium was right. The
  sibling references made this ~1 session rather than 2-3, and the
  module is ~90 LoC rather than the estimated 80-130.

### N2 — Capture the golden corpus (0.7.1) — ✅ SHIPPED (one criterion outstanding)

Do this **while the shim still exists** — the reference dies with
it.

- **Deliverable**: `tools/dbus_tap.py`, a ~60-line python3 AF_UNIX
  relay that listens on a socket, connects to
  `/run/dbus/system_bus_socket`, hex-dumps both directions, and is
  driven by pointing a client at it with
  `DBUS_SYSTEM_BUS_ADDRESS=unix:path=…`. Verified working. It
  needs no C, no libsystemd and no linking — unlike an
  `sd_bus_set_fd` capture tool, whose fallback is hand
  transcription.
- **Deliverable**: `tests/fixtures/dbus/` with per-fixture
  provenance (host, systemd version, command, date) and a
  **REAL / SYNTHETIC marker**.
- **Honesty requirement, and it is not optional**: the
  `TakeDevice` *reply* — the single most important decode fixture
  — **cannot be captured**, because no seated session is
  available and the shim could not execute `TakeDevice` anyway.
  It must be marked SYNTHETIC and hand-assembled from
  `dbus-marshalling.md`. That means an error in the prose becomes
  an error in the test and the implementation *simultaneously*.
  Mitigate by re-deriving it independently from the D-Bus spec,
  not from our own doc.
- **Exit**: ✅ corpus committed (15 fixtures + MANIFEST with
  provenance); ✅ every fixture marked REAL or SYNTHETIC (14 real,
  1 synthetic); ⚠️ **second-host capture OUTSTANDING** — only one
  host is available. A same-host repeat was done instead, which
  establishes which bytes are volatile (client -> bus is
  byte-identical except the pid) but cannot establish which are
  host-specific. Recorded in the MANIFEST rather than quietly
  dropped.
- **What it found**: our own SASL documentation was wrong in a way
  that would hang the native reader; one read carries **two
  complete messages** (101 + 181 = 282, zero residual); alignment
  is per-message, not per-buffer — a bug the decoder itself hit
  first, and which initially made the second message *look*
  truncated; header field order is arbitrary; the bus's first
  reply uses serial `0xFFFFFFFF`.
- **The synthetic fixture is derived from a REAL fd-bearing reply**
  (`Manager.Inhibit` returns `h` and needs no seat), not
  hand-assembled from prose — which is the mitigation this
  milestone asked for.

### N3 — Transport, auth and framing (0.8.0) — ✅ SHIPPED

Modules: `dbus_socket.cyr`, `dbus_auth.cyr`, `dbus_frame.cyr`.

**`dbus-marshalling.md` §SASL is wrong and N3 corrects it.** The
captured reality: libsystemd sends **one pipelined 48-byte
write** — `\0AUTH EXTERNAL\r\nDATA\r\nNEGOTIATE_UNIX_FD\r\nBEGIN\r\n`,
with an *empty-credential* `DATA` step — and the server answers
**three lines in a single 58-byte read**. The documented
`AUTH EXTERNAL <hex(ascii(uid))>` ping-pong is also legal and also
works, but the reply reader must be **line-oriented over a
buffer**; one-read-per-line hangs against a real bus.

**`dbus_frame.cyr` is a first-class module (~60–100 LoC), not a
detail of unmarshalling.** Proposal 0001 has no framer. Verified
necessary: the first server response after `Hello` is a single
262-byte read carrying **two complete messages** (the
`METHOD_RETURN`, then a `NameAcquired` signal). The framer owns a
persistent receive buffer, computes total length as
`16 + padded(fields_len) + body_len`, yields one message at a
time, and carries a partial tail across reads.

- **Also required, and named in no prior plan**: a **write-all
  loop**. A unix stream socket can short-write under load; a
  156-byte request that writes 100 bytes and returns leaves a torn
  message on the wire and the bus disconnects. The stdlib's write
  is a thin syscall wrapper, not a loop.
- **Also**: the connect path must **tolerate and discard
  unsolicited traffic**. `NameAcquired` arrives with the `Hello`
  reply, before any match rule exists. This is where serial
  correlation first breaks if it is not handled.
- **Abstract sockets**: `addrlen = 2 + strlen(path) + 1` is
  correct only for filesystem paths. An abstract socket (leading
  NUL in `sun_path`) needs `addrlen = 2 + 1 + name_len` and no
  trailing NUL — reachable in 1.0.0 via `DBUS_SYSTEM_BUS_ADDRESS`,
  not just a v1.1 session-bus concern.
- **Exit**: `Hello` round-trips against the real system bus and
  the unique name (`:1.NN`) is printed; feeding the captured
  262-byte blob **one byte at a time** yields the same two
  messages as feeding it whole, with zero residual bytes.

### N4 — Marshal and unmarshal (0.9.0) — ✅ SHIPPED

Modules: `dbus_marshal.cyr`, `dbus_unmarshal.cyr`.

**Golden bytes are a regression fixture, not a correctness
oracle.** The captured traffic shows libsystemd emits header
fields in order **1, 3, 2, 6, 8** — non-ascending, an
implementation choice, not a spec requirement — and the flags byte
varies per call (`0x00` on `Hello`, `0x04 NO_AUTO_START` on the
logind calls). A *correct* samvada emitting ascending field order
would fail a naive byte-equality gate. So:

- Byte-equality runs against samvada's **own** encoder output, as
  a regression pin, with volatile ranges (SERIAL, REPLY_SERIAL,
  the unique name in SENDER/DESTINATION, the flags byte)
  **documented per fixture and masked**.
- **The bus's acceptance is the correctness oracle.** Send
  samvada's bytes to the running system bus and assert its
  reaction. With `TakeControl` in scope this gives a three-way
  discrimination available on any dev box with no DRM hardware:
  a malformed request draws
  `org.freedesktop.DBus.Error.InvalidArgs`; a well-formed request
  from an uncontrolled session draws
  `org.freedesktop.login1.NotInControl`; a well-formed request
  from a *controlled* session with a nonexistent minor draws a
  device error. That is the cheapest strong evidence in the whole
  plan, and it directly exercises the CRIT-1 fix.
- **`u32` masking is a milestone-one acceptance criterion, not a
  risk-register row.** The bus's very first `METHOD_RETURN`
  carries serial `0xFFFFFFFF`. `>>` is logical and there is no
  unsigned type, so a signed-comparison bug fires on **message
  one**, not in a fuzz corner. Use that literal as the fixture.
- **Exit**: ✅ `Hello` encodes **byte-identically** to the capture
  (128/128) and the **bus accepted it and replied**; ✅ the reply
  decodes and yields the unique name; ✅ the `0xFFFFFFFF` serial
  reads back positive. ⚠️ The other five requests are encodable
  (`uu`, `b`, empty-body and `hb` shapes all pinned) but only
  `Hello` has been sent live — the logind calls need the session
  layer, which is N5.
- **This also closes N3's deferred criterion**: `Hello` now
  round-trips against the real bus and prints `:1.NNNNN`.

### N5 — The logind session layer (0.10.0)

Module: `dbus_session.cyr` — the six calls, the two signals, the
serial counter, and the signal dispatch loop that replaces
`sd_bus_process`.

**The frozen fncall arity constrains every native signature, and
no prior plan said so.** `src/samvada.cyr` is frozen and
dispatches with exactly:

```
fncall1(bus_slot, bus_out)
fncall4(sp_slot,  bus, pid, buf, len)
fncall6(slot,     bus, sess_cstr, major, minor, fd_out, active_out)
fncall4(slot,     bus, sess, major, minor)
fncall2(slot,     bus, sess)                 # take_control / release_control
```

The native populator must install Cyrius fns matching those exact
shapes — **including the vestigial `bus` first argument and the
C-style out-pointer pairs**. The session layer cannot be written
with natural Cyrius signatures; it must be written to the C
shim's ABI. Getting this wrong is a silent crash, not a compile
error. "Populate the same offsets with Cyrius fn addresses" is not
mechanical.

- **Session selection is an open design question, surfaced here.**
  `GetSessionByPID($$)` returns the caller's session, which is
  **not necessarily seated** — on the audit host it returned a
  `Seat=""` session while seat0 belonged to the display manager.
  So even with `TakeControl`, `TakeDevice` on the pid-derived
  session cannot get DRM master. Validating the session's `Seat`
  property needs `org.freedesktop.DBus.Properties`, which the
  scope fence defers. Decide in N5: validate (and widen the
  fence), or document that `TakeDevice` only works when the
  caller's own pid is in a seated session.
- **Session-path escaping**: logind returns `/session/_32`, where
  `_32` is hex-escaped ASCII `'2'`. samvada passes it through
  opaquely today, which is safe — but any comparison, validation
  or logging needs the unescaping rule, and it appears in no
  document or fixture.
- **Exit**: `GetSessionByPID` → `TakeControl` → `ReleaseControl`
  round-trips natively against the real bus; `TakeDevice` reaches
  a device-level error rather than `NotInControl`.

### N6 — Cutover (0.11.0)

`kind = PURE_CYRIUS` becomes the default backend. **The shim stays
in tree**, selectable per build, as the differential reference.

- **Error-code parity is a contract, and it is the likeliest
  silent breach.** The frozen API promises "sd-bus errno
  pass-through". libsystemd maps dbus error *names* to errnos; the
  native backend must reproduce that mapping or consumers
  branching on specific magnitudes break. This is an explicit
  deliverable with a name-to-errno table, not an afterthought.
- **Exit**: both backends pass the same suite; a differential run
  (sequential A/B, since the module-scope singleton and the
  `-EBUSY` guard forbid two live backends in one process) agrees
  on every outcome and every error code.

### N7 — Delete the shim, audit, tag 1.0.0

- `deps/samvada_main.c` deleted; libsystemd gone from every
  consumer build; `consumer-link.md` reduced to "add the dep".
- **Full security audit** of the native marshaller →
  `docs/audit/YYYY-MM-DD-audit.md`. Non-negotiable: this is where
  samvada takes ownership of alignment, endianness, bounds and fd
  handling that libsystemd used to own.
- Benchmark rows filled (handshake, signal-pump; `TakeDevice`
  from the CG lane if it has cleared).
- **Keep `tools/dbus_tap.py` as a committed dev tool.** `busctl`
  and libsystemd remain installed on any dev box long after
  samvada stops linking them — free ongoing conformance testing
  against the reference implementation, permanently.

### Standing exit criteria (every N milestone)

Mechanically checkable, so "the consumer is never broken" is a
gate rather than an intention:

- `dist/samvada.cyr`'s exported symbol set is unchanged (26 fns).
- `test_ffi_slot_offsets` is untouched; `kind` is still at +64.
- The C-shim backend still compiles, links and passes CI.
- mabda re-pins to the milestone tag and its smoke build stays
  green — **every milestone**, not only at cutover, so export
  drift surfaces immediately instead of at the most expensive
  commit.

---

## The CG lane (consumer green) — externally gated

Runs in parallel. Blocks **only** the 1.0.0 tag.

- **CG-1** — mabda's live-bus e2e from a seated session. Gated on
  the logind-master-retention hardware gate.
- **CG-2** — a `TakeDevice` that actually returns an fd, filling
  the last benchmark row.
- **CG-3** — the `state.md` note confirming the gate cleared
  (M1's original closing artifact, kept verbatim).

**A cheap way to de-gate this that nobody proposed**: a dedicated
**text VT** — a seated session with no compositor holding master —
or attaching a seat with `loginctl`. Every prior plan either
deferred to hardware or proposed running from the maintainer's
graphical session, which can black-screen the user. The text-VT
path is safe and cheap and should be tried before accepting the
gate as immovable.

**Expiry, which no prior plan gave it**: if CG-1 is still open at
the N7 gate, 1.0.0 ships with the native backend **documented as
not-yet-consumer-validated**, and the lane is re-evaluated at
1.1.0. A lane with no expiry is how M1 stayed open for five
releases.

---

## Kill criteria — a graded ladder

Each rung names the evidence, the fallback, and what the fallback
still buys. A flat "stop" is not actionable.

| Rung | Evidence | Fallback | What it still buys |
|---|---|---|---|
| **1** | N1 fails: `SCM_RIGHTS` cannot be made to work in Cyrius after a bounded attempt | Retain a ~40-line **libc-only** `recvmsg`+`cmsg` helper. No libsystemd, no `pkg-config` | Still deletes ~250 of the shim's ~330 lines; still drops the libsystemd dep from every consumer |
| **2** | N3/N4 stall: the bus rejects samvada's bytes and the cause resists diagnosis | Hybrid `kind = 3`: native transport, libsystemd marshalling | Keeps the native socket path; isolates the failure to the codec |
| **3** | N5 breaches error-code parity in a way consumers can observe | Hold cutover; ship native as opt-in behind the kind word | Native backend gets real exercise without breaking mabda |
| **4** | Effort exceeds ~3× the proposal-0001 estimate | Freeze the N lane; keep shipping the (now-correct) C shim | 0.5.1 made the shim work; it is a viable steady state, not a crisis |
| **5** | AGNOS standardizes on a different session-management primitive | **A.2 (removal)**, per ADR-0003's retained contingency | Surface is 5 fns with 1 consumer — migration is hours |

---

## Deferred past 1.0 (the v1.1+ wishlist)

Carried from M2 and the architecture docs' "what samvada does NOT
touch". All still gated on a second AGNOS consumer:

- `org.freedesktop.DBus.Properties` Get/Set/GetAll — **except**
  if N5's session-selection question forces `Seat` validation into
  1.0.
- `org.freedesktop.DBus.Introspectable`.
- Session bus (per-user, `DBUS_SESSION_BUS_ADDRESS`, abstract
  sockets in `$XDG_RUNTIME_DIR`).
- Generic `dbus_call_method(dst, path, iface, member, …)`.
- Async / non-blocking variants for consumers with their own
  event loops.
- Thread safety. samvada is single-threaded by design; module
  scope state has no locking and consumers must serialize. `var
  buf[N]` being static inside a fn is a structural reason this is
  hard, not merely undone.

## Out of scope (unchanged)

- **Windows / macOS portability.** dbus is Linux-shape.
- **DBus 1.x → KDBus migration.** KDBus was never merged.

---

## Notes / decisions

- **2026-04-30** — Project scaffolded. C-shim-then-pivot strategy
  locked in ([ADR-0001](../adr/0001-c-shim-then-pivot.md)) after
  the wgpu-native parallel made the shim the better stop-gap.
- **2026-06-02** — [Proposal 0001](../proposals/0001-v1-dbus-backend-pivot.md)
  scoped Path A.1 without choosing it; decision deferred to mabda
  v4.0.
- **2026-09-09** — `0.5.1`. The P(-1) audit found that
  `TakeDevice` had never been able to succeed (CRIT-1) and that
  the documented consumer link could never have worked (CRIT-2).
  Both trace to the same cause: nothing had been executed against
  a real bus or a real consumer link, because the backend was
  treated as disposable scaffolding.
  **[ADR-0003](../adr/0003-native-cyrius-dbus.md) ends the
  deferral and adopts native Cyrius dbus** — partly on the merits,
  and partly because a backend nobody has committed to is a
  backend nobody tests. This roadmap is rewritten around that
  destination, with the two-lane split and the every-milestone
  live-evidence rule as the direct structural answer to how CRIT-1
  survived five releases.
