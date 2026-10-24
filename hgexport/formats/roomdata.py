"""Room PAC slots 1, 2, 11, 13, 14 and 15 (the gameplay-side room data).

The room loader 0x11FFB0 hands each slot to its consumer:

* slot 1 -> walkable-floor setup 0x17CC00 (with slots 0 and 16): u32 count,
  12 pad bytes, 16-byte wall records ``u32 floor triangle, u32 edge, u32
  vertex a, u32 vertex b`` (b = a + 1 mod 3; the edge whose neighbour is -1):
  the room's wall edges. Floor method 0x17B9B0 (vtable 0x46A9D0 +72)
  intersects a segment with every wall edge (vertex a -> b, in the ground
  plane), checks the height on the triangle and returns the nearest wall
  crossed.
* slot 2 -> trigger manager (global 0x44E4D0): a directory of u32 offsets in
  4-byte words (the smallest offset is the directory length; index = the
  number room scripts use), then records ``u32 type, 0, f32 height, 0`` and
  float4 points from +16. Type 0 = boundary line (2 points; scripts test for
  crossing, 0x1FC390), type 1 = area (4 points; inside tests 0x1FC030 /
  0x1FC210). Example: EV0002 starts when the player crosses boundary 14 of
  ST_021's family.
* slot 11 -> 64-entry table of class 0x46B1D0 (method 0x1F4420): u32 count,
  16-byte records from +16, registered from index 21. Every shipped room has
  count 0 (own data or aliasing slot 12), so it holds nothing.
* slot 13 -> effects manager (0x266CD0): offsets at +8, +12, +16, +20 to the
  parameters of effects 31, 30, 29 (fog) and 28 (depth effect).
* slot 14 -> floor-region switcher (0x2A88A0 / 0x2A8740): u32 group count,
  u32 offsets (relative to the slot), groups = u32 count + u32 floor
  triangle indices. Scripts set or clear flag bits on a whole group (e.g.
  regions closed off until a door opens).
* slot 15 -> named-object manager (with slot 12): u32 count, u32 offsets to
  entries ``char name[16], u32 channels, u32 table offset`` (relative to the
  entry start); channel ``u32 frames, u32 type, u32 data offset`` (relative to the
  channel); per frame type 7 = Euler XYZ + position (6 f32), 8 = Euler XYZ,
  9 = position. Values are absolute object transforms, one per game update.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..util.binary import FormatError
from .stage import Pac


def _region(pac: Pac, slot: int) -> bytes:
    reg = pac.region(slot)
    return pac.data[reg[0]:reg[1]] if reg else b""


@dataclass
class Zone:
    index: int                       # directory index used by room scripts
    kind: str                        # "boundary" (line) or "area" (quad)
    height: float
    points: list[tuple[float, float, float]]


def parse_zones(pac: Pac) -> list[Zone]:
    data = _region(pac, 2)
    if len(data) < 4:
        return []
    first = struct.unpack_from("<I", data, 0)[0]
    if not first or first * 4 > len(data):
        raise FormatError(f"{pac.name}: invalid slot-2 directory")
    count = first
    for i in range(first):
        v = struct.unpack_from("<I", data, 4 * i)[0]
        if v:
            count = min(count, v)
    out = []
    for i in range(count):
        word = struct.unpack_from("<I", data, 4 * i)[0]
        if not word:
            continue
        at = word * 4
        kind = struct.unpack_from("<I", data, at)[0]
        npts = {0: 2, 1: 4}.get(kind)
        if npts is None or at + 16 + 16 * npts > len(data):
            raise FormatError(f"{pac.name}: slot-2 record {i} has type {kind}")
        height = struct.unpack_from("<f", data, at + 8)[0]
        pts = [struct.unpack_from("<3f", data, at + 16 + 16 * k) for k in range(npts)]
        out.append(Zone(i, "boundary" if kind == 0 else "area", height, pts))
    return out


def parse_floor_groups(pac: Pac) -> list[list[int]]:
    data = _region(pac, 14)
    if len(data) < 4:
        return []
    count = struct.unpack_from("<I", data, 0)[0]
    if count > 4096 or 4 + 4 * count > len(data):
        raise FormatError(f"{pac.name}: invalid slot-14 group count")
    groups = []
    for off in struct.unpack_from(f"<{count}I", data, 4):
        n = struct.unpack_from("<I", data, off)[0]
        groups.append(list(struct.unpack_from(f"<{n}I", data, off + 4)))
    return groups


def parse_walls(pac: Pac) -> list[tuple[int, int, int, int]]:
    """Slot 1 wall edges: (floor triangle, edge, vertex a, vertex b)."""
    return parse_floor_aux(pac)


def parse_floor_aux(pac: Pac) -> list[tuple[int, int, int, int]]:
    data = _region(pac, 1)
    if len(data) < 16:
        return []
    count = struct.unpack_from("<I", data, 0)[0]
    if 16 + 16 * count > len(data):
        raise FormatError(f"{pac.name}: invalid slot-1 count")
    return [struct.unpack_from("<4I", data, 16 + 16 * i) for i in range(count)]


def _rgba(b: bytes) -> list[int]:
    return [b[0], b[1], b[2], b[3]]


def parse_room_effects(pac: Pac) -> dict:
    """Slot 13 effect parameters: the tint (effect 31), the bloom (effect 30),
    the fog and the depth effect (same layouts as the cutscene PARAMS.BIN
    effects 29 and 28). See export/look.py for how the game draws them."""
    # Some rooms' parameter blocks lie past the next slot's start, so read
    # from the slot start to the end of the file.
    if not pac.slots[13]:
        return {}
    data = pac.data[pac.slots[13]:] + bytes(32)     # blocks may end at end of file
    if len(data) < 56:
        return {}
    o31, o30, o29, o28 = struct.unpack_from("<4I", data, 8)
    out: dict = {}
    if o31:
        b = data[o31:o31 + 9]
        out["tint"] = {"colors": [_rgba(b[0:4]), _rgba(b[4:8])], "mode": b[8]}
    if o30:
        b = data[o30:o30 + 5]
        out["bloom"] = {"color": _rgba(b[0:4]), "mode": b[4]}
    if o29:
        b = data[o29:o29 + 26]
        out["fog"] = {"colors": [_rgba(b[0:4]), _rgba(b[4:8])],
                      "start": round(struct.unpack_from("<f", b, 8)[0], 4),
                      "end": round(struct.unpack_from("<f", b, 12)[0], 4),
                      "word": struct.unpack_from("<I", b, 16)[0], "bytes": list(b[20:26])}
    if o28:
        out["depth_effect"] = {"distances": [round(v, 4) for v in struct.unpack_from("<4f", data, o28)]}
    return out


@dataclass
class ObjectChannel:
    kind: str                        # "rotation+translation", "rotation", "translation"
    frames: list[tuple]              # per frame: (euler3, pos3) / euler3 / pos3


@dataclass
class ObjectMotion:
    name: str
    channels: list[ObjectChannel] = field(default_factory=list)


_CHANNEL = {7: ("rotation+translation", 6), 8: ("rotation", 3), 9: ("translation", 3)}


def parse_object_motion(pac: Pac) -> list[ObjectMotion]:
    data = _region(pac, 15)
    if len(data) < 4:
        return []
    count = struct.unpack_from("<I", data, 0)[0]
    if count > 256 or 4 + 4 * count > len(data):
        raise FormatError(f"{pac.name}: invalid slot-15 count")
    out = []
    for off in struct.unpack_from(f"<{count}I", data, 4):
        name = data[off:off + 16].split(b"\0")[0].decode("ascii", "replace")
        nch, table = struct.unpack_from("<2I", data, off + 16)
        motion = ObjectMotion(name)
        for c in range(nch):
            at = off + table + 16 * c
            frames, kind, rel = struct.unpack_from("<3I", data, at)
            if kind not in _CHANNEL:
                raise FormatError(f"{pac.name}: {name} channel {c} has type {kind}")
            label, width = _CHANNEL[kind]
            start = at + rel
            if start + frames * width * 4 > len(data):
                raise FormatError(f"{pac.name}: {name} channel {c} overruns slot 15")
            values = [struct.unpack_from(f"<{width}f", data, start + i * width * 4) for i in range(frames)]
            if width == 6:
                values = [(v[0:3], v[3:6]) for v in values]
            motion.channels.append(ObjectChannel(label, values))
        out.append(motion)
    return out
