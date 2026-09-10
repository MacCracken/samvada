# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [1.0.0] — 2026-09-09

**Native dbus in Cyrius.** samvada speaks dbus over a raw unix
socket with no libsystemd, no C shim and no `pkg-config`. The road
[ADR-0003](docs/adr/0003-native-cyrius-dbus.md) set out at 0.5.1 is
complete: N0 through N7, seven milestones, ~1000 lines of Cyrius
replacing a C dependency at every consumer's edge.

`samvada_version()` → `(1,0,0)`.

### Added
- [`docs/audit/2026-09-09-native-audit.md`](docs/audit/2026-09-09-native-audit.md)
  — the pre-1.0 security audit of the native marshaller.
- Benchmark rows filled: native handshake **167 µs** vs libsystemd
  **494 µs**; `pump_signals` **2138 ns** idle. The `TakeDevice`
  round-trip row stays empty and says why.

### Fixed
- **AUDIT-4 (HIGH, functional) — the reply path could not receive a
  file descriptor at all.** `dbus_session_call` read replies with
  plain `sys_read`; on an `AF_UNIX` socket the kernel delivers a
  message's bytes and **discards its `SCM_RIGHTS` payload** unless
  the reader uses `recvmsg` with a control buffer. The reply framed
  and parsed correctly, its header still said `UNIX_FDS = 1`, its
  body still held a valid index — and the descriptor was gone.
  `take_device` could therefore only ever return `-EBADF`, or an
  unrelated fd left over from an earlier `pump_signals`. **Handing
  a DRM-master fd to a compositor is samvada's headline 1.0
  capability, and it did not work.**

  Measured end to end on the live bus via `Inhibit` (an fd-bearing
  logind reply that needs no seated session):
  `frame_take_fd()` **-1 → 4**.

  It survived five releases, the hand audit, the differential
  harness and 400-plus passing tests because an unseated host
  answers `TakeDevice` with `AccessDenied` and **an ERROR reply
  carries no fd** — so the fd branch was never once executed.
  `pump_signals` had always used `recvmsg` correctly; only the
  request/reply path did not, and that is the path descriptors
  arrive on.

  Fixed by reading through `dbus_sys_recv_fd_flags` and queueing the
  descriptor. The SASL reader keeps `sys_read` — no fds cross during
  authentication. Pinned by `test_reply_fd_survives_the_call_path`
  (mutation-tested: restoring `sys_read` fails it).

### Security
**Reply provenance — samvada now checks, instead of trusting the
bus to.** Two guards added to `dbus_session_call`, both of which
**exceed** the synchronous `sd_bus_call` path they replace (it
checked neither):
- **`DESTINATION` must be us.** `open_system_bus` now retains the
  unique name the bus assigns in the `Hello` reply body — which
  samvada previously read and discarded — and a matching reply is
  accepted only if its `DESTINATION` equals it. Mirrors sd-bus's
  async guard (`sd-bus.c:2790`). Deliberately permissive when the
  field is **absent**, exactly as sd-bus is, and that is
  load-bearing rather than lax: the `Hello` reply is matched before
  the bus has told us our own name. Making it strict does not fail
  the connect path, it **deadlocks** it.
- **Send-time watermark.** Bytes already pending when the request
  is written cannot be a reply to it — no peer answers before it
  receives. Mirrors sd-bus's `i = bus->rqueue_size`
  (`sd-bus.c:2448`) and closes the pre-plant window that sequential
  serials would otherwise leave open. A byte *count*, not an offset,
  because `maybe_compact()` slides the buffer.

**No `SENDER` check, deliberately.** The intuitive guard — require
`SENDER == org.freedesktop.login1` — would reject **every genuine
reply**: the broker rewrites `SENDER` to the sender's *unique* id,
so real logind replies arrive as `:1.5`. Confirmed against all four
captured logind replies in `tests/fixtures/dbus/`. Pinning logind's
unique id instead was rejected because a logind restart would
strand a long-lived consumer with a stale pin.

Live-verified end to end (unique name learned, `GetSessionByPID`
accepted, `Inhibit` fd intact) and mutation-tested: removing either
guard fails its own pin.

Three further defects found and fixed, all the same class — **the
unmarshaller trusted wire-supplied lengths without checking them
against the bytes present**:
- **AUDIT-1 (HIGH)** — a string field claiming `0xFFFFFFF0` bytes
  had that length returned to the caller with a pointer into the
  message. Measured: `str_at returned len=4294967280`.
- **AUDIT-2 (MEDIUM)** — `find_field` walked to `16 + fields_len`
  from the header; a 48-byte message claiming `fields_len = 65535`
  read ~65 KB past the end.
- **AUDIT-3 (HIGH)** — the body cursor took its end from `body_len`
  alone. Measured: `remaining = 16777215` in a 32-byte message,
  with `next_u32()` reading beyond it under attacker control.

None was reachable through samvada's own paths — the framer only
yields a message once `avail >= total`. They are filed at these
severities anyway because *"unreachable because the caller happens
to validate first"* is the exact shape of 0.5.1's two CRITICALs,
and this module ships in the consumer bundle where callers we do
not control can reach it. The one internal caller that touches
attacker-influenced data survived by luck, not design.

Fixed by making the message extent explicit and enforced in the
module (`dbus_unmarshal_set_limit`) rather than assumed of the
caller. Pinned by regression tests.

### Notes — read these before treating 1.0.0 as hardened
- **The first multi-agent audit FAILED; the reply-forgery re-run
  succeeded.** All eight agents of the original sweep terminated on
  a session limit with zero findings, so that pass was performed by
  hand instead and covered **three of seven planned dimensions**.
  The question it named as most valuable — reply forgery — was then
  re-run as a three-lane workflow with an adversarial adjudicator,
  and **that is where AUDIT-4 was found**. **Resource exhaustion and
  build/supply-chain remain NOT audited**, and the audit document
  lists them as outstanding rather than omitting them.
- **Reply forgery: audited, answer is no.** A hostile peer on the
  system bus **cannot** forge a reply samvada accepts. Verified
  structurally in dbus-broker's source and live as uid 1000 with the
  attacker given the victim's exact unique name, the correct serial
  and a spoofed `SENDER` — forged `METHOD_RETURN` and `ERROR` were
  both delivered zero times. **Severity LOW; it does not gate this
  release.**

  Two honest caveats. First, **the protection is the bus's, not
  samvada's**: samvada matches on `REPLY_SERIAL` alone and checks
  neither `SENDER` nor `DESTINATION`, so on a permissive or
  non-tracking bus — or against an attacker interposed on the
  socket — a forged reply is accepted unconditionally. A relay that
  rewrote a reply in flight made the real binary return
  `/org/freedesktop/PWNED1/session/_99`. Second, **this is not a
  regression from libsystemd**: `sd_bus_call` checks neither field
  either, so "we match sd-bus" is true here and worth nothing. Any
  real fix must exceed it — **and both were implemented before this
  tag** (see *Security* above). The bus remains the primary defence,
  but it is no longer the only one.

  Predictable serials turned out to be **irrelevant** — the broker
  rejects on sender identity, not serial secrecy — so serial
  randomisation is explicitly not the fix.
- **samvada's one in-code defence was an accident, and is now
  pinned.** The only frame a hostile peer can push to samvada is a
  directed `SIGNAL` carrying a matching `REPLY_SERIAL`; it is
  discarded solely because the reply matcher returns for message
  type 2/3 and nothing else. That guard read as spec-correctness
  (ignore `NameAcquired`), so its anti-forgery value was incidental
  and undocumented. Pinned by
  `test_forged_signal_cannot_impersonate_a_reply`.
- **The C shim is NOT deleted.** N7 planned to delete it; that is
  **deferred to 1.1.0** because the CG lane never cleared — mabda
  has never run the native backend and no seated session exists
  here, so a `TakeDevice` that returns a real descriptor remains
  unverified by anyone. Deleting the shim would remove both the
  fallback and `tools/differential/`, which had just caught a real
  `pump_signals` defect. Consumers lose nothing by waiting:
  `samvada_native_init()` already builds with zero libsystemd.
- **Two v1.0 criteria ship partially met, marked `[~]` in the
  roadmap rather than redefined**: libsystemd is absent from
  consumer builds (✅) but the shim is not deleted (❌); two
  benchmark rows are filled (✅), the `TakeDevice` one is not (❌).
- **Downstream consumer green is OPEN at 1.0.0**, exactly as the CG
  lane's expiry clause anticipated. The native backend is
  **not-yet-consumer-validated** and re-evaluated at 1.1.0.

### The road, for the record
| | |
|---|---|
| N0 (0.6.0) | ADR-0004; found `TakeControl` at init was blanking the console |
| N1 (0.7.0) | `SCM_RIGHTS` fd passing; found a surplus-fd leak |
| N2 (0.7.1) | golden corpus; corrected our own SASL docs |
| N3 (0.8.0) | transport, auth, framing; native SASL against the real bus |
| N4 (0.9.0) | `Hello` byte-identical to libsystemd's, accepted by the bus |
| N5 (0.10.0) | the frozen API on `kind = PURE_CYRIUS` |
| N6 (0.11.0) | cutover; error-code parity verified |
| N7 (1.0.0) | audit, benchmarks, tag |

## [0.11.0] — 2026-09-09

**N6 — cutover.** The native Cyrius dbus backend ships in the
consumer bundle. A consumer can now reach a working dbus client with
**no C shim, no libsystemd and no `pkg-config`** — verified by
building a probe against `dist/samvada.cyr` alone and confirming the
binary has **zero libsystemd references**:

```
samvada_native_init -> 0
take_device(226,1)  -> -13
release             -> 0
```

