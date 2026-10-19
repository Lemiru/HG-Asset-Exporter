"""Stage PAC files: room geometry, doors, named objects, textures, cameras.

A PAC begins with a 0x800-byte table of u32 slot offsets (0 = empty; equal
offsets alias one region; a region ends at the next distinct offset). Slots:

    0 navigation triangles   1 navigation extras   2 event regions
    3 main mesh pack         4/5 lights            6 gameplay cameras
    7 door placements        8 door meshes         9 textures
    10 messages              12 named objects      16 navigation flags

Draw record (main, door and rigid named objects)::

    i32 vertex_count, i32 material, u32 control, i32 fourth
    f32[16] matrix (row-major, translation in elements 3/7/11)
    vertex_count x (f32 u, f32 v)             -> 16-byte aligned
    vertex_count x (u8 r, g, b, a)            -> 16-byte aligned  (0x80 = 1.0)
    vertex_count x (f32 x, y, z, u32 w)       w bit 15 = strip restart

The control word's top byte is a visibility selector (0 = always drawn,
1..127 = toggled by event scripts) and bit 0 enables alpha blending.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

from ..util.binary import FormatError, Reader, align
from .tex import IndexedImage, parse_stage_texture_pack

PAC_HEADER = 0x800


@dataclass
class Pac:
    name: str
    data: bytes
    slots: list[int]

    def region(self, slot: int) -> tuple[int, int] | None:
        start = self.slots[slot]
        if not start:
            return None
        later = [o for o in set(self.slots) if o > start]
        return start, (min(later) if later else len(self.data))


def parse_pac_bytes(data: bytes, name: str = "PAC") -> Pac:
    if len(data) < PAC_HEADER:
        raise FormatError(f"{name}: shorter than the PAC header")
    r = Reader(data, name)
    slots = list(r.unpack(f"{PAC_HEADER // 4}I", 0))
    for o in slots:
        if o and not PAC_HEADER <= o <= len(data):
            raise FormatError(f"{name}: slot offset 0x{o:X} out of range")
    return Pac(name, data, slots)


def parse_pac(path: Path | str) -> Pac:
    p = Path(path)
    return parse_pac_bytes(p.read_bytes(), p.stem)


@dataclass
class DrawRecord:
    vertex_count: int
    material: int
    control: int
    fourth: int
    matrix: tuple[float, ...]
    uvs: list[tuple[float, float]]
    colors: list[tuple[int, int, int, int]]
    positions: list[tuple[float, float, float]]   # local (matrix not applied)
    restart: bytes
    offset: int
    extra: dict = field(default_factory=dict)

    @property
    def selector(self) -> int:
        return (self.control >> 24) & 0xFF

    @property
    def blend(self) -> bool:
        return bool(self.control & 1)

    def world_positions(self) -> list[tuple[float, float, float]]:
        m = self.matrix
        return [(m[0] * x + m[1] * y + m[2] * z + m[3],
                 m[4] * x + m[5] * y + m[6] * z + m[7],
                 m[8] * x + m[9] * y + m[10] * z + m[11]) for x, y, z in self.positions]


def _streams(r: Reader, at: int, count: int, uv_at: int):
    uvs = r.iter_unpack("2f", uv_at, count, "UVs")
    col_at = align(uv_at + count * 8)
    colors = r.iter_unpack("4B", col_at, count, "colours")
    pos_at = align(col_at + count * 4)
    raw = r.iter_unpack("3fI", pos_at, count, "positions")
    positions = [(x, y, z) for x, y, z, _w in raw]
    restart = bytes(1 if w & 0x8000 else 0 for _x, _y, _z, w in raw)
    if not all(math.isfinite(c) for p in positions for c in p):
        raise FormatError(f"{r.name}: non-finite vertex at 0x{at:X}")
    return uvs, colors, positions, restart, pos_at + count * 16


def read_draw_record(r: Reader, at: int) -> tuple[DrawRecord, int]:
    count, material, control, fourth = r.unpack("iiIi", at, "draw header")
    if not 0 <= count <= 200000:
        raise FormatError(f"{r.name}: invalid vertex count {count} at 0x{at:X}")
    matrix = r.unpack("16f", at + 16, "draw matrix")
    uvs, colors, positions, restart, end = _streams(r, at, count, at + 80)
    return DrawRecord(count, material, control, fourth, matrix, uvs, colors, positions, restart, at), end


@dataclass
class Door:
    index: int
    records: list[DrawRecord]
    position: tuple[float, float, float] | None
    euler: tuple[float, float, float] | None
    resource_id: int | None
    secondary_position: tuple[float, float, float] | None = None


@dataclass
class NamedObject:
    index: int
    category: int
    name: str
    euler: tuple[float, float, float]
    translation: tuple[float, float, float]
    record: DrawRecord
    shapes: list[list[tuple[float, float, float]]] | None = None   # category 3: two endpoints
    shape_colors: list[list[tuple[int, int, int, int]]] | None = None

    @property
    def blend(self) -> bool:
        # Named objects choose blending by name prefix "a_" (0x25FB10).
        return self.name.lower().startswith("a_")


@dataclass
class CameraCurve:
    index: int
    keys: list[list[tuple]]     # curves x key records (6 floats, 2 words)
    fov_degrees: float = 60.0   # from slot 5 (horizontal field of view)

    def sample(self, curve: int, p: float) -> float:
        """Evaluate one cubic Bezier curve at progress parameter ``p``. Keys
        store (parameter, value, in-handle d_param, d_value, out-handle
        d_param, d_value, mode, unused)."""
        keys = self.keys[curve]
        if p <= keys[0][0]:
            return keys[0][1]
        for a, b in zip(keys, keys[1:]):
            if p <= b[0]:
                x0, y0, x1, y1 = a[0], a[1], a[0] + a[4], a[1] + a[5]
                x2, y2, x3, y3 = b[0] + b[2], b[1] + b[3], b[0], b[1]
                lo, hi = 0.0, 1.0
                for _ in range(40):
                    u = (lo + hi) / 2
                    x = (1 - u) ** 3 * x0 + 3 * (1 - u) ** 2 * u * x1 + 3 * (1 - u) * u * u * x2 + u ** 3 * x3
                    lo, hi = (u, hi) if x < p else (lo, u)
                u = (lo + hi) / 2
                return (1 - u) ** 3 * y0 + 3 * (1 - u) ** 2 * u * y1 + 3 * (1 - u) * u * u * y2 + u ** 3 * y3
        return keys[-1][1]


@dataclass
class Stage:
    name: str
    groups: dict[int, list[DrawRecord]]
    doors: list[Door]
    objects: list[NamedObject]
    textures: list[IndexedImage]
    cameras: list[CameraCurve]
    lighting: "RoomLighting | None" = None                 # slot 4 (character lighting)
    effects: dict = field(default_factory=dict)           # slot 13 (formats/roomdata.py)
    object_motion: list = field(default_factory=list)     # slot 15 (formats/roomdata.py)


def _main_groups(pac: Pac) -> dict[int, list[DrawRecord]]:
    reg = pac.region(3)
    if not reg:
        return {}
    start, end = reg
    r = Reader(pac.data, pac.name, limit=end)
    rel = r.unpack("6I", start, "mesh group table")
    active = sorted({v for v in rel if v})
    groups = {}
    for g, v in enumerate(rel):
        if not v:
            continue
        nxt = [x for x in active if x > v]
        limit = start + nxt[0] if nxt else end
        gr = Reader(pac.data, pac.name, limit=limit)
        at = start + v
        records = []
        while True:
            a, b = gr.unpack("ii", at, "group terminator")
            if a == -1 and b == -1:
                break
            rec, at = read_draw_record(gr, at)
            records.append(rec)
            if len(records) > 100000:
                raise FormatError(f"{pac.name}: unterminated mesh group {g}")
        groups[g] = records
    return groups


def _doors(pac: Pac) -> list[Door]:
    reg8 = pac.region(8)
    if not reg8:
        return []
    start, end = reg8
    r = Reader(pac.data, pac.name, limit=end)
    directory = r.unpack("8i", start, "door directory")
    # Door i starts at pack + directory[i] + 16*i (resolver 0x222F60); -1 = absent.
    starts = [(i, start + d + i * 16) for i, d in enumerate(directory) if d >= 0]
    placements: dict[int, tuple] = {}
    reg7 = pac.region(7)
    if reg7:
        s7, e7 = reg7
        r7 = Reader(pac.data, pac.name, limit=e7)
        for i, rel in enumerate(r7.unpack("8I", s7, "door placement directory")):
            if rel:
                at = s7 + rel
                rid = r7.i32(at)
                pos = r7.unpack("3f", at + 4)
                pos2 = r7.unpack("3f", at + 0x10)
                eul = r7.unpack("3f", at + 0x1C)
                placements[i] = (rid, pos, eul, pos2)
    doors = []
    for i, at in starts:
        records = []
        while True:
            a, b = r.unpack("ii", at, "door terminator")
            if a == -1 and b == -1:
                break
            rec, at = read_draw_record(r, at)
            records.append(rec)
        p = placements.get(i)
        doors.append(Door(i, records, p[1] if p else None, p[2] if p else None,
                          p[0] if p else None, p[3] if p else None))
    return doors


def _shape_object(r: Reader, at: int, header: tuple) -> tuple[DrawRecord, list, list]:
    """Category-3 objects: two packed shape endpoints (int16 positions /128
    plus an int32 origin, RGBA colours per endpoint), int16 UVs /32768."""
    count, material, control, uv_rel, strip_rel, nshapes = r.unpack("6I", at + 0x40)
    if nshapes != 2 or not 3 <= count <= 200000:
        raise FormatError(f"{r.name}: unsupported shape object at 0x{at:X}")
    origin = r.unpack("3i", at + 0x60)
    ptrs = r.unpack("4I", at + 0x70)
    uvs = [(u / 32768.0, v / 32768.0) for u, v in r.iter_unpack("2H", at + uv_rel, count, "shape UVs")]
    restart = bytes(1 if b else 0 for b in r.bytes(at + strip_rel, count, "shape strip"))
    shapes, colors = [], []
    for k in range(2):
        col_at, pos_at = at + ptrs[k * 2], at + ptrs[k * 2 + 1]
        colors.append(r.iter_unpack("4B", col_at, count, "shape colours"))
        shapes.append([tuple((c + o) / 128.0 for c, o in zip(p, origin))
                       for p in r.iter_unpack("3h", pos_at, count, "shape positions")])
    identity = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
    rec = DrawRecord(count, material, control, 0, identity, uvs, colors[0], shapes[0], restart, at)
    return rec, shapes, colors


def _objects(pac: Pac) -> list[NamedObject]:
    reg = pac.region(12)
    if not reg:
        return []
    start, end = reg
    r = Reader(pac.data, pac.name, limit=end)
    counts = r.unpack("4I", start, "object category counts")
    total = sum(counts)
    if total > 4096:
        raise FormatError(f"{pac.name}: implausible named object count")
    offsets = r.unpack(f"{total}I", start + 32) if total else ()
    out = []
    bounds = list(offsets) + [end - start]
    category_of = [c for c, n in enumerate(counts) for _ in range(n)]
    for i, rel in enumerate(offsets):
        at = start + rel
        orr = Reader(pac.data, pac.name, limit=start + bounds[i + 1])
        name = orr.cstring(at, 16)
        euler = orr.unpack("3f", at + 0x10)
        trans = orr.unpack("3f", at + 0x20)
        cat = category_of[i]
        if cat == 3:
            rec, shapes, colors = _shape_object(orr, at, ())
            out.append(NamedObject(i, cat, name, euler, trans, rec, shapes, colors))
        else:
            count, material, control, uv_rel, _col_rel, _pos_rel = orr.unpack("3i3I", at + 0x40)
            identity = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
            uvs, colors, positions, restart, _end = _streams(orr, at, count, at + uv_rel)
            rec = DrawRecord(count, material, control & 0xFFFFFFFF, 0, identity, uvs, colors, positions,
                             restart, at)
            out.append(NamedObject(i, cat, name, euler, trans, rec))
    return out


def _cameras(pac: Pac) -> list[CameraCurve]:
    """Slot 6: camera count, reserved word, (curve_count, key_count) pairs,
    then (16-byte aligned) curve-major 32-byte cubic keys: parameter, value,
    in/out handle deltas (4 floats), mode, unknown. Every shipped camera has six
    curves (position xyz, target xyz) of eleven keys."""
    reg = pac.region(6)
    if not reg:
        return []
    start, end = reg
    r = Reader(pac.data, pac.name, limit=end)
    count = r.u32(start)
    if count > 256:
        raise FormatError(f"{pac.name}: implausible camera count")
    descs = r.iter_unpack("II", start + 8, count, "camera descriptors")
    at = align(start + 8 + count * 8)
    cams = []
    for i, (curves, keys) in enumerate(descs):
        rows = r.iter_unpack("6f2I", at, curves * keys, "camera keys")
        cams.append(CameraCurve(i, [rows[c * keys:(c + 1) * keys] for c in range(curves)]))
        at += curves * keys * 32
    return cams


def parse_stage(path: Path | str) -> Stage:
    pac = parse_pac(path)
    tex_reg = pac.region(9)
    textures = parse_stage_texture_pack(pac.data, *tex_reg, pac.name) if tex_reg else []
    try:
        cameras = _cameras(pac)
    except FormatError:
        cameras = []
    # Slot 5: per camera start position, field of view (degrees), target, 0.
    reg5 = pac.region(5)
    if reg5 and cameras:
        r5 = Reader(pac.data, pac.name, limit=reg5[1])
        for i, cam in enumerate(cameras):
            if reg5[0] + i * 32 + 32 <= reg5[1]:
                fov = r5.f32(reg5[0] + i * 32 + 12)
                if 5.0 < fov < 170.0:
                    cam.fov_degrees = fov
    lighting = parse_room_lighting(pac)
    from .roomdata import parse_object_motion, parse_room_effects
    return Stage(pac.name, _main_groups(pac), _doors(pac), _objects(pac), textures, cameras, lighting,
                 parse_room_effects(pac), parse_object_motion(pac))


@dataclass
class RoomLight:
    index: int
    position: tuple[float, float, float]      # game units (directional: far point the light comes from)
    color: tuple[float, float, float]         # 0-255 scale
    range: float                              # 0 = directional (no falloff)
    intensity: float                          # colour multiplier (shading 0x1FABA0, selection 0x1F9B48)
    param: float                              # never read by the game (0, 30..70)

    @property
    def directional(self) -> bool:
        return self.range == 0.0


@dataclass
class RoomLighting:
    ambient: tuple[float, float, float]       # 0-255 scale
    lights: list[RoomLight]


def parse_room_lighting(pac: Pac) -> RoomLighting | None:
    """Slot 4, installed by the lighting object's method 0x1FAF70: u32 count,
    f32 ambient RGB, then ``count`` 48-byte lights (f32 x, y, z, 1; f32 R, G,
    B, intensity; f32 range, 1/range, range squared, param). Characters are
    lit by the ambient plus the three strongest lights enabled for the floor
    triangle they stand on (navigation triangle +76 bit mask; 0x1F99B0):
    strength = (0.3 R + 0.6 G + 0.1 B) x intensity x (range - distance) /
    range, and range 0 = no falloff (directional). The shading (0x1FAA00)
    uses RGB x intensity; range squared and param are never read."""
    reg = pac.region(4)
    if not reg or reg[1] - reg[0] < 16:
        return None
    count = struct.unpack_from("<I", pac.data, reg[0])[0]
    if count > 16 or reg[0] + 16 + 48 * count > reg[1]:
        raise FormatError(f"{pac.name}: invalid slot-4 light count {count}")
    ambient = struct.unpack_from("<3f", pac.data, reg[0] + 4)
    lights = []
    for i in range(count):
        v = struct.unpack_from("<12f", pac.data, reg[0] + 16 + 48 * i)
        lights.append(RoomLight(i, v[0:3], v[4:7], v[8], v[7], v[11]))
    return RoomLighting(ambient, lights)


def parse_door_motion(path: Path | str) -> list[list[float]]:
    """``*_D000.MTN`` door-angle banks: count, offsets, then per curve three
    unused floats, a sample count and that many yaw angles in degrees (one per
    game update), added to the door's initial yaw by update routine 0x2237E0.
    Even curves open (0 -> -90), odd curves close."""
    p = Path(path)
    data = p.read_bytes()
    r = Reader(data, p.name)
    count = r.u32(0)
    if not 1 <= count <= 256:
        raise FormatError(f"{p.name}: invalid door curve count")
    offsets = list(r.unpack(f"{count}I", 4)) + [len(data)]
    curves = []
    for i in range(count):
        start, end = offsets[i], offsets[i + 1]
        n = r.u32(start + 12)
        if start + 16 + n * 4 != end:
            raise FormatError(f"{p.name}: door curve {i} size mismatch")
        curves.append(list(r.unpack(f"{n}f", start + 16)))
    return curves


def parse_curve_bank(path: Path | str) -> list[list[float]]:
    """``OBSTACLE.MTN``: count, offsets, then per curve a sample count and that
    many floats (one per game update). Played by the curve-channel module whose
    method table is at 0x46C328 (loader 0x21AF90, start 0x21A850)."""
    p = Path(path)
    data = p.read_bytes()
    r = Reader(data, p.name)
    count = r.u32(0)
    if not 1 <= count <= 256:
        raise FormatError(f"{p.name}: invalid curve count")
    offsets = list(r.unpack(f"{count}I", 4)) + [len(data)]
    curves = []
    for i in range(count):
        start, end = offsets[i], offsets[i + 1]
        n = r.u32(start)
        if start + 4 + n * 4 != end:
            raise FormatError(f"{p.name}: curve {i} size mismatch")
        curves.append(list(r.unpack(f"{n}f", start + 4)))
    return curves


@dataclass
class NavTriangle:
    vertices: tuple[tuple[float, float, float], ...]
    neighbors: tuple[int, int, int]
    flags: int
    override: int
    light_mask: int = 0      # bit i: room light i may light a character standing here


def parse_navigation(pac: Pac) -> list[NavTriangle]:
    """Slot 0 walkable-floor graph: u32 count, 12 pad bytes, then 80-byte
    triangles (three float4 vertices, three i32 neighbour triangles (-1 =
    edge), u32 flags, three centroid distances, u32). Slot 16 holds one
    override byte per triangle (missing tail = 0)."""
    reg = pac.region(0)
    if not reg:
        return []
    start, end = reg
    r = Reader(pac.data, pac.name, limit=end)
    count = r.u32(start)
    out = []
    reg16 = pac.region(16)
    overrides = pac.data[reg16[0]:reg16[1]] if reg16 else b""
    for i in range(count):
        at = start + 16 + i * 80
        v = r.unpack("12f", at, "navigation triangle")
        n = r.unpack("3i", at + 48)
        flags = r.u32(at + 60)
        out.append(NavTriangle((v[0:3], v[4:7], v[8:11]), n, flags, overrides[i] if i < len(overrides) else 0,
                               r.u32(at + 76)))
    return out
