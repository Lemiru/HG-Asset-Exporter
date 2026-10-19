"""Colour bleeding into fully transparent texels (opt-in: ``--bleed``).

PS2 sheets often store black (or unrelated colours) under texels with alpha
0. Filtered with the visible texels next to them, that colour darkens the
soft edge of cut-outs and blended parts. On these low-resolution sheets the
dark fringe is part of the look (it outlines crystals, lace, foliage), so
exports keep it by default; filling every alpha-0 texel with the colour of
its nearest visible texels (ring by ring, averaging the already filled
8-neighbours) removes it for engines where mipmapped halos matter more.
Alpha is left unchanged.
"""
from __future__ import annotations


def bleed_transparent(width: int, height: int, rgba: bytes) -> bytes:
    """``rgba`` with the RGB of every alpha-0 texel replaced by colour grown
    outward from the visible texels; unchanged if none or all are visible."""
    alpha = rgba[3::4]
    n = width * height
    if 0 not in alpha or alpha.count(0) == n:
        return rgba
    out = bytearray(rgba)
    filled = bytearray(1 if a else 0 for a in alpha)
    offsets = [(dx, dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dx or dy]

    def neighbours(i: int):
        x, y = i % width, i // width
        for dx, dy in offsets:
            nx, ny = x + dx, y + dy
            if 0 <= nx < width and 0 <= ny < height:
                yield ny * width + nx

    frontier = {j for i in range(n) if filled[i] for j in neighbours(i) if not filled[j]}
    while frontier:
        colours = {}
        for i in frontier:
            r = g = b = c = 0
            for j in neighbours(i):
                if filled[j]:
                    k = 4 * j
                    r += out[k]
                    g += out[k + 1]
                    b += out[k + 2]
                    c += 1
            colours[i] = ((r + c // 2) // c, (g + c // 2) // c, (b + c // 2) // c)
        for i, (r, g, b) in colours.items():
            out[4 * i:4 * i + 3] = bytes((r, g, b))
            filled[i] = 1
        frontier = {j for i in frontier for j in neighbours(i) if not filled[j]}
    return bytes(out)
