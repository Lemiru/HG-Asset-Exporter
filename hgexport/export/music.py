"""Render SQ music sequences to WAV with the game's own HD/BD instruments.

A deliberately small sample-playback synthesizer: per note it plays the
matching split/velocity-layer samples at the right pitch (root note,
transpose, detune, pitch bend), with the bank's volume and pan, channel
volume (CC7), expression (CC11) and pan (CC10), and an approximation of the
SPU2 ADSR envelope. No reverb is added: the game enables SPU2 reverb mode
STUDIO_B but leaves its effect volume at 0 (docs/FORMATS.md). Output covers one
pass up to the sequence's loop end, with the loop written as WAV loop points
so engines can loop it seamlessly.
"""
from __future__ import annotations

import math
from array import array
from bisect import bisect_right
from pathlib import Path

from ..formats.audio import Pcm, Sequence, decode_vag, parse_hd, parse_sq
from ..formats.soundbank import parse_programs, spu_envelope

RATE = 32000
BLOCK = 64


class _Timeline:
    def __init__(self, default: float):
        self.times = [0.0]
        self.values = [default]

    def add(self, t: float, v: float) -> None:
        self.times.append(t)
        self.values.append(v)

    def at(self, t: float) -> float:
        return self.values[bisect_right(self.times, t) - 1]


def _tempo_map(seq: Sequence):
    changes = [(0, 500000)]
    for tick, status, payload in seq.events:
        if status == 0xFF and payload[0] == 0x51:
            changes.append((tick, int.from_bytes(payload[1], "big")))
    changes.sort()
    marks = []      # (tick, seconds, us per quarter)
    seconds = 0.0
    last_tick, tempo = 0, 500000
    for tick, us in changes:
        seconds += (tick - last_tick) * tempo / 1e6 / seq.division
        marks.append((tick, seconds, us))
        last_tick, tempo = tick, us

    def to_seconds(tick: int) -> float:
        i = bisect_right([m[0] for m in marks], tick) - 1
        t0, s0, us = marks[max(i, 0)]
        return s0 + (tick - t0) * us / 1e6 / seq.division
    return to_seconds


