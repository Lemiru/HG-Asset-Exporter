"""Export the whole game (or selected categories) into one organised folder."""
from __future__ import annotations

import json
import os
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from ..formats.motion import parse_motion_bank
from ..formats.pck import parse_pck
from ..util.binary import FormatError

CATEGORIES = ("characters", "stages", "cutscenes", "textures", "audio", "music", "text", "movies", "misc")


def family_banks(pck: Path) -> list[tuple[str, Path]]:
    """External MTN motion banks for a model family: ``X_1xx.MTN`` in the
    model folder and in ``O_X_M``. Door banks (``*_D000``) are not character
    motion and are excluded."""
    fam = pck.stem.split("_")[0].upper()
    folders = [pck.parent, pck.parent.parent / f"O_{fam}_M"]
    out = []
    for folder in folders:
        if folder.is_dir():
            for mtn in sorted(folder.glob(f"{fam}_*.MTN")):
                if "_D" not in mtn.stem.upper():
                    out.append((mtn.stem.upper(), mtn))
    return out


def _job_character(pck: str, out: str, scale: float, fmt: str) -> dict:
    from .character import CharacterOptions, export_animation_variant, export_character, same_rig, variant_base
    p = Path(pck)
    model = parse_pck(p)
    base = variant_base(p)
    if base is not None and same_rig(model, parse_pck(base)):
        # X_001.. share mesh and skeleton with X_000: only the differing clips.
        return export_animation_variant(p, base, Path(out), CharacterOptions(scale=scale))
    banks, skipped = [], []
    for label, path in family_banks(p):
        try:
            bank = parse_motion_bank(path.read_bytes(), name=path.name)
        except FormatError as exc:
            skipped.append(f"{path.name}: {exc}")
            continue
        targets = {t.target for e in bank.entries for g in e.slots[1:] if g for t in g.tracks}
        if targets and max(targets) >= len(model.palette):
            skipped.append(f"{path.name}: targets bone slot {max(targets)} beyond palette {len(model.palette)}")
            continue
        banks.append((label, bank))
    rep = export_character(p, Path(out), CharacterOptions(scale=scale, extra_banks=banks))
    rep["mtn_banks"] = [label for label, _b in banks]
    from .character import export_shadow_volumes
    shadow = Path(out).with_name(Path(out).stem + "_shadow" + Path(out).suffix)
    rep["shadow_file"] = export_shadow_volumes(p, shadow, CharacterOptions(scale=scale)).get("shadow_volumes", 0)
    if skipped:
        rep.setdefault("warnings", []).extend(skipped)
    return rep


def _job_stage(pac: str, out: str, scale: float, fmt: str) -> dict:
    from .stage import StageOptions, export_stage
    from .stage import export_navmesh, export_zones
    door = Path(pac).parent.parent / "O_FIN" / "FIN_D000.MTN"
    rep = export_stage(pac, out, StageOptions(scale=scale, door_motion=door if door.is_file() else None))
    nav = Path(out).with_name(Path(out).stem + "_navmesh" + Path(out).suffix)
    rep["navmesh"] = export_navmesh(pac, nav, scale)
    rep["zones"] = export_zones(pac, Path(out).with_name(Path(out).stem + "_zones" + Path(out).suffix), scale)
    return rep


def _job_cutscene(event: str, out: str, scale: float, fmt: str, audio: bool, embed: bool = False) -> dict:
    from .cutscene import CutsceneOptions, export_event
    from .media import export_sfd, ffmpeg_available
    rep = export_event(event, out, CutsceneOptions(scale=scale, embed_models=embed, reference_suffix="." + fmt))
    sfd = next(Path(event).glob("*.SFD"), None)
    if audio and sfd and ffmpeg_available():
        rep["media"] = export_sfd(sfd, Path(out).parent, video=True, audio=True)
    return rep


def _job_tex(tex: str, out_dir: str) -> dict:
    from .media import export_tex
    return {"files": export_tex(Path(tex), Path(out_dir))}


def _job_adx(adx: str, out: str) -> dict:
    from .media import export_adx
    return export_adx(Path(adx), Path(out))


def _job_bank(hd: str, out_dir: str) -> dict:
    from .media import export_bank
    return {"samples": export_bank(Path(hd), Path(out_dir))}


def _job_music(sq: str, out_dir: str) -> dict:
    from .media import export_sq
    from .music import bank_for_sequence, export_music
    src = Path(sq)
    export_sq(src, Path(out_dir) / (src.stem + ".mid"))
    rep = export_music(src, bank_for_sequence(src), Path(out_dir) / (src.stem + ".wav"))
    rep["bank"] = bank_for_sequence(src).stem + ".sf2"
    return rep


