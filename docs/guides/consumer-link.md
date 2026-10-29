# Linking samvada into a consumer (v1.1.0)

**Read the native path and stop.** As of 1.0, samvada speaks dbus
natively in Cyrius — no C, no `libsystemd`, no `pkg-config`, no
two-stage build, nothing to link. Adding the dependency and
calling one function is the entire integration.

The libsystemd **C shim** (`deps/samvada_main.c`) is still in the
tree as a fallback and as the differential reference, and it
retires at 1.2.0. Its two-stage recipe is documented further down
because it is verified and someone may need it — but **new
consumers should not use it.**

> **Every command on this page was executed and verified against a
> running `systemd-logind`.** The native path was re-verified on
> cyrius 6.6.2 for 1.0.1; the C-shim commands were verified
> against cyrius 6.6.1 and libsystemd 261 on 2026-09-09.
>
> **1.0.2 (cyrius 6.6.20), 2026-10-28:** the native handshake
> (`samvada_native_init` → `samvada_pump_signals` →
> `samvada_release` → re-init) was re-run against the live system
> bus, unseated. The C-shim recipe was re-compiled and re-linked
> against libsystemd 262 but **not executed** — running it would
> take session control on the graphical session it was built
> from.
>
> **1.1.0 (cyrius 6.7.18), 2026-10-28:** the same native handshake
> re-run on the live system bus, unseated, from the 1.1.0 bundle.
> The C-shim recipe compiled and linked against libsystemd 262, and
> again was **not executed**.

## Adding samvada to your `cyrius.cyml`

```cyml
[deps.samvada]
git     = "https://github.com/MacCracken/samvada.git"
tag     = "1.1.0"
modules = ["dist/samvada.cyr"]
```

samvada's tags are **unprefixed** — `1.1.0`, not `v1.1.0`. Every
tag pushed to the repo is bare.

> **Do not pin below 1.0.1**, and there are two independent
> reasons, either of which is disqualifying.
>
> **Below 0.5.1**, `samvada_session_take_device()` could not
> succeed in **any** environment — samvada never sent logind's
> mandatory `TakeControl`, so every call returned
> `org.freedesktop.login1.NotInControl`
> ([audit CRIT-1](../audit/2026-09-09-audit.md)).
>
> **Below 1.0.1**, the native backend read replies with plain
> `read()`, which makes the kernel **discard** the `SCM_RIGHTS`
> payload — so `take_device` could only ever return `-EBADF`
> (AUDIT-4) — and it popped a shared fd queue FIFO, so once
> descriptors did arrive it could hand back the one from an
> unrelated `ResumeDevice` signal. Both are fixed in 1.0.1.

> **Commit your `cyrius.lock`.** The bundle is fetched from a git
> tag and samvada's tags are lightweight and unsigned, so the
> lock's commit pin is the only thing that makes a repointed tag
> fail loudly rather than resolve silently. See
> [`SECURITY.md`](../../SECURITY.md).

## The native build

Nothing to compile but your own Cyrius code.

```sh
cyrius deps                                  # pulls dist/samvada.cyr + dist/samvada.deps
cyrius build src/main.cyr build/myapp        # done
```

```cyr
include "lib/samvada.cyr"

var rc = samvada_native_init();                 # connect: socket, SASL, Hello, session
if (rc < 0) { return rc; }

var fd = samvada_session_take_device(226, 0);   # a DRM node
if (fd < 0) { samvada_release(); return fd; }
# ... use fd ...
samvada_session_release_device(226, 0);
samvada_release();
```

`samvada_native_init()` takes no arguments **by design**: CLAUDE.md
forbids exposing an FFI table in a public signature, so a native
consumer never handles one. Do not call `samvada_init()` or
`samvada_main()` — those are the C-shim entry points.

### Prerequisites (native)

The Cyrius toolchain. That is the whole list. No C compiler, no
`pkg-config`, no `libsystemd`, no `cycc`, no `objcopy`.

---

## The C-shim build (fallback — retires at 1.2.0)

