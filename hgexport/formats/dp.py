"""Realtime cutscenes: DH cut lists and DP per-cut animation files.

DH::

    u16 total_frames, u16 cut_count, 8 x u32 prop-group name lists (0 =
    none; group i = the objects animated by DP slot 40+i: u32 count, count x
    char[16]), cuts at 0x24, 12 bytes each:
    u32 mask (bit 0 camera, bit n actor slot n), u8 door mask (bit k: DP
    slot 32+k turns room door k), u8 prop-group mask (bit i -> DP slot 40+i),
    u16 0 (padding in every cut), u16 start, u16 end

DP: 48 u32 section offsets at 0. Slot 0 is the camera bank
(type, frame_count, data offset, 0; then 8 floats per frame: position xyz,
target xyz, vertical FOV radians, roll radians). Slots 1..31 are actor motion
banks (motion.py layout, one entry; slot 0 of that entry holds controllers:
-7..-10 facial vectors (codec 3), -11/-12 hand mixers (codec 5)). Slots 32
and 40..47 hold named prop tracks: count, then 28-byte descriptors
(frames, type 2, payload offset, name[16]) with 6 floats per frame
(Euler XYZ radians, translation); slot 32 stores Euler angles only. Cutscenes run at 30 frames per second
(verified against the event SFD audio durations).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..util.binary import FormatError, Reader
from .motion import MotionBank, parse_motion_bank

FPS = 30.0


@dataclass
class Cut:
    index: int
    mask: int
    aux_mask: int
    prop_mask: int
    start: int
    end: int

    @property
    def actors(self) -> list[int]:
        return [s for s in range(1, 32) if self.mask >> s & 1]

    @property
    def frames(self) -> int:
        return self.end - self.start + 1


@dataclass
class EventTimeline:
    name: str
    frames: int
    cuts: list[Cut]
    prop_groups: dict[int, list[str]] = field(default_factory=dict)


def parse_dh(path: Path | str) -> EventTimeline:
    p = Path(path)
    r = Reader(p.read_bytes(), p.name)
    frames, count = r.unpack("HH", 0)
    groups: dict[int, list[str]] = {}
    for g, off in enumerate(r.unpack("8I", 4)):
        if off:
            n = r.u32(off)
            groups[g] = [r.bytes(off + 4 + 16 * k, 16, "prop group name").split(b"\0")[0].decode("ascii", "replace")
                         for k in range(n)]
    cuts = []
    for i in range(count):
        mask, aux, prop, _pad, start, end = r.unpack("IBBHHH", 0x24 + i * 12)
        cuts.append(Cut(i, mask, aux, prop, start, end))
    return EventTimeline(p.stem, frames, cuts, groups)


@dataclass
class PropTrack:
    name: str
    section: int
    samples: list[tuple[float, ...]]      # euler xyz, translation xyz


@dataclass
class CutData:
    name: str
    frames: int
    camera: list[tuple[float, ...]]       # per frame: pos3, target3, fov, roll
    actors: dict[int, MotionBank]
    props: list[PropTrack] = field(default_factory=list)


def _props(r: Reader, slot: int, start: int, end: int) -> list[PropTrack]:
    count = r.u32(start)
    if not 1 <= count <= (end - start - 4) // 28:
        return []
    out = []
    for i in range(count):
        at = start + 4 + i * 28
        frames, kind, rel = r.unpack("III", at)
        name = r.cstring(at + 12, 16)
        # Sections 40..47 store Euler + translation; section 32 ("nullN"
        # helpers) stores Euler angles only.
        fmt, size = ("3f", 12) if slot == 32 else ("6f", 24)
        if kind != 2 or not frames or start + rel + frames * size > end:
            raise FormatError(f"{r.name}: bad prop track {i} in section {slot}")
        samples = r.iter_unpack(fmt, start + rel, frames, "prop samples")
        if slot == 32:
            samples = [v + (0.0, 0.0, 0.0) for v in samples]
        out.append(PropTrack(name, slot, samples))
    return out


def parse_dp_bytes(data: bytes, name: str = "DP") -> CutData:
    r = Reader(data, name)
    offsets = r.unpack("48I", 0, "DP directory")

    def end_of(start: int) -> int:
        later = [o for o in offsets if o > start]
        return min(later) if later else len(data)

    cam_at = offsets[0]
    if not cam_at:
        raise FormatError(f"{name}: no camera section")
    _kind, frames, rel, _z = r.unpack("4I", cam_at, "camera header")
    camera = r.iter_unpack("8f", cam_at + rel, frames, "camera frames")
    actors = {}
    for slot in range(1, 32):
        if offsets[slot]:
            bank = parse_motion_bank(data, offsets[slot], end_of(offsets[slot]), f"{name}:actor{slot}")
            for g in bank.entries[0].slots:
                if g and g.frames != frames:
                    raise FormatError(f"{name}: actor {slot} length {g.frames} differs from camera {frames}")
            actors[slot] = bank
    props: list[PropTrack] = []
    for slot in list(range(32, 48)):
        if offsets[slot]:
            props += _props(r, slot, offsets[slot], end_of(offsets[slot]))
    return CutData(name, frames, camera, actors, props)


def parse_dp(path: Path | str) -> CutData:
    p = Path(path)
    return parse_dp_bytes(p.read_bytes(), p.stem)
