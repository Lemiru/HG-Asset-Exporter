"""``GAME_FIX.GFM``: the resident 3D props textured from ``GAME_FIX.TEX``.

Loaded at boot next to GAME_FIX.TEX (0x380790) and drawn by two object
classes (draw 0x33B560 / 0x33D9F0, VU1 microprogram at 0x3AC418)::

    u32 entry offsets[4]                 (the first offset = directory size)
    entry = chain of parts, each 16-aligned after the previous one:
      +0  u32 vertex count n             +4  u32 palette index
      +8  u32 parts in the chain (first part only)   +12 0
      +16 u32 offsets (from the part) of: UV, colour, position, flag arrays
      +32 i32 x 4 start position (VIF ROW)
      UV       n x u16 s, t     (V2-16 unsigned; itof15 -> s/32768, t/32768)
      colour   n x u8 RGBA      (V4-8; 0x80 = 1.0)
      position n x i16 x, y, z  (V3-16 unpacked in VIF difference mode: each
                                 vertex = previous + delta, starting from the
                                 ROW value; itof4 -> /16 game units)
      flag     n x u8           (into W: 1 = strip restart, no triangle)

Primitives are alpha-blended Gouraud triangle strips (PRIM 0x5C, STQ). The
texture is always image 2 of GAME_FIX.TEX; TEX2 selects the CLUT, so the
part's palette index (+ a base the owning object adds for tinted/flashing
copies) picks one of the image's 16-colour palettes (CSA). Palette 1 is the
natural colouring, the others are tints.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from ..util.binary import FormatError

TEXTURE_IMAGE = 2       # GAME_FIX.TEX image used by every part


@dataclass
class GfmPart:
    palette: int
    positions: list[tuple[float, float, float]]   # game units
    uvs: list[tuple[float, float]]                # normalised over the 512 x 256 image
    colors: list[tuple[int, int, int, int]]       # raw GS values (0x80 = 1.0)
    restart: list[bool]

    def triangles(self) -> list[tuple[int, int, int]]:
        """Strip triangles with consistent winding (every other one flipped)."""
        out, k = [], 0
        for i, r in enumerate(self.restart):
            if r:
                k = 0
                continue
            k += 1
            if i >= 2:
                out.append((i - 2, i - 1, i) if k % 2 else (i - 1, i - 2, i))
        return out


def parse_gfm(path: Path | str) -> list[list[GfmPart]]:
    p = Path(path)
    data = p.read_bytes()
    first = struct.unpack_from("<I", data, 0)[0]
    if first % 4 or not 4 <= first <= 64:
        raise FormatError(f"{p.name}: not a GFM directory")
    entries = []
    for off in struct.unpack_from(f"<{first // 4}I", data, 0):
        parts, at = [], off
        chain = struct.unpack_from("<I", data, at + 8)[0]
        if not 1 <= chain <= 64:
            raise FormatError(f"{p.name}: entry at 0x{off:X} has {chain} parts")
        for _ in range(chain):
            n, palette, _c, _z, o_uv, o_col, o_pos, o_flag = struct.unpack_from("<8I", data, at)
            row = list(struct.unpack_from("<3i", data, at + 32))
            if not n or at + o_flag + n > len(data):
                raise FormatError(f"{p.name}: part at 0x{at:X} overruns the file")
            positions = []
            for i in range(n):
                d = struct.unpack_from("<3h", data, at + o_pos + 6 * i)
                row = [row[k] + d[k] for k in range(3)]
                positions.append(tuple(c / 16.0 for c in row))
            uvs = [tuple(c / 32768.0 for c in struct.unpack_from("<2H", data, at + o_uv + 4 * i)) for i in range(n)]
            colors = [tuple(data[at + o_col + 4 * i:at + o_col + 4 * i + 4]) for i in range(n)]
            restart = [bool(b & 1) for b in data[at + o_flag:at + o_flag + n]]
            parts.append(GfmPart(palette, positions, uvs, colors, restart))
            at = (at + o_flag + n + 15) & ~15
        entries.append(parts)
    return entries
