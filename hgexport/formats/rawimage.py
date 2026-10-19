"""Headerless SYSTEM images (``LOGO_CRI.BIN``, ``LOGO_DOLBY.BIN``,
``TITLE_BACK.BIN``): linear indexed pixels followed by the CLUT, as in a TEX
payload. The dimensions are not stored; the boot / title code hardcodes them
(LOGO_DOLBY is loaded at 0x37FCE0 and drawn as a 640 x 448 sprite).

* 8 bpp: width x height indices, then a 1024-byte CSM1-ordered CLUT.
* 4 bpp: width x height / 2 bytes (low nibble first), then 16 RGBA entries.
"""
from __future__ import annotations

from pathlib import Path

from ..util.binary import FormatError
from .tex import PSMT4, PSMT8, IndexedImage, palette_rgba

# name -> (width, height, bits per pixel); widths confirmed by row correlation
RAW_IMAGES = {
    "LOGO_CRI.BIN": (640, 448, 8),
    "LOGO_DOLBY.BIN": (720, 540, 8),
    "TITLE_BACK.BIN": (640, 448, 4),
}


def parse_raw_image(path: Path | str) -> IndexedImage:
    p = Path(path)
    spec = RAW_IMAGES.get(p.name.upper())
    if spec is None:
        raise FormatError(f"{p.name}: not a known raw SYSTEM image")
    w, h, bpp = spec
    data = p.read_bytes()
    pixels = w * h * bpp // 8
    clut_size = 1024 if bpp == 8 else 64
    if len(data) != pixels + clut_size:
        raise FormatError(f"{p.name}: expected {pixels + clut_size} bytes, found {len(data)}")
    clut = data[pixels:]
    if bpp == 8:
        indices = data[:pixels]
        palette = palette_rgba(clut)
        fmt = PSMT8
    else:
        indices = bytes(v for b in data[:pixels] for v in (b & 15, b >> 4))
        palette = [bytes((clut[4 * i], clut[4 * i + 1], clut[4 * i + 2], min(clut[4 * i + 3] * 2, 255)))
                   for i in range(16)] + [bytes(4)] * 240
        fmt = PSMT4
    return IndexedImage(w, h, indices, palette, fmt, {"source": p.name})
