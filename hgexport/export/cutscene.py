"""Realtime cutscene events (EV*/DE* folders) -> one animated glTF scene.

Every cut of an event is laid out on one timeline at its DH start frame:
an animated camera, every actor (skeletal, facial and hand animation), the
room(s) it plays in and every named prop track. Actors and rooms that are
absent from a cut are hidden by scaling their group node to zero. Prop tracks
whose name matches a named object of the room animate that object; others
become animated helper nodes.

By default the file holds only what is exclusive to the cutscene: actors are
bare armatures named like the separately exported characters (``J00``...)
plus a tiny blend-shape weight carrier, and rooms are empty reference nodes
named like the stage export's root and object nodes, so the animation binds
by name to the ``characters/...`` and ``stages/...`` files. ``embed_models``
includes the full actor meshes and room geometry instead (one self-contained
scene).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..formats.dp import FPS, CutData, EventTimeline, parse_dh, parse_dp
from ..formats.marks import parse_event_marks, parse_event_params, sparse
from ..formats.pck import parse_pck
from ..gltf.builder import GltfBuilder
from ..util.mathutil import euler_to_quat, look_at_quat, make_continuous
from ..formats.stage import parse_stage
from .character import UNLOCKABLE_ACCESSORIES, CharacterBuilder, CharacterOptions
from .pairing import rooms_for_cuts
from .stage import StageBuilder, StageOptions
from .textures import character_texture_sets

# DP actor slot -> model, from the executable's cutscene actor table
# (dispatcher 0x2CC5A0, table 0x45D3D0, constructors resolved through vtable
# +0xF8). Slot 1 is the player (current outfit) and slot 2 Hewie.
ACTOR_MODELS = {
    1: "O_FIN/FIN_000", 2: "O_HEW/HEW_000", 3: "O_DB0/DB0_000", 4: "O_DNL/DNL_000",
    5: "O_RCG/RCG_000", 6: "O_LRM/LRM_000", 7: "O_LRY/LRY_000", 8: "O_LRO/LRO_000",
    9: "O_LRF/LRF_000", 10: "O_GLM/GLM_000", 11: "O_FIN/FIN_200", 12: "O_FIW/FIW_200",
    13: "O_CRW/CRW_000", 14: "O_CRW/CRW_200", 15: "O_WHC/WHC_000", 16: "O_SGM/SGM_000",
    17: "O_FS0/FS0_000", 18: "O_RCT/RCT_000", 19: "O_SHT/SHT_000", 20: "O_WIR/WIR_000",
    21: "O_DNT/DNT_000", 22: "O_LRC/LRC_000", 23: "O_LRH/LRH_000", 24: "O_FIM/FIM_200",
    25: "O_HND/HND_000",
}
# Slot 1 maps to character type 0, i.e. the live player object. Until the
# clothes change the player is a separate class (method table 0x470620; its
# model method 0x2F7B80 returns O_FIS\FIS_000.PCK). EV0003 is the change
# itself: cuts 0-4 show the player (still FIS), from cut 6 on the event uses
# the dedicated slots 12 (FIW_200) and 11 (FIN_200). Afterwards the player is
# FIN_00k, where k only selects the pursuer-specific animation set (same
# mesh; see FORMATS.md), so FIN_000 serves every later event. Costumes live
# in their own folders (O_FIB, O_FIC, ...): override slot 1 to use one.
PLAYER_OUTFIT_BY_EVENT = {"EV0002": "O_FIS/FIS_000", "EV0003": "O_FIS/FIS_000"}
BOUNDARY = 0.5 / 1000.0     # seconds before a cut at which the previous cut is held


@dataclass
class CutsceneOptions:
    scale: float = 0.1
    actors: dict[int, str] = field(default_factory=dict)   # slot -> PCK path override
    aspect: float = 4.0 / 3.0
    hands: bool = True
    face: bool = True
    feet: bool = True              # foot contact intervals per actor (node extras)
    room: bool = True              # include the room geometry the event plays in
    room_override: str | None = None   # force one room (e.g. "ST_021") for every cut
    embed_models: bool = False     # full actor meshes + room geometry instead of references
    reference_suffix: str = ".glb"  # extension of the referenced character/stage exports


class _Track:
    """Values on the event timeline with exact cut boundaries."""

    def __init__(self):
        self.times: list[float] = []
        self.values: list = []

    def segment(self, start_frame: int, values: list, frames: int | None = None) -> None:
        """Append one cut's per-frame values. ``frames`` clamps (or pads by
        holding the last value) to the cut length."""
        if frames is not None and values:
            values = list(values[:frames]) + [values[-1]] * max(0, frames - len(values))
        t0 = start_frame / FPS
        if self.values and self.times[-1] < t0 - BOUNDARY:
            # hold the previous cut's last value until just before this cut
            self.times.append(t0 - BOUNDARY)
            self.values.append(self.values[-1])
        for i, v in enumerate(values):
            self.times.append(t0 + i / FPS)
            self.values.append(v)


# Daniella's glass shard (a separate "Glass Shard" object in her character
# file) is hidden in cutscenes except these, where the game shows it.
SHARD_EVENTS: frozenset[str] = frozenset()


def cutscene_hidden_parts(model_name: str, event: str) -> tuple[str, ...]:
    """Accessory objects the game does not show on this actor in this event."""
    family = model_name.split("_")[0].upper()
    if family == "FIN":
        return UNLOCKABLE_ACCESSORIES
    if family == "DNL" and event.upper() not in SHARD_EVENTS:
        return ("Glass Shard",)
    return ()


def _vfov(horizontal: float, aspect: float) -> float:
    return 2.0 * math.atan(math.tan(horizontal / 2.0) / aspect)


def _resolve_model(root: Path, event: str, slot: int, opts: CutsceneOptions) -> Path | None:
    if slot in opts.actors:
        return Path(opts.actors[slot])
    rel = PLAYER_OUTFIT_BY_EVENT.get(event) if slot == 1 else None
    rel = rel or ACTOR_MODELS.get(slot)
    if not rel:
        return None
    path = root / (rel + ".PCK")
    return path if path.is_file() else None


def _character_file(pck: Path, suffix: str) -> str:
    """Where the batch export writes this model (relative to the export root)."""
    return f"characters/{pck.parent.name}/{pck.stem}{suffix}"


def _stage_file(pac: Path, suffix: str) -> str:
    return f"stages/{pac.parent.name}/{pac.stem}{suffix}"


def _rest(obj, s: float) -> tuple[list[float], tuple]:
    return [c * s for c in obj.translation], euler_to_quat(*obj.euler)


def _door_rest(door, s: float) -> tuple[list[float], tuple]:
    """A door node's placement, as the stage export writes it."""
    return [c * s for c in door.position], euler_to_quat(*door.euler)


