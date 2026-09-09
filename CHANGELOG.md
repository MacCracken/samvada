# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
