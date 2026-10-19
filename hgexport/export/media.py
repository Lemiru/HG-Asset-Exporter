"""Textures, audio and movie batch helpers."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..formats.audio import bank_samples, decode_adx, sq_to_midi
from ..formats.tex import PSMT4, parse_tex
from ..util.png import encode_png


def extra_palettes(img) -> list[int]:
    """Populated 16-colour palettes 1..15 of a PSMT4 image (palette 0 is the
    one the base PNG uses)."""
    if getattr(img, "gs_format", None) != PSMT4 or len(img.palette) < 256:
        return []
    return [k for k in range(1, 16) if any(c[3] for c in img.palette[16 * k:16 * k + 16])]


class _RgbaImage:
    """An already decoded RGBA image (memory-card icon texture)."""

    def __init__(self, width: int, height: int, pixels: bytes):
        self.width, self.height, self._pixels = width, height, pixels

    def rgba(self) -> bytes:
        return self._pixels


def export_tex(path: Path, out_dir: Path) -> list[str]:
    """Every image the game ships -> PNG: TEX files, the texture packs inside
    room PAC files (slot 9), gallery art packs (ART*.LEN), THUMBNAIL.TXS, the
    raw SYSTEM logo/title images (*.BIN) and the memory-card icon (*.ICO)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    suffix = path.suffix.upper()
    if suffix == ".LEN":
        from ..formats.gallery import parse_art_pack
        images = parse_art_pack(path)
    elif suffix == ".TXS":
        from ..formats.gallery import parse_thumbnails
        images = parse_thumbnails(path)
    elif suffix == ".BIN":
        from ..formats.rawimage import parse_raw_image
        images = [parse_raw_image(path)]
    elif suffix == ".PAC":
        from ..formats.stage import parse_pac
        from ..formats.tex import parse_stage_texture_pack
        pac = parse_pac(path)
        region = pac.region(9)
        images = parse_stage_texture_pack(pac.data, *region, pac.name) if region else []
    elif suffix == ".ICO":
        from ..formats.psicon import parse_icon
        icon = parse_icon(path)
        images = [_RgbaImage(128, 128, icon.texture)] if icon.texture else []
    else:
        images = parse_tex(path)
    digits = 3 if len(images) > 99 else 1
    for i, img in enumerate(images):
        name = f"{path.stem}.png" if len(images) == 1 else f"{path.stem}_{i:0{digits}d}.png"
        (out_dir / name).write_bytes(encode_png(img.width, img.height, img.rgba()))
        written.append(name)
        # 4-bit images whose CLUT holds more palettes (selected in game by the
        # CLUT offset, e.g. tinted UI states): one extra PNG per palette.
        for k in extra_palettes(img):
            pal = img.palette[16 * k:16 * k + 16]
            alt = f"{Path(name).stem}_pal{k}.png"
            (out_dir / alt).write_bytes(encode_png(img.width, img.height,
                                                   b"".join(pal[i & 15] for i in img.indices)))
            written.append(alt)
    return written


def export_adx(path: Path, out: Path) -> dict:
    pcm = decode_adx(path.read_bytes(), path.name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pcm.wav())
    return {"rate": pcm.rate, "channels": pcm.channels, "seconds": len(pcm.samples) / pcm.channels / pcm.rate,
            "loop": pcm.loop}


def export_bank(hd: Path, out_dir: Path) -> int:
    bd = hd.with_suffix(".BD")
    data = hd.read_bytes()
    samples = bank_samples(data, bd.read_bytes(), hd.name)
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, pcm in enumerate(samples):
        (out_dir / f"{hd.stem}_{i:03d}.wav").write_bytes(pcm.wav())
    sdt = hd.with_suffix(".SDT")
    if sdt.is_file():
        export_effect_table(sdt, data, out_dir / f"{hd.stem}_effects.json")
    return len(samples)


