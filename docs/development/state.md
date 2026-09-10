# samvada — Current State

> Refreshed every release. CLAUDE.md is preferences/process/procedures
> (durable); this file is **state** (volatile).

## Version

**0.11.0** — 2026-09-09. **N6 — cutover.** The native dbus backend
**ships in the consumer bundle**. A probe built against
`dist/samvada.cyr` alone runs `samvada_native_init() -> 0` and
`take_device -> -13` with **zero libsystemd references in the
binary** — no C shim, no `pkg-config`, no two-stage build. One new
public fn, `samvada_native_init()`, which exists so consumers never
see a fn-table pointer. `dist/samvada.cyr` goes 332 → 2008 lines and
26 → 27 exported `samvada_*` fns; the "bundle unchanged" criterion
that governed N1–N5 retires here by design. **Error-code parity
with the C shim is verified** by `tools/differential/`, which runs
both backends in one process: `init`, `take_device`,
`release_device` and `release` all match. `pump_signals`' event
count differs and that is correct, not a breach — both backends
handle the same `NameAcquired` once, at different points. The shim
stays in tree as the reference until N7.

**0.10.0** — 2026-09-09. **N5** — the logind session layer.
**samvada's frozen public API now runs on a fully native Cyrius
backend** against the real bus, `kind = PURE_CYRIUS`, no libsystemd:
`samvada_init -> 0` (connect + SASL + Hello + GetSessionByPID +
TakeControl, all native) and `take_device -> -13` — `AccessDenied`,
a **device-level** error, not the `-22` `NotInControl` that meant
the call never got that far. `src/dbus_session.cyr` plus 71 asserts
(45 at N5; +13 from the 1.0.0 reply-forgery audit, which pinned the
`SCM_RIGHTS` reply path and the reply matcher's type gate; +13 more
for the provenance hardening — `DESTINATION` must be us, absent
destination stays permitted, and a pre-planted reply cannot answer a
later call; +9 for the wall-clock timeouts). **1.0.1 adds 68 more for
the A-lane repairs — fd ownership on both routes, the devnum upper
bound, null session paths on all four fns, zero-allocation pump and
reply paths, a poisoning marshaller, fail-closed unmarshal bounds, a
non-disarming deadline, and hostile SASL input. 585 asserts across
the suite.**
Every native fn is written to the C shim's ABI including the
vestigial `bus` argument and out-pointer pairs; the tests call each
one directly at its declared arity so a wrong arity fails the BUILD
rather than crashing through `fncallN`. Session selection decided:
document, not validate. No public API or bundle change.

**0.9.0** — 2026-09-09. **N4** — marshal and unmarshal. **Native
Cyrius now speaks dbus end to end**: connect, SASL, build a `Hello`
byte-identical to libsystemd's (128/128 bytes against the capture),
send it, and decode the reply — the bus accepted our bytes and
answered with `:1.20566`. One read carried two messages (101 + 181)
and the framer split them correctly; the `0xFFFFFFFF` serial read
back positive. This also closes **N3's deferred exit criterion**.
Two modules: `src/dbus_marshal.cyr` and `src/dbus_unmarshal.cyr`,
48 new asserts. Field order is never assumed — fields are located
by code, and a test builds the same message in a different order to
prove it. No public API or bundle change.

**0.8.0** — 2026-09-09. **N3** — transport, auth and framing.
**Native Cyrius code now authenticates to a real dbus daemon**:
connect to `/run/dbus/system_bus_socket`, the 48-byte pipelined SASL
blob, handshake complete, zero residual, and no libsystemd anywhere
in that path. Three modules — `src/dbus_frame.cyr` (owns the single
receive buffer; the framer), `src/dbus_socket.cyr` (both
`sockaddr_un` forms, connect, `sendto`+`MSG_NOSIGNAL` write-all),
`src/dbus_auth.cyr` (line-oriented SASL over the shared buffer).
210 new asserts. Also fixed **two live defects in 0.7.0's
`dbus_sys.cyr`**: `MSG_CTRUNC` leaked every descriptor the kernel
had installed (measured: 3 fds sent -> `MSG_CTRUNC` set *and* 2
installed, both leaked), and a 4-byte read past the control buffer.
`Hello` does not round-trip yet — that needs the marshaller (N4).
No public API or bundle change; native modules stay out of
`[lib] modules` until the N6 cutover.

