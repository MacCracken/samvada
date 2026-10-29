# samvada — Current State

> **This file is a SNAPSHOT, not a history.** Refreshed every
> release; it says what is true *now*. Per-release narrative lives
> in [`CHANGELOG.md`](../../CHANGELOG.md), what is left lives in
> [`roadmap.md`](roadmap.md), and durable rules live in
> `CLAUDE.md`.

## Version

**1.1.0** — 2026-10-28. A toolchain minor: cyrius `6.6.20` →
`6.7.18`, stdlib re-vendored, CI moved to Ubuntu 26.04 and every
action to its latest major. One source change: samvada spells no
syscall number of its own under an aarch64 conditional any more
(the stdlib's `sys_sendto` / `sys_recvmsg`, and `clock_gettime` as
the x86 `228` the compiler routes). Consumers need cyrius ≥ 6.6.5.

samvada speaks dbus natively in Cyrius over a raw unix socket.
`samvada_native_init()` gives a consumer a working bus connection
with **no libsystemd, no C shim, no `pkg-config`, nothing to
link**. The libsystemd shim is retained as a fallback and as the
differential reference; its deletion is scheduled for 1.2.0.

Two audits have shipped — the pre-1.0 pass over the native
marshaller (1.0.0) and the A-lane completion (1.0.1) — fixing
fourteen defects between them and closing the reply-forgery
question. **samvada is a stable release, not an audited-hardened
one**; what remains uncovered is in `SECURITY.md` and the roadmap.

## Toolchain

- **Cyrius pin**: `6.7.18` (`cyrius.cyml [package].cyrius` — the
  single source of truth; no version is hardcoded in CI YAML).
- CI additionally pins the **toolchain tarball's sha256**
  (`CYRIUS_TARBALL_SUM`) and the installer script's commit +
  sha256 — at 1.1.0 the installer is the one at cyrius's `6.7.18`
  tag. A version bump without a sum bump fails loudly, which is
  intended.
- **CI runners**: every job in `ci.yml` and `release.yml` runs on
  `ubuntu-26.04`, named explicitly — `ubuntu-latest` still meant
  24.04 at 1.1.0, and GitHub listed 26.04 as a **public preview**
  (`actions/runner-images#14226`). `ubuntu-24.04` is the fallback.
- **Actions**, all by commit SHA (dependabot ages them):
  `actions/checkout` v7.0.1, `actions/upload-artifact` v7.0.2,
  `actions/download-artifact` v8.0.2,
  `softprops/action-gh-release` v3.0.3.
- CLI shape notes for this pin:
  - `cyrius vet` requires an explicit source file
    (`cyrius vet src/main.cyr`); the bare form errors.
  - `cyrfmt` enforces a canonical continuation indent — 2 spaces
    per open paren (4 also accepted).
  - `cyrius distlib` emits a `dist/<name>.deps` sidecar alongside
    the bundle; `cyrius deps` consumes it downstream. From 6.6.18
    the sidecar is **derived by compiling the bundle** on six
    targets, not copied from `[deps] stdlib`, and the bundle
    carries the matching `include` block. It still warns, at 6.7.18,
    that `sys_recvmsg` is undefined on x86_64-windows (roadmap).
  - `cyrius deps` writes `cyrius.lock` for a stdlib-only project
    (6.6.9+); the lock is tracked. `cyrius deps --locked` checks it
    against `lib/` without writing (6.7.6+).
  - `cyrius build` refuses unknown flags (6.6.5+), so
    `--emit-object` errors (`error: cyrius build: unknown option`,
    exit 1) rather than writing an executable.
  - `CYRIUS_DCE=1` genuinely eliminates as of 6.5.72.
  - **No native-aarch64 syscall numbers in samvada's code** (1.1.0).
    ELF-aarch64 builds rewrite x86 numbers through a compat chain
    that matches a number, so samvada uses the stdlib's `sys_*` /
    `SYS_*` names, or the x86 number where none exists
    (`clock_gettime` = 228, as `lib/chrono.cyr` spells it). A CI gate
    in the security job rejects any aarch64 region in `src/` or
    `tests/`. The aarch64 proof is behavioural, under
    `qemu-aarch64 -strace` (CHANGELOG 1.1.0).
  - `object;` builds hide libc-reserved names (`vis=HIDDEN`) as of
    **6.6.2**, which removed the `objcopy -L` step from the
    consumer recipe. Check with `readelf -sW`, not `nm` — `nm`
    reports `T` either way.

