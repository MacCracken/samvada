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
| Protocol state machine | ❌ **not covered** — notably reply-forgery |
| Public API contract | ⚠️ partial — via the differential harness only |
| Build / supply chain | ❌ **not covered** |

**Four of seven dimensions are unaudited or partial.** They are
listed as outstanding below rather than quietly omitted.

---

## Summary

| Severity | Count | Disposition |
|---|---|---|
| HIGH | 2 | Fixed in 1.0.0 |
| MEDIUM | 1 | Fixed in 1.0.0 |
| **Total** | **3** | |

All three are the same defect class: **the unmarshaller trusted
wire-supplied lengths without checking them against the bytes
actually present.**

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
2. **Reply forgery.** samvada correlates replies on `REPLY_SERIAL`
   alone. Whether a hostile peer on the bus can forge a reply that
   samvada accepts — and what libsystemd does that samvada does not
   (SENDER checking) — was not examined. **This is the single most
   valuable thing to audit next.**
3. **fd lifecycle, re-audit.** 0.8.0 fixed a `MSG_CTRUNC` leak and an
   out-of-bounds read here, and descriptor-counting tests exist, but
   the native fd queue added in N3 has not been audited end to end.
4. **Build and supply chain** for the post-cutover tree.
5. **Anything requiring a seated session** — a `TakeDevice` that
   actually transfers a descriptor has never run, by anyone.

---

## Verdict

The three defects found are fixed and pinned. The native path works
against a real bus and matches the C shim on every error code.

But **1.0.0 is a first stable release, not an audited-hardened
one.** Four of seven planned dimensions were not covered, the
consumer-validation lane never cleared, and the reply-forgery
question is open. `SECURITY.md` says so, and the C shim is retained
as a fallback for exactly this reason.

## Cross-references

- [`docs/audit/2026-09-09-audit.md`](2026-09-09-audit.md) — the C-shim-era audit
- [ADR-0003](../adr/0003-native-cyrius-dbus.md), [ADR-0004](../adr/0004-session-control-lifecycle-and-signal-visibility.md)
- [`docs/development/roadmap.md`](../development/roadmap.md) — the CG lane
- `tools/differential/` — backend parity harness
