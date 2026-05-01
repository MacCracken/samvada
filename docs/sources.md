# Sources

Stable citations for every protocol byte, interface name, errno
magnitude, and kernel-visible behavior samvada relies on.

This page exists so a v1.0 pure-Cyrius marshaller (or a
consumer reviewer) can verify each load-bearing claim without
having to retrace the footnotes scattered across
`dbus-marshalling.md`, `samvada_main.c`, and the v0.x test
pins. New citations land here as numbered items
(`[S-N]`) — never renumbered.

## D-Bus protocol

- **[S-1] D-Bus Specification** —
  <https://dbus.freedesktop.org/doc/dbus-specification.html>.
  Canonical wire format. Specifically referenced sections:
  - §"Type system" — `y u b o s g h ...` codes used in
    samvada's signatures (`u`, `uu`, `o`, `hb`, `uus`, `uuh`).
  - §"Message format" — header layout, header field codes 1
    (PATH), 2 (INTERFACE), 3 (MEMBER), 4 (REPLY_SERIAL), 5
    (ERROR_NAME), 6 (DESTINATION), 7 (SENDER), 8 (SIGNATURE),
    9 (UNIX_FDS).
  - §"Sending Unix File Descriptors" — `h` is an index into
    the `SCM_RIGHTS` aux array, not a raw fd value.
  - §"AUTH commands" — `EXTERNAL` / `NEGOTIATE_UNIX_FD` /
    `BEGIN` sequence used on system-bus connect.

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
  Backs `sb_open_system_bus`.
- **[S-4] `sd_bus_call_method(3)`** —
  <https://www.freedesktop.org/software/systemd/man/sd_bus_call_method.html>.
  Backs `sb_get_session_path`, `sb_take_device`,
  `sb_release_device`. Return-value contract: `0` on success
  with reply, negative errno on failure.
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
  the v1.0 pure-Cyrius reader.
- **[S-10] `dup(2)`** —
  <https://man7.org/linux/man-pages/man2/dup.2.html>.
  Backs the dup-safety contract in `sb_take_device` — the
  returned fd outlives the dbus message that delivered it.

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
  `public-api.md`'s error-code catalog: `EACCES=13`,
  `EINVAL=22`, `ENOMEM=12`, `ENOSYS=38`, `ENOBUFS=105`,
  `ENOTCONN=107`, `EHOSTUNREACH=113`. samvada returns these
  as negative values per sd-bus convention.

## Cyrius platform

- **[S-13] cyrius `lib/fnptr.cyr`** — the `fncall1` …
  `fncall6` dispatch primitives. samvada keeps every C wrapper
  at `<= 6` args specifically so dispatch stays inside this
  contract on both x86_64 SysV and aarch64.
- **[S-14] cyrius `lib/syscalls_x86_64_linux.cyr`** — Linux
  syscall numbers. samvada uses `SYS_EXIT (60)`, `getpid` (via
  `sys_getpid`), and `close` (via `sys_close`).
- **[S-15] cyrius stdlib `alloc.cyr`** — bump-allocator
  contract. samvada's scratch buffers (`_samvada_sess`,
  `_samvada_outs`) live for process lifetime; this is the
  documented stdlib behavior, not a samvada-specific choice.

## Compiler / toolchain

- **[S-16] `cc -Wall -Wextra -Werror`** — the C-shim build
  flags samvada's CI requires (`.github/workflows/ci.yml`).
  `samvada_main.c` compiles clean against libsystemd 245+
  (Debian buster oldstable baseline) under these flags.

## Project-internal cross-references

- [`docs/architecture/public-api.md`](architecture/public-api.md)
  — the surface map.
- [`docs/architecture/dbus-marshalling.md`](architecture/dbus-marshalling.md)
  — wire-format walk-through; cites [S-1] inline.
- [`docs/adr/0001-c-shim-then-pivot.md`](adr/0001-c-shim-then-pivot.md)
  — why the C shim ships now and retires at v1.0.
- [`docs/adr/0002-append-after-kind-ffi-invariant.md`](adr/0002-append-after-kind-ffi-invariant.md)
  — why the FFI table layout is frozen.
- [`SECURITY.md`](../SECURITY.md) — threat model.
