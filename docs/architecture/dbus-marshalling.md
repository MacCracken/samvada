# dbus marshalling — wire format reference (v0.x scope)

This page documents the bytes that flow across the dbus socket
when samvada's v0.x C shim calls into logind. It is *not* a
general dbus tutorial — it is the slice of the spec the v0.5.1
fn-table actually exercises, written so the v1.0 pure-Cyrius
marshaller has a concrete target to replace the libsystemd
calls against.

For the canonical spec see
[D-Bus Specification §Type system](https://dbus.freedesktop.org/doc/dbus-specification.html#type-system)
and §Message format. This file extracts only the parts samvada
v0.x calls touch.

Every member name, signature and argument direction below was
**verified against a live `systemd-logind`** (systemd 261) with
`busctl introspect` rather than transcribed from the man page —
see [§Verification against logind](#verification-against-logind)
for the exact commands and the raw output.

## What samvada v0.5.1 sends

Five method calls, one acknowledgement it is *obliged* to send
but does not yet, and two signals it does not yet receive. Every
byte below is constructed by `sd_bus_call_method` /
`sd_bus_match_signal` under the hood; we list them so the
pure-Cyrius replacement knows what to emit.

| Call | Path | Interface | Member | Sig in | Sig out | Wired in 0.5.1 |
|---|---|---|---|---|---|---|
| GetSessionByPID | `/org/freedesktop/login1` | `org.freedesktop.login1.Manager` | `GetSessionByPID` | `u` | `o` | yes — `sb_get_session_path` |
| TakeControl | session path | `org.freedesktop.login1.Session` | `TakeControl` | `b` | (empty) | yes — `sb_take_control`, slot +72 |
| TakeDevice | session path | `org.freedesktop.login1.Session` | `TakeDevice` | `uu` | `hb` | yes — `sb_take_device` |
| ReleaseDevice | session path | `org.freedesktop.login1.Session` | `ReleaseDevice` | `uu` | (empty) | yes — `sb_release_device` |
| ReleaseControl | session path | `org.freedesktop.login1.Session` | `ReleaseControl` | (empty) | (empty) | yes — `sb_release_control`, slot +80 |
| PauseDeviceComplete | session path | `org.freedesktop.login1.Session` | `PauseDeviceComplete` | `uu` | (empty) | **no** — see §[PauseDevice / ResumeDevice](#pausedevice--resumedevice--the-signals-samvada-does-not-yet-receive) |
| PauseDevice (signal) | session path | `org.freedesktop.login1.Session` | `PauseDevice` | (recv `uus`) | — | **no handler** — slot +48 is populated by the shim but dispatched by nothing |
| ResumeDevice (signal) | session path | `org.freedesktop.login1.Session` | `ResumeDevice` | (recv `uuh`) | — | **no handler** — same |

`u` = uint32, `o` = object_path (utf-8 string with stricter
grammar), `h` = unix_fd (transmitted as a 4-byte index into the
auxiliary fd array carried in `SCM_RIGHTS`), `b` = boolean
(stored as uint32, 0 or 1), `s` = utf-8 string.

**Type fence for the marshaller.** Split by direction, because
the writer and the reader are separate pieces of code and only
one of them needs each type:

- **Sent** (the marshaller must *encode* these): `u`, `b`.
  `b` joined the sent set in 0.5.1 with `TakeControl(b force)`;
  before that it appeared only as `TakeDevice`'s second return
  value, and this document listed it as receive-only. A writer
  built to the pre-0.5.1 fence cannot emit `TakeControl` at all.
- **Received** (the reader must *decode* these): `o`, `h`, `b`,
  `s`.
- **Empty** — `ReleaseControl` takes no arguments; its
  in-signature is the empty signature, and its body is
  0 bytes. Per the spec's header-field table, SIGNATURE (code 8)
  is optional and "if omitted, it is assumed to be the empty
  signature", so a zero-length body may be sent with the field
  absent. `TakeControl`, `ReleaseDevice`, `ReleaseControl` and
  `PauseDeviceComplete` all *reply* with an empty body the same
  way — the marshaller's reader must accept a METHOD_RETURN with
  no SIGNATURE field and stop, rather than treating the missing
  field as malformed.

### Required call order

logind enforces this sequence. Skipping a step does not degrade
gracefully — it fails the *next* step, with an error that names
the wrong thing:

```
connect
  -> SASL EXTERNAL (+ NEGOTIATE_UNIX_FD)
  -> Hello                                   ; unique name :1.NN
  -> GetSessionByPID(pid) -> session path
  -> TakeControl(false)                      ; MANDATORY
  -> TakeDevice(major, minor) -> (fd, inactive)
     ...
     ( PauseDevice(major, minor, "pause") signal
         -> PauseDeviceComplete(major, minor) )   ; ack, NOT WIRED
     ( ResumeDevice(major, minor, fd) signal )
     ...
  -> ReleaseDevice(major, minor)
  -> ReleaseControl()
  -> close
```

Three ordering constraints are load-bearing:

1. **`NEGOTIATE_UNIX_FD` before `BEGIN`** — without it the bus
   refuses to carry `h`, and `TakeDevice`'s reply cannot be
   delivered at all.
2. **`TakeControl` before `TakeDevice`** — see the next section.
   This is the constraint samvada 0.2.0–0.5.0 violated.
3. **`PauseDeviceComplete` before logind's pause timeout** —
   see §[PauseDevice / ResumeDevice](#pausedevice--resumedevice--the-signals-samvada-does-not-yet-receive).

Everything down to and including `TakeControl` lives in
`samvada_init()` (`src/samvada.cyr`: open bus →
`GetSessionByPID` → `TakeControl`, each step aborting init on a
negative rc); the tail lives in `samvada_release()`
(`ReleaseControl` through slot +80, then close bus). The middle
— `TakeDevice` / `ReleaseDevice` — is the consumer's to drive,
one call per device, between those two.

## Session control — TakeControl / ReleaseControl

```
org.freedesktop.login1.Session.TakeControl(b force) -> ()
org.freedesktop.login1.Session.ReleaseControl()     -> ()
```

logind will not delegate a device to a client that has not
claimed the session. `TakeControl` makes the **calling bus
connection** the session's *controller*; the claim is bound to
that connection's unique name, not to the pid, not to the
session, and not to the process. A second connection from the
same process is not in control, and neither is a `fork`ed child
that opens its own bus. This is why samvada issues `TakeControl`
on the same `sd_bus*` it later calls `TakeDevice` on, inside
`samvada_init()` (`src/samvada.cyr`, immediately after the
session path resolves).

Without it, **every** `TakeDevice` fails:

```
org.freedesktop.login1.NotInControl: You are not in control of this session
```

That is not a corner case — it is unconditional, which is why
samvada 0.2.0 through 0.5.0 could never take a device in any
environment. Two properties of that failure make it expensive to
diagnose from the client side, and the marshaller should expect
both:

- The error is raised by the **control check, before any
  device-level validation**. Verified live: `TakeDevice(0, 0)`
  — a major/minor pair that names nothing — still returns
  `NotInControl`, so a nonsense device number is not what the
  message is about.
- libsystemd's error map translates
  `org.freedesktop.login1.NotInControl` to **`-EINVAL` (-22)**,
  which is exactly the value samvada returns for its own
  null-table and bad-kind rejections. A consumer branching on
  the magnitude cannot tell "you passed me a bad table" from
  "you never took control". The pure-Cyrius marshaller reads
  ERROR_NAME (**header field 4**) off the wire directly and should
  surface the *name*, not just a squashed errno.

**The `force` argument.** `b force` selects whether to steal
control from an existing controller. samvada sends `false` (`0`)
— `sb_take_control` in `deps/samvada_main.c` passes a literal
`0` — so a session already under another compositor's control is
left alone. Verified live, the contention error is *not* one of
logind's named errors: a second connection calling
`TakeControl(false)` against a controlled session gets
`System.Error.EBUSY` → **`-16` (-EBUSY)**. That is the same
value `samvada_init()` returns for re-init-without-release, a
second name/errno collision of the same shape as the
`NotInControl` one above. `force = true` is root-only
(`org.freedesktop.login1(5)`: "If the *force* argument is set
(root only), an existing controller is kicked out and
replaced"); live, an unprivileged connection setting it is
refused with `org.freedesktop.DBus.Error.AccessDenied`, "Only
owner of session may take control". `force` is deliberately not
exposed in samvada's public API: adding a parameter would be an
ABI change, not a wire change.

`TakeControl` itself is also uid-gated — the caller's effective
uid must be the session's user or root — so a `NotInControl`
downstream can additionally mean "the `TakeControl` reply was an
error nobody checked". `samvada_init()` checks it: a negative rc
from slot +72 aborts init, calls `samvada_release()` and returns
the sd-bus errno unchanged.

The body is one four-byte word:

```
body (aligned 8 from header end):
  bool: <u32>          ; 0 = do not steal, 1 = force
                       ; total body_len = 4, SIGNATURE = "b"
```

`ReleaseControl` is the teardown half and is the simplest
message samvada emits: no arguments, empty in-signature, empty
reply body, `body_len = 0`, and **no SIGNATURE header field**.
Two things follow from `org.freedesktop.login1(5)`:

- It "also releases all devices for which the controller
  requested ownership via `TakeDevice()`", so the `ReleaseDevice`
  step in the order above is a courtesy, not a requirement. The
  fd samvada handed the consumer is still the consumer's to
  `close()` — releasing on logind's side does not close the
  duplicate.
- "Closing the D-Bus connection implicitly releases control as
  well", so the explicit call is an optimisation rather than an
  obligation. `samvada_release()` calls it through slot +80 and
  deliberately **ignores the return value** before closing the
  bus, because the close would have released control anyway.

**Backward compatibility.** Slots +72 / +80 were appended after
`kind` at +64 per ADR-0002, so a pre-0.5.1 fn-table is still
structurally readable — its take_control slot is simply null.
`samvada_init()` treats that as fatal (`-38`, `-ENOSYS`) rather
than proceeding, because proceeding means a guaranteed
`NotInControl` later, attributed to the wrong call.

## Header fields actually populated

Every method-call message carries a fixed header followed by a
header-fields array. samvada's calls populate exactly these
fields:

| Field code | Name | Type | Required | Notes |
|---|---|---|---|---|
| 1 | PATH | object_path | yes | `/org/freedesktop/login1` or session path |
| 2 | INTERFACE | string | yes | `org.freedesktop.login1.{Manager,Session}` |
| 3 | MEMBER | string | yes | `GetSessionByPID` / `TakeControl` / `TakeDevice` / `ReleaseDevice` / `ReleaseControl` |
| 6 | DESTINATION | string | yes | always `org.freedesktop.login1` |
| 7 | SENDER | string | filled by bus | unique name like `:1.42` — this is the identity `TakeControl` binds the controller claim to |
| 8 | SIGNATURE | signature | when args | `u`, `uu`, `b` — **absent entirely** on `ReleaseControl`, which has no body |
| 9 | UNIX_FDS | uint32 | when fds attached | on the `TakeDevice` reply, and on the `ResumeDevice` signal (`uuh`) if it is ever delivered |

Field codes **4 (ERROR_NAME)** and **5 (REPLY_SERIAL)** appear on
replies; samvada's pure-Cyrius marshaller will need to read
them but doesn't construct them. ERROR_NAME in particular is not
optional to parse: §[Session control](#session-control--takecontrol--releasecontrol)
shows two distinct logind failures that squash to errno values
samvada already uses for its own local rejections, so the name
is the only thing that disambiguates them.

## Body alignment

Every type has a natural alignment within the message body. Get
this wrong and the peer sd-bus rejects the message with
`org.freedesktop.DBus.Error.InvalidArgs` (and the libsystemd
client rejects locally before sending — easier to debug).

| Type code | Type | Alignment | Size |
|---|---|---|---|
| `y` | byte | 1 | 1 |
| `b` | boolean | 4 | 4 (uint32, 0/1) |
| `n`, `q` | int16, uint16 | 2 | 2 |
| `i`, `u` | int32, uint32 | 4 | 4 |
| `x`, `t` | int64, uint64 | 8 | 8 |
| `d` | double | 8 | 8 |
| `s`, `o` | string, object_path | 4 | uint32 length + UTF-8 + NUL |
| `g` | signature | 1 | uint8 length + ASCII + NUL |
| `h` | unix_fd | 4 | 4 (index into aux fd array) |
| `a` | array | 4 (length) + element align | 4 + content |
| `(...)` | struct | 8 | concatenation, struct-aligned |
| `v` | variant | 1 (sig) + value align | sig + value |

Padding bytes between fields are 0x00. The header fields array
itself is aligned to 8 (struct alignment for `(yv)` entries).

## TakeDevice reply walk-through

This is the one call where every wire-format trick shows up at
once — fd passing, multi-type read, padding. The pure-Cyrius
marshaller's first end-to-end test should be reproducing this
exactly.

Wire bytes (logical, pre-endian-swap; sd-bus writes little-endian
in practice):

```
header (16 bytes fixed):
  endian: 'l' (0x6C) for LE
  type:   2  (METHOD_RETURN)
  flags:  0
  proto:  1
  body_len: <u32>     ; 8 = 4 (h) + 4 (b) -- `b` is a uint32 on the wire
  serial:   <u32>     ; matches request's serial in REPLY_SERIAL
  fields_len: <u32>   ; size of the (yv) array

header fields array (aligned 8):
  (5) REPLY_SERIAL (u) = <request serial>
  (6) DESTINATION (s)  = ":1.42"
  (7) SENDER (s)       = ":1.0"   ; logind's unique name
  (8) SIGNATURE (g)    = "hb"
  (9) UNIX_FDS (u)     = 1

body (aligned 8 from header end):
  fd_index: <u32>      ; bytes 0-3. usually 0 (first fd in aux array)
  bool:     <u32>      ; bytes 4-7. 0 if active, 1 if inactive (paused)
                       ; NO padding: `b` aligns to 4 and byte 4 is already
                       ; 4-aligned, so the body is exactly 8 bytes

ancillary data (SCM_RIGHTS):
  cmsg with one fd
```

Three traps the pure-Cyrius reader must handle:

1. **The fd is not in the message body** — body[0..4] is an
   *index* into the SCM_RIGHTS array on the unix socket, not a
   raw fd value. The sd-bus reader does the lookup; ours will
   too.
2. **`b` is uint32 on the wire even though it's 1 byte
   semantically** — 4-byte alignment, 4 bytes of storage,
   value strictly 0 or 1.
3. **REPLY_SERIAL must match the request's SERIAL** — out-of-
   order replies are valid in dbus but logind serializes per
   session, so we'll see them in order in practice; the
   marshaller still has to correlate by serial number, not by
   arrival order.

## PauseDevice / ResumeDevice — the signals samvada does not yet receive

```
org.freedesktop.login1.Session.PauseDevice(u major, u minor, s type)   ; signal
org.freedesktop.login1.Session.ResumeDevice(u major, u minor, h fd)    ; signal
org.freedesktop.login1.Session.PauseDeviceComplete(u major, u minor) -> ()
```

**State the gap first, because the rest of this section is
aspirational.** As of 0.5.1 samvada delivers neither signal.
Slot +48 (`sb_subscribe_pause_resume`) and slot +56
(`sb_unsubscribe`) are populated by the C shim in
`deps/samvada_main.c`, but **no Cyrius code dispatches either
one** — `grep samvada_slot_subscribe_pause_resume src/` finds
only the offset declaration in `src/samvada_ffi.cyr`, never a
call — and there is no public API through which a consumer could
install a handler. `samvada_pump_signals()` therefore runs
`sd_bus_process` against a connection with no registered
callback: it drains and discards. `PauseDeviceComplete` is not
wired at all, on either side of the FFI boundary. Any statement
elsewhere that samvada "handles pause/resume" describes the
shim's surface area, not a working path.

What the wire actually does, for the marshaller that closes the
gap:

**Delivery.** `org.freedesktop.login1(5)`: "The active session
controller *exclusively* gets `PauseDevice()` and
`ResumeDevice()` events for any device it requested via
`TakeDevice()`." The signals follow the `TakeControl` claim, so
they arrive on the same connection that took control — one more
reason the claim is per-connection and not per-process. The same
page adds that signals "are only emitted on objects referencing
a specific session ID, not on the
`/org/freedesktop/login1/session/self` or `…/auto` convenience
objects": a match rule or path filter written against `self`
matches nothing. `sb_subscribe_pause_resume` takes the session
path as a parameter and would be handed the concrete one
`GetSessionByPID` resolved into `_samvada_sess`, so the match it
*would* install is correctly shaped. Nothing installs it.

**`PauseDevice`'s `type` string** is one of three values, and
only one of them is a question:

| `type` | Meaning | Client must reply? |
|---|---|---|
| `pause` | logind is about to revoke access and "grants you a limited amount of time to pause the device" | **yes** — `PauseDeviceComplete(major, minor)` |
| `force` | the device was already paused; the signal is an asynchronous notification after the fact | no |
| `gone` | the device was unplugged; no further notifications, and no `ReleaseDevice` is needed | no |

**`PauseDeviceComplete(uu) -> ()`** is the acknowledgement for
the `pause` case: same `uu` body shape as `TakeDevice` and
`ReleaseDevice` (two 4-byte words, no padding between them,
`SIGNATURE = "uu"`), empty reply body. It is the client saying
"I have dropped DRM master, the VT switch may proceed". Until it
arrives — or until logind's internal timeout expires — the VT
switch is held; the man page notes logind "is free to send a
forced `PauseDevice()` if you do not respond in a timely
manner", and that "forced signals (or after an internal timeout)
are automatically completed by `systemd-logind` asynchronously".
So a client that never acknowledges does not deadlock the
machine, but it does stall every VT switch for the length of
that timeout and then gets its device yanked mid-frame instead
of at a point of its choosing. A compositor that ignores
`PauseDevice` is a compositor that makes VT switching feel
broken.

Like `TakeControl`, `PauseDeviceComplete` is gated on control —
verified live, a connection holding control but no device gets
`org.freedesktop.login1.DeviceNotTaken` (`-EINVAL`, -22), while
the same call from a connection with no control gets
`org.freedesktop.login1.NotInControl`. It is not usable as a
cheap liveness probe.

**`ResumeDevice(uuh)`** carries a *new* fd, not the old one:
"You should switch to the new descriptor and close the old one.
They are not guaranteed to have the same underlying open file
descriptor in the kernel." That `h` makes a signal an
fd-bearing message, which is why the UNIX_FDS header field is
not exclusive to the `TakeDevice` reply, and why the
[SCM_RIGHTS](#scm_rights--fd-passing) receive path below has to
run for signals too — including the `F_DUPFD_CLOEXEC` step, or
the resumed fd's lifetime ends with the signal message.

## SCM_RIGHTS — fd passing

logind's `TakeDevice` returns a real file descriptor with DRM
master delegated. dbus carries it as `SCM_RIGHTS` ancillary data
on the unix socket (see `unix(7)` and dbus spec §Sending Unix
File Descriptors).

samvada's v0.x path receives the fd via libsystemd's
`sd_bus_message_read("...h...")` which pops the fd from the
internal aux array. `sb_take_device` then duplicates it before
unref'ing the message — without that, the fd's lifetime ends
when sd-bus frees the message, and the consumer's drm fd
silently closes.

The duplication is `fcntl(fd, F_DUPFD_CLOEXEC, 0)`, **not**
`dup(2)`. `dup(2)` clears `FD_CLOEXEC` on the new descriptor, so
the pre-0.5.1 code handed the consumer a DRM-master fd that
survived any `execve` and leaked master rights into unrelated
children. The wire format does not carry the flag either way —
`SCM_RIGHTS` delivery honours the receiver's `MSG_CMSG_CLOEXEC`,
and the duplicate is a purely local decision — so the
pure-Cyrius `recvmsg(2)` loop has to make the same choice
explicitly: pass `MSG_CMSG_CLOEXEC` on the `recvmsg` and use
`F_DUPFD_CLOEXEC` for any duplicate it hands out.

For v1.0 pure-Cyrius the `recvmsg(2)` loop must:
1. Set up an `iovec` for the message bytes.
2. Set up a `cmsghdr` buffer sized for `CMSG_SPACE(sizeof(int) * N)`.
3. After `recvmsg`, walk `CMSG_FIRSTHDR / CMSG_NXTHDR`, look
   for `cmsg_level == SOL_SOCKET && cmsg_type == SCM_RIGHTS`,
   copy the int[] out of `CMSG_DATA(cmsg)`.
4. Index by the `h` field in the message body.

If the reader closes its receive socket before consuming the
fd-bearing cmsg, the kernel closes the fd. (libsystemd handles
this; the pure-Cyrius marshaller has to mirror it.)

## SASL EXTERNAL auth (connect path)

Before any method calls, the client speaks dbus's tiny SASL
profile to authenticate to the bus.

> **Corrected in 0.7.1 against captured bytes.** The ping-pong
> exchange this section described until now is *legal*, but it is
> not what the reference client emits, and a reader built to it
> **hangs against a real bus**. See
> [`tests/fixtures/dbus/MANIFEST.md`](../../tests/fixtures/dbus/MANIFEST.md).

What libsystemd actually sends is **one pipelined 48-byte write**,
with an *empty-credential* `DATA` step rather than an inline
hex-encoded uid:

```
client -> server:                                ; ONE write, 48 bytes
  <NUL> AUTH EXTERNAL\r\n DATA\r\n NEGOTIATE_UNIX_FD\r\n BEGIN\r\n

server -> client:                                ; ONE read, 58 bytes
  DATA\r\n OK <guid>\r\n AGREE_UNIX_FD\r\n
```

Two consequences for the native reader, both load-bearing:

1. **The server's three lines arrive in a single read.** A reader
   that issues one read per expected line blocks forever on the
   second one. The reply parser must be **line-oriented over a
   buffer**, not read-oriented.
2. **The client need not wait between steps.** Pipelining the
   whole handshake is what the reference implementation does, and
   it removes three round trips.

The older form remains valid and is kept here because a server
may still be driven that way:

```
client -> server: <NUL>
client -> server: AUTH EXTERNAL <hex(ascii(uid))>\r\n
server -> client: OK <guid>\r\n
client -> server: NEGOTIATE_UNIX_FD\r\n
server -> client: AGREE_UNIX_FD\r\n
client -> server: BEGIN\r\n
```

After `BEGIN`, the client emits a `Hello` method call
(`/org/freedesktop/DBus`, `org.freedesktop.DBus.Hello`) and the
bus replies with the unique name (`:1.NN`). Only after that is
samvada's `GetSessionByPID` call legal — this handshake is the
first two lines of
§[Required call order](#required-call-order), and the unique
name it yields is the identity `TakeControl` binds the
controller claim to.

`<hex(uid)>` is the ASCII-encoded uid, hex-encoded again — uid
1000 sends `31303030` (the ASCII of "1000"), not `0x3E8`.

## What samvada v0.x does NOT touch

Documented here so the v1.0 marshaller knows what it can
*defer* even after the C shim retires:

- **Properties.Get/Set/GetAll** — `org.freedesktop.DBus.Properties`
  isn't in the v0.x slot table. v0.3+ may add it; v1.0 minimum
  is whatever v0.x needed.
- **Object introspection** — `org.freedesktop.DBus.Introspectable.Introspect`
  returns XML; samvada hard-codes paths instead.
- **Session bus** — only the system bus is connected. Per-user
  bus needs different auth (DBUS_SESSION_BUS_ADDRESS, often a
  unix-abstract socket in `$XDG_RUNTIME_DIR`).
- **Generic method dispatch** — only five logind members are
  wrapped (`GetSessionByPID`, `TakeControl`, `TakeDevice`,
  `ReleaseDevice`, `ReleaseControl`), plus one wildcard signal
  match that nothing installs. A pure-Cyrius
  `dbus_call_method(dst, path, iface, member, in_sig, ...,
  out_sig, ...)` is nice-to-have but not required for the v1.0
  ship.
- **The rest of the Session interface** — `Activate`, `Lock` /
  `Unlock`, `SetType` / `SetClass` / `SetDisplay` / `SetTTY`,
  `SetBrightness`, `Kill`, `Terminate` are all present on the
  live object (see the introspection below) and all out of
  scope. Note that `SetType`, `SetDisplay` and `SetTTY` are
  gated on `TakeControl` the same way `TakeDevice` is, so a
  future minor that adds any of them inherits the ordering
  constraint rather than introducing a new one.

Explicitly **in** scope but still missing, so it does not get
filed under "deferred" by accident: `PauseDeviceComplete`, and
any dispatch path for the two signals. See
§[PauseDevice / ResumeDevice](#pausedevice--resumedevice--the-signals-samvada-does-not-yet-receive).

## Verification against logind

Every signature in this document was read off a running
`systemd-logind` (systemd 261) rather than transcribed, on
2026-09-09:

```sh
# resolve the concrete session path — NOT .../session/self,
# which emits no signals (see the signals section)
busctl call org.freedesktop.login1 /org/freedesktop/login1 \
    org.freedesktop.login1.Manager GetSessionByPID u $$
# -> o "/org/freedesktop/login1/session/_32"

busctl introspect org.freedesktop.login1 \
    /org/freedesktop/login1/session/_32 \
    org.freedesktop.login1.Session
```

That `_32` is not a session number — it is logind's escaping of
the session id `"2"`. Object-path elements may not begin with a
digit, so logind emits `_` followed by the hex of the offending
byte (`'2'` = `0x32`). The marshaller never has to *construct*
these: `GetSessionByPID` returns the escaped path as an `o` and
samvada copies the bytes verbatim into `_samvada_sess`, then
hands the same buffer to every later call. Any future code that
builds a session path from a session *name* would have to
implement the escaping; nothing in v0.x does, and nothing should.

The rows this document depends on, verbatim (whitespace
compressed; the columns are name, type, in-signature, result,
flags):

```
.PauseDeviceComplete    method   uu        -     -
.ReleaseControl         method   -         -     -
.ReleaseDevice          method   uu        -     -
.TakeControl            method   b         -     -
.TakeDevice             method   uu        hb    -
.PauseDevice            signal   uus       -     -
.ResumeDevice           signal   uuh       -     -
```

`--xml-interface` additionally confirms the argument names and
directions used above: `TakeControl(in b force)`,
`TakeDevice(in u major, in u minor, out h fd, out b inactive)`,
`PauseDeviceComplete(in u major, in u minor)`,
`PauseDevice(u major, u minor, s type)`,
`ResumeDevice(u major, u minor, h fd)`.

The failure modes quoted in this document were reproduced on the
same bus:

| Call | Precondition | Result |
|---|---|---|
| `TakeDevice(0, 0)` | no `TakeControl` | `org.freedesktop.login1.NotInControl` → `-22` |
| `TakeControl(false)` | uncontrolled session | success (`sd_bus_call_method` returns `1`, not `0`) |
| `TakeControl(false)` | second connection, session already controlled | `System.Error.EBUSY` → `-16` |
| `TakeControl(true)` | same, unprivileged caller | `org.freedesktop.DBus.Error.AccessDenied` |
| `TakeDevice(0, 0)` | after `TakeControl` | `org.freedesktop.DBus.Error.AccessDenied` → `-13` — past the control check, blocked by the seatless-session gate |
| `PauseDeviceComplete(0, 0)` | control, no device | `org.freedesktop.login1.DeviceNotTaken` → `-22` |
| `ReleaseControl()` | holding control | success (returns `1`) |

The `-13` / `AccessDenied` on the fifth row is the pre-existing
hardware gate, not a marshalling defect: the probing session had
no seat (`loginctl list-sessions` showed `SEAT` empty), and
`TakeDevice` "only works on devices that are attached to the
seat of the given session". A seated end-to-end run is still
unverified and remains a v1.0 gate — nothing in this document
should be read as claiming the full path has been exercised
against real DRM hardware.

The two success rows above carry one detail worth writing into
the marshaller's contract: **`sd_bus_call_method` returns a
positive `1` on success, not `0`.** Passing that through unchanged
is what made `samvada_session_release_device` return `1` against
a documented `0 | -err` contract before 0.5.1; both
`sb_release_device` (C) and `samvada_session_release_device`
(Cyrius) now normalise any non-negative rc to `0`. A pure-Cyrius
marshaller that owns the whole path has no libsystemd convention
to launder and should simply return `0`.

## References

- [D-Bus Specification](https://dbus.freedesktop.org/doc/dbus-specification.html)
- [logind D-Bus API (`org.freedesktop.login1`)](https://www.freedesktop.org/software/systemd/man/org.freedesktop.login1.html)
  — also `man 5 org.freedesktop.login1` locally; every quoted
  sentence in the session-control and signals sections is from
  the §Methods / §Signals prose there.
- `busctl(1)` — `introspect`, `--xml-interface`, `call`,
  `monitor`. The reproduction commands are in
  §[Verification against logind](#verification-against-logind).
- `unix(7)`, `recvmsg(2)`, `cmsg(3)`, `fcntl(2)` (`F_DUPFD_CLOEXEC`)
  — fd-passing primitives.
- `lib/syscalls_x86_64_linux.cyr` — Linux syscall numbers samvada uses.
- `deps/samvada_main.c` — the wrappers named throughout
  (`sb_take_control`, `sb_release_control`, `sb_take_device`, …)
  and the slot offsets they are installed at.
- `src/samvada.cyr` — the Cyrius side: `samvada_init()` owns the
  `GetSessionByPID` → `TakeControl` half of the order,
  `samvada_release()` owns `ReleaseControl` → close.
- `src/samvada_ffi.cyr` — the slot table, including the +72/+80
  append that carries session control.
