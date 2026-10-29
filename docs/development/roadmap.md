# samvada — roadmap

**Where samvada is.** v1.1.1. It speaks dbus natively, in Cyrius,
over a raw unix socket — no libsystemd, no C shim, no
`pkg-config`, nothing for a consumer to link. The road ADR-0003
set out at 0.5.1 is finished: N0 through N7 shipped, then a
seven-dimension security audit closed across 1.0.0 and 1.0.1, and
the A lane it left behind closed at 1.1.1 — all but signing tags,
which is the maintainer's to do.

**This file is forward-facing.** It says what is *left*. Completed
milestones live in [`CHANGELOG.md`](../../CHANGELOG.md), which is
where the detail belongs; the table below is a one-line index so
nobody has to reconstruct the order. Current state — versions,
test counts, layout, dep gaps — is in [`state.md`](state.md).

---

## Shipped

| | Version | |
|---|---|---|
| M0 — scaffold | 0.1.0 | |
| M1 — libsystemd C shim + logind subset | 0.2.0 | shipped **not correct**; `TakeDevice` could never succeed (audit CRIT-1) |
| P(-1) audit | 0.5.1 | 2 CRITICAL, 10 MEDIUM, 16 LOW |
| N0–N7 — native Cyrius dbus | 0.6.0 → 1.0.0 | signal contract, `SCM_RIGHTS`, byte corpus, transport/auth/framing, codec, session layer, cutover, audit |
| Pre-1.0 audit + reply forgery | 1.0.0 | AUDIT-1..4; forgery closed; provenance check, send-time watermark, wall-clock timeouts |
| A lane — the dimensions 1.0.0 missed | 1.0.1 | ten defects; public surface frozen mechanically |
| Toolchain patch — cyrius 6.6.2 → 6.6.20 | 1.0.2 | no source-logic change; the 6.6.5 / 6.6.6 pin-move checklists worked through and retired |
| Toolchain minor — cyrius 6.6.20 → 6.7.18, CI on Ubuntu 26.04 | 1.1.0 | no native aarch64 syscall numbers left (stdlib names; CI-gated); consumer floor cyrius 6.6.5; every action at its latest major; took the 1.1.0 number the shim retirement had held |
| A lane, closed — A-1, A-2, A-4, A-6 | 1.1.1 | sd-bus error parity; disconnect reported; stray reply fds closed; zero heap per call; 128 MiB cap; private-by-default bundle; reproducible `dist/`; tag signing left to the maintainer |

---

## Next: v1.2.0 — retire the C shim

The one deliberately deferred item from N7. `deps/samvada_main.c`
still ships as a fallback and as `tools/differential/`'s reference
backend.

**Gated on the CG lane below**, and that gate is real rather than
ceremonial: deleting the shim removes both the fallback *and* the
differential harness — which caught a genuine `pump_signals`
defect at 0.11.0 — at the moment the native path is least proven
in the field.

Consumers lose nothing by waiting: `samvada_native_init()` already
gives a zero-libsystemd build today. What 1.2.0 buys is the
tidiness of a native-only tree.

- [ ] `deps/samvada_main.c` deleted; `consumer-link.md` drops the
      two-stage recipe.
- [ ] `tools/differential/` retired or re-pointed (decide which —
      it is the only gate covering C-shim wrapper *wiring*, and CI
      never runs it).
- [ ] The `LIBSYSTEMD` backend kind retired from
      `samvada_ffi_kind`'s whitelist, or kept as a reserved value.
      **ADR-0002's append-after-kind invariant holds either way.**

---

## The CG lane (consumer green) — externally gated

Still open. It gated the 1.0.0 tag, expired per its own clause,
and now gates 1.2.0 (it gated 1.1.0 until that number went to the
toolchain minor).

- **CG-1** — mabda's live-bus e2e from a seated session.
- **CG-2** — a `TakeDevice` that actually returns a descriptor.
- **CG-3** — the `state.md` note confirming the gate cleared.

**A `TakeDevice` that transfers a real descriptor has never run,
by anyone.** 1.0.1 narrows this: the `SCM_RIGHTS` reply path is
now proven end to end on the live bus through `Inhibit`, which
returns an fd unprivileged and needs no seat. What remains
unverified is logind's `TakeDevice` authorisation and DRM-master
semantics on a seated host — not samvada's ability to receive a
descriptor.

**The cheap de-gate, still untried**: a dedicated **text VT** — a
seated session with no compositor holding master — or attaching a
seat with `loginctl`. Every prior plan either deferred to hardware
or proposed running from the maintainer's graphical session, which
can black-screen the user. Try this before accepting the gate as
immovable.

---

## The A lane — closed at 1.1.1, but for signing

