# samvada hardening review — 2026-05-01

**Scope**: P(-1) hardening pass against the v0.2.x C-shim
surface, targeting the v0.2.2 ship and any v0.3.x feature work
that follows. Covers `src/samvada.cyr` (public API +
module-scope state), `src/samvada_ffi.cyr` (FFI plumbing),
`deps/samvada_main.c` (C shim), and `tests/samvada.tcyr`
(structural pins).

**Auditor**: internal.

**Target release**: 0.2.2 (post-fix).

**Posture**: this is a **hardening review**, not the formal
pre-v1.0 security audit that `SECURITY.md` §"Audit History"
gates. The C-shim era is intentionally pre-audit because the
surface is transitional (libsystemd → pure-Cyrius marshaller
or removal at v1.0). Findings here harden the v0.x line
without committing samvada to a formal disclosure / response
posture before the surface stabilizes.

**Method**: per-file code walk of the entire surface (~250
LoC Cyrius + 270 LoC C); banned-pattern grep
(`sys_system` / `execve` / `fork` / `system(` / writes to
`/etc`, `/bin`, `/sbin` — all clean); strict-flag C-shim
compile (`-Wall -Wextra -Werror -Wshadow -Wconversion
-Wcast-qual -Wformat=2` against libsystemd 260 — clean); FFI
table-layout integrity check (slot offsets vs C `#define`s vs
test pins — match); fd-passing dup-safety walk; double-init /
double-release / re-entry walk.

## Summary

| Severity  | Count | Disposition                                       |
|-----------|-------|---------------------------------------------------|
| CRITICAL  | 0     | —                                                 |
| HIGH      | 1     | Fix required before 0.2.2                         |
| MEDIUM    | 1     | Fix required before 0.2.2                         |
| LOW       | 2     | Defer with documentation; not ship-blocking       |
| Total     | 4     |                                                   |

The surface is small and the C-shim's `sd_bus_*` wrappers are
each 5-30 LoC, which keeps the attack surface tractable. The
HIGH finding is a re-entry leak in `samvada_init` that surfaces
the moment a consumer retries init after a partial failure —
already plausible during mabda's logind dispatcher development.
The MEDIUM finding is an `errno`-staleness defect in the
`sb_take_device` error path that is currently unreachable in
practice (libsystemd's `sd_bus_message_read("hb")` is documented
to return a valid fd on success) but breaks the C-side
defensive contract.

The two LOW findings are defense-in-depth improvements that
land if cheap; they were considered for 0.2.2 but the patches
were not free of churn risk and the underlying paths are not
currently reachable from consumer code.

---

## Methodology

1. **Attack-surface enumeration**. samvada's input boundaries
   in v0.x:
   - Consumer-supplied `table` pointer to `samvada_init`
     (validated: null-check + kind-word check).
   - Consumer-supplied `major` / `minor` to
     `samvada_session_*_device` (currently passed through to
     logind without range validation; logind enforces ACLs).
   - Consumer-supplied caller pid (implicit via `sys_getpid`
     inside samvada — no consumer-supplied pid path in v0.x).
   - logind reply bytes via `sd_bus_message_read` — validated
     by libsystemd against the requested signature before
     samvada sees them.
   - `SCM_RIGHTS` fds from logind's `TakeDevice` — `dup`'d
     before message unref so consumer fd lifetime is
     independent of dbus message lifetime.
   - `PauseDevice` / `ResumeDevice` signal payloads on the
     session path — only `org.freedesktop.login1` can emit
     these on the system bus, so trust is upstream.

2. **Code walk** per file with checklist: input validation,
   buffer safety, integer overflow, syscall return handling,
   FFI table integrity, resource leak on failure paths,
   re-entry / idempotency.

3. **Strict-flag rebuild** of `deps/samvada_main.c` adding
   `-Wshadow -Wconversion -Wcast-qual -Wformat=2` to the
   project's CI flags. No new warnings surfaced.

4. **CVE sweep** — none in scope. samvada wraps libsystemd
   v260; per `SECURITY.md` §Scope, libsystemd CVEs are
   upstream's responsibility unless they specifically
   surface through samvada's wrapper. No 2026 libsystemd CVE
   touches the call subset samvada exercises
   (`sd_bus_default_system`, `sd_bus_call_method`,
   `sd_bus_message_read` on `o`/`hb`, `sd_bus_match_signal`,
   `sd_bus_process`, `sd_bus_unref`).

5. **Findings triaged** per the ladder in `SECURITY.md`:
   CRITICAL / HIGH / MEDIUM / LOW.

---

## Findings

### HIGH-1 — `samvada_init` is not double-init safe; leaks bus + scratch on re-entry

**File**: `src/samvada.cyr` lines 61–101 (`samvada_init`).

**Defect**: a second call to `samvada_init` without an
intervening `samvada_release` overwrites `_samvada_table`,
`_samvada_sess`, `_samvada_outs`, and `_samvada_bus` without
releasing the previous values. The leaks compound:

- The previously-opened `sd_bus *` is overwritten without
  `sd_bus_unref` — a persistent dbus connection until process
  exit.
- `_samvada_sess` (512 B) and `_samvada_outs` (16 B) are
  re-allocated from the bump allocator — bytes orphaned.
- The previous `_samvada_table` pointer is overwritten — any
  C-shim-stack-resident table the previous init pinned
  becomes unreachable.

**Reachability**: a consumer that retries init after a partial
failure (e.g. `-ENOTCONN` from a bus open under load) hits this
on the second call. Mabda's
`gpu_surface_configure_native_logind` does not currently retry,
but the v3.x multi-GPU disambiguation work could plausibly
introduce a per-card init dance.

**Fix** (lands in 0.2.2):

