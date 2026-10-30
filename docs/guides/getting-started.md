# Getting started with samvada

> **Consuming samvada?** You want
> [`consumer-link.md`](consumer-link.md) instead — the whole
> integration is one dependency and one function call. This page
> is for working *on* samvada.

## Build

```sh
cyrius deps                                 # resolve stdlib
cyrius build src/main.cyr build/samvada     # smoke build
cyrius test                                 # [build].test + tests/*.tcyr (1152 asserts)
```

## Layout

- `src/main.cyr` — standalone smoke entry. Top-level
  `var r = main(); syscall(SYS_EXIT, r);`.
- `src/samvada.cyr` — seven of the eight public fns
  (`samvada_native_init` is in `src/dbus_session.cyr`).
- `src/samvada_ffi.cyr` — fn-table layout (11 slots, 88 bytes).
- `src/dbus_*.cyr` — the native dbus implementation. See
  [`state.md`](../development/state.md) for what each module owns.
- `src/test.cyr` — `cyrius.cyml [build].test` entry.
- `tests/*.tcyr` — the suite; `cyrius test` auto-discovers.
- `tests/fixtures/dbus/` — real captured wire bytes, labelled real
  vs synthetic in `MANIFEST.md`.

## Adding a feature

1. Write the code and a test in the same change.
2. Run `cyrius test`.
3. **Prove the test can fail.** Break the code deliberately and
   confirm the test catches it. This project's three worst bugs
   all shipped behind green suites, because every test that could
   have caught them took the error branch. A test you have not
   watched fail is not evidence.
4. Run the full gate sweep — see
   [`CONTRIBUTING.md`](../../CONTRIBUTING.md). Changing the public
   surface requires editing `tools/public_surface.txt`.
5. Update `CHANGELOG.md`; bump `VERSION` before tagging.

See [`../adr/template.md`](../adr/template.md) when a non-trivial
design choice deserves an ADR.