1.0.1 closed A-3 and A-5; 1.1.1 closed the rest, each with a test, a
gate or a measurement (CHANGELOG 1.1.1 has the detail and the
mutants). One item is left, and it is not code.

- [ ] **A-2 — sign release tags.** The consumer fetch path is
  authenticated only by `cyrius.lock` on every resolve after the
  first. 1.1.1 gave a first resolve something to check — the release's
  `SHA256SUMS`, which `release.yml` now guarantees describes the tag's
  `dist/` — but that is a second object from the same host, not a
  second authority. A signed annotated tag is, and it needs only the
  maintainer's key: procedure in `CONTRIBUTING.md` § Releasing. Once
  tags are signed and the key is published, `release.yml` can refuse an
  unsigned tag. Related, also a setting rather than code: GitHub's
  *immutable releases* is off for this repository, so published assets
  can be replaced.

What closed the others:

- **A-1 — resource exhaustion.** Compaction is linear in bytes received
  under hostile message sizes (`dbus_frame_moved`, pinned); an over-cap
  flood drains in constant memory; a header declaring ≥ 128 MiB is
  refused, as sd-bus refuses it, instead of starting an ~8 GiB drain;
  1000 fd-bearing signals and 300 forced `MSG_CTRUNC`s leave no
  descriptor and no heap; every logind call and a live init/release
  cycle allocate 0 bytes (were 192–336 B per call and 600 B per cycle).
- **A-2 — build and supply chain.** `dist/` is reproducible: two clean
  rebuilds at different paths, `TZ` and locale are byte-identical, and
  CI's freshness gate repeats that on every push. `release.yml` refuses
  to publish a bundle that differs from the tag's, and publishes the
  `.deps` sidecar too.
- **A-4 — protocol state machine.** Error names map exactly as sd-bus
  maps them (fixture from libsystemd, re-checked in CI). A dead
  connection is a state, reported as sd-bus reports it. Every consumed
  message drops its own descriptors. A logind restart is survived —
  systemd v262 restores the controller, and the mock test drives the
  wire side. Pause/Resume orderings around TakeDevice keep each
  descriptor with its message; `PauseDevice("pause")` is drained,
  unacknowledged, per ADR-0004. **Kept as documented divergences**,
  each stricter than sd-bus: the Hello ordering (sd-bus fails the
  connection with `-EIO` if the first message is not the Hello reply;
  samvada discards and keeps reading), and a corrupt stream (-71 here,
  -74 there; a bad-version message fails the stream here and is
  dropped there). Revisit if samvada ever speaks to a non-broker peer.
- **A-6 — public API contract.** Cyrius grew visibility, which was this
  item's trigger. The bundle is private-by-default and exactly the
  eight frozen fns are `public`; `tools/boundary_check.py` compiles a
  consumer against every one of the 204 internals in CI and requires
  each to be refused. The boundary is Cyrius-level: an `object;` build
  still exports private fns as GLOBAL DEFAULT symbols on 6.7.18, against
  the cyrius guide — worth filing with cyrius.

### Standing rule for this lane

**Every A item closes with evidence, never with "the code was
reviewed".** AUDIT-4 survived five releases, a hand audit, a
differential harness and 400-plus passing tests because every test
that could have caught it took the error branch. Ask which branch
actually executed.

---

## Deferred past 1.0 — the wishlist

All still gated on a **second AGNOS consumer**. One consumer
cannot tell you which generalisation is the right one, and
widening the surface early enlarges exactly what a future pivot
would have to re-implement (ADR-0001 §Neutral).

- `org.freedesktop.DBus.Properties` Get/Set/GetAll.
- `org.freedesktop.DBus.Introspectable`.
- Session bus (per-user, `DBUS_SESSION_BUS_ADDRESS`, abstract
  sockets in `$XDG_RUNTIME_DIR`).
- Generic `dbus_call_method(dst, path, iface, member, …)`.
- Async / non-blocking variants for consumers with their own event
  loops.
- Big-endian decode. The framer is endian-aware; the unmarshaller
  is little-endian only, so a BE peer fails *safe* — it times out
  rather than misreading. Both target arches are little-endian.
- Thread safety. samvada is single-threaded by design; module
  scope state has no locking and consumers must serialize. `var
  buf[N]` being static inside a fn is a structural reason this is
  hard, not merely undone.

---

## Out of scope

- **Windows / macOS portability.** dbus is Linux-shape.
- **DBus 1.x → KDBus migration.** KDBus was never merged.

---

## Contingency

The graded kill-ladder that governed the N lane retired with it —
rungs 1–4 named failures that can no longer occur, since the
native backend shipped and reached parity. One rung is still live:

