"""Sony SCEI HD instrument tables (Prog / Sset / Smpl chunks).

Program: u32 split offset (from program start), u8 split count, u8 split
size, u8 volume, u8 pan, then LFO/key-follow parameters.
Split (20 bytes): u16 sample set, u8 key low, u8 cross-fade, u8 key high,
u8 split number, u16 bend low, u16 bend high, key-follow pitch/amp/pan with
centres, u8 volume, s8 pan, s8 transpose, s8 detune.
Sample set: u8 velocity curve, u8 velocity low, u8 velocity high, u8 count,
count x u16 sample index.
Sample (42 bytes): u16 VAG index, velocity range, velocity follow, u8 base
note @0x0B, s8 detune, u8 pan, u8 group, u8 priority, u8 volume @0x10,
u16 SPU2 ADSR1 @0x12, u16 ADSR2 @0x14, ...
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .audio import _iecs_chunks
from ..util.binary import FormatError


@dataclass
class BankSample:
    vag: int
    vel_low: int
    vel_high: int
    base_note: int
    detune: int
    pan: int
    volume: int
    adsr1: int
    adsr2: int


@dataclass
class Split:
    key_low: int
    key_high: int
    volume: int
    pan: int
    transpose: int
    detune: int
    samples: list[BankSample] = field(default_factory=list)


@dataclass
class Program:
    index: int
    volume: int
    pan: int
    splits: list[Split]


def _table(data: bytes, chunks: dict, name: str) -> tuple[int, list[int]]:
    if name not in chunks:
        raise FormatError(f"HD bank has no {name} chunk")
    at, _size = chunks[name]
    count = struct.unpack_from("<I", data, at + 12)[0] + 1
    return at, list(struct.unpack_from(f"<{count}I", data, at + 16))


def parse_programs(hd: bytes) -> list[Program]:
    chunks = _iecs_chunks(hd)
    smpl_at, smpl_offs = _table(hd, chunks, "Smpl")
    sset_at, sset_offs = _table(hd, chunks, "Sset")
    prog_at, prog_offs = _table(hd, chunks, "Prog")

    def sample(i: int) -> BankSample:
        p = smpl_at + smpl_offs[i]
        vag = struct.unpack_from("<H", hd, p)[0]
        adsr1, adsr2 = struct.unpack_from("<HH", hd, p + 0x12)
        return BankSample(vag, hd[p + 2], hd[p + 4], hd[p + 0x0B], struct.unpack_from("<b", hd, p + 0x0C)[0],
                          hd[p + 0x0D], hd[p + 0x10], adsr1, adsr2)

    programs = []
    for index, off in enumerate(prog_offs):
        if off == 0xFFFFFFFF:
            continue
        p = prog_at + off
        split_off, count, size, volume, pan = struct.unpack_from("<IBBBB", hd, p)
        splits = []
        for k in range(count):
            q = p + split_off + k * size
            sset, low, _fade, high = struct.unpack_from("<HBBB", hd, q)
            vol, span, transpose, detune = struct.unpack_from("<Bbbb", hd, q + 16)
            split = Split(low, high, vol, span, transpose, detune)
            if sset < len(sset_offs) and sset_offs[sset] != 0xFFFFFFFF:
                s = sset_at + sset_offs[sset]
                n = hd[s + 3]
                for idx in struct.unpack_from(f"<{n}H", hd, s + 4):
                    if idx < len(smpl_offs) and smpl_offs[idx] != 0xFFFFFFFF:
                        split.samples.append(sample(idx))
            splits.append(split)
        programs.append(Program(index, volume, pan, splits))
    return programs


# ------------------------------------------------------------- SPU2 envelopes
SPU_RATE = 48000.0


def _envelope_time(rate: int, increase: bool, exponential: bool, start: int, end: int) -> float:
    """Seconds the SPU2 envelope takes from ``start`` to ``end`` (0..0x7FFF)."""
    if rate >= 0x7F:
        return 1e6 if increase or end < start else 0.0
    shift, step = rate >> 2, rate & 3
    cycles_base = 1 << max(0, shift - 11)
    step_base = ((7 - step) if increase else (-8 + step)) << max(0, 11 - shift)
    level, samples = start, 0
    for _ in range(2_000_000):
        if (increase and level >= end) or (not increase and level <= end):
            break
        cycles = cycles_base
        delta = step_base
        if exponential and increase and level > 0x6000:
            cycles *= 4
        if exponential and not increase:
            delta = (delta * level) >> 15
            if delta == 0:
                delta = -1
        level += delta
        samples += cycles
    return samples / SPU_RATE


@dataclass
class Envelope:
    attack: float      # seconds
    decay: float       # seconds from full to sustain level
    sustain: float     # level 0..1
    release: float     # seconds from full to silence


def spu_envelope(adsr1: int, adsr2: int) -> Envelope:
    attack_exp = bool(adsr1 & 0x8000)
    attack_rate = (adsr1 >> 8) & 0x7F
    decay_rate = (adsr1 >> 4) & 0x0F
    sustain_level = min(0x7FFF, ((adsr1 & 0x0F) + 1) * 0x800)
    release_exp = bool(adsr2 & 0x20)
    release_rate = adsr2 & 0x1F
    attack = _envelope_time(attack_rate, True, attack_exp, 0, 0x7FFF)
    decay = _envelope_time(decay_rate * 4, False, True, 0x7FFF, sustain_level)
    release = _envelope_time(release_rate * 4, False, release_exp, 0x7FFF, 16)
    return Envelope(attack, decay, sustain_level / 0x7FFF, release)
