# dbus golden corpus — provenance

Captured with [`tools/dbus_tap.py`](../../../tools/dbus_tap.py), a transparent
AF_UNIX relay, by pointing a libsystemd client at it via
`DBUS_SYSTEM_BUS_ADDRESS`. Decoded and checked with
[`tools/dbus_decode.py`](../../../tools/dbus_decode.py).

**Why this exists, and why now.** These bytes are the reference the native
Cyrius marshaller (N3/N4) is built and tested against. They have to be
captured **while the libsystemd C shim still exists**, because the shim is
deleted at the v1.0 cutover (roadmap N7) and the reference implementation
goes with it.

## Provenance

| | |
|---|---|
| **Host** | `Linux 7.2.3-arch1-3 x86_64` |
| **systemd** | `261.2-1` |
| **broker** | `dbus-broker 37-3` |
| **Captured (UTC)** | `2026-09-09T22:57:49Z` |
| **Client** | libsystemd `sd_bus`, driving samvada's exact call sequence |
| **Session** | seatless (`Seat=""`, `vtnr=0`) — see the TakeDevice note below |

## Fixtures

| File | Bytes | Kind | sha256 (first 16) |
|---|---|---|---|
| `01-sasl-client.bin` | 48 | **REAL** | `9b46f166e17ab1d9` |
| `01-sasl-server.bin` | 58 | **REAL** | `3e5d88717d9a797e` |
| `02-hello-call.bin` | 128 | **REAL** | `2449c6d547427e9a` |
| `02-hello-reply-plus-nameacquired.bin` | 282 | **REAL** | `73a3a1768ec4c153` |
| `03-getsessionbypid-call.bin` | 156 | **REAL** | `67aaecdcb8c6165b` |
| `03-getsessionbypid-reply.bin` | 112 | **REAL** | `0c7b6cd465d115c0` |
| `04-takecontrol-call.bin` | 172 | **REAL** | `0f90c919871dbab7` |
| `04-takecontrol-reply.bin` | 64 | **REAL** | `c8e4a7d383df8075` |
| `05-takedevice-call.bin` | 176 | **REAL** | `edcc9abe3c29afaa` |
| `05-takedevice-error-reply.bin` | 148 | **REAL** | `53440a4ee68d394d` |
| `06-releasecontrol-call.bin` | 160 | **REAL** | `09e07db1477c03cf` |
| `06-releasecontrol-reply.bin` | 64 | **REAL** | `fe3e792b73c4e6e7` |
| `07-inhibit-call.bin` | 238 | **REAL** | `5469782588ecd1c6` |
| `07-inhibit-reply-with-fd-REAL.bin` | 84 | **REAL** | `e6d9b236a9ae94cd` |
| `08-takedevice-reply-SYNTHETIC.bin` | 88 | **SYNTHETIC** | `050a2a4ad7e81487` |

## REAL vs SYNTHETIC

Fourteen fixtures are **REAL** — bytes that actually crossed the socket.

One is **SYNTHETIC**: `08-takedevice-reply-SYNTHETIC.bin`, a successful
`TakeDevice` reply (`hb` + an fd). It cannot be captured here, because
`TakeDevice` needs an **active seated session** and this host has none —
`TakeDevice` returns `AccessDenied`, which is itself captured as
`05-takedevice-error-reply.bin`.

The roadmap warned that hand-assembling it from
`docs/architecture/dbus-marshalling.md` would put the same error into the
fixture *and* the implementation simultaneously. So it was **derived from
captured bytes instead**: `07-inhibit-reply-with-fd-REAL.bin` is a REAL
`METHOD_RETURN` carrying `h` plus a real `SCM_RIGHTS` cmsg, obtained from
`Manager.Inhibit`, which returns an fd and needs no seat. The synthetic is
that message with the signature `h` -> `hb` and the body extended by the
boolean. Verified: the two decode identically except for those two fields.

