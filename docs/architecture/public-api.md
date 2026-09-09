# Public API surface (v0.x)

This page is the canonical map of every exported symbol in
`dist/samvada.cyr`, what it returns, what it requires, and which
test pins it. It is written so a v1.0 pure-Cyrius marshaller (or
a consumer reviewer) has a complete reference without having to
re-read the source.

The surface is intentionally small — five consumer-facing fns,
one version probe, one C-shim entry, plus an internal FFI
plumbing layer that consumers never see in their own
signatures.

`dist/samvada.cyr` defines 28 fns. Two of them
(`_samvada_have_table`, `_samvada_slot`) carry the leading-
underscore private convention and are implementation detail;
the remaining **26 are exported and all 26 appear below** — 7 in
§[Public, consumer-facing](#public-consumer-facing), 19 in
§[Internal — FFI plumbing](#internal--ffi-plumbing). If that
arithmetic stops holding, this page has rotted:
`grep -c '^fn samvada' dist/samvada.cyr` is the check.

## Stability contract

**Signatures** in §[Public, consumer-facing](#public-consumer-facing)
are frozen across the v0.x line and across the v1.0 pivot: the
names, the arity, and the sign convention (`>= 0` success,
negative `-errno` failure) do not change. The C shim retires at
v1.0 (per ADR-0001) without touching them. A consumer that
branches on `rc < 0` and never on a magnitude compiles and runs
unchanged from `0.2.0` through `1.0.0`.

**Return-code semantics are not equally frozen.** Earlier
revisions of this page promised "consumer code written against
`0.2.0` continues to compile and run against `1.0.0`" and then,
two sections later, documented a 0.2.2 behaviour change that
contradicted it. The honest version is the one above: signatures
and the sign convention are frozen; specific rc *values* have
been corrected twice inside 0.x, in both cases moving the
implementation *toward* what this page already specified.

- `0.2.2` — a second `samvada_init` without an intervening
  `samvada_release` began returning `-16` (`-EBUSY`) instead of
  silently overwriting the live bus and leaking it (HIGH-1). The
  old behaviour was never documented as legal; the change made
  an undefined path defined.
- `0.5.1` — `samvada_session_release_device` began returning `0`
  on success. It had returned `1` since 0.2.0, because
  `sd_bus_call_method`'s positive success value was passed
  straight through, contradicting the `0 | -err` contract
  documented in four places across the project, this page
  included (MED-4). A
  consumer that worked around it by accepting `rc == 1` still
  works — `0` and `1` are both non-negative — and can now drop
  the workaround.

`0.5.1`'s **breaking** change is at the C/link surface, not the
Cyrius API: `deps/samvada_main.c` no longer defines `main()`
unconditionally (see [`samvada_main`](#samvada_main)). No Cyrius
signature moved, and no Cyrius call site needs editing.

Everything in §[Internal — FFI plumbing](#internal--ffi-plumbing)
is `@internal` and may change between minor versions. Consumers
must not call these directly; the C shim and the public fns are
the only legitimate callers.

## Threading contract

**samvada is single-threaded and has no locking whatsoever.**

Every public fn reads and writes the module-scope state in
§[Module-scope state](#module-scope-state) — `_samvada_table`,
`_samvada_bus`, `_samvada_sess`, `_samvada_outs` — with plain
`load64` / `store64` and no synchronisation of any kind (see
`src/samvada.cyr`; there is not a single atomic or lock in the
file). `_samvada_outs` in particular is a *shared 16-byte
scratch*: `samvada_init` transiently uses its first word as the
`bus_out` cell and `samvada_session_take_device` reuses both
words for `(fd_out, inactive_out)` on every call, so two
concurrent calls do not merely race on a flag — they hand each
other's out-parameters back as return values.

**Consumers must serialize all samvada calls onto one thread**,
or guard them with their own mutex. The underlying `sd_bus`
handle is likewise driven from a single thread; samvada owns no
event loop and does no dispatch of its own.

This contract previously existed only inside
`docs/audit/2026-05-01-hardening-review.md` §"Multi-process /
multi-thread", where no consumer would ever find it. Thread
safety is a v1.0+ design conversation, not a v0.x property.

## Public, consumer-facing

| Fn | In | Out | Error path |
|---|---|---|---|
| [`samvada_version`](#samvada_version) | (none) | packed u32 | — |
| [`samvada_init`](#samvada_init) | `table` | `0 \| -err` | -22 / -16 / -12 / -38 / -107 / sd-bus negatives |
| [`samvada_session_take_device`](#samvada_session_take_device) | `major`, `minor` | `fd \| -err` | -22 / -38 / -107 / -9 / sd-bus negatives |
| [`samvada_session_release_device`](#samvada_session_release_device) | `major`, `minor` | `0 \| -err` | -22 / -38 / -107 / sd-bus negatives |
| [`samvada_pump_signals`](#samvada_pump_signals) | (none) | `events \| -err` | -22 / -38 / -107 / sd-bus negatives |
| [`samvada_release`](#samvada_release) | (none) | `0` | (none — idempotent) |
| [`samvada_main`](#samvada_main) | `table` | `0 \| -err` | (delegates to `samvada_init`) |

All seven live in `src/samvada.cyr`.

### `samvada_version`

```cyr
fn samvada_version() -> u32_packed
```

Returns the version triple packed `(major << 16) | (minor << 8) | patch`.
No state, no allocations, always succeeds. Bumps lock-step with
`VERSION`.

- **Pre**: none.
- **Post**: caller can branch on a numeric comparison —
  `if (samvada_version() < ((0<<16)|(5<<8)|1)) { ... }` —
  to detect feature availability. That particular gate is the
  one worth writing: below `0.5.1` samvada never issued
  `TakeControl`, so `samvada_session_take_device` could not
  succeed in any environment (see [`samvada_init`](#samvada_init)).
- **Test**: `test_samvada_version` (`tests/samvada.tcyr`) —
  pins both the packed value and each component lane. CI's
  smoke-run step additionally decodes the triple printed by
  `src/main.cyr` and fails the build unless it equals the
  `VERSION` file, so the literal in `samvada_version()` cannot
  drift from the release number (added 0.5.1).

### `samvada_init`

```cyr
fn samvada_init(table) -> 0 | -err
```

Stash the FFI fn-table, open the system bus, look up the
caller's session object path via `GetSessionByPID`, then **take
session control** via `TakeControl(b force=0)`. Calling
`samvada_init` again **without** an intervening
`samvada_release` returns `-16` (`-EBUSY`) rather than
overwriting (and leaking) the live bus + scratch — the 0.2.2
HIGH-1 fix. The legitimate re-init-after-release path reuses the
already-allocated scratch buffers.

The `TakeControl` step landed in **0.5.1** and is not optional
window dressing. logind rejects `TakeDevice` with
`org.freedesktop.login1.NotInControl` unless the *same bus
connection* already holds session control (systemd's
`logind-session-dbus.c`, `method_take_device`). samvada
`0.2.0`–`0.5.0` never sent it, so
[`samvada_session_take_device`](#samvada_session_take_device)
could not succeed in **any** environment. Consequences for this
page: logind's control-side errors now surface out of
`samvada_init` rather than out of the first device call, and a
table whose `take_control` slot is null — i.e. a backend built
against a pre-0.5.1 `samvada_ffi.cyr` — is rejected with `-38`
rather than allowed to proceed into a guaranteed failure.

- **Pre**: `table` points at an **88-byte** fn-table — eleven
  slots, `samvada_ffi_size()`; it was 72 bytes / nine slots
  through 0.5.0 — populated by the C shim's
  `samvada_shim_init()` or by a Cyrius caller via
  `samvada_ffi_set_slot`. The `kind` word at `+64` must be
  `1` (`LIBSYSTEMD`) or `2` (`PURE_CYRIUS`) — since 0.5.1
  `samvada_init` **whitelists** kinds instead of accepting any
  non-zero value, so an uninitialised table whose `+64` word
  happens to be garbage is rejected rather than dispatched as
  function pointers (MED-6).
- **Pre — lifetime**: samvada **borrows** the table. It stores
  the pointer and re-reads `load64(table + off)` on *every*
  subsequent dispatch (`_samvada_slot` in `src/samvada.cyr`); it
  never copies the slots. **The table must remain valid, mapped
  and unmodified from `samvada_init` until `samvada_release`.**
  A stack-local table dangles the moment its frame pops, and
  mutating a slot mid-session changes where the next call
  dispatches. `deps/samvada_main.c` satisfies this with a
  file-scope `static int64_t samvada_fn_table[FFI_SIZE / 8]`;
  a Cyrius caller satisfies it with `samvada_ffi_alloc()`, whose
  bump-allocated memory is never freed. This contract was
  undocumented before 0.5.1 and is the reason the shim's table
  moved off the C stack.
- **Post on success**: `_samvada_table` / `_samvada_bus` /
  `_samvada_sess` are set, the connection holds session control,
  and subsequent `samvada_session_take_device` calls can
  dispatch.
- **Post on err**: returns negative, in one of three shapes.
  (a) *Rejected before any state was written* — `-22` on a null
  table or an unrecognised kind, `-16` on double-init: module
  state is untouched. (b) *Failed late, after the bus opened* —
  a `get_session_path` dispatch error and every `take_control`
  failure call `samvada_release()` themselves, so the bus is
  closed, the public API is disarmed, and the next
  `samvada_init` starts clean instead of returning `-16` (MED-8;
  before 0.5.1 these paths leaked the `sd_bus` *and* left the
  API armed on a half-initialised samvada). (c) *Failed in
  between* — a scratch alloc failure (`-12`), a null
  `open_system_bus` or `get_session_path` slot (`-38`), an
  `open_system_bus` error, or `-107`: these return without
  self-cleaning, so `_samvada_table` stays set and the next
  `samvada_init` returns `-16`. Note that the `-38` from a null
  `get_session_path` slot is reached with the bus already open.

  **The rule a consumer should encode: always call
  `samvada_release()` after a failed `samvada_init`.** It is
  idempotent and correct in all three shapes, it is what closes
  the bus in case (c), and it is what clears the `-16` guard.

| rc | Meaning | When |
|---|---|---|
| `0` | success | bus opened, session resolved, session control taken |
| `-22` (`-EINVAL`) | bad table | `table == 0`, OR the kind word at `+64` is not `1` / `2` (includes `0`, the NULL backend, and any unknown value) |
| `-16` (`-EBUSY`) | already initialized | called again without an intervening `samvada_release` (`_samvada_table != 0`); guards the double-init bus+scratch leak fixed in 0.2.2 (HIGH-1) |
| `-12` (`-ENOMEM`) | alloc failure | scratch buffers couldn't be allocated |
| `-38` (`-ENOSYS`) | slot unwired | the `open_system_bus`, `get_session_path` **or `take_control`** slot is null. The `take_control` case (new in 0.5.1) means the backend table predates 0.5.1 — rebuild the shim against the current `deps/samvada_main.c` |
| `-107` (`-ENOTCONN`) | bus open returned NULL | C wrapper succeeded but didn't write a bus handle |
| any other `< 0` | sd-bus errno pass-through | from `GetSessionByPID`: `-2` (no logind), `-13` (perm denied), `-113` (no session for pid). From `TakeControl`: `-13` (`AccessDenied` — the caller's euid is not the session's user, or logind refuses control of this session at all, which is the seatless case) |

**`-16` from `samvada_init` is ambiguous.** samvada returns it
for its own double-init guard, *and* logind answers a
`TakeControl(false)` against a session another controller
already holds with `System.Error.EBUSY`, which sd-bus maps to
the same `-16`. samvada passes `force = 0` and does not steal
(`force = true` is root-only and deliberately not exposed —
adding the parameter would be an ABI change). A consumer that
needs to tell "I called init twice" from "another compositor
owns this session" must track its own init state; the wire gives
it nothing. See `dbus-marshalling.md` §Session control for the
live verification.

- **Test**: `test_init_rejects_null_table`,
  `test_init_rejects_null_kind`,
  `test_init_rejects_unknown_kind` — the `-22` rejects;
  `test_init_rejects_double_init` — the `-16` guard and its
  release-clears-it half; `test_init_takes_session_control` —
  the CRIT-1 regression pin, asserting `TakeControl` is
  dispatched exactly once and *before* any device call;
  `test_init_rejects_table_without_take_control` — the new
  `-38`; `test_init_self_cleans_on_late_failure` — shape (b),
  including that a re-init after a self-clean is not `-16`. All
  run against the pure-Cyrius mock backend, no bus required.

### `samvada_session_take_device`

```cyr
fn samvada_session_take_device(major, minor) -> fd | -err
```

Ask logind for DRM-master delegation on a device node. Returns
a duplicated unix_fd (the consumer owns it; close it on
teardown). The fd's lifetime is independent of the dbus message
that delivered it (see `dbus-marshalling.md` §SCM_RIGHTS).

- **Pre**: `samvada_init` returned `0` — which since 0.5.1
  guarantees this connection holds session control, without
  which logind answers `NotInControl` unconditionally.
  `major` / `minor` are the caller's intended device node —
  typically `(226, N)` for `/dev/dri/cardN`. Both are
  **range-checked at the C boundary**: `devnum_ok` in
  `deps/samvada_main.c` rejects anything outside
  `[0, UINT32_MAX]` with `-EINVAL` (MED-2, 0.5.1). Before that
  they were cast `int64 -> uint32` with no check anywhere, so a
  negative or oversized value was silently truncated into a
  different device.
- **Post**: caller owns the *descriptor*. To return the device
  to logind, call `samvada_session_release_device(major, minor)`.
  To drop it unilaterally, `sys_close(fd)`. (Mabda's
  `gpu_surface_configure_native_logind` does both — release +
  close — on every error path.)
- **Post — the delegation dies with `samvada_release`.** Earlier
  revisions of this page said flatly that "the fd's lifetime is
  the caller's". That is true of the *descriptor* and misleading
  about the *rights*. `samvada_release()` issues `ReleaseControl`,
  and per `org.freedesktop.login1(5)` that "also releases all
  devices for which the controller requested ownership via
  `TakeDevice()`" — so every fd taken through this session is
  revoked server-side the moment samvada releases, and closing
  the bus does the same implicitly. The descriptor stays open and
  still needs `sys_close`, but it is no longer a DRM-master fd.
  Consumers must therefore order teardown as *stop drawing →
  `samvada_release()` → `sys_close(fd)`*, never the reverse, and
  must not treat a surviving fd as a surviving capability.
- **Post — the fd is close-on-exec.** The duplicate is made with
  `fcntl(fd, F_DUPFD_CLOEXEC, 0)`, not `dup()`. `dup()` clears
  `FD_CLOEXEC` on the new descriptor, so before 0.5.1 the
  DRM-master fd survived any `execve` the consumer performed and
  leaked master rights into an unrelated child (MED-3). A
  consumer that *intends* to hand the fd to a child must now
  clear `FD_CLOEXEC` itself.

| rc | Meaning | When |
|---|---|---|
| `>= 0` | fd | `TakeDevice` and the `F_DUPFD_CLOEXEC` both succeeded |
| `-22` (`-EINVAL`) | not initialized, **or** bad devnum, **or** `NotInControl` | four unrelated causes squash to this one value — see the [error catalog](#error-code-catalog) |
| `-38` (`-ENOSYS`) | slot unwired | `take_device` slot is null |
| `-107` (`-ENOTCONN`) | bus not open | `samvada_init` early-failed; `_samvada_bus == 0` |
| `-9` (`-EBADF`) | reply carried `fd < 0` | the `TakeDevice` reply read back a negative descriptor — a peer-bus contract violation. **Not** a duplication failure |
| any other `< 0` | sd-bus errno pass-through, or a failed duplication | typically `-13` (no DRM master / access denied), `-2` (device not found); or `-errno` from `fcntl(F_DUPFD_CLOEXEC)`, which is `-24` (`-EMFILE`, per-process descriptor limit) or `-23` (`-ENFILE`, system-wide file-table exhaustion) |

logind's reply is `(h fd, b inactive)` — the boolean is
**inactive**, not active. It is written to `_samvada_outs + 8`
and, as of 0.5.1, still not exposed to the caller: there is no
public fn that returns it. Pause/resume tracking therefore
remains entirely the consumer's problem, and *not* via
`samvada_pump_signals` — see that fn's entry for why signal
delivery is not wired in v0.x.

- **Test**: `test_take_device_behaviour` — dispatch, argument
  forwarding, out-parameter read-back, and negative-rc
  pass-through, against the pure-Cyrius mock backend;
  `test_fd_sign_extension` — the `int32 -> int64` sign-extension
  of the fd word across `0`, `INT32_MAX`, `INT32_MIN`,
  `0xFFFFFFFF` and `0xFFFFFFF7`; `test_dispatch_wiring` — that
  it dispatches through slot 16 and nothing else; slot offset 16
  itself pinned by `test_ffi_slot_offsets`. The C wrapper's own
  body (`sb_take_device`) has **no** unit test — the
  `devnum_ok`, `-EBADF` and `F_DUPFD_CLOEXEC` branches are
  compile-checked by CI but not executed by any gate. Live-bus
  exercise remains HW-gated through mabda's logind dispatcher.

### `samvada_session_release_device`

```cyr
fn samvada_session_release_device(major, minor) -> 0 | -err
```

Tell logind the consumer is done with `(major, minor)`. Pairs
with `samvada_session_take_device` at teardown. It would also be
the fn to call on a `PauseDevice`, for a consumer that chooses to
drop master rather than ride out the pause — but no consumer can
reach that path in v0.x, because
[`samvada_pump_signals`](#samvada_pump_signals) does not deliver
the signal.

At full teardown it is a courtesy rather than an obligation:
[`samvada_release`](#samvada_release) issues `ReleaseControl`,
which releases every device taken through this session anyway.
Where it genuinely earns its place is releasing *one* device
while keeping the session and its other devices alive.

- **Pre**: `samvada_init` returned `0` and a previous
  `take_device` for `(major, minor)` succeeded. (Logind
  tolerates `ReleaseDevice` for a device that was never taken
  — returns success — so the v0.x impl does not pre-check.)
  `major` / `minor` are range-checked at the C boundary exactly
  as in `take_device`.
- **Post**: master delegation is gone. The fd from
  `take_device` is *not* closed by this call — caller is still
  responsible for `sys_close(fd)`.

| rc | Meaning |
|---|---|
| `0` | success |
| `-22` / `-38` / `-107` / sd-bus negatives | as above (`-22` additionally covers an out-of-range `major`/`minor`) |

**`0` on success is true only from 0.5.1 onward.** From 0.2.0
through 0.5.0 this fn returned **`1`**, not `0`: `sd_bus_call_method`
returns a *positive* value on success and the C wrapper passed
it straight through, so the `0 | -err` contract this page has
asserted since 0.2.0 was simply false. 0.5.1 normalises in both
places — `sb_release_device` in `deps/samvada_main.c` returns
`(r < 0) ? r : 0`, and `samvada_session_release_device` in
`src/samvada.cyr` independently collapses any non-negative rc to
`0` so a `PURE_CYRIUS` backend is covered too (MED-4). A
consumer that worked around the old behaviour with
`rc == 0 || rc == 1` can drop the second clause; a consumer that
checked `rc != 0` and treated success as failure now works.

- **Test**: `test_release_device_normalises_success` — the mock
  backend deliberately returns `1`, mimicking
  `sd_bus_call_method`, and the pin asserts samvada hands back
  `0`; `test_dispatch_wiring` — that it dispatches through slot
  24 and nothing else. Live exercise HW-gated.

### `samvada_pump_signals`

```cyr
fn samvada_pump_signals() -> events_drained | -err
```

Drain pending bus messages. Calls `sd_bus_process` in a loop
until it returns `0` (queue empty), returns `< 0` (error), or
**256 messages have been processed**. Returns the count of
messages processed. Consumers typically call this once per frame
/ event-loop tick.

The cap (`SAMVADA_PUMP_MAX_EVENTS` in `deps/samvada_main.c`) is
new in 0.5.1. The pre-0.5.1 loop ran until the queue emptied, so
any peer able to emit signals on this connection could hold a
single call captive — measured at 0.77 s under an unprivileged
local flood (MED-5). The public Cyrius signature takes no
arguments and is frozen, so the cap lives in the C wrapper
rather than becoming a parameter. A residual queue is simply
drained on the next tick; a consumer that sees `256` returned
should expect more work pending and may pump again immediately.

- **Pre**: `samvada_init` returned `0`.
- **Post**: up to 256 pending messages have been dispatched by
  `sd_bus_process`. A return of `256` means the queue may not be
  empty; anything less means it was.
- **Post — what does NOT happen.** No `PauseDevice` /
  `ResumeDevice` callback runs, because **signal delivery is not
  wired in v0.x.** Earlier revisions of this page claimed
  "any installed match callbacks (e.g. `PauseDevice` /
  `ResumeDevice` from `subscribe_pause_resume`) have run". That
  post-condition was unreachable on the day it was written, and
  is corrected here.

  The mechanics: the C shim populates slot 48
  (`sb_subscribe_pause_resume`) and slot 56 (`sb_unsubscribe`),
  but **no Cyrius code anywhere dispatches either one** —
  `grep samvada_slot_subscribe_pause_resume src/` finds only the
  offset constant's own definition in `src/samvada_ffi.cyr`,
  never a call — and there is no public fn through which a
  consumer could install a match rule. With no match registered,
  `sd_bus_process` drains and discards. `PauseDeviceComplete` is
  unwired on both sides of the FFI boundary, so a consumer
  cannot acknowledge a pause either.

  **And there is no consumer-side workaround in v0.x.** logind
  delivers `PauseDevice` / `ResumeDevice` *exclusively* to the
  active session controller, and the controller is the bus
  connection that called `TakeControl` — which is samvada's,
  established during `samvada_init`. A consumer that opens its
  own `sd_bus` and installs its own match is not the controller
  and receives nothing. samvada exposes neither the bus handle
  nor a registration API, so those signals are currently
  unreachable, full stop. What a consumer *can* still do is
  notice that its DRM operations start failing on a revoked fd —
  which is, per `roadmap.md` §N0, the entire reason a consumer
  cares about a pause.

  Deciding how a consumer observes a pause is tracked as
  **roadmap N0** (0.5.2) and will be ratified as ADR-0004; it may
  require an additive slot at `+88`. Wire-level detail —
  the `pause` / `force` / `gone` `type` values, the
  acknowledgement deadline, and why an unacknowledging client
  makes VT switching feel broken — is in `dbus-marshalling.md`
  §PauseDevice / ResumeDevice.

| rc | Meaning |
|---|---|
| `0` | queue was empty |
| `1`..`256` | messages dispatched; `256` means the cap was hit, not that the queue drained |
| `-22` / `-38` / `-107` / sd-bus negatives | as above; a negative from `sd_bus_process` is passed through unchanged |

- **Test**: `test_pump_signals_behaviour` — dispatch, count
  pass-through, and negative-rc pass-through against the mock
  backend; `test_dispatch_wiring` — that it dispatches through
  slot 32 and nothing else. The 256-event cap lives in the C
  wrapper and no gate executes it. Live exercise HW-gated.

### `samvada_release`

```cyr
fn samvada_release() -> 0
```

Drop session control, close the bus, clear module state.
**Idempotent** — safe to call any number of times, including on
a samvada that never initialized (or had `samvada_init` return
an error mid-flight). The scratch buffers are *not* freed
(samvada's allocator is a bump allocator; the buffers live for
process lifetime), but their **contents are zeroed** — see
below.

`ReleaseControl` is dispatched before `close_bus`, and only when
`_samvada_bus != 0`. Its return value is ignored on purpose:
logind drops session control implicitly when the connection goes
away, so a failure here cannot leave the session wedged. If the
`release_control` slot is null (a pre-0.5.1 table), the call is
skipped and the bus is closed anyway.

**This revokes every device.** `ReleaseControl` "also releases
all devices for which the controller requested ownership via
`TakeDevice()`" (`org.freedesktop.login1(5)`), and the
subsequent bus close would do it implicitly regardless. Any fd
handed out by `samvada_session_take_device` loses its master
rights here — it remains an open descriptor the consumer must
still `sys_close`, but it is no longer a capability. Stop
drawing before you call this.

- **Pre**: none.
- **Post**: `_samvada_bus == 0`, `_samvada_table == 0`. Calling
  any other public fn afterwards returns `-22` until the
  consumer re-runs `samvada_init`.
- **Post — scratch is wiped.** Both scratch buffers are zeroed
  (LOW-5, 0.5.1). This matters for `_samvada_outs`, whose first
  word held a raw `sd_bus *` that is **dangling** the instant
  `close_bus` runs; `_samvada_sess` held the session object
  path. Neither should survive a release, and before 0.5.1 both
  did. The buffer *pointers* are deliberately left set, so a
  later `samvada_init` reuses the same allocations rather than
  bump-allocating a second pair.

- **Test**: `test_release_idempotent` — calls `samvada_release`
  twice on a never-init'd samvada and asserts both return `0`;
  `test_release_drops_control_and_closes` — that `ReleaseControl`
  and `close_bus` are each dispatched exactly once against a
  live mock, that the API is disarmed afterwards, and that a
  second release does **not** re-dispatch `close_bus`.

### `samvada_main`

```cyr
fn samvada_main(table) -> 0 | -err
```

C-shim entry point. Identical semantics to `samvada_init(table)`
— it calls it and returns the rc unchanged — kept as a
separately-named fn so the shim's expected entry point is
grep-able and so a future shim variant (e.g. a polkit-only
build) can replace it without touching `samvada_init`.

**It returns.** The pre-0.5.1 comment in `deps/samvada_main.c`
claimed `samvada_main()` "never returns until the process
exits"; that was false, and harmless only because the shim's
`main()` exited immediately after. Control reaches the line
after the call, and the rc must be checked.

#### The C-side entry changed in 0.5.1 (breaking, link surface)

`deps/samvada_main.c` no longer defines `main()` unconditionally.
The library entry point is now

```c
long samvada_shim_init(void);   /* 0 | -err */
```

which zeroes a **file-scope `static int64_t
samvada_fn_table[FFI_SIZE / 8]`**, fills its ten `sb_*` wrappers
in, sets `kind = 1` (`LIBSYSTEMD`) at `+64`, and calls
`samvada_main(table)`. Consumers call it once from their own
`main()`, after `_cyrius_init()` and `alloc_init()`.

Two things forced this:

- **`main()` collided.** Every real consumer already defines
  `main()` — mabda's `deps/wgpu_main.c` does — so linking the
  shim in produced `multiple definition of 'main'`. The
  two-stage build this page and `consumer-link.md` have
  documented since 0.2.0 could therefore never have worked
  (CRIT-2). A standalone probe binary can still get a `main()`
  by compiling with `-DSAMVADA_STANDALONE_MAIN`.
- **The table had to outlive the call.** It used to be a local
  in `main()`. Per the lifetime contract in
  [`samvada_init`](#samvada_init), samvada borrows the pointer
  and re-reads it on every dispatch, so a stack table is only
  accidentally safe while its frame happens to still be live —
  which the old `main()`-never-returns story papered over. At
  file scope with `static` storage duration it is correct by
  construction.

CI gates the new shape directly: one job compiles the shim both
with and without `-DSAMVADA_STANDALONE_MAIN` under
`-Wall -Wextra -Werror`, and a second links `shim_lib.o` against
a **consumer-owned `main()`** that calls `samvada_shim_init()`
— reproducing mabda's shape, which the pre-0.5.1 link test
(it stubbed `main()` out entirely) never did.

Consumers writing their own shim (vs. linking `samvada_main.c`)
are free to call `samvada_init` directly and skip
`samvada_main`. The smoke build (`src/main.cyr`, no shim) does
exactly this.

- **Test**: `test_samvada_main_entry` — that it returns at all,
  that it returns `0` on a good table and surfaces `-22` on a
  null one. Before 0.5.1 it had no test whatsoever, despite its
  return value becoming the standalone binary's exit code. Vet
  still flags it `dead` in the standalone smoke build — expected;
  the C shim is the live caller.

## Internal — FFI plumbing

These 19 fns (all in `src/samvada_ffi.cyr`) are exported — so
the C shim and tests can reach them — but `@internal`, and not
part of the stability contract. Listed here for completeness;
consumer code must not reference them directly.

| Fn | Purpose |
|---|---|
| `samvada_slot_open_system_bus` … `samvada_slot_unsubscribe` | The eight original slot-offset constants (0, 8, …, 56) — frozen by the append-after-kind invariant; see ADR-0002 |
| `samvada_slot_kind` | Returns `64` — the kind word offset, frozen forever |
| `samvada_slot_take_control` | **New in 0.5.1.** Returns `72` — `sb_take_control(bus, sess_cstr) -> 0 \| -err`. Appended *after* `kind`, per ADR-0002 |
| `samvada_slot_release_control` | **New in 0.5.1.** Returns `80` — `sb_release_control(bus, sess_cstr) -> 0 \| -err`. Also appended after `kind` |
| `samvada_ffi_size` | Returns `88` — total table size in bytes (11 slots). Was `72` (9 slots) through 0.5.0 |
| `samvada_backend_kind_null` / `samvada_backend_kind_libsystemd` / `samvada_backend_kind_pure_cyrius` | Kind-word values: `0` / `1` / `2`. Since 0.5.1 only `1` and `2` are accepted by `samvada_init` |
| `samvada_ffi_alloc` | Bump-allocate a zero-filled 88-byte table (Cyrius-side mocks and any Cyrius caller; the C shim uses its own file-scope `static` array instead) |
| `samvada_ffi_get_slot(t, off)` | Null-safe `load64(t + off)`. Returns `0` if `t == 0` |
| `samvada_ffi_set_slot(t, off, fp)` | Null-safe `store64`. Returns `0` if `t == 0` — the null guard matters more here than on the read path, since an unguarded store to a null table writes to address `off` |
| `samvada_ffi_kind(t)` | Convenience for `get_slot(t, samvada_slot_kind())` |

Slots 72 and 80 exist because logind will not honour
`TakeDevice` from a connection that has not called
`TakeControl`. They were **appended after `kind`** rather than
inserted at the tail of the fnptr run, which is exactly what
ADR-0002 requires: `kind` stays at `+64` forever, so a v0 caller
scanning a v(N+1) table never misreads a fnptr as the kind word,
and a pre-0.5.1 shim handed a v(N+1) table still finds its own
eight slots and the kind word where it expects them.

The slot offsets and kind values are pinned by
`test_ffi_slot_offsets` (12 asserts, now including 72 / 80 / 88)
and `test_ffi_backend_kinds`. `test_ffi_layout_invariants`
(0.5.1) pins the *relationship* rather than the literals: every
slot 8-aligned, distinct, strictly inside `ffi_size`, every
appended slot strictly greater than `samvada_slot_kind()`, and
`kind` still `64`.

Two gaps in that armour closed in 0.5.1, both worth knowing
about because this page and ADR-0002 previously **claimed** they
were already closed:

- **The C side was never compared to the Cyrius side.** The test
  pin only ever checked `src/samvada_ffi.cyr`; nothing read
  `deps/samvada_main.c`'s `#define SLOT_*` / `FFI_SIZE`, so the
  two could drift silently and misroute every dispatch. CI now
  extracts both sets, cross-checks them, fails on any mismatch
  or on an empty parse (a rotted regex), and separately asserts
  `kind == 64`.
- **`samvada_ffi_alloc`'s zero-fill assert was vacuous.** The
  bump allocator hands out untouched pages, so deleting the
  zero-fill loop left the test green. `test_ffi_alloc_and_set_get`
  now dirties a table with `0xDEADBEEF` before re-allocating, so
  the assert can actually fail.

## Module-scope state

Set by `samvada_init`, read by the rest of the API, cleared by
`samvada_release`. Documented here so a consumer reviewing the
implementation knows what to expect under `cyrius vet`'s "dead
code" list (these are reachable only through the public fns
above).

| Var | Set by | Cleared by | Notes |
|---|---|---|---|
| `_samvada_table` | `samvada_init` | `samvada_release` | The **borrowed** fn-table pointer; reset to `0` on release. Never a copy of the table — see the lifetime contract under [`samvada_init`](#samvada_init) |
| `_samvada_bus` | `samvada_init` | `samvada_release` | sd_bus handle; unref'd via `close_bus` slot, after `release_control` |
| `_samvada_sess` | `samvada_init` | contents zeroed by `samvada_release`; pointer kept | Bump-allocated 512-byte buffer holding the NUL-terminated session object path. Lives for process lifetime |
| `_samvada_outs` | `samvada_init` | contents zeroed by `samvada_release`; pointer kept | Bump-allocated 16-byte scratch. `samvada_init` transiently uses `+0` as the `bus_out` cell; `samvada_session_take_device` then reuses `+0` / `+8` for `(fd_out, inactive_out)` |

The buffer *pointers* survive `samvada_release` so a
re-`samvada_init` reuses the same allocations. This is
intentional — the bump allocator never frees, and reuse keeps
the per-call paths alloc-free (a v1.0 perf goal). `samvada_init`
re-zeroes both on every entry, and since 0.5.1 `samvada_release`
zeroes them on the way out too (LOW-5): `_samvada_outs + 0`
otherwise retained a raw `sd_bus *` that is dangling the moment
`close_bus` has run.

None of these are synchronised — see
§[Threading contract](#threading-contract). `_samvada_outs`
being a single shared scratch across `init` and every
`take_device` is the specific reason concurrent calls are not
merely racy but actively corrupting.

## Error-code catalog

Quick-reference for the negative codes consumers will see. All
are `-errno` semantics inherited from sd-bus — magnitudes match
`/usr/include/asm-generic/errno-base.h` and `errno.h`.

| Magnitude | Symbol | Source | Meaning in samvada |
|---|---|---|---|
| 2 | `ENOENT` | sd-bus | logind not running, or session has no caller pid |
| 9 | `EBADF` | C shim | the `TakeDevice` reply read back successfully but carried `fd < 0` — a peer-bus contract violation. `sb_take_device` returns this explicitly rather than reading a stale `errno` from a syscall it never made. **It is not a duplication failure** — see 23 / 24 below |
| 12 | `ENOMEM` | samvada | scratch buffer alloc failed |
| 13 | `EACCES` | sd-bus | logind denied. Two distinct callers now: `TakeControl` during `samvada_init` (logind refuses control of this session — the seatless case), and `TakeDevice` (no DRM master available) |
| 16 | `EBUSY` | samvada **or** logind — ambiguous | `samvada_init` called again before `samvada_release`; **or** `TakeControl(false)` against a session another controller already holds (`System.Error.EBUSY`) |
| 22 | `EINVAL` | **four sources — ambiguous** | see below |
| 23 | `ENFILE` | C shim | `fcntl(fd, F_DUPFD_CLOEXEC, 0)` failed: system-wide file-table exhaustion |
| 24 | `EMFILE` | C shim | `fcntl(fd, F_DUPFD_CLOEXEC, 0)` failed: this process hit `RLIMIT_NOFILE` |
| 38 | `ENOSYS` | samvada | requested slot is null (backend doesn't implement that fn). Since 0.5.1 this also means a **pre-0.5.1 backend table** when it comes from `samvada_init` and the null slot is `take_control` |
| 105 | `ENOBUFS` | C shim | session path didn't fit in the 512-byte scratch |
| 107 | `ENOTCONN` | samvada | bus open returned `NULL` without an explicit error |
| 113 | `EHOSTUNREACH` | sd-bus | no logind session for caller pid (typical for ssh shells) |
| any other | (sd-bus) | sd-bus | pass-through; consumer logs and aborts |

### `-22` is ambiguous

There are **four** independent ways to get `-22` back from a
device call, and samvada does not distinguish them:

1. **samvada not initialized** (Cyrius side, `src/samvada.cyr`)
   — `_samvada_table == 0`, i.e. `samvada_init` was never called,
   returned an error, or `samvada_release` has since run. Also
   covers `samvada_init`'s own rejects: a null table, or a `kind`
   word that is not `1` / `2`.
2. **Bad devnum** (C boundary, `devnum_ok` in
   `deps/samvada_main.c`) — `major` or `minor` outside
   `[0, UINT32_MAX]`, rejected before the `int64 -> uint32` cast
   that would otherwise truncate it silently. New in 0.5.1
   (MED-2); before it, `SECURITY.md`'s claim that these were
   "validated at C boundary" was simply untrue.
3. **logind's own devnum rejection** — a `major`/`minor` that
   fails logind's validity check comes back as
   `org.freedesktop.DBus.Error.InvalidArgs`, "Device major/minor
   is not valid.", which sd-bus maps to `-EINVAL`.
4. **`org.freedesktop.login1.NotInControl`** — libsystemd's error
   map translates it to `-EINVAL` as well (verified live; see
   `dbus-marshalling.md` §Session control). Since 0.5.1
   `samvada_init` takes control, so this should not appear on a
   healthy session — but control can be lost afterwards (a root
   process forcing control away, or the session ending), and
   samvada does not re-check.

Case 4 is the nastiest, because it is indistinguishable from
case 1 — "you never took control" and "you passed me a bad
table" are the same integer. A consumer must check its own state
and its own arguments first; the wire gives it nothing. The
pure-Cyrius marshaller reads `ERROR_NAME` (header field 5)
directly and should surface the *name* rather than a squashed
errno — that is a v1.0 surface question, not a v0.x property.

Consumers should treat any negative value as fatal — there is
no transient / retry path in the v0.x API — and should branch on
the sign, not the magnitude. libsystemd owns the mapping from
dbus error *names* to errno numbers, so a logind error whose
name is not in libsystemd's table arrives with a magnitude this
page cannot enumerate.

## Test coverage map

Each public symbol's pins in `tests/samvada.tcyr` — 23 groups,
**114 asserts**, all green with no bus, no logind and no device:

| Fn | Test group | Asserts |
|---|---|---|
| `samvada_version` | `v0.5.1 packed triple` | 4 (packed + 3 lanes) |
| `samvada_init` (null) | `init rejects null table` | 1 |
| `samvada_init` (NULL kind) | `init rejects NULL-kind table` | 2 (alloc + reject) |
| `samvada_init` (unknown kind) | `init rejects an unknown backend kind` | 2 (`kind=99` rejected, `PURE_CYRIUS` accepted) |
| `samvada_init` (double-init) | `init rejects double-init without release` | 6 (re-init `-EBUSY` + release clears the guard) |
| `samvada_init` (TakeControl) | `init takes session control (CRIT-1 regression)` | 6 (dispatched once, before any device call) |
| `samvada_init` (old table) | `init rejects a pre-0.5.1 table (no take_control slot)` | 3 (`-ENOSYS` + API left disarmed) |
| `samvada_init` (self-clean) | `init self-cleans on late failure (no armed API, no leak)` | 11 (session-path *and* TakeControl failure paths) |
| `samvada_session_take_device` | `take_device: dispatch, args, fd, errors` | 6 |
| `samvada_session_take_device` | `take_device fd sign-extension across int32` | 6 |
| `samvada_session_release_device` | `release_device normalises a positive rc to 0` | 4 |
| `samvada_pump_signals` | `pump_signals: dispatch + count pass-through` | 4 |
| `samvada_release` | `release is idempotent` | 2 (two consecutive calls) |
| `samvada_release` | `release drops control, closes bus, wipes scratch` | 7 |
| `samvada_main` | `samvada_main: returns, and surfaces init's rc` | 3 |
| (all public) | `each public fn dispatches through its OWN slot` | 7 |
| FFI plumbing | `ffi: slot offsets pin C shim contract` | 12 (10 slots + kind + size) |
| FFI plumbing | `ffi: append-after-kind layout invariants` | 10 |
| FFI plumbing | `ffi: backend kinds` | 3 |
| FFI plumbing | `ffi: alloc/get/set round-trip` | 7 |
| FFI plumbing | `ffi: get_slot is null-safe` | 3 |
| FFI plumbing | `ffi: set_slot is null-safe (the WRITE path)` | 3 |
| — | `smoke` | 2 |

### The "needs hardware" claim was false

This page asserted from 0.2.0 through 0.5.0 that
`samvada_session_take_device`,
`samvada_session_release_device` and `samvada_pump_signals` had
**no `tcyr` coverage** because "they need a running system dbus +
logind + a real device node". That was wrong. The fn-table is
just function pointers, so a **pure-Cyrius mock backend**
(`mock_table_new` in `tests/samvada.tcyr`, 0.5.1) exercises the
entire dispatch path — argument marshalling, out-parameter
read-back, rc normalisation, error mapping, call counting —
against no hardware at all. Every 0.5.1 repair is
mutation-proven: reverting the fix makes the suite fail.

What the mock backend does **not** cover, and what genuinely
remains gated:

- **The C wrapper bodies.** `sb_take_device`, `sb_get_session_path`,
  `sb_take_control` and friends in `deps/samvada_main.c` are
  compile-checked by CI (twice, with and without
  `-DSAMVADA_STANDALONE_MAIN`, under
  `-Wall -Wextra -Werror -Wshadow -Wconversion -Wsign-conversion
  -Wcast-qual -Wformat=2`) and their slot `#define`s are
  cross-checked against `src/samvada_ffi.cyr`, but **no gate
  executes them**. `devnum_ok`, the `-EBADF` branch, the
  `F_DUPFD_CLOEXEC` fallback and the 256-event pump cap have no
  running test.
- **Live-bus end-to-end.** Still HW-gated and unverified.
  `tests/samvada_live.bcyr` (the live-bus bench scaffold, 0.4.0)
  drives handshake / take_device / pump through a real table but
  SKIPs without a `LIBSYSTEMD`-kind backend, which the
  standalone build can never provide. The behavioural gap closes
  when mabda's `gpu_surface_configure_native_logind` runs
  end-to-end on a real seated desktop session — **CG-1** in
  `roadmap.md`, which blocks only the 1.0.0 tag. (Earlier
  revisions of this page called it "the M1 closeout gate"; that
  lane was renumbered.) The 0.5.1 `TakeControl` fix was
  reproduced by hand against a running `systemd-logind` (without
  it, "You are not in control of this session"; with it, the
  call advances past the control check), but that is a manual
  observation, not a gate.
- **Signal delivery.** Untested because
  [unimplemented](#samvada_pump_signals) — slots 48 and 56 are
  populated by the shim and dispatched by nothing.

## Cross-references

- [ADR-0001 — C shim, then pivot](../adr/0001-c-shim-then-pivot.md) — why the shim exists and when it retires
- [ADR-0002 — append-after-kind FFI invariant](../adr/0002-append-after-kind-ffi-invariant.md) — why slots 72 / 80 landed where they did
- [`dbus-marshalling.md`](dbus-marshalling.md) — wire format; §Session control for `TakeControl` / `ReleaseControl`, §PauseDevice / ResumeDevice for the signal gap
- [`../guides/consumer-link.md`](../guides/consumer-link.md) — two-stage build (consumer-owned `main()` + `samvada_shim_init()`)
- [`../development/roadmap.md`](../development/roadmap.md) — N0 (signal-visibility contract), CG-1 (live-bus e2e)
- [`../audit/2026-09-09-audit.md`](../audit/2026-09-09-audit.md) — the CRIT / MED / LOW finding IDs cited throughout this page
- [`../sources.md`](../sources.md) — protocol citations
- [`../../SECURITY.md`](../../SECURITY.md) — threat model
