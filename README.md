# samvada

**Status:** v0.2.0 — C-shim + minimum-viable logind subset
landed. Public API stable; live-bus end-to-end validation
pending a consumer (mabda).

`samvada` (Sanskrit *saṃvāda* "dialogue") is the dbus client for
the AGNOS Cyrius suite. Its first consumer is
[mabda](https://github.com/MacCracken/mabda)'s Phase D surface
present path — specifically the `gpu_surface_configure_native_logind`
public API, which needs `org.freedesktop.login1`'s `TakeDevice` to
delegate DRM master from a desktop session.

## Architectural strategy

samvada follows the same C-shim-during-the-dual-backend-era pattern
as mabda's `wgpu-native` integration: the v0.x line ships a
**libsystemd-backed C shim** (`deps/samvada_main.c`) wrapping
`sd_bus_*` calls, with thin Cyrius wrappers above. **At v1.0,
both C-shim deps retire together** — mabda's `wgpu-native` for AMD
+ samvada's `libsystemd` — replaced by pure-Cyrius implementations
or removed if a kernel-level path makes the dbus dance
unnecessary.

| Version | What ships | Status |
|---|---|---|
| v0.1.0 | Scaffold only — no protocol code | ✅ shipped 2026-04-30 |
| v0.2.0 | C-shim binding to `sd_bus`, minimal logind subset | 🟡 code-complete 2026-04-30, awaiting consumer e2e |
| v0.3+ | Generalize beyond logind (polkit, NetworkManager, generic dbus) | unscoped — files itself when a 2nd consumer hits a wall |
| **v1.0** | **Pure-Cyrius dbus marshaller OR removal** — coordinated with mabda v4.0's wgpu-native retirement | TBD; multi-week if pure-Cyrius |

The mabda public API surface stays stable across all of these:
`gpu_surface_configure_native_logind` is committed in mabda v3.0
as a stub; samvada v0.2.0 fills the real body; mabda v4.0 swaps to
pure-Cyrius without breaking consumer code.

## What the C shim covers (v0.2.0 scope)

The minimum-viable subset for mabda Phase D logind:

- **System bus connect.** `sd_bus_default_system()` — picks the
  right socket, handles `DBUS_SYSTEM_BUS_ADDRESS`.
- **`org.freedesktop.login1.Manager.GetSession`** — find the
  caller's session.
- **`org.freedesktop.login1.Session.TakeDevice(major, minor)`** —
  ask logind to hand back a fd with DRM master delegated.
- **`PauseDevice` / `ResumeDevice` signal handlers** — cooperate
  with the kernel's vt-switch handoff.
- **`ReleaseDevice`** on teardown.

Out of scope for v0.x: object-introspection, generic property-get
(only the logind subset), property-changed signals beyond Pause /
Resume, any non-system-bus paths.

## Build

```sh
cyrius deps                                    # resolve stdlib
cyrius build src/main.cyr build/samvada        # smoke build
cyrius test                                    # run tests/samvada.tcyr
```

C shim isn't built yet (v0.2.0); when it lands, builds will need
`libsystemd-dev` on the consumer's machine. samvada itself doesn't
link libsystemd — consumers do, the same way mabda consumers
provide wgpu-native.

## Repo layout

```text
samvada/
├── src/
│   ├── main.cyr        — smoke entry point
│   ├── lib.cyr         — include chain
│   └── samvada.cyr     — Cyrius API surface (placeholder v0.1.0)
├── deps/
│   └── samvada_main.c  — C shim entry, fn-table pattern (v0.2.0)
├── tests/
│   ├── samvada.tcyr    — CPU smoke + (eventually) marshalling tests
│   ├── samvada.bcyr    — bench harness (later)
│   └── samvada.fcyr    — fuzz harness (later)
├── docs/               — design notes (filled as v0.2.0+ lands)
├── lib/                — vendored stdlib (gitignored, populated by `cyrius deps`)
├── cyrius.cyml         — package manifest
├── VERSION             — 0.1.0
└── CHANGELOG.md
```

The `deps/samvada_main.c` shape mirrors `mabda/deps/wgpu_main.c`:
function-table-handed-to-`samvada_main()`, `_cyrius_init` +
`alloc_init` preamble in C, `fncall1`/`fncall2`/etc. plumbing on
the Cyrius side.

## Roadmap to v1.0

See [`docs/development/roadmap.md`](docs/development/roadmap.md).

## License

GPL-3.0-only. See `LICENSE`.
