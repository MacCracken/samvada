# samvada — Roadmap to v1.0

**Identity.** Cyrius dbus client for the AGNOS suite. First
consumer: [mabda](https://github.com/MacCracken/mabda)'s Phase D
surface present (`gpu_surface_configure_native_logind`).

**Mission.** Wrap enough of the dbus protocol to talk to logind
for DRM-master delegation. Eventually grow to cover the broader
dbus surface AGNOS tools need (polkit, NetworkManager, generic
service IPC).

**Architectural posture.** Same C-shim-during-the-dual-era
strategy as mabda's wgpu-native integration: ship a libsystemd
binding in v0.x, retire it at v1.0 alongside mabda v4.0's
wgpu-native retirement. Pure-Cyrius dbus marshalling is the
long-term target; the C shim is the pragmatic stop-gap that
lets logind support actually land without a multi-week
pure-Cyrius detour up front.

State (what's currently in tree, what's WIP) lives in
[`state.md`](state.md). This file is the sequencing — what
ships, in what order, against what dependency gates.

---

## v1.0 criteria

- [ ] Public API frozen — every exported symbol documented +
  tested
- [ ] At least one downstream consumer green on `samvada` ≥ 0.2.0
  (mabda's `_backend_native_surface_configure_logind` body
  filled, e2e program runs from a desktop session)
- [ ] Architectural pivot decided: pure-Cyrius dbus marshaller
  OR removal (coordinated with mabda v4.0)
- [ ] Benchmarks captured in `docs/benchmarks.md` (handshake
  latency, signal pump throughput)
- [ ] CHANGELOG complete from v0.1.0 onward
- [ ] Security audit pass (`docs/audit/YYYY-MM-DD-audit.md`)
- [ ] Six-consumer regression sweep — any AGNOS consumer
  depending on samvada builds + tests cleanly

---

## Milestones

### M0 — Scaffold (v0.1.0) — ✅ shipped 2026-04-30

What landed:

- `cyrius init samvada` baseline.
- `README.md` + `docs/development/roadmap.md` + this file with
  full architectural strategy.
- `src/lib.cyr` + `src/samvada.cyr` placeholder (`samvada_version()`
  returns the 0.1.0 version triple as a u32) so the bundle has a
  real symbol consumers can reference.
- `tests/samvada.tcyr` smoke green (`cyrius test` returns 0).
- `cyrius.cyml` populated: description, repository,
  `${file:VERSION}` substitution, expanded stdlib deps for the
  v0.2.0 work (`tagged` for Result types, `fnptr` for the
  fn-table dispatch pattern).
- ADRs / architecture / guides / examples folders ready (from
  `cyrius init`'s default doc tree).

What does NOT land:

- No protocol code, no C shim, no `sd_bus` calls.
- No dbus marshalling.
- No `.github/workflows/` (added in v0.1.1 alongside CI gates).

Why ship v0.1.0 as scaffold-only: lets mabda v3.0 reference
`[deps.samvada]` against a known package shape today. The
scaffold itself is the baseline-of-work commit.

---

### M1 — libsystemd C shim + minimum-viable logind (v0.2.0)

**Estimated effort:** 3–5 sessions.
**Gate:** mabda's `_backend_native_surface_configure_logind`
needs real bytes flowing through.

#### `deps/samvada_main.c` (new)

C shim entry point mirroring `mabda/deps/wgpu_main.c` exactly:

- C `main()` calls `_cyrius_init()` then `alloc_init()`.
- C builds a function table with `sd_bus_*` entries (~12 fns:
  `default_system`, `call_method`, `message_*` for marshalling,
  `add_match` for signals, `process` for the event loop, `unref`
  for cleanup, plus 2–3 helpers).
- C calls `samvada_main(fn_table_ptr)` which the consumer
  defines.
- Cyrius side calls `sd_bus_*` via `fncall1`/`fncall2`/`fncall5`
  through the table.

#### `src/samvada_ffi.cyr` (new)

The function-table layout + `samvada_ffi_init_table` populator,
matching `src/wgpu_ffi.cyr`'s shape. Slot offsets pinned by CPU
asserts in `tests/samvada.tcyr`.

#### `src/samvada.cyr` (filled)

The Cyrius API surface graduates from placeholder to real:

```cyrius
fn samvada_init(fn_table_ptr) → 0|err
fn samvada_session_take_device(major, minor) → fd | -err
fn samvada_session_release_device(major, minor) → 0|err
fn samvada_pump_signals() → events_drained_count
fn samvada_release() → 0
```

Internally these wrap:

- `sd_bus_default_system` — connect to system bus on init.
- `org.freedesktop.login1.Manager.GetSession` — find caller's
  session via `XDG_SESSION_ID` or PID lookup.
- `org.freedesktop.login1.Session.TakeDevice(uint32 major,
  uint32 minor)` — returns a unix_fd + bool active.
- `PauseDevice` / `ResumeDevice` signal handlers — drain via
  `pump_signals`.
- `org.freedesktop.login1.Session.ReleaseDevice` on teardown.

#### `tests/samvada.tcyr`

CPU tests pin the FFI table layout, the slot constants, the
struct shapes for marshalling buffers. Real `sd_bus` calls are
HW-gated (need a running system dbus + logind); guarded by an
`is_dbus_available`-style detection that skips on CI without
dbus.

#### `cyrius.cyml` deltas

- Bump `VERSION` 0.1.0 → 0.2.0.
- `[lib].modules` adds `src/samvada_ffi.cyr`.

#### Documentation

- `docs/architecture/dbus-marshalling.md` — wire format
  reference (header fields, type signatures, alignment rules)
  scoped to what `sd_bus` exposes.
- `docs/guides/consumer-link.md` — how to add libsystemd to a
  consumer's build (mabda updates its own README in parallel).

---

### M2 — Generalize beyond logind (v0.3.x)

**Estimated effort:** as needed by the next AGNOS consumer.

Likely additions when a consumer hits them:

- Generic dbus method-call API (any interface / member, not just
  the logind subset baked into v0.2.0).
- Property get / set with `org.freedesktop.DBus.Properties`.
- Object-path introspection
  (`org.freedesktop.DBus.Introspectable`).
- Session-bus support (today: system bus only).
- Async / non-blocking variants for consumers with their own
  event loops.

No firm scope yet — files itself when the second AGNOS consumer
hits a wall.

---

### M3 — The pivot (v1.0)

**Coordinated with:** mabda v4.0 (the wgpu-native retirement).
**Estimated effort:** multi-week if pure-Cyrius; trivial if
removal.

Two possible exits:

#### Path A — Pure-Cyrius dbus marshaller

If logind dbus integration is still load-bearing for AGNOS
consumers at v4.0 ship, replace the C shim with a hand-rolled
Cyrius dbus client. Scope:

- ~500–1000 LoC for system-bus connect + SASL EXTERNAL auth +
  message marshalling (header fields, type sigs, alignment) +
  method-call / signal-listen plumbing + minimal type system
  (int32, uint32, string, object_path, unix_fd, byte arrays).
- `deps/samvada_main.c` removed.
- libsystemd dep dropped from consumers.

Public API (`samvada_session_take_device` etc.) does **not**
change. Consumer code keeps working; only the impl below the
boundary swaps.

#### Path B — Removal

If the v4.0 logind story has evolved (e.g., kernel-level
master-delegation, or AGNOS standardizes on a different
session-management primitive), samvada can be deprecated.
Consumers' `gpu_surface_configure_native_logind` paths get
revised in mabda v4.0 to call the new mechanism directly.

The decision between A and B happens during mabda v4.0 design.
samvada's v0.x line is structured so either exit is clean — the
public API is small (5 fns) and the implementation is isolated
in one C shim + one Cyrius FFI module.

---

## Out of scope (for v1.0)

- **Authenticated session bus** (per-user dbus). Out until a
  consumer needs it.
- **Property-changed signal subscriptions** beyond the logind
  Pause/Resume cases. Out until needed.
- **Windows / macOS portability.** dbus is Linux-shape;
  AGNOS-on-Windows would need WinRT / COM analogs, not samvada.
  Different package.
- **DBus 1.x → KDBus migration.** KDBus was never merged into
  Linux; non-issue.

---

## Notes / decisions

- **2026-04-30** — Project scaffolded via `cyrius init samvada`,
  moved to `~/Repos/samvada`, README + this roadmap filled in,
  baseline `cyrius test` green. Decision: ship v0.1.0 as
  scaffold-only so mabda v3.0 can reference `[deps.samvada]`
  against a known package shape, even though no protocol code
  has landed yet. C-shim-then-pivot strategy locked in (was
  pure-Cyrius-from-v0.x earlier the same day; revised after
  realizing the wgpu-native parallel made the C shim the better
  stop-gap).