## Source

Public surface (`src/samvada.cyr`) — eight fns, frozen, and frozen
**mechanically** by `tools/surface_check.py` in CI:

- `samvada_native_init()` → `0 | -err`. **The supported entry
  point for the native backend.** Hides the FFI table entirely.
  The table is allocated once and reused across init/release.
- `samvada_version()` → packed u32 (1.1.0).
- `samvada_session_take_device(major, minor)` → `fd | -err`.
  Acquires session control on first use (ADR-0004) and hands it
  back if the take fails. Device numbers are range-checked
  `0 ≤ v ≤ UINT32_MAX` **before** the 64→32 narrowing.
- `samvada_session_release_device(major, minor)` → `0 | -err`.
  Drops session control when the last device is released, so
  logind restores the VT. The accounting is a **count**, not an
  identity set — see `SECURITY.md`.
- `samvada_pump_signals()` → `events | -err`. Allocates nothing
  per call.
- `samvada_release()` → `0` (idempotent). **logind revokes devices
  taken via `TakeDevice` when control is released**, so
  outstanding consumer fds become invalid.
- `samvada_init(table)` / `samvada_main(table)` → C-shim entry
  points. A native consumer should not call these.

Native backend — all in `[lib] modules`, so all of it ships in the
consumer bundle:

- `src/dbus_sys.cyr` — `SCM_RIGHTS` fd receive; the `recvmsg` half
  and the cmsg walk, split so every guard is testable.
- `src/dbus_socket.cyr` — `sockaddr_un` (both forms), connect,
  the stdlib's `sys_sendto`+`MSG_NOSIGNAL` write-all, and `SO_RCVTIMEO` armed at
  connect (25 s, so it covers the SASL handshake).
- `src/dbus_frame.cyr` — message framing; owns THE 8192-byte
  receive buffer that `dbus_auth` borrows, plus the fd queue.
- `src/dbus_auth.cyr` — SASL `EXTERNAL`. Can only report success
  by having *seen* `AGREE_UNIX_FD`.
- `src/dbus_marshal.cyr` — encoder. **Poisons** on an out-of-range
  length rather than truncating; the call path refuses to transmit
  a poisoned buffer.
- `src/dbus_unmarshal.cyr` — decoder. Bounds **fail closed**: an
  undeclared message extent refuses rather than permits.
- `src/dbus_session.cyr` — the six logind calls, serial counter,
  reply correlation, error mapping, `dbus_native_populate()`.
  Reply matching requires `DESTINATION` to equal the unique name
  learned at `Hello`, refuses replies that predate their request
  (send-time watermark), and bounds every call on a
  `CLOCK_MONOTONIC` deadline.

Plumbing:

- `src/samvada_ffi.cyr` — fn-table layout, **11 slots / 88 bytes**.
  `kind` is at `+64` and stays there forever (ADR-0002);
  `take_control` `+72` and `release_control` `+80` were appended
  after it. CI cross-checks the C `#define`s against the Cyrius
  slot fns.
- `src/main.cyr` — standalone smoke entry, not linked by consumers.
- `src/lib.cyr` — include chain.
- `src/test.cyr` — `cyrius.cyml [build].test` entry.
- `deps/samvada_main.c` — libsystemd shim. Not linked by `cyrius
  build`; a consumer builds it and calls `samvada_shim_init()`
  from its own `main()`. Retires at 1.2.0.

## Tests

**586 asserts.** Every repair in the 1.0.0 and 1.0.1 audits is
mutation-tested.

