"""Minimal PNG encoder (RGBA8) using only zlib."""
from __future__ import annotations

import struct
import zlib


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def encode_png(width: int, height: int, rgba: bytes, *, level: int = 6) -> bytes:
    """Encode ``width*height`` RGBA pixels (row 0 = top) as a PNG file.

    Fully opaque images are written as RGB to keep files smaller and to let
    importers treat them as opaque without inspecting every pixel.
    """
    if len(rgba) != width * height * 4:
        raise ValueError("pixel buffer size does not match dimensions")
    opaque = rgba[3::4].count(255) == width * height
    if opaque:
        rows = bytearray()
        rgb = bytearray(width * height * 3)
        rgb[0::3] = rgba[0::4]
        rgb[1::3] = rgba[1::4]
        rgb[2::3] = rgba[2::4]
        stride = width * 3
        for y in range(height):
            rows.append(0)
            rows += rgb[y * stride:(y + 1) * stride]
        colour_type = 2
    else:
        rows = bytearray()
        stride = width * 4
        for y in range(height):
            rows.append(0)
            rows += rgba[y * stride:(y + 1) * stride]
        colour_type = 6
    header = struct.pack(">IIBBBBB", width, height, 8, colour_type, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(bytes(rows), level)) + _chunk(b"IEND", b""))


def decode_png(blob: bytes) -> tuple[int, int, bytes]:
    """Decode an 8-bit RGB/RGBA/greyscale/paletted non-interlaced PNG to RGBA.

    Used by tests and verification tools only."""
    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    pos = 8
    idat = bytearray()
    plte = trns = None
    width = height = colour_type = 0
    while pos < len(blob):
        size, = struct.unpack_from(">I", blob, pos)
        kind = blob[pos + 4:pos + 8]
        body = blob[pos + 8:pos + 8 + size]
        pos += 12 + size
        if kind == b"IHDR":
            width, height, depth, colour_type, _c, _f, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or interlace:
                raise ValueError("only 8-bit non-interlaced PNG files are supported")
        elif kind == b"IDAT":
            idat += body
        elif kind == b"PLTE":
            plte = body
        elif kind == b"tRNS":
            trns = body
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour_type]
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    out = bytearray()
    previous = bytearray(stride)
    p = 0
    for _y in range(height):
        filter_type = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up = previous[i]
            upleft = previous[i - channels] if i >= channels else 0
            if filter_type == 1:
                line[i] = (line[i] + left) & 255
            elif filter_type == 2:
                line[i] = (line[i] + up) & 255
            elif filter_type == 3:
                line[i] = (line[i] + ((left + up) >> 1)) & 255
            elif filter_type == 4:
                pa, pb, pc = abs(up - upleft), abs(left - upleft), abs(left + up - 2 * upleft)
                pred = left if pa <= pb and pa <= pc else (up if pb <= pc else upleft)
                line[i] = (line[i] + pred) & 255
        out += line
        previous = line
    rgba = bytearray()
    if colour_type == 6:
        rgba = out
    elif colour_type == 2:
        for i in range(0, len(out), 3):
            rgba += out[i:i + 3] + b"\xff"
    elif colour_type == 0:
        for v in out:
            rgba += bytes((v, v, v, 255))
    elif colour_type == 4:
        for i in range(0, len(out), 2):
            rgba += bytes((out[i], out[i], out[i], out[i + 1]))
    else:
        for v in out:
            rgba += plte[v * 3:v * 3 + 3] + bytes((trns[v] if trns and v < len(trns) else 255,))
    return width, height, bytes(rgba)