def _event_extras(event_dir: Path, timeline: EventTimeline, report: dict) -> dict:
    """MARK.BIN frame marks and PARAMS.BIN per-cut parameters (formats/marks.py)."""
    extras: dict = {"fps": FPS, "frames": timeline.frames}
    if timeline.prop_groups:
        extras["prop_groups"] = {str(40 + g): names for g, names in sorted(timeline.prop_groups.items())}
        extras["prop_groups_note"] = ("DH prop-group lists: DP slot -> names of the objects that slot animates "
                                      "(the cuts' prop-group mask enables a group)")
    mark = event_dir / "MARK.BIN"
    if mark.is_file():
        from ..formats.marks import script_sync, vibration
        values = parse_event_marks(mark, timeline.frames)
        extras["marks"] = sparse(values)
        extras["marks_note"] = ("[[event frame, u16 bits], ...]: bit 0 = camera cut; bit 2 = small "
                                "vibration motor on; bits 3-4 = large motor level (0/85/170/255, see "
                                "'vibration'); every other bit is a sync point event scripts wait for")
        sync = script_sync(values)
        if sync:
            extras["script_sync"] = sync
            extras["script_sync_note"] = ("[[event frame, [bit numbers]], ...]: MARK.BIN sync points; the game "
                                          "counts each bit and event scripts (compiled into the program) wait "
                                          "for a bit number (condition opcode 58/59, 0x1FE530) before acting")
        rumble = vibration(values)
        if rumble:
            extras["vibration"] = rumble
            extras["vibration_note"] = ("[[event frame, small motor 0/1, large motor 0-255], ...] at each change "
                                        "(DualShock actuators, driven by the cutscene timeline)")
        if len(values) < timeline.frames:
            report["warnings"].append(f"MARK.BIN covers {len(values)} of {timeline.frames} frames")
    params = event_dir / "PARAMS.BIN"
    if params.is_file():
        starts = {c.index: c.start for c in timeline.cuts}
        records = parse_event_params(params)
        for r in records:
            r["start_frame"] = starts.get(r["cut"])
        extras["cut_parameters"] = records
        extras["cut_parameters_note"] = (
            "applied when the cut starts. lighting: room light colours x room_light_scale, ambient + "
            "ambient_add, up to two directional lights (0-255 colours) whose direction is the camera view "
            "direction turned by angles[0] about the camera's horizontal axis (positive: down, light from above) "
            "and by angles[1] about the up axis (positive: to the right, light from the left) (radians). depth_effect: four distances (likely depth-of-field ranges). fog: RGBA colours, "
            "start/end distance (game units, 1 = 0.1 m)")
        report["cut_parameters"] = len(records)
    movie = event_movie(event_dir, timeline)
    if movie:
        extras["movie"] = movie
    from .look import cutscene_story_state
    from .reflection import event_reflections
    extras["story_state"] = cutscene_story_state(event_dir.name)
    reflections = event_reflections(event_dir.name)
    if reflections:
        extras["reflections"] = reflections
        extras["reflections_note"] = ("[{slot, frames: [[first, last], ...]}]: event frames in which the room's "
                                      "reflection effect in that slot is live (room_effects.reflections of the "
                                      "room export), measured in the movie gallery")
    extras["story_state_note"] = ("story flags and phase when the cutscene plays in the story; with the event "
                                  "they pick the rooms' script_variants (room_effects). The movie gallery runs "
                                  "no enter scripts: it shows the room's own look and the variants whose "
                                  "gallery_events name the event")
    return extras


