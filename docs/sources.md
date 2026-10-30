# Sources

Stable citations for every protocol byte, interface name, errno
magnitude, and kernel-visible behavior samvada relies on.

This page exists so a reviewer of either backend — the native
Cyrius one (`src/dbus_*.cyr`) or the C shim — or of a consumer can
verify each load-bearing claim without having to retrace the
footnotes scattered across `dbus-marshalling.md`, `samvada_main.c`,
the `src/dbus_*.cyr` comments, and the test pins. New citations
land here as numbered items (`[S-N]`) — never renumbered.

## D-Bus protocol

- **[S-1] D-Bus Specification** —
  <https://dbus.freedesktop.org/doc/dbus-specification.html>.
  Canonical wire format. Specifically referenced sections:
  - §"Type system" — `y u b o s g h ...` codes used in
    samvada's signatures (`u`, `b`, `uu`, `o`, `hb`, `s`, `uus`,
    `uuh`).
  - §"Message format" — header layout, header field codes 1
    (PATH), 2 (INTERFACE), 3 (MEMBER), 4 (ERROR_NAME), 5
    (REPLY_SERIAL), 6 (DESTINATION), 7 (SENDER), 8 (SIGNATURE),
    9 (UNIX_FDS).
  - §"Sending Unix File Descriptors" — `h` is an index into
    the `SCM_RIGHTS` aux array, not a raw fd value.
  - §"Message format" — the 2^27-byte (128 MiB) maximum message
    length (`dbus_frame_msg_max`), and the type each defined header
    field must carry (`_dbu_field_type` in `src/dbus_unmarshal.cyr`).
  - §"Valid Object Paths" — the grammar `dbus_unmarshal_path_ok`
    enforces on a session path and the `GetSessionByPID` reply.
  - §"AUTH commands" — `AUTH EXTERNAL` / `DATA` /
    `NEGOTIATE_UNIX_FD` / `BEGIN` sequence used on system-bus connect
    (one pipelined write, with an empty-credential `DATA` step —
    `tests/fixtures/dbus/01-sasl-client.bin`).

## logind interface

- **[S-2] systemd `org.freedesktop.login1`** —
  <https://www.freedesktop.org/software/systemd/man/org.freedesktop.login1.html>.
  Source of every interface / member name in
  `samvada_main.c`:
  - `org.freedesktop.login1` (well-known dest, in `LOGIND_DEST`).
  - `/org/freedesktop/login1` (manager object, in `LOGIND_MGR`).
  - `org.freedesktop.login1.Manager.GetSessionByPID(u) → o`
    (in `LOGIND_IF_M`).
  - `org.freedesktop.login1.Session.TakeDevice(uu) → hb`
    (in `LOGIND_IF_S`).
  - `org.freedesktop.login1.Session.ReleaseDevice(uu)` (no
    return).
  - `org.freedesktop.login1.Session.TakeControl(b)` and
    `ReleaseControl()` (no return; sent since 0.5.1, around device
    ownership since 0.6.0 — ADR-0004).
  - `org.freedesktop.login1.Session.PauseDevice(uus)` (signal,
    receive-only).
  - `org.freedesktop.login1.Session.ResumeDevice(uuh)` (signal,
    receive-only).

  The "inactive" semantics of TakeDevice's `b` return — `0`
  for active, `1` for paused — is documented inline on this
  page.

## libsystemd sd-bus

- **[S-3] `sd_bus_default_system(3)`** —
  <https://www.freedesktop.org/software/systemd/man/sd_bus_default_system.html>.
  Documents `sd_bus_open_system`, which backs `sb_open_system_bus` since
  1.1.2 (a private connection; through 1.1.1 it was the thread's shared
  `sd_bus_default_system`, documented on the same page).
- **[S-4] `sd_bus_call_method(3)`** —
  <https://www.freedesktop.org/software/systemd/man/sd_bus_call_method.html>.
  Backs `sb_get_session_path`, `sb_take_device`,
  `sb_release_device`, `sb_take_control` and `sb_release_control`.
  Return-value contract: non-negative on success (`1` in practice,
  verified live — `dbus-marshalling.md` §Verification against
  logind), negative errno on failure; the shim normalises success
  to `0`.
