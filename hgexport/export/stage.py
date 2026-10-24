"""Stage PAC -> glTF: room geometry, doors, named objects, textures."""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from pathlib import Path

from ..formats.pck import strip_triangles
from ..formats.stage import DrawRecord, Stage, parse_door_motion, parse_stage
from ..gltf.builder import GltfBuilder
from ..util.mathutil import euler_to_quat, look_at_quat, make_continuous
from .alpha import assign_modes
from .look import LOOK_NOTE, engine_looks, room_variants
from .meshutil import VertexWelder
from .textures import MaterialFactory, TextureSet


@dataclass
class StageOptions:
    scale: float = 0.1
    vertex_colors: bool = True
    doors: bool = True
    objects: bool = True
    effects: bool = True           # group 5 (blended, no depth write)
    game_alpha: bool = False       # keep the blend flag as BLEND (default: alpha mode per element)
    bleed: bool = False            # fill the colour under alpha-0 texels (no dark fringes; not the game's look)
    unlit: bool = True             # lighting is baked into vertex colours
    animations: bool = True        # atlas, door, fan and shape-object clips
    door_motion: Path | None = None   # *_D000.MTN door-angle bank (FIN_D000 = player)
    extra_door_motion: list = field(default_factory=list)   # pursuer *_D000.MTN banks embedded as extra clips
    cameras: bool = True           # gameplay camera rails (slot 6) as animated cameras
    navmesh: bool = False          # walkable-floor navigation mesh as an extra node
    lights: bool = True            # slot-4 character lights as KHR_lights_punctual nodes
    emissive_glow: float = 0.0     # > 0: glow geometry (lamps; export/look.py) emissive at this strength
    updates_per_second: float = 30.0


# Curtain (category-3 shape object) controllers per room, from the
# executable: mode -> (start update, end update, reversed). Shape-1 weight is
# clamp((v - start) / (end - start)) of a stage variable advanced once per
# update, reversed when flagged.
_C014 = {0: (43, 54, False), 1: (12, 21, True), 2: (16, 28, True), 5: (11, 18, True)}
_C04B = {0: (12, 30, True), 1: (12, 28, False)}
CURTAINS = {"ST_014": _C014, "ST_0E5": _C014, "ST_04B": _C04B, "ST_04E": _C04B, "ST_06A": _C04B,
            "ST_05C": {0: (43, 56, False), 1: (12, 32, True), 2: (7, 18, True)}}

# Verified constant-speed fan controllers (from the executable): Euler
# component that advances and radians added per game update.
FANS = {"ST_01A": (1, 0.020070), "ST_021": (1, 0.020070), "ST_031": (1, 0.020070),
        "ST_032": (1, 0.020070), "ST_037": (1, 0.020070), "ST_0E0": (1, 0.020070),
        "ST_092": (0, 0.069814)}


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


# PS2 vertex colour 0x80 = 1.0 (texture modulate is (tex * vc) >> 7), applied
# in gamma space; glTF COLOR_0 is linear and multiplies the base colour.
# Untextured triangles show the vertex colour itself (0-255).
_RGB_LUT = [round(_srgb_to_linear(min(1.0, v / 128.0)) * 255) for v in range(256)]
_A_LUT = [min(255, v * 2) for v in range(256)]
_RGB_DIRECT = [round(_srgb_to_linear(v / 255.0) * 255) for v in range(256)]


class _Prim:
    def __init__(self):
        self.welder = VertexWelder()
        self.tris: list[tuple[int, int, int]] = []
        self.sources: list[str] = []
        self.targets: dict[int, list] = {}


