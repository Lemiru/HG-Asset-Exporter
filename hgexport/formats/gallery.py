"""Bonus-gallery images (``SUBSCR``).

* ``ART00.LEN`` ... ``ART05.LEN`` (loaded as ``SUBSCR\\ART%02X.LEN``): u32
  offsets to N entries (the first offset is the table size, 4 * N; eight per
  file). Each entry is run-length coded 8-bit indices followed by a 1 KiB
  GS-ordered CLUT (alpha 0x80 = opaque, like TEX files). A control byte
  ``c >= 0x80`` repeats the next byte ``c - 0x80`` times; ``c < 0x80`` copies
  ``c`` literal bytes. Images are 512 pixels wide; 21 are 512x896 portraits
  and 27 are 512x448 screens (PS2 pixels, shown at 4:3).
* ``THUMBNAIL.TXS``: u32 count, count * u32 offsets, then one TEX record per
  entry (110 thumbnails, 128x128 PSMT4 with a 16-colour CLUT).
"""
from __future__ import annotations

import struct
from pathlib import Path

from ..util.binary import FormatError
from .tex import IndexedImage, PSMT8, palette_rgba, parse_tex_bytes

ART_WIDTH = 512


def decode_rle(data: bytes, end: int) -> bytes:
    out = bytearray()
    i = 0
    while i < end:
        c = data[i]
        i += 1
        if c >= 0x80:
            if i >= end:
                raise FormatError("run past end of data")
            out += bytes((data[i],)) * (c - 0x80)
            i += 1
        else:
            if i + c > end:
                raise FormatError("literal past end of data")
            out += data[i:i + c]
            i += c
    return bytes(out)


def parse_art_pack(path: Path | str) -> list[IndexedImage]:
    p = Path(path)
    data = p.read_bytes()
    first = struct.unpack_from("<I", data, 0)[0]
    if first % 4 or not 4 <= first <= len(data):
        raise FormatError(f"{p.name}: invalid offset table")
    offsets = list(struct.unpack_from(f"<{first // 4}I", data, 0)) + [len(data)]
    images = []
    for k in range(len(offsets) - 1):
        entry = data[offsets[k]:offsets[k + 1]]
        if len(entry) <= 1024:
            raise FormatError(f"{p.name}: entry {k} too short")
        indices = decode_rle(entry, len(entry) - 1024)
        if len(indices) % ART_WIDTH:
            raise FormatError(f"{p.name}: entry {k} decodes to {len(indices)} bytes")
        images.append(IndexedImage(ART_WIDTH, len(indices) // ART_WIDTH, indices,
                                   palette_rgba(entry[-1024:]), PSMT8, {"entry": k}))
    return images


def parse_thumbnails(path: Path | str) -> list[IndexedImage]:
    p = Path(path)
    data = p.read_bytes()
    count = struct.unpack_from("<I", data, 0)[0]
    if not 0 < count < 4096 or 4 + 4 * count > len(data):
        raise FormatError(f"{p.name}: invalid count")
    offsets = list(struct.unpack_from(f"<{count}I", data, 4)) + [len(data)]
    images = []
    for k in range(count):
        found = parse_tex_bytes(data[offsets[k]:offsets[k + 1]], f"{p.name}[{k}]")
        if len(found) != 1:
            raise FormatError(f"{p.name}: entry {k} holds {len(found)} images")
        images.append(found[0])
    return images