def render(seq: Sequence, hd: bytes, bd: bytes, name: str = "music") -> Pcm:
    bank = parse_hd(hd, name)
    programs = {p.index: p for p in parse_programs(hd)}
    starts = sorted({v[0] for v in bank.vags} | {len(bd)})
    sources: dict[int, tuple] = {}

    def source(vag: int):
        if vag not in sources:
            offset, rate, _ = bank.vags[vag]
            pcm = decode_vag(bd, offset, starts[starts.index(offset) + 1], rate or 22050)
            data = array("f", (x / 32768.0 for x in pcm.samples))
            data.append(data[-1] if data else 0.0)
            sources[vag] = (data, pcm.rate, pcm.loop)
        return sources[vag]

    secs = _tempo_map(seq)
    end_tick = seq.loop[1] if seq.loop else seq.events[-1][0]
    total = secs(end_tick)
    vol = [_Timeline(1.0) for _ in range(16)]
    expr = [_Timeline(1.0) for _ in range(16)]
    pan = [_Timeline(0.0) for _ in range(16)]
    bend = [_Timeline(0.0) for _ in range(16)]
    program = [0] * 16
    notes = []          # (start, end, channel, key, velocity, program)
    active: dict[tuple[int, int], list] = {}
    for tick, status, data in seq.events:
        if status == 0xFF:
            continue
        t = secs(tick)
        ch, kind = status & 0x0F, status & 0xF0
        if kind == 0x90 and data[1]:
            note = [t, None, ch, data[0], data[1], program[ch]]
            notes.append(note)
            active.setdefault((ch, data[0]), []).append(note)
        elif kind == 0x80 or (kind == 0x90 and not data[1]):
            stack = active.get((ch, data[0]))
            if stack:
                stack.pop(0)[1] = t
        elif kind == 0xC0:
            program[ch] = data[0]
        elif kind == 0xB0:
            if data[0] == 7:
                vol[ch].add(t, data[1] / 127)
            elif data[0] == 11:
                expr[ch].add(t, data[1] / 127)
            elif data[0] == 10:
                pan[ch].add(t, (data[1] - 64) / 64)
        elif kind == 0xE0:
            bend[ch].add(t, ((data[1] << 7 | data[0]) - 8192) / 8192)
    length = int(total * RATE) + 1
    left = array("f", bytes(4 * (length + RATE * 2)))
    right = array("f", bytes(4 * (length + RATE * 2)))
    limit = len(left)
    for start, stop, ch, key, velocity, prog_index in notes:
        prog = programs.get(prog_index)
        if prog is None:
            continue
        if stop is None:
            stop = total
        for split in prog.splits:
            if not split.key_low <= key <= split.key_high:
                continue
            for s in split.samples:
                if not s.vel_low <= velocity <= max(s.vel_low, s.vel_high) or s.vag >= len(bank.vags):
                    continue
                data, src_rate, loop = source(s.vag)
                env = spu_envelope(s.adsr1, s.adsr2)
                gain = (prog.volume / 127) * (split.volume / 127) * (s.volume / 127) * (velocity / 127)
                base_pan = max(-1.0, min(1.0, ((s.pan - 64) + split.pan + (prog.pan - 64)) / 64))
                semis = key + split.transpose - s.base_note + (s.detune + split.detune) / 128
                inc0 = 2 ** (semis / 12) * src_rate / RATE
                pos = 0.0
                n_src = len(data) - 1
                i0 = int(start * RATE)
                release = max(env.release, 0.005)
                end_t = stop + release
                i = i0
                level_at_release = None
                while i < limit and i / RATE < end_t:
                    t = i / RATE
                    age = t - start
                    if t < stop:
                        if age < env.attack:
                            e = age / env.attack
                        elif age < env.attack + env.decay and env.decay > 0:
                            k = (age - env.attack) / env.decay
                            e = 1.0 - (1.0 - env.sustain) * (1 - math.exp(-4 * k)) / (1 - math.exp(-4))
                        else:
                            e = env.sustain
                    else:
                        if level_at_release is None:
                            level_at_release = e if i > i0 else 0.0
                        e = level_at_release * math.exp(-5 * (t - stop) / release)
                        if e < 1e-4:
                            break
                    g = gain * e * vol[ch].at(t) * expr[ch].at(t)
                    p = max(-1.0, min(1.0, base_pan + pan[ch].at(t)))
                    gl, gr = g * math.cos((p + 1) * math.pi / 4), g * math.sin((p + 1) * math.pi / 4)
                    inc = inc0 * (2 ** (bend[ch].at(t) * 2 / 12))
                    for k in range(min(BLOCK, limit - i)):
                        j = int(pos)
                        if j >= n_src:
                            if loop:
                                pos -= (loop[1] + 1 - loop[0])
                                j = int(pos)
                            else:
                                break
                        v = data[j] + (data[j + 1] - data[j]) * (pos - j)
                        left[i + k] += v * gl
                        right[i + k] += v * gr
                        pos += inc
                        if loop and pos >= loop[1] + 1:
                            pos -= (loop[1] + 1 - loop[0])
                    else:
                        i += BLOCK
                        continue
                    break
    peak = max(max(left[:length], default=0.0), -min(left[:length], default=0.0),
               max(right[:length], default=0.0), -min(right[:length], default=0.0), 1e-6)
    scale = min(1.0, 0.89 / peak) * 32767
    out = array("h", bytes(4 * length))
    for k in range(length):
        out[2 * k] = int(max(-32767, min(32767, left[k] * scale)))
        out[2 * k + 1] = int(max(-32767, min(32767, right[k] * scale)))
    loop_points = None
    if seq.loop:
        loop_points = (int(secs(seq.loop[0]) * RATE), length - 1)
    return Pcm(RATE, 2, out, loop_points)


def export_music(sq: Path, hd: Path, out: Path) -> dict:
    seq = parse_sq(sq.read_bytes(), sq.name)
    pcm = render(seq, hd.read_bytes(), hd.with_suffix(".BD").read_bytes(), hd.stem)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pcm.wav())
    return {"seconds": round(len(pcm.samples) / 2 / pcm.rate, 2), "bank": hd.name, "loop": pcm.loop}


def bank_for_sequence(sq: Path) -> Path:
    """S1_*/S2_*... use STAGEn_BANK; PANIC uses programs 0-9, present in
    every stage bank (STAGE1 is used)."""
    name = sq.stem.upper()
    n = name[1] if name.startswith("S") and name[1].isdigit() else "1"
    return sq.parent / f"STAGE{n}_BANK.HD"
