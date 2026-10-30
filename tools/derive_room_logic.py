"""Derive what each room's zones and cameras are for from SLUS_210.75.

    python tools/derive_room_logic.py path/to/SLUS_210.75 path/to/data hgexport/data/room_logic.json

The game keeps this in its room scripts, which are compiled into the
executable, so the exporter reads the committed result instead.

Method (all addresses for the NTSC-U executable, SHA-256 3b374d53...):

1. Each room's handler class (found through the room factory, see
   derive_event_rooms.py) returns its scripts from small accessors: enter
   (+0xC), phases 1-5 (+0x10 to +0x20), action table (+0x24), character entry
   (+0x30). Cutscenes are played by action scripts, so they are included.
2. Scripts are bytecode: F0-F5 + a condition, F7 else, F8/F9/FB end of block,
   FF end of script; action scripts end with command 06 followed by padding.
   Command lengths come from the game's skip function (jump table 0x4565F0),
   condition lengths from the table at 0x3D72C0.
3. Zones (PAC slot 2) are read by commands 27 (zone, set, path, direction) and
   29 (exit, set, path), and by conditions 01, 02, 03, 06 (character, zone).
   29 and condition 07 take an exit: its zone comes from the room map
   (0x3D8BC0). Conditions 35-37 test cylinders the scripts define themselves,
   not these zones.
4. Camera requests are (set, path) pairs: commands 27, 29 and 36 (character,
   set, path). Path 0xFF means a fixed camera, set 0xFF no camera. Every path
   used belongs to exactly one set. Two rooms request a path their file does
   not have, which the game's loader (0x219E10) ignores; such a request stays
   on the zone but gets no camera. Numbers a room's cameras cannot have come
   from data that follows a script without an end marker, and are dropped.
5. A command inside a block guarded by a zone is attributed to that zone
   (commands 01, 3A prefetch, 00 leave the room, 0A start an action). The else
   branch and negated conditions are not.
6. Rooms with the same zone table are variants of one map whose scripts use
   different parts of it, so a zone unused here may be used in a sibling.
"""
from __future__ import annotations

import collections
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from derive_event_rooms import FACTORY, GP, OFF, SIZE, SLOTS, VA, slot_room  # noqa: E402

from hgexport.formats.roomdata import parse_zones  # noqa: E402
from hgexport.formats.stage import parse_pac, parse_stage  # noqa: E402

CMD_TABLE, CMD_COUNT = 0x4565F0, 0xDB
COND_TABLE, COND_COUNT = 0x3D72C0, 0x66
ROOM_MAP, DOOR_TABLE, DOORS = 0x3D8BC0, 0x3DCFC0, 400
SCRIPTS = (0x3D8000, 0x4A0000)
SCRIPT_SLOTS = (("enter", 0xC), ("phase1", 0x10), ("phase2", 0x14), ("phase3", 0x18), ("phase4", 0x1C),
                ("phase5", 0x20), ("character_entry", 0x30))
ZONE_CONDITIONS = {1, 2, 3, 6}
NO_PATH = NO_SET = 0xFF


class Exe:
    def __init__(self, data: bytes):
        self.data = data

    def inside(self, a: int) -> bool:
        return VA <= a < VA + SIZE

    def byte(self, a: int) -> int:
        return self.data[a - VA + OFF]

    def word(self, a: int) -> int:
        return struct.unpack_from("<I", self.data, a - VA + OFF)[0]

    def returned_address(self, fn: int) -> int | None:
        """The address a small accessor builds (lui/addiu, or addiu from gp)."""
        high = None
        for k in range(8):
            ins = self.word(fn + 4 * k)
            op, imm = ins >> 26, ins & 0xFFFF
            low = imm - 0x10000 if imm & 0x8000 else imm
            if op == 0x0F and high is None:
                high = imm << 16
            elif op == 0x09 and (ins >> 21) & 31 == 28:
                return GP + low
            elif op == 0x09 and high is not None:
                return high + low
            if ins == 0x03E00008 and k > 3:
                return None
        return None


def is_script(a: int | None) -> bool:
    return a is not None and SCRIPTS[0] <= a < SCRIPTS[1]


def command_lengths(exe: Exe) -> dict:
    """Each case of the skip function adds the command's length to the pointer."""
    out = {}
    for op in range(CMD_COUNT):
        case = exe.word(CMD_TABLE + 4 * op)
        for k in range(6):
            ins = exe.word(case + 4 * k)
            if ins >> 26 == 0x09 and (ins >> 21) & 31 == (ins >> 16) & 31:
                out[op] = ins & 0xFFFF
                break
    return out