Everything below this line is the **libsystemd** path. Skip it
unless you are deliberately linking the reference backend.

### Prerequisites (C shim only)

- **libsystemd headers + library**:
  - Debian / Ubuntu: `apt install libsystemd-dev`
  - Arch: already in the `systemd` package
  - Fedora: `dnf install systemd-devel`
- A C compiler. (`objcopy` is needed **only** if you pin cyrius
  below 6.6.2 — see step 3.)
- `pkg-config` with `libsystemd.pc` discoverable.
- The Cyrius toolchain: both `cyrius` **and** `cycc`, which the
  installer drops side by side in `~/.cyrius/bin`. Step 2
  invokes `cycc` directly.

```sh
pkg-config --modversion libsystemd      # 245+ recommended
command -v cycc
```


`cyrius deps` copies each path in `modules` into `lib/`, so the
API surface lands at `lib/samvada.cyr` and the compiler
auto-includes it, giving you the public API:

```cyr
fn my_setup() {
    var fd = samvada_session_take_device(226, 0);   # /dev/dri/card0
    if (fd < 0) { return fd; }
    return fd;
}
```

Note there is **no `table` argument** in your code. The C shim
builds the fn-table and hands it to samvada; you never see it.

### Where the C shim source actually is

`cyrius deps` copies only the `modules` list into `lib/`. It does
**not** create a `lib/samvada/` directory — the path
`lib/samvada/deps/samvada_main.c`, which this guide gave until
1.0.1, does not exist on any machine. The full checkout is cached
whole, keyed by tag:

```
~/.cyrius/deps/samvada/1.1.0/deps/samvada_main.c
```

Most consumers vendor a copy into their own `deps/` rather than
reaching into a tool cache — mabda keeps `deps/wgpu_main.c` that
way — and the release artifact bundle ships the shim source for
exactly this. It is one self-contained `.c` file with no private
headers. The recipes below assume a vendored `deps/`.

### The build steps

Three objects get linked: the C shim, your Cyrius code, and your
own `main()`.

### 1. Compile the C shim

```sh
cc -Wall -Wextra -Werror -c deps/samvada_main.c \
   $(pkg-config --cflags libsystemd) \
   -o build/samvada_main.o
```

The shim does **not** define `main()` — you do. (Before 0.5.1 it
claimed `main()` unconditionally, which made this link
impossible.)

### 2. Compile your Cyrius code to an object

`cyrius build` produces a finished executable, not a relocatable
object, and it has no `--emit-object` flag. On the pinned
toolchain (6.7.18) passing one fails up front, on stderr:

```
error: cyrius build: unknown option '--emit-object'
```

exit `1`, nothing written, and the usage line on stdout (6.6.20
printed `Error:` and no usage line). **Through cyrius 6.6.4 it did not even
error**: verified on 6.6.1, the command exits `0`, prints
`OK (80904 bytes)`, and writes a
`ELF 64-bit LSB executable, statically linked` to the `.o` path
you named, and `cc` then fails at the link. cyrius 6.6.5 made
unknown flags an error. Either way the pre-0.5.1 recipe
`cyrius build src/main.cyr build/myapp.o --emit-object` never
produced an object file.

To get a linkable `.o`, prepend the `object;` directive and pipe
through `cycc` directly:

```sh
{ printf 'object;\n'
  for m in syscalls string fmt alloc io vec str assert tagged fnptr samvada; do
      echo "include \"lib/$m.cyr\""
  done
  cat src/app.cyr
} | cycc > build/app.o
```

This is the one sanctioned direct-`cycc` invocation — the same
pattern mabda uses for its wgpu shim (`Makefile`, `build/%.o`).
Everywhere else, use `cyrius build`.