The C shim stays in tree, still compiling and still passing CI, as
the differential reference. It is deleted at 1.0.0 (N7), not before.

### Added
- **`samvada_native_init()`** — the one new public fn, and the only
  supported way to reach the native backend. It exists because
  CLAUDE.md forbids exposing FFI types in public signatures: without
  it a native consumer would have to write `samvada_ffi_alloc()` +
  `dbus_native_populate(t)` + `samvada_init(t)`, which hands them a
  fn-table pointer. Additive — `samvada_init`'s frozen signature is
  untouched and shim consumers are unaffected.
- **The native modules enter `[lib] modules`.** This *is* the
  cutover. `dist/samvada.cyr` goes 332 → 2008 lines; the exported
  `samvada_*` set goes 26 → 27 (the new entry point), plus the
  `@internal` `dbus_*` layer. The "bundle unchanged" exit criterion
  that governed N1–N5 was scoped to the pre-cutover milestones and
  retires here, as designed.
- **`tools/differential/`** — builds both backends into one process
  and compares every return value. Sequential A/B is forced, not
  chosen: the module-scope state and the `-EBUSY` re-init guard make
  two live backends in one process impossible by construction.

### Fixed
- **Native `pump_signals` never read from the socket.** It drained
  only what was already buffered, so it could never observe anything
  new. Now does a non-blocking `MSG_DONTWAIT` receive first, with
  any `SCM_RIGHTS` descriptor queued rather than dropped.
  `MSG_DONTWAIT` is per-call deliberately: setting `O_NONBLOCK` on
  the socket would make the request/reply path non-blocking too and
  turn every method call into a busy-wait.

### Notes
- **Error-code parity HOLDS**, and it is the contract. `init`,
  `take_device`, `release_device` and `release` return identical
  values from both backends — including `-13` (`AccessDenied`) and
  `-22`, the two a consumer is most likely to branch on.
- **`pump_signals`' event count is NOT a parity target, and the
  differential found out the hard way.** Its first version reported
  **BREACHED** on shim=1 vs native=0. Investigating rather than
  "fixing" showed both backends handle the same `NameAcquired`
  exactly once, at different points: libsystemd drains its own queue
  at pump time, while the native backend consumes it earlier as a
  non-matching message inside `get_session_path`'s reply loop.
  Pinning the count would pin an internal buffering schedule. The
  harness now reports it and excludes it, with the reasoning written
  down so it is not re-litigated.
  This is exactly the failure mode N6 exists to catch — it just
  turned out that the first thing it caught was an over-strict
  test rather than a defect.
- **`dbus_session` gained a real dependency on `dbus_sys`** through
  the pump fix, which surfaced immediately as an undefined-function
  build failure in the session tests. Recorded because it is the
  kind of coupling that is invisible until the module graph is
  exercised.
- **Still not verified**: a `TakeDevice` that returns an actual fd.
  That needs an active seated session, which no host here has. Both
  backends agree on `-13` off a seat, which is as far as this
  environment can go — the CG lane remains open.

## [0.10.0] — 2026-09-09

**N5 — the logind session layer.** samvada's **frozen public API now
runs on a fully native Cyrius backend** against the real bus, with
`kind = PURE_CYRIUS` and zero libsystemd anywhere in the path:

```
samvada_init       -> 0
take_device(226,1) -> -13
release_device     -> -22
release            -> 0
```

`samvada_init -> 0` means connect + SASL + `Hello` +
`GetSessionByPID` + `TakeControl` all succeeded natively. And
`take_device -> -13` is **`AccessDenied`, a device-level error — not
`-22` (`NotInControl`)**, which is precisely the milestone's exit
criterion. `-22` from `release_device` is logind's own
"device not taken", correct since the take failed.

No public API change; the consumer bundle is untouched (26 exported
fns, no native module until N6).

### Added
- **`src/dbus_session.cyr`** — the six logind calls, a serial
  counter, reply correlation by `REPLY_SERIAL`, dbus-error-name to
  errno mapping, and `dbus_native_populate()` which fills the SAME
  FFI table the C shim fills.
- `tests/dbus_session.tcyr` — 45 asserts.

### Notes
- **Written to the C shim's ABI, deliberately.** Every native fn
  matches a frozen `fncallN` shape exactly — **including the
  vestigial leading `bus` argument and the C-style out-pointer
  pairs**, shapes no Cyrius-native design would choose. A mismatch
  is a silent crash through `fncallN`, not a compile error.
  The pin for this is the strongest in the suite: the tests call
  each native fn **directly at its declared arity**, so a lost or
  gained parameter fails the BUILD. Verified by mutation —
  dropping `active_out` from `take_device` now yields
  `error: 'dbus_native_take_device' expects 5 arguments, got 6`.
- **Session selection: DOCUMENT, not validate.** `GetSessionByPID`
  returns the caller's session, which is not necessarily seated —
  on this host it returns a `Seat=""` session while seat0 belongs
  to the display manager, so `TakeDevice` cannot get DRM master
  there regardless. Validating the session's `Seat` would need
  `org.freedesktop.DBus.Properties`, which ADR-0003's scope fence
  defers past 1.0; widening the fence to produce a nicer error
  message is a bad trade. samvada surfaces logind's error verbatim.
- **Session paths are passed through opaquely.** logind returns
  `/org/freedesktop/login1/session/_32`, where `_32` is hex-escaped
  ASCII `'2'`. samvada only ever echoes the path back as a method
  target — never parses or compares it — so no unescaping is
  needed. Anything that starts comparing session ids must implement
  it first.
- **Error-parity is a contract, and it starts here.** The frozen API
  promises sd-bus errno pass-through, so the native backend has to
  reproduce libsystemd's name→errno map or consumers branching on
  magnitudes break. Eight names are mapped; anything unmapped is
  `-EIO` rather than a plausible-looking wrong value. The captured
  `AccessDenied` reply maps to `-13`, matching the live run.
- **An empty-body call carries NO signature field at all.**
  `ReleaseControl` takes no arguments; emitting `SIGNATURE=""` is a
  distinct encoding. Pinned.
- **The serial wrap is a pure fn so it can be tested.** Driving
  `next_serial()` to the u32 ceiling would take four billion calls;
  extracting `dbus_session_wrap_serial()` makes the one branch that
  matters a one-line assertion. Found because the first version of
  that test could not fail.
- **Unsolicited traffic is discarded, not treated as an error.**
  The bus emits `NameAcquired` on the connect path before any match
  rule exists, so a reader that takes the first message as its
  answer gets the signal instead of its reply.

## [0.9.0] — 2026-09-09

**N4 — marshal and unmarshal.** Native Cyrius now speaks dbus end
to end: connect, SASL, build a `Hello` **byte-identical** to
libsystemd's, send it, and decode the bus's reply. No libsystemd in
that path. This also closes N3's deferred exit criterion.

```
SASL ok
Hello is 128 bytes
  msg type 2  serial 4294967295  len 101
  UNIQUE NAME = :1.20566
  msg type 4  serial 4294967295  len 181
```

One read, two messages, the unique name extracted, and the
`0xFFFFFFFF` serial read back POSITIVE. No public API change; the
consumer bundle is untouched (26 exported fns, no native module).

### Added
- **`src/dbus_marshal.cyr`** — header, `(yv)` field array and body
  for `u` / `s` / `o` / `g` / `b` / `h`. Reproduces the captured
  `Hello` **byte for byte, all 128 of them**.
- **`src/dbus_unmarshal.cyr`** — locates header fields BY CODE and
  pops body values through an aligned cursor.
- `tests/dbus_codec.tcyr` — 48 asserts.

### Notes
- **Field order is never assumed.** The corpus shows `[1,3,2,6]` on
  a call, `[5,7,6,8]` on its reply and `[5,6,8,9,7]` on an
  fd-bearing reply — it is an implementation choice, not a spec
  requirement. Fields are located by scanning for their code, and a
  test builds the same message with a deliberately different field
  order to prove extraction is unaffected.
- **The corpus is a regression fixture; the BUS is the correctness
  oracle.** Byte-equality against a golden buffer tests "did you
  reproduce libsystemd's arbitrary ordering". What actually proves
  correctness is that the daemon accepted our 128 bytes and
  answered — which it did.
- **`fields_len` EXCLUDES the trailing pad.** Hello's is 109, and
  the body starts at `align8(16 + 109)` = 128. Encoding the pad
  into the length is the obvious off-by-three.
- **`g` is length-prefixed by ONE byte, not four.** Treating it
  like `s` reads the signature text as a length. Pinned.
- **`b` is a u32 on the wire** — one byte semantically, four
  physically, strictly 0 or 1. The decoder rejects 2; the encoder
  normalises 99 to 1.
- **`h` is an INDEX**, not a descriptor: the fd arrives out of band
  via `SCM_RIGHTS` and is queued by `dbus_frame`. The body carries
  only a u32 into that array.
- **Every header u32 goes through `load32`**, which zero-extends —
  and a test asserts on our OWN encoding that `load64(b + 4)` is
  negative, so the reason the rule exists stays visible. This is
  the opposite of `dbus_sys.cyr`, which sign-extends correctly
  because an `SCM_RIGHTS` payload really is a signed int32 fd.
- A malformed field array (signature length 0) is refused rather
  than walked forever.

## [0.8.0] — 2026-09-09

**N3 — transport, auth and framing.** Native Cyrius code now
authenticates to a real dbus daemon with no libsystemd in the path.
Three new modules, 210 new asserts, and two live defects fixed in
the module N1 shipped. No public API change; the consumer bundle is
untouched (26 exported fns, no native module in it until the N6
cutover).

### Added
- **`src/dbus_frame.cyr`** — splits a byte stream into whole
  messages. Owns THE receive buffer; `dbus_auth` borrows it rather
  than keeping its own, because `alloc()` never frees and a private
  buffer would either copy or lose the server's pipelined residual.
