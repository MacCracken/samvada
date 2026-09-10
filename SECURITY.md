# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in samvada, please
report it responsibly through **GitHub Security Advisories**:

1. Go to the [Security tab](../../security/advisories) of this repository.
2. Click **"Report a vulnerability"**.
3. Fill in the details and submit.

**Do not open a public issue for security vulnerabilities.**

## Scope

This policy covers samvada's own source — `src/*.cyr`,
`deps/samvada_main.c`, the `dist/samvada.cyr` bundle and its
`dist/samvada.deps` sidecar — and the release supply chain that
produces them (`.github/workflows/`), since a consumer fetching
a tag trusts both.

samvada wraps `libsystemd`'s `sd_bus_*` API in v0.x; vulnerabilities
in libsystemd itself, in the dbus broker, in logind, or in the
underlying kernel session-management primitives should be reported
upstream (systemd / your distro). If an upstream bug specifically
affects samvada users — for example, a libsystemd misuse only
exposed by samvada's wrapper — flag it here and we will harden
the wrapper, document the workaround, or both.

## Supported Versions

| Version | Supported                                                            |
|---------|----------------------------------------------------------------------|
| 1.0.x   | **Yes** — current release line, receives security fixes              |
| 0.11.x  | No — superseded by 1.0.0                                             |
| 0.5.x   | No — the C-shim era; superseded                                      |
| 0.4.x   | No — superseded by 0.5.1; upgrade, there is no backport path         |
| 0.3.x   | No                                                                   |
| 0.2.x   | No                                                                   |
| 0.1.x   | No — pre-protocol scaffold only                                      |

Every release from 0.2.0 through 0.5.0 carried two defects that
0.5.1 fixes and that cannot be backported without the same code
change (see "Audit History"):

- `samvada_session_take_device()` could not succeed in **any**
  environment — samvada never sent `TakeControl`, so logind
  answered every `TakeDevice` with
  `org.freedesktop.login1.NotInControl`.
- `deps/samvada_main.c` defined `main()` unconditionally, so the
  shim could not link into a consumer that owns its own `main()`
  — i.e. into any real consumer. The documented two-stage build
  never worked.

Consequently the 0.2.x–0.4.x lines receive no security fixes:
the correct remedy for anything found there is to move to 0.5.x.
mabda currently pins `tag = "0.4.1"` and has not yet been
re-pinned.

Once v1.0 ships and the libsystemd C shim retires (replaced by
the pure-Cyrius dbus marshaller, or removed entirely — see
`docs/development/roadmap.md` §M3), v0.x will move to
security-fix-only maintenance for one release cycle, then EOL.

## Response Timeline

| Action                           | Target                     |
|----------------------------------|----------------------------|
| Acknowledgement                  | Within **48 hours**        |
| Initial assessment               | Within **5 business days** |
| Fix for CRITICAL severity        | Within **14 days**         |
| Fix for HIGH severity            | Within **30 days**         |
| Fix for MEDIUM / LOW severity    | Next scheduled release     |

Severity ladder: **CRITICAL** (exploitable immediately) /
**HIGH** (moderate effort) / **MEDIUM** (specific conditions) /
**LOW** (defense-in-depth).

## Threat Model

samvada sits between consumer Cyrius code and the system dbus
daemon (typically `dbus-broker` or `dbus-daemon`). Inputs samvada
trusts vs. validates:

