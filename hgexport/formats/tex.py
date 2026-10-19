"""PS2 indexed textures: standalone TEX files and stage (PAC slot 9) packs.

Verified: decoding all three HEW_000.TEX / FIS_000.TEX sheets reproduces the
Noesis reference PNGs pixel-for-pixel (after Noesis' vertical flip).

* Indices are stored linearly (no GS swizzle); PSMT4 packs two texels per
  byte, low nibble first.
* The 1 KiB CLUT is stored in GS CSM1 order: logical entry ``i`` lives at
  ``(i & ~0x18) | (i & 8) << 1 | (i & 0x10) >> 1``. PSMT4 uses entries 0..15
  of the same 256-entry table.
* Alpha is GS-scale (0x80 = opaque) and is expanded to 8 bits as min(2a, 255).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..util.binary import FormatError, Reader

PSMT8 = 0x13
PSMT4 = 0x14


def _clut_order() -> list[int]:
    return [(i & ~0x18) | ((i & 0x08) << 1) | ((i & 0x10) >> 1) for i in range(256)]


_CLUT_ORDER = _clut_order()


def palette_rgba(clut: bytes) -> list[bytes]:
    """256 logical RGBA8 palette entries from a CSM1-ordered GS CLUT."""
    if len(clut) != 1024:
        raise FormatError("CLUT must be 1024 bytes")
    out = []
    for i in range(256):
        p = _CLUT_ORDER[i] * 4
        r, g, b, a = clut[p:p + 4]
        out.append(bytes((r, g, b, min(a * 2, 255))))
    return out


@dataclass
class IndexedImage:
    width: int
    height: int
    indices: bytes          # one byte per texel, row 0 = top
    palette: list[bytes]    # 256 RGBA8 entries
    gs_format: int
    meta: dict = field(default_factory=dict)

    def rgba(self) -> bytes:
        pal = self.palette
        return b"".join(pal[i] for i in self.indices)

    @property
    def has_alpha(self) -> bool:
        used = set(self.indices)
        return any(self.palette[i][3] != 255 for i in used)


def parse_tex_bytes(data: bytes, name: str = "TEX") -> list[IndexedImage]:
    r = Reader(data, name)
    count = r.u32(0)
    if count == 0 or 16 + count * 16 > len(data):
        raise FormatError(f"{name}: invalid texture count {count}")
    images = []
    expected = 16 + count * 16
    for i in range(count):
        base = 16 + i * 16
        fmt, dims, layout, rel = r.unpack("4I", base)
        width, height = dims & 0xFFFF, dims >> 16
        if fmt not in (PSMT8, PSMT4) or not width or not height:
            raise FormatError(f"{name}: texture {i} has unsupported format 0x{fmt:X} or size")
        offset = base + rel
        if offset != expected:
            raise FormatError(f"{name}: texture {i} payload at 0x{offset:X}, expected 0x{expected:X}")
        packed_size = width * height if fmt == PSMT8 else (width * height + 1) // 2
        packed = r.bytes(offset, packed_size, "texel indices")
        clut = r.bytes(offset + packed_size, 1024, "CLUT")
        if fmt == PSMT4:
            idx = bytearray(width * height)
            idx[0::2] = bytes(b & 0x0F for b in packed)[:len(idx[0::2])]
            idx[1::2] = bytes(b >> 4 for b in packed)[:len(idx[1::2])]
            indices = bytes(idx)
        else:
            indices = packed
        images.append(IndexedImage(width, height, indices, palette_rgba(clut), fmt,
                                   {"index": i, "layout": layout}))
        expected = offset + packed_size + 1024
    if expected != len(data):
        raise FormatError(f"{name}: {len(data) - expected} unexplained trailing bytes")
    return images


def parse_tex(path: Path | str) -> list[IndexedImage]:
    p = Path(path)
    return parse_tex_bytes(p.read_bytes(), p.name)


def parse_stage_texture_pack(data: bytes, start: int, end: int, name: str = "PAC") -> list[IndexedImage]:
    """PAC slot 9: ``count`` then 16-byte descriptors
    (gs_format u32, width u16, height u16, pixel qwords u16, CLUT qwords u16,
    flags u32 whose upper 24 bits are the payload offset after the table)."""
    r = Reader(data, name, limit=end)
    count = r.u32(start)
    table = start + 16
    table_end = table + count * 16
    if count == 0 or table_end > end:
        raise FormatError(f"{name}: invalid stage texture count {count}")
    out = []
    for i in range(count):
        fmt, width, height, pix_q, pal_q, flags = r.unpack("IHHHHI", table + i * 16)
        pixel_size, palette_size = pix_q * 16, pal_q * 16
        payload = table_end + (flags & 0xFFFFFF00)
        if pixel_size != width * height or palette_size != 1024:
            raise FormatError(f"{name}: stage texture {i} is not an 8-bit indexed image")
        indices = r.bytes(payload, pixel_size, "stage texels")
        clut = r.bytes(payload + pixel_size, 1024, "stage CLUT")
        out.append(IndexedImage(width, height, indices, palette_rgba(clut), fmt,
                                {"index": i, "flags": flags, "texture_id": ((flags & 0xFF) >> 4) - 1}))
    return out
