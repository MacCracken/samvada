# samvada — Current State

> **This file is a SNAPSHOT, not a history.** Refreshed every
> release; it says what is true *now*. Per-release narrative lives
> in [`CHANGELOG.md`](../../CHANGELOG.md), what is left lives in
> [`roadmap.md`](roadmap.md), and durable rules live in
> `CLAUDE.md`.
>
> What sits between `state-sync` markers is **generated** by
> `tools/state_sync.py` from the tree; an edit there is overwritten,
> except the tests table's Covers column, which is written by hand,
> kept, and never carries a count. Everything else is written by
> hand, and the `narrative` marker
> below says which release it was last rewritten for — the tool
> reports the file stale until it matches `VERSION`.

<!-- state-sync:narrative 1.1.2 -->

## At a glance

<!-- state-sync:begin glance -->
| | |
|---|---|
| **Version** | 1.1.2, released 2026-10-29 (`VERSION`, `CHANGELOG.md`) |
| **Toolchain pin** | cyrius `6.7.18` (`cyrius.cyml [package].cyrius`) |
| **CI runners** | `ubuntu-26.04` — every job in `ci.yml`, `release.yml` |
| **Pinned actions** | `actions/checkout` v7.0.1 · `actions/download-artifact` v8.0.2 · `actions/upload-artifact` v7.0.2 · `softprops/action-gh-release` v3.0.3 (by commit SHA) |
| **Public surface** | 8 `public fn`s in `dist/samvada.cyr` (`tools/public_surface.txt`) |
| **Internal fns** | 234, every one private to the bundle (`tools/boundary_check.py`) |
| **FFI table** | 11 slots, 88 bytes; `kind` at `+64` (`src/samvada_ffi.cyr`) |
| **`[lib] modules`** | 10, `src/visibility.cyr` first |
| **stdlib leaves** | 10 declared (`cyrius.cyml [deps]`): string, fmt, alloc, io, vec, str, syscalls, assert, tagged, fnptr; the bundle needs 4 (`dist/samvada.deps`): alloc, syscalls, string, fnptr |
| **Tests** | 1152 asserts in 6 suites, none failing (§ Tests) |
| **sd-bus errno map** | 186 rows (`tests/fixtures/sdbus_errno_map.tsv`) |
| **Wire fixtures** | 15 captures, 14 REAL and 1 SYNTHETIC (`tests/fixtures/dbus/MANIFEST.md`) |
| **CPU bench baselines** | 4 (`tests/samvada.bcyr`) |
<!-- state-sync:end glance -->

## Version

<!-- state-sync:begin version -->
**1.1.2** — released 2026-10-29, on cyrius `6.7.18`.
<!-- state-sync:end version -->

A deep P(-1) pass: one HIGH — any local user could choose the descriptor
`TakeDevice` returned — five MEDIUM, and an adversarial re-verification
of the repairs (`docs/audit/2026-10-29-p1-audit.md`). Descriptors are
attributed by stream position, with every read bounded to one message;
`take_device` checks the descriptor is the device asked for; calls are
bounded by their deadline alone, send and receive; the public layer
holds devices by number; the surface, boundary and security-scan gates
were rebuilt on a comment-aware lexer, and every bypass the reviewers
demonstrated is a planted violation that must fail. Same toolchain as
1.1.0; consumers need cyrius **≥ 6.6.13** (was 6.6.5: the bundle now
calls `fd_wait_ready` and `sys_statx`).

samvada speaks dbus natively in Cyrius over a raw unix socket.
`samvada_native_init()` gives a consumer a working bus connection
with **no libsystemd, no C shim, no `pkg-config`, nothing to
link**. The libsystemd shim is retained as a fallback and as the
differential reference; its deletion is scheduled for 1.2.0.

Six audit passes have shipped (`SECURITY.md` § Audit History), four of
them over the native backend — the pre-1.0 pass over the native
marshaller (1.0.0), the A-lane completion (1.0.1), its close-out
(1.1.1, `docs/audit/2026-10-29-audit.md`) and the 1.1.2 P(-1) pass
(`docs/audit/2026-10-29-p1-audit.md`), which found a HIGH the first
three missed. **samvada is a stable
release, not an audited-hardened one**: tags are unsigned, and the
seated `TakeDevice` path has never run. Both are in `SECURITY.md` and
the roadmap.