- **`src/dbus_socket.cyr`** — `sockaddr_un` construction for both
  address forms, connect, and a reliable write.
- **`src/dbus_auth.cyr`** — the SASL handshake as a line-oriented
  reader over the shared buffer.
- 210 asserts across `tests/dbus_frame.tcyr` and
  `tests/dbus_auth.tcyr`, all corpus- or socketpair-driven.

### Fixed — two live defects in `src/dbus_sys.cyr` (shipped 0.7.0)
- **`MSG_CTRUNC` leaked every descriptor the kernel had installed.**
  The truncation check returned `-71` *before* parsing, but the
  kernel installs the fds that fit before `recvmsg` returns.
  Measured in C against that exact 24-byte control buffer: sending
  3 fds sets `MSG_CTRUNC` **and** installs **2 descriptors**, both
  of which leaked, per truncated message, unbounded, inside a
  compositor. Now closed before the error returns.
  My first Cyrius probe for this **passed against the leaking
  version** — a lowest-free-fd probe cannot see it, because the
  kernel installs the surviving fds at the lowest free numbers.
  The pin now counts open descriptors, as the C proof did.
- **A 4-byte read past the control buffer.** The surplus-fd walk
  used `load64(ctrl + off)` where `off + 4 <= clen`; for a legal
  2-fd cmsg `clen` is 24 and `off` reaches 20, so it read bytes
  24..27 of a 24-byte allocation. The mask hid the garbage and the
  bump allocator made it harmless; neither made it correct. Now
  `load32`.

### Notes
- **`load32`, never `load64`, for header u32s — and this is the
  opposite of what `dbus_sys.cyr` correctly does.** `load32`
  zero-extends. `load64(p + 4)` on the bus's very first reply
  returns **-4294967283**, because the `0xFFFFFFFF` serial occupies
  the high half of that load; `total` then goes negative, the
  cursor advances backwards and the framer never terminates.
  `dbus_sys.cyr` sign-extends deliberately and correctly — an
  `SCM_RIGHTS` payload is a signed int32 fd — so copying that idiom
  into the header path is the trap. Both behaviours are now pinned
  by tests that assert the *dangerous* form is dangerous.
- **The send path is `sendto` with `MSG_NOSIGNAL`, not
  `sys_write`.** Writing to a departed peer raises `SIGPIPE`, whose
  default disposition kills the process — not survivable for a
  library inside a compositor. `SIG_IGN` was rejected as the
  mechanism: it is process-global *and* inherited across `execve`,
  and samvada does not own mabda's signal policy. Pinned by a test
  that writes to a closed peer, expects `-EPIPE`, and then asserts
  the next line still runs.
  This required hardcoding the syscall number, because **the stdlib
  has no send-side socket call at all** — `sys_recvmsg` and
  `sys_recvfrom` exist, `sys_sendto`/`sys_sendmsg`/`sys_send` do
  not. Pinned with both arches (44 / 206), never a single number.
- **The two `sockaddr_un` forms can produce the same `addrlen`.**
  For a 10-byte name, filesystem gives `2+10+1` and abstract gives
  `2+1+10` — both 13. A test checking only the length would pass
  against a completely wrong address. What differs is the layout:
  abstract puts a NUL at `sun_path[0]` with no terminator. The test
  was written the wrong way first and caught by its own failure.
- **An over-capacity message is drained, not fatal.** D-Bus carries
  the full length in the fixed header, so the exact byte count to
  discard is known before a single body byte is read and the stream
  resynchronises. A bare length-prefix protocol could not do this.
- **Partial-tail handling is proven by drip-feed, not by a
  fixture.** No corpus fixture ends mid-message — that claim was
  made once from a decoder bug and retracted in 0.7.1. So the path
  is proven by feeding the 282-byte two-message buffer one byte at
  a time, and at chunk sizes 3/7/16/100, asserting no state other
  than `need_more` while incomplete.
- **Honest caveat on the write-all loop**: it is required (a torn
  request desynchronises the connection) but is **permanently
  unexercised** at samvada's message sizes, 48–238 bytes against a
  default `SO_SNDBUF` of hundreds of KB. A future closeout must not
  "prove" it with a test that cannot fail.
- **Big-endian framing is synthetic.** All 13 dbus fixtures are
  `'l'`. The `'B'` path is covered only by a hand-built twin of a
  real header, and is labelled as such in the test.

## [0.7.1] — 2026-09-09

**N2 — the golden byte corpus.** Fifteen fixtures of real dbus wire
traffic, captured while the libsystemd C shim still exists, because
the shim is deleted at the v1.0 cutover (roadmap N7) and the
reference implementation goes with it. No source change to the
public API or the bundle; `dist/samvada.cyr` still exports 26 fns.

### Added
- **`tools/dbus_tap.py`** — a transparent AF_UNIX relay. Point any
  client at it with `DBUS_SYSTEM_BUS_ADDRESS` and it dumps both
  directions, preserving chunk boundaries and forwarding
  `SCM_RIGHTS` ancillary data. No C, no libsystemd, no privileges.
- **`tools/dbus_decode.py`** — a message decoder written against
  the D-Bus specification rather than samvada's own
  `dbus-marshalling.md`, deliberately: the corpus exists to *check*
  that document, so a decoder derived from it would agree with its
  errors.
- **`tests/fixtures/dbus/`** — 15 fixtures plus a `MANIFEST.md`
  carrying host, systemd version, date, sha256s, and a REAL /
  SYNTHETIC marker per file.
- **CI gate**: every fixture must decode, exactly one may be
  SYNTHETIC, and the MANIFEST must keep stating its unmet criterion.

### Fixed
- **`dbus-marshalling.md`'s SASL section was wrong.** It documented
  a six-step ping-pong. The captured reality is **one pipelined
  48-byte write** — `\0AUTH EXTERNAL\r\nDATA\r\nNEGOTIATE_UNIX_FD\r\nBEGIN\r\n`
  with an *empty-credential* `DATA` step — answered by **three
  lines in a single 58-byte read**. Both forms are legal, but a
  reader built to the documented one **hangs against a real bus**
  waiting for lines that already arrived. Corrected, with the older
  form retained as the valid alternative it is.

### Notes
- **The synthetic fixture is derived from captured bytes, not from
  prose.** A successful `TakeDevice` reply cannot be captured here
  — it needs an active seated session, and this host has none, so
  the real `AccessDenied` error reply is what got captured. Rather
  than hand-assemble it from our own documentation (which would put
  the same error into the fixture *and* the implementation at
  once), `Manager.Inhibit` was used: it returns `h` with a real
  `SCM_RIGHTS` cmsg and needs no seat. The synthetic is that REAL
  message with `h` -> `hb` and the body extended by the boolean,
  and the two decode identically apart from those fields. It is
  still marked SYNTHETIC.
- **Eight findings the capture established**, each previously an
  assumption. Beyond the SASL correction: one read carries **two
  complete messages** (the very first exchange does — 101 + 181 =
  282 bytes, zero residual), so a one-message-per-read reader
  silently drops the signal; alignment is relative to the start of
  **each message**,
  not the buffer — the decoder got this wrong at first and
  mis-read the second message's header, a bug invisible until a
  multi-message buffer appears; header field order is neither
  ascending nor stable across message kinds (`[1,3,2,6]`,
  `[5,7,6,8]`, `[5,6,8,9,7]` all observed); the bus's own first
  `METHOD_RETURN` carries serial **`0xFFFFFFFF`**, which in a
  language with no unsigned type and a logical `>>` will fire a
  signed-comparison bug on *message one*.
- **Byte stability, measured over two runs.** Client -> bus is
  byte-identical except the pid argument in `GetSessionByPID`
  (2 bytes). So request-side fixtures can be compared directly.
  Reply-side cannot: they carry the connection's unique name and
  bus-assigned serials, which must be masked.
- **One exit criterion is NOT met and says so.** The roadmap asks
  for a second capture on a *different host / systemd version* to
  prove the corpus is not over-fitted. Only one host is available,
  so this is outstanding and recorded in the MANIFEST. Until then
  the corpus is a regression fixture for samvada's own encoder,
  **not** a conformance oracle — the oracle is the bus itself
  (N4).

## [0.7.0] — 2026-09-09

**N1 — SCM_RIGHTS fd passing, proven before any dbus byte exists.**
The first executable module of the native Cyrius backend
([ADR-0003](docs/adr/0003-native-cyrius-dbus.md)). Deliberately
first: proposal 0001 rates fd passing the pivot's only
Low-confidence module, and its fallback is cheap *only* while
nothing is built on top of it.

No public API change, and **no change to the consumer bundle** —
`dist/samvada.cyr` still exports exactly 26 fns and does not
contain `dbus_sys`. The native modules stay out of `[lib] modules`
until the N6 cutover, which is the roadmap's standing exit
criterion for every pre-cutover milestone.

### Added
- **`src/dbus_sys.cyr`** — receives a file descriptor over a unix
  socket via `SCM_RIGHTS`, in pure Cyrius. Two entry points:
  `dbus_sys_recv_fd()` (the `recvmsg` half) and
  `dbus_sys_parse_scm_rights()` (the cmsg-walk half).

  The split is not cosmetic. The kernel validates ancillary data
  before `recvmsg` returns, so a malformed cmsg can never arrive
  through a real socket — every bounds and type guard would have
  shipped **unreachable from any test**. Splitting the pure byte
  transform out makes all of them provable with a hand-built
  buffer, which is the same "most of it needs no dbus" principle
  the later marshaller milestones rest on.