**0.7.1** — 2026-09-09. **N2** — the golden byte corpus. 15 fixtures
of real dbus wire traffic in `tests/fixtures/dbus/`, captured with
the new `tools/dbus_tap.py` relay while the libsystemd C shim still
exists (it is deleted at the v1.0 cutover, and the reference goes
with it), decoded and gated by `tools/dbus_decode.py`. The capture
**corrected our own `dbus-marshalling.md`**: SASL is one pipelined
48-byte write answered by three lines in a single read, not the
six-step ping-pong documented — a reader built to the old text
hangs. Also established: one read carries two complete messages;
alignment is per-message, not per-buffer; header field
order is arbitrary; the bus's first reply carries serial
`0xFFFFFFFF`. The one SYNTHETIC fixture (a successful `TakeDevice`
reply, uncapturable without a seated session) is derived from a
REAL fd-bearing `Manager.Inhibit` reply rather than from prose. The
second-host capture criterion is **outstanding** and says so in the
MANIFEST. No public API or bundle change.

**0.7.0** — 2026-09-09. **N1** — the first executable module of the
native Cyrius dbus backend. `src/dbus_sys.cyr` receives an fd over
a unix socket via `SCM_RIGHTS`, proven over a real `socketpair`
with no bus, no logind and no hardware: the received descriptor is
shown to be the *same open file* by `fstat` dev/ino, `FD_CLOEXEC`
is asserted via `F_GETFD`, and 200 round trips leak nothing.
Writing the tests found a real defect — `CMSG_SPACE(1 fd)` and
`CMSG_SPACE(2 fds)` are both 24 bytes, so a two-fd message fits a
one-fd buffer without `MSG_CTRUNC` and the surplus descriptor was
silently leaked; the parser now closes it. The cmsg walk is split
from the syscall so every guard is reachable from a test (the
kernel validates ancillary data before `recvmsg` returns, so a
forged cmsg can never arrive through a socket). No public API
change and **no bundle change** — still 26 exported fns, with
`dbus_sys` deliberately out of `[lib] modules` until the N6
cutover. 63 new asserts (225 total across both suites), every
guard mutation-proven.

**0.6.0** — 2026-09-09. **N0** — the first milestone of the road to
native Cyrius dbus. Ratifies
[ADR-0004](../adr/0004-session-control-lifecycle-and-signal-visibility.md)
and fixes a console regression 0.5.1 introduced: `samvada_init()`
called `TakeControl`, and logind's `TakeControl` runs
`session_prepare_vt()` — `KDSKBMODE=K_OFF` plus
`KDSETMODE=KD_GRAPHICS` — so initializing samvada **blanked the
console and killed the keyboard** on any seated VT session, before
any device was requested. Invisible in testing because
`session_prepare_vt` returns early for `vtnr < 1` and every session
on this host is seatless. Session control is now scoped to device
ownership: acquired lazily on the first device take, returned if
that take fails, dropped on the last release and at
`samvada_release()`. ADR-0004 also ratifies that samvada **does not
deliver `PauseDevice`/`ResumeDevice` in 0.x** — a documented
property now, not the MED-7 gap. **No new FFI slot** (still 11
slots / 88 bytes), so N1 is unblocked. Tests 131 → 162, each new
pin mutation-proven. No public signature change.