def _job_sf2(hd: str, out: str) -> dict:
    from .soundfont import export_sf2
    return export_sf2(Path(hd), Path(out))


def _job_text(src: str, out_base: str) -> dict:
    from .media import export_text
    return export_text(Path(src), Path(out_base))


def _job_curves(src: str, out: str) -> dict:
    from .media import export_curves
    return export_curves(Path(src), Path(out))


def _job_gfm(src: str, out: str, scale: float) -> dict:
    from .gfm import export_gfm
    return export_gfm(Path(src), Path(out), scale)


def _job_doors(src: str, out: str) -> dict:
    from .stage import export_door_motion
    return export_door_motion(Path(src), Path(out))


def _job_icon(src: str, out: str) -> dict:
    from .psicon import export_icon
    return export_icon(Path(src), Path(out))


def _job_movie(sfd: str, out_dir: str) -> dict:
    from .media import export_sfd
    return {"files": export_sfd(Path(sfd), Path(out_dir))}


def plan(data: Path, out: Path, categories: set[str], scale: float, fmt: str, only: str | None,
         embed_cutscene_models: bool = False) -> list[tuple]:
    ext = "." + fmt
    jobs: list[tuple] = []

    def want(path: Path) -> bool:
        return only is None or only.lower() in str(path).lower()

    if "characters" in categories:
        from .character import variant_label
        for pck in sorted(list(data.glob("O_*/*.PCK")) + list(data.glob("O_*/*.MDL"))):
            if want(pck):
                label = variant_label(pck)
                stem = f"{pck.stem}_{label}" if label else pck.stem
                jobs.append(("characters", pck, _job_character,
                             (str(pck), str(out / "characters" / pck.parent.name / (stem + ext)), scale, fmt)))
    if "stages" in categories:
        for pac in sorted(data.glob("ST_*/*.PAC")):
            if want(pac):
                jobs.append(("stages", pac, _job_stage,
                             (str(pac), str(out / "stages" / pac.parent.name / (pac.stem + ext)), scale, fmt)))
    if "cutscenes" in categories:
        for dh in sorted(data.glob("*/*.DH")):
            ev = dh.parent
            if want(ev):
                jobs.append(("cutscenes", ev, _job_cutscene,
                             (str(ev), str(out / "cutscenes" / ev.name / (ev.name + ext)), scale, fmt,
                              "movies" in categories or "audio" in categories, embed_cutscene_models)))
    if "textures" in categories:
        from ..formats.rawimage import RAW_IMAGES
        raw = [p for p in data.glob("SYSTEM/*.BIN") if p.name.upper() in RAW_IMAGES]
        # Rooms keep their textures inside the PAC (slot 9); the memory-card
        # icon inside the ICO. Everything lands under textures/<source folder>.
        for tex in sorted(list(data.rglob("*.TEX")) + list(data.glob("SUBSCR/ART*.LEN")) +
                          list(data.glob("SUBSCR/*.TXS")) + raw + list(data.glob("ST_*/*.PAC")) +
                          list(data.glob("SUBSCR/ICON*.ICO"))):
            if want(tex):
                rel = tex.parent.relative_to(data)
                jobs.append(("textures", tex, _job_tex, (str(tex), str(out / "textures" / rel))))
    if "audio" in categories:
        for adx in sorted(data.rglob("*.ADX")):
            if want(adx):
                jobs.append(("audio", adx, _job_adx,
                             (str(adx), str(out / "audio" / "voice_sfx" / adx.parent.name / (adx.stem + ".wav")))))
        for hd in sorted(data.rglob("*.HD")):
            if want(hd) and hd.with_suffix(".BD").is_file():
                rel = hd.parent.relative_to(data)
                jobs.append(("audio", hd, _job_bank, (str(hd), str(out / "audio" / "banks" / rel / hd.stem))))
    if "music" in categories:
        for sq in sorted(data.rglob("*.SQ")):
            if want(sq):
                jobs.append(("music", sq, _job_music, (str(sq), str(out / "music"))))
        for hd in sorted(data.glob("BGM/*.HD")):
            if want(hd):
                jobs.append(("music", hd, _job_sf2, (str(hd), str(out / "music" / (hd.stem + ".sf2")))))
    if "text" in categories:
        for msg in sorted(data.glob("SUBSCR/MSG_*.BIN")):
            if want(msg):
                jobs.append(("text", msg, _job_text, (str(msg), str(out / "text" / msg.stem))))
        for pac in sorted(data.glob("ST_*/*.PAC")):
            if want(pac):
                jobs.append(("text", pac, _job_text, (str(pac), str(out / "text" / "rooms" / pac.stem))))
    if "misc" in categories:
        src = data / "OBSTACLE.MTN"
        if src.is_file() and want(src):
            jobs.append(("misc", src, _job_curves, (str(src), str(out / "misc" / "OBSTACLE.json"))))
        src = data / "GAME_FIX.GFM"
        if src.is_file() and want(src):
            jobs.append(("misc", src, _job_gfm, (str(src), str(out / "misc" / f"GAME_FIX.{fmt}"), scale)))
        # pursuers' door banks (the player's FIN_D000 is already in every room)
        for src in sorted(data.glob("O_*/*_D000.MTN")):
            if src.stem.upper() != "FIN_D000" and want(src):
                jobs.append(("misc", src, _job_doors, (str(src), str(out / "misc" / "doors" / f"{src.stem}.{fmt}"))))
        for src in sorted(data.glob("SUBSCR/ICON*.ICO")):
            if want(src):
                jobs.append(("misc", src, _job_icon, (str(src), str(out / "misc" / f"{src.stem}.{fmt}"))))
    if "movies" in categories:
        from .media import ffmpeg_available
        if ffmpeg_available():
            for sfd in sorted(data.rglob("*.SFD")):
                if want(sfd) and not (sfd.parent / f"{sfd.parent.name}.DH").is_file():
                    jobs.append(("movies", sfd, _job_movie, (str(sfd), str(out / "movies"))))
    return jobs


