# samvada pre-1.0 security audit — native dbus marshaller — 2026-09-09

**Scope**: the native Cyrius dbus implementation that replaced
libsystemd across N1–N6 — `src/dbus_sys.cyr`, `dbus_frame.cyr`,
`dbus_socket.cyr`, `dbus_auth.cyr`, `dbus_marshal.cyr`,
`dbus_unmarshal.cyr`, `dbus_session.cyr` — and the frozen public API
above them.

**Target release**: 1.0.0.

**Why this audit is not the 0.5.1 one.** That audit examined a thin
wrapper around libsystemd. Since then samvada has taken ownership of
everything libsystemd used to do: framing, alignment, endianness,
bounds checking, fd passing, and the parsing of bytes that arrive
from a socket shared with every other process on the system bus.
**The bus is not trusted input.**

---

## How this audit was conducted, and its limits

Stated first because it bears on how much weight the result carries.

A multi-agent audit was launched across seven dimensions with
adversarial verification, mirroring the 0.5.1 methodology. **It
failed entirely** — all eight agents terminated on a session limit,
returning zero findings and zero coverage notes.

What follows was therefore performed **directly and by hand**: byte
sequences constructed, driven through the real modules on the real
toolchain, and the actual behaviour recorded. That is a legitimate
audit, and it found three real defects. But it is **narrower** than
the one that was planned:

| Dimension | Covered |
|---|---|
| Hostile / malformed message | ✅ hands-on, with constructed inputs |
| Integer overflow & signedness | ✅ partial — the framer's arithmetic, exercised |
| fd lifecycle | ⚠️ **not re-audited here**; rests on 0.8.0's work and the existing descriptor-counting tests |
| Resource exhaustion / DoS | ❌ **not covered** |
| Protocol state machine | ✅ **reply forgery audited in full** — see below; rest of the state machine not covered |
| Public API contract | ⚠️ partial — via the differential harness only |
| Build / supply chain | ❌ **not covered** |

**Four of seven dimensions were unaudited or partial** at that
point. They were listed as outstanding rather than quietly omitted.

**Then the reply-forgery lane was re-run and it worked.** After the
session limit lifted, the single question the hand audit named as
"the most valuable thing to audit next" was put to a three-lane
multi-agent workflow with an adversarial adjudicator: what the
*broker* enforces, what *libsystemd* does that samvada does not, and
a lane whose only job was to *actually forge a reply against the
running binary*. Four agents, 148 tool calls, zero failures. Its
findings are in **§ Reply forgery** below, and they include one
defect that has nothing to do with forgery and mattered more than
anything else in this document.

---

## Summary

| Severity | Count | Disposition |
|---|---|---|
| HIGH | 2 | Fixed in 1.0.0 |
| MEDIUM | 1 | Fixed in 1.0.0 |
| **HIGH (functional)** | **1** | **Fixed in 1.0.0 — AUDIT-4** |
| LOW (defence-in-depth) | 1 | Documented, scheduled 1.0.x |
| **Total** | **5** | |

AUDIT-1..3 are one defect class: **the unmarshaller trusted
wire-supplied lengths without checking them against the bytes
actually present.**

AUDIT-4 is unrelated and is the most consequential finding here:
**samvada's reply path could not receive a file descriptor at all.**

---

## AUDIT-1 [HIGH] — `dbus_unmarshal_str_at` returned an unvalidated length

**File**: `src/dbus_unmarshal.cyr`.

**Defect**: a string-shaped header field whose u32 length claimed
`0xFFFFFFF0` had that length returned verbatim to the caller,
together with a pointer into the message. A caller copying `n` bytes
would read roughly 4 GB out of bounds.

**Measured, before the fix**:

```
(1) huge string len -> off=20
    str_at returned len=4294967280   <-- caller gets an unchecked length
```

**Reachability — stated honestly**: NOT reachable through samvada's
own call paths. The framer only yields a message once
`avail >= total`, so by the time the unmarshaller sees anything, the
header lengths are already consistent with the bytes present. The
one internal caller that uses `str_at` on attacker-influenced data
(`dbus_session_call`, reading `ERROR_NAME`) happens to survive
because `dbus_session_map_error` compares lengths before bytes and
rejects a mismatch immediately — **luck, not design**.

It is filed HIGH anyway, for the reason the 0.5.1 audit exists:
"unreachable because the caller happens to validate first" is the
exact shape of CRIT-1 and CRIT-2. This module also ships in the
consumer bundle, where callers we do not control can reach it.

**Fix**: an explicit message limit, enforced in the module rather
than assumed of the caller. `dbus_unmarshal_set_limit(total)` — and
`dbus_frame_next()` already returns exactly that number.

## AUDIT-2 [MEDIUM] — `find_field` walked past the buffer