Since 1.0.2 the bundle names its own requirements: cyrius 6.6.18+
`distlib` derives `dist/samvada.deps` by compiling the bundle on
six targets, and writes the result into `dist/samvada.cyr` as a
compile-verified `include` block — `alloc`, `syscalls`, `string`,
`fnptr`. So `include "lib/samvada.cyr"` alone compiles, and the
other leaves in that loop are there for **your** code (`fmt`,
`io`, …), not samvada's. `cycc` includes each file once, so the
repeats are harmless, and the order is no longer load-bearing
(both verified on 6.6.20 and again on 6.7.18; through 1.0.1 the bundle had no
includes and had to come last). `cycc` is the bare compiler: it
does **not** apply the manifest's `[deps].stdlib` auto-include
that `cyrius build` gives you, so the unit has to name every leaf
your own code uses. Includes must be
**relative** (resolved against the directory you run `cycc`
from); `cycc` rejects absolute include paths unless
`CYRIUS_ALLOW_ABSOLUTE_INCLUDES=1`.

Check the result before moving on — `file build/app.o` must say
`ELF 64-bit LSB relocatable`. If it says `executable`, the
`object;` line did not reach the compiler.

### 3. Symbol localization — NO LONGER NEEDED on cyrius ≥ 6.6.2

**Skip this step** if you are on the pinned toolchain. It is kept
here because a consumer on an older cyrius still needs it, and
because the failure it prevents is otherwise impossible to
diagnose.

Through cyrius **6.6.1**, an `object;` build exported
`memchr`, `strchr`, `strstr`, `strlen`, `memcpy`, `memset`,
`atoi` and `getenv` as **preemptible** globals (`vis=DEFAULT`), so
a linked C library's own calls rebound to Cyrius's
implementations. The contracts differ in both directions —
Cyrius's `memchr` returns an **offset or `-1`**, C's returns a
**pointer or NULL** — so "not found" reads as a garbage non-NULL
pointer and "found at offset 0" reads as NULL. samvada's process
hung inside `sd_bus_call_method`.

cyrius **6.6.2** emits these names with `vis=HIDDEN`, so the
dynamic linker can no longer preempt them. Verified on the pinned
toolchain:

| | 6.6.1 | 6.6.2 |
|---|---|---|
| `readelf -sW` on `memchr` | `bind=GLOBAL vis=DEFAULT` | `bind=GLOBAL vis=HIDDEN` |
| link with no `objcopy` | **hangs** | **works** |

(`nm` shows `T` in both cases — the binding is unchanged; it is
the *visibility* that was fixed. Checking with `nm` alone will
mislead you.)

Re-checked on 6.7.18 for 1.1.0: all eight are still
`bind=GLOBAL vis=HIDDEN` in the object, and the linked binary's
dynamic symbol table exports none of them. That binary was linked,
not run.

**If you are pinned below 6.6.2**, add:

```sh
objcopy -L atoi -L getenv -L memchr -L memcpy -L memset \
        -L strchr -L strlen -L strstr \
        build/app.o
```

`memchr` is the load-bearing one — localizing it alone was
sufficient — and `memeq`, which some older recipes list, is a
Cyrius-only name with no libc counterpart, so localizing it does
nothing.

Filed upstream as
`cyrius/docs/development/issues/2026-09-09-stdlib-exports-libc-names-with-incompatible-abi.md`
and fixed in 6.6.2.

### 4. Compile your `main()`, which calls `samvada_shim_init()`

`samvada_shim_init()` builds the fn-table in file-scope static
storage and calls `samvada_main(table)`, which calls
`samvada_init`: it opens the system bus, resolves your session
via `GetSessionByPID`, and validates the backend's `take_control`
slot is populated. It does **not** take session control — 0.6.0
moved that to the first `take_device`, because `TakeControl` runs
logind's `session_prepare_vt()` and blanks the console
([ADR-0004](../adr/0004-session-control-lifecycle-and-signal-visibility.md)). The table
must be static because samvada *borrows* the pointer and re-reads
it on every dispatch — it never copies it.

```c
/* src/launch.c */
#include <stdint.h>
#include <stdio.h>

extern void _cyrius_init(void);      /* Cyrius globals / enums  */
extern long alloc_init(void);        /* Cyrius heap; after init */
extern long samvada_shim_init(void); /* 0 | -errno              */
extern long samvada_release(void);
extern long app_main(void);          /* your Cyrius entry point */

int main(void) {
    _cyrius_init();
    alloc_init();

    long rc = samvada_shim_init();   /* 0, or a negative sd-bus errno */
    if (rc < 0) {
        fprintf(stderr, "samvada_shim_init: %ld\n", rc);
        return 1;
    }

    long r = app_main();             /* samvada's API is live in here */
    samvada_release();
    return (int)r;
}
```

