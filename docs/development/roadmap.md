# samvada — roadmap

**Where samvada is.** v1.0.1. It speaks dbus natively, in Cyrius,
over a raw unix socket — no libsystemd, no C shim, no
`pkg-config`, nothing for a consumer to link. The road ADR-0003
set out at 0.5.1 is finished: N0 through N7 shipped, then a
seven-dimension security audit closed across 1.0.0 and 1.0.1.

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

---

## Moving the cyrius pin to 6.6.5

Nothing breaks at this bump. cyrius 6.6.5 is not tagged
yet, and nothing below can land against the pin until it
is. The pin is 6.6.2 today, and this section lists only
what 6.6.5 itself changes.

- [ ] `docs/guides/consumer-link.md:170-173` and
      `:558-560` say `cyrius build … --emit-object` is
      silently ignored and writes a linked executable to
      the `.o` path. From 6.6.5 it fails with
      `unknown option '--emit-object'`. Update both
      passages. See the cyrius CHANGELOG [6.6.5] entry
      "The whole CLI took flags as file names, dropped
      flags written after the operand, and dropped extra
      operands".
- [ ] At the bump, re-run `cyrius deps` — the aarch64
      syscall peer moved SYS_UNLINKAT 35 → 263, so an
      un-re-vendored peer's sys_unlink would run
      nanosleep.

---

## Next: v1.1.0 — retire the C shim

The one deliberately deferred item from N7. `deps/samvada_main.c`
still ships as a fallback and as `tools/differential/`'s reference
backend.

**Gated on the CG lane below**, and that gate is real rather than
ceremonial: deleting the shim removes both the fallback *and* the
differential harness — which caught a genuine `pump_signals`
defect at 0.11.0 — at the moment the native path is least proven
in the field.

Consumers lose nothing by waiting: `samvada_native_init()` already
gives a zero-libsystemd build today. What 1.1.0 buys is the
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
and now gates 1.1.0.

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

## The A lane — what is still uncovered

1.0.1 closed A-3 and A-5 and cut into the rest. These remain, and
none of them blocks a release; they gate the claim that samvada is
*audited-hardened*, which it does not make.

**A-1 — resource exhaustion.** The 60 Hz pump and the reply path
now allocate nothing per call, measured. **Still uncovered**:
worst-case buffer compaction driven by a hostile peer, the
over-cap drain driven indefinitely, and the fd queue under
sustained fd-bearing traffic.

**A-2 — build and supply chain.** The toolchain tarball is pinned
by sha256 and all nine action refs by commit SHA, with dependabot
to age them. **Still open**: `dist/samvada.cyr` reproducibility has
never been diffed from a clean tree, and the **consumer fetch path
is authenticated only by `cyrius.lock`** — samvada's tags are
lightweight and unsigned, so a first resolve or a version bump has
nothing to verify against. Signing tags is a release-process
change, not a code change.

**A-4 — protocol state machine.** The SASL reader was driven with
hostile input and holds. **Still uncovered**:
`PauseDevice`/`ResumeDevice` ordering against ADR-0004,
mid-session disconnect, and logind restart. The **Hello-ordering
divergence is documented, not matched** — sd-bus fails the
connection with `-EIO` if the first message is not the Hello
reply; samvada discards and keeps reading. Revisit if samvada ever
speaks to a non-broker peer.

**A-6 — public API contract.** The 8-fn surface is now frozen
mechanically by `tools/surface_check.py` in CI. **Still open**:
`dist/samvada.cyr` exports ~200 functions because Cyrius has no
visibility mechanism, so every internal stays consumer-reachable.
Documented as unstable rather than pretended away; revisit if
Cyrius grows visibility.

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

## Moving the cyrius pin to 6.6.6

**Current pin: `cyrius = "6.6.2"` (cyrius.cyml:8). Bump it; nothing else.**

Checked item by item against the 6.6.6 change list, in samvada's own sources
(`src/`, `programs/`, `tests/` — vendored `lib/` excluded): no `O_APPEND` /
`O_TRUNC` anywhere, so the Windows append/truncate data-corruption fix is a
non-event — and samvada is a **dbus** client for the AGNOS suite (logind /
system bus), which has no Windows story by construction. Zero `struct`
declarations, so the new different-struct-copy compile error and the by-value
>8 B deep-copy change cannot apply. No `async` fns, no `operator` fns, no
`ret2`/`rethi` pair returns, no SIMD-typed returns, no top-level `{ }` blocks,
no `: cstring` parameters, no locally defined `vec_*` — and `assert` and `vec`
are both already in `[deps] stdlib`, so assert.cyr's new transitive
`include "lib/vec.cyr"` cannot collide. `lib/regression.cyr` is not vendored,
so the new exec deadline (`CYRIUS_CHECK_TIMEOUT`) is irrelevant.

**Global redeclaration:** `var r = main();` appears at `src/main.cyr:42` and
`src/test.cyr:10`. Those are two alternative *entry points*, never co-linked, so
neither 6.6.6's new "last definition wins from program start" behaviour nor its
new `redeclared with a different type or size` compile error can fire.

**Measured:** `cyrius build` under 6.6.2 and under 6.6.6 both exit 0 and emit
**zero** warnings or errors.

**Verify after bumping:** `cyrius deps` to re-vendor (6.6.6's `lib/io.cyr` is
rewritten and now self-sufficient — it includes `fmt`/`string` itself), then
`cyrius build`, `cyrius test`, `cyrius lint`, `cyrius vet`,
`cyrius fmt --check`, `cyrius bench` and `cyrius distlib`. Given this repo's own
lesson that *a green gate measures the branch it executes*, run the seated-host
path as well as the unseated one — a toolchain bump is exactly when a
never-executed branch would surface.
