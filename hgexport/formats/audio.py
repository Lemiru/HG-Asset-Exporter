"""Audio: CRI ADX streams, Sony SCEI HD/BD sound banks and SQ sequences."""
from __future__ import annotations

import math
import struct
from array import array
from dataclasses import dataclass, field
from pathlib import Path

from ..util.binary import FormatError, Reader


@dataclass
class Pcm:
    rate: int
    channels: int
    samples: array                        # interleaved int16
    loop: tuple[int, int] | None = None   # (start, end) sample frames, end inclusive

    def wav(self) -> bytes:
        data = self.samples.tobytes()
        fmt = struct.pack("<HHIIHH", 1, self.channels, self.rate, self.rate * self.channels * 2,
                          self.channels * 2, 16)
        chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
        if self.loop:
            smpl = struct.pack("<9I", 0, 0, int(1e9 / self.rate), 60, 0, 0, 0, 1, 0)
            smpl += struct.pack("<6I", 0, 0, self.loop[0], self.loop[1], 0, 0)
            chunks += b"smpl" + struct.pack("<I", len(smpl)) + smpl
        chunks += b"data" + struct.pack("<I", len(data)) + data
        return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks


def _clip16(v: int) -> int:
    return -32768 if v < -32768 else 32767 if v > 32767 else v


# ---------------------------------------------------------------------- ADX
def decode_adx(data: bytes, name: str = "ADX") -> Pcm:
    """Standard (type 3) CRI ADX. Output is bit-identical to FFmpeg's decoder."""
    r = Reader(data, name)
    magic, offset = struct.unpack_from(">HH", data, 0)
    if magic != 0x8000:
        raise FormatError(f"{name}: not an ADX stream")
    enc, block, bits, channels = data[4], data[5], data[6], data[7]
    rate, total, cutoff = struct.unpack_from(">IIH", data, 8)
    version, flags = data[0x12], data[0x13]
    if enc != 3 or bits != 4 or block != 18 or flags:
        raise FormatError(f"{name}: unsupported ADX variant (encoding {enc}, flags {flags})")
    if data[offset - 2:offset + 4] != b"(c)CRI":
        raise FormatError(f"{name}: missing ADX copyright tag")
    a = math.sqrt(2.0) - math.cos(2.0 * math.pi * cutoff / rate)
    b = math.sqrt(2.0) - 1.0
    c = (a - math.sqrt((a + b) * (a - b))) / b
    c1 = round(c * 2.0 * 4096)
    c2 = round(-(c * c) * 4096)
    loop = None
    if version in (3, 4):
        base = 0x18 if version == 3 else 0x24
        if base + 20 <= offset + 4:
            flag, ls, _lb, le, _leb = struct.unpack_from(">IIIII", data, base)
            if flag:
                loop = (ls, le)
    pos = offset + 4
    out = array("h", bytes(total * channels * 2))
    hist = [[0, 0] for _ in range(channels)]
    nblocks = (total + 31) // 32
    r.require(pos, nblocks * block * channels, "ADX blocks")
    for blk in range(nblocks):
        for ch in range(channels):
            at = pos + (blk * channels + ch) * block
            scale = (data[at] << 8) | data[at + 1]
            s1, s2 = hist[ch]
            base_n = blk * 32
            for i in range(32):
                n = base_n + i
                if n >= total:
                    break
                byte = data[at + 2 + (i >> 1)]
                d = (byte >> 4) if not i & 1 else (byte & 15)
                if d & 8:
                    d -= 16
                s0 = _clip16(d * scale + ((c1 * s1 + c2 * s2) >> 12))
                s2, s1 = s1, s0
                out[n * channels + ch] = s0
            hist[ch] = [s1, s2]
    return Pcm(rate, channels, out, loop)


# ---------------------------------------------------------------- PS-ADPCM
_VAG_FILTERS = ((0, 0), (60, 0), (115, -52), (98, -55), (122, -60))