- **`tests/dbus_sys.tcyr`** — 63 asserts, no bus, no logind, no
  hardware. An fd crosses a real `socketpair` and is proven to be
  the *same open file* by `fstat` dev/ino; `FD_CLOEXEC` is asserted
  via `F_GETFD` rather than trusted; every ABI constant is pinned;
  and 200 round trips leave the descriptor table unchanged.

### Fixed (found by writing the tests)
- **A surplus `SCM_RIGHTS` descriptor was silently leaked.**
  `CMSG_SPACE(1 fd)` and `CMSG_SPACE(2 fds)` are **both 24 bytes**
  (`16 + align8(4)` == `16 + align8(8)`), so a two-fd message fits
  a one-fd control buffer *exactly* and arrives with **no
  `MSG_CTRUNC`**. The first draft returned the first descriptor and
  left the second installed in the process's table for its
  lifetime. The parser now walks the remaining `cmsg_len` and
  closes every surplus fd. Pinned by probing `rfd + 1` with `fstat`
  and requiring `-EBADF`.

### Notes
- **Diffed, not derived.** The cmsg layout was taken from working
  in-language implementations —
  `kybernet/src/lib/notify.cyr:182-235` (receive walk with
  `MSG_CTRUNC` and bounds checks),
  `argonaut/src/notify.cyr:176-207`,
  `cyrius-doom/.../client.cyr:206-228` (send side) — then every
  constant was re-confirmed against glibc's own
  `offsetof`/`sizeof`/`CMSG_*` on x86_64. This is why proposal
  0001's Low confidence rating for this module was too pessimistic.
- **The aarch64 trap is real and is avoided.** `cyrius-doom`
  hardcodes `WL_SYS_SENDMSG = 46` with no aarch64 branch. 46 is
  `sendmsg` on x86_64 but **`ftruncate`** on aarch64 (verified:
  `__NR3264_ftruncate = 46` in `asm-generic/unistd.h`), so copying
  it verbatim would not fail loudly there — it would truncate a
  file. samvada arch-selects (`sendmsg` 46/211, `recvmsg` 47/212)
  and pins both numbers in a test.
- **`MSG_CMSG_CLOEXEC` is used on receive**, so the descriptor
  arrives with `FD_CLOEXEC` already set. That is the receive-side
  counterpart of 0.5.1's `F_DUPFD_CLOEXEC` fix (MED-3) — the
  native backend never has the window the C shim had to close by
  hand.
- **Non-reentrant by construction, and stated rather than
  discovered**: the scratch is file-scope because `var buf[N]`
  inside a Cyrius fn is STATIC data, not stack. samvada is
  single-threaded by design.
- Every guard is mutation-proven: removing the type check, the
  level check, either `cmsg_len` bound, the sign-extension,
  `MSG_CMSG_CLOEXEC`, the surplus-fd close, or corrupting
  `CMSG_DATA`'s offset each fails the suite.

## [0.6.0] — 2026-09-09

**N0 — the first milestone of the road to native Cyrius dbus.**
Ratifies [ADR-0004](docs/adr/0004-session-control-lifecycle-and-signal-visibility.md)
and fixes a user-visible console regression that 0.5.1 introduced.
No public signature changes; no new FFI slot, so the table stays at
11 slots / 88 bytes and N1 is unblocked. Tests 131 → 162.

### Fixed
- **`samvada_init()` blanked the console and disabled the keyboard
  on any seated VT session.** 0.5.1 fixed CRIT-1 by calling
  `TakeControl` inside `samvada_init()` — the right call in the
  wrong place. logind's `method_take_control` passes
  `prepare = true`, and that branch runs `session_prepare_vt()`,
  which for any session with `vtnr >= 1` performs:

  ```c
  ioctl(vt, KDSKBMODE, K_OFF);        /* keyboard off   */
  ioctl(vt, KDSETMODE, KD_GRAPHICS);  /* console blanks */
  ioctl(vt, VT_SETMODE, &mode);       /* VT_PROCESS     */
  ```

  So initializing samvada muted the user's console **before any
  device was requested**, and left it muted if the subsequent
  `TakeDevice` failed. The concrete path for the only consumer:
  mabda calls `samvada_shim_init()` at startup, its logind attempt
  can return "unavailable" and fall back to kiosk, and samvada
  stays initialized — console dead for the life of the process.

  **Session control is now scoped to device ownership**:
  `samvada_init()` no longer takes it (it still validates the slot,
  so a pre-0.5.1 backend fails loudly at init);
  `samvada_session_take_device()` acquires it lazily on the first
  take and hands it straight back if that take fails;
  `samvada_session_release_device()` drops it when the **last**
  device is released, so logind's `session_restore_vt()` returns
  the console immediately; `samvada_release()` drops it if held.

  **Why it was not caught in 0.5.1**: `session_prepare_vt()` opens
  with `if (s->vtnr < 1) return 0;`, and every session on the audit
  host is seatless (`vtnr == 0`). The defect could not reproduce in
  the only environment available — the same shape as CRIT-1 itself.

### Added
- [`docs/adr/0004-…`](docs/adr/0004-session-control-lifecycle-and-signal-visibility.md)
  — the N0 decision, in two parts: the control lifecycle above, and
  a ratification that **samvada does not deliver `PauseDevice` /
  `ResumeDevice` in the 0.x line**. That is now a documented
  property rather than the gap the 0.5.1 audit filed as MED-7.
- 31 new asserts (131 → 162), each mutation-proven: reverting any
  part of the control lifecycle fails the suite. Pins cover init
  *not* taking control, lazy acquisition, control returned on a
  failed take, control *kept* when another device is still held,
  and control dropped on last release.

### Changed
- Roadmap N-lane renumbered: 0.5.2 was consumed by the cyrius 6.6.2
  toolchain patch, so N0 ships as **0.6.0** and every later
  milestone shifts up one minor (N1 → 0.7.0 … N6 → 0.11.0).
- `samvada_version()` packed triple `(0,5,2)` → `(0,6,0)`.

### Notes
- **What we accept, stated plainly.** VT switching is not supported
  while a device is held: the consumer is not told the device was
  paused and will draw to a revoked fd until it stops. And
  `ResumeDevice`'s replacement fd is dropped, so after a
  pause/resume cycle the consumer's fd is stale. Supported usage is
  take-hold-release within one session activation.
- **Findings that made deferring signal delivery the right call**,
  all verified against systemd v261 source rather than the man page
  (which is wrong about the timeout):
  `PauseDevice("pause")` — the only type requiring an ack — is
  emitted solely from the no-VT branch of `session_activate()`, so
  a VT seat receives `"force"`, which needs no reply; `struct Seat`
  has no timer at all, and `session_leave_vt()` acknowledges VT
  switches unconditionally, so nothing waits on us; and
  `ResumeDevice` hands back a *new* fd, which means a "notify me"
  contract would be actively misleading without an fd-handoff path.
- **Recorded for whoever revisits this**: the signals are *unicast*
  (`sd_bus_message_set_destination` to the controller's unique
  name), so dbus-broker routes them with **no `AddMatch`** — a
  future implementation needs only a local filter, and can use a
  **C** filter in the shim rather than the Cyrius-fnptr-as-
  `sd_bus_message_handler_t` path that has never executed.

## [0.5.2] — 2026-09-09

Toolchain patch. Pinned Cyrius bumped `6.6.1` → `6.6.2`, which
contains the upstream fix for the symbol-collision defect samvada
filed during the 0.5.1 audit. No source-logic change; 131 tests
pass unchanged.

