"""``*.SDT`` sound-effect tables (one per sound-effect HD/BD bank).

The game plays sound effect ``n`` of a bank by record ``n``: 16-byte records,
all-zero records unused. Decoded by Capcom_Sound_Driver (``MODULES/SNDDRV.IRX``)
at 0x39FC (record version in byte 0; the shipped tables use version 1) and
played by 0x7C00..0x90C4:

====  ===================================================================
byte  meaning
====  ===================================================================
0     record version (1)
1     always 1 (copied to the voice request)
2     bit 7: priority tie rule; bits 0-6: priority (compared with the
      priority of the voice already playing)
3     bits 0-5: SPU voice (< 48 = fixed voice, >= 48 = allocate); bit 6:
      flag; bit 7: chain - the next record plays as another layer
4     bit 7: pan given; bits 0-6: pan (0 left, 64 centre, 127 right)
5     signed volume offset (applied, clamped to 0..128)
6     signed transpose in semitones
7     bits 4-7 / 0-2: two small counters (0 in all shipped tables);
      bit 3: flag
8     program (0: every effect bank has one program)
9     sample: split ``n`` of the program (key 12 + n -> sample set n)
10    unused (0-3; the decoder 0x39FC copies it to request +14, which
      nothing reads; the game program does not read the tables itself)
====  ===================================================================
"""
from __future__ import annotations

from pathlib import Path

from ..util.binary import FormatError


def _s8(v: int) -> int:
    return v - 256 if v >= 128 else v


def parse_sdt(path: Path | str) -> list[dict | None]:
    """One entry per record index (None for unused records)."""
    p = Path(path)
    data = p.read_bytes()
    if len(data) % 16:
        raise FormatError(f"{p.name}: size is not a multiple of 16")
    out: list[dict | None] = []
    for i in range(0, len(data), 16):
        r = data[i:i + 16]
        if not any(r):
            out.append(None)
            continue
        if r[0] not in (1, 2):
            raise FormatError(f"{p.name}: record {i // 16} has unknown version {r[0]}")
        voice = r[3] & 0x3F
        out.append({
            "version": r[0],
            "program": r[8] & 0x7F,
            "sample": r[9] & 0x7F,
            "voice": voice if voice < 48 else None,
            "priority": r[2] & 0x7F,
            "priority_flag": r[2] >> 7,
            "pan": (r[4] & 0x7F) if r[4] & 0x80 else None,
            "volume_offset": _s8(r[5]),
            "transpose": _s8(r[6]),
            "chain_next": bool(r[3] & 0x80),
            "flags": {"byte3_bit6": bool(r[3] & 0x40), "byte7_bit3": bool(r[7] & 0x08)},
            "counters": [r[7] >> 4, r[7] & 0x07],
            "byte1": r[1],
            "byte10": r[10],
            "raw": r.hex(),
        })
    return out
