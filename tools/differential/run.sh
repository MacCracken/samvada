#!/bin/sh
# Build and run the backend differential. Needs libsystemd (for the
# C shim half) and a running system bus.
set -e
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