| Source | Trust | Notes |
|---|---|---|
| Consumer-supplied fn-table pointer (`samvada_init`) | `kind` whitelisted, slots trusted | `samvada_init` rejects a null table, and rejects any `kind` word at `+64` outside `{LIBSYSTEMD=1, PURE_CYRIUS=2}` with `-EINVAL` (`src/samvada.cyr`). The slot fn-pointers themselves are dispatched unvalidated — they *are* the backend. samvada **borrows** the table and re-reads it with `load64` on every call, so keeping it alive and unmodified from `samvada_init()` to `samvada_release()` is the consumer's contract; the shim satisfies it with a file-scope `static int64_t samvada_fn_table[]` |
| Consumer-supplied `major` / `minor` device numbers | validated at the C boundary | `devnum_ok()` in `deps/samvada_main.c` gates both `sb_take_device` and `sb_release_device` on `0 <= v <= UINT32_MAX` and returns `-EINVAL` otherwise — **before** the 64→32 narrowing cast, which previously truncated silently. Values that pass go to `TakeDevice` / `ReleaseDevice` as `uu`; logind enforces the ACLs. The check lives in the shim, so a future `PURE_CYRIUS` backend must re-supply it |
| `pid` for `GetSessionByPID` | not consumer-supplied | samvada resolves its own session: `samvada_init` calls `sys_getpid()` and passes that (`src/samvada.cyr`). No consumer-supplied pid reaches the bus, and no public fn accepts one |
| Bytes from the system dbus socket (**C-shim backend**) | validated by libsystemd | `sd_bus_message_read` enforces signature match before we see the data. `sb_get_session_path` adds its own bounds: it rejects `out_buf == 0` or `out_buf_len <= 0` before the `(size_t)` cast, rejects a `NULL` path from a successful read, and returns `-ENOBUFS` rather than truncating a path that does not fit |
| Bytes from the system dbus socket (**native backend**, default at 1.0) | validated by samvada itself | The native marshaller owns framing, alignment, endianness and bounds checking — libsystemd is not present to do it. **The bus is not trusted input.** Every wire-supplied length is checked against the bytes actually present via `dbus_unmarshal_set_limit`, which the 2026-09-09 pre-1.0 audit added after finding three lengths trusted unchecked (AUDIT-1..3). The framer yields a message only once `avail >= total` |
| **Authenticity of a reply** | **checked by samvada, and enforced by the bus** | samvada correlates a reply to its call on `REPLY_SERIAL`, and since 1.0.0 additionally requires that the reply's `DESTINATION` (field 6) equal the unique name the bus assigned it at `Hello`, and that the reply did not already sit in the receive buffer when the request was written (send-time watermark). Both **exceed** the synchronous `sd_bus_call` path they replace, which checked neither. The bus remains the primary defence and the stronger one: dbus-broker and reference dbus-daemon route a `METHOD_RETURN`/`ERROR` only to the peer that made the matching call, so a hostile peer's forged reply is dropped before arrival (verified live — see the audit's *Reply forgery* section). samvada does **not** check `SENDER` (field 7): the broker rewrites it to the sender's *unique* id, so the intuitive "require `SENDER == org.freedesktop.login1`" would reject every genuine reply, and pinning logind's unique id would break on a logind restart. An attacker interposed on the `AF_UNIX` socket can still substitute a reply that satisfies both checks — the descriptor itself cannot be conjured, but the session path, `active` flag and errno can be chosen |
| FDs arriving on a **reply** (`TakeDevice`, `Inhibit`) | received via `recvmsg` + `SCM_RIGHTS`, queued by the framer | Until 1.0.0 this path used plain `read()`, which makes the kernel **discard** the descriptor while still delivering the bytes — the reply parsed cleanly, `UNIX_FDS` still said 1, and the fd was gone (AUDIT-4). Fixed and pinned by `test_reply_fd_survives_the_call_path` |
| FDs received via `SCM_RIGHTS` | re-duplicated `CLOEXEC` before the message is unref'd | see the fd-passing design principle below |
| logind's `PauseDevice` / `ResumeDevice` signals | **not surfaced, by decision** | samvada does not deliver session signals in the 0.x line — ratified in [ADR-0004](docs/adr/0004-session-control-lifecycle-and-signal-visibility.md), not an unfixed gap. logind emits them only to the session *controller*, which samvada now is only while it holds a device. See "Known Limitations" for what a consumer must plan around |
| logind session control (`TakeControl`) | **acquired only while a device is held** | `TakeControl` runs logind's `session_prepare_vt()`: on a session with `vtnr >= 1` it sets `KDSKBMODE=K_OFF` and `KDSETMODE=KD_GRAPHICS`, i.e. **it disables the keyboard and blanks the console**. 0.5.1 took control at init and so muted the console before any device was requested; 0.6.0 scopes it to device ownership (ADR-0004). Treat `TakeControl` as a privileged, user-visible action, not bookkeeping |

## Design Principles

- **No filesystem I/O.** samvada does not read or write any
  files. The dbus socket is the only kernel-visible side effect.
- **No process creation.** No `execve`, no `fork`, no
  `system()`. CI's security-scan job enforces this over
  `src/` **and** `tests/` **and** `deps/` — the C shim is a
  released artifact and was previously scanned by nothing. The
  deny list matches Cyrius spawn helpers by symbol
  (`sys_execve`, `sys_execveat`, `sys_fork`, `sys_vfork`,
  `sys_clone`/`sys_clone3`, `sys_posix_spawn`), `SYS_*`
  syscall constants, raw numeric `syscall(...)` spawn numbers,
  and the C library spawn family (`system`, `popen`, `exec*`,
  `fork`, `vfork`, `posix_spawn`) — all under `grep -E`,
  ignoring comment lines. The pre-0.5.1 list could not fire:
  it grepped for `sys_system`, which does not exist in the
  Cyrius stdlib, and for two numeric literals the codebase
  never writes.
- **No writes to system paths.** `/etc/`, `/bin/`, `/sbin/` are
  CI-rejected as string literals — samvada has no business
  touching them. Same scan, same widened scope.
- **No raw pointer arithmetic in user code.** Cyrius's
  `load64` / `store64` over named offset constants (the
  offset-table-on-heap pattern) replaces struct types.
- **`fncall6` ceiling honored.** Every C wrapper takes ≤6 args
  so dispatch stays portable across x86_64 SysV + aarch64.
- **Fd-passing is duplicate-safe *and* `CLOEXEC`-safe.** The C
  shim re-duplicates every fd returned via `SCM_RIGHTS` before
  unref'ing the dbus message, so the consumer's fd lifetime is
  independent of dbus message lifetime. The duplicate is made
  with `fcntl(fd, F_DUPFD_CLOEXEC, 0)`, **not** `dup()`:
  `dup()` clears `FD_CLOEXEC` on the new descriptor, which
  meant the DRM-master fd survived any `execve` the consumer
  performed and leaked master rights into an unrelated child.
  A message that reads successfully but delivers `fd < 0` — a
  peer contract violation — returns an explicit `-EBADF`
  rather than a stale `errno`.
- **Errors are sd-bus negative errnos.** Pass-through from
  libsystemd; consumers branch on `< 0` and never inspect
  magnitudes beyond logging. Success is exactly `0`:
  `sd_bus_call_method` returns a *positive* value on success,
  so `sb_release_device` and the `TakeControl` /
  `ReleaseControl` wrappers normalise any non-negative return
  to `0` before it crosses the FFI boundary, and
  `samvada_session_release_device` normalises again on the
  Cyrius side so a `PURE_CYRIUS` backend is covered too.
- **The bus pump is bounded.** `sb_pump_signals` drains at most
  `SAMVADA_PUMP_MAX_EVENTS` (256) messages per call and returns
  the count. The pre-0.5.1 loop ran until the queue emptied, so
  any peer able to emit signals on the connection could hold a
  single call captive — measured at 0.77 s under an
  unprivileged local flood. The public
  `samvada_pump_signals()` signature is frozen and takes no
  arguments, so the cap lives in the C wrapper; a residual
  queue drains on the consumer's next tick.
- **Init fails closed.** `samvada_init` refuses re-entry
  without an intervening release (`-EBUSY`), and calls
  `samvada_release()` itself on every late failure — so a
  failure after the bus opened neither leaks the `sd_bus` nor
  leaves the public API armed against a half-built session. The
  subsequent retry is a clean init, not `-EBUSY`.
- **Nothing sensitive survives release.** `samvada_release()`
  zeroes both scratch buffers: `_samvada_outs` holds a raw
  `sd_bus *` that is dangling the instant `close_bus` runs, and
  `_samvada_sess` holds the resolved session object path.
- **Buffers stay small and reviewable.** Every `var buf[N]` is
  N *bytes*, and a `var` declaration of ≥64 KB fails the CI
  security scan outright — large scratch belongs on the heap
  via `alloc()`, where it can be reviewed as an allocation
  rather than as static data shared across calls.
- **The release supply chain is pinned.** The toolchain
  installer is fetched from a pinned commit SHA and
  checksum-verified before it executes, rather than piped into
  a shell from a mutable branch. Workflow permissions default
  to `contents: read`, with `contents: write` granted only to
  the job that publishes; every `actions/checkout` runs with
  `persist-credentials: false` so no job leaves a
  `GITHUB_TOKEN` on disk for a third-party script to read.

## Known Limitations

Named here because a security document that only lists what is
defended is misleading about what is wired.

- **`PauseDevice` / `ResumeDevice` are not delivered.** The C
  shim populates slot `+48` (`sb_subscribe_pause_resume`) and
  slot `+56` (`sb_unsubscribe`), but **no samvada code path
  dispatches either**, and no public fn installs a match rule —
  the exported surface in `dist/samvada.cyr` carries the two
  slot-offset constants and nothing that calls through them.
  `samvada_pump_signals()` therefore services the connection
  (it drives `sd_bus_process`) but can never run a pause/resume
  callback, because none was ever registered — the callback ABI
  is sound and has never executed. A consumer can reach slot `+48`
  itself through the generic `samvada_ffi_get_slot` accessor
  and dispatch it by hand; that is unsupported, undocumented
  and untested. Any documentation implying samvada delivers
  these signals is wrong. `PauseDeviceComplete` — the
  acknowledgement half of logind's pause handshake — is not
  wired either; a consumer that needs it must implement it
  outside samvada. Filed as MED-7 in the 2026-09-09 audit and **decided** in
  scheduled on the roadmap.
- **Live-bus end-to-end is unverified.** Behaviour against a
  real `dbus-broker` + `systemd-logind` on a **seated** session
  remains hardware-gated. `TakeControl` was reproduced live
  against `systemd-logind` (without it, `TakeDevice` fails
  `NotInControl`; with it, the call advances past the control
  check), but the remaining path then hits an
  environment-specific `AccessDenied` on a seatless session.
  The full seated path is untested. Treat every runtime claim
  about `TakeDevice` success as unproven until the M1 closeout
  gate clears.
- **samvada is single-threaded and has no locking.** Module
  scope state (`_samvada_table`, `_samvada_bus`,
  `_samvada_sess`, `_samvada_outs`) is unguarded, and the
  scratch buffers are shared across calls. **Consumers must
  serialize all samvada calls.** Concurrent
  `samvada_session_take_device()` from two threads races on the
  same 16-byte out-scratch and can hand one thread the other's
  fd. This contract previously lived only inside
  `docs/audit/2026-05-01-hardening-review.md`; it is normative.
- **The pump cap bounds a call, not a peer.** 256 events per
  call stops one call being held captive; it does not
  rate-limit a peer that sustains a flood, which still consumes
  the pump budget on every tick. Rate-limiting is a v1.0 design
  item.
- **Input validation lives in the shim, not the core.** The
  `major` / `minor` range check is in `deps/samvada_main.c`.
  The Cyrius side passes both through unexamined, so a backend
  that is not the libsystemd shim must supply the check itself.

## Audit History

| Date       | Pass                                   | Target  | Report |
|------------|----------------------------------------|---------|--------|
| 2026-09-09 | P(-1) hardening review + security audit | 0.5.1  | [`docs/audit/2026-09-09-audit.md`](docs/audit/2026-09-09-audit.md) |
| 2026-05-01 | Hardening review                       | 0.2.2   | [`docs/audit/2026-05-01-hardening-review.md`](docs/audit/2026-05-01-hardening-review.md) |

The project uses two tiers deliberately. A **hardening review**
is an internal per-file walk of the current surface plus its
gates; the **formal pre-v1.0 audit** is the one that commits
samvada to a stable disclosure posture over a surface that has
stopped moving. Earlier revisions of this section said samvada
was pre-audit with the first pass gated to v1.0. That is
**superseded**: the 2026-09-09 pass was a full P(-1) hardening
review *and* security audit of the shipped surface, and it
found defects severe enough that deferring was no longer
defensible.

**What the 2026-09-09 pass covered.** A per-file walk of
`src/*.cyr`, `deps/samvada_main.c`, the test suite, both CI
workflows, and every doc making a security or behavioural
claim — plus live probing against a running `systemd-logind`,
which is the method change that mattered: prior reviews
reasoned about the protocol from this repo's own documentation,
this one issued the calls.

It found two CRITICAL defects — samvada never sent
`TakeControl`, so `TakeDevice` could not succeed anywhere; and
the shim owned `main()`, so it could not link into any real
consumer. Both are graded CRITICAL for functional impact on the
shipped surface, not for exploitability; the ladder under
"Response Timeline" grades *reported vulnerabilities* and is a
separate scale. Alongside them sat MEDIUM and LOW findings
spanning the `CLOEXEC` leak on the delegated DRM-master fd, an
unvalidated 64→32 narrowing of `major` / `minor`, a success
return that contradicted its own documented contract in four
places, an unbounded bus pump, a backend-`kind` check that
accepted any non-zero word, a leaking init failure path, and
several supply-chain and gate weaknesses in CI — a security
scan whose deny list could not fire, a slot-offset pin that
never checked the C side, a link test that never reproduced
the consumer shape, an installer piped from a mutable branch.

Every repair carries a test, and each was mutation-proven:
reverting the fix fails the suite. The suite grew from 38 to
114 asserts, largely via a pure-Cyrius mock backend in
`tests/samvada.tcyr` that gives `take_device` /
`release_device` / `pump_signals` real behavioural coverage
with no hardware — retiring the standing claim that those
paths could only be tested on hardware.

Admitted findings: 2 CRITICAL, 10 MEDIUM, 16 LOW and 9
INFO/deferred — 37 in total, with a further 6 candidate
findings refuted before admission and recorded as such. All 28
CRITICAL/MEDIUM/LOW findings are fixed in 0.5.1; the deferred
set is routed to the roadmap.

**What it did not cover.** Four things, and they are the four
that matter:

- **Live-bus behavioural validation.** `TakeControl`'s
  *necessity* was proven live, but no end-to-end `TakeDevice`
  has ever succeeded — that needs an active **seated** session
  holding a DRM device, which the probing host did not have.
  This is the M1 gate.
- **`PauseDevice` / `ResumeDevice` delivery**, and
  `PauseDeviceComplete`. Not wired, by decision (MED-7 →
  ADR-0004; a VT seat only ever receives `PauseDevice("force")`,
  which requires no reply, and nothing in logind waits on the
  controller — verified against systemd v261 source). The
  Cyrius-fn-pointer-as-`sd_bus_message_handler_t` callback ABI
  was reviewed and is sound, but it is unreachable, so it has
  never actually executed.
- **Thread safety.** Not analysed; the surface is
  single-threaded by design and the serialization contract is
  stated under "Known Limitations".
- **The native dbus marshaller.** This pass audits the
  **C-shim surface**, which v1.0 retires. Replacing it with
  hand-rolled byte parsing (Path A in `roadmap.md` §M3)
  substantially *enlarges* the audit surface — samvada would
  be parsing untrusted wire bytes itself rather than
  delegating to libsystemd's validated parser — and **must get
  its own audit before v1.0 tags**. The removal path (Path B)
  needs no samvada audit because the surface goes away.

Every PR additionally runs the CI security scan described under
"Design Principles", the fmt + lint + vet drift gates, the
C-shim strict-flag compile (`-Wall -Wextra -Werror -Wshadow
-Wconversion -Wsign-conversion -Wcast-qual -Wformat=2`, in both
library and standalone modes), the consumer-shape link test,
the C-versus-Cyrius slot-offset cross-check (which also asserts
`kind` is still at `+64`, per ADR-0002), and a
`samvada_version()`-versus-`VERSION` equality gate.

## Disclosure

We follow coordinated disclosure. Once a fix is released, we
will publish a security advisory crediting the reporter (unless
anonymity is requested). Audit findings that surface internally
are disclosed through `docs/audit/*.md` and the corresponding
CHANGELOG entry.
