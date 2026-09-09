#!/usr/bin/env python3
"""dbus_decode.py — decode captured dbus messages, per the D-Bus specification.

    ./tools/dbus_decode.py tests/fixtures/dbus/*.bin

Written against the spec's message format, not against samvada's own
`docs/architecture/dbus-marshalling.md`, deliberately: the corpus exists to
CHECK that document, so a decoder derived from it would agree with its
errors. Where the two disagree, the captured bytes win.

Its second job is to make the one synthetic fixture trustworthy. A
hand-assembled `TakeDevice` reply cannot be captured (no seated session),
so it is judged by a decoder that has first been shown to decode all
fourteen REAL messages correctly.

SPDX-License-Identifier: GPL-3.0-only
"""

import struct
import sys

MSG_TYPE = {1: "METHOD_CALL", 2: "METHOD_RETURN", 3: "ERROR", 4: "SIGNAL"}
FIELD = {1: "PATH", 2: "INTERFACE", 3: "MEMBER", 4: "ERROR_NAME",
         5: "REPLY_SERIAL", 6: "DESTINATION", 7: "SENDER", 8: "SIGNATURE",
         9: "UNIX_FDS"}
FLAGS = {0x01: "NO_REPLY_EXPECTED", 0x02: "NO_AUTO_START",
         0x04: "ALLOW_INTERACTIVE_AUTHORIZATION"}


def align(n, a):
    return n + ((-n) % a)


class Reader:
    """Alignment is relative to the START OF THE MESSAGE, not to the buffer.

    This matters the moment two messages share one read, which the corpus
    shows is routine: the second message begins at an arbitrary offset, and
    aligning against the buffer origin silently mis-reads its header. The
    first message decodes fine either way, so the bug hides until a
    multi-message buffer appears.
    """

    def __init__(self, buf, off=0, le=True):
        self.b, self.o, self.le, self.base = buf, off, le, off

    def u8(self):
        v = self.b[self.o]; self.o += 1; return v

    def u32(self):
        self.o = self.base + align(self.o - self.base, 4)
        v = struct.unpack_from("<I" if self.le else ">I", self.b, self.o)[0]
        self.o += 4
        return v

    def string(self):
        n = self.u32()
        s = self.b[self.o:self.o + n].decode("utf-8", "replace")
        self.o += n + 1
        return s

    def signature(self):
        n = self.u8()
        s = self.b[self.o:self.o + n].decode()
        self.o += n + 1
        return s


def decode_one(buf, off=0):
    """Decode one message starting at `off`. Returns (info, next_off).

    Returns ({"incomplete": n}, len(buf)) when the buffer ends mid-message.
    That is not an error condition — a single read from the bus routinely
    ends part-way through a message, so any real reader has to carry the
    partial tail into the next read. The corpus contains a live example.
    """
    if len(buf) - off < 16:
        return {"incomplete": len(buf) - off, "need": 16}, len(buf)
    endian = buf[off]
    le = endian == ord("l")
    r = Reader(buf, off, le)
    r.u8()
    mtype = r.u8()
    flags = r.u8()
    proto = r.u8()
    body_len = r.u32()
    serial = r.u32()
    fields_len = r.u32()

    total_needed = align(16 + fields_len, 8) + body_len   # message-relative
    if len(buf) - off < total_needed:
        return {"incomplete": len(buf) - off, "need": total_needed,
                "type": MSG_TYPE.get(mtype, mtype), "serial": serial}, len(buf)

    fields, order = {}, []
    fend = r.o + fields_len
    while r.o < fend:
        r.o = r.base + align(r.o - r.base, 8)
        if r.o >= fend:
            break
        code = r.u8()
        sig = r.signature()
        if sig == "u":
            val = r.u32()
        elif sig in ("s", "o"):
            val = r.string()
        elif sig == "g":
            val = r.signature()
        else:
            val = f"<unhandled sig {sig}>"
        fields[FIELD.get(code, code)] = val
        order.append(code)

    body_off = off + align(fend - off, 8)
    body = buf[body_off:body_off + body_len]
    return {
        "endian": chr(endian), "type": MSG_TYPE.get(mtype, mtype),
        "flags": flags,
        "flag_names": [n for b, n in FLAGS.items() if flags & b],
        "proto": proto, "serial": serial, "body_len": body_len,
        "fields_len": fields_len, "field_order": order, "fields": fields,
        "body": body, "total": body_off + body_len - off,
    }, body_off + body_len


def decode_stream(buf):
    out, off = [], 0
    while off < len(buf):
        m, off = decode_one(buf, off)
        if m is None:
            break
        out.append(m)
    return out


def main(paths):
    bad = 0
    for p in paths:
        data = open(p, "rb").read()
        print(f"\n=== {p}  ({len(data)} bytes) ===")
        if data[:1] == b"\x00" or data[:4] in (b"DATA", b"OK 0", b"AUTH"):
            print("  SASL / auth text, not a dbus message:")
            for line in data.split(b"\r\n"):
                if line:
                    print(f"    {line!r}")
            continue
        msgs = decode_stream(data)
        if not msgs:
            print("  !! decoded nothing"); bad += 1; continue
        print(f"  {len(msgs)} message(s) in this buffer")
        for m in msgs:
            if "incomplete" in m:
                print(f"  - INCOMPLETE TAIL: {m['incomplete']} bytes present,"
                      f" {m['need']} needed"
                      + (f" ({m.get('type')} serial={m.get('serial')})"
                         if "type" in m else ""))
                print("    ^ a reader MUST carry this into the next read")
                continue
            print(f"  - {m['type']} serial={m['serial']}"
                  f" (0x{m['serial']:08x}) flags=0x{m['flags']:02x}"
                  f" {m['flag_names']}")
            print(f"    body_len={m['body_len']} fields_len={m['fields_len']}"
                  f" field_order={m['field_order']}")
            for k, v in m["fields"].items():
                print(f"      {k} = {v!r}")
            if m["body"]:
                print(f"      body = {m['body'].hex(' ')}")
        consumed = sum(x.get("total", 0) for x in msgs)
        slack = len(data) - consumed
        if slack and not any("incomplete" in x for x in msgs):
            print(f"  !! {slack} trailing bytes not accounted for")
            bad += 1
    return bad


if __name__ == "__main__":
    sys.exit(1 if main(sys.argv[1:]) else 0)
