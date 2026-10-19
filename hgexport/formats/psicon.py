"""PS2 memory-card icon files (``SUBSCR/ICON.SYS`` + ``ICON00.ICO``), the
standard browser formats the game copies into its save directory.

ICON.SYS (964 bytes): "PS2D", u16 0, u16 title line-break offset (bytes),
u32 background alpha, 4 x i32[4] background corner colours, 3 x f32[4] light
directions, 3 x f32[4] light colours, f32[4] ambient, 68-byte Shift-JIS
title, then three 64-byte icon file names (list / copy / delete).

ICO: u32 0x00010000, u32 shape count, u32 texture type, f32 1.0, u32 vertex
count; per vertex: shape count x i16[4] position, i16[4] normal, i16[2] UV,
u8 RGBA (all fixed point /4096; triangle list; Y points down). Animation:
u32 1, u32 frame length, f32 speed, u32 play offset, u32 frame count, then per
frame u32 shape, u32 key count, keys (f32 time, f32 weight). Texture type 7 =
uncompressed 128 x 128 A1B5G5R5 (type bit 3 would mean RLE).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from ..util.binary import FormatError


@dataclass
class IconSys:
    title: str
    line_break: int
    background_alpha: int
    background_colors: list
    light_directions: list
    light_colors: list
    ambient: list
    icons: dict


@dataclass
class Icon:
    shapes: list[list[tuple[float, float, float]]]
    normals: list[tuple[float, float, float]]
    uvs: list[tuple[float, float]]
    colors: list[tuple[int, int, int, int]]
    frame_length: int
    speed: float
    keys: dict[int, list[tuple[float, float]]] = field(default_factory=dict)   # shape -> (time, weight)
    texture: bytes = b""                    # 128 x 128 RGBA8, row 0 = first stored row


def parse_icon_sys(path: Path | str) -> IconSys:
    d = Path(path).read_bytes()
    if d[:4] != b"PS2D" or len(d) != 964:
        raise FormatError(f"{Path(path).name}: not an icon.sys")
    _r, brk, alpha = struct.unpack_from("<HHI", d, 4)
    four = lambda fmt, at, n: [list(struct.unpack_from(fmt, d, at + 16 * i)) for i in range(n)]
    name = lambda at: d[at:at + 64].split(b"\0")[0].decode("ascii", "replace")
    return IconSys(d[0xC0:0x104].split(b"\0\0")[0].decode("shift_jis", "replace"), brk, alpha,
                   four("<4i", 0x10, 4), four("<4f", 0x50, 3), four("<4f", 0x80, 3), four("<4f", 0xB0, 1)[0],
                   {"list": name(0x104), "copy": name(0x144), "delete": name(0x184)})


def parse_icon(path: Path | str) -> Icon:
    p = Path(path)
    d = p.read_bytes()
    magic, nshapes, tex_type, _one, n = struct.unpack_from("<IIIfI", d, 0)
    if magic != 0x10000 or not 1 <= nshapes <= 16 or n % 3:
        raise FormatError(f"{p.name}: not a PS2 icon")
    stride = nshapes * 8 + 16
    fx = 1 / 4096
    shapes = [[tuple(c * fx for c in struct.unpack_from("<3h", d, 20 + i * stride + 8 * s)) for i in range(n)]
              for s in range(nshapes)]
    tail = 20 + nshapes * 8
    normals = [tuple(c * fx for c in struct.unpack_from("<3h", d, tail + i * stride)) for i in range(n)]
    uvs = [tuple(c * fx for c in struct.unpack_from("<2h", d, tail + 8 + i * stride)) for i in range(n)]
    colors = [tuple(d[tail + 12 + i * stride:tail + 16 + i * stride]) for i in range(n)]
    at = 20 + n * stride
    _id, length, speed, _offset, frames = struct.unpack_from("<IIfII", d, at)
    at += 20
    icon = Icon(shapes, normals, uvs, colors, length, speed)
    for _ in range(frames):
        shape, count = struct.unpack_from("<II", d, at)
        icon.keys[shape] = [struct.unpack_from("<ff", d, at + 8 + 8 * k) for k in range(count)]
        at += 8 + 8 * count
    if tex_type & 8:
        raise FormatError(f"{p.name}: compressed icon textures are not supported")
    if len(d) - at != 128 * 128 * 2:
        raise FormatError(f"{p.name}: texture size {len(d) - at}")
    out = bytearray()
    for v in struct.unpack_from("<16384H", d, at):
        out += bytes(((v & 31) * 255 // 31, (v >> 5 & 31) * 255 // 31, (v >> 10 & 31) * 255 // 31, 255))
    icon.texture = bytes(out)
    return icon
