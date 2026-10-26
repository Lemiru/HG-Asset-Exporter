"""Command line interface: ``python -m hgexport <command> ...``"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _out(path: str, fmt: str | None) -> Path:
    p = Path(path)
    if p.suffix.lower() not in (".glb", ".gltf"):
        p = p.with_suffix("." + (fmt or "glb"))
    return p


def cmd_all(a) -> int:
    from .export.batch import CATEGORIES, run
    cats = set(a.only_category or CATEGORIES)
    manifest = run(Path(a.data), Path(a.out), cats, scale=a.scale, fmt=a.format, workers=a.workers, only=a.filter,
                   embed_cutscene_models=a.embed_cutscene_models, specular=a.specular,
                   emissive_glow=a.emissive_glow or 0.0, light_units=a.light_units)
    print(json.dumps(manifest["counts"]), "failures:", manifest["failures"])
    return 1 if manifest["failures"] else 0


def cmd_character(a) -> int:
    from .export.batch import family_banks
    from .export.character import CharacterOptions, export_character
    from .formats.motion import parse_motion_bank
    banks = []
    mtns = [Path(m) for m in a.mtn] if a.mtn else ([p for _l, p in family_banks(Path(a.pck))] if a.family_mtn else [])
    for m in mtns:
        banks.append((m.stem.upper(), parse_motion_bank(m.read_bytes(), name=m.name)))
    opts = CharacterOptions(scale=a.scale, animations=not a.no_animations, mesh=not a.animations_only,
                            morphs=not a.no_morphs, game_alpha=a.game_alpha, bleed=a.bleed,
                            specular=a.specular, texture_variant=a.textures, unlit=a.unlit, extra_banks=banks,
                            root_motion=a.root_motion, separate_parts=a.separate_parts,
                            shadow_volumes=a.shadow_volumes, feet=not a.no_foot_contacts,
                            hand_poses=not a.no_hand_poses)
    if a.variant_of:
        from .export.character import export_animation_variant
        rep = export_animation_variant(a.pck, a.variant_of, _out(a.out, a.format), opts)
    else:
        rep = export_character(a.pck, _out(a.out, a.format), opts)
    print(json.dumps(rep, indent=1, default=str))
    return 0


def cmd_stage(a) -> int:
    from .export.stage import StageOptions, export_stage
    rep = export_stage(a.pac, _out(a.out, a.format),
                       StageOptions(scale=a.scale, vertex_colors=not a.no_vertex_colors, doors=not a.no_doors,
                                    objects=not a.no_objects, unlit=not a.lit,
                                    animations=not a.no_animations, lights=not a.no_lights,
                                    game_alpha=a.game_alpha, bleed=a.bleed, emissive_glow=a.emissive_glow or 0.0,
                                    light_units=a.light_units,
                                    door_motion=Path(a.door_motion) if a.door_motion else _default_door(a.pac),
                                    extra_door_motion=_extra_doors(a)))
    print(json.dumps(rep, indent=1))
    return 0


def _extra_doors(a) -> list[Path]:
    if a.pursuer_doors:
        return sorted(p for p in Path(a.pac).resolve().parent.parent.glob("O_*/*_D000.MTN")
                      if p.stem.upper() != "FIN_D000")
    return [Path(p) for p in a.extra_door_motion or []]


def cmd_doors(a) -> int:
    from .export.stage import export_door_motion
    print(json.dumps(export_door_motion(Path(a.mtn), _out(a.out, a.format)), indent=1))
    return 0


def _default_door(pac: str) -> Path | None:
    door = Path(pac).resolve().parent.parent / "O_FIN" / "FIN_D000.MTN"
    return door if door.is_file() else None


def cmd_cutscene(a) -> int:
    from .export.cutscene import CutsceneOptions, export_event
    actors = {}
    for item in a.actor or []:
        slot, path = item.split("=", 1)
        actors[int(slot)] = path
    rep = export_event(a.event, _out(a.out, a.format),
                       CutsceneOptions(scale=a.scale, actors=actors, room=not a.no_room, room_override=a.room,
                                       embed_models=a.embed_models, feet=not a.no_foot_contacts))
    print(json.dumps(rep, indent=1))
    return 0


def cmd_tex(a) -> int:
    from .export.media import export_tex
    print("\n".join(export_tex(Path(a.tex), Path(a.out))))
    return 0


def cmd_gfm(a) -> int:
    from .export.gfm import export_gfm
    print(json.dumps(export_gfm(Path(a.gfm), _out(a.out, a.format), a.scale, a.bleed), indent=1))
    return 0


def cmd_icon(a) -> int:
    from .export.psicon import export_icon
    print(json.dumps(export_icon(Path(a.ico), _out(a.out, a.format)), indent=1))
    return 0


def cmd_audio(a) -> int:
    from .export import media
    src = Path(a.file)
    out = Path(a.out)
    suffix = src.suffix.upper()
    if suffix == ".ADX":
        print(media.export_adx(src, out if out.suffix else out / (src.stem + ".wav")))
    elif suffix in (".HD", ".BD"):
        from .export.soundfont import export_sf2
        hd = src.with_suffix(".HD")
        print(media.export_bank(hd, out), "samples")
        print(export_sf2(hd, out / (hd.stem + ".sf2")))
    elif suffix == ".SQ":
        from .export.music import bank_for_sequence, export_music
        folder = out if not out.suffix else out.parent
        media.export_sq(src, folder / (src.stem + ".mid"))
        print(export_music(src, bank_for_sequence(src), folder / (src.stem + ".wav")))
    elif suffix == ".SFD":
        print(media.export_sfd(src, out))
    else:
        print("unsupported audio file", file=sys.stderr)
        return 2
    return 0


def cmd_validate(a) -> int:
    from .gltf.reader import validate
    bad = 0
    for f in a.files:
        errors = validate(f)
        print(f"{f}: {'OK' if not errors else str(len(errors)) + ' problem(s)'}")
        for e in errors[:20]:
            print("   ", e)
        bad += bool(errors)
    return 1 if bad else 0


def cmd_info(a) -> int:
    p = Path(a.file)
    suffix = p.suffix.upper()
    info: dict = {"file": p.name}
    if suffix == ".PCK":
        from .formats.pck import parse_pck
        m = parse_pck(p)
        info.update(bones=len(m.bones), palette=len(m.palette), meshes=len(m.meshes), morphs=len(m.morphs),
                    clips=len(m.motion.entries) if m.motion else 0)
    elif suffix == ".PAC":
        from .formats.stage import parse_stage
        s = parse_stage(p)
        info.update(groups={g: len(r) for g, r in s.groups.items()}, doors=len(s.doors),
                    objects=[o.name for o in s.objects], textures=len(s.textures), cameras=len(s.cameras))
    elif suffix == ".MTN":
        from .formats.motion import parse_motion_bank
        b = parse_motion_bank(p.read_bytes(), name=p.name)
        info.update(entries=len(b.entries), ids=len(b.lookup))
    elif suffix == ".DP":
        from .formats.dp import parse_dp
        c = parse_dp(p)
        info.update(frames=c.frames, actors=sorted(c.actors), props=[t.name for t in c.props])
    elif suffix == ".DH":
        from .formats.dp import parse_dh
        t = parse_dh(p)
        info.update(frames=t.frames, cuts=[(c.index, c.start, c.end, c.actors) for c in t.cuts])
    elif suffix == ".TEX":
        from .formats.tex import parse_tex
        info.update(images=[(i.width, i.height, hex(i.gs_format)) for i in parse_tex(p)])
    else:
        print("unsupported file type", file=sys.stderr)
        return 2
    print(json.dumps(info, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hgexport", description="Haunting Ground (PS2) asset exporter")
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p, scale=True):
        if scale:
            p.add_argument("--scale", type=float, default=0.1, help="output scale (default 0.1: game units -> metres)")
        p.add_argument("--format", choices=("glb", "gltf"), default=None,
                       help="output format when OUT has no extension (default glb)")

    def light_units(p):
        p.add_argument("--light-units", choices=("physical", "unitless"), default="physical",
                       help="rooms: glTF light intensities; physical (default): the game's 128 = 2000 lux, for "
                            "Blender's importer and exposure-based renderers (Unity HDRP); unitless: 128 = 1, for "
                            "engines that use the value as it is (Godot, Unity URP)")

    p = sub.add_parser("all", help="export everything from an extracted DATA.CVM folder")
    p.add_argument("data")
    p.add_argument("out")
    p.add_argument("--only-category", action="append", choices=("characters", "stages", "cutscenes", "textures",
                                                                "audio", "music", "text", "movies", "misc"))
    p.add_argument("--filter", help="only sources whose path contains this text")
    p.add_argument("--workers", type=int)
    p.add_argument("--specular", action="store_true",
                   help="characters: the game's per-part shine as specular in the materials (the masks are always "
                        "written to textures/<folder>/<model>_mNN_shine.png)")
    p.add_argument("--emissive-glow", type=float, nargs="?", const=4.0, metavar="STRENGTH",
                   help="rooms: make the glow geometry (lamps, candles; drawn into the game's bloom) emissive at this strength, default 4, for engines whose bloom works from emission (KHR_materials_emissive_strength; off by default)")
    light_units(p)
    p.add_argument("--embed-cutscene-models", action="store_true",
                   help="cutscene files include actor meshes and room geometry (default: animation, camera "
                        "and props only, referencing the character/stage exports by name)")
    common(p)
    p.set_defaults(func=cmd_all, format="glb")

    p = sub.add_parser("character", help="export a PCK model")
    p.add_argument("pck")
    p.add_argument("out")
    p.add_argument("--mtn", action="append", help="add clips from an external MTN bank (repeatable)")
    p.add_argument("--family-mtn", action="store_true", help="add all MTN banks of the model's family")
    p.add_argument("--textures", default="default", help="texture set: default, gameplay, cutscene, H, ...")
    p.add_argument("--no-animations", action="store_true")
    p.add_argument("--animations-only", action="store_true", help="skeleton + clips, no mesh")
    p.add_argument("--variant-of", metavar="BASE.PCK",
                   help="animation-only file with just the clips that differ from BASE (same mesh/skeleton), "
                        "e.g. O_FIN/FIN_003.PCK --variant-of O_FIN/FIN_000.PCK")
    p.add_argument("--no-morphs", action="store_true")
    p.add_argument("--game-alpha", action="store_true",
                   help="keep the game's alpha-blend flag as BLEND materials (default: one alpha mode per mesh element "
                        "from the texels it uses)")
    p.add_argument("--unlit", action="store_true")
    p.add_argument("--specular", action="store_true",
                   help="the game's per-part shine as specular in the materials (KHR_materials_specular; off by "
                        "default: the game computes it but never shows it in normal play)")
    p.add_argument("--bleed", action="store_true", help="fill the colour under fully transparent texels from their visible neighbours (removes the dark "
                        "fringes the sheets store there; the game shows them)")
    p.add_argument("--shadow-volumes", action="store_true",
                   help="embed the model's shadow-volume boxes (the batch export writes them to <model>_shadow.glb)")
    p.add_argument("--separate-parts", action="store_true",
                   help="one object per PCK submesh instead of one body mesh")
    p.add_argument("--no-foot-contacts", action="store_true",
                   help="leave out the per-clip foot contact intervals / foot points (clip extras)")
    p.add_argument("--no-hand-poses", action="store_true",
                   help="no MRK-driven hand pose weights / extras in the clips (Fiona, Debilitas)")
    p.add_argument("--root-motion", action="store_true",
                   help="move a motion node above the skeleton by the clips' authored root motion")
    common(p)
    p.set_defaults(func=cmd_character)

    p = sub.add_parser("stage", help="export a stage PAC")
    p.add_argument("pac")
    p.add_argument("out")
    p.add_argument("--no-vertex-colors", action="store_true")
    p.add_argument("--no-doors", action="store_true")
    p.add_argument("--no-objects", action="store_true")
    p.add_argument("--lit", action="store_true", help="lit materials instead of unlit (lighting is baked)")
    p.add_argument("--no-animations", action="store_true", help="no atlas/door/fan/shape clips")
    p.add_argument("--no-lights", action="store_true", help="leave out the room's character lights (slot 4)")
    light_units(p)
    p.add_argument("--emissive-glow", type=float, nargs="?", const=4.0, metavar="STRENGTH",
                   help="rooms: make the glow geometry (lamps, candles; drawn into the game's bloom) emissive at this strength, default 4, for engines whose bloom works from emission (KHR_materials_emissive_strength; off by default)")
    p.add_argument("--bleed", action="store_true", help="fill the colour under fully transparent texels from their visible neighbours (removes the dark "
                        "fringes the sheets store there; the game shows them)")
    p.add_argument("--game-alpha", action="store_true",
                   help="keep the game's alpha-blend flag as BLEND materials (default: one alpha mode per mesh element "
                        "from the texels it uses)")
    p.add_argument("--door-motion", help="*_D000.MTN door-angle bank (default: O_FIN/FIN_D000.MTN)")
    p.add_argument("--extra-door-motion", action="append", metavar="X_D000.MTN",
                   help="also add this character's door clips (<door>_<X>_open_1 ...); repeatable")
    p.add_argument("--pursuer-doors", action="store_true",
                   help="add every pursuer's door clips (the batch writes them once to misc/doors/ instead)")
    common(p)
    p.set_defaults(func=cmd_stage)

    p = sub.add_parser("cutscene", help="export an EV*/DE* event folder as one animated scene")
    p.add_argument("event")
    p.add_argument("out")
    p.add_argument("--actor", action="append", help="SLOT=PATH.PCK override (slot 1 = Fiona, 2 = Hewie)")
    p.add_argument("--room", help="force the room for every cut, e.g. ST_021 (default: automatic)")
    p.add_argument("--no-room", action="store_true", help="leave the room (and props bound to it) out")
    p.add_argument("--embed-models", action="store_true",
                   help="include actor meshes and room geometry (one self-contained scene); by default "
                        "only cutscene-exclusive data is written and models/rooms are referenced by name")
    p.add_argument("--no-foot-contacts", action="store_true",
                   help="leave out the actors' foot contact intervals (actor node extras)")
    common(p)
    p.set_defaults(func=cmd_cutscene)

    p = sub.add_parser("tex", help="convert a TEX / room PAC (its texture pack) / ART*.LEN / THUMBNAIL.TXS / SYSTEM logo .BIN / ICON .ICO to PNG")
    p.add_argument("tex")
    p.add_argument("out")
    p.set_defaults(func=cmd_tex)

    p = sub.add_parser("gfm", help="export GAME_FIX.GFM (resident props, textured from GAME_FIX.TEX)")
    p.add_argument("gfm")
    p.add_argument("out")
    p.add_argument("--bleed", action="store_true", help="fill the colour under fully transparent texels from their visible neighbours (removes the dark "
                        "fringes the sheets store there; the game shows them)")
    common(p)
    p.set_defaults(func=cmd_gfm)

    p = sub.add_parser("doors", help="export a *_D000.MTN door-curve bank as clips on a door pivot")
    p.add_argument("mtn")
    p.add_argument("out")
    common(p, scale=False)
    p.set_defaults(func=cmd_doors)

    p = sub.add_parser("icon", help="export the memory-card icon (ICONnn.ICO + ICON.SYS) as an animated model")
    p.add_argument("ico")
    p.add_argument("out")
    common(p, scale=False)
    p.set_defaults(func=cmd_icon)

    p = sub.add_parser("audio", help="convert ADX / HD+BD / SQ (MIDI + rendered WAV) / SFD")
    p.add_argument("file")
    p.add_argument("out")
    p.set_defaults(func=cmd_audio)

    p = sub.add_parser("validate", help="structurally validate glTF/GLB files")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("info", help="summarise a PCK/PAC/MTN/DP/DH/TEX file")
    p.add_argument("file")
    p.set_defaults(func=cmd_info)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
