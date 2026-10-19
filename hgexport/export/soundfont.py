"""Build a SoundFont 2 (.sf2) from a Sony HD/BD instrument bank, so the
exported MIDI music plays with the game's own instruments."""
from __future__ import annotations

import math
import struct
from pathlib import Path

from ..formats.audio import decode_vag, parse_hd
from ..formats.soundbank import parse_programs, spu_envelope

# SoundFont generator operators
KEY_RANGE, VEL_RANGE, ATTENUATION, PAN = 43, 44, 48, 17
ATTACK, DECAY, SUSTAIN, RELEASE = 34, 36, 37, 38
SAMPLE_MODES, ROOT_KEY, FINE_TUNE, COARSE_TUNE, SAMPLE_ID, INSTRUMENT = 54, 58, 52, 51, 53, 41


def _chunk(tag: bytes, payload: bytes) -> bytes:
    if len(payload) % 2:
        payload += b"\0"
    return tag + struct.pack("<I", len(payload)) + payload


def _list(tag: bytes, payload: bytes) -> bytes:
    return b"LIST" + struct.pack("<I", len(payload) + 4) + tag + payload


def _name(text: str) -> bytes:
    return text.encode("ascii", "replace")[:19].ljust(20, b"\0")


def _timecents(seconds: float) -> int:
    seconds = max(seconds, 0.001)
    return max(-12000, min(8000, round(1200 * math.log2(seconds))))


def _gen(op: int, amount: int) -> bytes:
    return struct.pack("<Hh", op, amount)


def _range(op: int, lo: int, hi: int) -> bytes:
    return struct.pack("<HBB", op, lo, hi)


def build_sf2(hd: bytes, bd: bytes, name: str) -> bytes:
    bank = parse_hd(hd, name)
    programs = parse_programs(hd)
    starts = sorted({v[0] for v in bank.vags} | {len(bd)})

    # ---------------------------------------------------------------- samples
    smpl = bytearray()
    shdr = bytearray()
    sample_ids: dict[int, int] = {}
    loops: dict[int, bool] = {}
    used = sorted({s.vag for p in programs for sp in p.splits for s in sp.samples if s.vag < len(bank.vags)})
    for vag in used:
        offset, rate, _flag = bank.vags[vag]
        pcm = decode_vag(bd, offset, starts[starts.index(offset) + 1], rate or 22050)
        begin = len(smpl) // 2
        data = pcm.samples.tobytes() or b"\0\0"
        smpl += data + bytes(46 * 2)
        end = begin + len(data) // 2
        if pcm.loop:
            ls, le = begin + pcm.loop[0], begin + pcm.loop[1] + 1
        else:
            ls, le = begin, end
        loops[vag] = pcm.loop is not None
        sample_ids[vag] = len(shdr) // 46
        shdr += _name(f"{name}_{vag:03d}") + struct.pack("<IIIIIBbHH", begin, end, ls, le, pcm.rate, 60, 0, 0, 1)
    shdr += _name("EOS") + bytes(26)

    # ------------------------------------------------------------- instruments
    inst, ibag, igen = bytearray(), bytearray(), bytearray()
    phdr, pbag, pgen = bytearray(), bytearray(), bytearray()
    for prog in programs:
        inst += _name(f"{name}_p{prog.index:03d}") + struct.pack("<H", len(ibag) // 4)
        for split in prog.splits:
            for s in split.samples:
                if s.vag not in sample_ids:
                    continue
                env = spu_envelope(s.adsr1, s.adsr2)
                gain = (prog.volume / 127) * (split.volume / 127) * (s.volume / 127)
                atten = min(1440, round(-200 * math.log10(max(gain, 1e-4))))
                pan = max(-64, min(63, (s.pan - 64) + split.pan + (prog.pan - 64)))
                ibag += struct.pack("<HH", len(igen) // 4, 0)
                igen += _range(KEY_RANGE, split.key_low, min(127, split.key_high))
                igen += _range(VEL_RANGE, s.vel_low, max(s.vel_low, s.vel_high))
                igen += _gen(ATTENUATION, atten)
                igen += _gen(PAN, round(pan / 64 * 500))
                igen += _gen(ATTACK, _timecents(env.attack))
                igen += _gen(DECAY, _timecents(env.decay))
                igen += _gen(SUSTAIN, min(1440, round(-200 * math.log10(max(env.sustain, 1e-4)))))
                igen += _gen(RELEASE, _timecents(env.release))
                igen += _gen(COARSE_TUNE, split.transpose)
                igen += _gen(FINE_TUNE, round((s.detune + split.detune) * 100 / 128))
                igen += _gen(ROOT_KEY, s.base_note)
                igen += _gen(SAMPLE_MODES, 1 if loops[s.vag] else 0)
                igen += _gen(SAMPLE_ID, sample_ids[s.vag])
        phdr += _name(f"{name} {prog.index}") + struct.pack("<HHHIII", prog.index, 0, len(pbag) // 4, 0, 0, 0)
        pbag += struct.pack("<HH", len(pgen) // 4, 0)
        pgen += _gen(INSTRUMENT, len(inst) // 22 - 1)
    inst += _name("EOI") + struct.pack("<H", len(ibag) // 4)
    ibag += struct.pack("<HH", len(igen) // 4, 0)
    igen += bytes(4)
    phdr += _name("EOP") + struct.pack("<HHHIII", 0, 0, len(pbag) // 4, 0, 0, 0)
    pbag += struct.pack("<HH", len(pgen) // 4, 0)
    pgen += bytes(4)

    info = _list(b"INFO", _chunk(b"ifil", struct.pack("<HH", 2, 1)) + _chunk(b"isng", b"EMU8000\0")
                 + _chunk(b"INAM", name.encode() + b"\0") + _chunk(b"ISFT", b"hgexport\0"))
    sdta = _list(b"sdta", _chunk(b"smpl", bytes(smpl)))
    pdta = _list(b"pdta", _chunk(b"phdr", bytes(phdr)) + _chunk(b"pbag", bytes(pbag))
                 + _chunk(b"pmod", bytes(10)) + _chunk(b"pgen", bytes(pgen)) + _chunk(b"inst", bytes(inst))
                 + _chunk(b"ibag", bytes(ibag)) + _chunk(b"imod", bytes(10)) + _chunk(b"igen", bytes(igen))
                 + _chunk(b"shdr", bytes(shdr)))
    body = b"sfbk" + info + sdta + pdta
    return b"RIFF" + struct.pack("<I", len(body)) + body


def export_sf2(hd: Path, out: Path) -> dict:
    data = build_sf2(hd.read_bytes(), hd.with_suffix(".BD").read_bytes(), hd.stem)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return {"bytes": len(data), "programs": len(parse_programs(hd.read_bytes()))}
