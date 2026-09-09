# Linking samvada into a consumer (v0.5.1)

samvada ships **only** the Cyrius API surface. It does not link
`libsystemd`. Consumer projects that want logind DRM-master
delegation build the C shim (`deps/samvada_main.c`) and link
`libsystemd` themselves.

The libsystemd dependency lives at the consumer's edge, and
disappears at v1.0 when native Cyrius dbus lands
([ADR-0003](../adr/0003-native-cyrius-dbus.md)) — without
touching consumer `.cyr` code.

> **Every command on this page was executed and verified against
> cyrius 6.6.1, libsystemd 261 and a running `systemd-logind` on
> 2026-09-09.** The pre-0.5.1 version of this guide documented a
> build that could not link and a compiler flag that does not
> exist; see [§What changed in 0.5.1](#what-changed-in-051).

## Prerequisites

- **libsystemd headers + library**:
  - Debian / Ubuntu: `apt install libsystemd-dev`
  - Arch: already in the `systemd` package
  - Fedora: `dnf install systemd-devel`
- A C compiler, plus `objcopy` (binutils — **required**, see
  step 3).
- `pkg-config` with `libsystemd.pc` discoverable.
- The Cyrius toolchain: both `cyrius` **and** `cycc`, which the
  installer drops side by side in `~/.cyrius/bin`. Step 2
  invokes `cycc` directly.

```sh
pkg-config --modversion libsystemd      # 245+ recommended
command -v cycc objcopy
```

## Adding samvada to your `cyrius.cyml`

```cyml
[deps.samvada]
git = "https://github.com/MacCracken/samvada.git"
tag = "0.5.1"
modules = ["dist/samvada.cyr"]
```

samvada's tags are **unprefixed** — `0.5.1`, not `v0.5.1`. Every
tag pushed to the repo is bare (`git tag` → `0.2.0 … 0.5.0`); the
`tag = "v0.2.0"` line this guide carried until 0.5.1 resolves to
nothing at all.

> **Do not pin below 0.5.1.** Every release from 0.2.0 through
> 0.5.0 shipped a `samvada_session_take_device()` that could not
> succeed in **any** environment — samvada never sent logind's
> mandatory `TakeControl`, so every call returned
> `org.freedesktop.login1.NotInControl`
> ([audit CRIT-1](../audit/2026-09-09-audit.md)). Those tags are
> not merely older; they are non-functional for the one thing
> this library exists to do.

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
0.5.1, does not exist on any machine. The full checkout is cached
whole, keyed by tag:

```
~/.cyrius/deps/samvada/0.5.1/deps/samvada_main.c
```

Most consumers vendor a copy into their own `deps/` rather than
reaching into a tool cache — mabda keeps `deps/wgpu_main.c` that
way — and the release artifact bundle ships the shim source for
exactly this. It is one self-contained `.c` file with no private
headers. The recipes below assume a vendored `deps/`.

## The build

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
object. Its whole usage line in 6.6.1 is

```
build [--aarch64|--win|--agnos] [--no-deps] [--strict] [--features <list>] <src> <out>
```

— there is no `--emit-object`, and passing one anyway does not
even error: verified on 6.6.1, the command exits `0`, prints
`OK (80904 bytes)`, and writes a
`ELF 64-bit LSB executable, statically linked` to the `.o` path
you named. `cc` then fails at the link. The pre-0.5.1 recipe
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

Everything before `samvada` in that loop is exactly the contents
of `dist/samvada.deps`, and the order is load-bearing —
`lib/samvada.cyr` goes last, after the leaves it folds over.
`cycc` is the bare compiler: it does **not** apply the manifest's
`[deps].stdlib` auto-include that `cyrius build` gives you, so
the unit has to name every leaf itself. Includes must be
**relative** (resolved against the directory you run `cycc`
from); `cycc` rejects absolute include paths unless
`CYRIUS_ALLOW_ABSOLUTE_INCLUDES=1`.

Check the result before moving on — `file build/app.o` must say
`ELF 64-bit LSB relocatable`. If it says `executable`, the
`object;` line did not reach the compiler.

### 3. Localize the Cyrius object's libc symbols — REQUIRED

```sh
objcopy -L atoi -L getenv -L memchr -L memcpy -L memset \
        -L strchr -L strlen -L strstr \
        build/app.o
```

**Do not skip this.** The Cyrius object exports its own `atoi`,
`getenv`, `memchr`, `memcpy`, `memset`, `strchr`, `strlen` and
`strstr` as global `T` symbols. A strong definition in a `.o`
beats a shared library's, so linking without localizing rebinds
*libsystemd's* calls to Cyrius's implementations, program-wide.

Derive the list rather than trusting this one — it grows with
your include set:

```sh
nm -g --defined-only build/app.o \
  | awk '$2 ~ /^[TDB]$/ {print $3}' | sort -u > /tmp/cy.txt
nm -D --defined-only "$(ldd /bin/true | awk '/libc\.so/ {print $3}')" \
  | awk '{print $3}' | sed 's/@.*//' | sort -u > /tmp/libc.txt
comm -12 /tmp/cy.txt /tmp/libc.txt
```

For the ten stdlib leaves above that prints exactly the eight
names flagged here.

The failure is silent and misleading. Verified on this exact
setup, with byte-identical C source either side:

| | `samvada_shim_init()` returns |
|---|---|
| without `objcopy -L …` | **`-107`** (`-ENOTCONN`) on one build, a **hang** on another — every `sd_bus_call_method` misbehaves, on a host whose system bus is running fine |
| with `objcopy -L …` | **`0`** — success |

Nothing in the error points at symbol interposition, and the
build succeeds either way.

**`memchr` is the load-bearing one.** Bisecting one symbol at a
time: localizing `memchr` *alone* is sufficient, and localizing
any single other symbol is not. The contracts differ in both
directions — Cyrius's `memchr` returns an **offset or `-1`**,
C's returns a **pointer or NULL** — so "not found" reads as a
non-NULL garbage pointer and "found at offset 0" reads as NULL.
Localize the whole set anyway: `strchr` and `strstr` have the
same offset-or-`-1` shape and are simply not on a hot path today.

Two things make this easy to misdiagnose, both verified:

- **It depends on reachability.** If nothing in your include
  chain reaches `memchr` it is eliminated as unreachable, never
  lands in the `.o`, and there is no bug — until unrelated code
  makes it reachable later.
- **`sd_bus_default_system()` alone does not trip it.** The bus
  opens cleanly either way; the failure needs a call that goes
  further, such as `GetSessionByPID`.

Filed upstream as
`cyrius/docs/development/issues/2026-09-09-stdlib-exports-libc-names-with-incompatible-abi.md`
with a standalone repro. If a future toolchain stops exporting
these names, this step becomes a no-op rather than wrong.

### 4. Compile your `main()`, which calls `samvada_shim_init()`

`samvada_shim_init()` builds the fn-table in file-scope static
storage and calls `samvada_main(table)`, which calls
`samvada_init`: it opens the system bus, resolves your session
via `GetSessionByPID`, and **takes session control**. The table
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
objcopy -L atoi -L getenv -L memchr -L memcpy -L memset \
        -L strchr -L strlen -L strstr build/probe.o

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
the wire detail and roadmap §N0, which is where the
signal-visibility contract gets decided before any of it is
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
4. **Add the `objcopy` step** (step 3). It was always required;
   it was never documented.
5. **Drop `--emit-object`** from your build. It does not exist;
   use the `object;` directive (step 2).
6. **Drop any `rc == 1` workaround** around
   `samvada_session_release_device()`. It returned `1` on success
   from 0.2.0 through 0.5.0 — `sd_bus_call_method`'s positive
   success value leaking through — and now returns `0` as the
   contract always said. Both are non-negative, so a consumer
   branching on `rc < 0` needs no edit.
7. **Re-pin to `0.5.1`.**

Behaviour you get for free, with no code change:
`TakeControl` / `ReleaseControl` around the session (which is
what makes `TakeDevice` reachable at all), `F_DUPFD_CLOEXEC`
instead of `dup()` on the device fd so DRM-master no longer
survives your `execve`, major/minor range-checking at the C
boundary, a 256-event cap on `samvada_pump_signals()`, and
`samvada_init()` self-cleaning on late failures so a failed init
no longer leaks the bus and leaves the API armed.

## What changed in 0.5.1

The 2026-09-09 audit found this guide documented a build that
**could never have worked**:

- The shim defined `main()` and so did you → `multiple definition
  of 'main'` (CRIT-2).
- `cyrius build … --emit-object` — no such flag, and passing it
  is silently ignored rather than rejected, so the "object" it
  writes is a linked executable.
- The `objcopy` localization step was missing entirely, and
  without it the link succeeds and the program then hangs or
  fails with `-107`, depending on the build.
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