**Defect**: the field-array walk bounded itself at `16 + fields_len`
taken from the header, with no check that the field array fits the
message. A header claiming `fields_len = 65535` in a 48-byte message
walked ~65 KB past the end looking for a code that was not there.

**Measured**: `fields_len=65535` → the walk ran to completion and
returned `-1201` **after** reading far out of bounds.

**Fix**: refuse a field array that extends past the limit, and bound
each individual field's advance.

## AUDIT-3 [HIGH] — the body cursor trusted `body_len`

**Defect**: `dbus_unmarshal_body_begin` set the cursor's end from
the header's `body_len` alone. A body_len of `0xFFFFFF` in a 32-byte
message yielded `remaining = 16777215`, and `next_u32()` then read
beyond the message — repeatedly, one aligned value at a time, under
the attacker's control.

**Measured, before the fix**:

```
(4) lying body_len -> remaining=16777215
    next_u32=0            <-- read from beyond the real message
```

**Fix**: clamp the cursor to the message's real extent.

**After the fix**, all three refuse:

```
(1) huge string len  -> find_field=-1201, str_at=-1201
(3) fields_len=65535 -> -1201
(4) lying body_len   -> remaining=8   (the real extent)
```

Pinned by `test_audit_hostile_lengths_refused` and
`test_audit_framer_rejects_absurd_headers`.

---

## AUDIT-4 [HIGH — functional] — the reply path could not receive a file descriptor

**File**: `src/dbus_session.cyr`, `dbus_session_call`.

Found by the reply-forgery workflow's libsystemd lane while reading
the reply path for a different reason, independently reproduced by
its adjudicator, and verified here against the live bus before any
fix was written.

**Defect**: `dbus_session_call` read the reply with plain
`sys_read`. On an `AF_UNIX` socket the kernel delivers the *bytes*
of a message and **discards its `SCM_RIGHTS` payload** unless the
reader uses `recvmsg` with a control buffer. So the reply arrived,
framed correctly, parsed correctly, still carrying `UNIX_FDS = 1`
in its header and a valid fd index in its body — and the descriptor
was gone. Nothing downstream could tell.

`dbus_native_take_device` therefore could never return a working
descriptor. It could only return `-9` (`-EBADF`), or — worse — an
unrelated fd left in the queue by an earlier `pump_signals`.
**Handing a DRM-master fd to a compositor is samvada's headline
1.0 capability, and it did not work.**

**Measured — the kernel half**, counting open descriptors across a
socketpair:

```
A) plain read()   : got 1 byte(s) 'X'  open fds 5 -> 5   => fd DISCARDED
B) recvmsg()+ctrl : got 1 byte(s) 'X'  open fds 5 -> 6   => fd INSTALLED (fd=5)
```

**Measured — end to end on the live system bus.** `Inhibit` returns
an `h` and needs no seated session, which makes it the one
fd-bearing logind reply reachable on an unseated host (it is how
`07-inhibit-reply-with-fd-REAL.bin` was captured). Driving the real
modules:

```
before:  reply UNIX_FDS header = 1
         body fd index         = 0
         frame_take_fd()       = -1     <-- the descriptor is gone
after:   frame_take_fd()       = 4      <-- SCM_RIGHTS survived
```

**Why five releases of tests missed it.** An unseated host answers
`TakeDevice` with `AccessDenied`, and **an ERROR reply carries no
fd**. Every live exercise of this path took the error branch, so
the fd branch was never once executed. `pump_signals` always used
`dbus_sys_recv_fd_flags` correctly — only the request/reply path
did not, and that is exactly the path the descriptor arrives on.
The 0.11.0 differential compared *error codes*, which matched.

**Fix**: `dbus_session_call` now reads through
`dbus_sys_recv_fd_flags` and pushes any received descriptor onto
the framer's fd queue, matching what `pump_signals` already did.
The SASL handshake reader keeps `sys_read` — no descriptors cross
during authentication.

Pinned by `test_reply_fd_survives_the_call_path`, which plants a
real descriptor on a real reply over a socketpair (no bus, no seat
required). **Mutation-tested**: restoring `sys_read` fails it with
`got 0, expected 1`.

---

## Reply forgery — audited, and the answer is no

**The question**: can a hostile peer on the system bus forge a
reply that samvada accepts as the answer to its in-flight call?

**The answer**: **No — but samvada is not what stops it.**

### What actually blocks it

The message bus does, through spec-mandated reply tracking. When
samvada calls `org.freedesktop.login1` with `flags = 0` (reply
expected), dbus-broker creates a reply slot in the *addressed*
peer's registry keyed by `(caller_id, serial)`
(`src/bus/peer.c:769`). An inbound reply is routed only if
`reply_slot_get_by_id` finds it in the **replier's own** registry
(`peer.c:868`). A forging peer's registry has no such slot, so the
message is dropped as `PEER_E_UNEXPECTED_REPLY` — silently, with no
bounce, giving the attacker no oracle. Reference `dbus-daemon`
implements the same tracking; this is not a broker quirk.

