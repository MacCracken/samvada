# Linking samvada into a consumer (v0.2.0)

samvada itself ships **only** the Cyrius API surface. It does
not link `libsystemd`. Consumer projects that want logind
DRM-master delegation must build the C shim
(`deps/samvada_main.c`) and link `libsystemd` themselves.

This is the same pattern mabda follows for `wgpu-native`:
samvada's source bundle is sovereign Cyrius, the libsystemd
dependency lives at the consumer's edge, and the dependency
disappears at v1.0 when the pure-Cyrius marshaller lands —
without touching consumer code.

## Prerequisites

- **libsystemd headers + library** on the build host:
  - Debian / Ubuntu: `apt install libsystemd-dev`
  - Arch: already in the `systemd` package
  - Fedora: `dnf install systemd-devel`
- A C compiler (`cc`, `gcc`, `clang` — any will do).
- `pkg-config` with `libsystemd.pc` discoverable.

Verify:

```sh
pkg-config --modversion libsystemd      # 245+ recommended
pkg-config --cflags --libs libsystemd
```

## Adding samvada to your `cyrius.cyml`

```cyml
[deps.samvada]
git = "https://github.com/MacCracken/samvada"
tag = "v0.2.0"
modules = ["dist/samvada.cyr"]
```

Then `cyrius deps` resolves it into your `lib/` directory and
the compiler auto-includes `lib/samvada.cyr`. Your Cyrius source
gets the public API:

```cyr
fn my_setup(table) {
    var rc = samvada_init(table);
    if (rc < 0) { return rc; }
    var fd = samvada_session_take_device(226, 0);   # /dev/dri/card0
    if (fd < 0) { return fd; }
    return fd;
}
```

## The two-stage build

Because `deps/samvada_main.c` provides its own `main()` and
calls into Cyrius via `samvada_main(table)`, your build links
the C shim alongside your Cyrius binary. The recipe:

```sh
# 1. Compile the C shim. It lives in your samvada checkout
#    under lib/samvada/deps/samvada_main.c after `cyrius deps`.
cc -c lib/samvada/deps/samvada_main.c \
   $(pkg-config --cflags libsystemd) \
   -o build/samvada_main.o

# 2. Build your Cyrius source. The Cyrius compiler already knows
#    samvada's API surface (auto-included from lib/samvada.cyr).
cyrius build src/main.cyr build/myapp.o --emit-object

# 3. Link them together with libsystemd.
cc build/samvada_main.o build/myapp.o \
   $(pkg-config --libs libsystemd) \
   -o build/myapp
```

Your Cyrius `src/main.cyr` defines `samvada_main(table)` (the C
shim calls it), not its own `main()` — `main()` comes from the C
shim. If you want a no-shim smoke build for development, follow
samvada's own `src/main.cyr` shape: `main()` in Cyrius for
`cyrius build`, `samvada_main()` in samvada.cyr for the linked
path.

## What samvada DOESN'T link

- **No `libdbus`.** logind speaks libsystemd's `sd_bus`, not
  the older libdbus stack. samvada follows libsystemd.
- **No `libelogind`** for non-systemd Linux distros. v0.x is
  systemd-only; pure-Cyrius v1.0 talks the wire protocol
  directly and works on any dbus daemon.
- **No `wgpu`, no graphics deps.** samvada is a dbus client,
  full stop. mabda is the GPU layer; the two compose.

## Runtime requirements

The consumer process needs to be runnable from a logind session
to call `TakeDevice`. In practice:

- A graphical login session (gdm, sddm, lightdm, ly, ...) sets
  `XDG_SESSION_ID` and registers the session with logind.
- A bare TTY login also works — `loginctl session-status`
  reports the session, and `TakeDevice` will succeed against
  `/dev/dri/cardN` if the user has the right ACLs (`video`
  group on most distros).
- `ssh` sessions are *not* logind sessions by default. Test
  inside a real seat or use `loginctl enable-linger` +
  `systemd-run --user --scope`.

## Verifying the link worked

A 10-line consumer binary prints the session path it found:

```cyr
include "lib/samvada.cyr"

fn samvada_main(table) {
    alloc_init();
    var rc = samvada_init(table);
    if (rc < 0) {
        println("samvada_init failed");
        return rc;
    }
    println("samvada wired; session resolved.");
    return samvada_release();
}
```

If linked correctly:

```
$ ./build/myapp
samvada wired; session resolved.
```

If you see `samvada_init failed` with rc = -2 or -113, the bus
connect succeeded but no session — likely an ssh shell. Run
from a console TTY or graphical session.

## Future: dropping the libsystemd dep

samvada v1.0 retires `deps/samvada_main.c` and the libsystemd
link. Consumers will:

1. Drop the `cc -c lib/samvada/deps/samvada_main.c` line.
2. Drop `pkg-config --libs libsystemd` from the link.
3. Bump `tag = "v1.0.0"` in `[deps.samvada]`.

Their Cyrius source code does not change. The public API
(`samvada_init` / `samvada_session_take_device` / etc.) is
frozen at v0.2.0 and survives the pivot intact. See
`docs/development/roadmap.md` §M3 for the pivot details.