### Fixed
- **The `objcopy` symbol-localization step is no longer needed.**
  cyrius 6.6.2 fixes the defect samvada filed
  (`cyrius/docs/development/issues/2026-09-09-stdlib-exports-libc-names-with-incompatible-abi.md`,
  credited upstream as *"filed by samvada, reproduced and
  fixed"*). Through 6.6.1 an `object;` build exported `memchr`,
  `strchr`, `strstr`, `strlen`, `memcpy`, `memset`, `atoi` and
  `getenv` as **preemptible** globals, so a linked C library's
  calls rebound to Cyrius's implementations — and the contracts
  differ in both directions (Cyrius's `memchr` returns an offset
  or `-1`; C's returns a pointer or NULL). samvada's process hung
  inside `sd_bus_call_method`.

  **Verified on the pinned toolchain rather than taken from the
  changelog:**

  | | 6.6.1 | 6.6.2 |
  |---|---|---|
  | `readelf -sW` on `memchr` | `bind=GLOBAL vis=DEFAULT` | `bind=GLOBAL vis=HIDDEN` |
  | full consumer link, no `objcopy` | **hangs** | `samvada_init -> 0`, `take_device -> -13` |

  Note the fix is to **visibility**, not binding — `nm` reports
  `T` in both cases, so checking with `nm` alone would suggest
  nothing had changed. Upstream also re-derived the affected
  name list as **11 symbols, not the 8** in samvada's filing.

  `docs/guides/consumer-link.md` and `README.md` drop the step
  from the main recipe; it is retained in the guide, clearly
  scoped, for consumers pinned below 6.6.2.

### Changed
- **Toolchain pin** `cyrius.cyml [package].cyrius` bumped
  `6.6.1` → `6.6.2`. CI and release both read this pin.
- `samvada_version()` packed triple `(0,5,1)` → `(0,5,2)`; the
  version-triple pin in `tests/samvada.tcyr` updated lock-step.
- `dist/samvada.cyr` regenerated under 6.6.2 (332 lines, shape
  unchanged).

### Notes
- 6.6.2's headline repair is to `lib/tagged.cyr` (`tag()` and
  `is_tag()` had been silently redefined at unchanged arity in
  6.6.0). samvada declares `tagged` as a stdlib leaf but calls
  **none** of that API — verified by grep across `src/` and
  `tests/` — so it is a no-op here. `tagged` and `str` remain
  declared-but-unused, which the 0.5.1 audit filed as INFO.
- CI gates, FFI layout (11 slots / 88 bytes, `kind` at +64) and
  the public API are all unchanged. The C shim compiles clean in
  both modes under 6.6.2.

## [0.5.1] — 2026-09-09

**P(-1) hardening + security audit release.** Two CRITICAL, ten
MEDIUM and sixteen LOW findings fixed; the test suite grows
38 → 114 asserts; every repair is mutation-proven. Full report:
[`docs/audit/2026-09-09-audit.md`](docs/audit/2026-09-09-audit.md).

Both CRITICAL findings share one root cause: **no code path in
this repository had ever been executed against a real dbus daemon
or a real consumer link.** Everything was pinned structurally
against a mock table that could not fail in the ways that
mattered. Every gate was green while the product did not work.

### Security
- **CRIT-1 — `samvada_session_take_device()` could never succeed
  in any environment.** logind requires `TakeControl(b)` on the
  session before it will honour `TakeDevice`, bound to the same
  bus connection. samvada 0.2.0 through 0.5.0 never sent it, so
  every call returned `org.freedesktop.login1.NotInControl`.
  Reproduced live against `systemd-logind`: without `TakeControl`,
  *"You are not in control of this session"*; with it, the call
  advances past the control check. Two slots are appended after
  `kind` per ADR-0002 — `take_control` → +72, `release_control`
  → +80, `samvada_ffi_size()` 72 → 88, `kind` unmoved at +64.
  `samvada_init()` now takes control; `samvada_release()` drops
  it. A table whose `take_control` slot is null (a pre-0.5.1
  backend) is rejected with `-38` rather than proceeding into a
  guaranteed failure. **No public signature changed.**
- **MED-3 — the DRM-master fd leaked across `execve`.**
  `sb_take_device` used `dup()`, which **clears** `FD_CLOEXEC`,
  so the delegated master fd survived any `exec` the consumer
  performed and leaked master rights into unrelated children. Now
  `fcntl(fd, F_DUPFD_CLOEXEC, 0)`.
- **MED-5 — unbounded signal drain.** `sb_pump_signals` looped
  until the queue emptied; one call measured captive for **0.77 s**
  under an unprivileged local flood. Capped at 256 events per
  call. The frozen zero-argument signature forbids a parameter, so
  the cap lives in the C wrapper; residue drains on the next tick.
- **MED-9 — CI supply chain.** The toolchain installer was piped
  from a mutable branch into `sh`, in a job that had already run
  `actions/checkout` with `persist-credentials` — so the job's
  `GITHUB_TOKEN` sat on disk, readable, while upstream code ran
  with `contents: write`. Now: pinned commit + `sha256sum -c`;
  `contents: read` by default with write granted only to the
  publishing job; `persist-credentials: false` on every checkout.
- **MED-10 — the security scan could not fire.** It grepped for
  `sys_system` (which does not exist in the Cyrius stdlib) and two
  numeric literals the codebase never writes, while `sys_execve`,
  `sys_fork` and `syscall(SYS_EXECVE, …)` all passed unflagged.
  Proof-of-miss: `sys_execve("/usr/bin/id", …)` added to
  `src/main.cyr` passed **every** gate. Now matches symbol names,
  `SYS_*` constants and a widened numeric set under `grep -E`.
- **MED-11 — the C shim was scanned by nothing.** The scan covered
  `src/` only. `deps/samvada_main.c` — a released artifact, the
  only code touching raw pointers and fds — was excluded. Scope
  widened to `src/ tests/ deps/`, with C spawn patterns added.
- **MED-2 — `major`/`minor` were silently truncated** 64→32 with
  no range check at any layer, while `SECURITY.md` claimed they
  were "validated at C boundary". `devnum_ok()` now makes that
  claim true.
- **LOW-1**, open since 2026-05-01: `sb_get_session_path` now
  checks `path == NULL` after a successful read.
- **LOW-5** (was LOW-2), open since 2026-05-01:
  `samvada_release()` now zeroes both scratch buffers. **New
  angle**: `_samvada_outs` holds a raw `sd_bus *` that is
  *dangling* after `close_bus` — not merely "an fd index" as
  originally characterised.
- **LOW-4/LOW-7**: bounds check on `out_buf`/`out_buf_len` before
  the `(size_t)` cast a negative length would defeat;
  `sb_unsubscribe` rejects `slot_ <= 0` so a caller who skipped an
  error check cannot turn an errno into a wild pointer.

### Breaking
- **The C shim no longer defines `main()` by default.**

  `deps/samvada_main.c` defined `int main()` unconditionally.
  Every real consumer already owns `main()` — mabda's
  `deps/wgpu_main.c:435` does — so linking both gave
  `multiple definition of 'main'`. **The documented two-stage
  build could never have worked** (audit CRIT-2). samvada's own CI
  missed it because the shim link test used stub objects that
  deliberately omit `main()` — the one shape no real consumer has.

  **Migration.** Call `samvada_shim_init()` once from your own
  `main()`:

  ```c
  extern void _cyrius_init(void);
  extern long alloc_init(void);
  extern long samvada_shim_init(void);

  int main(void) {
      _cyrius_init();
      alloc_init();
      long rc = samvada_shim_init();   /* 0, or a negative sd-bus errno */
      if (rc < 0) { /* handle */ }
      /* ... your program ... */
  }
  ```

  For a standalone probe binary instead, compile the shim with
  `-DSAMVADA_STANDALONE_MAIN` and it supplies `main()` as before.
  Nothing else changes: the Cyrius public API is untouched and
  consumer `.cyr` code needs no edit. Pre-1.0, taking the clean
  shape beats carrying an unusable one into 1.0.

  **Two further reasons the old recipe could not work**, found
  while verifying the fix end to end — both now documented with
  executed commands in
  [`consumer-link.md`](docs/guides/consumer-link.md):
  - **`cyrius build … --emit-object` does not exist** (`error:
    unknown flag`). `cyrius build` emits a finished executable,
    not a relocatable. A linkable object needs the `object;`
    directive piped through `cycc` — so the documented recipe
    could not produce the object it then told you to link.
  - **`objcopy -L` symbol localization was never documented and
    is mandatory.** A Cyrius object exports its own `memcpy`,
    `memset`, `strlen`, `strchr`, `strstr`, `memchr` and `atoi`
    as global symbols; without localizing them, *libsystemd's*
    calls bind to Cyrius's. Measured on an otherwise-identical
    build: `samvada_shim_init()` returns **`-107`** without the
    `objcopy` step and **`0`** with it. The build succeeds either
    way and nothing in the failure points at symbol interposition.
  - **The pinned tag did not exist** (`tag = "v0.2.0"`; every
    samvada tag is unprefixed) and **`lib/samvada/deps/samvada_main.c`
    is not a path on any machine** — `cyrius deps` copies only the
    `modules` list into `lib/`, and the full checkout is cached at
    `~/.cyrius/deps/samvada/<tag>/`.
- **The fn-table is now `static`.** It was a stack local in
  `main()`. samvada *borrows* the pointer — `_samvada_slot()`
  re-reads `load64(table + off)` on every dispatch, it never
  copies — so under a library-style entry the frame would pop
  while the pointer stayed live. The comment justifying the stack
  table claimed `samvada_main()` "never returns until the process
  exits"; it returns immediately. Both that comment and a second
  one claiming the table "lives in C static memory" were wrong and
  are corrected; the borrow contract is now stated in
  `public-api.md`.

### Fixed
- **MED-4 — `samvada_session_release_device` returned `1`, not
  `0`, on success.** `sd_bus_call_method` returns a positive value
  on success and it was passed straight through, contradicting the
  documented `0 | -err` contract in four places. Normalised in
  both the C wrapper and the Cyrius fn.
- **MED-6 — `samvada_init` accepted any non-zero `kind`**, so a
  wrongly-shaped table whose +64 word happened to be non-zero was
  dispatched as function pointers instead of rejected. Now
  whitelists `LIBSYSTEMD` / `PURE_CYRIUS`.
- **MED-8 — `samvada_init` did not self-clean on late failure.** A
  failure after the bus opened returned the error but left
  `_samvada_table`/`_samvada_bus` set — leaking the `sd_bus` and
  leaving the public API armed on a half-initialised samvada, with
  the next `samvada_init` hitting `-EBUSY`. It now releases on
  every late-failure path.

### Added
- **A pure-Cyrius mock backend** in `tests/samvada.tcyr`. The
  long-standing claim that `take_device` / `release_device` /
  `pump_signals` could not be tested without hardware was false —
  the fn-table is just function pointers, and a mock exercises the
  whole dispatch path with no bus, no logind and no device.
- New CI gates: a link test reproducing the **real** consumer
  shape (consumer-owned `main()` + shim); a mechanical
  **C-`#define`-vs-Cyrius-slot cross-check** with an assertion
  that `kind` is still at +64 — ADR-0002 and `public-api.md` both
  *claimed* the test pin froze this contract and it never did, it
  only checked the Cyrius side; a **version-triple-vs-`VERSION`**
  check; `dist/samvada.deps` freshness; `CYRIUS_DCE=1` on the
  released binary (declared the CI default in CLAUDE.md, set by no
  workflow).
- [`docs/audit/2026-09-09-audit.md`](docs/audit/2026-09-09-audit.md)
  — the full report, including six findings **refuted** before
  admission.
- [`docs/adr/0003-native-cyrius-dbus.md`](docs/adr/0003-native-cyrius-dbus.md)
  — see below.
- **CI: the C-shim job no longer depends on apt sources samvada does
  not use.** The runner image ships third-party repos (Google
  Chrome, Microsoft prod), and `apt-get update` exits 100 if *any*
  configured source fails — even when it reports "they have been
  ignored" and every package we need resolved fine. A hash-sum
  mismatch in Google's Chrome index (a stale `Packages.gz` against
  a fresh `Release` on their CDN) failed the job on 2026-09-09 for
  a repo samvada never reads. Those sources are now removed before
  `apt-get update`, which also gains `Acquire::Retries=3` and a
  `pkg-config --modversion libsystemd` confirmation. Scoped
  deliberately: the Ubuntu archive is untouched, so a genuine
  failure to fetch `libsystemd-dev` still fails loudly rather than
  being masked with `|| true`.

