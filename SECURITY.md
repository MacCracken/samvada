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
| 1.1.x   | **Yes** — current release line, receives security fixes; take 1.1.2 or later (P1-1, HIGH) |
| 1.0.x   | No — superseded by 1.1.x; the 1.1.2 fixes are not backported         |
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
the correct remedy for anything found there is to move to the
current line.

**Every release from 0.11.0 (the native cutover) through 1.1.1 lets
any local user choose the descriptor `samvada_session_take_device()`
returns** on the native backend (P1-1,
`docs/audit/2026-10-29-p1-audit.md`; before 1.0.1 any signal's
descriptor would do, since then one on a message samvada cannot
parse). The C-shim backend is not affected. mabda pins `tag = "1.0.1"`; its logind path
(`-D MABDA_LOGIND`) is off by default.

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
| Consumer-supplied `major` / `minor` device numbers | validated at the C boundary | `devnum_ok()` in `deps/samvada_main.c` gates both `sb_take_device` and `sb_release_device` on `0 <= v <= UINT32_MAX` and returns `-EINVAL` otherwise — **before** the 64→32 narrowing cast, which previously truncated silently. Values that pass go to `TakeDevice` / `ReleaseDevice` as `uu`; logind enforces the ACLs. The native backend re-supplies the same check (`dbus_session_devnum_ok`, since 1.0.1), and since 1.1.2 the public layer checks first, before `TakeControl` — which mutes a seated VT — is sent |
| `pid` for `GetSessionByPID` | not consumer-supplied | samvada resolves its own session: `samvada_init` calls `sys_getpid()` and passes that (`src/samvada.cyr`). No consumer-supplied pid reaches the bus, and no public fn accepts one |
| Bytes from the system dbus socket (**C-shim backend**) | validated by libsystemd | `sd_bus_message_read` enforces signature match before we see the data. `sb_get_session_path` adds its own bounds: it rejects `out_buf == 0` or `out_buf_len <= 0` before the `(size_t)` cast, rejects a `NULL` path from a successful read, and returns `-ENOBUFS` rather than truncating a path that does not fit |
| Bytes from the system dbus socket (**native backend**, default at 1.0) | validated by samvada itself | The native marshaller owns framing, alignment, endianness and bounds checking — libsystemd is not present to do it. **The bus is not trusted input.** Every wire-supplied length is checked against the bytes actually present via `dbus_unmarshal_set_limit`, which the 2026-09-09 pre-1.0 audit added after finding three lengths trusted unchecked (AUDIT-1..3). The framer yields a message only once `avail >= total` |
| **Authenticity of a reply** | **checked by samvada, and enforced by the bus** | samvada correlates a reply to its call on `REPLY_SERIAL`, and since 1.0.0 additionally requires that the reply's `DESTINATION` (field 6) equal the unique name the bus assigned it at `Hello`, and that the reply did not already sit in the receive buffer when the request was written (send-time watermark). Both **exceed** the synchronous `sd_bus_call` path they replace, which checked neither. The bus remains the primary defence and the stronger one: dbus-broker and reference dbus-daemon route a `METHOD_RETURN`/`ERROR` only to the peer that made the matching call, so a hostile peer's forged reply is dropped before arrival (verified live — see the audit's *Reply forgery* section). samvada does **not** check `SENDER` (field 7): the broker rewrites it to the sender's *unique* id, so the intuitive "require `SENDER == org.freedesktop.login1`" would reject every genuine reply, and pinning logind's unique id would break on a logind restart. An attacker interposed on the `AF_UNIX` socket can still substitute a reply that satisfies both checks — the descriptor itself cannot be conjured, but the session path, `active` flag and errno can be chosen |
| FDs arriving on a **reply** (`TakeDevice`, `Inhibit`) | received via `recvmsg` + `SCM_RIGHTS`, **attributed by stream position** (1.1.2) | Every read is bounded to the message at the head of the buffer, so a message's descriptors are exactly those that arrived with its bytes; consuming it closes every one it did not hand out. `TakeDevice` hands out the one its reply's `h` names, and only from a `(hb)` reply whose `UNIX_FDS` matches what arrived. Until 1.1.2 attribution counted each message's own `UNIX_FDS`, and a message samvada could not parse counted as none — so any local user could plant the descriptor the next `TakeDevice` returned (P1-1). Until 1.0.0 this path used plain `read()`, which makes the kernel **discard** the descriptor (AUDIT-4) |
| **Liveness of the bus** | **bounded, not trusted** | Since 1.0.0 samvada refuses to wait indefinitely on a peer. `SO_RCVTIMEO` is armed at connect (25 s, matching `DBUS_DEFAULT_TIMEOUT`, covering the SASL handshake), and every call carries a `CLOCK_MONOTONIC` deadline so a peer dribbling bytes cannot extend it — `SO_RCVTIMEO` alone bounds a receive, not a call. Both surface as `-ETIMEDOUT`. Since 1.1.2 the deadline is a call's **only** bound, and it bounds the **send** as well as the receive: every wait is a poll for the time left — for readability, or for writability when the send buffer is full — and every send and receive after one is non-blocking, so a socket that polls readable with nothing to read cannot hold the call either. No count of messages can end a call early (a 64-read budget let 64 one-descriptor signals fail any call, P1-3); `EINTR` is retried, not returned (P1-5); a clock that fails mid-call ends it; the SASL handshake has the same deadline. `SO_RCVTIMEO` and `SO_SNDTIMEO` (25 s) are the backstop when the clock has failed. A request cut off part-way latches the connection closed: half a message cannot be taken back. Before this samvada had no time-based bound of any kind and a bus that accepted a request then went silent froze the caller — which, in a compositor, is indefinite |
| **Loss of the connection** | **a state, reported as sd-bus reports it** (1.1.1) | The call that sees the drop — EOF, a stream cut mid-message, or a disconnect errno on send or receive — returns `-ECONNRESET`; every later call `-ENOTCONN` without touching the socket; `samvada_pump_signals()` `-ECONNRESET`. Until 1.1.1 the pump returned 0 events forever after the bus went away, so a consumer could not tell a quiet bus from a gone one |
| Error names in an `ERROR` reply | **mapped exactly as sd-bus maps them** (1.1.1) | `dbus_session_map_error` reproduces libsystemd's table for `org.freedesktop.DBus.Error.*`, `org.freedesktop.login1.*` and `System.Error.<ERRNO>`; `tests/fixtures/sdbus_errno_map.tsv` is libsystemd's own output, re-checked against the runner's libsystemd in CI. Until 1.1.1 it held eight names, two with the wrong errno |
| FDs received via `SCM_RIGHTS` | re-duplicated `CLOEXEC` before the message is unref'd (shim); received `MSG_CMSG_CLOEXEC` (native); **every consumed message closes its own** | see the fd-passing design principle below. Since 1.1.2 "its own" is decided by stream position, not by parsing the message (P1-1); the receive reserves room for 253 descriptors, so no message truncates the control data and closes the bus (P1-2); no received descriptor is left on 0..2; and `take_device` hands out a descriptor only if the kernel says it **is** the device asked for — a character device whose rdev is `(major, minor)` (`statx`) |
| logind's `PauseDevice` / `ResumeDevice` signals | **not surfaced, by decision** | samvada does not deliver session signals in the 0.x line — ratified in [ADR-0004](docs/adr/0004-session-control-lifecycle-and-signal-visibility.md), not an unfixed gap. logind emits them only to the session *controller*, which samvada now is only while it holds a device. See "Known Limitations" for what a consumer must plan around |
| logind session control (`TakeControl`) | **acquired only while a device is held** | `TakeControl` runs logind's `session_prepare_vt()`: on a session with `vtnr >= 1` it sets `KDSKBMODE=K_OFF` and `KDSETMODE=KD_GRAPHICS`, i.e. **it disables the keyboard and blanks the console**. 0.5.1 took control at init and so muted the console before any device was requested; 0.6.0 scopes it to device ownership (ADR-0004). Treat `TakeControl` as a privileged, user-visible action, not bookkeeping |

## Design Principles

- **No filesystem I/O.** samvada does not read or write any
  files. The dbus socket is the only kernel-visible side effect.
- **No process creation.** No `execve`, no `fork`, no
  `system()`. CI's security-scan job enforces this over
  `src/` **and** `tests/` **and** `deps/` with
  `tools/security_scan.py` (1.1.2), which reads code through a
  comment- and string-aware lexer: Cyrius spawn helpers by symbol,
  called or not (`sys_execve`, `&sys_execve`, …), `SYS_*` and
  `__NR_*` spawn constants in either case, raw `syscall(...)`
  spawn numbers in decimal or hex on both arches, and the C
  library spawn family (`system`, `popen`, every `exec*`, `fork`,
  `vfork`, `clone`, `posix_spawn`/`posix_spawnp`). Its syscall
  allow-list is positive, executable memory (`mmap` / `mprotect` with
  `PROT_EXEC`) is refused, `cyrius.cyml`'s stdlib list is frozen
  (`tools/stdlib_allowed.txt`, with process-spawning modules refused by
  name), and the compiled objects are judged too: the bundle's must
  leave nothing undefined, the shim's only allow-listed symbols and no
  syscall instruction. Every bypass the 1.1.2 audit demonstrated is a
  positive control in a `--self-test` or a CI step, which must fail for
  its own reason. The pre-0.5.1 list could not fire at all.
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
  with `fcntl(fd, F_DUPFD_CLOEXEC, 3)` — never onto 0..2, since 1.1.2 — **not** `dup()`:
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
  the count; the native pump likewise, and at most 1024 receives. The pre-0.5.1 loop ran until the queue emptied, so
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
  N *bytes*, and a `var` declaration of ≥64 KB — decimal or hex —
  fails the CI security scan outright — large scratch belongs on the heap
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

### Added by the 1.1.2 P(-1) pass

- **samvada's device table is a copy of logind's, and nothing re-syncs
  it.** `PauseDevice` / `ResumeDevice` are not delivered (ADR-0004), and
  logind drops devices on its own — on hot-unplug, or when another
  controller forces a takeover. samvada heals at the next call: a release
  forgets the device whatever logind answers, and a take of a device
  logind still holds fails once (`DeviceIsTaken`) and releases it. Until
  then the two views can differ.
- **`MSG_CTRUNC` is unproven on aarch64.** The refusal of a truncated
  descriptor set rests on the kernel reporting it, and under
  `qemu-aarch64` user mode the flag never reaches the guest (five asserts
  fail there, all on this one cause; roadmap). With 253 descriptors'
  room reserved, truncation needs another cmsg sharing the buffer, which
  samvada never enables — and `take_device`'s `statx` check stands
  either way.

### Added by the 1.0.1 A-lane audit

These are stated rather than fixed, and each says what the real
defence is so a deployment can judge whether it holds.

- ~~**`release_device`'s control accounting is a bare counter.**~~ —
  closed in 1.1.2 (P1-8). The defence this entry relied on was
  logind's, and it did not hold: a take that failed on samvada's side
  after logind granted it left logind holding a device samvada never
  counted, so releasing it succeeded and brought the count to zero —
  and `ReleaseControl` then revoked every device, the live one
  included. samvada now holds devices by `(major, minor)` and drops
  control only when its own table is empty.
- **The consumer fetch path is authenticated only by the lock — and,
  on a first resolve, by `SHA256SUMS` from the same host.** A
  `[deps.samvada]` consumer fetches `dist/samvada.cyr` from a git
  clone of the release **tag**. samvada's tags are lightweight and
  unsigned. With a `cyrius.lock` present, a repointed tag is refused
  (commit-pin mismatch — verified). On a **first** resolve, or a
  version bump, the lock has nothing to compare; since 1.1.1 the
  release job refuses to publish unless the release's
  `samvada-<tag>.cyr` and `.deps` are byte-identical to the tag's
  `dist/`, so a consumer can check against `SHA256SUMS`
  (`docs/guides/consumer-link.md` § Checking a first resolve). Both are
  served by GitHub, so that is a second object, not a second
  authority, and the repository's releases are not marked immutable.
  **Signed tags are the fix**, and need the maintainer's key
  (`CONTRIBUTING.md` § Releasing). Consumers should commit
  `cyrius.lock` and review the commit a version bump moves to.
- **The bundle's private-by-default boundary is Cyrius-level only**
  (1.1.1). A consumer compiling `dist/samvada.cyr` can name only the
  eight `public fn`s — every other fn is a compile error, which CI
  proves for every one of them (`tools/boundary_check.py`). But an `object;`
  build still exports every fn as a GLOBAL DEFAULT symbol (measured on
  cyrius 6.7.18, contrary to the cyrius guide), so **C code** linked
  against that object — the libsystemd-shim path — can still declare
  an internal `extern` and call it, `dbus_session_set_timeout_ms`
  included. Through 1.1.0 every internal was reachable from Cyrius too.
- **The unmarshaller is little-endian only** while the framer is
  endian-aware. A big-endian peer's reply is framed, then every field
  read returns malformed and the call times out at `-ETIMEDOUT`. It
  fails **safe** — no out-of-bounds read, and since 1.1.2 no
  descriptor left behind — but samvada cannot interoperate with a BE
  peer. Both target arches are little-endian. **Until 1.1.2 this entry
  was wrong**: a big-endian message's descriptors stayed queued for the
  next `TakeDevice`, which any local user could exploit (P1-1).
- ~~**Two invariants are enforced by review, not mechanically**~~ —
  closed: `tools/surface_check.py` has gated "no FFI types in public
  signatures" and the public set since 1.0.1, and
  `tools/boundary_check.py` gates reachability since 1.1.1.


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
  is sound and has never executed. Since 1.1.1 a Cyrius consumer
  cannot reach slot `+48` at all (`samvada_ffi_get_slot` is private
  to the bundle); only C code linked against an `object;` build can
  call into it, which is unsupported, undocumented and untested. Any documentation implying samvada delivers
  these signals is wrong. `PauseDeviceComplete` — the
  acknowledgement half of logind's pause handshake — is not
  wired either; a consumer that needs it must implement it
  outside samvada. Filed as MED-7 in the 2026-09-09 audit and
  **decided** in ADR-0004: not delivered, by design.
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
- **Input validation is in the core and in each backend.** Since
  1.1.2 the public layer range-checks `major` / `minor` before
  `TakeControl` or any dispatch (`_samvada_devnum_ok`, take and
  release), and both backends check again (`devnum_ok` in
  `deps/samvada_main.c`, `dbus_session_devnum_ok` natively). A future
  backend called only through the public layer inherits the check.

## Audit History

| Date       | Pass                                   | Target  | Report |
|------------|----------------------------------------|---------|--------|
| 2026-10-29 | P(-1) pass — wire path, session layer, CI gates, CVE review; adversarially re-verified | 1.1.2 | [`docs/audit/2026-10-29-p1-audit.md`](docs/audit/2026-10-29-p1-audit.md), and [`CHANGELOG.md`](CHANGELOG.md) 1.1.2 |
| 2026-10-29 | A-lane close-out — A-1, A-2, A-4, A-6 | 1.1.1 | [`docs/audit/2026-10-29-audit.md`](docs/audit/2026-10-29-audit.md), and [`CHANGELOG.md`](CHANGELOG.md) 1.1.1 |
| 2026-09-10 | A-lane completion — the dimensions 1.0.0 did not cover | 1.0.1 | [`docs/audit/2026-09-09-native-audit.md`](docs/audit/2026-09-09-native-audit.md) §A lane, and [`CHANGELOG.md`](CHANGELOG.md) |
| 2026-09-09 | Pre-1.0 audit of the native marshaller + reply forgery | 1.0.0 | [`docs/audit/2026-09-09-native-audit.md`](docs/audit/2026-09-09-native-audit.md) |
| 2026-09-09 | P(-1) hardening review + security audit | 0.5.1  | [`docs/audit/2026-09-09-audit.md`](docs/audit/2026-09-09-audit.md) |
| 2026-05-01 | Hardening review                       | 0.2.2   | [`docs/audit/2026-05-01-hardening-review.md`](docs/audit/2026-05-01-hardening-review.md) |

**The 1.0 line's two passes, and what they did not cover.** The
1.0.0 audit planned seven dimensions and covered four, finding
four defects (AUDIT-1..4) — including one that meant `take_device`
could never return a working descriptor at all — and closing the
reply-forgery question. The remaining dimensions became the
roadmap's *A lane*, and 1.0.1 worked through them, fixing ten more
defects. Between them: fourteen defects, two of which meant
samvada handed a consumer the wrong descriptor or the wrong
device, silently, and returned success.

**1.1.1 closed the A lane, all but signed tags.** Resource
exhaustion, build and supply chain, the protocol state machine and
the public API contract each closed with a test, a gate or a
measurement, and the pass found real defects in three of them
(`docs/audit/2026-10-29-audit.md`). samvada still does **not** claim
to be audited-hardened: release tags are unsigned until the
maintainer signs them, and a `TakeDevice` that transfers a real
descriptor on a seated session has never run (the CG lane). Both are
listed in [`docs/development/roadmap.md`](docs/development/roadmap.md)
rather than omitted, and the C shim is retained as a fallback.

**1.1.2's P(-1) pass found a HIGH the earlier passes missed**: any
local user could choose the descriptor `TakeDevice` returned, through a
directed signal samvada could not parse (P1-1, the bug class of
CVE-2022-42012). Three earlier passes had looked at this queue; each
closed the case in front of it — a signal's descriptor, an ERROR
reply's — by counting from the message, and none asked what happens
when the message cannot be counted. Attribution is by stream position
now, which asks nothing of the message.

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