def export_effect_table(sdt: Path, hd: bytes, out: Path) -> int:
    """SDT sound-effect table -> JSON: effect id -> sample WAV + playback
    settings (formats/soundtable.py). The sample is resolved through the
    bank's program: split for key 12 + n -> sample set -> sample -> waveform."""
    import json
    from ..formats.soundbank import parse_programs
    from ..formats.soundtable import parse_sdt
    programs = {p.index: p for p in parse_programs(hd)}
    effects = []
    for index, rec in enumerate(parse_sdt(sdt)):
        if rec is None:
            continue
        entry = {"id": index, **rec}
        program = programs.get(rec["program"])
        key = 12 + rec["sample"]
        split = next((s for s in program.splits if s.key_low <= key <= s.key_high), None) if program else None
        if split and split.samples:
            entry["wav"] = f"{sdt.stem}_{split.samples[0].vag:03d}.wav"
            if len(split.samples) > 1:
                entry["velocity_layers"] = [f"{sdt.stem}_{s.vag:03d}.wav" for s in split.samples]
        effects.append(entry)
    out.write_text(json.dumps({"source": sdt.name, "note": "effect id = SDT record index, as played by the game; "
                               "see hgexport/formats/soundtable.py for the fields", "effects": effects},
                              indent=1), encoding="utf-8")
    return len(effects)


def export_sq(path: Path, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(sq_to_midi(path.read_bytes(), path.name))


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def is_event_sfd(path: Path) -> bool:
    return path.parent.name.upper()[:2] in ("EV", "DE")


def export_sfd(path: Path, out_dir: Path, *, video: bool = True, audio: bool = True,
               separate_audio: bool | None = None) -> list[str]:
    """SFD = MPEG-2 video (+ CRI ADX audio) program stream. Converted with
    FFmpeg; video is re-timed from frame order at the declared 30 fps because
    some SFD timestamps repeat. Event SFDs are mostly black but contain the
    pre-rendered inserts shown during the realtime cutscene, so they are kept.

    The audio is written once: event SFDs (``separate_audio``, the default for
    files in EV*/DE* folders) give a lossless WAV soundtrack for assembling
    the cutscene plus a video-only MP4; stand-alone movies give one MP4 with
    the audio inside."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if separate_audio is None:
        separate_audio = is_event_sfd(path)
    written = []
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                            str(path)], capture_output=True, text=True)
    kinds = [k.strip(",") for k in probe.stdout.split()]
    if audio and separate_audio and "audio" in kinds:
        wav = out_dir / f"{path.stem}.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-map", "0:a:0", "-c:a", "pcm_s16le",
                        str(wav)], check=True)
        written.append(wav.name)
    if video and "video" in kinds:
        mp4 = out_dir / f"{path.stem}.mp4"
        cmd = ["ffmpeg", "-v", "error", "-y", "-fflags", "+genpts", "-i", str(path), "-map", "0:v:0"]
        if audio and not separate_audio and "audio" in kinds:
            cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k"]
        cmd += ["-vf", "setpts=N/(30*TB)", "-r", "30", "-c:v", "libx264", "-crf", "16", "-preset", "medium",
                "-pix_fmt", "yuv420p", str(mp4)]
        subprocess.run(cmd, check=True)
        written.append(mp4.name)
    return written


def export_curves(source: Path, out: Path) -> dict:
    """Stand-alone scalar curve banks (OBSTACLE.MTN) -> JSON."""
    import json
    from ..formats.stage import parse_curve_bank
    curves = parse_curve_bank(source)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"source": source.name, "fps": 30, "note": "one value per game update; consumed by gameplay code "
                               "(player actions near runtime-flagged nav triangles)", "curves": curves}, indent=1),
                   encoding="utf-8")
    return {"curves": len(curves), "samples": [len(c) for c in curves]}


def export_text(source: Path, out_base: Path) -> dict:
    """Message bank (.BIN) or room PAC slot 10 -> JSON (index -> text) and TXT."""
    import json
    from ..formats.text import parse_messages
    if source.suffix.upper() == ".PAC":
        from ..formats.stage import parse_pac
        pac = parse_pac(source)
        region = pac.region(10)
        data = pac.data[region[0]:region[1]] if region else b""
    else:
        data = source.read_bytes()
    messages = parse_messages(data)
    if not any(messages):
        return {"messages": 0}
    out_base.parent.mkdir(parents=True, exist_ok=True)
    out_base.with_suffix(".json").write_text(json.dumps(dict(enumerate(messages)), indent=1, ensure_ascii=False),
                                             encoding="utf-8")
    out_base.with_suffix(".txt").write_text(
        "\n".join(f"[{i}]\n{m}\n" for i, m in enumerate(messages) if m), encoding="utf-8")
    return {"messages": sum(1 for m in messages if m)}