### Changed
- **`docs/development/roadmap.md` is rewritten as *the road to
  Native DBus in Cyrius*.** ADR-0003 ends the A.1-vs-A.2 deferral
  and adopts native Cyrius dbus as the v1.0 path — partly on the
  merits, and partly because a backend nobody has committed to is
  a backend nobody tests, which is how CRIT-1 survived five
  releases. The roadmap now runs two lanes: an **N lane** (N0–N7)
  buildable with zero consumer involvement and zero special
  hardware, and a quarantined **CG lane** for the
  hardware-gated consumer e2e that never blocks an N milestone.
  All prior commitments are folded in explicitly — M0–M3, the old
  v1.0 checklist, M2's surface wishlist and the 2026-05-01 audit's
  "roadmap items" each land somewhere named, including where the
  disposition is "deferred past 1.0".
- **Test-suite quality, not just quantity.** Vacuous assertions
  removed: `test_init_rejects_null_table` passed with the guard
  deleted, and `"fresh slot is 0"` could not fail because the bump
  allocator hands out untouched pages. `samvada_ffi_set_slot`'s
  null guard was untested while `get_slot`'s was pinned — and
  `set_slot` is the *write*. There was no dispatch-wiring pin, so
  a misroute could return fd 0 (stdin) with the suite green.
  `samvada_main` had no test at all despite its return value
  becoming the process exit code.
- Documentation corrected against the code throughout:
  `SECURITY.md` (supported versions were six releases stale; the
  threat model had a row for a consumer-supplied-pid path that
  does not exist), `public-api.md` (undocumented table-lifetime
  contract, mis-attributed `-EBADF`, an unreachable
  `pump_signals` post-condition, the missing threading contract),
  `consumer-link.md` (rewritten around `samvada_shim_init`),
  `README.md`, and `dbus-marshalling.md` (which omitted the
  session-control handshake entirely — the specification mirrored
  the bug).

### Notes
- **Not fixed, and stated plainly.** Slots 48/56 are populated by
  the shim but dispatched by no Cyrius code, and no public API
  installs a match rule — so `PauseDevice`/`ResumeDevice` are
  **not delivered** and `PauseDeviceComplete` is unwired. Four
  documents claimed otherwise; they now tell the truth. Wiring it
  needs the signal-visibility contract decided first, because the
  frozen API gives `samvada_pump_signals()` a single integer
  return and no callback registration. That is roadmap **N0**.
- **`samvada_release()` invalidates outstanding device fds.**
  logind revokes every device taken via `TakeDevice` when session
  control is released. This was already true via the bus close;
  0.5.1 makes it explicit and documents it.
- **The fix is verified through the complete stack.** A full
  consumer binary — Cyrius object + C shim + consumer-owned
  `main()`, linked against libsystemd — was built and run against
  the live system bus:

  ```
  samvada_init (incl. TakeControl) -> 0
  take_device(226,1)               -> -13
  ```

  `samvada_init` returning **0** means bus open →
  `GetSessionByPID` → `TakeControl` all succeeded. The `-13`
  (`-EACCES`) is the *environment* — that probe ran from a
  `Seat=""` session, which can never own a DRM device — and
  crucially it is **not** `-22`, which is what `NotInControl`
  squashes to and what every pre-0.5.1 build returned.
- **What remains unverified**: whether `TakeControl` is
  *sufficient*, which needs an active **seated** session holding a
  DRM device. The audit host had none. This is the CG-lane gate
  and is now the single most valuable unverified thing in the
  project.

## [0.5.0] — 2026-09-09

Toolchain update release. The pinned Cyrius toolchain moves
from `6.2.6` to `6.6.1` — four minor lines within the 6.x
series, no major-line jump. The full local gate sweep (lint,
fmt --check, vet, distlib, build, C-shim compile-check, test,
bench) passes clean with no source-logic change. Minor bump
(0.4.1 → 0.5.0) rather than a patch: the pin moves four minor
lines and the move is *measurable* — `CYRIUS_DCE=1` eliminates
for the first time (the pass padded rather than eliminated
before cyrius 6.5.72), cutting the release binary 81 %, and
`ffi_alloc` halves. No new protocol surface and no public API
change: the exported symbol set in `dist/samvada.cyr` is
identical to 0.4.1 (26 fns, same names, same signatures, same
error-code contracts). `samvada_version()` packed triple bumps
`(0,5,0)`.

### Changed
- **Toolchain pin** `cyrius.cyml [package].cyrius` bumped
  `6.2.6` → `6.6.1`. CI and release both read this pin; no
  hardcoded version strings in YAML, so no workflow edits were
  needed.
- **Stdlib re-resolved** under 6.6.1 — `lib/` wiped and
  repopulated via `cyrius deps` from the same ten declared
  leaves (`string`, `fmt`, `alloc`, `io`, `vec`, `str`,
  `syscalls`, `assert`, `tagged`, `fnptr`). `[deps].stdlib` is
  unchanged; the vendored module bodies are 6.6.1's. samvada
  uses none of `Result` / `Option` / `Either`, so 6.6.0's
  value-form flip for those types is a no-op here.
- **Continuation-line reformat** — the 6.6.x `cyrfmt` enforces
  a canonical continuation indent (2 spaces per open paren, 4
  also accepted); the pre-6.6 tree wrapped continuations at the
  statement indent. `src/samvada.cyr` (2 call sites) and
  `tests/samvada.tcyr` (4 call sites) reformatted by
  `cyrius fmt`. Whitespace only — no token changed.
- `samvada_version()` packed triple `(0,4,1)` → `(0,5,0)` in
  `src/samvada.cyr`; the version-triple pin in
  `tests/samvada.tcyr` (`test_samvada_version`) updated
  lock-step.
- `dist/samvada.cyr` regenerated under 6.6.1's `cyrius distlib`
  emitter — 269 lines, unchanged shape. The only diffs are the
  version stamp, the `samvada_version()` triple, and the two
  reformatted continuation lines.

### Added
- `dist/samvada.deps` — 6.6.x's `cyrius distlib` emits a dep
  sidecar next to the bundle listing the stdlib leaves the fold
  needs in scope (the same ten from `[deps].stdlib`);
  `cyrius deps` consumes it downstream. **Tracked**, matching
  the sibling repos (yukti ships `dist/yukti.deps`, mabda
  `dist/mabda.deps`) — a `[deps.samvada]` consumer that fetches
  the release tag gets the sidecar with the bundle.

### Performance
- **`CYRIUS_DCE=1` now eliminates.** The dead-code pass NOP-ed
  and padded rather than compacting before cyrius 6.5.72; under
  6.6.1 the release build drops **80,904 B → 15,368 B
  (−81.0 %)** on the standalone smoke binary, with 362
  unreachable fns (63,814 B) removed. The default (non-DCE)
  build is unchanged at 80,904 B. CI's release path already
  sets `CYRIUS_DCE=1`, so this lands with no workflow edit.
- **CPU bench baselines improve on 6.6.1 codegen** (AMD Ryzen 7
  5800H, 1,000,000 iters, three runs — `ffi_alloc` measured
  28 / 28 / 29 ns, the other three identical across all three;
  deltas vs the last recorded row, `0.3.0` under cyrius
  6.0.40):
  `ffi_alloc` **63 ns → 28 ns (−55.6 %)**,
  `ffi_get_slot` **11 ns → 9 ns (−18.2 %)**,
  `init_reject_null` **7 ns → 6 ns**,
  `release_idempotent` **7 ns → 6 ns**.
  The last two are inside this host's documented jitter floor;
  `ffi_alloc` is not — it is a real codegen win on the
  `alloc(72)` + 9-`store64` zero-fill path. Appended to
  [`docs/benchmarks.md`](docs/benchmarks.md) as Run 4.

### Notes
- No source-logic change. 38 tcyr asserts pass (unchanged
  count); the live-bus scaffold still runs as the separate CI
  skip-path smoke and still takes the SKIP path. FFI slot
  offsets (9 slots / 72 bytes, kind pinned at +64) and the v0.x
  stability contract are untouched.
- `docs/benchmarks.md` gained no rows for 0.4.0 or 0.4.1
  despite the every-release cadence in CLAUDE.md. Rather than
  backfill numbers that were never captured, Run 4 states its
  deltas against Run 3 (`0.3.0`) and the gap is recorded in the
  doc.
- **Downstream not re-verified against 0.5.0.** mabda pins
  `[deps.samvada] tag = "0.4.1"` and resolves the bundle from
  the git tag, so a real 0.5.0 downstream build needs the tag
  to exist first. What *was* verified: mabda's smoke build is
  green, and the 0.5.0 bundle's exported symbol set is
  identical to the 0.4.1 bundle mabda vendors — mabda calls
  only `samvada_session_take_device` /
  `samvada_session_release_device`, both unchanged.

## [0.4.1] — 2026-06-14

Toolchain update release. The pinned Cyrius toolchain moves
from `6.0.40` to `6.2.6` — a minor-line bump within the 6.x
series (no major-line jump, no CLI-surface change), which the
full local gate sweep (lint, fmt --check, vet, distlib, build,
C-shim compile-check, test) passes clean against with no
source-logic change. Patch bump (0.4.0 → 0.4.1): toolchain
minor-line move, no new protocol surface and no public API
change — every exported symbol's signature and error-code
contract is unchanged from 0.4.0. `samvada_version()` packed
triple bumps `(0,4,1)`.

