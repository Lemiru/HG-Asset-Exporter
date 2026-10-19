"""Derive which room (stage PAC) owns each cutscene event from SLUS_210.75.

    python tools/derive_event_rooms.py path/to/SLUS_210.75 hgexport/data/event_rooms.json

Method (all addresses for the NTSC-U executable, SHA-256 3b374d53...):

1. Every room handler class has a resource table listing its events as
   (name, "EVxxxx\\EVxxxx.SFD") pointer pairs, plus MTN banks and named
   objects. Small accessor methods return the table address with
   ``lui/addiu`` (or ``addiu reg, gp, imm``; gp = 0x4828F0).
2. The room manager allocates one handler per slot; slot ``k``'s vtable is
   installed by the stub at 0x20A5C8 + 16*k (``lui; addiu; b; sw``).
3. Slot k handles the k-th room of the game's room list (SLOT_RANGES). The
   list was derived from handlers whose named objects match exactly one PAC's
   slot-12 objects; it has exactly one entry per slot (171) and skips IDs that
   do not exist (0x89, 0x8B, 0x90). The result was checked by placing every
   event's actors on the predicted room's navigation floor.
"""
from __future__ import annotations

import collections
import json
import re
import struct
import sys

VA, OFF, SIZE = 0x100000, 0x80, 0x37B200
GP = 0x4828F0
FACTORY = 0x20A5C8
SLOTS = 0xAB
# Room IDs in slot order: 0x00-0x37, 0x40-0x70, 0x80-0x88, 0x8A, 0x8C-0x8F,
# 0x91-0x9B, 0xC0-0xC8, 0xD0-0xD9, 0xE0-0xE9, 0x100-0x10B (171 = slot count).
SLOT_RANGES = [(0x00, 0x37, 0x00), (0x38, 0x68, 0x08), (0x69, 0x71, 0x17), (0x72, 0x72, 0x18),
               (0x73, 0x76, 0x19), (0x77, 0x81, 0x1A), (0x82, 0x8A, 0x3E), (0x8B, 0x94, 0x45),
               (0x95, 0x9E, 0x4B), (0x9F, 0xAA, 0x61)]


def slot_room(slot: int) -> int | None:
    for lo, hi, offset in SLOT_RANGES:
        if lo <= slot <= hi:
            return slot + offset
    return None


def derive(exe: bytes) -> dict[str, list[str]]:
    def off(a):
        return a - VA + OFF if VA <= a < VA + SIZE else None

    def w(a):
        return struct.unpack_from("<I", exe, off(a))[0]

    def string(a):
        o = off(a)
        if o is None:
            return None
        e = exe.find(b"\0", o, o + 40)
        raw = exe[o:e] if e > o else b""
        return raw.decode() if raw and all(32 <= c < 127 for c in raw) else None

    entries = {}
    for o in range(OFF, OFF + SIZE - 8, 4):
        a = struct.unpack_from("<I", exe, o)[0]
        if 0x400000 <= a < 0x480000:
            t = string(a)
            if t and re.fullmatch(r"(EV|DE)[0-9A-F]{4}", t):
                entries[o - OFF + VA] = t

    def function_start(c):
        a = c
        while a > VA + 16:
            if w(a - 8) == 0x03E00008:          # previous jr ra (+ delay slot)
                while w(a) == 0 and a < c:
                    a += 4
                return a
            a -= 4
        return c

    def near(addr):
        return any(0 <= e - addr < 0x80 for e in entries)

    tables = collections.defaultdict(set)
    for a in range(VA, 0x3E0000, 4):
        ins = w(a)
        op = ins >> 26
        if op == 9 and (ins >> 21) & 31 == 28:   # addiu reg, gp, imm
            lo = ins & 0xFFFF
            addr = GP + (lo - 0x10000 if lo & 0x8000 else lo)
            if near(addr):
                tables[function_start(a)].add(addr)
        elif op == 0x0F:                          # lui reg, hi ; addiu/ori reg, reg, lo
            rt, hi = (ins >> 16) & 31, ins & 0xFFFF
            for k in range(1, 6):
                j = w(a + 4 * k)
                if j >> 26 in (0x09, 0x0D) and (j >> 21) & 31 == rt:
                    lo = j & 0xFFFF
                    addr = (hi << 16) + (lo - 0x10000 if (j >> 26 == 9 and lo & 0x8000) else lo)
                    if near(addr):
                        tables[function_start(a)].add(addr)
                    break
    rooms: dict[str, set[int]] = collections.defaultdict(set)
    for slot in range(SLOTS):
        stub = FACTORY + 16 * slot
        lui, addiu = w(stub), w(stub + 4)
        lo = addiu & 0xFFFF
        vtable = ((lui & 0xFFFF) << 16) + (lo - 0x10000 if lo & 0x8000 else lo)
        room = slot_room(slot)
        for p in range(vtable, vtable + 0x40, 4):
            for addr in tables.get(w(p), ()):
                for e, name in entries.items():
                    if 0 <= e - addr < 0x80 and room is not None:
                        rooms[name].add(room)
    return {k: [f"ST_{r:03X}" for r in sorted(v)] for k, v in sorted(rooms.items())}


if __name__ == "__main__":
    exe = open(sys.argv[1], "rb").read()
    result = derive(exe)
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    print(f"{len(result)} events mapped")