**0.5.2** — 2026-09-09. Toolchain patch. Pinned Cyrius bumped
`6.6.1` → `6.6.2`, which carries the upstream fix for the
symbol-collision defect samvada filed during the 0.5.1 audit
(credited upstream as *"filed by samvada, reproduced and
fixed"*). The `objcopy` localization step drops out of the
consumer build: 6.6.2 emits the libc-reserved names with
`vis=HIDDEN` instead of `vis=DEFAULT`, verified here by
`readelf` **and** by a full consumer link that hangs on 6.6.1 and
returns `samvada_init -> 0` / `take_device -> -13` on 6.6.2. The
fix is to visibility, not binding — `nm` shows `T` either way.
No source-logic change; 131 tests pass unchanged. 6.6.2's
headline `lib/tagged.cyr` repair is a no-op for samvada, which
calls none of that API.

**0.5.1** — 2026-09-09. **P(-1) hardening + security audit
release.** 2 CRITICAL / 10 MEDIUM / 16 LOW findings fixed; tests
38 → 114; every repair mutation-proven. Report:
[`docs/audit/2026-09-09-audit.md`](../audit/2026-09-09-audit.md).

The two CRITICALs share one cause — nothing in the repo had ever
run against a real dbus daemon or a real consumer link:
- **CRIT-1**: `samvada_session_take_device()` could never succeed
  in any environment. logind requires `TakeControl` before
  `TakeDevice`; samvada never sent it, so every call got
  `NotInControl`. Proven live. Fixed by appending two slots after
  `kind` (ADR-0002): `take_control` +72, `release_control` +80;
  `samvada_ffi_size()` 72 → 88; `kind` unmoved at +64.
- **CRIT-2**: the documented consumer link could never work — the
  shim defined `main()` unconditionally and every consumer owns
  its own. **BREAKING**: `main()` is now behind
  `-DSAMVADA_STANDALONE_MAIN`; consumers call
  `samvada_shim_init()` from their own `main()`. The fn-table is
  now `static` (samvada borrows the pointer, it never copies).

No public Cyrius API signature changed. The break is at the
C/link surface only.

Also: `F_DUPFD_CLOEXEC` instead of `dup()` (the DRM fd leaked
across `execve`); `release_device` returned 1 not 0; `init` now
self-cleans on late failure and whitelists backend kinds; the
signal drain is capped at 256/call; CI's security scan (which
could not fire) rewritten and widened to `deps/`; the toolchain
installer pinned + checksummed and workflow tokens scoped to
`contents: read`. **[ADR-0003](../adr/0003-native-cyrius-dbus.md)
adopts native Cyrius dbus as the v1.0 path** and
[`roadmap.md`](roadmap.md) is rewritten around it.

**0.5.0** — 2026-09-09. Toolchain update release. Pinned
Cyrius toolchain bumped `6.2.6` → `6.6.1` (four minor lines
within the 6.x series — no major-line jump). Full local gate
sweep (lint, fmt --check, vet, distlib, build, C-shim
compile-check, test, bench) passes clean with no source-logic
change. Minor bump rather than a patch because the move is
measurable, not just a pin edit: `CYRIUS_DCE=1` **eliminates
for the first time** (the pass padded instead of compacting
before cyrius 6.5.72) — release binary **80,904 B → 15,368 B
(−81.0 %)** — and `ffi_alloc` drops **63 ns → 28 ns
(−55.6 %)**. `samvada_version()` packed triple → `(0,5,0)`;
version-triple pin updated lock-step. `dist/samvada.cyr`
regenerated under 6.6.1's distlib (269 lines, unchanged shape);
`dist/samvada.deps` is **new** — 6.6.x's distlib emits a
stdlib-leaf sidecar next to the bundle, now tracked to match
yukti/mabda. Two files reformatted by 6.6.x's `cyrfmt`
continuation-indent rule (whitespace only, no token changed).
No public API change — the exported symbol set is identical to
0.4.1 (26 fns). 38 tests pass (unchanged count). See CHANGELOG
0.5.0 and `docs/benchmarks.md` Run 4.

**0.4.1** — 2026-06-14. Toolchain update release. Pinned
Cyrius toolchain bumped `6.0.40` → `6.2.6` (a minor-line move
within the 6.x series — no major-line jump, no CLI-surface
change). Full local gate sweep (lint, fmt --check, vet,
distlib, build, C-shim compile-check, test) passes clean with
no source-logic change. Patch bump reflects the toolchain
minor-line move, not new protocol surface. `samvada_version()`
packed triple → `(0,4,1)`; version-triple pin updated
lock-step. `dist/samvada.cyr` regenerated under 6.2.6's distlib
(269 lines, unchanged shape — version stamp + triple only). No
public API change. 38 tests pass (unchanged count). See
CHANGELOG 0.4.1.

**0.4.0** — 2026-06-02. Road-to-v1.0 batch. No public API
change (`samvada_version()` → `(0,4,0)`; `dist/samvada.cyr`
shape unchanged). Added the HW-gated live-bus bench harness
scaffold (`tests/samvada_live.bcyr` — handshake / TakeDevice /
signal-pump, SKIPs without a libsystemd backend; CI runs it as
a skip-path smoke). Closed two v1.0 criteria: **Public API
frozen** (certification audit fixed the missing `-EBUSY`
documentation in `public-api.md` + the double-init test-map
row) and **CHANGELOG complete from v0.1.0**. Scoped the v1.0
pivot in `docs/proposals/0001-v1-dbus-backend-pivot.md` (module
map, LoC, no-hardware test strategy, risks, de-risking spike) —
decision still deferred to mabda v4.0. 38 tcyr asserts
unchanged; the live-bus scaffold is exercised as a separate CI
skip-path smoke (`cyrius bench tests/samvada_live.bcyr`).

