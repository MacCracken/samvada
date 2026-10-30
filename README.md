# samvada

**Status: v1.1.2.** samvada speaks dbus natively, in Cyrius, over
a raw unix socket. **No libsystemd, no C shim, no `pkg-config`,
nothing for a consumer to link.**

`samvada` (Sanskrit *saṃvāda*, "dialogue") is the dbus client for
the AGNOS Cyrius suite. Its first consumer is
[mabda](https://github.com/MacCracken/mabda)'s Phase D surface
present path, which needs `org.freedesktop.login1`'s `TakeDevice`
to delegate DRM master from a desktop session.

```cyr
include "lib/samvada.cyr"

var rc = samvada_native_init();                    # connect, no libsystemd
if (rc < 0) { return rc; }
var fd = samvada_session_take_device(226, 0);      # a DRM node
# ... use fd ...
samvada_session_release_device(226, 0);
samvada_release();
```

## Read this before planning against samvada

Two honest caveats, both load-bearing:

- **A `TakeDevice` that transfers a real descriptor has never run,
  by anyone.** No seated session has been available. The
  `SCM_RIGHTS` reply path *is* proven end to end against the live
  bus via `Inhibit` (which returns an fd unprivileged and needs no
  seat) — so samvada demonstrably receives descriptors. What is
  unverified is logind's `TakeDevice` authorisation and DRM-master
  semantics on a seated host.
- **`PauseDevice` / `ResumeDevice` are not delivered to consumers,
  by decision, not omission.** Ratified in
  [ADR-0004](docs/adr/0004-session-control-lifecycle-and-signal-visibility.md).
  `samvada_pump_signals()` drains bus traffic but runs no
  pause/resume callback, and `PauseDeviceComplete` is not sent. A
  consumer needing VT-switch handoff cannot get it from samvada
  today.

Also: **samvada is single-threaded.** Module-scope state carries
no locking; consumers must serialize every call.

## Public API

Eight functions, frozen — and frozen *mechanically* since 1.0.1:
`tools/surface_check.py` diffs `dist/samvada.cyr` against
`tools/public_surface.txt` on every CI run, so adding, removing or
re-signing one now requires editing the manifest.

```text
samvada_native_init()                        -> 0 | -err   ← native entry point
samvada_session_take_device(major, minor)    -> fd | -err
samvada_session_release_device(major, minor) -> 0 | -err
samvada_pump_signals()                       -> events_drained | -err
samvada_release()                            -> 0   (idempotent)
samvada_version()                            -> packed u32 — (1 << 16) | (1 << 8) | 2

samvada_init(table)                          -> 0 | -err   ← C-shim entry
samvada_main(table)                          -> 0 | -err   ← C-shim entry
```

Errors are negative sd-bus errnos, so consumers branch on `< 0`
and the C convention passes through unchanged. Full map —
preconditions, every error code, the test that pins it:
[`docs/architecture/public-api.md`](docs/architecture/public-api.md).

> **The eight above are the only fns a consumer can call.**
> `dist/samvada.cyr` is private-by-default since 1.1.1: naming any
> other fn in it is a compile error (`'…' is private to its file`),
> and CI proves it for every one of them.

## What samvada does natively

All of it, in `src/dbus_*.cyr` — about 3,200 lines of Cyrius
replacing libsystemd:

- **Transport** — `AF_UNIX` connect, `sendto`+`MSG_NOSIGNAL`
  writes, `SO_RCVTIMEO` armed at connect.
- **SASL `EXTERNAL`** — a 48-byte pipelined blob, line-oriented
  reply reader, `AGREE_UNIX_FD` required.
- **Framing** — the 8192-byte receive buffer, message boundaries,
  endianness, the over-cap drain.
- **Marshal / unmarshal** — header fields, alignment, body
  cursors. The encoder reproduces libsystemd's `Hello` byte for
  byte.
- **`SCM_RIGHTS` fd passing** — `recvmsg` with room for 253
  descriptors, `MSG_CMSG_CLOEXEC`, every read bounded to one message
  so each descriptor belongs to the message it arrived with.
- **The logind session layer** — `GetSessionByPID`, `TakeControl`,
  `TakeDevice`, `ReleaseDevice`, `ReleaseControl`.

Reply handling checks that a reply is addressed to us
(`DESTINATION` must equal the unique name learned at `Hello`),
refuses replies that predate their request (a send-time
watermark), and gives up on a wall clock — send and receive. All
three **exceed** the synchronous `sd_bus_call` path they replaced,
which checked none of them. A descriptor belongs to the message
whose bytes it arrived with (every read stops at the end of one
message, as sd-bus's do), and `TakeDevice` hands one out only if the
kernel says it is the device asked for.

**Performance** — native handshake **167 µs** vs libsystemd
**494 µs**; idle `pump_signals` **2138 ns**. Full table:
[`docs/benchmarks.md`](docs/benchmarks.md).

## Build

```sh
cyrius deps                                  # resolve stdlib
cyrius build src/main.cyr build/samvada      # smoke build
cyrius test                                  # every suite; counts in docs/development/state.md
```

### Linking into a consumer

Add the dependency and call `samvada_native_init()`. That is the
whole integration — no C, no `pkg-config`, no `objcopy`.

```toml
[deps.samvada]
git     = "https://github.com/MacCracken/samvada.git"
tag     = "1.1.2"
modules = ["dist/samvada.cyr"]
```

> Commit your `cyrius.lock`. The bundle is fetched from a git tag,
> and samvada's tags are lightweight and unsigned — the lock's
> commit pin is what makes a repointed tag fail loudly. For the first
> resolve, check the bytes against the release's `SHA256SUMS`
> ([`consumer-link.md`](docs/guides/consumer-link.md) § Checking a
> first resolve). See [`SECURITY.md`](SECURITY.md).

The **libsystemd C shim** (`deps/samvada_main.c`) is retained as a
fallback and as `tools/differential/`'s reference backend, and is
scheduled for deletion at 1.2.0. It needs a two-stage build and a
consumer-owned `main()`; that recipe lives in
[`docs/guides/consumer-link.md`](docs/guides/consumer-link.md) and
nowhere else. **New consumers should not use it.**

## Repo layout

```text
samvada/
├── src/
│   ├── main.cyr           — standalone smoke entry (not linked by consumers)
│   ├── lib.cyr            — include chain
│   ├── visibility.cyr     — makes the bundle private-by-default (bundle only)
│   ├── samvada.cyr        — public Cyrius API surface
│   ├── samvada_ffi.cyr    — fn-table slot offsets
│   ├── dbus_sys.cyr       — SCM_RIGHTS / cmsg
│   ├── dbus_socket.cyr    — AF_UNIX transport, recv timeout
│   ├── dbus_frame.cyr     — the receive buffer, framing, fd queue
│   ├── dbus_auth.cyr      — SASL EXTERNAL
│   ├── dbus_marshal.cyr   — encoder
│   ├── dbus_unmarshal.cyr — decoder (bounds fail CLOSED)
│   └── dbus_session.cyr   — the logind calls, reply matching
├── deps/samvada_main.c    — libsystemd shim (fallback; retires at 1.2.0)
├── dist/                  — tracked bundle consumers resolve via [deps.samvada]
├── tests/                 — the suites (counts in docs/development/state.md) + fixtures/ (real captured wire bytes, sd-bus's errno table)
├── tools/
│   ├── cyr_scan.py        — comment/string-aware lexer the gates read code through
│   ├── surface_check.py   — freezes the public surface (CI gate)
│   ├── boundary_check.py  — proves a consumer reaches only that surface (CI gate)
│   ├── security_scan.py   — no spawning, no system paths, no huge buffers (CI gate)
│   ├── stdlib_allowed.txt — the frozen [deps].stdlib list the scan enforces
│   ├── public_surface.txt — the frozen public-fn manifest both surface gates read
│   ├── shim_symbols.txt   — the only symbols the shim's object may leave undefined (CI gate)
│   ├── rxbench.cyr        — receive-path microbench (not a gate)
│   ├── state_sync.py      — regenerates state.md's derived facts; release refuses a stale one
│   ├── shim_slot_check.c  — every shim slot reaches the wrapper it names (CI gate)
│   ├── sdbus_errno_probe.c — libsystemd's errno for each error name (CI gate)
│   ├── dbus_tap.py        — AF_UNIX relay for capturing traffic
│   ├── dbus_decode.py     — spec decoder for captured bytes
│   └── differential/      — runs both backends in one process, compares error codes
├── docs/                  — adr/ architecture/ guides/ development/ audit/ proposals/
├── cyrius.cyml            — package manifest; the toolchain pin lives here
└── VERSION                — 1.1.2
```

The fn-table obeys ADR-0002's **append-after-kind** invariant:
`kind` stays at `+64` forever and new slots append past it, so a
v0 caller reading a v(N+1) table never misreads a fnptr as the
kind word. CI cross-checks the C `#define`s against the Cyrius
slot fns on every run.

## Security

Four audit passes have covered the native backend: the pre-1.0
pass over the native marshaller
([1.0.0](docs/audit/2026-09-09-native-audit.md)), the A-lane
completion (1.0.1), its close-out
([1.1.1](docs/audit/2026-10-29-audit.md)), and a deep P(-1) pass
([1.1.2](docs/audit/2026-10-29-p1-audit.md)) that found a HIGH the
first three missed — any local user could choose the descriptor
`TakeDevice` returned — and was itself re-verified adversarially.
**Take 1.1.2 or later.** **samvada is a stable release, not an
audited-hardened one** — tags are unsigned and the seated
`TakeDevice` path has never run; both are listed in
[`SECURITY.md`](SECURITY.md) and
[the roadmap](docs/development/roadmap.md), not omitted.

Report vulnerabilities per [`SECURITY.md`](SECURITY.md).

## Roadmap

[`docs/development/roadmap.md`](docs/development/roadmap.md) —
what is left. Next up is **v1.2.0**, retiring the C shim, gated on
a consumer running the native path from a seated session.

## License

GPL-3.0-only. See `LICENSE`.
