"""Frame markers and per-cut cutscene parameters.

* ``*.MRK`` (next to a character PCK): u32 count, ``count`` u32 animation IDs
  (the same IDs, in the same order, as the PCK's animation lookup), ``count``
  u32 absolute offsets (0 = absent, equal offsets share one record), then one
  byte per animation frame, padded to 4 bytes. The game reads the byte of the
  current frame through 0x1F4770 and callers test single bits (1, 2, 4, 8,
  16, 32); their gameplay meanings are not assigned.
* ``MARK.BIN`` (event folder): one u16 per event frame. Read by the cutscene
  timeline (method table 0x46ED30, getter 0x2C9C10). Its per-frame update
  (around 0x2CA9D0) drives the controller vibration from two fields and
  counts every bit for event scripts:

  - bit 0: camera cut (743 of 770 bit-0 frames are DH cut starts);
  - bit 2 (0x2C9560): small motor on/off;
  - bits 3-4 (0x2C9590, field 0..3): large motor strength, through the
    table at timeline +666 = 0, 85, 170, 255 (set at 0x2CB668).
    Whenever either value changes from the previous frame, the timeline
    calls the pad manager (global 0x44E7A8, method table 0x46F4F0: +20
    small motor on/off, +24 large motor strength, 720-frame timeout); its
    update (0x2D42A0) passes both to the controller object (global
    0x44FEB0, method +12: port 0, small, large). Both stop when the
    timeline stops advancing.
  - every bit 0-15 is also counted per frame (+518..+533); event scripts and
    game code wait for a given bit number (0x2C9B50; the script interpreter
    reads the number from the script, 0x1FE538; bit 11 at 0x200190 toggles
    game flag 41). Bits 1, 5-15 (1 and 8 mostly as start/end pairs) are
    such script sync points; their effect is whatever the script waiting
    on them does.
* ``PARAMS.BIN`` (event folder): u32 count, 12 bytes padding, then 48-byte
  records ``u8 1, u8 type, u16 cut``, 12 bytes padding, 32 bytes of type data
  (applied by 0x2CA130 when the cut starts):

  - type 0, character lighting (lighting object 0x46B300): ``+16`` light-0
    RGB, ``+20`` ambient RGB added to the room's ambient, ``+24`` scale
    applied to the room's light colours, ``+28/+32`` light-0 angles, ``+36``
    light-1 RGB (all zero = no second light), ``+40/+44`` light-1 angles. A
    cut light's direction is the camera's view direction turned by the first
    angle about the camera's horizontal axis and by the second about world
    up (0x1FA8AC). Colours use the same 0-255 scale as the rooms' slot-4
    lighting.
  - type 1, depth effect (effect 28, class 0x46EC60): four floats sent to the
    renderer as draw command 33 after a check against the camera's clip
    range; most likely depth-of-field near/far blur ranges.
  - type 2, fog (effect 29, class 0x46EB40): two RGBA colours, fog start and
    end distances (renderer method +192, turned into the per-depth fog
    factor), a u32 and six control bytes.
"""
from __future__ import annotations

import struct
from pathlib import Path

from ..util.binary import FormatError


def parse_mrk(path: Path | str) -> dict[int, bytes]:
    """Animation ID -> per-frame marker bytes (padded to 4)."""
    p = Path(path)
    data = p.read_bytes()
    if len(data) < 4:
        raise FormatError(f"{p.name}: too short")
    count = struct.unpack_from("<I", data, 0)[0]
    if not 0 < count < 4096 or 4 + 8 * count > len(data):
        raise FormatError(f"{p.name}: invalid record count {count}")
    ids = struct.unpack_from(f"<{count}I", data, 4)
    offsets = struct.unpack_from(f"<{count}I", data, 4 + 4 * count)
    present = sorted({o for o in offsets if o})
    if any(o < 4 + 8 * count or o > len(data) for o in present):
        raise FormatError(f"{p.name}: record offset out of range")
    ends = {o: (present[i + 1] if i + 1 < len(present) else len(data)) for i, o in enumerate(present)}
    return {rid: data[o:ends[o]] for rid, o in zip(ids, offsets) if o}


def sparse(values, limit: int | None = None) -> list[list[int]]:
    """[[frame, value], ...] for the non-zero values (first ``limit`` frames)."""
    seq = values if limit is None else values[:limit]
    return [[i, int(v)] for i, v in enumerate(seq) if v]


def parse_event_marks(path: Path | str, frames: int) -> list[int]:
    """Per-frame u16 marks of an event (only the frames the file holds)."""
    data = Path(path).read_bytes()
    n = min(len(data) // 2, frames)
    return list(struct.unpack_from(f"<{n}H", data, 0))


VIBRATION_LEVELS = (0, 85, 170, 255)      # timeline +666: large-motor strength per 2-bit level


def vibration(values: list[int]) -> list[list[int]]:
    """[[event frame, small motor 0/1, large motor 0-255], ...] whenever the
    pair changes (first frame included)."""
    out, last = [], None
    for f, v in enumerate(values):
        cur = (1 if v & 4 else 0, VIBRATION_LEVELS[(v >> 3) & 3])
        if cur != last:
            out.append([f, *cur])
            last = cur
    return out if any(s or l for _f, s, l in out) else []


SYNC_BITS = tuple(b for b in range(16) if b not in (0, 2, 3, 4))


def script_sync(values: list[int]) -> list[list]:
    """[[event frame, [bit numbers]], ...] for the script sync bits (all but
    the cut bit 0 and the vibration bits 2-4)."""
    out = []
    for f, v in enumerate(values):
        bits = [b for b in SYNC_BITS if v >> b & 1]
        if bits:
            out.append([f, bits])
    return out


def _rgb(b: bytes) -> list[int]:
    return [b[0], b[1], b[2]]


def parse_event_params(path: Path | str) -> list[dict]:
    p = Path(path)
    data = p.read_bytes()
    count = struct.unpack_from("<I", data, 0)[0]
    if 16 + 48 * count != len(data):
        raise FormatError(f"{p.name}: {count} records do not fill {len(data)} bytes")
    out = []
    for i in range(count):
        rec = data[16 + 48 * i:64 + 48 * i]
        kind, cut = rec[1], struct.unpack_from("<H", rec, 2)[0]
        body = rec[16:]
        if kind == 0:
            scale = struct.unpack_from("<f", body, 8)[0]
            lights = [{"color": _rgb(body[0:3]), "angles": [round(v, 6) for v in struct.unpack_from("<2f", body, 12)]}]
            if any(body[20:23]):
                lights.append({"color": _rgb(body[20:23]),
                               "angles": [round(v, 6) for v in struct.unpack_from("<2f", body, 24)]})
            out.append({"cut": cut, "type": "lighting", "ambient_add": _rgb(body[4:7]),
                        "room_light_scale": round(scale, 6), "lights": lights})
        elif kind == 1:
            out.append({"cut": cut, "type": "depth_effect",
                        "distances": [round(v, 6) for v in struct.unpack_from("<4f", body, 0)],
                        "note": "renderer draw command 33; most likely depth-of-field near/far ranges"})
        elif kind == 2:
            out.append({"cut": cut, "type": "fog", "colors": [list(body[0:4]), list(body[4:8])],
                        "start": round(struct.unpack_from("<f", body, 8)[0], 6),
                        "end": round(struct.unpack_from("<f", body, 12)[0], 6),
                        "word": struct.unpack_from("<I", body, 16)[0], "bytes": list(body[20:26])})
        else:
            out.append({"cut": cut, "type": f"unknown_{kind}", "raw": body.hex()})
    return out