- **[S-5] `sd_bus_message_read(3)`** —
  <https://www.freedesktop.org/software/systemd/man/sd_bus_message_read.html>.
  Reads `o` / `hb` reply bodies. fd ownership note (cited in
  `samvada_main.c`'s `dup` rationale): "the file descriptor
  is owned by the message object" — message unref closes the
  fd.
- **[S-6] `sd_bus_match_signal(3)`** —
  <https://www.freedesktop.org/software/systemd/man/sd_bus_match_signal.html>.
  Backs `sb_subscribe_pause_resume`.
- **[S-7] `sd_bus_process(3)`** —
  <https://www.freedesktop.org/software/systemd/man/sd_bus_process.html>.
  Backs `sb_pump_signals`. Return `> 0` = events processed,
  `0` = empty queue, `< 0` = errno.

## fd-passing primitives

- **[S-8] `unix(7)`** — `man 7 unix` /
  <https://man7.org/linux/man-pages/man7/unix.7.html>.
  `SCM_RIGHTS` definition; ancillary-data semantics on
  `AF_UNIX` sockets.
- **[S-9] `recvmsg(2)` + `cmsg(3)`** —
  <https://man7.org/linux/man-pages/man3/cmsg.3.html>.
  `CMSG_FIRSTHDR` / `CMSG_NXTHDR` / `CMSG_DATA` walk used by
  the native reader (`dbus_sys_parse_rights` in `src/dbus_sys.cyr`,
  which walks every cmsg since 1.1.2).
- **[S-10] `fcntl(2)` `F_DUPFD_CLOEXEC`** (it replaced `dup(2)`) —
  <https://man7.org/linux/man-pages/man2/fcntl.2.html>.
  Backs the dup-safety contract in `sb_take_device`:
  `fcntl(fd, F_DUPFD_CLOEXEC, 3)`, so the returned fd outlives the
  dbus message that delivered it, keeps `FD_CLOEXEC` (which `dup(2)`
  clears; 0.5.1 MED-3) and never lands on 0..2 (1.1.2). The native
  backend uses the same call to move a received descriptor off 0..2
  (`dbus_socket_above_stdio`).

## Kernel constants

- **[S-11] DRM major number = `226`** —
  Linux source `drivers/gpu/drm/drm_drv.c`
  (`DRM_MAJOR = 226`). Used in mabda's
  `gpu_surface_configure_native_logind` when calling
  `samvada_session_take_device(226, N)`. Not hard-coded in
  samvada itself — samvada is device-agnostic; the consumer
  picks the major/minor.
- **[S-12] errno values** — `errno.h` /
  `asm-generic/errno-base.h` /
  `asm-generic/errno.h`. Magnitudes referenced in
  `public-api.md`'s error-code catalog: `ENOENT=2`, `ENXIO=6`,
  `EBADF=9`, `ENOMEM=12`, `EACCES=13`, `EBUSY=16`, `EINVAL=22`,
  `ENFILE=23`, `EMFILE=24`, `ENOSYS=38`, `EPROTO=71`,
  `ECONNRESET=104`, `ENOBUFS=105`, `ENOTCONN=107`, `ETIMEDOUT=110`,
  `EHOSTUNREACH=113`. samvada returns these as negative values per
  sd-bus convention.

## Cyrius platform

- **[S-13] cyrius `lib/fnptr.cyr`** — the `fncall1` …
  `fncall6` dispatch primitives. samvada keeps every C wrapper
  at `<= 6` args specifically so dispatch stays inside this
  contract on both x86_64 SysV and aarch64.
- **[S-14] cyrius `lib/syscalls_x86_64_linux.cyr`** (with
  `lib/syscalls_linux_common.cyr`, which holds the `sys_*`
  wrappers) — Linux syscall numbers. Through the stdlib's wrappers
  samvada uses `socket`, `connect`, `setsockopt`, `sendto`,
  `recvmsg`, `fcntl`, `statx`, `close`, `getpid` and `poll` (via
  `fd_wait_ready`), plus `clock_gettime` as the literal x86 number
  `228` on every arch (`dbus_session_now_ms`); `SYS_EXIT (60)` only
  in the `src/main.cyr` / `src/test.cyr` entry points.
- **[S-15] cyrius stdlib `alloc.cyr`** — bump-allocator
  contract. samvada's scratch buffers (`_samvada_sess`,
  `_samvada_outs`) live for process lifetime; this is the
  documented stdlib behavior, not a samvada-specific choice.