**0.3.0** — 2026-06-02. Toolchain/language update release.
Pinned Cyrius toolchain bumped `5.7.48` → `6.0.40` (a 5.7.x →
6.0.x major-line jump). Full local gate sweep (lint, fmt
--check, vet, distlib, build, C-shim compile-check, test,
bench) passes clean with no source-logic change. Minor bump
reflects the toolchain major-line jump, not new protocol
surface — M2 ("generalize beyond logind") feature scope stays
unscoped pending a second AGNOS consumer. `samvada_version()`
packed triple → `(0,3,0)`; version-triple pin updated
lock-step. `dist/samvada.cyr` regenerated under 6.0.40's
distlib emitter (269 lines, was 270 — a collapsed duplicate
blank line in the generated header, artifact-only). No public
API change. 38 tests pass (unchanged count). Note: 6.0.40's
`cyrius fmt` takes the file before the `--check` flag
(`cyrius fmt <file> --check`) — arg order changed from the
5.7.x line. CI/release toolchain install modernized to the
canonical upstream `scripts/install.sh` (the 6.0.x toolchain is
version-aware — resolves the pin against
`~/.cyrius/versions/<pin>/lib`, which the old flat-`cp` install
never produced); CI fmt gate switched to the exit-code form
(6.0.x `--check` emits no stdout to diff). See CHANGELOG 0.3.0.

**0.2.2** — 2026-05-01. P(-1) hardening pass. One HIGH +
one MED correctness defect fixed (HIGH-1: `samvada_init`
double-init leak → `-EBUSY` reject + scratch reuse;
MED-1: `sb_take_device` stale-errno on `fd<0` → explicit
`-EBADF`, errno captured before sd-bus cleanup). Three new
docs land the v1.0 references: `docs/architecture/public-api.md`
(canonical surface map), `docs/sources.md` (consolidated
protocol citations), `docs/benchmarks.md` (perf-history seed,
two runs captured). Audit at `docs/audit/2026-05-01-hardening-review.md`
files the methodology. 38 tests (was 32; +6 in
`test_init_rejects_double_init`). M1 closeout gate unchanged
— mabda 3.0.0-rc.1 ships the consumer body but live-bus e2e
is rc.2-deferred.

**0.2.1** — 2026-04-30. Polish patch on top of 0.2.0 — no
public API change, every addition is internal tooling, docs,
or test scaffolding. ADR-0001 + ADR-0002 filed. Real bench
harness replaces the no-op stub (4 CPU baselines: ffi_alloc
54ns, ffi_get_slot 9ns, init_reject_null 6ns,
release_idempotent 7ns). CI gate set hardened to yukti
parity. mabda is integrating against 0.2.0 — M1 closeout gate
unchanged.

**0.2.0** — 2026-04-30. C-shim FFI scaffold complete: fn-table
layout pinned, `deps/samvada_main.c` populating sd_bus wrappers,
`src/samvada.cyr` exposing the v0.x stable public surface (init /
take_device / release_device / pump_signals / release).
Live-bus end-to-end validation pending mabda's
`_backend_native_surface_configure_logind` consumer.

## Toolchain

- **Cyrius pin**: `6.6.2` (in `cyrius.cyml [package].cyrius`)
- Local cyrius bin: `6.6.2` — pin and local match; bumped in
  0.5.2 from `6.6.1` (a patch carrying samvada's own upstream
  fix), and in 0.5.0 from `6.2.6` (four 6.x minor lines). The full gate
  sweep passes clean under 6.6.1 with no source-logic change.
  The `cyrius fmt <file> --check` arg order (introduced in the
  0.3.0 6.0.x jump) is unchanged.
- CLI shape notes for this pin:
  - `cyrius vet` requires an explicit source file
    (`cyrius vet src/main.cyr`, which is what CI already runs);
    the bare form errors with a usage line.
  - `cyrfmt` enforces a canonical continuation indent — 2
    spaces per open paren, 4 also accepted. Pre-6.6 trees that
    wrapped continuations at the statement indent report drift
    on the first offending line.
  - `cyrius distlib` emits a `dist/<name>.deps` sidecar
    alongside the bundle (stdlib leaves the fold needs in
    scope); `cyrius deps` consumes it downstream.
  - `CYRIUS_DCE=1` genuinely eliminates as of cyrius 6.5.72 —
    before that it NOP-ed and padded, so the release build
    carried its dead code.
  - `object;` builds hide libc-reserved names (`vis=HIDDEN`) as
    of **6.6.2**. Below that, a consumer linking a Cyrius object
    against a C library must `objcopy -L` them or the C library
    binds to Cyrius's incompatible implementations.

