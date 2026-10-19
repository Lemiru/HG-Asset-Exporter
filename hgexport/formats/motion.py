"""Motion banks: the animation container shared by PCK, MTN and DP files.

Layout (all offsets little-endian u32)::

    bank+0x00  entry_count
    bank+0x04  group_count            (number of populated slots)
    bank+0x08  0
    bank+0x0C  lookup offset          (relative to bank start)
    bank+0x10  entry_count x 20 bytes: five slot words, each relative to the
               *entry* start, 0 = empty slot
    group:     track_count, frame_count, track_table (relative to group)
    track:     target i32, flags u32, payload (relative to the track record)
    lookup:    count, 3 reserved words, then (resource_id u32, entry i32) rows

A PCK embeds one bank at header +0x10, an MTN file *is* one bank, and each DP
character slot holds one single-entry bank.

Slot 0 holds controller tracks (negative targets: actor-level channels such as
root heading, facial vectors and hand selectors). Slots 1..4 hold joint tracks
whose target is an index into the model's bone palette. Codecs (flags & 0xFF):

    0  3 x i16 Euler angles * pi/32768
    1  3 x i16 controller values / 256
    2  3 x i16 angles + 3 x i16 translation / 256
    3  4 x i16 / 32767                    (DP facial vectors)
    4  same storage as 2                  (DP)
    5  i16 selector, i16 selector, i16 blend / 32767   (DP hand mixers)
    7  6 x f32 angles + translation

Flag bit 16 marks a constant track holding a single sample.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..util.binary import FormatError, Reader

ANGLE = math.pi / 32768.0
FIXED8 = 1.0 / 256.0
SNORM = 1.0 / 32767.0

# codec -> (struct format, decoder)
CODECS: dict[int, tuple[str, object]] = {
    0: ("3h", lambda v: (v[0] * ANGLE, v[1] * ANGLE, v[2] * ANGLE)),
    1: ("3h", lambda v: (v[0] * FIXED8, v[1] * FIXED8, v[2] * FIXED8)),
    2: ("6h", lambda v: (v[0] * ANGLE, v[1] * ANGLE, v[2] * ANGLE,
                         v[3] * FIXED8, v[4] * FIXED8, v[5] * FIXED8)),
    3: ("4h", lambda v: tuple(x * SNORM for x in v)),
    4: ("6h", lambda v: (v[0] * ANGLE, v[1] * ANGLE, v[2] * ANGLE,
                         v[3] * FIXED8, v[4] * FIXED8, v[5] * FIXED8)),
    5: ("3h", lambda v: (float(v[0]), float(v[1]), v[2] * SNORM)),
    7: ("6f", lambda v: tuple(v)),
}
CODEC_SIZE = {0: 6, 1: 6, 2: 12, 3: 8, 4: 12, 5: 6, 7: 24}
JOINT_CODECS = {0: "rotation", 2: "rotation+translation", 4: "rotation+translation",
                7: "rotation+translation"}


@dataclass
class Track:
    target: int            # >= 0: bone-palette index; < 0: controller channel
    codec: int
    flags: int
    samples: list[tuple]   # decoded values, 1 sample when constant
    payload_offset: int

    @property
    def constant(self) -> bool:
        return bool(self.flags & 0x10000)

    @property
    def has_translation(self) -> bool:
        return self.codec in (2, 4, 7)


@dataclass
class Group:
    frames: int
    tracks: list[Track]
    offset: int


@dataclass
class Entry:
    index: int
    slots: list[Group | None]
    resource_ids: list[int] = field(default_factory=list)


@dataclass
class MotionBank:
    entries: list[Entry]
    lookup: list[tuple[int, int]]     # raw (resource_id, entry_index) rows
    offset: int

    def entry_ids(self) -> dict[int, list[int]]:
        """Effective resource IDs per entry. The game scans the lookup to the
        last match, so a repeated ID belongs to its final row."""
        final: dict[int, int] = {}
        for rid, idx in self.lookup:
            final[rid] = idx
        out: dict[int, list[int]] = {}
        for rid, idx in final.items():
            if idx >= 0:
                out.setdefault(idx, []).append(rid)
        return out


def _parse_group(r: Reader, at: int, name: str) -> Group:
    track_count, frames, table_rel = r.unpack("3I", at, "motion group header")
    if frames == 0 or frames > 100000 or track_count > 4096:
        raise FormatError(f"{name}: implausible motion group at 0x{at:X} ({track_count} tracks, {frames} frames)")
    table = at + table_rel
    tracks = []
    for k in range(track_count):
        te = table + k * 12
        target, flags, payload_rel = r.unpack("iII", te, "track record")
        codec = flags & 0xFF
        if codec not in CODECS or flags & ~0x100FF:
            raise FormatError(f"{name}: unsupported track codec/flags 0x{flags:X} at 0x{te:X}")
        fmt, decode = CODECS[codec]
        count = 1 if flags & 0x10000 else frames
        payload = te + payload_rel
        raw = r.iter_unpack(fmt, payload, count, "track samples")
        samples = [decode(v) for v in raw]  # type: ignore[operator]
        if codec == 7 and not all(math.isfinite(c) for s in samples for c in s):
            raise FormatError(f"{name}: non-finite float track at 0x{te:X}")
        tracks.append(Track(target, codec, flags, samples, payload))
    return Group(frames, tracks, at)


def parse_motion_bank(data: bytes, base: int = 0, end: int | None = None, name: str = "bank") -> MotionBank:
    r = Reader(data, name, limit=end)
    entry_count, _groups, reserved, lookup_rel = r.unpack("4I", base, "motion bank header")
    if entry_count == 0 or entry_count > 10000 or reserved != 0:
        raise FormatError(f"{name}: not a motion bank (count={entry_count}, reserved={reserved})")
    r.require(base + 16, entry_count * 20, "motion bank entries")
    entries = []
    for i in range(entry_count):
        at = base + 16 + i * 20
        words = r.unpack("5I", at)
        slots = [(_parse_group(r, at + w, name) if w else None) for w in words]
        entries.append(Entry(i, slots))
    lookup: list[tuple[int, int]] = []
    if lookup_rel:
        lk = base + lookup_rel
        n = r.u32(lk)
        for rid, idx in r.iter_unpack("Ii", lk + 16, n, "animation lookup"):
            if idx >= entry_count:
                raise FormatError(f"{name}: lookup references missing entry {idx}")
            lookup.append((rid, idx))
    bank = MotionBank(entries, lookup, base)
    ids = bank.entry_ids()
    for e in entries:
        e.resource_ids = sorted(ids.get(e.index, []))
    return bank