## Native backend — kernel interfaces

- **[S-17] `SCM_MAX_FD = 253`** — Linux `include/net/scm.h`: the
  most descriptors one `sendmsg` carries. Because `net/unix/af_unix.c`
  `unix_stream_read_generic` stops after the first segment that
  carried descriptors, it is also the most one `recvmsg` delivers.
  Sizes `dbus_sys_fd_max()` and the 1032-byte
  `CMSG_SPACE(253 * 4)` control buffer (`src/dbus_sys.cyr`).
- **[S-18] `recvmsg(2)` flags and cmsg types** — `MSG_CMSG_CLOEXEC`
  (`0x40000000`), `MSG_DONTWAIT` (`64`), `MSG_CTRUNC` (`8`), and
  `SCM_PIDFD` (`4`, Linux 6.5; closed on receipt) —
  `src/dbus_sys.cyr`.
- **[S-19] `send(2)` `MSG_NOSIGNAL`** (`16384`) and `socket(7)`
  `SO_RCVTIMEO` / `SO_SNDTIMEO` (`20` / `21`) — the send that cannot
  raise `SIGPIPE`, and the 25 s backstops armed at connect
  (`src/dbus_socket.cyr`).
- **[S-20] `clock_gettime(2)` `CLOCK_MONOTONIC`** (`1`) and `poll(2)`
  (the stdlib's `fd_wait_ready`) — the call and handshake deadline
  (`dbus_session_now_ms`, `dbus_session_call`, `dbus_session_sasl`).
- **[S-21] `statx(2)`** — `include/uapi/linux/stat.h`: one layout on
  every arch (`stx_mode` @28, `stx_rdev_major` @128, `stx_rdev_minor`
  @132); `AT_EMPTY_PATH` (`4096`), `STATX_TYPE` (`1`). Backs
  `dbus_session_fd_is_device`, the 1.1.2 check that a `TakeDevice`
  descriptor is the device asked for.

## Compiler / toolchain

- **[S-16] `cc -Wall -Wextra -Werror`** — the C-shim build
  flags samvada's CI requires (`.github/workflows/ci.yml`; the
  c-shim job's primary compile-check adds `-Wshadow -Wconversion
  -Wsign-conversion -Wcast-qual -Wformat=2`, in library and
  `-DSAMVADA_STANDALONE_MAIN` mode). CI compiles it only against the
  runner's libsystemd (`ubuntu-26.04`); no older libsystemd is
  CI-tested (`docs/guides/consumer-link.md` recommends 245+).

## Project-internal cross-references

- [`docs/architecture/public-api.md`](architecture/public-api.md)
  — the surface map.
- [`docs/architecture/dbus-marshalling.md`](architecture/dbus-marshalling.md)
  — wire-format walk-through; cites [S-1] inline.
- [`docs/adr/0001-c-shim-then-pivot.md`](adr/0001-c-shim-then-pivot.md)
  — why the C shim shipped first; its retirement, deferred from
  v1.0, is scheduled for v1.2.0
  ([`roadmap.md`](development/roadmap.md)).
- [`docs/adr/0002-append-after-kind-ffi-invariant.md`](adr/0002-append-after-kind-ffi-invariant.md)
  — why the FFI table layout is frozen.
- [`docs/adr/0003-native-cyrius-dbus.md`](adr/0003-native-cyrius-dbus.md)
  — why the native Cyrius backend (`src/dbus_*.cyr`) is the path.
- [`docs/adr/0004-session-control-lifecycle-and-signal-visibility.md`](adr/0004-session-control-lifecycle-and-signal-visibility.md)
  — when session control is taken, and why signals are not surfaced
  (cites systemd v261 source).
- [`tests/fixtures/dbus/MANIFEST.md`](../tests/fixtures/dbus/MANIFEST.md)
  — the captured wire bytes, with provenance.
- `tests/fixtures/sdbus_errno_map.tsv` — sd-bus's error-name → errno
  table, produced from libsystemd by `tools/sdbus_errno_probe.c`; the
  native map's ground truth, re-checked in CI.
- [`SECURITY.md`](../SECURITY.md) — threat model.
