# Public API surface (v0.x)

This page is the canonical map of every exported symbol in
`dist/samvada.cyr`, what it returns, what it requires, and which
test pins it. It is written so a v1.0 pure-Cyrius marshaller (or
a consumer reviewer) has a complete reference without having to
re-read the source.

The surface is intentionally small — six consumer-facing fns,
one C-shim entry, one version probe, plus an internal FFI
plumbing layer that consumers never see in their own
signatures.

## Stability contract

Everything in §[Public, consumer-facing](#public-consumer-facing)
is **frozen across the v0.x line and across the v1.0 pivot.**
The C shim retires at v1.0 (per ADR-0001) but the Cyrius-side
signatures and return-code shapes do not change. Consumer code
written against samvada `0.2.0` continues to compile and run
against `1.0.0`.

Everything in §[Internal — FFI plumbing](#internal--ffi-plumbing)
is `@internal` and may change between minor versions. Consumers
must not call these directly; the C shim and the public fns are
the only legitimate callers.

## Public, consumer-facing

| Fn | In | Out | Error path |
|---|---|---|---|
| [`samvada_version`](#samvada_version) | (none) | packed u32 | — |
| [`samvada_init`](#samvada_init) | `table` | `0 \| -err` | -22 / -12 / -38 / -107 / sd-bus negatives |
| [`samvada_session_take_device`](#samvada_session_take_device) | `major`, `minor` | `fd \| -err` | -22 / -38 / -107 / sd-bus negatives |
| [`samvada_session_release_device`](#samvada_session_release_device) | `major`, `minor` | `0 \| -err` | -22 / -38 / -107 / sd-bus negatives |
| [`samvada_pump_signals`](#samvada_pump_signals) | (none) | `events \| -err` | -22 / -38 / -107 / sd-bus negatives |
| [`samvada_release`](#samvada_release) | (none) | `0` | (none — idempotent) |
| [`samvada_main`](#samvada_main) | `table` | `0 \| -err` | (delegates to `samvada_init`) |

### `samvada_version`

```cyr
fn samvada_version() -> u32_packed
```

Returns the version triple packed `(major << 16) | (minor << 8) | patch`.
No state, no allocations, always succeeds. Bumps lock-step with
`VERSION`.

- **Pre**: none.
- **Post**: caller can branch on a numeric comparison —
  `if (samvada_version() < ((0<<16)|(2<<8)|2)) { ... }` —
  to detect feature availability.
- **Test**: `test_samvada_version` (`tests/samvada.tcyr`) —
  pins both the packed value and each component lane.

### `samvada_init`

```cyr
fn samvada_init(table) -> 0 | -err
```

Stash the FFI fn-table, open the system bus, look up the
caller's session object path via `GetSessionByPID`. Idempotent
*on error* — if init fails partway, internal state is
consistent and `samvada_release()` is safe.

- **Pre**: `table` is a heap or stack pointer to a 72-byte
  fn-table populated by the C shim (or a Cyrius mock with
  `samvada_ffi_set_slot`). The table's `kind` slot at +64 must
  be non-zero.
- **Post on success**: `_samvada_table` / `_samvada_bus` /
  `_samvada_sess` are set; subsequent
  `samvada_session_take_device` calls can dispatch.
- **Post on err**: returns negative; module state is either
  fully zero (early reject) or partially set with `_samvada_bus`
  unref-safe via `samvada_release`.

| rc | Meaning | When |
|---|---|---|
| `0` | success | bus opened + session resolved |
| `-22` (`-EINVAL`) | bad table | `table == 0` OR kind word at +64 is `0` (NULL backend) |
| `-12` (`-ENOMEM`) | alloc failure | scratch buffers couldn't be allocated |
| `-38` (`-ENOSYS`) | slot unwired | `open_system_bus` or `get_session_path` slot is null in the table |
| `-107` (`-ENOTCONN`) | bus open returned NULL | C wrapper succeeded but didn't write a bus handle |
| any other `< 0` | sd-bus errno pass-through | typically `-2` (no logind), `-13` (perm denied), `-113` (no session for pid) |

- **Test**: `test_init_rejects_null_table`,
  `test_init_rejects_null_kind` — pin the two `-22` early
  rejects without touching the heap path.

### `samvada_session_take_device`

```cyr
fn samvada_session_take_device(major, minor) -> fd | -err
```

Ask logind for DRM-master delegation on a device node. Returns
a `dup`'d unix_fd (the consumer owns it; close it on teardown).
The fd's lifetime is independent of the dbus message that
delivered it (see `dbus-marshalling.md` §SCM_RIGHTS).

- **Pre**: `samvada_init` returned `0`. `major` / `minor` are
  the caller's intended device node — typically `(226, N)` for
  `/dev/dri/cardN`.
- **Post**: caller owns the fd. To return it to logind, call
  `samvada_session_release_device(major, minor)`. To drop it
  unilaterally, `sys_close(fd)`. (Mabda's
  `gpu_surface_configure_native_logind` does both — release +
  close — on every error path.)

| rc | Meaning | When |
|---|---|---|
| `>= 0` | fd | TakeDevice + `dup` both succeeded |
| `-22` (`-EINVAL`) | not initialized | `samvada_init` was never called or returned an error |
| `-38` (`-ENOSYS`) | slot unwired | `take_device` slot is null |
| `-107` (`-ENOTCONN`) | bus not open | `samvada_init` early-failed; `_samvada_bus == 0` |
| any other `< 0` | sd-bus errno pass-through | typically `-13` (no DRM master), `-2` (device not found), `-EBADF` from a failed `dup` |

The **active** flag (logind returns `(h fd, b inactive)`) is
written into samvada's scratch but not exposed to the caller in
v0.x. Pause/resume tracking is the consumer's job (currently via
`samvada_pump_signals`); v0.3+ may surface a structured event.

- **Test**: structural — slot offset 16 pinned by
  `test_ffi_slot_offsets`. Live-bus exercise is HW-gated through
  mabda's logind dispatcher.

### `samvada_session_release_device`

```cyr
fn samvada_session_release_device(major, minor) -> 0 | -err
```

Tell logind the consumer is done with `(major, minor)`. Pairs
with `samvada_session_take_device` at teardown, and also runs
on `PauseDevice` if the consumer chooses to drop master rather
than ride out the pause.

- **Pre**: `samvada_init` returned `0` and a previous
  `take_device` for `(major, minor)` succeeded. (Logind
  tolerates `ReleaseDevice` for a device that was never taken
  — returns success — so the v0.x impl does not pre-check.)
- **Post**: master delegation is gone. The fd from
  `take_device` is *not* closed by this call — caller is still
  responsible for `sys_close(fd)`.

| rc | Meaning |
|---|---|
| `0` | success |
| `-22` / `-38` / `-107` / sd-bus negatives | as above |

- **Test**: structural pin only (slot 24); live exercise
  HW-gated.

### `samvada_pump_signals`

```cyr
fn samvada_pump_signals() -> events_drained | -err
```

Drain pending bus messages. Calls `sd_bus_process` in a loop
until it returns `0` (queue empty) or `< 0` (error). Returns
the count of messages processed. Consumers typically call this
once per frame / event-loop tick.

- **Pre**: `samvada_init` returned `0`.
- **Post**: bus queue is empty; any installed match callbacks
  (e.g. `PauseDevice` / `ResumeDevice` from
  `subscribe_pause_resume`) have run.

| rc | Meaning |
|---|---|
| `>= 0` | events drained |
| `-22` / `-38` / `-107` / sd-bus negatives | as above |

- **Test**: structural pin only (slot 32); live exercise
  HW-gated.

### `samvada_release`

```cyr
fn samvada_release() -> 0
```

Close the bus, clear module state. **Idempotent** — safe to
call any number of times, including on a samvada that never
initialized (or had `samvada_init` return an error mid-flight).
The scratch buffers are *not* freed (samvada's allocator is a
bump allocator; the buffers live for process lifetime).

- **Pre**: none.
- **Post**: `_samvada_bus == 0`, `_samvada_table == 0`. Calling
  any other public fn afterwards returns `-22` until the
  consumer re-runs `samvada_init`.

- **Test**: `test_release_idempotent` — calls `samvada_release`
  twice on a never-init'd samvada and asserts both return `0`.

### `samvada_main`

```cyr
fn samvada_main(table) -> 0 | -err
```

C-shim entry point. The `deps/samvada_main.c` `main()` builds
the fn-table on its own stack and calls into this. Identical
semantics to `samvada_init(table)` — kept as a separately-named
fn so the shim's expected entry point is grep-able and so a
future shim variant (e.g. a polkit-only build) can replace it
without touching `samvada_init`.

Consumers writing their own shim (vs. linking `samvada_main.c`)
are free to call `samvada_init` directly and skip
`samvada_main`. The smoke build (`src/main.cyr`, no shim) does
exactly this.

- **Test**: vet flags it `dead` in the standalone smoke build —
  expected; the C shim is the live caller.

## Internal — FFI plumbing

These fns are exported (so the C shim and tests can reach them)
but `@internal` — not part of the stability contract. Listed
here for completeness; consumer code must not reference them
directly.

| Fn | Purpose |
|---|---|
| `samvada_slot_open_system_bus` … `samvada_slot_unsubscribe` | Slot-offset constants (0, 8, …, 56) — frozen by the append-after-kind invariant; see ADR-0002 |
| `samvada_slot_kind` | Returns `64` — the kind word offset, frozen forever |
| `samvada_ffi_size` | Returns `72` — total table size in bytes for the v1 layout |
| `samvada_backend_kind_null` / `samvada_backend_kind_libsystemd` / `samvada_backend_kind_pure_cyrius` | Kind-word values: `0` / `1` / `2` |
| `samvada_ffi_alloc` | Heap-allocate a zero-filled table (Cyrius-side mocks; C shim builds its table on the C stack) |
| `samvada_ffi_get_slot(t, off)` | Null-safe `load64(t + off)`. Returns `0` if `t == 0` |
| `samvada_ffi_set_slot(t, off, fp)` | Null-safe `store64`. Returns `0` if `t == 0` |
| `samvada_ffi_kind(t)` | Convenience for `get_slot(t, samvada_slot_kind())` |

The slot offsets and kind values are pinned by
`test_ffi_slot_offsets` and `test_ffi_backend_kinds`. Reordering
these without bumping the C shim would silently misroute every
dispatch — the test turns that into a CPU-test failure rather
than a runtime crash inside libsystemd.

## Module-scope state

Set by `samvada_init`, read by the rest of the API, cleared by
`samvada_release`. Documented here so a consumer reviewing the
implementation knows what to expect under `cyrius vet`'s "dead
code" list (these are reachable only through the public fns
above).

| Var | Set by | Cleared by | Notes |
|---|---|---|---|
| `_samvada_table` | `samvada_init` | `samvada_release` | The fn-table pointer; reset to `0` on release |
| `_samvada_bus` | `samvada_init` | `samvada_release` | sd_bus handle; unref'd via `close_bus` slot |
| `_samvada_sess` | `samvada_init` | (never) | Heap ptr to NUL-terminated session path; bump-allocated, lives for process lifetime |
| `_samvada_outs` | `samvada_init` | (never) | 16-byte scratch for `(fd_out, active_out)` from TakeDevice |

`_samvada_sess` and `_samvada_outs` survive `samvada_release` so
a re-`samvada_init` reuses the same buffers. This is intentional
— the bump allocator never frees, and reuse keeps the per-call
paths alloc-free (a v1.0 perf goal).

## Error-code catalog

Quick-reference for the negative codes consumers will see. All
are `-errno` semantics inherited from sd-bus — magnitudes match
`/usr/include/asm-generic/errno-base.h` and `errno.h`.

| Magnitude | Symbol | Source | Meaning in samvada |
|---|---|---|---|
| 2 | `ENOENT` | sd-bus | logind not running, or session has no caller pid |
| 12 | `ENOMEM` | samvada | scratch buffer alloc failed |
| 13 | `EACCES` | sd-bus | logind denied (e.g. no DRM master available) |
| 22 | `EINVAL` | samvada | bad table / NULL kind / missing init |
| 38 | `ENOSYS` | samvada | requested slot is null (backend doesn't implement that fn) |
| 105 | `ENOBUFS` | C shim | session path didn't fit in the 512-byte scratch |
| 107 | `ENOTCONN` | samvada | bus open returned `NULL` without an explicit error |
| 113 | `EHOSTUNREACH` | sd-bus | no logind session for caller pid (typical for ssh shells) |
| any other | (sd-bus) | sd-bus | pass-through; consumer logs and aborts |

Consumers should treat any negative value as fatal — there is
no transient / retry path in the v0.x API.

## Test coverage map

Each public symbol's structural pin in `tests/samvada.tcyr`:

| Fn | Test group | Asserts |
|---|---|---|
| `samvada_version` | `v0.2.x packed triple` | 4 (packed + 3 lanes) |
| `samvada_init` (null) | `init rejects null table` | 1 |
| `samvada_init` (NULL kind) | `init rejects NULL-kind table` | 2 (alloc + reject) |
| `samvada_release` | `release is idempotent` | 2 (two consecutive calls) |
| FFI plumbing | `ffi: slot offsets pin C shim contract` | 10 (8 slots + kind + size) |
| FFI plumbing | `ffi: backend kinds` | 3 |
| FFI plumbing | `ffi: alloc/get/set round-trip` | 5 |
| FFI plumbing | `ffi: get_slot is null-safe` | 3 |

Live-bus paths (`samvada_session_take_device`,
`samvada_session_release_device`, `samvada_pump_signals`) have
**no `tcyr` coverage** — they need a running system dbus +
logind + a real device node. That gap closes when mabda's
`gpu_surface_configure_native_logind` runs end-to-end on a real
desktop session (the M1 closeout gate in `roadmap.md`).

## Cross-references

- [ADR-0002 — append-after-kind FFI invariant](../adr/0002-append-after-kind-ffi-invariant.md)
- [`dbus-marshalling.md`](dbus-marshalling.md) — wire format
- [`../guides/consumer-link.md`](../guides/consumer-link.md) — two-stage build
- [`../sources.md`](../sources.md) — protocol citations
- [`../../SECURITY.md`](../../SECURITY.md) — threat model