def decode_vag(data: bytes, start: int, end: int, rate: int) -> Pcm:
    """Sony PS-ADPCM: 16-byte frames (shift/filter, flags, 28 nibbles).
    Flag bit 0 ends the sample, bit 1 marks a looping end, bit 2 loop start."""
    out = array("h")
    s1 = s2 = 0
    loop_start = loop_end = None
    pos = start
    while pos + 16 <= end:
        head, flags = data[pos], data[pos + 1]
        shift, filt = head & 0x0F, min(head >> 4, 4)
        f0, f1 = _VAG_FILTERS[filt]
        if flags & 4 and loop_start is None:
            loop_start = len(out)
        for i in range(28):
            byte = data[pos + 2 + (i >> 1)]
            nib = (byte >> 4) if i & 1 else (byte & 15)
            if nib & 8:
                nib -= 16
            sample = (nib << 12) >> shift if shift <= 12 else 0
            val = _clip16(sample + ((s1 * f0 + s2 * f1 + 32) >> 6))
            s2, s1 = s1, val
            out.append(val)
        pos += 16
        if flags & 1:
            if flags & 2:
                loop_end = len(out) - 1
            break
    loop = (loop_start, loop_end) if loop_start is not None and loop_end is not None else None
    return Pcm(rate, 1, out, loop)


@dataclass
class SoundBank:
    vags: list[tuple[int, int, bool]]     # (BD offset, sample rate, loop flag)
    chunks: dict[str, tuple[int, int]] = field(default_factory=dict)


def _iecs_chunks(data: bytes) -> dict[str, tuple[int, int]]:
    pos = 0
    chunks = {}
    while pos + 12 <= len(data) and data[pos:pos + 4] == b"IECS":
        tag = data[pos + 4:pos + 8][::-1].decode("ascii", "replace")
        size = struct.unpack_from("<I", data, pos + 8)[0]
        chunks[tag] = (pos, size)
        if size == 0:
            break
        pos += size
    return chunks


def parse_hd(data: bytes, name: str = "HD") -> SoundBank:
    """SCEI sound bank header: 'IECS' chunks Vers, Head, Vagi, Smpl, Sset,
    Prog. Vagi lists (BD offset u32, rate u16, loop u8, pad u8) per sample."""
    chunks = _iecs_chunks(data)
    if "Vagi" not in chunks:
        raise FormatError(f"{name}: no Vagi chunk")
    at, _size = chunks["Vagi"]
    count = struct.unpack_from("<I", data, at + 12)[0] + 1
    offsets = struct.unpack_from(f"<{count}I", data, at + 16)
    vags = []
    for o in offsets:
        if o == 0xFFFFFFFF:
            continue
        bd, rate, loop, _ = struct.unpack_from("<IHBB", data, at + o)
        vags.append((bd, rate, bool(loop)))
    return SoundBank(vags, chunks)


def bank_samples(hd: bytes, bd: bytes, name: str = "bank") -> list[Pcm]:
    bank = parse_hd(hd, name)
    starts = sorted({v[0] for v in bank.vags} | {len(bd)})
    out = []
    for offset, rate, _loop in bank.vags:
        end = starts[starts.index(offset) + 1]
        out.append(decode_vag(bd, offset, end, rate or 22050))
    return out


# ----------------------------------------------------------------------- SQ
def _vlq(value: int) -> bytes:
    parts = [value & 0x7F]
    value >>= 7
    while value:
        parts.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(parts))


END_OF_TRACK = bytes((0xFF, 0x2F, 0x00))


@dataclass
class Sequence:
    division: int                          # ticks per quarter note
    events: list[tuple[int, int, tuple]]   # (absolute tick, status, data) ; meta: (0xFF, (kind, payload))
    loop: tuple[int, int] | None = None    # (start tick, end tick) from Sony NRPN 99 = 0 / 1