## Source

- `src/main.cyr` — smoke entry point ("hello from samvada").
- `src/lib.cyr` — include chain: samvada_ffi.cyr → samvada.cyr.
- `src/samvada_ffi.cyr` — fn-table layout (**11 slots, 88 bytes**
  as of 0.5.1; `take_control` +72 and `release_control` +80 were
  appended after `kind`, which stays at +64) + alloc/get/set
  helpers.
- `src/samvada.cyr` — public API surface (v0.x stable). Full
  surface map in `docs/architecture/public-api.md`.
  - `samvada_version()` → packed u32 (0.11.0).
  - `samvada_init(table)` → 0 | -err (opens bus, looks up
    session, **takes session control**). Returns `-EBUSY` (`-16`)
    on re-init without release as of 0.2.2; self-cleans on every
    late failure as of 0.5.1.
  - `samvada_session_take_device(major, minor)` → fd | -err.
    Acquires logind session control on first use (0.6.0,
    ADR-0004) and returns it if the take fails.
  - `samvada_session_release_device(major, minor)` → 0 | -err.
    Drops session control when the last device is released, so
    logind restores the VT.
  - `samvada_pump_signals()` → events | -err.
  - `samvada_release()` → 0 (idempotent). Drops session
    control and zeroes scratch as of 0.5.1. **Note**: logind
    revokes devices taken via `TakeDevice` when control is
    released, so outstanding consumer fds become invalid.
  - `samvada_main(table)` → 0 | -err (C-shim entry point).
  - `samvada_native_init()` → 0 | -err (0.11.0). The supported way
    to use the native backend; hides the FFI table entirely.
- `src/dbus_session.cyr` — **native backend, N5**. The six logind
  calls, serial counter, reply correlation, error mapping, and
  `dbus_native_populate()`.
- `src/dbus_marshal.cyr` — **native backend, N4**. Message encoder.
- `src/dbus_unmarshal.cyr` — **native backend, N4**. Field lookup
  by code + an aligned body cursor.
- `src/dbus_frame.cyr` — **native backend, N3**. Message framing;
  owns THE receive buffer that `dbus_auth` borrows.
- `src/dbus_socket.cyr` — **native backend, N3**. `sockaddr_un`
  (both forms), connect, `sendto`+`MSG_NOSIGNAL` write-all.
- `src/dbus_auth.cyr` — **native backend, N3**. SASL handshake.
- `src/dbus_sys.cyr` — **native backend, N1**. `SCM_RIGHTS` fd
  receive: `dbus_sys_recv_fd()` (the `recvmsg` half) and
  `dbus_sys_parse_scm_rights()` (the cmsg walk). NOT in
  `[lib] modules` — native modules stay out of the consumer bundle
  until N6.
- `src/test.cyr` — top-level test entry referenced by
  `cyrius.cyml [build].test`.
- `deps/samvada_main.c` — libsystemd C shim. Not linked by
  `cyrius build`; consumers build it and link libsystemd, calling
  `samvada_shim_init()` from their own `main()`. The shim's own
  `main()` is compiled only under `-DSAMVADA_STANDALONE_MAIN`
  (0.5.1, breaking).

## Tests

- `tests/samvada.tcyr` — **162 asserts** (was 38) across 23
  groups. Adds a **pure-Cyrius mock backend** (`mock_table_new`)
  giving `take_device` / `release_device` / `pump_signals` real
  behavioural coverage with no hardware — the long-standing
  "HW-gated, untestable" claim was false. Also pins: the
  `TakeControl` dispatch (CRIT-1 regression), init self-clean on
  late failure, dispatch wiring per slot, fd sign-extension across
  int32, `samvada_main`, `set_slot`'s null guard, and the
  append-after-kind layout invariants. Vacuous assertions removed
  and the load-bearing ones mutation-proven. Live sd_bus calls are
  still HW-gated and not in this suite.