It is still marked SYNTHETIC. Treat any test that depends on it as weaker
evidence than the rest, and replace it the moment a seated session is
available (CG lane).

## What the capture established

Each of these contradicted or refined an assumption, and each is now
evidence rather than belief.

1. **SASL is ONE pipelined write.** The client sends 48 bytes in a single
   write: `\0AUTH EXTERNAL\r\nDATA\r\nNEGOTIATE_UNIX_FD\r\nBEGIN\r\n`,
   with an *empty-credential* `DATA` step — not the
   `AUTH EXTERNAL <hex(uid)>` ping-pong `dbus-marshalling.md` documents.
   Both forms are legal; only one is what the reference client emits.
2. **The server answers all three SASL lines in ONE 58-byte read.** A
   reader written one-read-per-line hangs against a real bus.
3. **One read can carry TWO messages** — and can also END MID-MESSAGE.
   `02-hello-reply-plus-nameacquired.bin` holds a complete `METHOD_RETURN`
   followed by a `NameAcquired` SIGNAL that is *truncated* at the buffer
   edge; its tail arrived in the next read. A framer must carry partial
   tails. This is not an edge case: it is the very first exchange.
4. **Alignment is relative to the START OF EACH MESSAGE**, not to the
   buffer. The decoder got this wrong at first and mis-read the second
   message's header — a bug that is invisible until a multi-message buffer
   appears, which is exactly what the first exchange produces.
5. **Header field order is not ascending and is not stable across message
   kinds.** Observed: `[1,3,2,6]` on the Hello call, `[5,7,6,8]` on its
   reply, `[5,6,8,9,7]` on an fd-bearing reply. Byte-equality against a
   golden buffer therefore tests "did you copy libsystemd's arbitrary
   ordering", not "is your message correct".
6. **The flags byte varies per call**: `0x00` on Hello, `0x01`
   (`NO_REPLY_EXPECTED`) on replies.
7. **The bus's own first `METHOD_RETURN` carries serial `0xFFFFFFFF`.**
   Cyrius has no unsigned type and `>>` is logical, so a signed-comparison
   bug in the header reader fires on **message one**, not in a fuzz corner.
   Use this literal as a fixture.
8. **`UNIX_FDS` (field 9) is present** on any reply carrying a descriptor,
   and the fd itself travels in `SCM_RIGHTS` ancillary data, *not* in the
   body — the body holds only a u32 index.

## Byte stability, measured

Two captures of the identical sequence on this host, client -> bus:

| Message | Result |
|---|---|
| SASL | **identical** |
| Hello call | **identical** |
| `GetSessionByPID` call | differs at **2 bytes** (offsets 152-153) — the pid argument |
| `TakeControl` call | **identical** |
| `TakeDevice` call | **identical** |
| `ReleaseControl` call | **identical** |

So the **request** side is byte-stable and can be tested by direct
comparison, masking only the pid. The **reply** side is not: it carries
the connection's unique name (`:1.NNNNN`) and bus-assigned serials, both
of which change every run. Any reply fixture used for byte-equality must
have those ranges masked.

## Exit criterion NOT met

The roadmap requires a second capture **on a different host / systemd
version**, to prove the corpus is not over-fitted to one machine. Only one
host is available here, so this is **outstanding**. What was done instead
is the same-host repeat above, which establishes which bytes are volatile
but cannot establish which are host-specific. Field ordering and the flags
byte are the likeliest to differ elsewhere, since finding 5 shows they are
implementation choices rather than spec requirements.

Until a second host is available, treat the corpus as a **regression
fixture for samvada's own encoder** rather than as a conformance oracle.
The conformance oracle is the bus itself: send the bytes and check the
reaction (roadmap N4).

## Reproducing

```sh
./tools/dbus_tap.py /tmp/tap.sock --out /tmp/capture &
DBUS_SYSTEM_BUS_ADDRESS=unix:path=/tmp/tap.sock <your client>
./tools/dbus_decode.py /tmp/capture/*.bin
```
