"""Game text: SUBSCR/MSG_*.BIN banks and PAC slot 10 (room dialogue).

Layout: u16 highest index, then index+1 u16 byte offsets to NUL-terminated
ASCII strings (the last offset is a message, not an end marker). Control
bytes: 0x01 line break, 0x02 page break (wait for the player), 0x03 / 0x04
highlighted text on / off; other bytes below 0x20 are kept as ``{XX}``.
"""
from __future__ import annotations

import struct


def parse_messages(data: bytes) -> list[str]:
    if len(data) < 4:
        return []
    highest = struct.unpack_from("<H", data, 0)[0]
    if 2 + (highest + 1) * 2 > len(data):
        raise ValueError("message table exceeds data")
    offsets = struct.unpack_from(f"<{highest + 1}H", data, 2)
    out = []
    for off in offsets:
        if off >= len(data):
            out.append("")
            continue
        end = data.find(b"\0", off)
        out.append(_decode(data[off:end if end >= 0 else len(data)]))
    return out


def _decode(raw: bytes) -> str:
    parts = []
    for b in raw:
        if b == 1:
            parts.append("\n")
        elif b == 2:
            parts.append("\n\n")
        elif b == 3:
            parts.append("<em>")
        elif b == 4:
            parts.append("</em>")
        elif b == 10:
            parts.append("")
        elif b < 0x20 or b >= 0x7F:
            parts.append(f"{{{b:02X}}}")
        else:
            parts.append(chr(b))
    return "".join(parts).strip("\n")