- `tests/samvada.bcyr` — 4 CPU baselines (`ffi_alloc`,
  `ffi_get_slot`, `init_reject_null`, `release_idempotent`).
  History tracked in `docs/benchmarks.md`.
- `tests/samvada_live.bcyr` — live-bus bench harness scaffold
  (0.4.0): handshake / TakeDevice / signal-pump. HW-gated —
  SKIPs without a libsystemd-backed table. CI runs it as a
  skip-path smoke; real numbers need a consumer C-shim build.
- `tests/samvada.fcyr` — fuzz stub.

## Dependencies

Direct (declared in `cyrius.cyml`):

- stdlib — string, fmt, alloc, io, vec, str, syscalls, assert,
  tagged, fnptr.
  - `tagged` + `fnptr` added during scaffold for the v0.2.0
    Result-type + fn-table plumbing.
  - Unchanged in 0.5.0 — the 6.6.1 bump re-resolved the vendored
    module bodies in `lib/`, not the declared leaf set. samvada
    uses no `Result` / `Option` / `Either`, so 6.6.0's value-form
    flip for those types is a no-op here.

Published alongside the bundle:

- `dist/samvada.deps` (new in 0.5.0) — the distlib-emitted
  stdlib-leaf sidecar listing the same ten leaves. Tracked, so a
  `[deps.samvada]` consumer fetching the release tag gets it with
  `dist/samvada.cyr`; `cyrius deps` consumes it downstream.

## Consumers

- **mabda** — `3.0.0-rc.1` (tagged 2026-04-30) ships the
  consumer body for `gpu_surface_configure_native_logind`
  (`src/surface_v3.cyr:102`) calling
  `samvada_session_take_device(226, 0)` with minor=1
  fallback and full fd-release on every failure path
  (mabda's audit HIGH-2 fix). Pinned via
  `[deps.samvada] tag = "0.2.0"` — bump to `0.2.2` is on
  mabda's `3.0.0-rc.2` punchlist. Live-bus end-to-end
  validation against a real desktop session is **not** on
  rc.2; mabda's own e2e (`programs/native_present_e2e.cyr`)
  stays on the kiosk path through 3.0.0.
  - As of 0.5.0 mabda pins `tag = "0.4.1"` and resolves the
    bundle from the git tag, so a real downstream build against
    0.5.0 needs the tag pushed first — **not yet verified
    end-to-end**. What is verified: mabda's smoke build is green,
    and the 0.5.0 bundle's exported symbol set is identical to
    the 0.4.1 bundle mabda vendors (26 fns). mabda calls only
    `samvada_session_take_device` /
    `samvada_session_release_device`, both unchanged, so the pin
    bump is expected to be a no-op for it.

## Next

See [`roadmap.md`](roadmap.md) — rewritten 0.5.1 as **the road to
Native DBus in Cyrius**, with the N lane (N0–N7, no consumer or
hardware dependency) quarantined from the CG lane (the
hardware-gated consumer e2e). Immediate next item is **N0**:
decide the signal-visibility contract as ADR-0004, because the
answer may require an additive FFI slot and that propagates
through every later milestone.

Historic note below is retained for context; the M-numbered
milestones are folded into the new lanes. M1 status unchanged —
code-complete, awaiting live-bus e2e through a desktop
session. mabda's rc.2 pulls 0.2.2 but does not schedule the
e2e validation. M2 generalization is unscoped pending a
second AGNOS consumer.

The v1.0 criteria checklist after 0.4.0 — two closed, two
advanced:
- ✅ Public API frozen — every exported symbol documented +
  tested (closed by `docs/architecture/public-api.md`; the
  0.4.0 audit fixed the `-EBUSY` doc gap + double-init test-map
  row).
- ✅ CHANGELOG complete from v0.1.0 onward (verified 0.4.0 —
  every released version parses under the CI docs-gate and the
  release body extractor).
- 🟡 Architectural pivot decided — **scoped** in
  `docs/proposals/0001-v1-dbus-backend-pivot.md` (Path A.1 vs
  A.2, LoC, no-hardware test strategy, risks, spike); decision
  deferred to mabda v4.0.
- 🟡 Benchmarks captured in `docs/benchmarks.md` — CPU
  baselines seeded (3 runs); live-bus rows pending, but the
  harness now exists (`tests/samvada_live.bcyr`, HW-gated).
- ⏳ Six-consumer regression sweep, downstream consumer green,
  security audit pass — unchanged, gated on M1 closeout / a
  second consumer / mabda v4.0 design.