```sh
cc -Wall -Wextra -Werror -c src/launch.c -o build/launch.o
```

Order matters. `_cyrius_init()` resets Cyrius globals, so
`alloc_init()` must follow it, and both must precede every Cyrius
call — `samvada_shim_init()` included, since it dispatches
straight into `samvada_main`. Call it **once**: a second
`samvada_init` with no intervening `samvada_release()` returns
`-16` (`-EBUSY`) rather than overwriting the live bus.

Your Cyrius `src/app.cyr` defines `app_main` (or whatever you
name it) and **no `main()`** — the launcher owns that.

### 5. Link

```sh
cc build/samvada_main.o build/app.o build/launch.o \
   $(pkg-config --libs libsystemd) \
   -o build/myapp
```

### Standalone probe binary (optional)

For a throwaway binary with no `main()` of your own, compile the
shim with `-DSAMVADA_STANDALONE_MAIN` and it supplies one:

```sh
cc -Wall -Wextra -Werror -DSAMVADA_STANDALONE_MAIN \
   -c deps/samvada_main.c \
   $(pkg-config --cflags libsystemd) -o build/samvada_standalone.o

printf 'object;\n' | cat - src/probe.cyr | cycc > build/probe.o

cc build/samvada_standalone.o build/probe.o \
   $(pkg-config --libs libsystemd) -o build/samvada-probe
```

`src/probe.cyr` needs no code at all — the `include` lines and
nothing else. The Cyrius object only has to supply
`_cyrius_init`, `alloc_init` and `samvada_main`, and
`lib/samvada.cyr` plus the stdlib leaves already define all three
(`nm build/probe.o | grep ' T '` confirms it).

The shim's `main()` is `_cyrius_init(); alloc_init(); return
(int)samvada_shim_init();`, so the **exit status is the only
output channel** — there is nothing to print, and a negative `rc`
reaches the shell as `256 + rc`:

```sh
$ ./build/samvada-probe; echo "exit=$?"
exit=0                       # bus + session + TakeControl all OK