Reproduced live as uid 1000 against dbus-broker 37 / systemd
261.2, with the attacker given *best-case* knowledge — the victim's
exact unique name, the correctly-guessed sequential serial, and a
spoofed `SENDER=org.freedesktop.login1`:

- forged `METHOD_RETURN` → **delivered 0 times**
- forged `ERROR` → **delivered 0 times**
- the victim received only the genuine reply from `:1.5` (real
  logind, uid 0)

Two further bus-level guards back it up: the default `system.conf`
denies peer-to-peer `method_call` outright (so no reply window can
even be opened), and the broker **overwrites the `SENDER` field
with the sender's true unique id** on every forwarded message
(`message_stitch_sender`), so logind cannot be impersonated at all.

**Predictable serials turned out to be irrelevant.** The hand audit
flagged samvada's `1,2,3…` serials as a forgery enabler. They are
not: the broker rejects on *sender identity*, not serial secrecy, so
a correctly-guessed serial buys the attacker nothing. Serial
randomisation is therefore the *weaker* hardening and should not be
mistaken for the fix.

### The uncomfortable part: parity with libsystemd is worthless here

The hand audit assumed samvada was diverging from libsystemd by not
checking `SENDER`. **It is not.** Read against installed sd-bus
261.2 and proven empirically with a hostile bus server driving the
real `libsystemd.so.0.44.0`:

- `sd_bus_call` — the synchronous path samvada's retired C shim
  used via `sd_bus_call_method` — matches a reply on
  `reply_cookie` **alone** (`sd-bus.c:2472`).
- A `METHOD_RETURN` claiming `sender=:1.13`, and one with **no
  sender field at all**, were both accepted as the answer to a call
  addressed to `org.freedesktop.login1`, payload intact.
- sd-bus's only reply-provenance check anywhere is
  `m->destination == bus->unique_name` (`sd-bus.c:2790`) — and it
  exists **only on the async path**, which the shim never used.

So the native cutover removed no check, because there was none to
remove. *"We match libsystemd byte for byte"* is true here and
worth nothing as a security argument. **Any real fix must exceed
sd-bus, not match it.**

### What samvada actually contributes

