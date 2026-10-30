#!/usr/bin/env python3
"""dbus_tap.py — a transparent AF_UNIX relay for capturing dbus wire bytes.

Listens on a unix socket, forwards to the real system bus, and dumps every
byte in both directions. Point any dbus client at it:

    ./tools/dbus_tap.py /tmp/tap.sock --out tests/fixtures/dbus/run1 &
    DBUS_SYSTEM_BUS_ADDRESS=unix:path=/tmp/tap.sock busctl --system status

Why a relay and not an sd_bus hook: this needs no C, no libsystemd, no
linking, and no privileges. It works against *any* client, including the
samvada C shim, which is the point — the corpus has to be captured while
the shim still exists, because the reference implementation disappears when
the shim is deleted at 1.2.0 (deferred from the v1.0 cutover, roadmap N7).

Each direction is written to its own `.bin` (raw, appendable) plus a `.txt`
hexdump. Chunk boundaries are recorded because they are load-bearing: the
bus answers the SASL handshake with three lines in ONE read, and the first
post-Hello read carries TWO complete dbus messages. A reader written to
one-read-per-line, or to one-read-per-message, hangs or mis-parses against
a real bus.

SPDX-License-Identifier: GPL-3.0-only
"""

import argparse
import array
import os
import selectors
import socket
import sys
import time

SYSTEM_BUS = "/run/dbus/system_bus_socket"
# The kernel's per-message descriptor cap (SCM_MAX_FD) is 253, and dbus's
# own; src/dbus_sys.cyr receives into the same 253 * 4 bytes (1.1.2).
MAX_FDS = 253


def hexdump(data: bytes, indent: str = "") -> str:
    out = []
    for off in range(0, len(data), 16):
        chunk = data[off : off + 16]
        hexa = " ".join(f"{b:02x}" for b in chunk)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        out.append(f"{indent}{off:08x}  {hexa:<47}  |{text}|")
    return "\n".join(out)


class Capture:
    """One direction of one connection."""

    def __init__(self, outdir, conn_id, label):
        self.path_bin = os.path.join(outdir, f"conn{conn_id}-{label}.bin")
        self.path_txt = os.path.join(outdir, f"conn{conn_id}-{label}.txt")
        self.label = label
        self.conn_id = conn_id
        self.chunk = 0
        self.total = 0
        open(self.path_bin, "wb").close()
        with open(self.path_txt, "w") as f:
            f.write(f"# {label} — chunk boundaries are recorded deliberately;\n")
            f.write("# see the module docstring for why they matter.\n\n")

    def write(self, data: bytes):
        self.chunk += 1
        with open(self.path_bin, "ab") as f:
            f.write(data)
        with open(self.path_txt, "a") as f:
            f.write(f"--- chunk {self.chunk}: {len(data)} bytes "
                    f"(offset {self.total}) ---\n")
            f.write(hexdump(data) + "\n\n")
        self.total += len(data)


def relay(listen_path, outdir, bus_path, quiet):
    # Owner-only, for the socket AND the captures (1.1.2). The tap's
    # upstream connection authenticates to the bus as the TAP's user, so
    # anyone who can connect to the listening socket gets a bus
    # connection with that identity; under the default umask the socket
    # (and the captured traffic) could be reachable by other local users.
    os.umask(0o077)
    os.makedirs(outdir, exist_ok=True)
    if os.path.exists(listen_path):
        os.unlink(listen_path)

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(listen_path)
    os.chmod(listen_path, 0o600)
    srv.listen(8)
    if not quiet:
        print(f"tap listening on {listen_path} -> {bus_path}", file=sys.stderr)
        print(f"capturing into {outdir}", file=sys.stderr)

    sel = selectors.DefaultSelector()
    sel.register(srv, selectors.EVENT_READ, ("accept", None))
    conns = {}
    conn_id = 0

    try:
        while True:
            for key, _ in sel.select(timeout=1.0):
                kind, peer = key.data
                if kind == "accept":
                    cli, _ = srv.accept()
                    up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    try:
                        up.connect(bus_path)
                    except OSError as e:
                        print(f"upstream connect failed: {e}", file=sys.stderr)
                        cli.close()
                        continue
                    conn_id += 1
                    c2s = Capture(outdir, conn_id, "client-to-bus")
                    s2c = Capture(outdir, conn_id, "bus-to-client")
                    conns[cli] = (up, c2s)
                    conns[up] = (cli, s2c)
                    sel.register(cli, selectors.EVENT_READ, ("data", cli))
                    sel.register(up, selectors.EVENT_READ, ("data", up))
                    if not quiet:
                        print(f"conn {conn_id} opened", file=sys.stderr)
                    continue

                other, cap = conns.get(peer, (None, None))
                if other is None:
                    continue
                # SCM_RIGHTS must be forwarded, not dropped: TakeDevice's
                # reply carries its fd this way, and a relay that ignores
                # ancillary data would silently break the very call the
                # corpus exists to document.
                #
                # Room for the kernel's 253 (1.1.2): CMSG_SPACE(64) held 16,
                # and the kernel CLOSES descriptors that do not fit — the tap
                # forwarded 16 of a 20-fd message and said so only in a
                # flags value, altering exactly the traffic M1 is about.
                try:
                    data, ancdata, flags, _ = peer.recvmsg(65536, socket.CMSG_SPACE(MAX_FDS * 4))
                except OSError:
                    data, ancdata, flags = b"", [], 0
                if flags & socket.MSG_CTRUNC:
                    msg = (f"conn {cap.conn_id} {cap.label}: MSG_CTRUNC — the kernel dropped "
                           f"descriptors (more than {MAX_FDS} in one message); this capture "
                           "is NOT the traffic the peers sent")
                    print(f"\n*** WARNING: {msg} ***\n", file=sys.stderr)
                    with open(cap.path_txt, "a") as f:
                        f.write(f"!!! {msg} !!!\n\n")
                if not data and not ancdata:
                    for s in (peer, other):
                        try:
                            sel.unregister(s)
                        except Exception:
                            pass
                        conns.pop(s, None)
                        s.close()
                    continue
                if data:
                    cap.write(data)
                if ancdata:
                    with open(cap.path_txt, "a") as f:
                        f.write(f"--- ancillary: {len(ancdata)} cmsg "
                                f"(flags={flags:#x}) ---\n")
                        for lvl, typ, cdata in ancdata:
                            f.write(f"    level={lvl} type={typ} "
                                    f"len={len(cdata)}\n")
                            f.write(hexdump(cdata, "    ") + "\n")
                        f.write("\n")
                    try:
                        other.sendmsg([data], ancdata)
                    except OSError:
                        pass
                    # The kernel installed every forwarded descriptor in
                    # THIS process too; sendmsg duplicated it to the peer.
                    # Close ours, or the tap leaks one per fd-bearing
                    # message (1.1.2).
                    for lvl, typ, cdata in ancdata:
                        if lvl == socket.SOL_SOCKET and typ == socket.SCM_RIGHTS:
                            usable = len(cdata) - len(cdata) % 4
                            for fd in array.array("i", cdata[:usable]):
                                try:
                                    os.close(fd)
                                except OSError:
                                    pass
                elif data:
                    try:
                        other.sendall(data)
                    except OSError:
                        pass
    except KeyboardInterrupt:
        pass
    finally:
        try:
            os.unlink(listen_path)
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("listen", help="path for the tap's listening socket")
    ap.add_argument("--out", required=True, help="directory for captures")
    ap.add_argument("--bus", default=SYSTEM_BUS, help="upstream bus socket")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    relay(a.listen, a.out, a.bus, a.quiet)


if __name__ == "__main__":
    main()
