"""PCK character -> glTF: skeleton, skinned meshes, blend shapes, animations."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ..formats.marks import parse_mrk, sparse
from ..formats.motion import Group, MotionBank
from ..formats.pck import FIXED12, PckModel, parse_pck, strip_triangles
from ..gltf.builder import GltfBuilder
from ..util.mathutil import (euler_to_quat, make_continuous, mat4_mul, mat4_point, mat4_rigid_inverse,
                              quat_rotate,
                              trs_matrix)
from .alpha import assign_modes
from .meshutil import VertexWelder, normalize, smooth_normals
from .textures import MaterialFactory, TextureSet, character_texture_sets


@dataclass
class CharacterOptions:
    scale: float = 0.1                  # native units are decimetres; 0.1 -> metres
    fps: float = 30.0
    mesh: bool = True
    animations: bool = True
    morphs: bool = True
    game_alpha: bool = False            # keep the game's blend flag as BLEND (default: alpha mode per element)
    specular: bool = False              # the game's per-part shine as KHR_materials_specular (export/shine.py)
    bleed: bool = False                 # fill the colour under alpha-0 texels (no dark fringes; not the game's look)
    texture_variant: str = "default"
    material_variants: bool = True      # KHR_materials_variants for alternate texture sheets
    unlit: bool = False
    extra_banks: list[tuple[str, MotionBank]] = field(default_factory=list)
    bone_prefix: str = ""
    root_motion: bool = False           # integrate controller -1 onto a motion node above the skeleton
    separate_parts: bool = False        # one object per PCK submesh (toggle anything in a DCC/engine)
    accessories: bool = True            # known optional accessories as separate objects
    exclude_parts: tuple[str, ...] = ()  # accessory/part names to leave out entirely
    markers: bool = True                # per-frame MRK marker bytes in clip extras
    shadow_volumes: bool = False        # embed the PCK shadow-volume boxes (see export_shadow_volumes)
    feet: bool = True                   # foot contact intervals (+ differing foot points) in clip extras
    hand_poses: bool = True             # MRK bits 4/8 hand poses (Fiona, Debilitas) as clip weights + extras


# --------------------------------------------------------------------------- primitives
class Prim:
    """A primitive being assembled: welded vertex rows plus optional morph deltas."""

    def __init__(self, component: int, blend: bool):
        self.component = component
        self.blend = blend
        self.rows: list[tuple] = []            # (pos3, nrm3, uv2, joints4, weights4)
        self.tris: list[tuple[int, int, int]] = []
        self.targets: dict[int, tuple[list, list]] = {}   # target -> (dpos, dnrm) per vertex
        self.source: list[str] = []
        self.tri_source: list[int] = []       # per triangle: index into source
        self.blended: set[str] = set()        # sources the game draws with alpha blending

    def subset(self, indices: list[int]) -> "Prim":
        """The triangles ``indices`` as a primitive of their own."""
        out = Prim(self.component, self.blend)
        remap: dict[int, int] = {}
        for i in indices:
            for v in self.tris[i]:
                if v not in remap:
                    remap[v] = len(out.rows)
                    out.rows.append(self.rows[v])
        order = sorted(remap, key=remap.get)
        out.tris = [tuple(remap[v] for v in self.tris[i]) for i in indices]
        out.targets = {t: ([dp[v] for v in order], [dn[v] for v in order]) for t, (dp, dn) in self.targets.items()}
        keep = sorted({self.tri_source[i] for i in indices})
        out.source = [self.source[k] for k in keep]
        out.tri_source = [keep.index(self.tri_source[i]) for i in indices]
        out.blended = self.blended
        return out

    @staticmethod
    def merge(prims: list["Prim"]) -> "Prim":
        """One primitive from several with the same texture and alpha mode."""
        if len(prims) == 1:
            return prims[0]
        out = Prim(prims[0].component, any(p.blend for p in prims))
        for t in sorted({t for p in prims for t in p.targets}):
            out.targets[t] = ([], [])
        for p in prims:
            base = len(out.rows)
            out.rows += p.rows
            out.tris += [(a + base, b + base, c + base) for a, b, c in p.tris]
            zero = [(0.0, 0.0, 0.0)] * len(p.rows)
            for t, (dp, dn) in out.targets.items():
                pdp, pdn = p.targets.get(t, (zero, zero))
                dp += pdp
                dn += pdn
            for name in p.source:
                if name not in out.source:
                    out.source.append(name)
            out.tri_source += [out.source.index(p.source[i]) for i in p.tri_source]
            out.blended |= p.blended
        return out


# Optional jewelry inside Fiona's main outfit (identified visually by the
# previous project). Matched by submesh vertex counts, so it applies to every
# FIN_* model sharing the layout.
ACCESSORIES = {
    "FIN": [("Default Neck Accessory", (("skinned", 3), ("skinned", 4))),
            ("Inquisitor Earrings", (("skinned", 32),)),
            ("Philosopher Earrings", (("rigid", 2),)),
            ("Alchemist Earrings", (("rigid", 3),)),
            ("Fairy Earrings", (("skinned", 33),)),
            ("Jade Choker", (("skinned", 5),)),
            ("Ruby Choker", (("skinned", 22),)),
            ("Diamond Choker", (("skinned", 6), ("skinned", 7))),
            ("Obsidian Choker", (("skinned", 23), ("skinned", 24)))],
    # Daniella's long glass shard: a rigid piece on her hand prop bone
    # (bone 45 = palette J24) that the game shows only in some scenes.
    # Two submeshes, both on that bone only: the opaque blade (rigid 5) and its
    # translucent glass layer (skinned 17).
    "DNL": [("Glass Shard", (("rigid", 5), ("skinned", 17)))],
}
# Submesh vertex counts that identify the layout each table was made for.
SIGNATURES = {
    "FIN": {("skinned", 3): 30, ("skinned", 4): 18, ("skinned", 32): 64},
    "DNL": {("rigid", 5): 192, ("skinned", 17): 78},
}
UNLOCKABLE_ACCESSORIES = ("Inquisitor Earrings", "Philosopher Earrings", "Alchemist Earrings", "Fairy Earrings",
                          "Jade Choker", "Ruby Choker", "Diamond Choker", "Obsidian Choker")


def _part_groups(model: PckModel, opts: "CharacterOptions") -> dict[tuple[str, int], str]:
    """Submesh -> object name for submeshes that leave the main body mesh."""
    groups: dict[tuple[str, int], str] = {}
    if opts.separate_parts:
        for mesh in model.meshes:
            groups[(mesh.kind, mesh.index)] = f"{model.name}_{mesh.kind}{mesh.index:02d}"
        return groups
    family = model.name.split("_")[0].upper()
    table = ACCESSORIES.get(family) if opts.accessories else None
    if table:
        counts = {(m.kind, m.index): len(m.positions) for m in model.meshes}
        if all(counts.get(k) == v for k, v in SIGNATURES[family].items()):
            for label, members in table:
                for kind, index in members:
                    if (kind, index) in counts:
                        groups[(kind, index)] = label
    return groups


def _face_regions(face: list) -> dict[int, str]:
    """Names for the 15 facial shapes, from what they move (measured on every
    character with a face; the game's facial controls address them by
    number): 1-6 the mouth, 7 and 8 one eyelid each, 9-12 the brows, 13-15
    broader expressions. The eyelids' side
    differs per character, so it is taken from the geometry: the face looks
    along +Z with Y up, so the character's left is +X."""
    d = max(face, key=lambda x: len(x.uvs))
    if len(d.shapes) != 16:
        return {}
    names = {k: "mouth" for k in range(1, 7)}
    names.update({k: "brow" for k in range(9, 13)})
    names.update({k: "expression" for k in range(13, 16)})
    base = d.shape_positions(0)
    for k in (7, 8):
        moved = [(sum((a - b) ** 2 for a, b in zip(p, q)) ** 0.5, q[0]) for p, q in zip(d.shape_positions(k), base)]
        total = sum(w for w, _x in moved)
        side = sum(w * x for w, x in moved) / total if total else 0.0
        names[k] = "blink_L" if side > 0 else "blink_R"
    if names[7] == names[8]:                  # not one eye each: leave the side out
        names[7] = names[8] = "blink"
    return names