def _sfd_width(path: Path) -> int:
    """Width from the SFD's MPEG sequence header (the 4-byte start code also
    occurs by chance inside audio packets, so require a plausible size)."""
    data = path.read_bytes()
    code = bytes((0, 0, 1, 0xB3))
    at = data.find(code)
    while at >= 0:
        width = (data[at + 4] << 4) | (data[at + 5] >> 4)
        height = ((data[at + 5] & 15) << 8) | data[at + 6]
        if width in (256, 320, 512, 640) and height in (64, 224, 240, 448, 480):
            return width
        at = data.find(code, at + 1)
    return 0


def movie_content(sfd: Path) -> list[list[int]]:
    """[[first, last event frame], ...] where the event movie is not black
    (FFmpeg blackdetect on the video re-timed as in export_sfd); [] without
    FFmpeg."""
    import re
    import shutil
    import subprocess
    if shutil.which("ffmpeg") is None:
        return []
    run = subprocess.run(["ffmpeg", "-hide_banner", "-fflags", "+genpts", "-i", str(sfd), "-map", "0:v:0", "-vf",
                          "setpts=N/(30*TB),blackdetect=d=0.05:pix_th=0.06", "-an", "-f", "null", "-"],
                         capture_output=True, text=True)
    frames = re.findall(r"frame=\s*(\d+)", run.stderr)
    total = int(frames[-1]) if frames else 0
    spans, t = [], 0
    for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", run.stderr):
        a, b = round(float(a) * FPS), round(float(b) * FPS)
        if a - t > 2:
            spans.append([t, a - 1])
        t = b
    if total - t > 2:
        spans.append([t, total - 1])
    return spans


# The movie player's luminance key per event: an overlay movie's pixel covers
# the picture by (Y - low) / (high - low), limited to 0-1, Y being the
# decoder's luma (16-235). Read from the player's settings (0x3EA1E4) while
# each event played in PCSX2's movie gallery; where the game sets it was not
# found (not in the event's files, the SFD or a table in the program).
MOVIE_ALPHA_KEYS = {
    "DE0000": (16, 32), "DE0001": (15, 60), "DE0002": (15, 150), "DE0003": (40, 255), "DE0004": (16, 32), "DE0005": (18, 30),
    "DE0006": (30, 200), "DE0007": (16, 32), "DE0008": (16, 255), "DE0009": (40, 180), "DE000A": (40, 255), "DE000B": (16, 255),
    "DE000C": (40, 200), "DE000D": (16, 32), "DE000E": (31, 100), "DE000F": (16, 32), "DE0010": (31, 100), "DE0011": (16, 32),
    "DE0012": (80, 255), "DE0013": (16, 32), "DE0014": (31, 100), "DE0015": (30, 200), "DE0016": (16, 32), "DE0017": (16, 40),
    "DE0018": (16, 32), "DE0019": (16, 32), "DE001A": (30, 200), "DE001B": (31, 100), "DE001C": (16, 32), "DE001D": (16, 32),
    "DE001E": (20, 200), "DE001F": (16, 32), "DE0020": (16, 40), "DE0021": (16, 80), "DE0022": (16, 32), "DE0023": (16, 255),
    "DE0024": (16, 32), "DE0025": (20, 120), "DE0026": (40, 120), "DE0027": (16, 32), "DE0028": (16, 32), "DE0029": (16, 255),
    "DE002A": (16, 32), "DE002B": (16, 32), "DE002C": (31, 100), "DE002D": (90, 255), "DE002E": (16, 32), "DE002F": (16, 200),
    "DE0030": (16, 200), "DE0031": (16, 255), "DE0032": (16, 32), "DE0033": (16, 255), "DE0034": (16, 255), "DE0036": (16, 32),
    "DE0039": (16, 255), "DE003B": (16, 80), "DE003C": (16, 32), "DE003D": (16, 32), "DE003F": (16, 32), "DE0040": (16, 32),
    "DE0041": (16, 32), "DE0042": (16, 32), "DE0043": (16, 255), "DE0044": (16, 32), "DE0045": (16, 32), "DE0046": (100, 255),
    "DE0047": (20, 100), "DE0048": (16, 200), "DE0049": (16, 200), "DE1018": (16, 32), "DE101F": (16, 32), "DE102E": (16, 32),
    "DE1033": (16, 255), "DE1034": (16, 255), "DE2018": (16, 32), "DE201F": (16, 120), "EV0002": (31, 100), "EV0003": (16, 32),
    "EV0004": (31, 100), "EV0005": (31, 100), "EV0006": (16, 32), "EV0007": (16, 32), "EV0008": (31, 100), "EV0009": (16, 32),
    "EV0010": (16, 32), "EV0011": (16, 32), "EV0012": (16, 32), "EV0013": (16, 32), "EV0014": (50, 255), "EV0015": (16, 32),
    "EV0016": (16, 32), "EV0017": (16, 32), "EV0018": (30, 200), "EV0019": (16, 32), "EV0020": (16, 32), "EV0021": (31, 100),
    "EV0022": (16, 32), "EV0023": (16, 32), "EV0024": (16, 32), "EV0025": (16, 32), "EV0026": (45, 65), "EV0027": (16, 32),
    "EV0028": (16, 32), "EV0029": (16, 255), "EV0030": (16, 32), "EV0031": (16, 32), "EV0032": (16, 32), "EV0033": (16, 32),
    "EV0034": (16, 32),
}
MOVIE_KEY_DEFAULT = (16, 32)        # what most events use