class StageBuilder:
    def __init__(self, stage: Stage, opts: StageOptions, builder: GltfBuilder | None = None):
        self.stage = stage
        self.opts = opts
        self.b = builder or GltfBuilder("hgexport stage")
        self.factory = MaterialFactory(self.b, stage.textures, stage.name,
                                       [f"{stage.name}.PAC#{i}" for i in range(len(stage.textures))], opts.unlit,
                                       opts.bleed)
        self._shadow: dict[int, bool] = {}
        self.pac_path: Path | None = None
        self.report = {"stage": stage.name, "nodes": [], "triangles": 0, "clips": []}
        self.clips: list[dict] = []

    # ----------------------------------------------------------------- helpers
    def is_shadow_texture(self, tex: int) -> bool:
        """Blend-only textures whose visible texels are all black darken the
        floor beneath objects (baked contact shadows)."""
        if tex not in self._shadow:
            ok = 0 <= tex < len(self.stage.textures)
            if ok:
                px = self.factory.rgba(tex)
                alphas = px[3::4]
                ok = 0 in alphas or any(a < 255 for a in alphas)
                ok = ok and all(px[i + 3] == 0 or px[i:i + 3] == b"\0\0\0" for i in range(0, len(px), 4))
            self._shadow[tex] = ok
        return self._shadow[tex]

    def _modes(self, rec: DrawRecord, blend: bool) -> list[str]:
        """Alpha mode per strip triangle of a record (export/alpha.py), from its
        texels scaled by the vertex alpha (0x80 = 1)."""
        tris = strip_triangles(rec.restart)
        sampler = self.factory.sampler(rec.material)

        def texels(i: int, reach: bool) -> list[int]:
            t = tris[i]
            scale = min(1.0, sum(rec.colors[v][3] for v in t) / (3 * 0x80))
            alphas = sampler.texels([rec.uvs[v] for v in t], reach) if sampler else [255]
            return [min(255, round(a * scale)) for a in alphas]

        def fades(i: int) -> bool:
            return any(rec.colors[v][3] < 0x80 for v in tris[i])

        return assign_modes(tris, lambda v: tuple(rec.positions[v]), texels, blend, self.opts.game_alpha, fades)

    def _add_record(self, prims: dict, rec: DrawRecord, positions, modes: list[str] | str, label: str,
                    shape_delta: list | None = None) -> int:
        """``modes``: one alpha mode for the whole record or one per strip triangle."""
        s = self.opts.scale
        repeat = any(not (-0.001 <= c <= 1.001) for uv in rec.uvs for c in uv)
        tris_by_mode: dict[str, list] = {}
        for i, t in enumerate(strip_triangles(rec.restart)):
            tris_by_mode.setdefault(modes if isinstance(modes, str) else modes[i], []).append(t)
        count = 0
        rgb, alpha = (_RGB_LUT, _A_LUT) if rec.material >= 0 else (_RGB_DIRECT, range(256))
        for m, tris in tris_by_mode.items():
            prim = prims.setdefault((rec.material, m, repeat), _Prim())
            prim.sources.append(label)
            remap = {}
            for t in tris:
                out = []
                for v in t:
                    if v not in remap:
                        p = positions[v]
                        r, g, b_, a = rec.colors[v]
                        row = ((p[0] * s, p[1] * s, p[2] * s), rec.uvs[v],
                               (rgb[r], rgb[g], rgb[b_], alpha[a]))
                        key = row if shape_delta is None else (row, shape_delta[v])
                        before = len(prim.welder.rows)
                        idx = prim.welder.add(key, row)
                        if shape_delta is not None and len(prim.welder.rows) > before:
                            prim.targets.setdefault(0, []).append(tuple(c * s for c in shape_delta[v]))
                        remap[v] = idx
                    out.append(remap[v])
                if len(set(out)) == 3:        # welding can collapse strip triangles to lines
                    prim.tris.append(tuple(out))
                    count += 1
        return count

    def _mesh(self, name: str, prims: dict, extras: dict | None = None, glow: bool = False) -> int | None:
        """``glow``: the room's glow geometry (export/look.py), on its own
        materials (emissive with ``emissive_glow``)."""
        b = self.b
        primitives = []
        has_targets = any(p.targets for p in prims.values())
        for (tex, mode, repeat), prim in prims.items():
            if not prim.tris:
                continue
            rows = prim.welder.rows
            attrs = {"POSITION": b.positions([r[0] for r in rows]),
                     "TEXCOORD_0": b.vec2([r[1] for r in rows])}
            if self.opts.vertex_colors:
                attrs["COLOR_0"] = b.colors_u8([r[2] for r in rows])
            mat_name = None
            if repeat and 0 <= tex < len(self.stage.textures):
                mat_name = f"{self.stage.name}_m{tex:03d}_{mode.lower()}_tiled"
            if glow:
                mat_name = (mat_name or (f"{self.stage.name}_m{tex:03d}_{mode.lower()}" if tex >= 0
                                         else f"{self.stage.name}_untextured")) + "_glow"
            material = self.factory.material(tex, mode, repeat=repeat, name=mat_name)
            if glow:
                self._glow_material(material, tex, [r[2] for r in rows])
            out = {"attributes": attrs, "indices": b.indices([i for t in prim.tris for i in t]),
                   "material": material, "mode": 4}
            if has_targets:
                delta = prim.targets.get(0) or [(0.0, 0.0, 0.0)] * len(rows)
                out["targets"] = [{"POSITION": b.positions(delta)}]
            primitives.append(out)
            self.report["triangles"] += len(prim.tris)
        if not primitives:
            return None
        mesh = {"name": name, "primitives": primitives}
        if has_targets:
            mesh["weights"] = [0.0]
            mesh["extras"] = {"targetNames": ["shape_1"]}
        if extras:
            mesh["extras"] = {**mesh.get("extras", {}), **extras}
        return b.mesh(mesh)

    def _glow_material(self, index: int, tex: int, colors: list) -> None:
        mat = self.b.doc["materials"][index]
        mat.setdefault("extras", {})["glow"] = True
        strength = self.opts.emissive_glow
        if strength <= 0 or "emissiveFactor" in mat:
            return
        # emission = the texture x the mean vertex colour (glTF emission has no vertex colours)
        n = max(1, len(colors))
        mat["emissiveFactor"] = [round(sum(c[k] for c in colors) / n / 255.0, 4) for k in range(3)]
        base = mat.get("pbrMetallicRoughness", {}).get("baseColorTexture")
        if base is not None and tex >= 0:
            mat["emissiveTexture"] = dict(base)
        ext = mat.setdefault("extensions", {})
        ext.pop("KHR_materials_unlit", None)          # unlit materials ignore emission
        ext["KHR_materials_emissive_strength"] = {"emissiveStrength": strength}
        self.b.extensions_used.add("KHR_materials_emissive_strength")

    # ---------------------------------------------------------------- building
    def build(self, parent: int | None = None) -> GltfBuilder:
        st, b, s = self.stage, self.b, self.opts.scale
        extras = {"source": f"{st.name}.PAC"}
        if st.lighting:
            extras["character_lighting"] = {
                "ambient_rgb": [round(c, 4) for c in st.lighting.ambient], "note": LIGHTING_NOTE}
        if st.effects:
            extras["room_effects"] = dict(st.effects)
            variants = room_variants(st.name)
            if variants:
                extras["room_effects"]["script_variants"] = variants
            extras["room_effects"]["engine_look"] = engine_looks(st.effects, variants)
            extras["room_effects_note"] = ("PAC slot 13 screen effects: fog (RGBA colours, start/end distance in "
                                           "game units), depth effect (four distances). " + LOOK_NOTE)
        root = b.node({"name": st.name, "extras": extras}, root=parent is None)
        if parent is not None:
            b.add_child(parent, root)
        self.root = root
        self.object_nodes: dict[str, int] = {}
        self.door_nodes: dict[int, int] = {}      # door index (PAC slot 7/8) -> node
        buckets: dict[tuple[str, int], dict] = {}
        for g, records in sorted(st.groups.items()):
            if g == 5 and not self.opts.effects:
                continue
            for i, rec in enumerate(records):
                if not rec.vertex_count:
                    continue
                blend = True if g == 5 else rec.blend
                if g == 4 and self.opts.animations and self._atlas(root, rec, i):
                    continue
                kind = {2: "glow", 4: "animated", 5: "effects"}.get(g, "main")
                if kind == "main" and blend and self.is_shadow_texture(rec.material):
                    kind = "shadows"
                # Shadows and effects (some additive) stay blended as drawn.
                mode = "BLEND" if kind in ("shadows", "effects") else self._modes(rec, blend)
                prims = buckets.setdefault((kind, rec.selector), {})
                self._add_record(prims, rec, rec.world_positions(), mode, f"g{g}r{i}")
        for (kind, sel), prims in sorted(buckets.items()):
            name = f"{st.name}_{kind}" + (f"_sel{sel:03d}" if sel else "")
            extras = {"visibility_selector": sel, "kind": kind}
            if kind == "effects":
                extras["note"] = "PS2 draws these without depth writes; some use additive blending"
            if kind == "glow":
                extras["note"] = ("lamps and lights: drawn after the tint and fog and marking the bloom "
                                  "(export/look.py)")
            mesh = self._mesh(name, prims, extras, glow=kind == "glow")
            if mesh is not None:
                b.add_child(root, b.node({"name": name, "mesh": mesh, "extras": extras}))
                self.report["nodes"].append(name)
        if self.opts.doors:
            for door in st.doors:
                prims: dict = {}
                for i, rec in enumerate(door.records):
                    self._add_record(prims, rec, rec.world_positions(), self._modes(rec, rec.blend),
                                     f"door{door.index}r{i}")
                name = f"{st.name}_door{door.index}"
                mesh = self._mesh(name, prims)
                if mesh is None:
                    continue
                node = {"name": name, "mesh": mesh, "extras": {"door_index": door.index,
                                                                "resource_id": door.resource_id}}
                if door.position is not None:
                    node["translation"] = [c * s for c in door.position]
                    node["rotation"] = list(euler_to_quat(*door.euler))
                index = b.node(node)
                b.add_child(root, index)
                self.door_nodes[door.index] = index
                self.report["nodes"].append(name)
                if self.opts.animations and door.euler is not None and door.resource_id not in (None, -1):
                    self._door_clips(index, name, door.euler)
        if self.opts.objects:
            for obj in st.objects:
                rec = obj.record
                prims = {}
                delta = None
                if obj.shapes:
                    delta = [tuple(q - p for p, q in zip(a, c)) for a, c in zip(obj.shapes[0], obj.shapes[1])]
                    if not any(any(d) for d in delta):
                        delta = None
                self._add_record(prims, rec, rec.positions, self._modes(rec, obj.blend), obj.name, delta)
                name = f"{st.name}_obj{obj.index:03d}_{obj.name or 'unnamed'}"
                glow = obj.name.lower().startswith("g_")       # drawn in the glow layer (export/look.py)
                mesh = self._mesh(name, prims, {"category": obj.category}, glow=glow)
                if mesh is None:
                    continue
                node = {"name": name, "mesh": mesh, "translation": [c * s for c in obj.translation],
                        "rotation": list(euler_to_quat(*obj.euler)),
                        "extras": {"object_index": obj.index, "object_name": obj.name, "category": obj.category,
                                   **({"kind": "glow"} if glow else {})}}
                index = b.node(node)
                b.add_child(root, index)
                self.object_nodes.setdefault(obj.name, index)
                self.report["nodes"].append(name)
                if self.opts.animations:
                    if delta is not None:
                        self._shape_clips(index, name)
                    fan = FANS.get(st.name.upper())
                    if fan and obj.name.lower().startswith("fan"):
                        self._spin_clip(index, name, obj.euler, *fan)
        if self.opts.lights and st.lighting and st.lighting.lights:
            self._lights(root)
        if self.opts.animations:
            self._object_motion_clips()
        if self.opts.cameras:
            for cam in st.cameras:
                self._camera_rail(root, cam)
        if self.opts.navmesh:
            self._navmesh(root)
        for clip in self.clips:
            b.animation(clip)
            self.report["clips"].append(clip["name"])
        return b

    # ---------------------------------------------------------------- lights
    def _lights(self, root: int) -> None:
        """Slot-4 character lights. The room geometry is unlit (lighting is
        baked into its vertex colours); these lights only shade characters."""
        b, s = self.b, self.opts.scale
        defs = b.doc.setdefault("extensions", {}).setdefault("KHR_lights_punctual", {"lights": []})["lights"]
        b.extensions_used.add("KHR_lights_punctual")
        group = b.node({"name": f"{self.stage.name}_lights", "extras": {"note": LIGHTING_NOTE}})
        b.add_child(root, group)
        for light in self.stage.lighting.lights:
            peak = max(light.color) or 1.0
            full = LUX_PER_UNIT * peak * max(light.intensity, 0.0)   # the game scales RGB by intensity
            gl = {"name": f"{self.stage.name}_light{light.index:02d}",
                  "color": [round(max(c, 0.0) / peak, 4) for c in light.color]}
            node = {"name": gl["name"], "extras": {
                "game_rgb": [round(c, 4) for c in light.color], "game_intensity": round(light.intensity, 4),
                "range_game_units": light.range, "param_unused": light.param, "mask_bit": light.index}}
            pos = [c * s for c in light.position]
            if light.directional:
                gl["type"] = "directional"
                gl["intensity"] = round(full, 3)                      # lux
                # shines from the stored far point towards the room
                node["rotation"] = list(look_at_quat((0.0, 0.0, 0.0), [-c for c in pos]))
                node["extras"]["position_game_units"] = list(light.position)
            else:
                gl["type"] = "point"
                gl["range"] = round(light.range * s, 5)
                # candela: inverse-square illuminance matches the game's linear
                # falloff at a typical character distance (half the range, at
                # most 2.5 m), so wide-range lights do not blow out nearby actors
                d = min(light.range * s / 2, MATCH_DISTANCE)
                gl["intensity"] = round(full * (1 - d / (light.range * s)) * d * d, 3)
                node["translation"] = pos
            defs.append(gl)
            node["extensions"] = {"KHR_lights_punctual": {"light": len(defs) - 1}}
            b.add_child(group, b.node(node))
        self.report["lights"] = len(self.stage.lighting.lights)

    # -------------------------------------------------------------- animation
    def _object_motion_clips(self) -> None:
        """PAC slot 15: authored motion of interactive named objects (levers,
        drawers, door pairs, lids...), absolute transforms per game update."""
        s, fps = self.opts.scale, self.opts.updates_per_second
        for motion in self.stage.object_motion:
            node = self.object_nodes.get(motion.name)
            if node is None:
                self.report.setdefault("warnings", []).append(f"slot 15 motion '{motion.name}' has no object")
                continue
            node_name = self.b.doc["nodes"][node]["name"]
            for k, ch in enumerate(motion.channels):
                times = [i / fps for i in range(len(ch.frames))]
                extras = {"source": "PAC slot 15", "object": motion.name, "channel": k, "kind": ch.kind,
                          "frames": len(ch.frames)}
                if ch.kind == "rotation+translation":
                    rots = [f[0] for f in ch.frames]
                    pos = [f[1] for f in ch.frames]
                elif ch.kind == "rotation":
                    rots, pos = [f for f in ch.frames], None
                else:
                    rots, pos = None, [f for f in ch.frames]
                clip = None
                if rots is not None:
                    quats = make_continuous([euler_to_quat(*e) for e in rots])
                    clip = self._clip(f"{node_name}_motion{k}", node, "rotation", times, quats, "VEC4",
                                      extras=extras)
                if pos is not None:
                    clip = self._clip(f"{node_name}_motion{k}", node, "translation", times,
                                      [tuple(c * s for c in p) for p in pos], "VEC3", extras=extras, clip=clip)

    def _clip(self, name: str, node: int, path: str, times: list[float], values: list, kind: str,
              interp: str = "LINEAR", extras: dict | None = None, clip: dict | None = None) -> dict:
        b = self.b
        if clip is None:
            clip = {"name": name, "samplers": [], "channels": [], "extras": extras or {}}
            self.clips.append(clip)
        clip["samplers"].append({"input": b.times(times), "output": b.accessor(values, kind),
                                 "interpolation": interp})
        clip["channels"].append({"sampler": len(clip["samplers"]) - 1, "target": {"node": node, "path": path}})
        return clip

    def _atlas(self, root: int, rec: DrawRecord, index: int) -> bool:
        """Group-4 records cycle through the UV cells of a texture atlas.
        Exported as one mesh per frame under a parent node, switched by STEP
        scale keys (portable to every glTF importer)."""
        frames = (rec.control >> 16) & 0xFF
        hold = max(1, rec.fourth & 0x0F)
        us = [u for u, _v in rec.uvs]
        vs = [v for _u, v in rec.uvs]
        w, h = max(us) - min(us), max(vs) - min(vs)
        if frames < 2 or w <= 0 or h <= 0 or min(us) > 1e-4 or min(vs) > 1e-4:
            return False
        cols, rows = round(1 / w), round(1 / h)
        if abs(cols * w - 1) > 1e-3 or abs(rows * h - 1) > 1e-3 or frames > cols * rows:
            return False
        st, b = self.stage, self.b
        name = f"{st.name}_atlas{index:02d}" + (f"_sel{rec.selector:03d}" if rec.selector else "")
        # (fourth >> 4) & 15: 2 = play once and hold the last cell (0x25CBF0;
        # unused by the shipped rooms), other values pick GS blend setups.
        parent = b.node({"name": name, "extras": {"frames": frames, "kind": "animated_texture",
                                                  "visibility_selector": rec.selector,
                                                  "hold_updates": hold, "draw_mode": (rec.fourth >> 4) & 15}})
        b.add_child(root, parent)
        frame_recs = []
        for f in range(frames):
            du, dv = (f % cols) * w, (f // cols) * h
            frame_recs.append(dataclasses.replace(rec, uvs=[(u + du, v + dv) for u, v in rec.uvs]))
        # One mode per element over all cells, so every frame uses the same materials.
        rank = {"OPAQUE": 0, "TEST": 1, "MASK": 2, "BLEND": 3}
        modes = [max(ms, key=rank.get) for ms in zip(*(self._modes(fr, rec.blend) for fr in frame_recs))]
        children = []
        for f, frame_rec in enumerate(frame_recs):
            prims: dict = {}
            self._add_record(prims, frame_rec, rec.world_positions(), modes, f"atlas{index}f{f}")
            mesh = self._mesh(f"{name}_f{f:02d}", prims)
            child = b.node({"name": f"{name}_f{f:02d}", "mesh": mesh, "scale": [1.0 if f == 0 else 0.0] * 3})
            b.add_child(parent, child)
            children.append(child)
        rate = self.opts.updates_per_second / hold
        times = [k / rate for k in range(frames + 1)]
        clip = None
        for f, child in enumerate(children):
            values = [(1.0, 1.0, 1.0) if k % frames == f else (0.0, 0.0, 0.0) for k in range(frames + 1)]
            clip = self._clip(f"{name}_loop", child, "scale", times, values, "VEC3", "STEP",
                              {"loop": True, "frames_per_second": rate,
                               "note": "one cell per hold frames at the game's 30 fps (verified in the executable)"}, clip)
        self.report["nodes"].append(name)
        return True

    def _door_clips(self, node: int, name: str, euler) -> None:
        banks = ([("", self.opts.door_motion)] if self.opts.door_motion else []) +             [(f"{Path(p).stem.split('_')[0].upper()}_", p) for p in self.opts.extra_door_motion]
        if not banks:
            return
        if not hasattr(self, "_door_curves"):
            self._door_curves = {str(p): parse_door_motion(p) for _prefix, p in banks}
        fps = self.opts.updates_per_second
        for prefix, path in banks:
            for i, curve in enumerate(self._door_curves[str(path)]):
                if len(curve) < 2:
                    continue
                quats = make_continuous([euler_to_quat(euler[0], euler[1] + math.radians(a), euler[2])
                                         for a in curve])
                self._clip(f"{name}_{prefix}{door_curve_label(i)}", node, "rotation",
                           [k / fps for k in range(len(curve))], quats, "VEC4",
                           extras={"source": Path(path).name, "curve": i})

    def _shape_clips(self, node: int, name: str) -> None:
        """Category-3 objects (curtains etc.) blend between two stored shapes.
        Rooms with a known controller get its native transitions (one key per
        game update, including the initial hold); others get generic
        one-second open/close clips."""
        modes = CURTAINS.get(self.stage.name.upper())
        if modes:
            fps = self.opts.updates_per_second
            for mode, (start, end, reverse) in modes.items():
                weights = []
                for v in range(end + 1):
                    w = (min(max(v, start), end) - start) / (end - start)
                    weights.append(1.0 - w if reverse else w)
                self._clip(f"{name}_mode{mode}", node, "weights", [v / fps for v in range(end + 1)], weights,
                           "SCALAR", extras={"controller_mode": mode, "note": "native curtain transition"})
            return
        times = [0.0, 1.0]
        self._clip(f"{name}_open", node, "weights", times, [0.0, 1.0], "SCALAR",
                   extras={"note": "generic blend to stored shape 1"})
        self._clip(f"{name}_close", node, "weights", times, [1.0, 0.0], "SCALAR",
                   extras={"note": "generic blend back to stored shape 0"})

    def _camera_rail(self, root: int, cam) -> None:
        """Gameplay cameras slide along a rail as the player moves: position
        (curves 0-2) and look-at target (curves 3-5) as functions of a 1..100
        progress parameter. Exported as a camera whose clip runs the rail at
        one parameter step per frame."""
        if len(cam.keys) < 6:
            return
        s, b = self.opts.scale, self.b
        aspect = 4.0 / 3.0
        yfov = 2 * math.atan(math.tan(math.radians(cam.fov_degrees) / 2) / aspect)
        lo, hi = cam.keys[0][0][0], cam.keys[0][-1][0]
        params = [lo + (hi - lo) * i / 99 for i in range(100)]
        pos = [tuple(cam.sample(c, p) * s for c in range(3)) for p in params]
        tgt = [tuple(cam.sample(c, p) * s for c in range(3, 6)) for p in params]
        quats = make_continuous([look_at_quat(a, t) for a, t in zip(pos, tgt)])
        name = f"{self.stage.name}_cam{cam.index}"
        camera = b.camera({"name": name, "type": "perspective",
                           "perspective": {"yfov": yfov, "aspectRatio": aspect, "znear": 0.01, "zfar": 1000.0}})
        node = b.node({"name": name, "camera": camera, "translation": list(pos[0]), "rotation": list(quats[0]),
                       "extras": {"kind": "gameplay_camera", "horizontal_fov_degrees": cam.fov_degrees}})
        b.add_child(root, node)
        times = [i / self.opts.updates_per_second for i in range(100)]
        clip = self._clip(f"{name}_rail", node, "translation", times, pos, "VEC3",
                          extras={"note": "time = progress along the camera rail (1..100), not real time"})
        self._clip("", node, "rotation", times, quats, "VEC4", clip=clip)

    def _navmesh(self, root: int) -> None:
        from ..formats.stage import parse_navigation, parse_pac
        tris = parse_navigation(parse_pac(self.pac_path))
        if not tris:
            return
        s, b = self.opts.scale, self.b
        positions = [tuple(c * s for c in v) for t in tris for v in t.vertices]
        indices = []
        for i in range(len(tris)):
            indices += [3 * i, 3 * i + 2, 3 * i + 1]
        mesh = b.mesh({"name": f"{self.stage.name}_navmesh", "primitives": [
            {"attributes": {"POSITION": b.positions(positions)}, "indices": b.indices(indices), "mode": 4,
             "material": b.material({"name": "navmesh", "pbrMetallicRoughness": {
                 "baseColorFactor": [0.1, 0.8, 0.2, 0.5], "metallicFactor": 0.0},
                 "alphaMode": "BLEND", "doubleSided": True})}],
            "extras": {"triangle_flags": [t.flags for t in tris], "neighbors": [list(t.neighbors) for t in tris]}})
        b.add_child(root, b.node({"name": f"{self.stage.name}_navmesh", "mesh": mesh,
                                  "extras": {"kind": "navigation"}}))

    def _spin_clip(self, node: int, name: str, euler, axis: int, step: float) -> None:
        updates = 2 * math.pi / step
        keys = 16
        times, quats = [], []
        for k in range(keys + 1):
            e = list(euler)
            e[axis] += 2 * math.pi * k / keys
            times.append(updates * k / keys / self.opts.updates_per_second)
            quats.append(euler_to_quat(*e))
        self._clip(f"{name}_spin", node, "rotation", times, make_continuous(quats), "VEC4",
                   extras={"loop": True, "radians_per_update": step})


def export_stage(pac: Path | str, out: Path | str, opts: StageOptions | None = None) -> dict:
    opts = opts or StageOptions()
    stage = parse_stage(pac)
    sb = StageBuilder(stage, opts)
    sb.pac_path = Path(pac)
    sb.b.doc["asset"]["extras"] = {"source": Path(pac).name}
    sb.build()
    sb.b.write(Path(out))
    return sb.report


LIGHTING_NOTE = ("PAC slot 4 character lighting (the room itself is pre-lit in its vertex colours). The game "
                 "lights a character with the ambient plus the 3 strongest lights enabled on the floor triangle "
                 "under it (navmesh 'light_masks'): strength = luminance x (range - distance) / range; "
                 "directional lights have no falloff. Colours are on the game's 0-255 scale (game_rgb extras) and "
                 "scaled by game_intensity; glTF intensities assume 128 = 2000 lux: directional = that illuminance, point lights match "
                 "the game's strength at half their range (at most 2.5 m) (glTF falls off with the inverse square, the "
                 "game linearly). The game multiplies the texture's display value by (ambient + lights) / 128, "
                 "so linear-light materials look brighter than the game in dim rooms; scale to taste.")
LUX_PER_UNIT = 2000.0 / 128.0
MATCH_DISTANCE = 2.5      # metres: where point-light intensity matches the game's falloff


def door_curve_label(i: int) -> str:
    return ("open" if i % 2 == 0 else "close") + f"_{i // 2 + 1}"


def export_door_motion(mtn: Path | str, out: Path | str, fps: float = 30.0) -> dict:
    """One *_D000.MTN door bank on its own: a ``door`` node at the identity
    rotation and one clip per curve, rotating it about +Y by the curve's yaw
    offset. In a room, play it on a pivot parented to the door (or as an
    additive layer): the game adds the curve to the door's initial yaw."""
    mtn = Path(mtn)
    curves = parse_door_motion(mtn)
    who = mtn.stem.split("_")[0].upper()
    b = GltfBuilder("hgexport door motion")
    node = b.node({"name": "door", "extras": {"note": "pivot: parent a room door under it (keep the door's own "
                                                       "rotation) or blend the clips additively"}}, root=True)
    b.doc["asset"]["extras"] = {"source": mtn.name, "character": who,
                                "note": "door-opening curves used when this character opens a door (the game "
                                        "keeps one bank for the player and one for the current pursuer); the "
                                        "curve pair (open_1/close_1 vs open_2/close_2) is chosen by gameplay "
                                        "code; one sample per game update (30/s)"}
    clips = 0
    for i, curve in enumerate(curves):
        extras = {"source": mtn.name, "curve": i, "degrees": [round(a, 4) for a in curve]}
        if len(curve) < 2:
            extras["note"] = "single-sample curve (no motion)"
        quats = make_continuous([euler_to_quat(0.0, math.radians(a), 0.0) for a in curve])
        times = [k / fps for k in range(len(curve))]
        b.animation({"name": f"{who}_{door_curve_label(i)}", "extras": extras,
                     "samplers": [b.anim_sampler(times, quats, "VEC4")],
                     "channels": [{"sampler": 0, "target": {"node": node, "path": "rotation"}}]})
        clips += 1
    b.write(Path(out))
    return {"clips": clips, "frames": [len(c) for c in curves]}


def export_navmesh(pac: Path | str, out: Path | str, scale: float = 0.1) -> dict:
    """Walkable-floor navigation triangles of one room as a separate file
    (flags and neighbour links in the mesh extras), plus the switchable floor
    groups of PAC slot 14 and the slot-1 wall edges as separate nodes."""
    from ..formats.roomdata import parse_floor_groups, parse_walls
    from ..formats.stage import parse_navigation, parse_pac
    pac_data = parse_pac(pac)
    tris = parse_navigation(pac_data)
    if not tris:
        return {"triangles": 0}
    b = GltfBuilder("hgexport navmesh")
    positions = [tuple(c * scale for c in v) for t in tris for v in t.vertices]
    indices = []
    for i in range(len(tris)):
        indices += [3 * i, 3 * i + 2, 3 * i + 1]
    mesh = b.mesh({"name": f"{Path(pac).stem}_navmesh", "primitives": [
        {"attributes": {"POSITION": b.positions(positions)}, "indices": b.indices(indices), "mode": 4}],
        "extras": {"triangle_flags": [t.flags for t in tris], "flag_overrides": [t.override for t in tris],
                   "neighbors": [list(t.neighbors) for t in tris],
                   "light_masks": [t.light_mask for t in tris],
                   "light_masks_note": "bit i = room light i (<room>_lightNN in the room file) may light a "
                                       "character standing on this triangle"}})
    root = b.node({"name": f"{Path(pac).stem}_navmesh", "mesh": mesh}, root=True)
    walls = parse_walls(pac_data)
    wall_pos, kept, skipped = [], [], []
    for rec in walls:
        tri, _edge, va, vb = rec
        if tri >= len(tris) or va > 2 or vb > 2:
            skipped.append(list(rec))
            continue
        wall_pos += [tuple(c * scale for c in tris[tri].vertices[va]),
                     tuple(c * scale for c in tris[tri].vertices[vb])]
        kept.append(list(rec))
    if kept:
        wmat = b.material({"name": "wall_edge", "pbrMetallicRoughness": {"baseColorFactor": [1.0, 0.6, 0.0, 1.0],
                                                                          "metallicFactor": 0.0},
                           "extensions": {"KHR_materials_unlit": {}}})
        b.extensions_used.add("KHR_materials_unlit")
        wmesh = b.mesh({"name": "walls", "primitives": [
            {"attributes": {"POSITION": b.positions(wall_pos)}, "indices": b.indices(list(range(len(wall_pos)))),
             "mode": 1, "material": wmat}]})
        extras = {"records": kept, "note": "PAC slot 1 wall edges [floor triangle, edge, vertex a, vertex b]: "
                                           "the game tests movement / sight segments against these lines "
                                           "(nearest wall crossed, 0x17B9B0); line k = record k"}
        if skipped:
            extras["skipped_records"] = skipped
        b.add_child(root, b.node({"name": "walls", "mesh": wmesh, "extras": extras}))
    groups = parse_floor_groups(pac_data)
    for g, members in enumerate(groups):
        members = [t for t in members if t < len(tris)]
        if not members:
            continue
        gpos = [tuple(c * scale for c in v) for t in members for v in tris[t].vertices]
        gidx = []
        for i in range(len(members)):
            gidx += [3 * i, 3 * i + 2, 3 * i + 1]
        gmesh = b.mesh({"name": f"floor_group{g:02d}", "primitives": [
            {"attributes": {"POSITION": b.positions(gpos)}, "indices": b.indices(gidx), "mode": 4}]})
        b.add_child(root, b.node({"name": f"floor_group{g:02d}", "mesh": gmesh, "extras": {
            "group": g, "triangles": members,
            "note": "PAC slot 14: room scripts set or clear flags on all these floor triangles at once"}}))
    b.write(Path(out))
    return {"triangles": len(tris), "floor_groups": len(groups), "walls": len(kept)}


def export_zones(pac: Path | str, out: Path | str, scale: float = 0.1) -> dict:
    """PAC slot 2 trigger geometry: one node per directory entry (the index
    room scripts use), boundary lines and four-point areas."""
    from ..formats.roomdata import parse_zones
    from ..formats.stage import parse_pac
    zones = parse_zones(parse_pac(pac))
    if not zones:
        return {"zones": 0}
    b = GltfBuilder("hgexport zones")
    stem = Path(pac).stem
    root = b.node({"name": f"{stem}_zones", "extras": {
        "note": "PAC slot 2: room-script trigger geometry; boundary = line the scripts test for crossing, "
                "area = quad they test for inside; 'height' is the record's vertical parameter (game units)"}},
        root=True)
    colors = {"boundary": [1.0, 0.2, 0.1, 1.0], "area": [0.1, 0.5, 1.0, 0.35]}
    mats = {k: b.material({"name": f"zone_{k}", "pbrMetallicRoughness": {"baseColorFactor": c,
                                                                       "metallicFactor": 0.0},
                           "alphaMode": "BLEND" if c[3] < 1 else "OPAQUE", "doubleSided": True,
                           "extensions": {"KHR_materials_unlit": {}}}) for k, c in colors.items()}
    b.extensions_used.add("KHR_materials_unlit")
    for z in zones:
        pts = [tuple(c * scale for c in p) for p in z.points]
        if z.kind == "boundary":
            prim = {"attributes": {"POSITION": b.positions(pts)}, "indices": b.indices([0, 1]), "mode": 1,
                    "material": mats["boundary"]}
        else:
            prim = {"attributes": {"POSITION": b.positions(pts)}, "indices": b.indices([0, 1, 2, 0, 2, 3]),
                    "mode": 4, "material": mats["area"]}
        name = f"zone{z.index:02d}_{z.kind}"
        mesh = b.mesh({"name": name, "primitives": [prim]})
        b.add_child(root, b.node({"name": name, "mesh": mesh, "extras": {
            "index": z.index, "kind": z.kind, "height": round(z.height * scale, 4),
            "points": [[round(c, 4) for c in p] for p in pts]}}))
    b.write(Path(out))
    return {"zones": len(zones), "boundaries": sum(z.kind == "boundary" for z in zones)}