$ DBUS_SYSTEM_BUS_ADDRESS=unix:path=/nonexistent ./build/samvada-probe
$ echo "exit=$?"
exit=254                     # 256 - 2 → rc = -2 = -ENOENT
```

Use this only for probes. Any real program owns its `main()` —
`-DSAMVADA_STANDALONE_MAIN` is exactly what reinstates the
`multiple definition of 'main'` collision that made the pre-0.5.1
recipe unbuildable.

## Verifying it worked

There are **two** gates here and they are not the same gate.
`samvada_shim_init()` needs a reachable system bus and a logind
session that `GetSessionByPID` can find for the calling process,
and then takes session control. All three succeed from an
ordinary ssh login on a `pam_systemd` host — measured 2026-09-09:
`GetSessionByPID` → `/org/freedesktop/login1/session/_32`
(logind's escaped spelling of session `2`), `TakeControl` → `0`.

`samvada_session_take_device()` needs strictly more: the session
must be attached to a **seat**, and an ssh session is not.

```
$ ./build/myapp; echo "exit=$?"
consumer: samvada is live
consumer: take_device rc = -13
exit=0
```

That is a **successful link and a successful init**. The bus
opened, the session resolved, `TakeControl` was granted, and only
`TakeDevice` was refused — by logind, with
`org.freedesktop.DBus.Error.AccessDenied` / *"Operation not
permitted"*, which surfaces as `-13` (`-EACCES`). It is the
expected result off a seat, and no build flag moves it:

```sh
$ loginctl show-session "$XDG_SESSION_ID" -p Seat -p Remote -p Active
Seat=
Remote=yes
Active=yes
```

If instead the launcher prints `samvada_shim_init: <negative>`,
init itself failed and nothing after it ran:

| rc | Reading |
|---|---|
| `-2` | `-ENOENT` — no system bus socket reachable (measured by pointing `DBUS_SYSTEM_BUS_ADDRESS` at a nonexistent path) |
| `-107` | `-ENOTCONN` — the bus handle came back null, or every `sd_bus_call_method` is failing. If the latter, you skipped step 3 |
| `-38` | `-ENOSYS` — a slot the call needs is null. A pre-0.5.1 shim linked against a 0.5.1 bundle lands here on `take_control` |
| `-22` | `-EINVAL` — the table's `kind` word is neither `1` nor `2`; you are not linking the shim you think you are |
| `-16` | `-EBUSY` — `samvada_shim_init()` called twice with no `samvada_release()` between |

**`-22` from `take_device`** means something else, and it is the
pre-0.5.1 failure: sd-bus maps logind's `NotInControl` onto
`-EINVAL` (measured — call `TakeDevice` without `TakeControl` and
sd-bus returns `-22` with
`name=org.freedesktop.login1.NotInControl`). If you see it, check
that your pin is ≥ 0.5.1.

Device numbers are the real ones, and `card0` is not universal:
`stat -c '%t %T' /dev/dri/card*` prints major and minor in hex
(`e2 1` → major 226, minor 1). To actually receive an fd you need
an **active, seated** session holding the device — the hardware
gate tracked as the CG lane in
[`roadmap.md`](../development/roadmap.md).

### Teardown

`samvada_release()` issues `ReleaseControl` before closing the
bus, and per `org.freedesktop.login1(5)` that "also releases all
devices for which the controller requested ownership via
`TakeDevice()`". Any fd you still hold stops being a live
DRM-master handle at that moment — the descriptor itself remains
yours to `close()`, since samvada handed you an
`F_DUPFD_CLOEXEC` duplicate, but releasing on logind's side does
not close it. Do device teardown **before** `samvada_release()`,
not after. Message-level detail in
[`dbus-marshalling.md`](../architecture/dbus-marshalling.md).

## What is NOT wired

**`PauseDevice` and `ResumeDevice` never reach you.** Do not
design a v0.x consumer around them.

The C shim populates slot +48 (`sb_subscribe_pause_resume`) and
slot +56 (`sb_unsubscribe`), and both wrappers work — but **no
Cyrius code dispatches either one**. Grep `src/` for
`samvada_slot_subscribe_pause_resume`: the only hits are the
offset constant's own definition in `src/samvada_ffi.cyr` and the
offset pin in `tests/samvada.tcyr`. No public fn installs a match
rule, so `sd_bus_process` has nothing to route the signals to.
`samvada_pump_signals()` therefore counts and discards messages
and runs no pause/resume callback, ever. `PauseDeviceComplete` is
unwired on both sides of the FFI boundary, so a consumer cannot
acknowledge a pause either.

A second bus connection is not a workaround: per
`org.freedesktop.login1(5)` the active session controller
*exclusively* receives these signals, and the controller claim is
bound to the connection that called `TakeControl` — which is
samvada's. See
[`dbus-marshalling.md`](../architecture/dbus-marshalling.md) for
the wire detail, and
[ADR-0004](../adr/0004-session-control-lifecycle-and-signal-visibility.md)
for the conditions under which any of it is
built.

The practical consequence for a compositor: logind revokes
DRM-master on VT switch, and v0.x samvada gives you no
notification that it happened. Your fd simply goes quiet.

Two more things worth stating plainly:

- **Live-bus end-to-end on a seated session is unverified.**
  Everything up to and including `TakeControl` is measured;
  `TakeDevice` succeeding is not, and that gate is hardware.
- **samvada is single-threaded and has no locking.** Every public
  fn reads and writes module-scope state with plain `load64` /
  `store64`, and `_samvada_outs` is a shared 16-byte scratch that
  concurrent callers would hand each other's out-parameters back
  through. **Consumers must serialize all samvada calls onto one
  thread**, or guard them with their own mutex — see
  [`public-api.md`](../architecture/public-api.md).

## Upgrading from 0.5.0 or earlier

**Breaking, at the link surface only.** No `.cyr` source changes.

1. **Your `main()` now calls `samvada_shim_init()`.** Previously
   the shim owned `main()`. If your build somehow worked before,
   it was because you were not linking the shim at all.
2. **Do not define `samvada_main` in your Cyrius code.** The old
   guide told you to. `dist/samvada.cyr` already defines it, so a
   second definition makes the compiler warn on every build of
   the unit —

   ```
   warning:<source>:13:1: duplicate fn 'samvada_main'
     (last definition wins; first defined in lib/samvada.cyr)
   ```

   — and *last definition wins* means your stub **replaces**
   samvada's, which is the one that calls `samvada_init`. The
   shim then dispatches into your version and samvada is never
   initialised. Delete it; give your entry point any other name
   and call it from `main()`.
3. **Re-vendor the shim** from the 0.5.1 checkout. A 0.5.0 shim
   against a 0.5.1 bundle leaves slot +72 null, and
   `samvada_init` returns `-38` (`-ENOSYS`) rather than walking
   into a guaranteed `NotInControl`.
4. **You can drop the `objcopy` step** if you also move to
   cyrius ≥ 6.6.2, which hides the colliding libc names at the
   source (step 3). On an older cyrius it is still required, and
   it was never documented before 0.5.1.
5. **Drop `--emit-object`** from your build. It does not exist;
   use the `object;` directive (step 2).
6. **Drop any `rc == 1` workaround** around
   `samvada_session_release_device()`. It returned `1` on success
   from 0.2.0 through 0.5.0 — `sd_bus_call_method`'s positive
   success value leaking through — and now returns `0` as the
   contract always said. Both are non-negative, so a consumer
   branching on `rc < 0` needs no edit.
7. **Re-pin to `1.1.0`.**

Behaviour you get for free, with no code change:
`TakeControl` / `ReleaseControl` around the session (which is
what makes `TakeDevice` reachable at all), `F_DUPFD_CLOEXEC`
instead of `dup()` on the device fd so DRM-master no longer
survives your `execve`, major/minor range-checking at the C
boundary, a 256-event cap on `samvada_pump_signals()`, and
`samvada_init()` self-cleaning on late failures so a failed init
no longer leaks the bus and leaves the API armed.

## Upgrading from 0.5.1 to 1.0.1

No `.cyr` signature changes — the eight-fn public surface is
frozen, and since 1.0.1 it is frozen *mechanically*
(`tools/surface_check.py` runs in CI). What changes underneath
you:

1. **Switch to `samvada_native_init()`** and delete the entire C
   build: the shim object, `pkg-config`, the `cycc` object step,
   `objcopy`, and your `samvada_shim_init()` call. Your own
   `main()` no longer needs to exist for samvada's sake.
2. **Session control moved** (0.6.0, ADR-0004). `samvada_init` no
   longer takes it; the first `take_device` does, and the last
   `release_device` gives it back. If you relied on control being
   held from init, you were relying on the behaviour that blanked
   the console.
3. **New error codes reach you** from the native backend: `-110`
   (`-ETIMEDOUT`, the 25 s wall-clock deadline or an expired
   receive timeout) and `-71` (`-EPROTO`, a malformed or truncated
   reply). Both are `< 0`, so existing branching is unaffected.
4. **Out-of-range device numbers are now rejected**
   (`0 ≤ v ≤ UINT32_MAX`, `-EINVAL`) rather than silently
   truncated to 32 bits. If you were passing a value above
   `UINT32_MAX` you were opening a *different device*.
5. Nothing to do about the C shim: it still builds and still
   works. It retires at 1.2.0.

## Upgrading from 1.0.2 to 1.1.0

samvada moved its own cyrius pin `6.6.20` → `6.7.18` and its CI to
Ubuntu 26.04. The public surface is unchanged, and `dist/samvada.deps`
is unchanged (`alloc`, `syscalls`, `string`, `fnptr`). One thing can
reach your build:

1. **Your cyrius pin must be 6.6.5 or later.** The bundle now sends
   through the stdlib's `sys_sendto` rather than a syscall number of
   its own, and `sys_sendto` first shipped in cyrius 6.6.5. The
   bundle compiles under **your** manifest's pin, not samvada's.
   Verified with a probe built from the bundle under each
   toolchain's own stdlib. On 6.6.4 it fails:

   ```
   warning: undefined function 'sys_sendto'
   error: refusing to emit binary with 1 reachable undefined function(s) (pass --allow-undef to downgrade)
   ```

   (`cycc` exit 1). On 6.6.5 it builds and the native handshake
   runs; mabda's 6.7.5 pin compiles it. 1.0.2's bundle built on
   6.6.4.
2. **Re-pin to `1.1.0`.**
3. Moving your own pin to 6.7.x is a separate decision with its
   own notes — cyrius's `docs/development/ecosystem-migration.md`
   — and samvada 1.1.0 does not require it.
4. If you reached into the bundle's `dbus_*` internals (outside the
   frozen 8-fn surface), `dbus_socket_sendto_nr`,
   `dbus_sys_recvmsg_nr` and `dbus_session_clock_nr` are gone; use
   the stdlib's `SYS_SENDTO` / `SYS_RECVMSG`, or `228`.

## Upgrading from 1.0.1 to 1.0.2

A toolchain patch: samvada moved its cyrius pin `6.6.2` →
`6.6.20`. No source, signature or behaviour change; the public
surface is identical. One thing can reach your build:

1. **`dist/samvada.deps` lists 4 leaves, not 10.** cyrius 6.6.18
   stopped copying a producer's `[deps] stdlib` into its sidecar;
   it is now derived by compiling the bundle, and samvada itself
   needs only `alloc`, `syscalls`, `string` and `fnptr`. If your
   code called `fmt`, `io`, `vec`, `str`, `assert` or `tagged`
   without declaring them, it compiled only because samvada's
   sidecar happened to drag them in. The build now fails and
   names the fn and its leaf — verified on 6.6.20:

   ```
   hint: 'fmt_int' is defined by stdlib leaf 'fmt' — add it to [deps] stdlib in cyrius.cyml
   ```

   Add it, `cyrius deps`, rebuild.
   mabda already declares every leaf it uses.
2. **Re-pin to `1.0.2`.**

## What changed in 0.5.1

The 2026-09-09 audit found this guide documented a build that
**could never have worked**:

- The shim defined `main()` and so did you → `multiple definition
  of 'main'` (CRIT-2).
- `cyrius build … --emit-object` — no such flag, and through
  cyrius 6.6.4 passing it was silently ignored rather than
  rejected, so the "object" it wrote was a linked executable.
  From 6.6.5 it fails with `unknown option '--emit-object'`.
- The `objcopy` localization step was missing entirely, and on
  cyrius ≤ 6.6.1 the link succeeds without it and the program
  then hangs (or fails with `-107`, depending on the build).
  Filed upstream and **fixed in cyrius 6.6.2**, which samvada
  now pins — so on the pinned toolchain the step is no longer
  needed at all.
- It told you to define `samvada_main` yourself, colliding with
  the bundle's and replacing it.
- It pointed at `lib/samvada/deps/samvada_main.c`, a path
  `cyrius deps` never creates.
- It pinned `v0.2.0` — a tag spelling that does not exist, on a
  release whose `take_device` cannot succeed.
- It claimed ssh shells are not logind sessions and blamed init
  failures on that. On a `pam_systemd` host they are logind
  sessions; what they lack is a **seat**, and that bites at
  `TakeDevice`, not at `samvada_init`.

Every recipe above has now been run end to end.

## What samvada DOESN'T link

- **No `libdbus`.** logind speaks libsystemd's `sd_bus`.
- **No `libelogind`.** v0.x is systemd-only; native v1.0 talks
  the wire protocol directly and works against any dbus daemon.
- **No graphics deps.** samvada is a dbus client.