def _bone_names(model: PckModel, prefix: str) -> list[str]:
    """Joints are named by their bone-palette slot (the index animation data
    uses), so clips bind to any model of the same rig family by name.
    Bones outside the palette are never keyframed and get an X prefix."""
    names = [f"{prefix}X{b:02d}" for b in range(len(model.bones))]
    for slot, bone in enumerate(model.palette):
        names[bone] = f"{prefix}J{slot:02d}"
    return names


class CharacterBuilder:
    def __init__(self, model: PckModel, textures: TextureSet | None, opts: CharacterOptions,
                 builder: GltfBuilder | None = None, variants: dict[str, TextureSet] | None = None,
                 parent_node: int | None = None):
        self.model = model
        self.opts = opts
        self.b = builder or GltfBuilder("hgexport character")
        self.textures = textures
        self.variants = variants or {}
        self.bone_nodes: list[int] = []
        self.mesh_node: int | None = None
        self.root_nodes: list[int] = []
        self.parent_node = parent_node
        self.target_names: list[str] = []
        self.face_targets: dict[int, int] = {}       # PCK shape index -> glTF target (shared face pieces)
        self.descriptor_targets: dict[int, dict[int, int]] = {}  # descriptor -> shape -> target
        self.hand_descriptors: set[int] = set()
        self.report: dict = {"model": model.name, "warnings": []}
        self.markers_used: set[int] = set()
        self._feet = None
        prefix = model.name
        self.factory = (MaterialFactory(self.b, textures.images, prefix, textures.sources, opts.unlit,
                                       opts.bleed)
                        if textures else None)
        if self.factory and opts.specular:
            from .shine import shine_masks
            self.factory.shine = shine_masks(model, textures.images)[0]
            self.factory.specular = True

    def _node(self, node: dict, top: bool) -> int:
        if top and self.opts.root_motion and "extras" in node and "bone_index" in node["extras"]:
            if getattr(self, "motion_node", None) is None:
                self.motion_node = self.b.node({"name": f"{self.opts.bone_prefix}{self.model.name}_motion"},
                                               root=self.parent_node is None)
                if self.parent_node is not None:
                    self.b.add_child(self.parent_node, self.motion_node)
            index = self.b.node(node)
            self.b.add_child(self.motion_node, index)
            return index
        index = self.b.node(node, root=top and self.parent_node is None)
        if top and self.parent_node is not None:
            self.b.add_child(self.parent_node, index)
        return index

    # ------------------------------------------------------------------ skeleton
    def add_skeleton(self) -> list[int]:
        m, s = self.model, self.opts.scale
        names = _bone_names(m, self.opts.bone_prefix)
        used: set[int] = set()
        for mesh in m.meshes:
            if mesh.kind == "rigid":
                used.add(mesh.parent_bone)
            for j, w in zip(mesh.joints, mesh.weights):
                used.update(b for b, x in zip(j, w) if x > 0)
        used.update(mo.parent_bone for mo in m.morphs)
        globals_: list[list[float]] = []
        ibms: list[list[float]] = []
        for bone in m.bones:
            t = tuple(c * s for c in bone.translation)
            q = euler_to_quat(*bone.euler)
            local = trs_matrix(t, q)
            g = mat4_mul(globals_[bone.parent], local) if bone.parent >= 0 else local
            globals_.append(g)
            if bone.index in used:
                # The stored inverse bind matrix is what skinning uses at runtime;
                # it can differ slightly from the rest pose (e.g. the DB* rigs).
                ibm = list(bone.inverse_bind)
                ibm[12] *= s
                ibm[13] *= s
                ibm[14] *= s
                ibm[3] = ibm[7] = ibm[11] = 0.0
                ibm[15] = 1.0
            else:
                ibm = mat4_rigid_inverse(g)
            ibms.append(ibm)
            node = {"name": names[bone.index], "translation": list(t), "rotation": list(q),
                    "extras": {"bone_index": bone.index}}
            self.bone_nodes.append(self._node(node, top=bone.parent < 0))
            if bone.parent >= 0:
                self.b.add_child(self.bone_nodes[bone.parent], self.bone_nodes[-1])
            else:
                self.root_nodes.append(self.bone_nodes[-1])
        self.ibms = ibms
        return self.bone_nodes

    # -------------------------------------------------------------------- meshes
    def _component(self, material: int) -> int:
        n = len(self.factory.images) if self.factory else 0
        if material >= n and n:
            self.report["warnings"].append(f"material {material} beyond texture count {n}; using 0")
            return 0
        return material

    def _default_weights(self, count: int) -> list[float]:
        """The game's resting hand pose is shape 1 of both hand descriptors
        (initialiser 0x1F1FE0), not the base mesh."""
        weights = [0.0] * count
        for d in (1, 2):
            target = self.descriptor_targets.get(d, {}).get(1)
            if target is not None and d in self.hand_descriptors:
                weights[target] = 1.0
        return weights

    def _plan_targets(self) -> int:
        m = self.model
        hands = {d.index for d in m.morphs if d.topology_mode == 0 and d.index in (1, 2) and len(d.shapes) == 10}
        self.hand_descriptors = hands
        face = [d for d in m.morphs if d.topology_mode == 1]
        names: list[str] = []
        if face:
            label = "face" if hands else "shape"
            regions = _face_regions(face) if hands else {}
            for s in range(1, max(len(d.shapes) for d in face)):
                self.face_targets[s] = len(names)
                names.append(f"{label}_{s:02d}" + (f"_{regions[s]}" if s in regions else ""))
        for d in m.morphs:
            if d.topology_mode == 1:
                self.descriptor_targets[d.index] = dict(self.face_targets)
                continue
            label = {1: "hand_l", 2: "hand_r"}.get(d.index) if d.index in hands else f"morph{d.index}"
            mapping = {}
            for s in range(1, len(d.shapes)):
                mapping[s] = len(names)
                names.append(f"{label}_{s:02d}")
            self.descriptor_targets[d.index] = mapping
        self.target_names = names
        return len(names)

    def add_mesh(self) -> int | None:
        m, s = self.model, self.opts.scale
        if not m.has_geometry:
            return None
        prims: dict[tuple, Prim] = {}
        welders: dict[tuple, VertexWelder] = {}
        groups = _part_groups(m, self.opts)
        for mesh in m.meshes:
            if groups.get((mesh.kind, mesh.index)) in self.opts.exclude_parts:
                continue
            comp = self._component(mesh.material)
            key = (groups.get((mesh.kind, mesh.index), ""), comp, mesh.blend)
            prim = prims.setdefault(key, Prim(comp, mesh.blend))
            welder = welders.setdefault(key, VertexWelder())
            prim.source.append(f"{mesh.kind}{mesh.index}")
            if mesh.blend:
                prim.blended.add(prim.source[-1])
            remap: dict[int, int] = {}
            for tri in strip_triangles(mesh.restart):
                out = []
                for v in tri:
                    if v not in remap:
                        p = mesh.positions[v]
                        pos = (p[0] * FIXED12 * s, p[1] * FIXED12 * s, p[2] * FIXED12 * s)
                        nrm = normalize(mesh.normals[v])
                        if mesh.kind == "skinned":
                            j, w = mesh.joints[v], mesh.weights[v]
                        else:
                            j, w = (mesh.parent_bone, 0, 0, 0), (1.0, 0.0, 0.0, 0.0)
                        row = (pos, nrm, mesh.uvs[v], j, w)
                        remap[v] = welder.add(row, row)
                    out.append(remap[v])
                if len(set(out)) == 3:
                    prim.tris.append(tuple(out))
                    prim.tri_source.append(len(prim.source) - 1)
            prim.rows = welder.rows
        morph_prims: list[Prim] = []
        target_count = self._plan_targets() if (self.opts.morphs and m.morphs) else 0
        for d in m.morphs:
            comp = self._component(d.material)
            prim = Prim(comp, d.blend)
            prim.source.append(f"morph{d.index}")
            if d.blend:
                prim.blended.add(prim.source[-1])
            shapes = [d.shape_positions(k) for k in range(len(d.shapes))] if target_count else [d.shape_positions(0)]
            welder = VertexWelder()
            remap = {}
            tris = []
            for tri in strip_triangles(d.restart):
                out = []
                for v in tri:
                    if v not in remap:
                        key = (d.uvs[v], tuple(sh[v] for sh in shapes))
                        remap[v] = welder.add(key, (v,))
                    out.append(remap[v])
                if len(set(out)) == 3:
                    tris.append(tuple(out))
            src = [row[0] for row in welder.rows]          # original record per welded vertex
            per_shape_pos = [[tuple(c * FIXED12 * s for c in sh[v]) for v in src] for sh in shapes]
            per_shape_nrm = [smooth_normals(per_shape_pos[k], tris, [shapes[k][v] for v in src],
                                            [d.shape_normals[k][v] for v in src]) for k in range(len(shapes))]
            j, w = (d.parent_bone, 0, 0, 0), (1.0, 0.0, 0.0, 0.0)
            prim.rows = [(per_shape_pos[0][i], per_shape_nrm[0][i], d.uvs[v], j, w) for i, v in enumerate(src)]
            prim.tris = tris
            prim.tri_source = [0] * len(tris)
            if target_count:
                for shape, target in self.descriptor_targets[d.index].items():
                    if shape >= len(shapes):
                        continue
                    dpos = [tuple(a - b for a, b in zip(per_shape_pos[shape][i], per_shape_pos[0][i]))
                            for i in range(len(src))]
                    dnrm = [tuple(a - b for a, b in zip(per_shape_nrm[shape][i], per_shape_nrm[0][i]))
                            for i in range(len(src))]
                    prim.targets[target] = (dpos, dnrm)
            morph_prims.append(prim)
        body = [p for (g, _c, _b), p in prims.items() if not g and p.tris] + [p for p in morph_prims if p.tris]
        parts: dict[str, list[Prim]] = {}
        for (g, _c, _b), p in prims.items():
            if g and p.tris:
                parts.setdefault(g, []).append(p)
        skin = self.add_skin()
        count = 0
        self.part_nodes: dict[str, int] = {}
        for name, plist, targets in [(m.name, body, target_count)] + [(g, pl, 0) for g, pl in parts.items()]:
            if not plist:
                continue
            primitives = [self._emit(part, mode, targets) for part, mode in self._alpha_groups(plist)]
            count += len(primitives)
            mesh = {"name": name, "primitives": primitives}
            if targets:
                mesh["weights"] = self._default_weights(targets)
                mesh["extras"] = {"targetNames": self.target_names}
            node = {"name": name, "mesh": self.b.mesh(mesh)}
            if skin is not None:
                node["skin"] = skin
            index = self._node(node, top=True)
            if name == m.name:
                self.mesh_node = index
            else:
                self.part_nodes[name] = index
        self.report.update(primitives=count, morph_targets=target_count, parts=sorted(self.part_nodes),
                           triangles=sum(len(p.tris) for p in body) + sum(len(p.tris) for pl in parts.values() for p in pl))
        return self.mesh_node

    def add_skin(self, name: str | None = None) -> int | None:
        """The skin over the skeleton (created once). Without a mesh it still
        makes importers build an armature, so animation-only files bind by
        bone name to the character exported on its own."""
        if getattr(self, "skin_index", None) is None and self.bone_nodes:
            self.skin_index = self.b.skin({
                "name": name or f"{self.model.name}_skeleton",
                "joints": self.bone_nodes,
                "inverseBindMatrices": self.b.mat4s(self.ibms),
                "skeleton": self.root_nodes[0] if len(self.root_nodes) == 1 else self.bone_nodes[0],
            })
        return getattr(self, "skin_index", None)

    def add_weight_carrier(self, name: str) -> int | None:
        """A degenerate one-triangle mesh with the model's blend-shape names and
        zero deltas: it carries animated morph weights in files that leave the
        real mesh out (copy the weights/shape-key action onto the body mesh)."""
        m = self.model
        count = self._plan_targets() if (self.opts.morphs and m.morphs) else 0
        if not count:
            return None
        zero = [(0.0, 0.0, 0.0)] * 3
        prim = {"attributes": {"POSITION": self.b.positions(zero)}, "mode": 4,
                "targets": [{"POSITION": self.b.positions(zero)} for _ in range(count)]}
        mesh = self.b.mesh({"name": name, "primitives": [prim], "weights": [0.0] * count,
                            "extras": {"targetNames": self.target_names}})
        self.mesh_node = self._node({"name": name, "mesh": mesh, "extras": {
            "note": "blend-shape weight carrier; apply its weights to the body mesh "
                    f"of {m.name} (same target names)"}}, top=True)
        return self.mesh_node

    def add_shadow_volumes(self, volumes: list, parent: int | None = None) -> list[int]:
        """PCK shadow volumes (formats/shadow.py) as one mesh whose boxes are
        rigidly skinned to their bones: bind-pose positions = inverse(IBM) *
        bone-local vertex, so they follow every clip exactly like the game's
        volumes. Quads are split into triangles facing their stored normals;
        the box ranges are listed in the node extras."""
        if not volumes or not self.bone_nodes:
            return []
        b, s = self.b, self.opts.scale
        mat = b.material({"name": "shadow_volume", "pbrMetallicRoughness": {
            "baseColorFactor": [0.0, 0.0, 0.0, 0.35], "metallicFactor": 0.0},
            "alphaMode": "BLEND", "doubleSided": True, "extensions": {"KHR_materials_unlit": {}}})
        b.extensions_used.add("KHR_materials_unlit")
        pos, joints, tris, boxes = [], [], [], []
        for vol in volumes:
            base, first = len(pos), len(tris) // 3
            bind = mat4_rigid_inverse(self.ibms[vol.bone])
            pos += [mat4_point(bind, tuple(c * s for c in v)) for v in vol.vertices]
            joints += [(vol.bone, 0, 0, 0)] * len(vol.vertices)
            for quad, n in zip(vol.quads, vol.normals):
                for tri in ((quad[0], quad[1], quad[2]), (quad[0], quad[2], quad[3])):
                    a, c1, c2 = (vol.vertices[i] for i in tri)
                    e1 = [c1[k] - a[k] for k in range(3)]
                    e2 = [c2[k] - a[k] for k in range(3)]
                    cross = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2],
                             e1[0] * e2[1] - e1[1] * e2[0])
                    if sum(cross[k] * n[k] for k in range(3)) < 0:
                        tri = (tri[0], tri[2], tri[1])
                    tris.extend(base + i for i in tri)
            boxes.append({"index": vol.index, "bone": b.doc["nodes"][self.bone_nodes[vol.bone]]["name"],
                          "bone_index": vol.bone, "vertices": [base, len(vol.vertices)],
                          "triangles": [first, len(tris) // 3 - first]})
        prim = {"attributes": {"POSITION": b.positions(pos), "JOINTS_0": b.joints(joints),
                               "WEIGHTS_0": b.vec4([(1.0, 0.0, 0.0, 0.0)] * len(pos))},
                "indices": b.indices(tris), "mode": 4, "material": mat}
        name = f"{self.model.name}_shadow_volumes"
        node = b.node({"name": name, "mesh": b.mesh({"name": name, "primitives": [prim]}), "skin": self.add_skin(),
                       "extras": {"note": "PCK stencil shadow volumes (one box per bone, rigidly skinned); the game "
                                          "extrudes their silhouette away from the light. Not part of the visible "
                                          "model.", "boxes": boxes}}, root=parent is None)
        if parent is not None:
            b.add_child(parent, node)
        nodes = [node]
        self.report["shadow_volumes"] = len(volumes)
        return nodes

    def _alpha_groups(self, prims: list[Prim]) -> list[tuple[Prim, str]]:
        """Give every triangle of a mesh an alpha mode (export/alpha.py) and
        merge the triangles that share texture and mode: OPAQUE first, then
        TEST / MASK, then BLEND."""
        buckets: dict[tuple[int, str], list[Prim]] = {}
        for prim in prims:
            sampler = self.factory.sampler(prim.component) if self.factory else None
            if sampler is None:
                buckets.setdefault((prim.component, "OPAQUE"), []).append(prim)
                continue
            uvs = [tuple(prim.rows[v][2] for v in t) for t in prim.tris]
            modes = assign_modes(prim.tris, lambda v: prim.rows[v][0], lambda i, reach: sampler.texels(uvs[i], reach),
                                 prim.blend, self.opts.game_alpha)
            by_mode: dict[str, list] = {}
            for i, mode in enumerate(modes):
                by_mode.setdefault(mode, []).append(i)
            for mode, indices in by_mode.items():
                part = prim if len(by_mode) == 1 else prim.subset(indices)
                buckets.setdefault((prim.component, mode), []).append(part)
        order = {"OPAQUE": 0, "TEST": 1, "MASK": 2, "BLEND": 3}
        return [(Prim.merge(buckets[k]), k[1]) for k in sorted(buckets, key=lambda k: (order[k[1]], k[0]))]

    def _emit(self, prim: Prim, mode: str, target_count: int) -> dict:
        b = self.b
        rows = prim.rows
        attrs = {
            "POSITION": b.positions([r[0] for r in rows]),
            "NORMAL": b.vec3([r[1] for r in rows]),
            "TEXCOORD_0": b.vec2([r[2] for r in rows]),
        }
        if self.bone_nodes:
            # unused influences (weight 0) must reference joint 0
            attrs["JOINTS_0"] = b.joints([tuple(j if w > 0 else 0 for j, w in zip(r[3], r[4])) for r in rows])
            attrs["WEIGHTS_0"] = b.vec4([r[4] for r in rows])
        out = {"attributes": attrs, "indices": b.indices([i for t in prim.tris for i in t]), "mode": 4,
               "extras": {"source": prim.source,
                          "game_blended": [x for x in prim.source if x in prim.blended]}}
        if self.factory:
            out["material"] = self.factory.material(prim.component, mode)
            if self.opts.material_variants and len(self.variants) > 1:
                self._variant_mappings(out, prim.component, mode)
        if target_count:
            zeros = None
            targets = []
            for t in range(target_count):
                if t in prim.targets:
                    dp, dn = prim.targets[t]
                    targets.append({"POSITION": b.positions(dp), "NORMAL": b.vec3(dn)})
                else:
                    if zeros is None:
                        zero = [(0.0, 0.0, 0.0)] * len(rows)
                        zeros = {"POSITION": b.positions(zero), "NORMAL": b.vec3(zero)}
                    targets.append(zeros)
            out["targets"] = targets
        return out

    # ---------------------------------------------------------- material variants
    def _variant_mappings(self, primitive: dict, component: int, mode: str) -> None:
        if not hasattr(self, "_variant_factories"):
            self._variant_factories = {}
            names = list(self.variants)
            self._variant_index = {n: i for i, n in enumerate(names)}
            ext = self.b.doc.setdefault("extensions", {}).setdefault("KHR_materials_variants", {"variants": []})
            ext["variants"] = [{"name": n} for n in names]
            self.b.extensions_used.add("KHR_materials_variants")
            for name, ts in self.variants.items():
                if ts.images is self.textures.images:
                    self._variant_factories[name] = self.factory
                else:
                    factory = MaterialFactory(
                        self.b, ts.images, f"{self.model.name}_{name}", ts.sources, self.opts.unlit, self.opts.bleed)
                    if self.factory.specular:            # same UV layout, the variant's sheet sizes
                        from .shine import shine_masks
                        factory.shine, factory.specular = shine_masks(self.model, ts.images)[0], True
                    self._variant_factories[name] = factory
        groups: dict[int, list[int]] = {}
        for name, factory in self._variant_factories.items():
            if factory is self.factory or factory.images[component] is self.factory.images[component]:
                mat = primitive["material"]
            else:
                mat = factory.material(component, mode)
            groups.setdefault(mat, []).append(self._variant_index[name])
        primitive.setdefault("extensions", {})["KHR_materials_variants"] = {
            "mappings": [{"material": mat, "variants": v} for mat, v in groups.items()]}

    # ---------------------------------------------------------------- animation
    @property
    def feet(self):
        """Foot-control mapping of this rig (export/feet.py), built on first use."""
        if self._feet is None:
            from .feet import FootControls
            self._feet = FootControls(self.model, _bone_names(self.model, self.opts.bone_prefix), self.opts.scale)
        return self._feet

    def foot_info(self) -> dict | None:
        return self._feet.info() if self._feet is not None and self.report.get("foot_clips") else None

    def add_bank_clips(self, bank: MotionBank, prefix: str, *, entries: Iterable[int] | None = None,
                       names: dict[int, str] | None = None, extras: dict[int, dict] | None = None,
                       markers: dict[int, bytes] | None = None) -> list[dict]:
        """One glTF animation per motion-bank entry. Joint groups in slots 1..4
        with the same length and disjoint bones are merged into one clip;
        other groups become separate ``_layerN`` clips (authored overlays such as
        ears/tail that the game blends at runtime)."""
        clips = []
        wanted = set(entries) if entries is not None else None
        for entry in bank.entries:
            if wanted is not None and entry.index not in wanted:
                continue
            groups = [(slot, g) for slot, g in enumerate(entry.slots) if slot and g]
            if not groups:
                continue
            plans: list[tuple[str, list[tuple[int, Group]]]] = []
            primary = next(((s, g) for s, g in groups if s == 1), None)
            merged = [primary] if primary else []
            bones = {t.target for t in primary[1].tracks} if primary else set()
            for slot, g in groups:
                if primary and slot == 1:
                    continue
                mask = {t.target for t in g.tracks}
                if primary and g.frames == primary[1].frames and not (mask & bones):
                    merged.append((slot, g))
                    bones |= mask
                else:
                    plans.append((f"_layer{slot}", [(slot, g)]))
            if merged:
                plans.insert(0, ("", merged))
            ids = entry.resource_ids
            record = markers.get(ids[0]) if markers else None
            for suffix, parts in plans:
                base = names.get(entry.index) if names else None
                name = f"{prefix}{base or f'{entry.index:03d}'}{suffix}"
                anim = self._clip(name, parts)
                if anim is None:
                    continue
                if self.opts.root_motion and suffix == "":
                    self._root_motion(anim, entry.slots[0], parts[0][1].frames)
                anim["extras"] = {"entry": entry.index, "slots": [s for s, _ in parts],
                                  "frames": parts[0][1].frames, "fps": self.opts.fps,
                                  "resource_ids": [f"0x{i:X}" for i in ids]}
                if extras and entry.index in extras:
                    anim["extras"].update(extras[entry.index])
                if self.opts.feet and suffix == "":
                    feet = self.feet.clip_extras(entry.slots[0], [g for _s, g in parts], parts[0][1].frames)
                    if feet:
                        anim["extras"].update(feet)
                        self.report["foot_clips"] = self.report.get("foot_clips", 0) + 1
                if record is not None and self.opts.hand_poses and suffix == "" and bank is self.model.motion:
                    self._hand_poses(anim, bank, ids[0], record, parts[0][1].frames)
                if record is not None:
                    # per-frame MRK bits of this animation ID (game reader 0x1F4770)
                    anim["extras"]["markers"] = sparse(record, parts[0][1].frames)
                    self.markers_used.add(ids[0])
                    record = None
                self.b.animation(anim)
                clips.append({"name": name, "entry": entry.index, "frames": parts[0][1].frames,
                              "slots": [s for s, _ in parts], "resource_ids": ids})
        return clips

    def _hand_poses(self, anim: dict, bank: MotionBank, rid: int, record: bytes, frames: int) -> None:
        """MRK bits 4 / 8 toggle the two hand mixers (formats/handpose.py)."""
        from ..formats.handpose import hand_table, hand_weights, simulate
        table = hand_table(self.model.name)
        row = next((i for i, (r, _e) in enumerate(bank.lookup) if r == rid), None)
        if table is None or row is None or row >= len(table) or not table[row]:
            return
        poses = simulate(record, frames, table[row])
        keys: dict[str, list] = {}
        for side, label in enumerate(("hand_l", "hand_r")):
            last = None
            for f, frame in enumerate(poses):
                a, b, w = frame[side]
                if (a, b, w) != last:
                    keys.setdefault(label, []).append([f, a, b, round(w, 4)])
                    last = (a, b, w)
        anim["extras"]["hand_poses"] = {"type": f"0x{table[row]:02X}", **keys}
        self.report["hand_pose_clips"] = self.report.get("hand_pose_clips", 0) + 1
        mapping = [self.descriptor_targets.get(1), self.descriptor_targets.get(2)]
        if self.mesh_node is None or not all(mapping):
            return
        rows = []
        for hands in hand_weights(poses):
            row_w = [0.0] * len(self.target_names)
            for side, weights in enumerate(hands):
                for shape, w in weights.items():
                    if shape in mapping[side]:
                        row_w[mapping[side][shape]] += w
            rows.append(row_w)
        times = [f / self.opts.fps for f in range(frames)] if frames > 1 else [0.0]
        anim["samplers"].append(self.b.anim_sampler(times, rows, "WEIGHTS", "LINEAR"))
        anim["channels"].append({"sampler": len(anim["samplers"]) - 1,
                                 "target": {"node": self.mesh_node, "path": "weights"}})

    def _root_motion(self, anim: dict, controls: Group | None, frames: int) -> None:
        """Controller -1 holds per-update steps: a heading increment (Euler Y)
        and a translation in the actor's local frame. The game adds the
        heading first, then moves by the step rotated by the new heading."""
        b, s, fps = self.b, self.opts.scale, self.opts.fps
        track = next((t for t in (controls.tracks if controls else []) if t.target == -1 and t.codec == 7), None)
        samples = (track.samples if track and len(track.samples) == frames else
                   (track.samples[:1] * frames if track else [(0.0,) * 6] * frames))
        heading, pos = 0.0, [0.0, 0.0, 0.0]
        positions, quats = [], []
        for i in range(frames):
            positions.append(tuple(pos))
            quats.append(euler_to_quat(0.0, heading, 0.0))
            v = samples[i]
            heading += v[1]
            step = quat_rotate(euler_to_quat(0.0, heading, 0.0), (v[3] * s, v[4] * s, v[5] * s))
            pos = [pos[k] + step[k] for k in range(3)]
        times = [i / fps for i in range(frames)] if frames > 1 else [0.0]
        tin = b.times(times)
        for path, values, kind in (("translation", positions, "VEC3"),
                                   ("rotation", make_continuous(quats), "VEC4")):
            anim["samplers"].append({"input": tin, "output": b.anim_output(values, kind), "interpolation": "LINEAR"})
            anim["channels"].append({"sampler": len(anim["samplers"]) - 1,
                                     "target": {"node": self.motion_node, "path": path}})

    def _clip(self, name: str, parts: list[tuple[int, Group]]) -> dict | None:
        b, m, fps, s = self.b, self.model, self.opts.fps, self.opts.scale
        samplers, channels = [], []
        seen: set[int] = set()
        for _slot, g in parts:
            full_times = [i / fps for i in range(g.frames)]
            for t in g.tracks:
                if t.target < 0 or t.codec not in (0, 1, 2, 4, 7):
                    continue
                if t.target >= len(m.palette):
                    self.report["warnings"].append(f"{name}: palette slot {t.target} missing")
                    continue
                bone = m.palette[t.target]
                if bone in seen:
                    self.report["warnings"].append(f"{name}: duplicate track for bone {bone} skipped")
                    continue
                seen.add(bone)
                samples = t.samples
                if len(samples) == 1:
                    times = [0.0] if g.frames == 1 else [0.0, full_times[-1]]
                    samples = samples * len(times)
                else:
                    times = full_times
                if t.codec != 1:
                    quats = make_continuous([euler_to_quat(v[0], v[1], v[2]) for v in samples])
                    samplers.append(b.anim_sampler(times, quats, "VEC4"))
                    channels.append({"sampler": len(samplers) - 1,
                                     "target": {"node": self.bone_nodes[bone], "path": "rotation"}})
                if t.codec == 1 or t.has_translation:
                    trans = [(v[0] * s, v[1] * s, v[2] * s) if t.codec == 1 else (v[3] * s, v[4] * s, v[5] * s)
                             for v in samples]
                    samplers.append(b.anim_sampler(times, trans, "VEC3"))
                    channels.append({"sampler": len(samplers) - 1,
                                     "target": {"node": self.bone_nodes[bone], "path": "translation"}})
        if not channels:
            return None
        return {"name": name, "samplers": samplers, "channels": channels}


# Numbered animation variants are chosen by story flags (docs/FORMATS.md).
# Player X_001..X_006 (Fiona and every costume folder) hold the player's half
# of paired animations with the current pursuer (matching clip lengths);
# Hewie's HEW/HED/HEG_001..004 are his own per-chapter sets, selected by the
# same flags as the player's DNL_000 / RCG_000 / LRO_000 / LRY_000 sets.
VARIANT_PARTNERS = {1: "DNL_000", 2: "DNL_001", 3: "RCG_000", 4: "RCT_000", 5: "LRO_000", 6: "LRY_000"}
HEWIE_CHAPTERS = {1: "DNL_000", 2: "RCG_000", 3: "LRO_000", 4: "LRY_000"}
_VARIANT = re.compile(r"^([A-Z0-9]{3})_00([1-9])$", re.IGNORECASE)


def variant_base(pck: Path | str) -> Path | None:
    """``X_00k.PCK`` (k >= 1) with an ``X_000.PCK`` next to it, else None."""
    pck = Path(pck)
    m = _VARIANT.match(pck.stem)
    if not m:
        return None
    base = pck.with_name(f"{m.group(1)}_000{pck.suffix}")
    return base if base.is_file() else None


def variant_relation(pck: Path | str) -> tuple[str, str] | None:
    """(relation, pursuer model) for known animation variants: player sets are
    ``paired_with`` that pursuer, Hewie's sets belong to its ``chapter``."""
    m = _VARIANT.match(Path(pck).stem)
    if not m:
        return None
    family, index = m.group(1).upper(), int(m.group(2))
    if family.startswith("FI") and index in VARIANT_PARTNERS:
        return "paired_with", VARIANT_PARTNERS[index]
    if family in ("HEW", "HED", "HEG") and index in HEWIE_CHAPTERS:
        return "chapter", HEWIE_CHAPTERS[index]
    if family == "DNL" and index == 1:
        # Daniella's later-phase actor class (descriptor 0x43DBD0, own MRK and
        # sound banks), active from the story flag that also selects the
        # player's set 2 ([+40] & 0x8000); its partner is FI*_002.
        return "paired_with", "FI*_002"
    return None


def variant_label(pck: Path | str) -> str | None:
    """Pursuer named in the export file name of a known animation variant."""
    rel = variant_relation(pck)
    return rel[1] if rel and "*" not in rel[1] else None


def same_rig(a: PckModel, b: PckModel) -> bool:
    return ([b_.parent for b_ in a.bones] == [b_.parent for b_ in b.bones] and a.palette == b.palette and
            [(m.kind, m.index, m.positions, m.uvs) for m in a.meshes] ==
            [(m.kind, m.index, m.positions, m.uvs) for m in b.meshes])


MARKER_NOTE = ("clip extras 'markers' = [[frame, bits], ...] from the model's MRK file: one byte per "
               "frame tested by game code (0x1F4770). Bits 4 / 8 toggle the left / right hand pose (Fiona, "
               "Debilitas; decoded into clip extras 'hand_poses' and hand blend-shape weights); bits 1, 2, 16, "
               "32 are gameplay event cues (attacks, alerts, state changes) whose effects are not assigned")


def load_markers(pck: Path, report: dict) -> dict[int, bytes]:
    mrk = pck.with_suffix(".MRK")
    if not mrk.is_file():
        return {}
    try:
        return parse_mrk(mrk)
    except Exception as exc:  # keep exporting the model without markers
        report["warnings"].append(f"{mrk.name}: {exc}")
        return {}


def marker_info(pck: Path, markers: dict[int, bytes], used: set[int]) -> dict:
    """Asset extras: source and the records no exported clip carries."""
    unmatched = {f"0x{rid:X}": sparse(rec) for rid, rec in sorted(markers.items())
                 if rid not in used and any(rec)}
    info = {"source": pck.with_suffix(".MRK").name, "note": MARKER_NOTE}
    if unmatched:
        info["records_without_clip"] = unmatched
    return info


def _entry_key(entry) -> tuple:
    return tuple(entry.resource_ids)


def _entry_signature(entry) -> tuple:
    return tuple(None if g is None else (g.frames, tuple((t.target, t.codec, tuple(map(tuple, t.samples)))
                                                         for t in g.tracks))
                 for g in entry.slots)


def export_animation_variant(pck: Path | str, base: Path | str, out: Path | str,
                             opts: CharacterOptions | None = None) -> dict:
    """Animation-only export of a model that shares mesh and skeleton with
    ``base``: a bare armature (bone names as in the base export) and only the
    clips that differ from the base or are new. Clips are named after the base
    clip with the same resource ID, so they replace it by name."""
    opts = opts or CharacterOptions()
    pck, base = Path(pck), Path(base)
    model, ref = parse_pck(pck), parse_pck(base)
    if not same_rig(model, ref):
        raise ValueError(f"{pck.name} does not share mesh and skeleton with {base.name}")
    ref_entries = {_entry_key(e): e for e in (ref.motion.entries if ref.motion else [])}
    cb = CharacterBuilder(model, None, CharacterOptions(scale=opts.scale, fps=opts.fps, mesh=False,
                                                        root_motion=opts.root_motion, bone_prefix=opts.bone_prefix,
                                                        feet=opts.feet))
    markers = load_markers(pck, cb.report) if opts.markers else {}
    ref_markers = load_markers(base, cb.report) if opts.markers else {}
    names: dict[int, str] = {}
    extras: dict[int, dict] = {}
    for entry in (model.motion.entries if model.motion else []):
        other = ref_entries.get(_entry_key(entry))
        if other is None:
            names[entry.index] = f"r{entry.resource_ids[0]:04X}"
            extras[entry.index] = {"replaces": None}
        elif (_entry_signature(other) != _entry_signature(entry) or
              markers.get(entry.resource_ids[0]) != ref_markers.get(other.resource_ids[0])):
            names[entry.index] = f"{other.index:03d}"
            extras[entry.index] = {"replaces": f"{other.index:03d}"}
    keys = {_entry_key(e) for e in (model.motion.entries if model.motion else [])}
    absent = sorted(f"{e.index:03d}" for k, e in ref_entries.items() if k not in keys)
    relation = variant_relation(pck)
    info = {"source": pck.name, "animation_only": True,
            "base_model": f"characters/{base.parent.name}/{base.stem}{Path(out).suffix}",
            "note": "bone names match the base export; each clip replaces the base clip of the same name",
            "absent_in_variant": absent}
    if relation:
        info[relation[0]] = relation[1]
    cb.b.doc["asset"]["extras"] = info
    cb.add_skeleton()
    cb.add_skin()
    clips = (cb.add_bank_clips(model.motion, "", entries=names, names=names, extras=extras, markers=markers)
             if names else [])
    if markers:
        info["markers"] = {"source": pck.with_suffix(".MRK").name, "note": MARKER_NOTE}
    if cb.foot_info():
        info["foot_controls"] = cb.foot_info()
    cb.b.write(Path(out))
    cb.report.update(animation_only=True, base=base.name, clips=len(clips),
                     **({relation[0]: relation[1]} if relation else {}),
                     replaced=sorted(v["replaces"] for v in extras.values() if v["replaces"]),
                     added=sorted(n for i, n in names.items() if extras[i]["replaces"] is None),
                     absent_in_variant=absent)
    return cb.report


def export_shadow_volumes(pck: Path | str, out: Path | str, opts: CharacterOptions | None = None) -> dict:
    """The model's shadow volumes on their own: a bare armature with the
    character export's bone names plus one box per bone, skinned to it (so
    the boxes follow the character's clips when bound to its armature)."""
    from ..formats.shadow import parse_shadow_volumes, pck_bytes
    opts = opts or CharacterOptions()
    pck = Path(pck)
    volumes = parse_shadow_volumes(pck_bytes(pck), pck.name)
    if not volumes:
        return {"shadow_volumes": 0}
    model = parse_pck(pck)
    cb = CharacterBuilder(model, None, CharacterOptions(scale=opts.scale, mesh=False))
    cb.b.doc["asset"]["extras"] = {"source": pck.name, "shadow_volumes_only": True,
                                   "base_model": f"characters/{pck.parent.name}/{pck.stem}{Path(out).suffix}"}
    cb.add_skeleton()
    cb.add_shadow_volumes(volumes)
    cb.b.write(Path(out))
    return cb.report


def export_character(pck: Path | str, out: Path | str, opts: CharacterOptions | None = None) -> dict:
    """Export one PCK to ``out`` (.glb or .gltf). Returns a small report."""
    opts = opts or CharacterOptions()
    pck = Path(pck)
    model = parse_pck(pck)
    sets = character_texture_sets(pck)
    textures = sets.get(opts.texture_variant) or sets.get("default")
    cb = CharacterBuilder(model, textures if opts.mesh else None, opts, variants=sets)
    cb.b.doc["asset"]["extras"] = {"source": pck.name}
    cb.add_skeleton()
    if opts.mesh:
        cb.add_mesh()
    else:
        cb.add_skin()
    clips = []
    markers = load_markers(pck, cb.report) if opts.markers and opts.animations else {}
    if opts.animations:
        if model.motion:
            clips += cb.add_bank_clips(model.motion, "", markers=markers)
        for label, bank in opts.extra_banks:
            clips += cb.add_bank_clips(bank, f"{label}_")
    if opts.shadow_volumes:
        from ..formats.shadow import parse_shadow_volumes, pck_bytes
        cb.add_shadow_volumes(parse_shadow_volumes(pck_bytes(pck), pck.name))
    if markers:
        cb.b.doc["asset"]["extras"]["markers"] = marker_info(pck, markers, cb.markers_used)
        cb.report["marker_records"] = len(markers)
    if cb.foot_info():
        cb.b.doc["asset"]["extras"]["foot_controls"] = cb.foot_info()
    cb.b.write(Path(out))
    cb.report.update(clips=len(clips), texture_sets=list(sets), textures=textures.label if textures else None)
    return cb.report
