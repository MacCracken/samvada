#!/bin/sh
# Build and run the backend differential. Needs libsystemd (for the
# C shim half) and a running system bus.
#
# IT TAKES SESSION CONTROL. Both halves call TakeDevice, which takes
# logind control of the caller's session first — and on a seated VT
# session logind's TakeControl turns the keyboard off and blanks the
# console (session_prepare_vt) until control is released. Run it from a
# seatless session (ssh, a container), never from the graphical session
# you are typing in. So it refuses to start without an explicit opt-in
# (1.1.2: it ran on invocation).
set -e
if [ "${SAMVADA_DIFF_TAKES_CONTROL:-}" != "1" ]; then
    echo "run.sh: this takes logind session control (keyboard off, console blanked on a seated VT)." >&2
    echo "run.sh: run it from a seatless session, with SAMVADA_DIFF_TAKES_CONTROL=1." >&2
    exit 2
fi
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
W="$(mktemp -d)"
trap 'rm -rf "$W"' EXIT
mkdir -p "$W/lib"
cp "$ROOT/dist/samvada.cyr" "$W/lib/"
LIB="${CYRIUS_LIB:-$HOME/.cyrius/lib}"
for m in syscalls string fmt alloc io vec str assert tagged fnptr; do
    cp "$LIB/$m.cyr" "$W/lib/" 2>/dev/null || true
done
cp "$ROOT/tools/differential/probe.cyr" "$ROOT/tools/differential/main.c" "$W/"
cd "$W"
{ printf 'object;\n'
  for m in syscalls string fmt alloc io vec str assert tagged fnptr; do
      echo "include \"lib/$m.cyr\""
  done
  cat probe.cyr
} | cycc > probe.o
cc -Wall -Wextra -Werror -c "$ROOT/deps/samvada_main.c" $(pkg-config --cflags libsystemd) -o shim.o
cc -c main.c -o main.o
cc shim.o probe.o main.o $(pkg-config --libs libsystemd) -o diffprobe
./diffprobe