| File | Asserts | Covers |
|---|---|---|
| `tests/samvada.tcyr` | 162 | public API, the pure-Cyrius mock backend, FFI layout invariants |
| `tests/dbus_session.tcyr` | 131 | logind layer, reply provenance, fd ownership, timeouts, devnum bounds, hostile SASL |
| `tests/dbus_frame.tcyr` | 79 | framing, the receive buffer, the fd queue |
| `tests/dbus_codec.tcyr` | 76 | marshal/unmarshal, overflow poisoning, fail-closed bounds |
| `tests/dbus_sys.tcyr` | 67 | `SCM_RIGHTS`, cmsg guards, descriptor-leak counting |
| `tests/dbus_auth.tcyr` | 65 | SASL handshake |
| `src/test.cyr` | 6 | `[build].test` entry |

- `tests/fixtures/dbus/` — 15 files of **real captured wire
  traffic** (14 real, 1 synthetic, labelled in `MANIFEST.md`).
- `tests/samvada.bcyr` — 4 CPU baselines. History in
  [`../benchmarks.md`](../benchmarks.md).
- `tests/samvada_live.bcyr` — live-bus bench scaffold. HW-gated;
  SKIPs without a seated session.
- `tests/samvada.fcyr` — fuzz stub.

## Tools

- `tools/surface_check.py` + `tools/public_surface.txt` — freezes
  the 8-fn public surface; **a CI gate**, run right after the
  distlib freshness check.
- `tools/dbus_tap.py` — `AF_UNIX` relay for capturing traffic.
- `tools/dbus_decode.py` — spec decoder for captured bytes.
- `tools/differential/` — runs both backends in one process and
  compares error codes. **CI does not run it**, and it is the only
  gate covering C-shim wrapper *wiring*.

## Dependencies

- stdlib (10 leaves declared): string, fmt, alloc, io, vec, str,
  syscalls, assert, tagged, fnptr — what the tests and smoke binary
  build against.
- `dist/samvada.deps` — the distlib-emitted sidecar: the **4**
  leaves the bundle itself needs (alloc, syscalls, string, fnptr),
  compile-verified. Tracked, so a `[deps.samvada]` consumer
  fetching the release tag gets it alongside `dist/samvada.cyr`.
- **No non-stdlib dependencies.** samvada links nothing.

## Consumers

- **mabda** — pins `[deps.samvada] tag = "1.0.1"` (on cyrius
  6.7.5). Neither 1.0.2 nor 1.1.0 changes the API, so re-pinning is
  optional; its `[deps] stdlib` already declares every leaf it uses,
  so the smaller sidecar does not reach it. It calls only
  `samvada_session_take_device` / `samvada_session_release_device`,
  under `-D MABDA_LOGIND` (off by default).
  - **Downstream check, 1.1.0** (compile-only): a scratch copy of
    mabda on its own 6.7.5 pin builds `programs/smoke.cyr` and
    `programs/native_present_e2e.cyr` under `-D MABDA_LOGIND`
    against the 1.1.0 bundle with no `undefined function` warning.
    Neither was run — both reach DRM master.
  - How: mabda resolves samvada by **git tag**, and `cyrius build`
    re-resolves deps even with `--no-deps`, so a bundle dropped into
    `mabda/lib/` is overwritten before it compiles. In a scratch
    copy, replace `git`/`tag` with `path = "<samvada checkout>"`
    and delete its `cyrius.lock`. Then grep the build log: under
    `-D MABDA_LOGIND` a missing samvada fn is a **warning**
    (`undefined function`) and the build still exits 0 — confirmed
    by building against a bundle with `samvada_session_take_device`
    renamed away.
- No second consumer exists. Everything in the roadmap's wishlist
  is gated on one.

## Next

See [`roadmap.md`](roadmap.md). Next up is **v1.2.0** — retiring
the C shim — gated on the CG lane: a consumer running the native
path from a seated session. **A `TakeDevice` that transfers a real
descriptor has never run, by anyone.**