def movie_alpha_key(event: str) -> tuple[list[int], str]:
    """The event's movie key and where it comes from: measured, the base
    event's for variants not in the gallery (DE100B -> DE000B), or assumed."""
    name = event.upper()
    if name in MOVIE_ALPHA_KEYS:
        return list(MOVIE_ALPHA_KEYS[name]), "measured in the game"
    base = name[:2] + "0" + name[3:]
    if base in MOVIE_ALPHA_KEYS:
        return list(MOVIE_ALPHA_KEYS[base]), f"from {base} (not in the movie gallery)"
    return list(MOVIE_KEY_DEFAULT), "assumed (not in the movie gallery)"


def event_movie(event_dir: Path, timeline: EventTimeline) -> dict | None:
    """The event SFD plays for the whole cutscene, black except where it has
    content. 512-pixel-wide SFDs (EV0002/4/5/8/21) hold full-screen
    pre-rendered inserts on camera-only cuts, shown instead of the 3D view
    (EV0002 cut 20 in the game shows only the insert, although the cut's
    camera faces a lit portrait). 256-wide SFDs hold effects (smoke,
    sparkles, fire, lens flares, ...) drawn over black, which the game blends
    over the finished picture with an alpha taken from their brightness."""
    sfd = next(iter(sorted(event_dir.glob("*.SFD"))), None)
    if sfd is None:
        return None
    content = movie_content(sfd)
    out = {"file": f"{timeline.name}.mp4", "content_frames": content}
    if _sfd_width(sfd) >= 512:
        out["mode"] = "inserts"
        out["insert_cuts"] = [c.index for c in timeline.cuts if c.mask == 1 and not c.aux_mask and not c.prop_mask
                              and any(a <= c.end and b >= c.start for a, b in content)]
        out["note"] = ("full-screen pre-rendered inserts: the movie replaces the finished picture (after the "
                       "screen effects) during insert_cuts; "
                       "content_frames = [[first, last event frame], ...] where the movie is not black")
    else:
        out["mode"] = "overlay"
        out["alpha_key"], out["alpha_key_source"] = movie_alpha_key(timeline.name)
        out["note"] = ("effects drawn over black: the game blends each frame over the finished picture "
                       "(after the screen effects), stretched to the whole frame, in display values, with "
                       "alpha = (Y - alpha_key[0]) / (alpha_key[1] - alpha_key[0]) limited to 0-1, Y = 16 + "
                       "219 * (0.299 R + 0.587 G + 0.114 B) with R, G, B on 0-1; content_frames = [[first, "
                       "last event frame], ...] where it is not black")
    return out
