# samvada — roadmap

**Where samvada is.** v1.1.2. It speaks dbus natively, in Cyrius,
over a raw unix socket — no libsystemd, no C shim, no
`pkg-config`, nothing for a consumer to link. The native road
(ADR-0003) and the audit lanes behind it are finished; what is left
is below.

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
| P(-1) pass | 1.1.2 | 1 HIGH (a bus peer could choose `TakeDevice`'s descriptor), 5 MEDIUM; descriptors attributed by stream position, reads bounded to one message; deadline-only calls; devices held by number; gates rebuilt on a comment-aware lexer, each with positive controls |

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
      CI never runs it; since 1.1.2 `tools/shim_slot_check.c` gates
      the shim's wrapper *wiring*, and goes with the shim).
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

## Open — samvada's own work

- [ ] **Sign release tags** (the last A-2 item). After the first
  resolve, a consumer's fetch is authenticated only by `cyrius.lock`;
  on a first resolve, only by the release's `SHA256SUMS`, which comes
  from the same host. A signed annotated tag is a second authority and
  needs only the maintainer's key — procedure in `CONTRIBUTING.md`
  § Releasing. Once tags are signed and the key is published,
  `release.yml` refuses an unsigned tag. Turning on GitHub's
  *immutable releases* for the repository is the companion setting.
- [ ] **aarch64 `MSG_CTRUNC` on hardware.** Under `qemu-aarch64` user
  mode the truncation flag never reaches the guest, so five asserts fail
  there, all on this one cause: three in `tests/dbus_sys.tcyr` (the
  truncation is reported; nothing left open; 100 truncations reported)
  and two in `tests/dbus_session.tcyr` (a truncated set is refused; its
  descriptors closed). The bytes and the descriptors that fit do arrive.
  Probably qemu's ancillary-data emulation (1.1.1 saw the same cause in
  two asserts); settle it on real aarch64 hardware before calling the
  truncated-set refusal aarch64-proven.

**Standing rule for audit work: every item closes with evidence,
never with "the code was reviewed".** AUDIT-4 survived five releases
and 400-plus passing tests because every test that could have caught
it took the error branch; three of 1.1.1's defects sat next to passing
tests that could not see them. Ask which branch actually executed.

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
  is little-endian only, so a big-endian message is discarded, its
  descriptors with it, and a BE peer's call times out rather than
  misreading. That is *safe* only since 1.1.2: until then such a
  message's descriptors stayed queued for the next `TakeDevice`
  (P1-1). Both target arches are little-endian.
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

## Decisions in force

- [ADR-0002](../adr/0002-append-after-kind-ffi-invariant.md) — append
  after `kind`; the FFI layout never reorders.
- [ADR-0003](../adr/0003-native-cyrius-dbus.md) — native Cyrius dbus;
  removal (A.2) retained only as the contingency above.
- [ADR-0004](../adr/0004-session-control-lifecycle-and-signal-visibility.md)
  — session control scoped to device ownership; session signals not
  surfaced.
- **A toolchain bump runs the seated-host path as well as the unseated
  one.** Not yet met at 1.0.2 or 1.1.0, for want of a seat that is not
  the maintainer's graphical session — the CG lane's cheap de-gate
  would settle it.

The history of how each was reached is in the CHANGELOG and the ADRs.