## Toolchain

- **Cyrius pin**: `cyrius.cyml [package].cyrius` is the single
  source of truth (§ At a glance has the version); no version is
  hardcoded in CI YAML.
- CI additionally pins the **toolchain tarball's sha256**
  (`CYRIUS_TARBALL_SUM`) and the installer script's commit +
  sha256 — at 1.1.0 the installer is the one at cyrius's `6.7.18`
  tag. A version bump without a sum bump fails loudly, which is
  intended.
- **CI runners**: every job names its runner explicitly (§ At a
  glance has it) — `ubuntu-latest` still meant
  24.04 at 1.1.0, and GitHub listed 26.04 as a **public preview**
  (`actions/runner-images#14226`). `ubuntu-24.04` is the fallback.
- **Actions** are pinned by commit SHA, and dependabot ages them;
  § At a glance lists each one's version.
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
    that `sys_recvmsg` is undefined on x86_64-windows — a known warning, not
    work: samvada is Linux-only (roadmap § Out of scope).
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

Public surface — eight `public fn`s (`src/samvada.cyr`, plus
`samvada_native_init` in `src/dbus_session.cyr`), frozen
**mechanically** by `tools/surface_check.py` in CI. They are the
**only** fns a consumer can name: the bundle is private-by-default
(`src/visibility.cyr`, first in `[lib] modules`), and
`tools/boundary_check.py` proves every other fn is refused:

- `samvada_native_init()` → `0 | -err`. **The supported entry
  point for the native backend.** Hides the FFI table entirely.
  The table is allocated once and reused across init/release.
- `samvada_version()` → `VERSION`, packed `(major << 16) | (minor << 8) | patch`.
- `samvada_session_take_device(major, minor)` → `fd | -err`.
  Acquires session control on first use (ADR-0004) and hands it
  back if the take fails. Device numbers are range-checked
  `0 ≤ v ≤ UINT32_MAX` **before** control is taken; a device already
  held is `-22` without a round trip.
- `samvada_session_release_device(major, minor)` → `0 | -err`.
  Devices are held **by number** (`(major << 32) | minor`, 256
  entries); a release forgets the device whatever logind answers, and
  control is dropped when the table is empty, so logind restores the VT.
  The table is a copy of logind's state that nothing re-syncs
  (`SECURITY.md`).
- `samvada_pump_signals()` → `events | -err`; `-ECONNRESET` once the
  connection is gone (1.1.1), any other receive error since 1.1.2.
  Drains until the socket is empty (256 events / 1024 receives per
  call). Allocates nothing per call.
- `samvada_release()` → `0` (idempotent). **logind revokes devices
  taken via `TakeDevice` when control is released**, so
  outstanding consumer fds become invalid.
- `samvada_init(table)` / `samvada_main(table)` → C-shim entry
  points. A native consumer should not call these.

Native backend — all in `[lib] modules`, so all of it ships in the
consumer bundle:

- `src/dbus_sys.cyr` — `SCM_RIGHTS` fd receive: room for 253
  descriptors (`SCM_MAX_FD`), every cmsg walked, `MSG_CTRUNC` keeps the
  bytes; the `recvmsg` half and the walk split so every guard is
  testable.
- `src/dbus_socket.cyr` — `sockaddr_un` (both forms), connect (the
  socket moved above stdio), and `SO_RCVTIMEO` / `SO_SNDTIMEO` armed at
  connect (25 s): the backstop when the clock has failed.
- `src/dbus_frame.cyr` — message framing; owns THE 8192-byte
  receive buffer that `dbus_auth` borrows, and the head message's
  descriptors. Every read is bounded to the head message
  (`dbus_frame_want` / `dbus_frame_prepare`), so a descriptor belongs to
  the message it arrived with, and `dbus_frame_advance` closes every one
  that message did not hand out.
- `src/dbus_auth.cyr` — SASL `EXTERNAL`. Can only report success
  by having seen `OK <32-hex GUID>` and then `AGREE_UNIX_FD`.
- `src/dbus_marshal.cyr` — encoder. **Poisons** on an out-of-range
  length rather than truncating; the call path refuses to transmit
  a poisoned buffer.
