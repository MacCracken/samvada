# samvada

**Status:** v0.5.1 — the logind path now issues `TakeControl`,
so `TakeDevice` can succeed for the first time. Signal delivery
(`PauseDevice` / `ResumeDevice`) is **not** wired. Live-bus
end-to-end through a seated session remains hardware-gated and
unverified.

`samvada` (Sanskrit *saṃvāda* "dialogue") is the dbus client for
the AGNOS Cyrius suite. Its first consumer is
[mabda](https://github.com/MacCracken/mabda)'s Phase D surface
present path — specifically the `gpu_surface_configure_native_logind`
public API, which needs `org.freedesktop.login1`'s `TakeDevice` to
delegate DRM master from a desktop session.

## What changed in 0.5.1

logind answers `TakeDevice` with
`org.freedesktop.login1.NotInControl` unless the **same bus
connection** already holds session control via `TakeControl`.
samvada never sent it — so every release from **v0.2.0 through
v0.5.0 could not take a device in any environment**. The failure
was universal, not environmental. `samvada_init()`
(`src/samvada.cyr`) now calls `TakeControl` immediately after
resolving the session path, and `samvada_release()` drops it via
`ReleaseControl` before closing the bus. A fn-table whose
`take_control` slot is null — a pre-0.5.1 backend — makes
`samvada_init()` return `-38` (`-ENOSYS`) rather than proceed
into a guaranteed rejection.

The C shim changed shape in the same release. `deps/samvada_main.c`
used to define `main()` unconditionally, which collided with the
`main()` every real consumer already owns; the documented
two-stage build could not link. It is now a library —
`samvada_shim_init()`, called from the consumer's own `main()`.
See [Build](#build).

Both are breaking for anyone integrated against ≤ 0.5.0. mabda
pins `0.4.1` today and must move to `0.5.1` for the logind path
to work at all.

## Architectural strategy

samvada follows the same C-shim-during-the-dual-backend-era pattern
as mabda's `wgpu-native` integration: the v0.x line ships a
**libsystemd-backed C shim** (`deps/samvada_main.c`) wrapping
`sd_bus_*` calls, with thin Cyrius wrappers above. **At v1.0,
both C-shim deps retire together** — mabda's `wgpu-native` for AMD
+ samvada's `libsystemd`. [ADR-0003](docs/adr/0003-native-cyrius-dbus.md)
settles what replaces samvada's half: native dbus in Cyrius —
socket transport, SASL `EXTERNAL`, marshalling, `SCM_RIGHTS` fd
passing, logind session layer. Deleting samvada outright is
retired as the plan of record.

| Version | What ships | Status |
|---|---|---|
| v0.1.0 | Scaffold only — no protocol code | ✅ shipped 2026-04-30 |
| v0.2.0 – v0.5.0 | C-shim binding to `sd_bus`, minimal logind subset | ⚠️ shipped, but `TakeDevice` could never succeed — no `TakeControl` |
| **v0.5.1** | `TakeControl` / `ReleaseControl`; shim becomes a library, not a `main()` | 🟡 current — covered by a mock backend, live bus still unverified |
| v0.6+ | Signal delivery (Pause / Resume), then generalize beyond logind (polkit, NetworkManager, generic dbus) | unscoped — files itself when a consumer hits the wall |
| **v1.0** | **Native Cyrius dbus** — the libsystemd shim is deleted at the tag; the public API is unchanged | path decided (ADR-0003), sequenced with mabda v4.0; multi-week |

mabda's own public surface is unaffected by any of this:
`gpu_surface_configure_native_logind` is committed in mabda v3.0,
samvada 0.5.1 is the first release whose body can actually reach
`TakeDevice`, and mabda v4.0 swaps to pure-Cyrius without
touching consumer code.

## Public API

```text
samvada_init(table)                          -> 0 | -err
samvada_session_take_device(major, minor)    -> fd | -err
samvada_session_release_device(major, minor) -> 0 | -err
samvada_pump_signals()                       -> events_drained | -err
samvada_release()                            -> 0   (idempotent)
samvada_version()                            -> packed u32 — (0 << 16) | (5 << 8) | 1
samvada_main(table)                          -> 0 | -err   (C-shim entry)
```

Errors are negative sd-bus errnos, so consumers branch on `< 0`
and the C convention passes through unchanged. The Cyrius
signatures are frozen across v0.x and across the v1.0 pivot;
0.5.1 changed behaviour behind them (above), not their shape.
Full map — preconditions, every error code, the test that pins
it: [`docs/architecture/public-api.md`](docs/architecture/public-api.md).

## What the C shim covers

The minimum-viable subset for mabda Phase D logind, all in
`deps/samvada_main.c`:

- **System bus connect** — `sd_bus_default_system()`, which picks
  the right socket and honours `DBUS_SYSTEM_BUS_ADDRESS`.
- **`Manager.GetSessionByPID(pid)`** — resolve our own session
  object path.
- **`Session.TakeControl(false)`** — the mandatory precondition
  above. `false` means "do not steal control from another
  controller".
- **`Session.TakeDevice(major, minor)`** → `(fd, inactive)`. The
  fd is re-opened with `fcntl(F_DUPFD_CLOEXEC)`, so it is
  independent of the dbus message lifetime *and* does not survive
  an `execve` in the consumer — a plain `dup()` clears
  `FD_CLOEXEC` and would leak DRM master into children.
- **`Session.ReleaseDevice(major, minor)`** and
  **`Session.ReleaseControl()`** on teardown.
- **Bounded message pump** — at most 256 messages per
  `samvada_pump_signals()` call, so a local signal flood cannot
  hold an event-loop tick captive; the residue drains next tick.

Device numbers are range-checked (`0 ≤ v ≤ UINT32_MAX`) at the C
boundary and rejected with `-EINVAL` before the 64→32 bit cast.

## What is NOT wired

Read this before planning against samvada.

- **`PauseDevice` / `ResumeDevice` are not delivered.** The shim
  populates `sb_subscribe_pause_resume` (slot 48) and
  `sb_unsubscribe` (slot 56), but no Cyrius code dispatches
  either slot and there is no public API to install a match rule.
  `samvada_pump_signals()` drains bus messages without ever
  running a pause/resume callback. A consumer that needs
  vt-switch handoff cannot get it from samvada today.
- **`PauseDeviceComplete` is not sent** — the acknowledgement
  half of the handoff is absent too.
- **No live-bus end-to-end.** `tests/samvada.tcyr` runs 114
  asserts against a pure-Cyrius mock backend (`mock_table_new()`),
  which gives take / release / pump real behavioural coverage
  with no hardware — but it is not a seated session. The
  `TakeControl` defect was reproduced by hand against
  systemd-logind
  ([audit CRIT-1](docs/audit/2026-09-09-audit.md)); the full path
  through a real seat is still unverified.
- **samvada is single-threaded.** Module-scope state carries no
  locking; consumers must serialize every call.

Also out of scope for v0.x: object introspection, generic
property-get, property-changed signals, any non-system-bus path.

## Build

Standalone — no shim, no libsystemd:

```sh
cyrius deps                                  # resolve stdlib
cyrius build src/main.cyr build/samvada      # smoke build
cyrius test                                  # 114 asserts, mock backend
```

### Linking into a consumer

samvada does not link libsystemd. The consumer does, at its own
edge, so the v1.0 retirement is a clean swap. **The shim does not
own `main()`**: it exposes `long samvada_shim_init(void)`, which
builds the static fn-table and calls into Cyrius. Call it once
from your own `main()`, after the Cyrius runtime preamble:

```c
extern void _cyrius_init(void);
extern long alloc_init(void);
extern long samvada_shim_init(void);

int main(void) {
    _cyrius_init();
    alloc_init();
    long rc = samvada_shim_init();      /* 0, or a negative sd-bus errno */
    if (rc < 0) { return 1; }
    /* ... your loop; the Cyrius public API is live from here ... */
}
```

```sh
# 1. the C shim (it no longer defines main() — you do)
cc -c deps/samvada_main.c $(pkg-config --cflags libsystemd) -o build/samvada_main.o

# 2. your Cyrius source -> a relocatable object. `cyrius build` emits a finished
#    executable, so an object needs the `object;` directive through cycc.
{ printf 'object;\n'
  # `samvada` goes LAST — cycc does not apply the manifest's auto-include.
  for m in syscalls string fmt alloc io vec str assert tagged fnptr samvada; do
      echo "include \"lib/$m.cyr\""
  done
  cat src/app.cyr
} | cycc > build/app.o

# 3. your own main(), which calls samvada_shim_init()
cc -c src/launch.c -o build/main.o    # calls samvada_shim_init()

# 4. link
cc build/samvada_main.o build/app.o build/main.o \
   $(pkg-config --libs libsystemd) -o build/app
```

Full recipe, with every command verified against a live bus:
[`docs/guides/consumer-link.md`](docs/guides/consumer-link.md).

`-DSAMVADA_STANDALONE_MAIN` compiles a `main()` into the shim for
a standalone probe binary — leave it off for anything that links
into a real application. CI gates both compile modes plus a link
test in the consumer shape (consumer-owned `main()` + shim).

The fn-table is a file-scope `static` in the shim, and samvada
**borrows** it: `samvada_init()` stashes the pointer and re-reads
it on every dispatch, so the table must stay valid and unmodified
from `samvada_init()` until `samvada_release()`.

libsystemd prerequisites and the logind session requirements
(`TakeDevice` needs a real seat — ssh shells are not logind
sessions) are in
[`docs/guides/consumer-link.md`](docs/guides/consumer-link.md).

## Repo layout

```text
samvada/
├── src/
│   ├── main.cyr          — standalone smoke entry (not linked by consumers)
│   ├── lib.cyr           — include chain
│   ├── samvada_ffi.cyr   — fn-table slot offsets (11 slots, 88 bytes)
│   └── samvada.cyr       — public Cyrius API surface
├── deps/
│   └── samvada_main.c    — libsystemd shim: samvada_shim_init(), no main()
├── dist/
│   ├── samvada.cyr       — tracked bundle; consumers resolve it via [deps.samvada]
│   └── samvada.deps      — stdlib leaves the bundle needs in scope
├── tests/
│   ├── samvada.tcyr      — 114 asserts, incl. the pure-Cyrius mock backend
│   ├── samvada.bcyr      — CPU bench baselines
│   ├── samvada_live.bcyr — live-bus bench scaffold (HW-gated; SKIPs with no backend)
│   └── samvada.fcyr      — fuzz stub
├── docs/                 — adr/ architecture/ guides/ development/ audit/ proposals/
├── lib/                  — vendored stdlib (gitignored, populated by `cyrius deps`)
├── cyrius.cyml           — package manifest; the toolchain pin lives here
├── VERSION               — 0.5.1
└── CHANGELOG.md
```

The table obeys ADR-0002's **append-after-kind** invariant:
`kind` stays at `+64` forever and new slots append past it, which
is where `take_control` (`+72`) and `release_control` (`+80`)
landed in 0.5.1 — a v0 caller reading a v(N+1) table never
misreads a fnptr as the kind word. CI cross-checks the C
`#define`s against the Cyrius slot fns on every run.

## Roadmap to v1.0

See [`docs/development/roadmap.md`](docs/development/roadmap.md).

## License

GPL-3.0-only. See `LICENSE`.