def room_scripts(exe: Exe, vtable: int) -> dict[str, int]:
    out = {}
    for name, off in SCRIPT_SLOTS:
        a = exe.returned_address(exe.word(vtable + off))
        if is_script(a):
            out[name] = a
    table = exe.returned_address(exe.word(vtable + 0x24))
    if is_script(table):
        for i in range(64):
            a = exe.word(table + 4 * i)
            if is_script(a):
                out[f"action{i}"] = a
            elif a:
                break
    return out


def decode(exe: Exe, lengths: dict, cond_lengths: bytes, va: int) -> list:
    """Script -> [(kind, op, args, guards)]; guards = [(condition, args)] that
    hold for this instruction: conditions of the enclosing blocks that are not
    negated and not in an else branch."""
    events = []
    stack: list[list] = []                       # block: [conditions, else?]
    pending: list = []
    end = va + 0x8000
    while va < end and exe.inside(va):
        op = exe.byte(va)
        if 0xF0 <= op <= 0xF5:
            cond = exe.byte(va + 1)
            if cond >= COND_COUNT:
                return events
            n = 3 + exe.byte(va + 3) if cond == 0x11 else cond_lengths[cond]
            args = bytes(exe.byte(va + 1 + k) for k in range(1, n))
            events.append(("cond", cond, args, []))
            pending.append((cond, args, op in (0xF1, 0xF3, 0xF5)))
            va += 1 + n
            if exe.byte(va) not in (0xF2, 0xF3, 0xF4, 0xF5):
                stack.append([pending, False])
                pending = []
            continue
        if op >= 0xF0:
            if op == 0xFF:
                return events
            if op == 0xF7 and stack:
                stack[-1][1] = not stack[-1][1]
            elif op in (0xF8, 0xF9, 0xFB) and stack:
                stack.pop()
            elif op not in (0xF6, 0xF7, 0xF8, 0xF9, 0xFA, 0xFB):
                return events
            va += 1
            continue
        n = lengths.get(op)
        if not n:
            return events
        if op in (0x22, 0x33):
            n = 3 + exe.byte(va + 2)
        guards = [(c, a) for conds, inverted in stack for c, a, negated in conds if negated == inverted]
        events.append(("cmd", op, bytes(exe.byte(va + k) for k in range(1, n)), guards))
        va += n
        if op == 6 and not stack:
            return events
    return events


def room_map(exe: Exe, room: int) -> list[int]:
    return [struct.unpack_from("<H", exe.data, ROOM_MAP + room * 0x40 + e * 8 + 6 - VA + OFF)[0] for e in range(8)]


def exit_destinations(exe: Exe) -> dict:
    out = collections.defaultdict(set)
    for i in range(DOORS):
        a, ea, b, eb = struct.unpack_from("<HB3xHB3x", exe.data, DOOR_TABLE + 16 * i - VA + OFF)
        if a < 0x110 and b < 0x110:
            out[(a, ea)].add(b)
            out[(b, eb)].add(a)
    return out


def add_unique(items: list, item) -> None:
    if item not in items:
        items.append(item)