def run(data: Path, out: Path, categories: set[str] | None = None, *, scale: float = 0.1, fmt: str = "glb",
        workers: int | None = None, only: str | None = None, embed_cutscene_models: bool = False,
        log=print) -> dict:
    categories = set(categories or CATEGORIES)
    out.mkdir(parents=True, exist_ok=True)
    jobs = plan(data, out, categories, scale, fmt, only, embed_cutscene_models)
    log(f"{len(jobs)} export jobs")
    results: dict[str, list] = {c: [] for c in categories}
    failures = 0
    started = time.time()
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_guard, fn, args): (cat, src) for cat, src, fn, args in jobs}
        for n, fut in enumerate(as_completed(futures), 1):
            cat, src = futures[fut]
            ok, payload = fut.result()
            entry = {"source": str(Path(src).relative_to(data)), "ok": ok}
            if ok:
                entry["report"] = payload
            else:
                entry["error"] = payload
                failures += 1
                log(f"FAILED {src}: {payload.splitlines()[-1]}")
            results[cat].append(entry)
            if n % 50 == 0 or n == len(jobs):
                log(f"{n}/{len(jobs)} done ({time.time() - started:.0f}s, {failures} failed)")
    path = out / "manifest.json"
    seconds = round(time.time() - started, 1)
    if categories != set(CATEGORIES) or only:
        results = _merge_results(path, results, only is not None)
        failures = sum(not e["ok"] for entries in results.values() for e in entries)
    for entries in results.values():
        entries.sort(key=lambda e: e["source"])
    manifest = {"tool": "hgexport", "data": str(data), "scale": scale, "format": fmt,
                "seconds": seconds, "failures": failures,
                "counts": {c: len(v) for c, v in results.items()}, "results": results}
    path.write_text(json.dumps(manifest, indent=1, default=str), encoding="utf-8")
    return manifest


def _merge_results(path: Path, results: dict[str, list], partial: bool) -> dict[str, list]:
    """A run over some categories (or a --filter) updates the existing
    manifest instead of replacing it: categories that were run replace their
    entries (with a filter only the sources that were run), the rest stay."""
    try:
        merged = json.loads(path.read_text(encoding="utf-8"))["results"]
    except (OSError, ValueError, KeyError):
        return results
    for cat, entries in results.items():
        if partial:
            done = {e["source"] for e in entries}
            merged[cat] = [e for e in merged.get(cat, []) if e["source"] not in done] + entries
        else:
            merged[cat] = entries
    return {c: merged[c] for c in CATEGORIES if c in merged}


def _guard(fn, args):
    try:
        return True, fn(*args)
    except Exception:  # reported in the manifest instead of aborting the batch
        return False, traceback.format_exc(limit=6)
