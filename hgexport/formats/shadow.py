"""PCK shadow volumes (PCK header +0x0C; ``.SHD`` in the O_T00 split set).

Section: u32 count, 12 pad bytes, ``count`` 32-byte descriptors
``u32 bone, u32 faces, u32 face/edge/vertex stream offsets (relative to the
section), 3 x u32 0``. Faces are 48 bytes: four u32 vertex indices (a quad),
four u32 edge indices and a float4 normal; edges are u32 vertex pairs;
vertices are float4 (w = 1) in the bone's local space. ``bone`` is an index
into the model's bone table (the runtime pose list is built in that order).

Every shipped volume (2,086 in 109 models) is a closed box of 8 vertices and
6 quads. The game uses them as stencil shadow volumes: 0x1F2900 classifies
faces against the light and finds silhouette edges, 0x1F2B80 emits the caps
and extruded side quads (see the old project's PCK_VOLUME_RUNTIME notes).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from ..util.binary import FormatError
from .pck import assemble_split_model


@dataclass
class ShadowVolume:
    index: int
    bone: int
    vertices: list[tuple[float, float, float]]
    quads: list[tuple[int, int, int, int]]
    normals: list[tuple[float, float, float]]
    edges: list[tuple[int, int]]


def pck_bytes(path: Path | str) -> bytes:
    p = Path(path)
    return assemble_split_model(p) if p.suffix.upper() == ".MDL" else p.read_bytes()


def parse_shadow_volumes(data: bytes, name: str = "PCK") -> list[ShadowVolume]:
    section, animation = struct.unpack_from("<2I", data, 12)
    if not section:
        return []
    end = animation if animation > section else len(data)
    count = struct.unpack_from("<I", data, section)[0]
    if count > 1024 or section + 16 + 32 * count > end:
        raise FormatError(f"{name}: invalid shadow-volume count {count}")
    descs = [struct.unpack_from("<5I", data, section + 16 + 32 * i) for i in range(count)]
    bounds = sorted({d[k] for d in descs for k in (2, 3, 4)} | {end - section})

    def stream_end(offset: int) -> int:
        return next(b for b in bounds if b > offset)

    out = []
    for i, (bone, faces, fa, fb, fc) in enumerate(descs):
        base = section
        nv = (stream_end(fc) - fc) // 16
        ne = (stream_end(fb) - fb) // 8
        if stream_end(fa) - fa != 48 * faces:
            raise FormatError(f"{name}: volume {i} face stream size")
        verts = [struct.unpack_from("<3f", data, base + fc + 16 * k) for k in range(nv)]
        edges = [struct.unpack_from("<2I", data, base + fb + 8 * k) for k in range(ne)]
        quads, normals = [], []
        for f in range(faces):
            at = base + fa + 48 * f
            q = struct.unpack_from("<4I", data, at)
            if max(q) >= nv:
                raise FormatError(f"{name}: volume {i} face {f} vertex out of range")
            quads.append(q)
            normals.append(struct.unpack_from("<3f", data, at + 32))
        out.append(ShadowVolume(i, bone, verts, quads, normals, edges))
    return out
