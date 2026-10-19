"""PCK character/object packages: skeleton, meshes, morph shapes, animations.

Header (u32): +0x04 model, +0x08 morph section, +0x0C shadow-volume section,
+0x10 motion bank (see motion.py). Model block::

    +0x00 bone_count   +0x04 skinned table   +0x08 rigid table   +0x0C palette
    +0x10 bone_count x 0x70 bone records:
          +0x00 parent (i32, -1 = root)  +0x10 Euler XYZ  +0x20 translation
          +0x30 inverse bind matrix (4x4, glTF column-major layout)

Positions are fixed point with 12 fractional bits (/4096). Base meshes store
an int32 start position followed by cumulative int16 deltas. All values were
checked against the Noesis reference exports (exact positions/UVs/bones).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from ..util.binary import FormatError, Reader
from .motion import MotionBank, parse_motion_bank

FIXED12 = 1.0 / 4096.0
SNORM15 = 1.0 / 32768.0


@dataclass
class Bone:
    index: int
    parent: int
    euler: tuple[float, float, float]
    translation: tuple[float, float, float]
    inverse_bind: tuple[float, ...]          # 16 floats, column-major


@dataclass
class BaseMesh:
    kind: str                                 # "skinned" or "rigid"
    index: int
    material: int
    render_flags: int
    positions: list[tuple[int, int, int]]     # fixed-point grid units
    normals: list[tuple[float, float, float]]
    uvs: list[tuple[float, float]]
    restart: bytes                            # 1 = strip restart marker
    joints: list[tuple[int, ...]] = field(default_factory=list)   # skeleton bone indices
    weights: list[tuple[float, ...]] = field(default_factory=list)
    parent_bone: int = 0

    @property
    def blend(self) -> bool:
        """Render flag bit 0 enables GS alpha blending (else alpha test only)."""
        return bool(self.render_flags & 1)


@dataclass
class MorphMesh:
    """Rigidly bound blend-shape geometry (faces, hands, teeth...)."""
    index: int
    parent_bone: int
    material: int
    topology_mode: int                        # 0: byte markers, 1: i32 sign markers (faces)
    render_flags: int
    uvs: list[tuple[float, float]]
    restart: bytes
    origin: tuple[int, int, int]              # fixed-point grid units
    shapes: list[list[tuple[int, int, int]]]  # per shape: local int16 positions
    shape_normals: list[list[tuple[float, float, float]]]

    @property
    def blend(self) -> bool:
        return bool(self.render_flags & 1)

    def shape_positions(self, shape: int) -> list[tuple[int, int, int]]:
        ox, oy, oz = self.origin
        return [(x + ox, y + oy, z + oz) for x, y, z in self.shapes[shape]]


@dataclass
class PckModel:
    name: str
    bones: list[Bone]
    palette: list[int]                        # animation target -> bone index
    meshes: list[BaseMesh]
    morphs: list[MorphMesh]
    motion: MotionBank | None
    data: bytes = field(repr=False, default=b"")

    @property
    def has_geometry(self) -> bool:
        return bool(self.meshes or self.morphs)


def strip_triangles(restart: bytes | list[bool]) -> list[tuple[int, int, int]]:
    """Expand a PS2 triangle strip with restart markers into CCW triangles.

    A marked vertex starts a new strip (the first two vertices of every strip
    carry the marker). Winding alternates within a strip and starts as
    (v-2, v-1, v) after each restart; this agrees with the stored vertex
    normals on 99.9 % of the 329k triangles in the PCK corpus.
    """
    tris = []
    first = False
    forward = True
    for v, marked in enumerate(restart):
        if marked:
            first = True
            continue
        if first:
            first = False
            forward = True
        if v >= 2:
            tris.append((v - 2, v - 1, v) if forward else (v, v - 1, v - 2))
        forward = not forward
    return tris


def _positions(r: Reader, at: int, count: int) -> list[tuple[int, int, int]]:
    x, y, z = r.unpack("3i", at, "position base")
    out = []
    for dx, dy, dz in r.iter_unpack("3h", at + 16, count, "position deltas"):
        x += dx
        y += dy
        z += dz
        out.append((x, y, z))
    return out


def _uvs(r: Reader, at: int, count: int) -> list[tuple[float, float]]:
    return [(u * SNORM15, v * SNORM15) for u, v in r.iter_unpack("2h", at, count, "UVs")]


def parse_pck_bytes(data: bytes, name: str = "PCK") -> PckModel:
    r = Reader(data, name)
    model = r.u32(4)
    bone_count, skinned_rel, rigid_rel, palette_rel = r.unpack("4I", model, "model header")
    if bone_count == 0 or bone_count > 512:
        raise FormatError(f"{name}: implausible bone count {bone_count}")
    bones = []
    for b in range(bone_count):
        at = model + 0x10 + b * 0x70
        parent = r.i32(at)
        if not -1 <= parent < b:
            raise FormatError(f"{name}: bone {b} has invalid parent {parent}")
        euler = r.unpack("3f", at + 0x10)
        trans = r.unpack("3f", at + 0x20)
        ibm = r.unpack("16f", at + 0x30)
        bones.append(Bone(b, parent, euler, trans, ibm))
    pal_at = model + palette_rel
    palette = list(r.bytes(pal_at + 1, r.u8(pal_at), "bone palette"))
    if any(p >= bone_count for p in palette):
        raise FormatError(f"{name}: bone palette references a missing bone")

    meshes: list[BaseMesh] = []
    for kind, rel, stride in (("skinned", skinned_rel, 0x30), ("rigid", rigid_rel, 0x20)):
        if not rel:
            continue
        table = model + rel
        count = r.u32(table)
        r.require(table + 16, count * stride, f"{kind} mesh table")
        for i in range(count):
            e = table + 16 + i * stride
            vc = r.u32(e)
            if not vc:
                continue
            pos = _positions(r, e + r.u32(e + 4), vc)
            uvs = _uvs(r, e + r.u32(e + 8), vc)
            normals = [(x * SNORM15, y * SNORM15, z * SNORM15)
                       for x, y, z in r.iter_unpack("3h", e + r.u32(e + 12), vc, "normals")]
            if kind == "skinned":
                restart = r.bytes(e + r.u32(e + 24), vc, "strip markers")
                material, flags = r.u32(e + 28), r.u32(e + 32)
                pal_n = r.u32(e + 36)
                local_palette = r.bytes(e + r.u32(e + 40), pal_n, "mesh bone palette")
                influences = r.u32(e + 44)
                if not 1 <= influences <= 4:
                    raise FormatError(f"{name}: skinned mesh {i} has {influences} influences")
                raw_w = r.iter_unpack("H", e + r.u32(e + 16), vc * influences, "weights")
                raw_j = r.bytes(e + r.u32(e + 20), vc * influences, "joint indices")
                joints, weights = [], []
                for v in range(vc):
                    pairs: dict[int, float] = {}
                    for s in range(influences):
                        slot = raw_j[v * influences + s] >> 2       # VU matrix slot (x4 qwords)
                        w = raw_w[v * influences + s][0] / 32768.0
                        if slot >= pal_n:
                            raise FormatError(f"{name}: mesh {i} vertex {v} uses missing palette slot")
                        bone = local_palette[slot]
                        pairs[bone] = pairs.get(bone, 0.0) + w
                    if sum(pairs.values()) <= 0:
                        # Only occurs on unused strip-filler vertices.
                        pairs = {local_palette[raw_j[v * influences] >> 2]: 1.0}
                    items = sorted(pairs.items(), key=lambda kv: -kv[1])[:4]
                    total = sum(w for _b, w in items)
                    items += [(0, 0.0)] * (4 - len(items))
                    joints.append(tuple(b for b, _w in items))
                    weights.append(tuple(w / total for _b, w in items))
                meshes.append(BaseMesh(kind, i, material, flags, pos, normals, uvs, restart,
                                       joints, weights))
            else:
                restart = r.bytes(e + r.u32(e + 16), vc, "strip markers")
                material, flags, parent = r.u32(e + 20), r.u32(e + 24), r.u32(e + 28)
                if parent >= bone_count:
                    raise FormatError(f"{name}: rigid mesh {i} bound to missing bone {parent}")
                meshes.append(BaseMesh(kind, i, material, flags, pos, normals, uvs, restart,
                                       parent_bone=parent))

    morphs: list[MorphMesh] = []
    section = r.u32(8)
    if section:
        count = r.u32(section)
        r.require(section + 16, count * 0x40, "morph descriptor table")
        for i in range(count):
            e = section + 16 + i * 0x40
            shape_count, vc = r.u32(e), r.u32(e + 4)
            if not shape_count and not vc:
                continue
            if not shape_count or not vc:
                raise FormatError(f"{name}: morph descriptor {i} is half-populated")
            parent, material, topo, flags = r.unpack("4I", e + 20)
            if parent >= bone_count or topo not in (0, 1):
                raise FormatError(f"{name}: morph descriptor {i} has invalid binding")
            uvs = _uvs(r, e + r.u32(e + 8), vc)
            marker_at = e + r.u32(e + 12)
            if topo:
                restart = bytes(1 if m < 0 else 0 for (m,) in r.iter_unpack("i", marker_at, vc, "markers"))
            else:
                restart = bytes(1 if b else 0 for b in r.bytes(marker_at, vc, "markers"))
            origin = r.unpack("3i", e + 48)
            ptr_table = e + r.u32(e + 16)
            # SHT_200's final normal stream runs 4 bytes past the end of the
            # file; tolerate such a short tail by reading zeros.
            rn = Reader(data + bytes(16), name, limit=len(data) + 16)
            shapes, shape_normals = [], []
            for s in range(shape_count):
                slot_p = ptr_table + s * 8
                pos_at = slot_p + r.u32(slot_p)
                nrm_at = slot_p + 4 + r.u32(slot_p + 4)
                shapes.append(r.iter_unpack("3h", pos_at, vc, "shape positions"))
                # Morph normals are stored in Y, Z, X component order.
                shape_normals.append([(y * SNORM15, z * SNORM15, x * SNORM15)
                                      for x, y, z in rn.iter_unpack("3h", nrm_at, vc, "shape normals")])
            morphs.append(MorphMesh(i, parent, material, topo, flags, uvs, restart, origin,
                                    shapes, shape_normals))

    motion = None
    bank_at = r.u32(16)
    if bank_at:
        motion = parse_motion_bank(data, bank_at, name=name)
        for e in motion.entries:
            for g in e.slots[1:]:
                if g:
                    for t in g.tracks:
                        if not 0 <= t.target < len(palette):
                            raise FormatError(f"{name}: joint track targets palette slot {t.target}")
    return PckModel(name, bones, palette, meshes, morphs, motion, data)


def assemble_split_model(mdl: Path) -> bytes:
    """O_T00 stores an early (April 2004) Fiona as separate files: ``.MDL``
    is a PCK model block, ``.SHP`` its morph section, ``.SHD`` the shadow
    volumes and ``.MTN`` the motion bank. All internal offsets are relative,
    so packing them behind a PCK header yields an ordinary PCK."""
    blob = bytearray(0x20)
    offsets = []
    for ext in (".MDL", ".SHP", ".SHD", ".MTN"):
        part = mdl.with_suffix(ext)
        while len(blob) % 16:
            blob.append(0)
        offsets.append(len(blob) if part.is_file() else 0)
        if part.is_file():
            blob += part.read_bytes()
    struct.pack_into("<5I", blob, 0, 4, *offsets)
    return bytes(blob)


def parse_pck(path: Path | str) -> PckModel:
    p = Path(path)
    if p.suffix.upper() == ".MDL":
        return parse_pck_bytes(assemble_split_model(p), p.stem)
    return parse_pck_bytes(p.read_bytes(), p.stem)
