# dbus marshalling — wire format reference (v0.x scope)

This page documents the bytes that flow across the dbus socket
when samvada's v0.x C shim calls into logind. It is *not* a
general dbus tutorial — it is the slice of the spec the v0.2.0
fn-table actually exercises, written so the v1.0 pure-Cyrius
marshaller has a concrete target to replace the libsystemd
calls against.

For the canonical spec see
[D-Bus Specification §Type system](https://dbus.freedesktop.org/doc/dbus-specification.html#type-system)
and §Message format. This file extracts only the parts samvada
v0.x calls touch.

## What samvada v0.2.0 sends

Three method calls + two signal subscriptions. Every byte below
is constructed by `sd_bus_call_method` / `sd_bus_match_signal`
under the hood; we list them so the pure-Cyrius replacement
knows what to emit.

| Call | Path | Interface | Member | Sig in | Sig out |
|---|---|---|---|---|---|
| GetSessionByPID | `/org/freedesktop/login1` | `org.freedesktop.login1.Manager` | `GetSessionByPID` | `u` | `o` |
| TakeDevice | session path | `org.freedesktop.login1.Session` | `TakeDevice` | `uu` | `hb` |
| ReleaseDevice | session path | `org.freedesktop.login1.Session` | `ReleaseDevice` | `uu` | (empty) |
| PauseDevice (signal) | session path | `org.freedesktop.login1.Session` | `PauseDevice` | (recv `uus`) | — |
| ResumeDevice (signal) | session path | `org.freedesktop.login1.Session` | `ResumeDevice` | (recv `uuh`) | — |

`u` = uint32, `o` = object_path (utf-8 string with stricter
grammar), `h` = unix_fd (transmitted as a 4-byte index into the
auxiliary fd array carried in `SCM_RIGHTS`), `b` = boolean
(stored as uint32, 0 or 1), `s` = utf-8 string.

## Header fields actually populated

Every method-call message carries a fixed header followed by a
header-fields array. samvada's calls populate exactly these
fields:

| Field code | Name | Type | Required | Notes |
|---|---|---|---|---|
| 1 | PATH | object_path | yes | `/org/freedesktop/login1` or session path |
| 2 | INTERFACE | string | yes | `org.freedesktop.login1.{Manager,Session}` |
| 3 | MEMBER | string | yes | `GetSessionByPID` / `TakeDevice` / `ReleaseDevice` |
| 6 | DESTINATION | string | yes | always `org.freedesktop.login1` |
| 7 | SENDER | string | filled by bus | unique name like `:1.42` |
| 8 | SIGNATURE | signature | when args | `u`, `uu`, etc. |
| 9 | UNIX_FDS | uint32 | when fd in | only on TakeDevice reply |

Field codes 4 (REPLY_SERIAL) and 5 (ERROR_NAME) appear on
replies; samvada's pure-Cyrius marshaller will need to read
them but doesn't construct them.

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
  body_len: <u32>     ; "5" = 4 (h) + 1 (b padded to 4) actually 8
  serial:   <u32>     ; matches request's serial in REPLY_SERIAL
  fields_len: <u32>   ; size of the (yv) array

header fields array (aligned 8):
  (1) REPLY_SERIAL (u) = <request serial>
  (6) DESTINATION (s)  = ":1.42"
  (7) SENDER (s)       = ":1.0"   ; logind's unique name
  (8) SIGNATURE (g)    = "hb"
  (9) UNIX_FDS (u)     = 1

body (aligned 8 from header end):
  fd_index: <u32>      ; usually 0 (first fd in aux array)
  pad:      <u32 = 0>  ; align to 4 for the bool (already 4-aligned)
  bool:     <u32>      ; 0 if active, 1 if inactive (paused)

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

## SCM_RIGHTS — fd passing

logind's `TakeDevice` returns a real file descriptor with DRM
master delegated. dbus carries it as `SCM_RIGHTS` ancillary data
on the unix socket (see `unix(7)` and dbus spec §Sending Unix
File Descriptors).

samvada's v0.2.0 path receives the fd via libsystemd's
`sd_bus_message_read("...h...")` which pops the fd from the
internal aux array. The C shim then `dup`s before unref'ing the
message — without the dup, the fd's lifetime ends when sd-bus
frees the message, and the consumer's drm fd silently closes.

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
profile to authenticate to the bus. Wire bytes for the
`EXTERNAL` mechanism (which uses the connecting uid as the
credential — sufficient for system bus on Linux):

```
client -> server: <NUL>                         ; one zero byte
client -> server: AUTH EXTERNAL <hex(uid)>\r\n
server -> client: OK <guid>\r\n
client -> server: NEGOTIATE_UNIX_FD\r\n          ; required for h
server -> client: AGREE_UNIX_FD\r\n
client -> server: BEGIN\r\n
```

After `BEGIN`, the client emits a `Hello` method call
(`/org/freedesktop/DBus`, `org.freedesktop.DBus.Hello`) and the
bus replies with the unique name (`:1.NN`). Only after that is
samvada's `GetSessionByPID` call legal.

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
- **Generic method dispatch** — only the four logind members
  are wrapped. A pure-Cyrius `dbus_call_method(dst, path, iface,
  member, in_sig, ..., out_sig, ...)` is nice-to-have but not
  required for the v1.0 ship.

## References

- [D-Bus Specification](https://dbus.freedesktop.org/doc/dbus-specification.html)
- [logind D-Bus API (`org.freedesktop.login1`)](https://www.freedesktop.org/software/systemd/man/org.freedesktop.login1.html)
- `unix(7)`, `recvmsg(2)`, `cmsg(3)` — fd-passing primitives.
- `lib/syscalls_x86_64_linux.cyr` — Linux syscall numbers samvada uses.