def export_event(event_dir: Path | str, out: Path | str, opts: CutsceneOptions | None = None) -> dict:
    opts = opts or CutsceneOptions()
    event_dir = Path(event_dir)
    data_root = event_dir.parent
    timeline: EventTimeline = parse_dh(next(event_dir.glob("*.DH")))
    dp_files = sorted(event_dir.glob("CUT*.DP"))
    cuts: list[tuple] = []
    for cut in timeline.cuts:
        cuts.append((cut, parse_dp(dp_files[cut.index])))
    report = {"event": timeline.name, "frames": timeline.frames, "cuts": len(cuts), "actors": {}, "warnings": [],
              "mode": "embedded" if opts.embed_models else "animation only (models referenced)"}
    if len(dp_files) > len(timeline.cuts):
        report["warnings"].append(f"{len(dp_files) - len(timeline.cuts)} DP file(s) without a DH cut were skipped")

    b = GltfBuilder("hgexport cutscene")
    b.doc["asset"]["extras"] = {"source": timeline.name}
    scene_root = b.node({"name": timeline.name, "extras": _event_extras(event_dir, timeline, report)}, root=True)
    channels: list[tuple[int, str, _Track, str]] = []   # node, path, track, interpolation
    weight_tracks: list[tuple[int, _Track]] = []
    s = opts.scale

    # ------------------------------------------------------------------ camera
    cam_track_t, cam_track_r, fov_track = _Track(), _Track(), _Track()
    for cut, data in cuts:
        cam = data.camera
        cam_track_t.segment(cut.start, [(c[0] * s, c[1] * s, c[2] * s) for c in cam])
        cam_track_r.segment(cut.start, make_continuous([look_at_quat(c[0:3], c[3:6], c[7]) for c in cam]))
        # DP stores a horizontal FOV (same convention as the gameplay cameras,
        # verified against an in-game screenshot); glTF wants vertical.
        fov_track.segment(cut.start, [_vfov(c[6], opts.aspect) for c in cam])
    camera = b.camera({"name": f"{timeline.name}_camera", "type": "perspective",
                       "perspective": {"yfov": _vfov(cuts[0][1].camera[0][6], opts.aspect), "aspectRatio": opts.aspect,
                                       "znear": 0.01, "zfar": 1000.0}})
    cam_node = b.node({"name": f"{timeline.name}_camera", "camera": camera})
    b.add_child(scene_root, cam_node)
    channels.append((cam_node, "translation", cam_track_t, "LINEAR"))
    channels.append((cam_node, "rotation", cam_track_r, "LINEAR"))

    # ------------------------------------------------------------------ actors
    slots = sorted({slot for cut, _d in cuts for slot in cut.actors})
    palettes: dict[int, list[int]] = {}
    roots: dict[int, int] = {}
    for slot in slots:
        pck = _resolve_model(data_root, timeline.name, slot, opts)
        if pck is None:
            report["warnings"].append(f"actor slot {slot}: no model known")
            continue
        model = parse_pck(pck)
        palettes[slot] = model.palette
        roots[slot] = next(b_.index for b_ in model.bones if b_.parent < 0)
        label = f"A{slot:02d}_{model.name}"
        sets = character_texture_sets(pck)
        variant = "cutscene" if "cutscene" in sets else "default"
        extras = {"dp_actor_slot": slot, "model": pck.name}
        if not opts.embed_models:
            extras.update(model_file=_character_file(pck, opts.reference_suffix), texture_variant=variant)
            hidden = cutscene_hidden_parts(model.name, timeline.name)
            if hidden:
                extras["hidden_parts"] = list(hidden)
        group = b.node({"name": label, "extras": extras})
        b.add_child(scene_root, group)
        if opts.embed_models:
            cb = CharacterBuilder(model, sets.get(variant), CharacterOptions(scale=s, bone_prefix=f"{label}_",
                                                                               material_variants=False,
                                                                               exclude_parts=cutscene_hidden_parts(
                                                                                   model.name, timeline.name)),
                                  builder=b, parent_node=group)
            cb.add_skeleton()
            cb.add_mesh()
        else:
            # Bone names as in the character export, so the actions retarget by name.
            cb = CharacterBuilder(model, None, CharacterOptions(scale=s), builder=b, parent_node=group)
            cb.add_skeleton()
            cb.add_skin(name=label)
            if opts.face or opts.hands:
                cb.add_weight_carrier(f"{label}_blendshapes")
        report["actors"][slot] = model.name
        _actor_channels(cb, slot, cuts, channels, weight_tracks, group, opts, report)

    # ------------------------------------------------------------------- rooms
    cut_rooms: list[Path | None] = [None] * len(cuts)
    # pac -> object name -> [node (None until first used), rest translation, rest rotation, node name, parent]
    room_objects: dict[Path, dict[str, list]] = {}
    # pac -> door index -> [node (None until first used), rest translation, rest rotation, node name, parent,
    #                       placement Euler]
    room_doors: dict[Path, dict[int, list]] = {}
    if opts.room:
        if opts.room_override:
            name = opts.room_override.upper()
            pac = data_root / f"ST_{int(name[3:], 16) & ~7:03X}" / f"{name}.PAC"
            picks = [(pac, 1.0, "override")] * len(cuts)
        else:
            picks = rooms_for_cuts(data_root, timeline.name, [d for _c, d in cuts], palettes, roots)
        cut_rooms = [p for p, _f, _m in picks]
        report["rooms"] = [{"cut": c.index, "room": p.stem if p else None, "floor_fit": round(f, 2), "method": m}
                           for (c, _d), (p, f, m) in zip(cuts, picks)]
        distinct = list(dict.fromkeys(p for p in cut_rooms if p is not None))
        for pac in distinct:
            stage = parse_stage(pac)
            if opts.embed_models:
                sb = StageBuilder(stage, StageOptions(scale=s, animations=False, cameras=False), builder=b)
                sb.build(parent=scene_root)
                room_root = sb.root
                room_objects[pac] = {o.name: [sb.object_nodes[o.name], *_rest(o, s), None, room_root]
                                     for o in stage.objects if o.name in sb.object_nodes}
                room_doors[pac] = {d.index: [sb.door_nodes[d.index], *_door_rest(d, s), None, room_root, d.euler]
                                   for d in stage.doors if d.index in sb.door_nodes and d.euler is not None}
            else:
                # Empty stand-in named like the stage export's root node; object
                # nodes are added only for objects the props animate.
                room_root = b.node({"name": stage.name, "extras": {
                    "room_file": _stage_file(pac, opts.reference_suffix),
                    "note": "reference only: import the room file at the origin; child nodes are "
                            "named like its object nodes"}})
                b.add_child(scene_root, room_root)
                room_objects[pac] = {}
                for o in stage.objects:
                    if o.name and o.name not in room_objects[pac] and o.record.vertex_count:
                        room_objects[pac][o.name] = [None, *_rest(o, s),
                                                     f"{stage.name}_obj{o.index:03d}_{o.name}", room_root]
                room_doors[pac] = {d.index: [None, *_door_rest(d, s), f"{stage.name}_door{d.index}", room_root,
                                             d.euler]
                                   for d in stage.doors if d.records and d.position is not None
                                   and d.euler is not None}
            if len(distinct) > 1:
                vis = _Track()
                for (cut, _d), room in zip(cuts, cut_rooms):
                    vis.segment(cut.start, [(1.0, 1.0, 1.0) if room == pac else (0.0, 0.0, 0.0)])
                channels.append((room_root, "scale", vis, "STEP"))

    # ------------------------------------------------------------------- doors
    # DP sections 32 + k animate room door k in the cuts whose DH door mask
    # has bit k (the director's Doors_TurnTo 0x221A90): the key's Y angle
    # replaces the door's heading, which it keeps after the cut. Every shipped
    # door track is section 32 (door 0).
    door_tracks: dict[tuple, _Track] = {}
    door_props: set[int] = set()
    for (cut, data), room in zip(cuts, cut_rooms):
        doors = room_doors.get(room, {})
        for i, prop in enumerate(data.props):
            k = prop.section - 32
            if not (0 <= k < 8 and cut.aux_mask >> k & 1) or k not in doors:
                continue
            door_props.add(id(prop))
            entry = doors[k]
            if entry[0] is None:
                entry[0] = b.node({"name": entry[3], "translation": list(entry[1]), "rotation": list(entry[2])})
                b.add_child(entry[4], entry[0])
            track = door_tracks.setdefault((room, k), _Track())
            if not track.times and cut.start > 0:
                track.segment(0, [entry[2]])              # placement heading until the door moves
            ex, _ey, ez = entry[5]
            track.segment(cut.start, [euler_to_quat(ex, v[1], ez) for v in prop.samples], cut.frames)
    for (room, k), track in door_tracks.items():
        channels.append((room_doors[room][k][0], "rotation", track, "LINEAR"))
    report["doors_animated"] = sorted(f"{room.stem}_door{k}" for room, k in door_tracks)

    # ------------------------------------------------------------------- props
    prop_nodes: dict[str, int] = {}
    prop_tracks: dict[str, tuple[_Track, _Track]] = {}
    bound_rest: dict[str, tuple] = {}
    for (cut, data), room in zip(cuts, cut_rooms):
        seen: dict[str, int] = {}
        objects = room_objects.get(room, {})
        for prop in data.props:
            if id(prop) in door_props:
                continue
            base = f"{prop.section}_{prop.name}"
            seen[base] = seen.get(base, 0) + 1
            target = objects.get(prop.name) if seen[base] == 1 else None
            key = f"room:{room}:{prop.name}" if target else f"{base}_{seen[base]}"
            if key not in prop_nodes:
                if target:
                    if target[0] is None:
                        target[0] = b.node({"name": target[3], "translation": list(target[1]),
                                            "rotation": list(target[2])})
                        b.add_child(target[4], target[0])
                    prop_nodes[key] = target[0]
                    bound_rest[key] = (target[1], target[2])
                else:
                    label = prop.name if seen[base] == 1 else f"{prop.name}.{seen[base]}"
                    node = b.node({"name": f"prop_{label}", "extras": {"dp_section": prop.section}})
                    b.add_child(scene_root, node)
                    prop_nodes[key] = node
                prop_tracks[key] = (_Track(), _Track())
            tt, tr = prop_tracks[key]
            tt.segment(cut.start, [(v[3] * s, v[4] * s, v[5] * s) for v in prop.samples], cut.frames)
            tr.segment(cut.start, make_continuous([euler_to_quat(*v[:3]) for v in prop.samples]), cut.frames)
        # room objects that are not animated in this cut return to their rest pose
        for key, (rest_t, rest_q) in bound_rest.items():
            tt, tr = prop_tracks[key]
            if tt.times and tt.times[-1] < cut.start / FPS - BOUNDARY:
                tt.segment(cut.start, [tuple(rest_t)])
                tr.segment(cut.start, [rest_q])
    for key, node in prop_nodes.items():
        tt, tr = prop_tracks[key]
        if key in bound_rest and tt.times and tt.times[0] > 0:
            # rest pose before the first animated cut
            rest_t, rest_q = bound_rest[key]
            tt.times.insert(0, 0.0); tt.values.insert(0, tuple(rest_t))
            tr.times.insert(0, 0.0); tr.values.insert(0, rest_q)
            if tt.times[1] - tt.times[0] > BOUNDARY:
                tt.times.insert(1, tt.times[1] - BOUNDARY); tt.values.insert(1, tuple(rest_t))
                tr.times.insert(1, tr.times[1] - BOUNDARY); tr.values.insert(1, rest_q)
        channels.append((node, "translation", tt, "LINEAR"))
        channels.append((node, "rotation", tr, "LINEAR"))
    report["props_bound_to_room_objects"] = sorted(k.split(":")[-1] for k in bound_rest)

    # The room objects listed in the DH prop groups are shown only in the cuts
    # that animate their group; groups the event uses at all are hidden in the
    # other cuts (Cutscene_StartShot; groups in order, the last one decides).
    used_groups = 0
    for cut, _d in cuts:
        used_groups |= cut.prop_mask
    shown: dict[tuple, _Track] = {}
    cut_state: dict[int, dict[str, bool]] = {}
    for (cut, _data), room in zip(cuts, cut_rooms):
        objects = room_objects.get(room, {})
        state: dict[str, bool] = {}
        cut_state[cut.index] = state
        for g in range(8):
            if not used_groups >> g & 1:
                continue
            for name in timeline.prop_groups.get(g, []):
                if name in objects:
                    state[name] = bool(cut.prop_mask >> g & 1)
        for name, on in state.items():
            target = objects[name]
            if target[0] is None:
                target[0] = b.node({"name": target[3], "translation": list(target[1]), "rotation": list(target[2])})
                b.add_child(target[4], target[0])
            shown.setdefault((room, name), _Track()).segment(cut.start, [(1.0, 1.0, 1.0) if on else (0.0, 0.0, 0.0)])
    for (room, name), track in shown.items():
        channels.append((room_objects[room][name][0], "scale", track, "STEP"))
    report["props_shown_per_cut"] = sorted({name for _room, name in shown})

    # Event doors: some cutscenes swing a room object of their own (ev_door,
    # ev_door_a / ev_ddor_b, demo_door0..2) that stands in a gameplay door's
    # doorway; the gameplay door is hidden while it is shown (EV0004, EV0011,
    # EV0027, EV0028), else the closed door covers it.
    replaced = []
    for pac, doors in room_doors.items():
        objects = room_objects.get(pac, {})
        event_doors = [n for n in objects if re.search(r"d[od]or", n, re.IGNORECASE)]
        for k, entry in doors.items():
            near = [n for n in event_doors
                    if math.dist((objects[n][1][0], objects[n][1][2]), (entry[1][0], entry[1][2])) < 15 * s]
            if not near:
                continue
            track = _Track()
            for (cut, _data), room in zip(cuts, cut_rooms):
                if room == pac:
                    present = any(cut_state.get(cut.index, {}).get(n) for n in near)
                    track.segment(cut.start, [(0.0, 0.0, 0.0) if present else (1.0, 1.0, 1.0)])
            if not any(v == (0.0, 0.0, 0.0) for v in track.values):
                continue
            if entry[0] is None:
                entry[0] = b.node({"name": entry[3], "translation": list(entry[1]), "rotation": list(entry[2])})
                b.add_child(entry[4], entry[0])
            channels.append((entry[0], "scale", track, "STEP"))
            replaced.append(f"{pac.stem}_door{k}")
    report["doors_replaced_by_event_doors"] = sorted(replaced)

    # --------------------------------------------------------------- animation
    samplers, chans = [], []
    for node, path, track, interp in channels:
        if not track.times:
            continue
        kind = {"translation": "VEC3", "rotation": "VEC4", "scale": "VEC3"}[path]
        if path == "rotation":
            track.values = make_continuous(track.values)
        samplers.append(b.anim_sampler(track.times, track.values, kind, interp))
        chans.append({"sampler": len(samplers) - 1, "target": {"node": node, "path": path}})
    for node, track in weight_tracks:
        samplers.append(b.anim_sampler(track.times, track.values, "WEIGHTS", "STEP"))
        chans.append({"sampler": len(samplers) - 1, "target": {"node": node, "path": "weights"}})
    # Camera field of view through KHR_animation_pointer (ignored by importers
    # without support; the per-cut values are also listed in extras).
    samplers.append(b.anim_sampler(fov_track.times, fov_track.values, "SCALAR", "STEP"))
    chans.append({"sampler": len(samplers) - 1, "target": {
        "path": "pointer", "extensions": {"KHR_animation_pointer": {
            "pointer": f"/cameras/{camera}/perspective/yfov"}}}})
    b.extensions_used.add("KHR_animation_pointer")
    b.animation({"name": timeline.name, "samplers": samplers, "channels": chans,
                 "extras": {"fps": FPS, "cuts": [{"cut": c.index, "dp": dp_files[c.index].name,
                                                   "start_frame": c.start, "end_frame": c.end,
                                                   "start_time": c.start / FPS, "actors": c.actors,
                                                   "horizontal_fov": d.camera[0][6]} for c, d in cuts]}})
    b.write(Path(out))
    return report