### Changed
- **Toolchain pin** `cyrius.cyml [package].cyrius` bumped
  `6.0.40` → `6.2.6`. CI and release both read this pin; no
  hardcoded version strings in YAML, so no workflow edits were
  needed.
- `samvada_version()` packed triple `(0,4,0)` → `(0,4,1)` in
  `src/samvada.cyr`; the version-triple pin in
  `tests/samvada.tcyr` (`test_samvada_version`) updated
  lock-step.
- `dist/samvada.cyr` regenerated under 6.2.6's `cyrius distlib`
  emitter — version stamp + the `samvada_version()` triple are
  the only changes (269 lines, unchanged shape). No API or
  symbol change.

### Notes
- No source-logic change. 38 tcyr asserts pass (unchanged
  count); the live-bus scaffold still runs as the separate CI
  skip-path smoke. FFI slot offsets and the v0.x stability
  contract are untouched.

## [0.4.0] — 2026-06-02

Road-to-v1.0 batch. No public API change — `samvada_version()`
bumps `(0,4,0)` and the consumer-facing surface
(`dist/samvada.cyr`) is byte-for-byte unchanged in shape. The
work advances three v1.0 criteria: a live-bus bench harness
scaffold lands, the public-API-freeze and CHANGELOG-complete
criteria are certified + closed, and the v1.0 pivot is fully
scoped in a proposal.

### Added
- `tests/samvada_live.bcyr` — HW-gated live-bus bench harness
  scaffold for the three v1.0-gate measurements (handshake
  latency, `TakeDevice` round-trip, signal-pump drain cost).
  `samvada_live_bench_run(table)` gates on the FFI backend
  `kind` and probes the bus, then SKIPs (exit 0) on any build
  without a libsystemd-backed table — so it runs on CI as a
  compile + skip-path smoke and is ready for a consumer C-shim
  build to capture real numbers. Not part of the public bundle
  (`[lib]` modules unchanged).
- `docs/proposals/0001-v1-dbus-backend-pivot.md` (+ proposals
  index) — scopes the v1.0 pivot named in ADR-0001 into a
  concrete plan: Path A.1 (pure-Cyrius marshaller) module map,
  ~650–1010 LoC estimate grounded in the wire format
  `dbus-marshalling.md` documents, a test strategy where codec
  round-trips / SASL / `SCM_RIGHTS` are all CI-green over
  `socketpair` (no dbus hardware), a risk register, and a
  1-session de-risking spike. Path A.2 (removal) scoped for
  comparison. The decision stays deferred to mabda v4.0; this
  makes it a short one.
- New CI step — runs the live-bus scaffold as a skip-path smoke
  (asserts the SKIP path holds on CI).