Exactly one thing, and it was an accident. The only frame type a
hostile peer *can* push to samvada is a **directed SIGNAL** — the
broker delivers those to any destination, and a peer can bolt a
matching `REPLY_SERIAL` onto one. Verified: such a signal *is*
delivered (with `SENDER` stitched to the attacker's real id). It is
discarded solely because `dbus_session_call` returns only for
message type 2/3.

That guard reads as spec-correctness (ignore `NameAcquired`), so
its anti-forgery value was incidental and undocumented. It is now
pinned by `test_forged_signal_cannot_impersonate_a_reply` —
**mutation-tested**: widening the type gate to admit signals fails
it with `got 4, expected 2`.

### The residual risk, stated plainly

samvada performs **no independent authenticity validation of its
own**. It matches on `REPLY_SERIAL` and nothing else: it never
reads `DESTINATION` (field 6) or `SENDER` (field 7)
(`dbus_marshal_f_sender()` is defined and never called), it never
learns its own unique name (the Hello reply body is discarded), and
it does not watermark the buffer at send time.

The consequence was demonstrated against the **real binary**. Put a
rewriting relay on the socket and samvada accepts whatever it is
told:

```
SESSION PATH samvada ACCEPTED: /org/freedesktop/PWNED1/session/_99
```

That attacker — one who can interpose on the `AF_UNIX` stream — is
**strictly stronger** than a bus peer and is not the threat this
audit asked about. But it shows the trust is concentrated in one
place: the moment a forged reply *does* reach samvada, it is
accepted unconditionally.

**Severity: LOW.** Not exploitable by an unprivileged local user —
verified as "cannot", not merely "did not", because the block is
structural in the broker. Exploitation requires root (own
`org.freedesktop.login1`, which `login1.conf` gates to `user=root`,
or MITM the root-owned socket) or an out-of-model bus with
permissive policy or no reply tracking.

**It does not block 1.0.0.** samvada is designed to run on
arbitrary AGNOS buses, though, and it currently *relies* on a
property of the bus it does not verify — so the reliance is now
stated in `SECURITY.md` rather than left implicit.

### Scheduled hardening (1.0.x, API-preserving)

In priority order, all internal to `src/dbus_session.cyr`:

1. **Provenance check.** Retain the unique name from the Hello
   reply body (currently discarded) and require a matching reply's
   `DESTINATION` to equal it — this is sd-bus's async guard, needs
   no new round-trip, and so does not breach ADR-0003's scope
   fence. Additionally require `SENDER` to equal the addressed
   destination; this *exceeds* sd-bus and is reliable precisely
   because the broker stitches `SENDER` to the true id.
2. **Send-time watermark.** Record the frame-buffer position before
   `dbus_socket_write_all` and refuse any message sitting below it,
   mirroring sd-bus's `i = bus->rqueue_size`. Closes the pre-plant
   window that sequential serials otherwise leave open.
3. **A real wall-clock timeout.** `64 spins` is a message budget,
   not a clock; samvada currently has no time-based bound at all.

Serial randomisation is explicitly *not* on this list as a fix.

---

## Checked and found sound

Negative results, because they are what the fixes rest on.

- **No 64-bit wrap on 32-bit wire fields.** `fields_len` and
  `body_len` both at `0xFFFFFFFF` give `total = 8589934607` — large,
  positive, and correctly routed to the over-cap drain. Cyrius's
  64-bit ints absorb the sum, so no negative total and no backwards
  cursor walk. Verified directly.
- **The framer refuses what it must and tolerates what it should.**
  Unknown endian byte → `badendian`; `proto != 1` → `badproto`; an
  all-zero header → `badendian`. Unknown *message types* (0, 255)
  are accepted, which is correct — the spec requires receivers to
  ignore message types they do not understand, and rejecting them
  would be a forward-compatibility bug.
- **`load32` zero-extension holds throughout the header path**, and
  the deliberate sign-extension in `dbus_sys.cyr` is correct where
  it is (an `SCM_RIGHTS` payload is a signed int32 fd). Both
  behaviours are pinned by tests that assert the *dangerous* form is
  dangerous.
- **Error-code parity with the C shim holds** —
  `tools/differential/run.sh` runs both backends in one process and
  every error code matches.

---

## Outstanding — NOT audited before 1.0.0

These are gaps in this audit, not clean results. They are the
strongest argument for treating 1.0.0 as a first stable release
rather than a hardened one.

1. **Resource exhaustion / DoS.** `alloc()` never frees. Nothing
   here established whether a long-running consumer pumping at 60 Hz
   grows without bound, whether a peer can force worst-case buffer
   compaction, or whether the over-cap drain can be driven
   indefinitely.
2. ~~**Reply forgery.**~~ **Audited — see above.** Answer: not
   exploitable by an unprivileged peer (LOW, does not block 1.0.0);
   the defence is the broker's, not samvada's, and the
   defence-in-depth shortfall is scheduled for 1.0.x.
3. **fd lifecycle, re-audit.** 0.8.0 fixed a `MSG_CTRUNC` leak and an
   out-of-bounds read here, and descriptor-counting tests exist. The
   forgery audit incidentally found the largest hole in this
   dimension — AUDIT-4, a reply path that could not receive a
   descriptor at all — but the native fd queue still has not been
   audited end to end, and **the queue's behaviour when a reply and
   a signal both carry descriptors remains unexamined**.
4. **Build and supply chain** for the post-cutover tree.
5. **Anything requiring a seated session** — a `TakeDevice` that
   actually transfers a descriptor has still never run, by anyone.
   AUDIT-4 narrows this materially: the descriptor mechanism is now
   proven end to end on the live bus through `Inhibit`, which
   exercises the identical `SCM_RIGHTS` reply path. What remains
   unverified is logind's `TakeDevice` authorisation and DRM master
   semantics on a seated host — not samvada's ability to receive an
   fd, which is now demonstrated.

---

## Verdict

Four defects found, fixed and pinned — including **AUDIT-4, which
meant the headline 1.0 capability did not work at all**. The native
path works against a real bus, matches the C shim on every error
code, and now demonstrably receives a file descriptor.

The reply-forgery question is **closed**: a hostile bus peer cannot
forge a reply samvada accepts, verified structurally and live. What
that audit also established is less comfortable and is recorded
above — the protection is entirely the bus's, samvada's one
contribution was incidental, and matching libsystemd here buys
nothing because libsystemd does not check either.

**1.0.0 remains a first stable release, not an audited-hardened
one.** Resource exhaustion and build/supply-chain were still not
covered, the consumer-validation lane never cleared, and the
defence-in-depth work is scheduled rather than done. `SECURITY.md`
says so, and the C shim is retained as a fallback for exactly this
reason.

The strongest argument for that caution is AUDIT-4 itself: a defect
that survived five releases, a full hand audit, a differential
harness and 400-plus passing tests, because every test that could
have caught it took the error branch instead.

## Cross-references

- [`docs/audit/2026-09-09-audit.md`](2026-09-09-audit.md) — the C-shim-era audit
- [ADR-0003](../adr/0003-native-cyrius-dbus.md), [ADR-0004](../adr/0004-session-control-lifecycle-and-signal-visibility.md)
- [`docs/development/roadmap.md`](../development/roadmap.md) — the CG lane
- `tools/differential/` — backend parity harness