1. Reject re-init with `-EBUSY` (`-16`) when
   `_samvada_table != 0`. Consumer must call `samvada_release`
   before re-init.
2. Reuse already-allocated scratch buffers
   (`_samvada_sess`, `_samvada_outs`) on re-init-after-release
   — both survive `samvada_release` (bump allocator), so a
   conditional `if (_samvada_sess == 0) alloc(...)` pattern
   avoids the leak entirely on the legitimate re-init path.

**Test**: new `test_init_rejects_double_init` group asserts
`-16` on second init and that `samvada_release` clears the
guard.

### MEDIUM-1 — `sb_take_device` reads stale `errno` if logind returns `fd < 0`

**File**: `deps/samvada_main.c` lines 122–155 (`sb_take_device`).

**Defect**: the success path is

```c
int dup_fd = (fd >= 0) ? dup(fd) : -1;
*(int32_t *)(uintptr_t)fd_out = (int32_t)dup_fd;
...
return (dup_fd < 0) ? -errno : 0;
```

If `sd_bus_message_read` returns `>= 0` but the `h` field
delivered `fd < 0`, `dup` is **not** called and `errno` is
whatever the previous syscall left there. The function
returns `-errno` on a stale value — could be `0`, could be
anything. The caller would interpret `0` as success while
having received `fd_out = -1`, triggering an immediate
`sys_close(-1)` on the Cyrius side.

**Reachability**: libsystemd's contract for
`sd_bus_message_read` on signature `hb` documents that on
success the `h` slot is a valid index into the aux fd array,
and the returned fd is `>= 0`. So this is not currently
reachable from real logind traffic. It is reachable from a
malicious or buggy peer that crafts a `(fd=-1, b)` reply
through some other library on the same bus name — narrow but
not impossible.

**Fix** (lands in 0.2.2): explicit `-EBADF` for the `fd < 0`
case, never reaching `dup`.

```c
if (fd < 0) {
    sd_bus_message_unref(reply);
    sd_bus_error_free(&err);
    return -EBADF;
}
int dup_fd = dup(fd);
if (dup_fd < 0) {
    int saved = errno;
    sd_bus_message_unref(reply);
    sd_bus_error_free(&err);
    return -saved;
}
```

The `int saved = errno` capture also closes a smaller
secondary issue: the original code reads `errno` after
`sd_bus_message_unref` + `sd_bus_error_free`, which
*could* clobber it under a future libsystemd that touches
errno on its cleanup paths. Defense-in-depth.

**Test**: not added (cannot reproduce the path without a
peer-bus harness). Documented in the C source instead.

### LOW-1 — `sb_get_session_path` does not defend against `path == NULL` on a successful read

**File**: `deps/samvada_main.c` lines 78–113.

**Defect**: `sd_bus_message_read(reply, "o", &path)` is checked
for `r >= 0` but `path` is then passed straight to `strlen`. If
libsystemd's contract is ever violated (or the `o` slot is
empty for some reason), this dereferences NULL.

**Reachability**: libsystemd guarantees `path != NULL` on
`r >= 0` for a string-shaped read. Currently unreachable.

**Disposition**: defer. A defensive `if (!path) return -EINVAL;`
is one line and harmless, but it ships under a behavior change
that wants its own test, and the current test surface cannot
exercise it. File on the v0.3.x backlog.

### LOW-2 — `samvada_release` does not zero the scratch buffers

**File**: `src/samvada.cyr` lines 155–163 (`samvada_release`).

**Defect**: the function clears `_samvada_bus` and
`_samvada_table` but leaves `_samvada_sess` and `_samvada_outs`
holding their last-known contents (a session path string and
a possibly-dup'd fd index). Re-init reuses these buffers.

**Reachability**: information leak only across re-init
boundaries within the same process. The session path is not
secret (logind gives it to anyone on the bus). The fd index in
`_samvada_outs` is from the previous TakeDevice; it is no
longer valid after `samvada_release` runs.

**Disposition**: defer. The leak is entirely intra-process and
the values are non-secret. With HIGH-1's fix, re-init explicitly
re-zeroes these buffers before any consumer-visible state, so
the LOW-2 surface narrows further.

---

## What this audit does not cover

- **Live-bus behavior** — every `sb_*` wrapper's runtime
  behavior against a real `dbus-broker` + `systemd-logind` is
  exercised only through mabda's
  `gpu_surface_configure_native_logind` end-to-end run, which
  has not yet happened (M1 closeout gate).
- **`sd_bus_match_signal` callback safety** — the callback
  invocation lifecycle (called from `sd_bus_process`, which
  samvada's `samvada_pump_signals` drives) has not been
  exercised. Consumer-supplied callbacks must be re-entrant
  with respect to `samvada_pump_signals` itself; document this
  contract on the v0.3.x signal-API design.
- **Multi-process / multi-thread** — samvada's module-scope
  state (`_samvada_table` etc.) is not thread-safe. Consumers
  must serialize access. v0.x is single-threaded by design;
  thread safety is a v1.0+ design conversation.
- **Rate-limiting / DoS hardening** of the bus-pump path —
  `sb_pump_signals` drains until empty without a cap. A bus
  flooded with `PauseDevice` events would block the consumer's
  event loop. v0.3+ should expose a max-events parameter.

These are **roadmap items, not findings** — the audit is
scoped to the v0.2.x C-shim line.

---

## Cross-references

- [`SECURITY.md`](../../SECURITY.md) — threat model + audit gating
- [`docs/architecture/public-api.md`](../architecture/public-api.md) — surface map
- [`docs/architecture/dbus-marshalling.md`](../architecture/dbus-marshalling.md) — wire format
- [`docs/sources.md`](../sources.md) — protocol citations
- [`docs/development/roadmap.md`](../development/roadmap.md) §M3 — pivot decision