### Fixed
- `docs/architecture/public-api.md` documented the `-EBUSY`
  (`-16`) double-init reject that 0.2.2 actually shipped but the
  page never recorded (the 0.2.2 CHANGELOG claimed it "joins the
  error catalog" but the edit was never applied). Added to the
  `samvada_init` rc table, the error-code catalog, and the
  prose; added the `init rejects double-init without release`
  row to the test-coverage map. Surfaced by the 0.4.0
  public-API-freeze certification audit.

### Changed
- `samvada_version()` packed triple `(0,3,0)` → `(0,4,0)`;
  version-triple pin in `tests/samvada.tcyr` updated lock-step.
- `docs/development/roadmap.md` — two v1.0 criteria closed:
  **Public API frozen** (`[x]`, after the public-api.md audit
  above) and **CHANGELOG complete from v0.1.0 onward** (`[x]`,
  verified every released version parses under both the CI
  docs-gate and the release body extractor). The architectural-
  pivot criterion stays open but now links the scoping proposal.
- `docs/benchmarks.md` — the "harness does not yet exist" note
  replaced; the scaffold (`tests/samvada_live.bcyr`) now exists
  and the consumer-invocation recipe is documented.

### Notes
- No new runtime code on the public path — the live bench fns
  live in `tests/` and never enter `dist/samvada.cyr`, so the
  v0.x stability contract and the FFI slot offsets are
  untouched.
- The live-bus *numbers* (and the signal-pump synthetic flood
  generator) remain the v1.0/M1 gate work — gated on a consumer
  C-shim build with running dbus/logind on real hardware.

## [0.3.0] — 2026-06-02

Toolchain/language update release. The pinned Cyrius toolchain
moves from `5.7.48` to `6.0.40` — a major-line bump (5.7.x →
6.0.x) that the full local gate sweep (lint, fmt --check, vet,
distlib, build, C-shim compile-check, test, bench) passes
clean against with no source-logic change. The minor bump
(0.2.x → 0.3.0) reflects the toolchain major-line jump, not new
protocol surface; M2 ("generalize beyond logind") feature scope
in `docs/development/roadmap.md` stays unscoped pending a second
AGNOS consumer. `samvada_version()` packed triple bumps
`(0,3,0)`. No public API change — every exported symbol's
signature and error-code contract is unchanged from 0.2.2.

### Changed
- **Toolchain pin** `cyrius.cyml [package].cyrius` bumped
  `5.7.48` → `6.0.40`. CI and release both read this pin; no
  hardcoded version strings in YAML, so no workflow edits were
  needed. The 6.0.x codegen produces no measurable regression
  on the dispatch hot path (`ffi_get_slot` flat at 11 ns; see
  `docs/benchmarks.md` Run 3).
- `dist/samvada.cyr` regenerated under 6.0.40's `cyrius distlib`
  emitter, which collapses a duplicate blank line in the
  generated header (269 lines, was 270). Generated artifact
  only — no API or symbol change.
- `samvada_version()` packed triple `(0,2,2)` → `(0,3,0)` in
  `src/samvada.cyr`; the version-triple pin in
  `tests/samvada.tcyr` (`test_samvada_version`) updated
  lock-step.
- `CONTRIBUTING.md` prerequisite note now reads `6.0.40`.
- `docs/benchmarks.md` Run 3 appended (CPU baselines under
  6.0.40: `ffi_alloc` 63 ns, `ffi_get_slot` 11 ns,
  `init_reject_null` 7 ns, `release_idempotent` 7 ns). Deltas
  are within the documented per-iteration jitter floor.
- **CI/release toolchain install modernized** (`ci.yml` +
  `release.yml`). Both workflows now resolve the toolchain via
  the canonical upstream installer
  (`scripts/install.sh | CYRIUS_VERSION=<pin> sh`) instead of a
  manual tarball fetch + flat `cp` into `~/.cyrius/{bin,lib}`.
  The 6.0.x toolchain is version-aware — it resolves the
  `cyrius.cyml` pin against `~/.cyrius/versions/<pin>/lib`, a
  layout the old flat copy never produced, so `cyrius deps`
  would have failed under 6.0.40. The pin remains the single
  source of truth; no version is hardcoded in YAML. Mirrors the
  patra/yukti install pattern.
- **CI fmt gate fixed for 6.0.x** (`ci.yml`). `cyrius fmt`
  changed to file-before-flag (`cyrius fmt <file> --check`) and
  `--check` is now a pure exit-code check that emits nothing to
  stdout. The gate previously diffed that stdout against the
  committed file, which would report drift on every file under
  6.0.x; it now branches on the exit code.

### Notes
- Consumers pinning `[deps.samvada]` need no migration — the
  bundled API surface (`dist/samvada.cyr`) is unchanged in shape
  and the FFI slot offsets are frozen by the slot-offset pin.
- The C shim (`deps/samvada_main.c`) is unchanged and still
  compiles `-Wall -Wextra -Werror` clean against libsystemd 260;
  samvada itself does not link libsystemd, so the toolchain bump
  does not touch the consumer's link step.
- 38 tests pass (unchanged count); the only test edit is the
  version-triple value.

## [0.2.2] — 2026-05-01

P(-1) hardening pass on top of 0.2.1. One HIGH and one MEDIUM
correctness defect fixed, three new docs file the v1.0
preparatory references, audit doc lands the methodology used.
No public API change — `samvada_version()` packed triple bumps
`(0,2,2)` and the new `-EBUSY` (`-16`) return code is an
*addition* to the negative-errno catalog, not a behavior change
on any existing path.

### Added
- `docs/architecture/public-api.md` — canonical map of every
  exported symbol: signature, pre/post-conditions, full
  error-code table per fn, test-coverage map, and the v0.x
  stability contract. Closes one of the six open v1.0 criteria
  in `docs/development/roadmap.md` ("public API frozen — every
  exported symbol documented + tested").
- `docs/sources.md` — consolidated protocol citations [S-1]
  through [S-16]: D-Bus spec, logind interface,
  `sd_bus_*(3)` man pages, `unix(7)` /
  `recvmsg(2)` / `cmsg(3)` / `dup(2)` for fd-passing, DRM
  major number, errno header refs, cyrius `lib/fnptr.cyr` /
  `lib/syscalls_x86_64_linux.cyr`. The v1.0 pure-Cyrius
  marshaller has every reference in one place.
- `docs/benchmarks.md` — perf-history seed. Run 1 captured at
  commit `4c7ada9` against samvada 0.2.1: `ffi_alloc` 56 ns,
  `ffi_get_slot` 9 ns, `init_reject_null` 6 ns,
  `release_idempotent` 6 ns. Method, variance, and graduation
  criteria documented inline; each release appends a column.
  Live-bus rows (handshake latency, `TakeDevice` round-trip,
  signal-pump throughput) are the v1.0 gate work.
- `docs/audit/2026-05-01-hardening-review.md` — internal
  hardening review (NOT the formal v1.0 audit, which
  `SECURITY.md` §"Audit History" gates). Methodology, four
  findings (1 HIGH / 1 MED / 2 LOW), disposition for each, and
  scope notes on what the audit explicitly does *not* cover
  (live-bus behavior, signal callback safety, multi-thread,
  bus-pump DoS).
- `tests/samvada.tcyr` — new `test_init_rejects_double_init`
  group (6 asserts) pinning HIGH-1's fix: re-init without
  release returns `-EBUSY`; release clears the guard.

### Fixed
- **HIGH-1** — `samvada_init` is now double-init safe.
  Previously a second `samvada_init` without an intervening
  `samvada_release` overwrote `_samvada_table` / `_samvada_bus` /
  `_samvada_sess` / `_samvada_outs` without releasing the
  previous values, leaking the dbus connection + scratch bytes
  on every retry. Now: returns `-16` (`-EBUSY`) when
  `_samvada_table != 0`. The legitimate re-init-after-release
  path reuses the already-allocated scratch buffers (they
  survive `samvada_release` per the bump-allocator contract)
  rather than re-allocating, which would leak under any
  allocator that doesn't bump-pack identically.
- **MEDIUM-1** — `sb_take_device` now returns `-EBADF`
  explicitly when the message read succeeded but delivered a
  negative fd, instead of returning `-errno` from a syscall
  that was never made. Also captures `errno` *before*
  `sd_bus_message_unref` / `sd_bus_error_free` so a future
  libsystemd that touches errno on cleanup paths cannot clobber
  the dup-failure code. Reachability is narrow (libsystemd's
  `sd_bus_message_read("hb")` is documented to return `fd >= 0`
  on success), but the C-side defensive contract was wrong.

### Changed
- `CLAUDE.md` work-loop step 2 now points at
  `docs/benchmarks.md` for per-release row appends, replacing
  the placeholder note that history "arrives at the v1.0 gate."

### Notes
- The new `-EBUSY` return code joins the error catalog in
  `docs/architecture/public-api.md`. Consumers that ignored
  unknown negative returns (the documented "branch on `< 0`,
  log, abort" pattern) need no migration. Consumers that
  retried `samvada_init` on failure now hit a deterministic
  reject instead of silently leaking.
- The C-shim recompile passed strict-flag rebuild
  (`-Wall -Wextra -Werror -Wshadow -Wconversion -Wcast-qual
  -Wformat=2`) clean against libsystemd 260; the audit's
  posture check did not require code changes.

## [0.2.1] — 2026-04-30

Polish patch on top of 0.2.0. No public API change — every
addition is internal tooling, docs, or test scaffolding.
samvada_version() bumps `(0,2,1)` so consumer version-gates
can pin the exact tooling generation.

### Added
- `docs/adr/0001-c-shim-then-pivot.md` — formalizes the v0.x
  libsystemd-C-shim choice and the v1.0 retirement plan
  (pure-Cyrius marshaller or removal). Borrowed from vidya's
  `ship_now_swap_backend_later_pattern` field note; mirrors
  mabda's wgpu-native pattern.
- `docs/adr/0002-append-after-kind-ffi-invariant.md` —
  documents why `samvada_slot_kind` sits at offset 64 forever.
  Load-bearing for v0→v1 forward-compat across the C-shim
  retirement.
- `docs/adr/README.md` — index updated with both ADRs.
- `tests/samvada.bcyr` — real bench harness replacing the
  no-op `bench("noop", ...)` stub. Four measurements (CPU-only,
  no dbus): `ffi_alloc` (54 ns avg), `ffi_get_slot` (9 ns avg —
  the dispatch hot path), `init_reject_null` (6 ns avg),
  `release_idempotent` (7 ns avg). Numbers are
  developer-machine baseline; `docs/benchmarks.md` will
  capture proper history once v1.0's bench-history.sh shape
  lands.
- `CONTRIBUTING.md` — dev workflow, local gate sweep, FFI-slot
  addition recipe. Was missing from 0.2.0; the docs CI gate
  now requires it.
- `SECURITY.md` — threat model, supported versions, response
  timeline, banned-pattern policy enforced by CI.

### Changed
- `.github/workflows/ci.yml` — full rewrite to match yukti's
  quality bar. Toolchain step uses `curl -sfLO` (fail-fast on
  404) instead of silent download. Adds lint, fmt-check, vet,
  distlib-freshness, ELF magic, smoke-output regex check,
  bench harness smoke run, C shim compile + link test against
  `libsystemd-dev`, security scan (raw execve / fork /
  sys_system / writes-to-{etc,bin,sbin} / large stack
  buffers), docs check (required files + version-in-CHANGELOG).
  Smoke gate matches `ffi slots = N (N*8 bytes)` as a regex
  with cross-checked arithmetic so adding slots in v0.3+
  doesn't silently break.
- `.github/workflows/release.yml` — full rewrite. Accepts
  both `vX.Y.Z` and `X.Y.Z` tag styles. Gates on CI via
  `workflow_call`. Ships 5 artifacts (src tarball,
  `dist/samvada.cyr` renamed, smoke binary, C shim source,
  SHA256SUMS). Extracts the matching `## [VERSION]` block from
  CHANGELOG.md as the release body. 0.x tags ship as
  prereleases.
- `CLAUDE.md` — rewritten to align with agnosticos's
  `example_claude.md` template. Adds P(-1) scaffold-hardening,
  Closeout Pass, full Cyrius Conventions block (18 language
  gotchas), CI/Release section, Documentation Structure,
  reference `.gitignore`, CHANGELOG format. Volatile state
  delegated to `docs/development/state.md`.
- `.gitignore` — regrouped + commented to match the
  first-party template. Adds `cyrius-*.tar.gz`, `SHA256SUMS`,
  `Thumbs.db`. Explicit note that `dist/` stays tracked.
- `src/samvada.cyr` + `tests/samvada.tcyr` — applied
  `cyrius fmt`; minimum-indent on continuation lines (was
  aligned-with-paren). Behavior unchanged.

### Notes
- The three issues that landed during 0.2.0 work (stale dist
  bundle, missing CONTRIBUTING/SECURITY, fmt drift) are now
  hard CI gates; the original workflow would have caught none
  of them.
- mabda's e2e integration against `0.2.0` is the M1 closeout
  gate — `0.2.1` polish does not move M1's status.
- Bench numbers are developer-machine baseline (single laptop,
  not pinned hardware); CI runs `cyrius bench` as a smoke for
  harness wiring but does not gate on the numbers.

## [0.2.0] — 2026-04-30

### Added
- `src/samvada_ffi.cyr` — FFI fn-table layout (9 slots, 72 bytes,
  kind pinned at offset 64 forever per the append-after-kind
  invariant). Slot offsets exposed as fns to dodge the
  global-init-order silent-zero gotcha.
- `deps/samvada_main.c` — libsystemd C-shim entry point.
  `_cyrius_init` + `alloc_init` preamble, then populates the
  fn-table with `sd_bus_*`-backed wrappers and calls
  `samvada_main(table)`. Compiles clean under
  `cc -Wall -Wextra -Werror` against libsystemd 260. Coverage:
  open_system_bus, get_session_path (login1 GetSessionByPID),
  take_device (TakeDevice + dup'd fd), release_device,
  pump_signals (sd_bus_process loop), close_bus,
  subscribe_pause_resume (sd_bus_match_signal), unsubscribe.
- `src/samvada.cyr` graduated from placeholder to real public
  surface: `samvada_init` / `samvada_session_take_device` /
  `samvada_session_release_device` / `samvada_pump_signals` /
  `samvada_release` / `samvada_main`. All errors are negative
  sd-bus errnos so the C convention passes through unchanged.
- 7 new test groups (32 asserts) in `tests/samvada.tcyr`:
  slot-offset pin (freezes the C-shim contract), backend-kind
  pins, alloc/get/set round-trip, null-safety, init rejection
  paths, release idempotency, v0.2.0 version triple.
- `samvada_version()` bumped 0.1.0 -> 0.2.0 packed triple.

### Notes
- Live `sd_bus` calls are NOT exercised by `cyrius test` — they
  need a running system dbus + logind. CPU coverage is the
  structural contract + null-safety paths. The end-to-end
  validation gate is mabda's
  `_backend_native_surface_configure_logind` once that fills.
- samvada itself does not link libsystemd — the consumer builds
  `deps/samvada_main.c` and links libsystemd. See
  `docs/guides/consumer-link.md` for the build recipe.

## [0.1.0] — 2026-04-30

### Added
- Initial project scaffold via `cyrius init samvada`.
- Project identity + roadmap to v1.0 documented in `README.md`
  + `docs/development/roadmap.md`.
- `src/lib.cyr` include chain stub.
- `src/samvada.cyr` API surface placeholder — `samvada_version()`
  returns the 0.1.0 version triple as a u32 (signals the package
  shape exists; real API lands in v0.2.0).
- `cyrius.cyml` populated with description, repository,
  `${file:VERSION}` substitution, expanded stdlib deps for the
  v0.2.0 implementation work (`tagged` for Result types, `fnptr`
  for the fn-table dispatch pattern).
- `tests/samvada.tcyr` extended with a version-triple round-trip
  assertion so the smoke gate exercises the placeholder fn.

### Notes
- No protocol code, no C shim, no real dbus calls. v0.1.0 is the
  baseline-of-work commit so consumers (mabda v3.0) can already
  reference `[deps.samvada]` against a known package shape.
- Architectural strategy committed in `README.md`: C-shim era
  through v0.x mirroring mabda's wgpu-native pattern; both C-shim
  deps retire together at samvada v1.0 / mabda v4.0.