- `src/dbus_unmarshal.cyr` — decoder. Bounds **fail closed**: an
  undeclared message extent refuses rather than permits. Every message
  the reply loop considers is validated whole first
  (`dbus_unmarshal_validate`, sd-bus's `bus_message_parse_fields` rules).
- `src/dbus_session.cyr` — the five logind calls (plus Hello), serial counter,
  reply correlation, error mapping, `dbus_native_populate()`.
  Reply matching requires `DESTINATION` to equal the unique name
  learned at `Hello`, refuses replies that predate their request
  (a send-time watermark, a stream position since 1.1.2), and bounds
  every call, send and receive, on a `CLOCK_MONOTONIC` deadline: each
  wait is a poll for the time left. Error names map exactly as sd-bus
  maps them (`tests/fixtures/sdbus_errno_map.tsv`); a dead connection
  is latched (`_dbs_closed`) and answered as sd-bus answers it;
  `take_device` hands out the reply's `h`-indexed descriptor only if
  `statx` says it is the device asked for; the connect handshake is
  `dbus_session_sasl` + `dbus_session_hello`, driven over a socketpair
  in the tests; no call allocates.
- `src/visibility.cyr` — a bare `private`, bundle-only: it makes
  `dist/samvada.cyr` private-by-default. Not in `src/lib.cyr`, so
  samvada's own build and tests still reach the internals.

Plumbing:

- `src/samvada_ffi.cyr` — fn-table layout (§ At a glance has the
  slot count and size).
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

<!-- state-sync:begin tests -->
**1152 asserts**, in 6 suites, none failing.

| File | Asserts | Covers |
|---|---|---|
| `tests/samvada.tcyr` | 238 | public API, the pure-Cyrius mock backend, FFI layout invariants; the held-device table and the control lifecycle |
| `tests/dbus_session.tcyr` | 512 | logind layer, reply provenance, descriptor attribution (the H1 proofs of concept), the `rdev` check, timeouts and the send deadline, devnum bounds, hostile SASL, the handshake and Hello over a socketpair; the sd-bus error table (every row of the fixture file, read at run time); disconnect, logind restart, Pause/Resume orderings; zero heap per call; sustained fd traffic |
| `tests/dbus_frame.tcyr` | 135 | framing, the receive buffer, the head message's descriptors; bounded reads never cross a message; linear compaction, over-cap floods, the 128 MiB cap |
| `tests/dbus_codec.tcyr` | 105 | marshal/unmarshal, overflow poisoning, fail-closed bounds, the field walk, whole-array validation, object paths |
| `tests/dbus_sys.tcyr` | 91 | `SCM_RIGHTS`, every cmsg walked, 253-descriptor room, `MSG_CTRUNC` keeping the bytes, descriptor-leak counting |
| `tests/dbus_auth.tcyr` | 71 | SASL handshake, the `OK` GUID, the send path |
<!-- state-sync:end tests -->

`cyrius test` with no operand also runs `src/test.cyr`, the
`[build].test` entry, which asserts nothing; the `N passed` line it
prints last counts files, not asserts (1.1.1's table misread it).
Every repair in the 1.0.0, 1.0.1, 1.1.1 and 1.1.2 audits is
mutation-tested but one: 1.1.2's EINTR retries (P1-5) have no test
(`docs/audit/2026-10-29-p1-audit.md` § Still open).

On aarch64 (`qemu-aarch64` user mode) every suite passes but for five
asserts that need `MSG_CTRUNC`, which qemu-user never delivers to the
guest (roadmap).

- `tests/fixtures/dbus/` — **real captured wire traffic**, and the
  synthetic captures, each labelled REAL or SYNTHETIC in
  `MANIFEST.md`.
- `tests/samvada.bcyr` — the CPU baselines. History in
  [`../benchmarks.md`](../benchmarks.md).
- `tests/samvada_live.bcyr` — live-bus bench scaffold. HW-gated: it
  benches only a `kind = LIBSYSTEMD` table on which `samvada_init`
  succeeds and SKIPs otherwise, so every standalone and CI build SKIPs.
- `tests/samvada.fcyr` — fuzz stub.

## Tools

- `tools/cyr_scan.py` — the comment-, string- and directive-aware
  lexer the surface, boundary and security-scan gates read code
  through (1.1.2): declarations matched anywhere on a line,
  `pub`/`public` both, every `fn` and `public` token accounted for.
  `--self-test` runs first in CI.
- `tools/surface_check.py` + `tools/public_surface.txt` — freezes
  the public surface and requires the bundle's `private` marker;
  **a CI gate**, run right after the distlib freshness check, with a
  planted violation per demonstrated bypass that must fail.
- `tools/boundary_check.py` — compiles consumers against the bundle:
  every `public fn` reachable, every other fn refused. **A CI gate**.
- `tools/security_scan.py` — no process creation, no system paths, no
  ≥ 64 KB static buffers; a positive syscall allow-list, no executable
  memory, no `#define` / `enum` / `#derive` in the bundle; also judges
  `dist/samvada.cyr` whole and `cyrius.cyml` (1.1.2). **A CI gate**,
  with `--self-test`.
- `tools/stdlib_allowed.txt` — the frozen `[deps].stdlib` list the scan
  holds `cyrius.cyml` to, each module with its reason.
- `tools/rxbench.cyr` — the receive-path microbench (benchmarks Run 10);
  not a gate.
- `tools/state_sync.py` — regenerates this file's `state-sync`
  blocks from the tree (it runs every suite to count asserts) and,
  with `--check`, reports them stale. ci.yml warns on a stale
  snapshot; release.yml refuses to release one. `--self-test` runs
  first in both.
- `tools/shim_slot_check.c` — links the C shim against a fake
  libsystemd and calls every slot, checking what each sends: member,
  destination, interface, path and arguments (1.1.2). **A CI gate**,
  with sed-mutated shims that must fail.
- `tools/shim_symbols.txt` — every symbol the shim's object may leave
  undefined (`nm -u`), each justified; the bundle's own object must
  leave none, and the shim's objects may contain no syscall
  instruction (`objdump`). **CI gates**.
- `tools/sdbus_errno_probe.c` — asks libsystemd for each error name's
  errno; produced `tests/fixtures/sdbus_errno_map.tsv`, and CI's
  c-shim job re-runs it against the runner's libsystemd.
- `tools/dbus_tap.py` — `AF_UNIX` relay for capturing traffic.
- `tools/dbus_decode.py` — spec decoder for captured bytes. CI's c-shim
  job decodes every `tests/fixtures/dbus/*.bin` with it, then checks each
  against `MANIFEST.md`'s size, sha256 prefix and REAL/SYNTHETIC label,
  with a swapped-label MANIFEST that must fail. **A CI gate**.
- `tools/differential/` — runs both backends in one process and
  compares error codes. **CI does not run it**: it takes logind session
  control, and refuses to start without `SAMVADA_DIFF_TAKES_CONTROL=1`.
  The shim's wrapper wiring is gated by `tools/shim_slot_check.c`.

## Dependencies

- stdlib — the leaves `cyrius.cyml [deps]` declares (§ At a glance):
  what the tests and smoke binary build against.
- `dist/samvada.deps` — the distlib-emitted sidecar: the leaves the
  bundle itself needs (§ At a glance), compile-verified. Tracked, so a `[deps.samvada]` consumer
  fetching the release tag gets it alongside `dist/samvada.cyr`.
- **No non-stdlib dependencies.** samvada links nothing.

## Consumers

- **mabda** — pins `[deps.samvada] tag = "1.0.1"` (on cyrius
  6.7.5). No later release changes the API it calls, but 1.0.1 is
  unsupported and carries P1-1 (HIGH) on the native backend
  (`SECURITY.md` § Supported Versions), so re-pinning to 1.1.2 is
  due — its 6.7.5 pin already meets the ≥ 6.6.13 floor; its
  `[deps] stdlib` already declares every leaf it uses, so the
  smaller sidecar does not reach it. It calls only
  `samvada_session_take_device` / `samvada_session_release_device`,
  under `-D MABDA_LOGIND` (off by default).
  - **Downstream check, 1.1.2** (compile-only, as for 1.1.1): a
    scratch copy of mabda on its own 6.7.5 pin builds
    `programs/smoke.cyr` and `programs/native_present_e2e.cyr` under
    `-D MABDA_LOGIND` against the 1.1.2 bundle (byte-identical to
    `dist/samvada.cyr`), with no `undefined function` and no `private
    to its file`. Neither was run — both reach DRM master.
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