def _actor_channels(cb: CharacterBuilder, slot: int, cuts: list, channels: list, weight_tracks: list,
                    group: int, opts: CutsceneOptions, report: dict) -> None:
    model, s = cb.model, opts.scale
    bones = model.bones
    rot = {b.index: _Track() for b in bones}
    trans = {b.index: _Track() for b in bones}
    uses_trans: set[int] = set()
    vis = _Track()
    weights = _Track()
    n_targets = len(cb.target_names)
    rest_q = [euler_to_quat(*b.euler) for b in bones]
    rest_t = [tuple(c * s for c in b.translation) for b in bones]
    last_q, last_t = list(rest_q), list(rest_t)
    # The hand mixers are persistent state: a cut without a hand track keeps
    # the previous pose, starting from the game's resting pose (shape 1).
    last_w = cb._default_weights(n_targets) if n_targets else []
    contact: dict[str, list] = {}
    points: dict[str, list] = {}
    for cut, data in cuts:
        n = data.frames
        bank = data.actors.get(slot)
        present = bank is not None
        vis.segment(cut.start, [(1.0, 1.0, 1.0) if present else (0.0, 0.0, 0.0)])
        frame_q = {b: [last_q[b]] * n for b in rot}
        frame_t = {b: [last_t[b]] * n for b in rot}
        frame_w = [list(last_w) for _ in range(n)]
        if present:
            entry = bank.entries[0]
            for g in entry.slots[1:]:
                if not g:
                    continue
                for t in g.tracks:
                    if not 0 <= t.target < len(model.palette):
                        report["warnings"].append(f"slot {slot}: palette index {t.target} missing in {model.name}")
                        continue
                    bone = model.palette[t.target]
                    sm = t.samples if len(t.samples) == n else t.samples[:1] * n
                    if t.codec == 1:
                        frame_t[bone] = [(v[0] * s, v[1] * s, v[2] * s) for v in sm]
                        uses_trans.add(bone)
                    else:
                        frame_q[bone] = [euler_to_quat(v[0], v[1], v[2]) for v in sm]
                        if t.has_translation:
                            frame_t[bone] = [(v[3] * s, v[4] * s, v[5] * s) for v in sm]
                            uses_trans.add(bone)
            if n_targets and entry.slots[0]:
                frame_w = _morph_weights(cb, entry.slots[0], n, opts, last_w)
            if opts.feet:
                feet = cb.feet.clip_extras(entry.slots[0], [g for g in entry.slots[1:] if g], n)
                for bone, spans in feet.get("foot_contact", {}).items():
                    contact.setdefault(bone, []).extend([a + cut.start, b + cut.start] for a, b in spans)
                for bone, rows in feet.get("foot_points", {}).items():
                    points.setdefault(bone, []).extend([r[0] + cut.start] + r[1:] for r in rows)
        for bone in rot:
            rot[bone].segment(cut.start, frame_q[bone])
            trans[bone].segment(cut.start, frame_t[bone])
            last_q[bone], last_t[bone] = frame_q[bone][-1], frame_t[bone][-1]
        weights.segment(cut.start, frame_w)
        last_w = frame_w[-1]
    channels.append((group, "scale", vis, "STEP"))
    if contact or points:
        extras = cb.b.doc["nodes"][group].setdefault("extras", {})
        if contact:
            extras["foot_contact"] = contact
        if points:
            extras["foot_points"] = points
        extras["foot_controls"] = {**cb.feet.info(), "frames": "event frames"}
    for b in bones:
        if any(v != rest_q[b.index] for v in rot[b.index].values):
            channels.append((cb.bone_nodes[b.index], "rotation", rot[b.index], "LINEAR"))
        if b.index in uses_trans:
            channels.append((cb.bone_nodes[b.index], "translation", trans[b.index], "LINEAR"))
    if n_targets and cb.mesh_node is not None and any(any(r) for r in weights.values):
        weight_tracks.append((cb.mesh_node, weights))