def parse_sq(data: bytes, name: str = "SQ") -> Sequence:
    """Decode a Sony SCEI sequence (SQ).

    The 'IECSidiM' chunk holds u32 size, u32 max sequence index and per
    sequence an offset (relative to the chunk); a sequence starts with a u32
    and u16 ticks per quarter note, followed by a running-status event stream.
    Sony compresses the stream: when an event's last data byte has bit 7 set,
    the next event has no delta-time field (delta 0), and note-off (0x8n)
    events store only the key number. Loop points are NRPN 99 = 0 (start) and
    99 = 1 (end).
    """
    chunks = _iecs_chunks(data)
    if "Midi" not in chunks:
        raise FormatError(f"{name}: no Midi chunk")
    chunk, size = chunks["Midi"]
    seq = chunk + struct.unpack_from("<I", data, chunk + 16)[0]
    division = struct.unpack_from("<H", data, seq + 4)[0] or 480
    p = seq + 6
    limit = chunk + size
    events: list[tuple[int, int, tuple]] = []
    tick = 0
    status = 0
    no_delta = False
    loop_start = loop_end = None
    lengths = {0x80: 1, 0x90: 2, 0xA0: 2, 0xB0: 2, 0xC0: 1, 0xD0: 1, 0xE0: 2}
    while p < limit:
        if not no_delta:
            delta = 0
            while True:
                byte = data[p]
                p += 1
                delta = (delta << 7) | (byte & 0x7F)
                if not byte & 0x80:
                    break
            tick += delta
        no_delta = False
        if data[p] & 0x80:
            status = data[p]
            p += 1
        if status == 0xFF:
            kind, length = data[p], data[p + 1]
            events.append((tick, 0xFF, (kind, bytes(data[p + 2:p + 2 + length]))))
            p += 2 + length
            if kind == 0x2F:
                break
            continue
        n = lengths.get(status & 0xF0)
        if n is None:
            raise FormatError(f"{name}: unsupported MIDI status 0x{status:02X} at 0x{p:X}")
        args = bytearray(data[p:p + n])
        p += n
        if args[-1] & 0x80:
            args[-1] &= 0x7F
            no_delta = True
        if status & 0xF0 == 0x80:
            args.append(0x40)       # Sony note-off events carry only the key
        if status & 0xF0 == 0xB0 and args[0] == 99:
            if args[1] == 0 and loop_start is None:
                loop_start = tick
            elif args[1] == 1:
                loop_end = tick
        events.append((tick, status, tuple(args)))
    if not events or events[-1][1] != 0xFF or events[-1][2][0] != 0x2F:
        events.append((tick, 0xFF, (0x2F, b"")))
    loop = (loop_start, loop_end) if loop_start is not None and loop_end is not None else None
    return Sequence(division, events, loop)


def sq_to_midi(data: bytes, name: str = "SQ") -> bytes:
    """Standard type-0 MIDI file; Sony loop points become "loopStart" /
    "loopEnd" marker events (plus the original NRPN controller events)."""
    seq = parse_sq(data, name)
    out = bytearray()
    last = 0
    markers = []
    if seq.loop:
        markers = [(seq.loop[0], b"loopStart"), (seq.loop[1], b"loopEnd")]
    for tick, status, payload in seq.events:
        while markers and markers[0][0] <= tick:
            mt, text = markers.pop(0)
            out += _vlq(mt - last) + bytes((0xFF, 0x06, len(text))) + text
            last = mt
        out += _vlq(tick - last)
        last = tick
        if status == 0xFF:
            kind, body = payload
            out += bytes((0xFF, kind)) + _vlq(len(body)) + body
        else:
            out += bytes((status,)) + bytes(payload)
    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, seq.division)
    return header + b"MTrk" + struct.pack(">I", len(out)) + bytes(out)


def write_wav(path: Path, pcm: Pcm) -> None:
    Path(path).write_bytes(pcm.wav())