def analyse_room(exe: Exe, lengths: dict, cond_lengths: bytes, room: int, vtable: int, zones: set[int],
                 exits: dict, rail_count: int, set_count: int) -> dict:
    uses: dict[int, dict] = collections.defaultdict(dict)
    rails: dict[int, set] = collections.defaultdict(set)
    fixed: set[int] = set()
    areas = room_map(exe, room)

    def camera(set_index: int, path: int) -> dict | None:
        if set_index == NO_SET or set_index >= set_count:
            return None
        if path == NO_PATH:
            fixed.add(set_index)
            return {"set": set_index, "rail": None}
        if path < rail_count:
            rails[path].add(set_index)
        return {"set": set_index, "rail": path}

    for e, area in enumerate(areas):
        if area in zones:
            add_unique(uses[area].setdefault("exits", []), e)
    for script, start in room_scripts(exe, vtable).items():
        for kind, op, args, guards in decode(exe, lengths, cond_lengths, start):
            if kind == "cond":
                if op in ZONE_CONDITIONS and len(args) >= 2 and args[1] in zones:
                    u = uses[args[1]]
                    add_unique(u.setdefault("characters", []), args[0])
                    add_unique(u.setdefault("scripts", []), script)
                continue
            if op == 0x27 and len(args) >= 4:
                entry = camera(args[1], args[2])
                if entry and args[0] in zones:
                    add_unique(uses[args[0]].setdefault("cameras", []),
                               {**entry, "direction": 1 if args[3] < 0x80 else -1})
                    add_unique(uses[args[0]].setdefault("scripts", []), script)
            elif op == 0x29 and len(args) >= 3:
                entry = camera(args[1], args[2])
                if entry and args[0] < 8 and areas[args[0]] in zones:
                    add_unique(uses[areas[args[0]]].setdefault("cameras", []), entry)
                    add_unique(uses[areas[args[0]]].setdefault("scripts", []), script)
            elif op == 0x36 and len(args) >= 3:
                camera(args[1], args[2])
            for cond, cargs in guards:
                if cond not in ZONE_CONDITIONS or len(cargs) < 2 or cargs[1] not in zones:
                    continue
                u = uses[cargs[1]]
                if op == 0x01 and args:
                    add_unique(u.setdefault("prefetch", []), {"exit": args[0]})
                elif op == 0x3A and len(args) >= 2:
                    add_unique(u.setdefault("prefetch", []), {"room": f"ST_{args[0] << 8 | args[1]:03X}"})
                elif op == 0x00 and args:
                    add_unique(u.setdefault("exits", []), args[0])
                elif op == 0x0A and len(args) >= 2:
                    add_unique(u.setdefault("actions", []), args[1])
                    if cond == 6:
                        u["facing"] = True
    result: dict = {"zones": {}}
    for zone in sorted(uses):
        u = uses[zone]
        if "exits" in u:
            u["exits"] = [{"exit": e, "leads_to": sorted(f"ST_{r:03X}" for r in exits.get((room, e), ()))}
                          for e in sorted(u["exits"])]
        result["zones"][str(zone)] = {k: sorted(v) if k in ("characters",) else v for k, v in u.items()}
    pairs = {}
    for rail, sets in sorted(rails.items()):
        if len(sets) != 1:
            print(f"ST_{room:03X}: rail {rail} is used with sets {sorted(sets)}")
        pairs[str(rail)] = min(sets)
    result["camera_rails"] = pairs
    result["camera_fixed_sets"] = sorted(fixed)
    return result


def zone_key(z) -> tuple:
    return z.kind, round(z.height, 2), tuple(tuple(round(c, 2) for c in p) for p in z.points)


def derive(exe_bytes: bytes, data_root: Path) -> dict:
    exe = Exe(exe_bytes)
    lengths = command_lengths(exe)
    cond_lengths = exe_bytes[COND_TABLE - VA + OFF:COND_TABLE - VA + OFF + COND_COUNT]
    exits = exit_destinations(exe)
    zones, cameras = {}, {}
    for pac in sorted(data_root.glob("ST_*/ST_*.PAC")):
        room = int(pac.stem[3:], 16)
        zones[room] = {z.index: z for z in parse_zones(parse_pac(pac))}
        stage = parse_stage(pac)
        cameras[room] = len(stage.cameras), len(stage.camera_sets)
    rooms: dict[str, dict] = {}
    for slot in range(SLOTS):
        room = slot_room(slot)
        if room not in zones:
            continue
        stub = FACTORY + 16 * slot
        lo = exe.word(stub + 4) & 0xFFFF
        vtable = ((exe.word(stub) & 0xFFFF) << 16) + (lo - 0x10000 if lo & 0x8000 else lo)
        rooms[f"ST_{room:03X}"] = analyse_room(exe, lengths, cond_lengths, room, vtable, set(zones[room]), exits,
                                               *cameras[room])
    twins = collections.defaultdict(list)
    for room, table in zones.items():
        for index, z in table.items():
            twins[zone_key(z)].append((f"ST_{room:03X}", index))
    for room, table in zones.items():
        name = f"ST_{room:03X}"
        if name not in rooms:
            continue
        for index, z in table.items():
            if str(index) in rooms[name]["zones"]:
                continue
            used = [{"room": r, "zone": i} for r, i in twins[zone_key(z)]
                    if r != name and str(i) in rooms[r]["zones"] and "used_in" not in rooms[r]["zones"][str(i)]]
            if used:
                rooms[name]["zones"][str(index)] = {"used_in": used}
    return {k: rooms[k] for k in sorted(rooms)}


def write(result: dict, out: Path) -> None:
    lines = [f" {json.dumps(k)}: {json.dumps(v, separators=(',', ':'))}" for k, v in result.items()]
    out.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8")


if __name__ == "__main__":
    result = derive(open(sys.argv[1], "rb").read(), Path(sys.argv[2]))
    write(result, Path(sys.argv[3]))
    used = sum(1 for r in result.values() for z in r["zones"].values() if "used_in" not in z)
    siblings = sum(1 for r in result.values() for z in r["zones"].values() if "used_in" in z)
    print(f"{len(result)} rooms, {used} zones used, {siblings} used only in a sibling room")