def _morph_weights(cb: CharacterBuilder, controls, n: int, opts: CutsceneOptions,
                   previous: list[float] | None = None) -> list[list[float]]:
    """Facial vectors -7..-10 hold 16 controls (control k drives shape k of
    every facial piece; the full mixer uses 1..15). Hand mixers -11/-12 blend
    two selected shapes of descriptors 1 and 2: s0 * (1 - b) + s1 * b."""
    rows = [[0.0] * len(cb.target_names) for _ in range(n)]
    by_target = {t.target: t for t in controls.tracks}

    def samples(t):
        return t.samples if len(t.samples) == n else t.samples[:1] * n

    if opts.face and cb.face_targets and all(k in by_target for k in (-7, -8, -9, -10)):
        vectors = [samples(by_target[k]) for k in (-7, -8, -9, -10)]
        for f in range(n):
            ctrl = [c for v in vectors for c in v[f]]
            for shape, target in cb.face_targets.items():
                if shape < 16:
                    rows[f][target] = ctrl[shape]
    if opts.hands:
        for tgt, desc in ((-11, 1), (-12, 2)):
            mapping = cb.descriptor_targets.get(desc)
            if not mapping:
                continue
            if tgt not in by_target:
                if previous:                     # no track: the hand keeps its pose
                    for target in mapping.values():
                        for row in rows:
                            row[target] = previous[target]
                continue
            for f, (s0, s1, blend) in enumerate(samples(by_target[tgt])):
                for shape, w in ((int(s0), 1.0 - blend), (int(s1), blend)):
                    if shape in mapping:
                        rows[f][mapping[shape]] += w
    return rows