| Evidence | Fallback | What it still buys |
|---|---|---|
| AGNOS standardizes on a different session-management primitive | **A.2 (removal)**, per [ADR-0003](../adr/0003-native-cyrius-dbus.md)'s retained contingency | Surface is 8 fns with 1 consumer — migration is hours |

---

## Notes / decisions

- **2026-04-30** — Project scaffolded. C-shim-then-pivot locked in
  ([ADR-0001](../adr/0001-c-shim-then-pivot.md)).
- **2026-06-02** — [Proposal 0001](../proposals/0001-v1-dbus-backend-pivot.md)
  scoped Path A.1 without choosing it.
- **2026-09-09** — `0.5.1`. The P(-1) audit found `TakeDevice` had
  never been able to succeed (CRIT-1) and the documented consumer
  link could never have worked (CRIT-2). Both trace to one cause:
  nothing had been executed against a real bus or a real consumer
  link, because the backend was treated as disposable scaffolding.
  [ADR-0003](../adr/0003-native-cyrius-dbus.md) ends the deferral
  and adopts native Cyrius dbus.
- **2026-09-09** — `1.0.0`. Native dbus complete. The planned
  multi-agent audit **failed entirely** (session limit, zero
  findings) and was performed by hand instead, covering four of
  seven dimensions; the rest became the A lane. Reply forgery was
  then audited and closed — a hostile peer cannot forge a reply,
  but the defence is the *broker's*, and sd-bus checks neither
  `SENDER` nor `DESTINATION` either, so parity was worth nothing
  as an argument.
- **2026-09-10** — `1.0.1`. The A lane found ten more defects, two
  of which meant samvada handed the consumer the wrong descriptor
  or the wrong device, silently, and returned success. The pattern
  behind all three of this project's worst bugs is now explicit:
  **a green gate measures the branch it executes.** An unseated
  host answers `TakeDevice` with `AccessDenied`, and an error reply
  carries no fd — so the fd branch never once ran.
- **2026-10-28** — `1.0.2`. cyrius `6.6.2` → `6.6.20`. The 6.6.5
  and 6.6.6 pin-move checklists were worked through and dropped from
  this file; the 6.6.18 `sys_recvmsg` item stays open. The roadmap's
  own rule for a toolchain bump — run the seated-host path as well as
  the unseated one — was **not** met: only the unseated native
  handshake was run, because `take_device` takes session control and
  the only session available was the graphical one.
- **2026-10-28** — `1.1.0`. cyrius `6.6.20` → `6.7.18`, CI on
  `ubuntu-26.04`, every action at its latest major. Released as a
  minor, it takes the number the shim retirement held, so that
  moves to 1.2.0, still behind the CG lane. It also removed
  samvada's native aarch64 syscall pins, found while reading the
  6.7.x migration notes, which raises the consumer floor to cyrius
  6.6.5 (`sys_sendto`). The
  seated-host rule was again **not** met, for the same reason as
  1.0.2.
- **2026-10-29** — `1.1.1`. The A lane worked through to the end. The
  standing rule held up: A-1, A-2 and A-4 each hid a real defect, and
  three of those sat next to passing tests that could not see them —
  the error-map test pinned two wrong values, the fd tests never put a
  descriptor on a reply that should carry none, and the `MSG_CTRUNC`
  test ran the leaking branch without measuring the heap. Signing tags
  is the one item left, because it is the maintainer's key, not code.

## Moved from cyrius's roadmap (2026-10-08) — still open at the 6.7.18 pin

cyrius no longer carries this repo's follow-ups; the items below were moved here from its roadmap. Each
applies from the cyrius version it names. Everything else that changes between this repo's pin and the
current cyrius is in cyrius's `CHANGELOG.md` and `docs/development/ecosystem-migration.md`.

- [ ] **(cyrius ≥ 6.6.18) `sys_recvmsg` on Windows** — distlib's verify compiles the bundle for
  x86_64-windows and reports `sys_recvmsg` undefined there: the stdlib defines it for Linux and agnos only.
  First seen through mabda's distlib; **from 1.0.2 (pin 6.6.20) samvada's own `cyrius distlib` prints it**, still at 6.7.18,
  as a warning — distlib exits 0 and CI's dist gate does not read warnings. Gate the call off Windows, or
  file an issue with cyrius if a Windows decline stub is wanted.

## Found at the 6.7.18 pin move (1.1.0) — open

- [ ] **aarch64 `MSG_CTRUNC`: 2 `tests/dbus_sys.tcyr` asserts fail under qemu-user**, identically on 6.6.20
  and 6.7.18 — EPROTO on truncated ancillary data (got 1) and descriptors closed (11 vs 9). Probably
  qemu-user's ancillary-data emulation; unconfirmed, because aarch64 hardware has never run the suite. Settle
  on hardware before calling the surplus-fd path aarch64-proven.
