"""Assemble an animation-only cutscene export into a complete Blender scene.

The default cutscene export (``cutscenes/EVxxxx/EVxxxx.glb``) holds the camera,
the actors' animation and the prop animation; actor models and rooms live in
``characters/`` and ``stages/``. This script imports all of them and wires the
cutscene animation onto the real models (see docs/BLENDER_CUTSCENES.md for
what it does step by step and how to do the same by hand).

Command line (Blender 4.4 or newer; 5.x tested):

    blender --python tools/blender_assemble_cutscene.py -- export/cutscenes/EV0010/EV0010.glb
    blender -b --python tools/blender_assemble_cutscene.py -- CUTSCENE.glb --save EV0010.blend

Options after ``--``:
    --root DIR          export root (default: three folders above the cutscene file)
    --save FILE.blend   save the assembled scene
    --room-anims MODE   ambient (default: looping atlases and fans), all, none
    --keep-current      import into the open file instead of a new empty scene
    --resolution WxH    render size (default 1440x1080; the game is 4:3)
    --no-sound          do not add the event's soundtrack (EVxxxx.wav) to the sequencer
    --no-cut-lighting   keep the rooms' lights as imported instead of applying the
                        cutscene's per-cut character lighting (PARAMS.BIN)
    --shadows           keep the lights' shadows (the game casts none from its lights;
                        with them, hair and low-poly edges throw hard shadows on faces)
    --blended           keep the importer's BLENDED render method for the characters'
                        BLEND materials (EEVEE then draws a mesh's translucent layers
                        unsorted, so the inside of a dress can show through its front);
                        the default switches them to DITHERED, which depth-sorts like
                        the game. Rooms keep BLENDED (decals over opaque surfaces)
    --no-physics        leave the cloth, hair and chest bones (X bones, moved by the game
                        at run time) in their bind pose. By default they get the game's
                        own simulation as a live Geometry Nodes setup (restarted at
                        every cut): play from frame 0, or bake it under Physics >
                        Simulation, before rendering a single frame
    --bake-physics      bake the simulations over the whole cutscene and pack them into the
                        .blend, so jumping to any frame shows what playing from the start
                        gives (free the bake in the HG Physics modifier to go back to live)
    --no-movie          leave out the event movie. By default it is placed on a plane in
                        front of the camera: full-screen pre-rendered inserts replace the
                        view during their cuts, effect movies are added over the view
    --view-transform T  colour management view transform (default Standard: the game's
                        colours unchanged; Blender's own default AgX darkens them)

From Blender's Text Editor: set CUTSCENE below and press Run Script (adds
the cutscene to the open file and sets its frame rate to 30 fps).
"""
import json
import math
import re
import struct
import sys
from pathlib import Path

import bpy

CUTSCENE = r""          # for use from the Text Editor, e.g. r"export/cutscenes/EV0010/EV0010.glb"
FPS = 30


def _find(path: str) -> Path:
    """A path as given, or else relative to the project folder (the parent of
    tools/), so relative paths work from Blender's Text Editor too."""
    p = Path(path)
    if not p.is_absolute() and not p.exists():
        try:
            p = Path(__file__).resolve().parent.parent / p
        except NameError:
            pass
    return p.resolve()


def base_name(name: str) -> str:
    """Blender appends .001, .002 ... when names collide between imports."""
    return re.sub(r"\.\d{3}$", "", name)


def import_gltf(path: Path) -> list:
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    return [o for o in bpy.data.objects if o not in before]


def copy_animation(src_id, dst_id) -> bool:
    """Give ``dst_id`` the action (and, on Blender 4.4+, the slot) that animates ``src_id``."""
    src = src_id.animation_data
    if src is None or src.action is None:
        return False
    dst = dst_id.animation_data_create()
    dst.action = src.action
    if hasattr(src, "action_slot") and src.action_slot is not None:
        dst.action_slot = src.action_slot
    return True


def scale_to_visibility(arm, meshes: list) -> int:
    """The cutscene hides an actor off stage by keying its scale to 0. Key
    its meshes' visibility instead and keep the armature at scale 1, so the
    bones (and the simulation hooked to them) keep running while hidden, as
    the game keeps simulating an actor that is off camera. Returns the
    number of visibility changes."""
    curves = [fc for fc in fcurves_of(arm) if fc.data_path == "scale"]
    if not curves:
        return 0
    first, last = (math.floor(curves[0].keyframe_points[0].co.x),
                   math.ceil(curves[0].keyframe_points[-1].co.x))
    changes, state = [], None
    for f in range(first, last + 1):            # the curve as the frames see it
        hidden = curves[0].evaluate(f) < 0.5
        if hidden != state:
            changes.append((f, hidden))
            state = hidden
    for mesh in meshes:
        if mesh.hide_render:                     # parts hidden for the whole cutscene
            continue
        for f, hidden in changes:
            mesh.hide_render = mesh.hide_viewport = hidden
            mesh.keyframe_insert("hide_render", frame=f)
            mesh.keyframe_insert("hide_viewport", frame=f)
        for path in ("hide_render", "hide_viewport"):
            constant_keys(mesh, path)
    for fc in curves:
        for kp in fc.keyframe_points:
            kp.co.y = kp.handle_left.y = kp.handle_right.y = 1.0
        fc.update()
    return len(changes)


def fcurves_of(id_data):
    ad = id_data.animation_data
    if ad is None or ad.action is None:
        return []
    action = ad.action
    if hasattr(action, "layers") and getattr(ad, "action_slot", None) is not None:
        curves = []
        for layer in action.layers:
            for strip in layer.strips:
                bag = strip.channelbag(ad.action_slot)
                if bag:
                    curves.extend(bag.fcurves)
        return curves
    return list(getattr(action, "fcurves", []))


def apply_variant(objs: list, variant: str) -> bool:
    """Switch the just-imported objects to a KHR_materials_variants variant.
    The importer replaces the scene's variant list on every import and meshes
    refer to variants by list position, so this must run right after each
    import and only on that import's meshes (the add-on's Display Variant
    button touches every mesh in the file)."""
    names = [v.name for v in bpy.context.scene.gltf2_KHR_materials_variants_variants]
    if variant not in names:
        return False
    index = names.index(variant)
    for obj in (o for o in objs if o.type == "MESH"):
        for entry in obj.data.gltf2_variant_mesh_data:
            if index in [v.variant.variant_idx for v in entry.variants]:
                if entry.material_slot_index < len(obj.material_slots):
                    obj.material_slots[entry.material_slot_index].material = entry.material
    return True


def read_gltf_json(path: Path) -> dict:
    data = path.read_bytes()
    if data[:4] == b"glTF":
        length = struct.unpack_from("<I", data, 12)[0]
        return json.loads(data[20:20 + length])
    return json.loads(data)


LUX_PER_UNIT = 2000.0 / 128.0      # the exporter's light scale: game colour 128 = 2000 lux
WATTS_PER_LUX = 1.0 / 683.0        # Blender sun strength (W/m^2) per lux


def constant_keys(id_data, path: str, index: int = -1) -> None:
    for fc in fcurves_of(id_data):
        if fc.data_path == path and (index < 0 or fc.array_index == index):
            for kp in fc.keyframe_points:
                kp.interpolation = "CONSTANT"


def apply_cut_lighting(scene, camera, cut_json: dict, room_refs: list, root: Path, report: list) -> None:
    """The game lights characters per cut (PARAMS.BIN 'lighting' records):
    room lights x room_light_scale, ambient = room ambient + ambient_add, and
    up to two directional lights whose direction is the camera's view turned
    by angles[0] about the camera's horizontal axis and angles[1] about up.
    Rooms are unlit (pre-lit vertex colours), so this only changes actors."""
    extras = next((n.get("extras", {}) for n in cut_json.get("nodes", [])
                   if "cut_parameters" in n.get("extras", {})), {})
    anim = (cut_json.get("animations") or [{}])[0].get("extras", {})
    cuts = [c["start_frame"] for c in anim.get("cuts", [])]
    if not cuts:
        return
    lighting = {p["start_frame"]: p for p in extras.get("cut_parameters", []) if p.get("type") == "lighting"}
    ambient = [0.0, 0.0, 0.0]
    for ref in room_refs:                       # room ambient (character_lighting) from the room file
        room_file = root / ref["room_file"]
        if room_file.is_file():
            for node in read_gltf_json(room_file).get("nodes", []):
                amb = node.get("extras", {}).get("character_lighting", {}).get("ambient_rgb")
                if amb:
                    ambient = amb
                    break
    room_lights = [o for o in scene.objects if o.type == "LIGHT" and o.parent is not None]
    base = {o.name: o.data.energy for o in room_lights}
    cut_lights = []
    for k in range(2):
        data = bpy.data.lights.new(f"cut_light{k}", "SUN")
        obj = bpy.data.objects.new(f"cut_light{k}", data)
        scene.collection.objects.link(obj)
        obj.parent = camera                      # follows the camera; shines along its view
        cut_lights.append(obj)
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    rec = None
    for frame in cuts:
        rec = lighting.get(frame, rec)          # a cut without a record keeps the previous lighting
        scale = rec["room_light_scale"] if rec else 1.0
        for o in room_lights:
            o.data.energy = base[o.name] * scale
            o.data.keyframe_insert("energy", frame=frame)
        add = rec["ambient_add"] if rec else [0, 0, 0]
        # irradiance of a uniform world = pi * radiance; match LUX_PER_UNIT
        amb = [(a + b) * LUX_PER_UNIT * WATTS_PER_LUX / math.pi for a, b in zip(ambient, add)]
        if bg is not None:
            bg.inputs[0].default_value = (*amb, 1.0)
            bg.inputs[1].default_value = 1.0
            bg.inputs[0].keyframe_insert("default_value", frame=frame)
        lights = rec["lights"] if rec else []
        for k, obj in enumerate(cut_lights):
            light = lights[k] if k < len(lights) else None
            color = light["color"] if light else [0, 0, 0]
            peak = max(color) or 1
            obj.data.color = [c / peak for c in color]
            obj.data.energy = LUX_PER_UNIT * max(color) * WATTS_PER_LUX
            pitch, yaw = (light["angles"] if light else (0.0, 0.0))
            obj.rotation_euler = (pitch, yaw, 0.0)
            obj.data.keyframe_insert("color", frame=frame)
            obj.data.keyframe_insert("energy", frame=frame)
            obj.keyframe_insert("rotation_euler", frame=frame)
    for o in room_lights:
        constant_keys(o.data, "energy")
    for o in cut_lights:
        for path in ("color", "energy"):
            constant_keys(o.data, path)
        constant_keys(o, "rotation_euler")
    if bg is not None:
        constant_keys(world.node_tree, 'nodes["Background"].inputs[0].default_value')
    report.append(f"cut lighting: {len(lighting)} of {len(cuts)} cuts, {len(room_lights)} room lights scaled, "
                  f"2 camera lights, ambient {ambient}")


def is_blended(mat) -> bool:
    if hasattr(mat, "surface_render_method"):            # Blender 4.2+
        return mat.surface_render_method == "BLENDED"
    return mat.blend_method == "BLEND"


def match_game_rendering(actor_objs: list, room_objs: list, objs: list, flags: set, report: list) -> None:
    """The game casts no shadows from its lights and draws translucent
    triangles in mesh order: a character's with depth writes (EEVEE's
    *Dithered* sorts them the same way), a room's after its opaque geometry
    (decals such as the soft shadows under plates lie on the opaque surface
    and are blended over it, which EEVEE's *Blended* does)."""
    lights = {o.data for o in objs if o.type == "LIGHT"}
    if "--shadows" not in flags:
        for light in lights:
            light.use_shadow = False
    report.append(f"render: {len(lights)} lights without shadows" if "--shadows" not in flags else "render: shadows kept")
    changed = 0
    if "--blended" not in flags:
        for mat in {s.material for o in actor_objs if o.type == "MESH" for s in o.material_slots if s.material}:
            if is_blended(mat):
                mat.surface_render_method = "DITHERED"
                changed += 1
    if changed:
        report.append(f"render: {changed} character BLEND materials -> DITHERED")
    # Lift room decals 1 mm off the surface they lie on: renderers that
    # intersect rays (Cycles) or resolve equal depths differently would
    # otherwise pick either of the two coplanar surfaces.
    lifted = 0
    for o in room_objs:
        if o.type != "MESH":
            continue
        me = o.data
        blend = {i for i, s in enumerate(o.material_slots) if s.material and is_blended(s.material)}
        if not blend:
            continue
        other = {v for f in me.polygons if f.material_index not in blend for v in f.vertices}
        normals = {}
        for f in me.polygons:
            if f.material_index in blend:
                for v in f.vertices:
                    if v not in other:
                        normals[v] = normals.get(v, f.normal * 0) + f.normal
        for v, n in normals.items():
            if n.length > 0:
                me.vertices[v].co += n.normalized() * 0.001    # rooms import at unit scale
        lifted += len(normals)
    if lifted:
        report.append(f"render: {lifted} room decal vertices lifted 1 mm")


def add_movie_layer(scene, camera, cut_json: dict, movie: Path, report: list) -> None:
    """The event movie on a plane just in front of the camera, filling the
    frame. ``inserts`` movies (full-screen pre-rendered shots) cover the view
    only during their insert cuts; ``overlay`` movies (effects on black) are
    added on top of the 3D view all the time."""
    info = next((n["extras"]["movie"] for n in cut_json.get("nodes", [])
                 if "movie" in n.get("extras", {})), None)
    if info is None or not movie.is_file():
        return
    anim = (cut_json.get("animations") or [{}])[0].get("extras", {})
    cuts = anim.get("cuts", [])
    inserts = info["mode"] == "inserts"
    if inserts and not info.get("insert_cuts"):
        return
    image = bpy.data.images.load(str(movie), check_existing=True)
    image.source = "MOVIE"
    mat = bpy.data.materials.new(f"{movie.stem}_movie")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.image_user.frame_duration = image.frame_duration
    tex.image_user.frame_start = 0            # scene frame 0 = first movie frame
    tex.image_user.use_auto_refresh = True
    emit = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(tex.outputs["Color"], emit.inputs["Color"])
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    if inserts:
        nt.links.new(emit.outputs[0], out.inputs["Surface"])
    else:                                      # additive: black adds nothing
        add = nt.nodes.new("ShaderNodeAddShader")
        nt.links.new(nt.nodes.new("ShaderNodeBsdfTransparent").outputs[0], add.inputs[0])
        nt.links.new(emit.outputs[0], add.inputs[1])
        nt.links.new(add.outputs[0], out.inputs["Surface"])
        if hasattr(mat, "surface_render_method"):
            mat.surface_render_method = "BLENDED"
    # plane at the frame's corners, just past the near clip plane
    depth = camera.data.clip_start * 1.05

    def corners():
        return [v / -v.z for v in camera.data.view_frame(scene=scene)]

    scene.frame_set(0)
    base = corners()
    mesh = bpy.data.meshes.new(f"{movie.stem}_movie")
    mesh.from_pydata([(v.x * depth, v.y * depth, 0.0) for v in base], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new()
    for loop in mesh.loops:                    # view_frame order: top right, bottom right, bottom left, top left
        uv.data[loop.index].uv = ((1, 1), (1, 0), (0, 0), (0, 1))[loop.vertex_index]
    mesh.materials.append(mat)
    plane = bpy.data.objects.new(f"{movie.stem}_movie", mesh)
    scene.collection.objects.link(plane)
    plane.parent = camera
    plane.location = (0.0, 0.0, -depth)
    for attr in ("visible_diffuse", "visible_glossy", "visible_transmission", "visible_volume_scatter",
                 "visible_shadow"):
        if hasattr(plane, attr):
            setattr(plane, attr, False)       # seen by the camera only, lights nothing
    width0 = base[0].x
    shown = set(info.get("insert_cuts", []))
    for c in cuts:                             # the frame size follows each cut's field of view
        scene.frame_set(c["start_frame"])
        r = corners()[0].x / width0
        plane.scale = (r, r, 1.0)
        plane.keyframe_insert("scale", frame=c["start_frame"])
        if inserts:
            plane.hide_render = plane.hide_viewport = c["cut"] not in shown
            plane.keyframe_insert("hide_render", frame=c["start_frame"])
            plane.keyframe_insert("hide_viewport", frame=c["start_frame"])
    for path in ("scale", "hide_render", "hide_viewport"):
        constant_keys(plane, path)
    report.append(f"movie: {movie.name} ({info['mode']}"
                  + (f", insert cuts {sorted(shown)})" if inserts else ")"))


# --------------------------------------------------------------- secondary motion
# The X bones (cloth, hair, chest) are never keyed by the game's clips: the
# game moves them at run time with particle "nodes" owned by the character
# object (docs/SECONDARY_MOTION.md). The exports keep them unanimated. Here
# every actor whose character class has nodes gets the same simulation,
# live (no keyframes): a proxy mesh with one point per node, moved by a
# Geometry Nodes simulation zone that reproduces the game's node types step
# for step (one step per 30 fps frame, nodes in the game's order, 30
# settling steps after a restart), and bone constraints that write the
# points back like the game does (bone origin on the point it hangs from,
# X axis aimed at its own point, roll from the game's up direction; chest
# bones moved onto theirs). The bones'
# animated frames (before the write-back) and the collision spheres and
# capsules are points hooked to the body.
# GAME_PHYSICS holds the game's own parameters (game units, 1 = 0.1 m, per
# frame), extracted by running each character class's constructor and
# setup routine from the executable. Per container: gravity, damping,
# colliders ("sphere", bone, offset, radius) / ("capsule", bone, offset,
# bone2, offset2, radius), and nodes (type, bone, previous node or -1 for a
# chain start, length, k, cone angle, partner node, carry weight, reference
# bone, plane normal in the reference bone's frame, side sign of an apron).
# Bones are game bone indices (the glTF joints' extras.bone_index).
GAME_PHYSICS = {
    "FIN": [
        {"gravity": 0.2, "damping": 0.9,
         "colliders": [],
         "nodes": [('skirt', 11, -1, 0.4092, 0.0, 0.3491, -1, 0.0, 2, (0.0, 1.0, 0.0), 0.0),
                   ('skirt', 12, 0, 0.4092, 0.0, 0.6981, -1, 0.0, 2, (0.0, 1.0, 0.0), 0.0),
                   ('skirt', 17, -1, 0.4092, 0.0, 0.3491, -1, 0.0, 6, (0.0, 1.0, 0.0), 0.0),
                   ('skirt', 18, 2, 0.4092, 0.0, 0.6981, -1, 0.0, 6, (0.0, 1.0, 0.0), 0.0),
                   ('skirt', 13, -1, 0.4092, 0.0, 0.3491, -1, 0.0, 2, (0.0, -1.0, 0.0), 0.0),
                   ('skirt', 14, 4, 0.4092, 0.0, 0.6981, -1, 0.0, 2, (0.0, -1.0, 0.0), 0.0),
                   ('skirt', 19, -1, 0.4092, 0.0, 0.3491, -1, 0.0, 6, (0.0, -1.0, 0.0), 0.0),
                   ('skirt', 20, 6, 0.4092, 0.0, 0.6981, -1, 0.0, 6, (0.0, -1.0, 0.0), 0.0),
                   ('skirt', 15, -1, 0.4092, 0.0, 0.3491, -1, 0.0, 2, (0.0, 0.0, 1.0), 0.0),
                   ('skirt', 16, 8, 0.4092, 0.0, 0.6981, -1, 0.0, 2, (0.0, 0.0, 1.0), 0.0),
                   ('skirt', 21, -1, 0.4092, 0.0, 0.3491, -1, 0.0, 6, (0.0, 0.0, -1.0), 0.0),
                   ('skirt', 22, 10, 0.4092, 0.0, 0.6981, -1, 0.0, 6, (0.0, 0.0, -1.0), 0.0)]},
        {"gravity": 0.1, "damping": 0.99,
         "colliders": [],
         "nodes": [('cone', 41, -1, 1.0, 0.0, 0.0, -1, 0.0, -1, None, 0.0)]},
        {"gravity": 0.1, "damping": 0.8,
         "colliders": [('sphere', 26, (0.0, 0.0, 0.0), 1.0), ('sphere', 42, (0.0, 0.0, 0.0), 1.0), ('sphere', 33, (0.0, 0.0, 0.0), 1.0), ('sphere', 27, (0.0, 0.0, 0.0), 1.0), ('sphere', 43, (0.0, 0.0, 0.0), 1.0), ('sphere', 33, (0.0, 1.0, 0.0), 1.0)],
         "nodes": [('hair', 37, -1, 0.45, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 38, 0, 0.45, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 39, 1, 0.45, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 40, 2, 0.45, 0.0, 0.0, -1, 0.0, -1, None, 0.0)]},
        {"gravity": 0.0, "damping": 0.75,
         "colliders": [],
         "nodes": [('jiggle', 25, -1, 0.0, 0.9, 0.0, -1, 0.0, -1, None, 0.0)]},
        {"gravity": 0.1, "damping": 0.99,
         "colliders": [],
         "nodes": [('cone', 48, -1, 1.0, 0.0, 0.0, -1, 0.0, -1, None, 0.0)]},
    ],
    "FIS": [
        {"gravity": 0.4, "damping": 0.6,
         "colliders": [],
         "nodes": [('sheet', 11, -1, 1.0, 0.0, 0.3491, -1, 0.0, 2, (0.0, 0.34, 0.94), 0.0),
                   ('sheet', 12, 0, 1.0, 0.0, 0.6981, -1, 0.0, 2, (0.0, 0.34, 0.94), 0.0),
                   ('sheet', 13, 1, 1.0, 0.0, 1.0472, -1, 0.0, 2, (0.0, 0.34, 0.94), 0.0),
                   ('sheet', 14, 2, 1.0, 0.0, 1.3963, -1, 0.0, 2, (0.0, 0.34, 0.94), 0.0),
                   ('sheet', 15, -1, 1.0, 0.0, 0.3491, -1, 0.0, 2, (0.0, 1.0, 0.0), 0.0),
                   ('sheet', 16, 4, 1.0, 0.0, 0.6981, -1, 0.0, 2, (0.0, 1.0, 0.0), 0.0),
                   ('sheet', 17, 5, 1.0, 0.0, 1.0472, -1, 0.0, 2, (0.0, 1.0, 0.0), 0.0),
                   ('sheet', 18, 6, 1.0, 0.0, 1.3963, -1, 0.0, 2, (0.0, 1.0, 0.0), 0.0),
                   ('sheet', 19, -1, 1.0, 0.0, 0.3491, -1, 0.0, 6, (0.0, 1.0, 0.0), 0.0),
                   ('sheet', 20, 8, 1.0, 0.0, 0.6981, -1, 0.0, 6, (0.0, 1.0, 0.0), 0.0),
                   ('sheet', 21, 9, 1.0, 0.0, 1.0472, -1, 0.0, 6, (0.0, 1.0, 0.0), 0.0),
                   ('sheet', 22, 10, 1.0, 0.0, 1.3963, -1, 0.0, 6, (0.0, 1.0, 0.0), 0.0),
                   ('sheet', 23, -1, 1.0, 0.0, 0.3491, -1, 0.0, 6, (0.0, 0.34, -0.94), 0.0),
                   ('sheet', 24, 12, 1.0, 0.0, 0.6981, -1, 0.0, 6, (0.0, 0.34, -0.94), 0.0),
                   ('sheet', 25, 13, 1.0, 0.0, 1.0472, -1, 0.0, 6, (0.0, 0.34, -0.94), 0.0),
                   ('sheet', 26, 14, 1.0, 0.0, 1.3963, -1, 0.0, 6, (0.0, 0.34, -0.94), 0.0),
                   ('sheet', 27, -1, 1.0, 0.0, 0.3491, -1, 0.0, 2, (0.0, -0.34, 0.94), 0.0),
                   ('sheet', 28, 16, 1.0, 0.0, 0.6981, -1, 0.0, 2, (0.0, -0.34, 0.94), 0.0),
                   ('sheet', 29, 17, 1.0, 0.0, 1.0472, -1, 0.0, 2, (0.0, -0.34, 0.94), 0.0),
                   ('sheet', 30, 18, 1.0, 0.0, 1.3963, -1, 0.0, 2, (0.0, -0.34, 0.94), 0.0),
                   ('sheet', 31, -1, 1.0, 0.0, 0.3491, -1, 0.0, 2, (0.0, -1.0, 0.0), 0.0),
                   ('sheet', 32, 20, 1.0, 0.0, 0.6981, -1, 0.0, 2, (0.0, -1.0, 0.0), 0.0),
                   ('sheet', 33, 21, 1.0, 0.0, 1.0472, -1, 0.0, 2, (0.0, -1.0, 0.0), 0.0),
                   ('sheet', 34, 22, 1.0, 0.0, 1.3963, -1, 0.0, 2, (0.0, -1.0, 0.0), 0.0),
                   ('sheet', 35, -1, 1.0, 0.0, 0.3491, -1, 0.0, 6, (0.0, -1.0, 0.0), 0.0),
                   ('sheet', 36, 24, 1.0, 0.0, 0.6981, -1, 0.0, 6, (0.0, -1.0, 0.0), 0.0),
                   ('sheet', 37, 25, 1.0, 0.0, 1.0472, -1, 0.0, 6, (0.0, -1.0, 0.0), 0.0),
                   ('sheet', 38, 26, 1.0, 0.0, 1.3963, -1, 0.0, 6, (0.0, -1.0, 0.0), 0.0),
                   ('sheet', 39, -1, 1.0, 0.0, 0.3491, -1, 0.0, 6, (0.0, -0.34, -0.94), 0.0),
                   ('sheet', 40, 28, 1.0, 0.0, 0.6981, -1, 0.0, 6, (0.0, -0.34, -0.94), 0.0),
                   ('sheet', 41, 29, 1.0, 0.0, 1.0472, -1, 0.0, 6, (0.0, -0.34, -0.94), 0.0),
                   ('sheet', 42, 30, 1.0, 0.0, 1.3963, -1, 0.0, 6, (0.0, -0.34, -0.94), 0.0)]},
        {"gravity": 0.1, "damping": 0.99,
         "colliders": [],
         "nodes": [('cone', 61, -1, 1.0, 0.0, 0.0, -1, 0.0, -1, None, 0.0)]},
        {"gravity": 0.1, "damping": 0.8,
         "colliders": [('sphere', 46, (0.0, 0.0, 0.0), 1.0), ('sphere', 62, (0.0, 0.0, 0.0), 1.0), ('sphere', 53, (0.0, 0.0, 0.0), 1.0), ('sphere', 47, (0.0, 0.0, 0.0), 1.0), ('sphere', 63, (0.0, 0.0, 0.0), 1.0), ('sphere', 53, (0.0, 1.0, 0.0), 1.0)],
         "nodes": [('hair', 57, -1, 0.45, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 58, 0, 0.45, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 59, 1, 0.45, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 60, 2, 0.45, 0.0, 0.0, -1, 0.0, -1, None, 0.0)]},
        {"gravity": 0.0, "damping": 0.75,
         "colliders": [],
         "nodes": [('jiggle', 45, -1, 0.0, 0.9, 0.0, -1, 0.0, -1, None, 0.0)]},
    ],
    "DNL": [
        {"gravity": 0.8, "damping": 0.5,
         "colliders": [('capsule', 2, (0.0, 1.0, 1.0), 6, (0.0, 1.0, -1.0), 1.6), ('capsule', 2, (1.0, 1.0, 1.0), 6, (1.0, 1.0, -1.0), 1.7), ('capsule', 2, (2.0, 1.0, 1.0), 6, (2.0, 1.0, -1.0), 1.8), ('capsule', 2, (3.0, 1.0, 1.0), 6, (3.0, 1.0, -1.0), 1.9), ('capsule', 2, (4.0, 1.0, 1.0), 6, (4.0, 1.0, -1.0), 2.0)],
         "nodes": [('apron', 11, -1, 1.2, 0.0, 0.0, 5, 0.8, -1, None, -1.0),
                   ('apron', 12, 0, 1.2, 0.0, 0.0, 6, 0.6, -1, None, -1.0),
                   ('apron', 13, 1, 1.2, 0.0, 0.0, 7, 0.4, -1, None, -1.0),
                   ('apron', 14, 2, 1.2, 0.0, 0.0, 8, 0.2, -1, None, -1.0),
                   ('apron', 15, 3, 1.2, 0.0, 0.0, 9, 0.0, -1, None, -1.0),
                   ('apron', 16, -1, 1.2, 0.0, 0.0, 0, 0.8, -1, None, 1.0),
                   ('apron', 17, 5, 1.2, 0.0, 0.0, 1, 0.6, -1, None, 1.0),
                   ('apron', 18, 6, 1.2, 0.0, 0.0, 2, 0.4, -1, None, 1.0),
                   ('apron', 19, 7, 1.2, 0.0, 0.0, 3, 0.2, -1, None, 1.0),
                   ('apron', 20, 8, 1.2, 0.0, 0.0, 4, 0.0, -1, None, 1.0)]},
        {"gravity": 0.3, "damping": 0.5,
         "colliders": [('capsule', 25, (0.0, 0.0, 0.0), 41, (0.0, 0.0, 0.0), 1.0), ('capsule', 23, (1.5, 0.0, -0.5), 23, (-1.5, 0.0, -0.5), 1.0), ('sphere', 31, (0.0, 0.0, 0.0), 1.0), ('sphere', 31, (0.0, 1.0, 0.0), 1.0)],
         "nodes": [('hair_push', 34, -1, 0.8, 0.2, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair_push', 35, 0, 0.8, 0.1, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair_push', 36, 1, 0.8, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair_push', 37, -1, 0.8, 0.2, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair_push', 38, 3, 0.8, 0.1, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair_push', 39, 4, 0.8, 0.0, 0.0, -1, 0.0, -1, None, 0.0)]},
        {"gravity": 0.0, "damping": 0.75,
         "colliders": [],
         "nodes": [('jiggle', 23, -1, 0.0, 0.9, 0.0, -1, 0.0, -1, None, 0.0)]},
        {"gravity": 0.1, "damping": 0.99,
         "colliders": [],
         "nodes": [('cone', 47, -1, 1.0, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('cone', 48, -1, 1.0, 0.0, 0.0, -1, 0.0, -1, None, 0.0)]},
    ],
    "RCG": [
        {"gravity": 0.5, "damping": 0.4,
         "colliders": [('sphere', 2, (-0.8, 0.0, 0.0), 1.0), ('sphere', 2, (0.0, 0.0, 0.0), 1.0), ('sphere', 2, (0.8, 0.0, 0.0), 1.0), ('sphere', 2, (1.6, 0.0, 0.0), 1.0)],
         "nodes": [('hair', 22, -1, 1.01, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 23, 0, 1.01, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 24, -1, 1.01, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 25, 2, 1.01, 0.0, 0.0, -1, 0.0, -1, None, 0.0)]},
        {"gravity": 0.1, "damping": 0.8,
         "colliders": [('capsule', 2, (-0.5, 0.0, -0.1), 6, (-0.5, 0.0, 0.1), 1.0), ('capsule', 2, (0.0, 0.0, -0.1), 6, (0.0, 0.0, 0.1), 1.0), ('capsule', 2, (0.5, 0.0, -0.1), 6, (0.5, 0.0, 0.1), 1.0), ('capsule', 2, (1.0, 0.0, -0.1), 6, (1.0, 0.0, 0.1), 1.0), ('capsule', 2, (0.0, -0.3, -0.1), 6, (0.0, -0.3, 0.1), 1.0)],
         "nodes": [('robe', 10, -1, 1.1, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('robe', 11, 0, 1.1, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('robe', 12, -1, 1.1, 0.5, 0.0, 0, 0.0, -1, None, 0.0),
                   ('robe', 13, 2, 1.1, 0.5, 0.0, 1, 0.0, -1, None, 0.0),
                   ('robe', 14, -1, 1.1, 0.5, 0.0, 0, 0.0, -1, None, 0.0),
                   ('robe', 15, 4, 1.1, 0.5, 0.0, 1, 0.0, -1, None, 0.0),
                   ('robe', 16, -1, 1.1, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('robe', 17, 6, 1.1, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('robe', 18, -1, 1.1, 0.5, 0.0, 6, 0.0, -1, None, 0.0),
                   ('robe', 19, 8, 1.1, 0.5, 0.0, 7, 0.0, -1, None, 0.0),
                   ('robe', 20, -1, 1.1, 0.5, 0.0, 6, 0.0, -1, None, 0.0),
                   ('robe', 21, 10, 1.1, 0.5, 0.0, 7, 0.0, -1, None, 0.0)]},
        {"gravity": 0.1, "damping": 0.8,
         "colliders": [],
         "nodes": [('stiff_hair', 38, -1, 0.67, 0.4, 0.0, -1, 0.0, -1, None, 0.0),
                   ('stiff_hair', 39, 0, 0.67, 0.4, 0.0, -1, 0.0, -1, None, 0.0),
                   ('stiff_hair', 40, -1, 0.67, 0.4, 0.0, -1, 0.0, -1, None, 0.0),
                   ('stiff_hair', 41, 2, 0.67, 0.4, 0.0, -1, 0.0, -1, None, 0.0)]},
    ],
    "RCT": [
        {"gravity": 0.5, "damping": 0.4,
         "colliders": [('sphere', 2, (-0.8, 0.0, 0.0), 1.0), ('sphere', 2, (0.0, 0.0, 0.0), 1.0), ('sphere', 2, (0.8, 0.0, 0.0), 1.0), ('sphere', 2, (1.6, 0.0, 0.0), 1.0)],
         "nodes": [('hair', 22, -1, 1.01, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 23, 0, 1.01, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 24, -1, 1.01, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 25, 2, 1.01, 0.0, 0.0, -1, 0.0, -1, None, 0.0)]},
        {"gravity": 0.1, "damping": 0.8,
         "colliders": [('capsule', 2, (-0.5, 0.0, -0.1), 6, (-0.5, 0.0, 0.1), 1.0), ('capsule', 2, (0.0, 0.0, -0.1), 6, (0.0, 0.0, 0.1), 1.0), ('capsule', 2, (0.5, 0.0, -0.1), 6, (0.5, 0.0, 0.1), 1.0), ('capsule', 2, (1.0, 0.0, -0.1), 6, (1.0, 0.0, 0.1), 1.0), ('capsule', 2, (0.0, -0.3, -0.1), 6, (0.0, -0.3, 0.1), 1.0)],
         "nodes": [('robe', 10, -1, 1.1, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('robe', 11, 0, 1.1, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('robe', 12, -1, 1.1, 0.5, 0.0, 0, 0.0, -1, None, 0.0),
                   ('robe', 13, 2, 1.1, 0.5, 0.0, 1, 0.0, -1, None, 0.0),
                   ('robe', 14, -1, 1.1, 0.5, 0.0, 0, 0.0, -1, None, 0.0),
                   ('robe', 15, 4, 1.1, 0.5, 0.0, 1, 0.0, -1, None, 0.0),
                   ('robe', 16, -1, 1.1, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('robe', 17, 6, 1.1, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('robe', 18, -1, 1.1, 0.5, 0.0, 6, 0.0, -1, None, 0.0),
                   ('robe', 19, 8, 1.1, 0.5, 0.0, 7, 0.0, -1, None, 0.0),
                   ('robe', 20, -1, 1.1, 0.5, 0.0, 6, 0.0, -1, None, 0.0),
                   ('robe', 21, 10, 1.1, 0.5, 0.0, 7, 0.0, -1, None, 0.0)]},
    ],
    "LRM": [
        {"gravity": 0.1, "damping": 0.8,
         "colliders": [('sphere', 36, (0.0, 0.0, 0.0), 1.0), ('sphere', 53, (0.0, 0.0, 0.0), 1.0), ('sphere', 37, (0.0, 0.0, 0.0), 1.0), ('sphere', 54, (0.0, 0.0, 0.0), 1.0), ('sphere', 43, (0.0, 0.0, 0.0), 1.0), ('sphere', 43, (0.0, 1.0, 0.0), 1.0)],
         "nodes": [('hair', 47, -1, 0.5542, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 48, 0, 0.5542, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 49, 1, 0.5542, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 50, -1, 0.5542, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 51, 3, 0.5542, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 52, 4, 0.5542, 0.0, 0.0, -1, 0.0, -1, None, 0.0)]},
        {"gravity": 0.2, "damping": 0.9,
         "colliders": [],
         "nodes": [('skirt', 6, -1, 2.125, 0.0, 0.3491, -1, 0.0, 2, (0.0, 1.0, 0.0), 0.0),
                   ('skirt', 7, 0, 2.125, 0.0, 0.6981, -1, 0.0, 2, (0.0, 1.0, 0.0), 0.0),
                   ('skirt', 8, 1, 2.125, 0.0, 1.0472, -1, 0.0, 3, (0.0, 1.0, 0.0), 0.0),
                   ('skirt', 9, 2, 2.125, 0.0, 1.3963, -1, 0.0, 3, (0.0, 1.0, 0.0), 0.0),
                   ('skirt', 22, -1, 2.125, 0.0, 0.3491, -1, 0.0, 18, (0.0, 1.0, 0.0), 0.0),
                   ('skirt', 23, 4, 2.125, 0.0, 0.6981, -1, 0.0, 18, (0.0, 1.0, 0.0), 0.0),
                   ('skirt', 24, 5, 2.125, 0.0, 1.0472, -1, 0.0, 19, (0.0, 1.0, 0.0), 0.0),
                   ('skirt', 25, 6, 2.125, 0.0, 1.3963, -1, 0.0, 19, (0.0, 1.0, 0.0), 0.0),
                   ('skirt', 10, -1, 2.125, 0.0, 0.3491, -1, 0.0, 2, (0.0, -1.0, 0.0), 0.0),
                   ('skirt', 11, 8, 2.125, 0.0, 0.6981, -1, 0.0, 2, (0.0, -1.0, 0.0), 0.0),
                   ('skirt', 12, 9, 2.125, 0.0, 1.0472, -1, 0.0, 3, (0.0, -1.0, 0.0), 0.0),
                   ('skirt', 13, 10, 2.125, 0.0, 1.3963, -1, 0.0, 3, (0.0, -1.0, 0.0), 0.0),
                   ('skirt', 26, -1, 2.125, 0.0, 0.3491, -1, 0.0, 18, (0.0, -1.0, 0.0), 0.0),
                   ('skirt', 27, 12, 2.125, 0.0, 0.6981, -1, 0.0, 18, (0.0, -1.0, 0.0), 0.0),
                   ('skirt', 28, 13, 2.125, 0.0, 1.0472, -1, 0.0, 19, (0.0, -1.0, 0.0), 0.0),
                   ('skirt', 29, 14, 2.125, 0.0, 1.3963, -1, 0.0, 19, (0.0, -1.0, 0.0), 0.0),
                   ('skirt', 14, -1, 2.125, 0.0, 0.3491, -1, 0.0, 2, (0.0, 0.0, 1.0), 0.0),
                   ('skirt', 15, 16, 2.125, 0.0, 0.6981, -1, 0.0, 2, (0.0, 0.0, 1.0), 0.0),
                   ('skirt', 16, 17, 2.125, 0.0, 1.0472, -1, 0.0, 3, (0.0, 0.0, 1.0), 0.0),
                   ('skirt', 17, 18, 2.125, 0.0, 1.3963, -1, 0.0, 3, (0.0, 0.0, 1.0), 0.0),
                   ('skirt', 30, -1, 2.125, 0.0, 0.3491, -1, 0.0, 18, (0.0, 0.0, -1.0), 0.0),
                   ('skirt', 31, 20, 2.125, 0.0, 0.6981, -1, 0.0, 18, (0.0, 0.0, -1.0), 0.0),
                   ('skirt', 32, 21, 2.125, 0.0, 1.0472, -1, 0.0, 19, (0.0, 0.0, -1.0), 0.0),
                   ('skirt', 33, 22, 2.125, 0.0, 1.3963, -1, 0.0, 19, (0.0, 0.0, -1.0), 0.0)]},
    ],
    "LRY": [
        {"gravity": 0.2, "damping": 0.9,
         "colliders": [],
         "nodes": [('skirt', 6, -1, 2.125, 0.0, 0.0873, -1, 0.0, 2, (0.0, 1.0, 0.0), 0.0),
                   ('skirt', 7, 0, 2.125, 0.0, 0.1745, -1, 0.0, 2, (0.0, 1.0, 0.0), 0.0),
                   ('skirt', 8, 1, 2.125, 0.0, 0.2618, -1, 0.0, 3, (0.0, 1.0, 0.0), 0.0),
                   ('skirt', 9, 2, 2.125, 0.0, 0.3491, -1, 0.0, 3, (0.0, 1.0, 0.0), 0.0),
                   ('skirt', 22, -1, 2.125, 0.0, 0.0873, -1, 0.0, 18, (0.0, 1.0, 0.0), 0.0),
                   ('skirt', 23, 4, 2.125, 0.0, 0.1745, -1, 0.0, 18, (0.0, 1.0, 0.0), 0.0),
                   ('skirt', 24, 5, 2.125, 0.0, 0.2618, -1, 0.0, 19, (0.0, 1.0, 0.0), 0.0),
                   ('skirt', 25, 6, 2.125, 0.0, 0.3491, -1, 0.0, 19, (0.0, 1.0, 0.0), 0.0),
                   ('skirt', 10, -1, 2.125, 0.0, 0.0873, -1, 0.0, 2, (0.0, -1.0, 0.0), 0.0),
                   ('skirt', 11, 8, 2.125, 0.0, 0.1745, -1, 0.0, 2, (0.0, -1.0, 0.0), 0.0),
                   ('skirt', 12, 9, 2.125, 0.0, 0.2618, -1, 0.0, 3, (0.0, -1.0, 0.0), 0.0),
                   ('skirt', 13, 10, 2.125, 0.0, 0.3491, -1, 0.0, 3, (0.0, -1.0, 0.0), 0.0),
                   ('skirt', 26, -1, 2.125, 0.0, 0.0873, -1, 0.0, 18, (0.0, -1.0, 0.0), 0.0),
                   ('skirt', 27, 12, 2.125, 0.0, 0.1745, -1, 0.0, 18, (0.0, -1.0, 0.0), 0.0),
                   ('skirt', 28, 13, 2.125, 0.0, 0.2618, -1, 0.0, 19, (0.0, -1.0, 0.0), 0.0),
                   ('skirt', 29, 14, 2.125, 0.0, 0.3491, -1, 0.0, 19, (0.0, -1.0, 0.0), 0.0),
                   ('skirt', 14, -1, 2.125, 0.0, 0.0873, -1, 0.0, 2, (0.0, 0.0, 1.0), 0.0),
                   ('skirt', 15, 16, 2.125, 0.0, 0.1745, -1, 0.0, 2, (0.0, 0.0, 1.0), 0.0),
                   ('skirt', 16, 17, 2.125, 0.0, 0.2618, -1, 0.0, 3, (0.0, 0.0, 1.0), 0.0),
                   ('skirt', 17, 18, 2.125, 0.0, 0.3491, -1, 0.0, 3, (0.0, 0.0, 1.0), 0.0),
                   ('skirt', 30, -1, 2.125, 0.0, 0.0873, -1, 0.0, 18, (0.0, 0.0, -1.0), 0.0),
                   ('skirt', 31, 20, 2.125, 0.0, 0.1745, -1, 0.0, 18, (0.0, 0.0, -1.0), 0.0),
                   ('skirt', 32, 21, 2.125, 0.0, 0.2618, -1, 0.0, 19, (0.0, 0.0, -1.0), 0.0),
                   ('skirt', 33, 22, 2.125, 0.0, 0.3491, -1, 0.0, 19, (0.0, 0.0, -1.0), 0.0)]},
    ],
    "LRO": [
        {"gravity": 0.1, "damping": 0.8,
         "colliders": [('sphere', 12, (0.0, 0.0, 0.0), 1.0), ('sphere', 29, (0.0, 0.0, 0.0), 1.0), ('sphere', 13, (0.0, 0.0, 0.0), 1.0), ('sphere', 30, (0.0, 0.0, 0.0), 1.0), ('sphere', 19, (0.0, 0.0, 0.0), 1.0), ('sphere', 19, (0.0, 1.0, 0.0), 1.0), ('sphere', 19, (0.0, 0.0, 0.4), 1.0), ('sphere', 19, (0.0, 1.0, 0.4), 1.0)],
         "nodes": [('hair', 23, -1, 0.5542, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 24, 0, 0.5542, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 25, 1, 0.5542, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 26, -1, 0.5542, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 27, 3, 0.5542, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 28, 4, 0.5542, 0.0, 0.0, -1, 0.0, -1, None, 0.0)]},
    ],
    "DB0": [
        {"gravity": 0.4, "damping": 0.99,
         "colliders": [('sphere', 2, (0.0, 0.0, 0.0), 1.8), ('sphere', 2, (1.0, 0.0, 0.0), 1.8)],
         "nodes": [('hair', 10, -1, 1.25, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 11, 0, 1.25, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 12, 0, 1.25, 0.0, 0.0, -1, 0.0, -1, None, 0.0),
                   ('hair', 13, 0, 1.25, 0.0, 0.0, -1, 0.0, -1, None, 0.0)]},
    ],
}
PHYSICS_CLASSES = {"FIN": "FIN", "FIW": "FIN", "FIS": "FIS", "DNL": "DNL", "RCG": "RCG", "RCT": "RCT",
                   "LRM": "LRM", "LRY": "LRY", "LRO": "LRO", "DB0": "DB0"}   # model family -> character class
NODE_TYPES = {"hair": 0, "cone": 1, "jiggle": 2, "skirt": 3, "sheet": 4, "stiff_hair": 5, "robe": 6,
              "hair_push": 7, "apron": 8}
PUSH_STRENGTH = {"hair": 1.0, "robe": 1.0, "hair_push": 1.0, "apron": 3.0}  # types that collide
UNIT = 0.1                      # metres per game unit (the exporter's scale)
PUSH_BONE = 31                  # hair_push pushes along this bone's Z axis (fixed in the game code)
PHYSICS = {"Settle Steps": 30, "Teleport": 0.25, "Max Skip": 4, "Snap": 0.03, "Snap Ratio": 2.0}
# The hair floor: a character method (Fiona: vtable +188, 0x2F6DF0) sets a
# floor height and switch on her ponytail's container; hair nodes below it
# are pushed up (v.y += floor - y). Cutscene scripts switch it on (event
# command handler 0x2ABBC0: height 0.25 game units, world Y). Its use per
# event, as recorded from the running game: event -> [(frame, height in
# game units or None for off)].
# What the game re-seeds on its reset (per class driver): container index ->
# (mode, bone, sign); robe nodes 1-6 go along +Z, 7-12 along -Z.
RESEED = {"FIN": {2: (1, 33, -1.0)}, "FIS": {2: (1, 53, -1.0)}, "RCG": {1: (1, 1, 0.0)},
          "RCT": {1: (1, 1, 0.0)}, "DNL": {0: (1, 0, 1.0), 1: (2, 31, 0.0)}}
HAIR_FLOOR_CONTAINER = {"FIN": 2}         # class -> container index the setter writes
EVENT_HAIR_FLOOR = {"EV0004": [(4415, 0.25)]}
PHYSICS_GROUP = "HG Game Physics"
PHYSICS_UP_GROUP = "HG Physics Up"


def _game_frames(model_json: dict) -> dict:
    """Game bone index -> (joint name, origin, (X, Y, Z) axes) of the bind
    pose in Blender armature space (glTF Y-up converted to Z-up)."""
    from mathutils import Matrix, Quaternion, Vector
    nodes = model_json.get("nodes", [])
    parent = {c: i for i, n in enumerate(nodes) for c in n.get("children", [])}
    cache = {}

    def world(i):
        if i not in cache:
            n = nodes[i]
            if "matrix" in n:
                m = Matrix([n["matrix"][r::4] for r in range(4)])
            else:
                x, y, z, w = n.get("rotation", (0.0, 0.0, 0.0, 1.0))
                m = (Matrix.Translation(Vector(n.get("translation", (0.0, 0.0, 0.0))))
                     @ Quaternion((w, x, y, z)).to_matrix().to_4x4()
                     @ Matrix.Diagonal((*n.get("scale", (1.0, 1.0, 1.0)), 1.0)))
            cache[i] = world(parent[i]) @ m if i in parent else m
        return cache[i]

    conv = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
    out = {}
    for skin in model_json.get("skins", [])[:1]:
        for i in skin["joints"]:
            index = (nodes[i].get("extras") or {}).get("bone_index")
            if index is not None:
                m = conv @ world(i)
                out[index] = (nodes[i]["name"], m.translation.copy(),
                              tuple(m.col[k].xyz.normalized() for k in range(3)))
    return out


class _Graph:
    """Small helper for building Geometry Nodes graphs: inputs may be
    sockets (linked) or plain values (set)."""

    def __init__(self, nt):
        self.nt = nt

    def node(self, kind, **props):
        n = self.nt.nodes.new(kind)
        for k, v in props.items():
            setattr(n, k, v)
        return n

    def put(self, socket, value):
        if isinstance(value, bpy.types.NodeSocket):
            self.nt.links.new(value, socket)
        elif value is not None:
            socket.default_value = value

    def vec(self, op, a, b=None, scale=None):
        n = self.node("ShaderNodeVectorMath", operation=op)
        self.put(n.inputs[0], a)
        if b is not None:
            self.put(n.inputs[1], b)
        if scale is not None:
            self.put(n.inputs["Scale"], scale)
        return n.outputs["Value"] if op in ("DOT_PRODUCT", "LENGTH", "DISTANCE") else n.outputs["Vector"]

    def add(self, *vs):
        out = vs[0]
        for v in vs[1:]:
            out = self.vec("ADD", out, v)
        return out

    def sub(self, a, b):
        return self.vec("SUBTRACT", a, b)

    def scale(self, a, s):
        return self.vec("SCALE", a, scale=s)

    def dot(self, a, b):
        return self.vec("DOT_PRODUCT", a, b)

    def norm(self, a):
        return self.vec("NORMALIZE", a)

    def cross(self, a, b):
        return self.vec("CROSS_PRODUCT", a, b)

    def length(self, a):
        return self.vec("LENGTH", a)

    def math(self, op, a, b=None):
        n = self.node("ShaderNodeMath", operation=op)
        self.put(n.inputs[0], a)
        if b is not None:
            self.put(n.inputs[1], b)
        return n.outputs[0]

    def clamp(self, a, lo, hi):
        return self.math("MINIMUM", self.math("MAXIMUM", a, lo), hi)

    def boolean(self, op, a, b=None):
        n = self.node("FunctionNodeBooleanMath", operation=op)
        self.put(n.inputs[0], a)
        if b is not None:
            self.put(n.inputs[1], b)
        return n.outputs[0]

    def int_eq(self, a, b):
        n = self.node("FunctionNodeCompare", data_type="INT", operation="EQUAL")
        self.put(n.inputs[0], a)
        self.put(n.inputs[1], b)
        return n.outputs["Result"]

    def switch(self, kind, cond, false, true):
        n = self.node("GeometryNodeSwitch", input_type=kind)
        self.put(n.inputs["Switch"], cond)
        self.put(n.inputs["False"], false)
        self.put(n.inputs["True"], true)
        return n.outputs["Output"]

    def attr(self, name, kind):
        n = self.node("GeometryNodeInputNamedAttribute", data_type=kind)
        n.inputs["Name"].default_value = name
        return n.outputs["Attribute"]

    def sample(self, geometry, value, index, kind):
        n = self.node("GeometryNodeSampleIndex", data_type=kind, domain="POINT")
        self.put(n.inputs["Geometry"], geometry)
        self.put(n.inputs["Value"], value)
        self.put(n.inputs["Index"], index)
        return n.outputs["Value"]

    def store(self, geometry, name, value, kind, selection=None):
        n = self.node("GeometryNodeStoreNamedAttribute", data_type=kind, domain="POINT")
        n.inputs["Name"].default_value = name
        self.put(n.inputs["Geometry"], geometry)
        self.put(n.inputs["Value"], value)
        if selection is not None:
            self.put(n.inputs["Selection"], selection)
        return n.outputs["Geometry"]

    def set_position(self, geometry, position, selection=None):
        n = self.node("GeometryNodeSetPosition")
        self.put(n.inputs["Geometry"], geometry)
        self.put(n.inputs["Position"], position)
        if selection is not None:
            self.put(n.inputs["Selection"], selection)
        return n.outputs["Geometry"]

    def stat_max(self, geometry, value):
        n = self.node("GeometryNodeAttributeStatistic", data_type="FLOAT", domain="POINT")
        self.put(n.inputs["Geometry"], geometry)
        self.put(n.inputs["Attribute"], value)
        return n.outputs["Max"]

    def xyz(self, x=0.0, y=0.0, z=0.0):
        n = self.node("ShaderNodeCombineXYZ")
        for i, v in enumerate((x, y, z)):
            self.put(n.inputs[i], v)
        return n.outputs[0]

    def split(self, v):
        n = self.node("ShaderNodeSeparateXYZ")
        self.put(n.inputs[0], v)
        return n.outputs[0], n.outputs[1], n.outputs[2]

    def repeat(self, geometry, iterations, counter=None):
        rin, rout = self.node("GeometryNodeRepeatInput"), self.node("GeometryNodeRepeatOutput")
        rin.pair_with_output(rout)
        if counter:
            rout.repeat_items.new("INT", counter)
        self.put(rin.inputs["Iterations"], iterations)
        self.put(rin.inputs["Geometry"], geometry)
        return rin, rout


def _physics_node_group():
    """The game's node steps as a Geometry Nodes simulation (see the node
    attributes written by add_physics)."""
    nt = bpy.data.node_groups.get(PHYSICS_GROUP)
    if nt is not None:
        return nt
    nt = bpy.data.node_groups.new(PHYSICS_GROUP, "GeometryNodeTree")
    io = nt.interface
    io.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    io.new_socket("Frames", in_out="INPUT", socket_type="NodeSocketObject")
    io.new_socket("Colliders", in_out="INPUT", socket_type="NodeSocketObject")
    io.new_socket("Reset", in_out="INPUT", socket_type="NodeSocketBool")
    io.new_socket("Hair Floor On", in_out="INPUT", socket_type="NodeSocketBool")
    io.new_socket("Hair Floor", in_out="INPUT", socket_type="NodeSocketFloat")
    for name, default in PHYSICS.items():
        sock = io.new_socket(name, in_out="INPUT",
                             socket_type="NodeSocketInt" if isinstance(default, int) else "NodeSocketFloat")
        sock.default_value = default
    io.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    G = _Graph(nt)
    gin, gout = G.node("NodeGroupInput"), G.node("NodeGroupOutput")
    position = G.node("GeometryNodeInputPosition").outputs[0]
    index = G.node("GeometryNodeInputIndex").outputs[0]

    def object_geometry(socket):
        n = G.node("GeometryNodeObjectInfo", transform_space="ORIGINAL")   # armature space, like the proxy
        G.put(n.inputs["Object"], socket)
        return n.outputs["Geometry"]

    frames, cols = object_geometry(gin.outputs["Frames"]), object_geometry(gin.outputs["Colliders"])

    def frame(base_attr):
        """A bone's animated frame: origin and unit X/Y/Z axes, from four hooked points."""
        base = G.attr(base_attr, "INT")
        origin = G.sample(frames, position, base, "FLOAT_VECTOR")
        axes = [G.scale(G.sub(G.sample(frames, position, G.math("ADD", base, k), "FLOAT_VECTOR"), origin),
                        1.0 / UNIT) for k in (1, 2, 3)]
        return origin, axes

    O, (X, Y, Z) = frame("hg_f")
    _RO, (RX, RY, RZ) = frame("hg_r")
    kind = G.attr("hg_type", "INT")
    is_type = {name: G.int_eq(kind, k) for name, k in NODE_TYPES.items()}
    length, k_val = G.attr("hg_len", "FLOAT"), G.attr("hg_k", "FLOAT")
    damping, gravity = G.attr("hg_damp", "FLOAT"), G.attr("hg_grav", "FLOAT")

    sim_in, sim_out = G.node("GeometryNodeSimulationInput"), G.node("GeometryNodeSimulationOutput")
    sim_in.pair_with_output(sim_out)
    G.put(sim_in.inputs["Geometry"], gin.outputs["Geometry"])
    state = sim_in.outputs["Geometry"]
    # one game step per frame. The simulation runs when the frame changes;
    # when the viewport drops frames during playback it catches up with one
    # step per frame that went by since its last run (Blender's delta time
    # stays one frame). A larger jump (scrubbing, jumping to a frame) or a
    # step backwards cannot be simulated without the frames in between, so
    # it restarts there, like a cut
    scene_frame = G.node("GeometryNodeInputSceneTime").outputs["Frame"]
    gap = G.math("ROUND", G.math("SUBTRACT", scene_frame, G.stat_max(state, G.attr("hg_frame", "FLOAT"))))
    skipped = G.boolean("OR", G.math("LESS_THAN", gap, 0.5), G.math("GREATER_THAN", gap, gin.outputs["Max Skip"]))
    # restart: keyed Reset (cuts), a frame jump, the body jumping more than
    # Teleport metres in a frame, and broken state (NaN, or points left far
    # behind)
    jump = G.stat_max(state, G.vec("DISTANCE", O, G.attr("hg_O", "FLOAT_VECTOR")))
    far = G.stat_max(state, G.vec("DISTANCE", position, O))
    # (a jump at a keyed Reset is the game's cut reset, as in the game)
    restart = G.boolean("AND", G.math("GREATER_THAN", jump, gin.outputs["Teleport"]),
                        G.boolean("NOT", gin.outputs["Reset"]))
    restart = G.boolean("OR", restart, G.boolean("NOT", G.math("LESS_THAN", far, 50.0)))
    restart = G.boolean("OR", restart, skipped)
    # the game's own reset (keyed Reset): only some chains are re-seeded
    # (hg_rs: 1 along +-the Z axis of bone hg_q, 2 Daniella's hair: along
    # that Z axis x k plus the gravity vector), everything else keeps its
    # position and velocity; then Settle Steps steps
    # The game also resets when an actor's motion changes inside a cut; the
    # animation shows that as a pose splice: the body jumps more than Snap
    # metres and Snap Ratio times its previous frame's movement
    last_jump = G.stat_max(state, G.attr("hg_jump", "FLOAT"))
    snap = G.boolean("AND", G.math("GREATER_THAN", jump, gin.outputs["Snap"]),
                     G.math("GREATER_THAN", jump, G.math("MULTIPLY", last_jump, gin.outputs["Snap Ratio"])))
    game_reset = G.boolean("AND", G.boolean("OR", gin.outputs["Reset"], snap), G.boolean("NOT", restart))
    # re-seed in the bind shape: each point on its bone's X axis, chests on their bone
    seed = G.switch("VECTOR", is_type["jiggle"], G.add(O, G.scale(X, length)), O)
    geo = G.set_position(state, G.switch("VECTOR", restart, position, seed))
    geo = G.store(geo, "hg_v", G.switch("VECTOR", restart, G.attr("hg_v", "FLOAT_VECTOR"), G.xyz()), "FLOAT_VECTOR")
    geo = G.store(geo, "hg_aprev", G.switch("VECTOR", restart, G.attr("hg_aprev", "FLOAT_VECTOR"), O), "FLOAT_VECTOR")
    orders = G.math("ADD", G.stat_max(geo, G.attr("hg_order", "INT")), 1.0)
    rs_in, rs_out = G.repeat(geo, G.switch("INT", game_reset, 0, orders), counter="Order")
    rcur = rs_in.outputs["Geometry"]
    mode = G.attr("hg_rs", "INT")
    _QO, (_QX, _QY, QZ) = frame("hg_q")
    ranchor = G.switch("VECTOR", G.attr("hg_start", "BOOLEAN"),
                       G.sample(rcur, position, G.attr("hg_prev", "INT"), "FLOAT_VECTOR"), O)
    hair_dir = G.norm(G.add(G.scale(QZ, k_val), G.xyz(z=G.math("DIVIDE", gravity, UNIT))))
    rdir = G.switch("VECTOR", G.int_eq(mode, 2), G.scale(QZ, G.attr("hg_rs_sign", "FLOAT")), hair_dir)
    rsel = G.boolean("AND", G.int_eq(G.attr("hg_order", "INT"), rs_in.outputs["Order"]),
                     G.boolean("NOT", G.int_eq(mode, 0)))
    rgeo = G.store(rcur, "hg_v", G.xyz(), "FLOAT_VECTOR", selection=rsel)
    rgeo = G.store(rgeo, "hg_aprev", ranchor, "FLOAT_VECTOR", selection=rsel)
    G.put(rs_out.inputs["Geometry"], G.set_position(rgeo, G.add(ranchor, G.scale(rdir, length)), rsel))
    G.put(rs_out.inputs["Order"], G.math("ADD", rs_in.outputs["Order"], 1.0))
    geo = rs_out.outputs["Geometry"]
    steps = G.switch("INT", G.boolean("OR", restart, game_reset), G.math("MAXIMUM", gap, 1.0),
                     gin.outputs["Settle Steps"])

    step_in, step_out = G.repeat(geo, steps)
    node_in, node_out = G.repeat(step_in.outputs["Geometry"], orders, counter="Order")
    cur = node_in.outputs["Geometry"]
    order = node_in.outputs["Order"]
    selected = G.int_eq(G.attr("hg_order", "INT"), order)
    p0, v0, aprev = position, G.attr("hg_v", "FLOAT_VECTOR"), G.attr("hg_aprev", "FLOAT_VECTOR")
    anchor = G.switch("VECTOR", G.attr("hg_start", "BOOLEAN"),
                      G.sample(cur, position, G.attr("hg_prev", "INT"), "FLOAT_VECTOR"), O)
    partner = G.sample(cur, position, G.attr("hg_partner", "INT"), "FLOAT_VECTOR")
    # robes and aprons are first carried along by their anchor's motion
    carry = G.switch("FLOAT", is_type["robe"], G.attr("hg_weight", "FLOAT"), 0.6)
    pc = G.add(p0, G.scale(G.sub(anchor, aprev), carry))

    # collider pushes, summed over the colliders of the node's container:
    # spheres push by d (1 - |d|/r), capsules by unit d (1 - |d|/r)
    size = G.node("GeometryNodeAttributeDomainSize", component="MESH")
    G.put(size.inputs["Geometry"], cols)
    col_in, col_out = G.repeat(G.store(cur, "hg_push", G.xyz(), "FLOAT_VECTOR"), size.outputs["Edge Count"],
                               counter="Collider")
    c = col_in.outputs["Collider"]
    first = G.math("MULTIPLY", c, 2.0)
    a = G.sample(cols, position, first, "FLOAT_VECTOR")
    b = G.sample(cols, position, G.math("ADD", first, 1.0), "FLOAT_VECTOR")
    radius = G.sample(cols, G.attr("hg_radius", "FLOAT"), first, "FLOAT")
    ab = G.sub(b, a)
    t = G.math("DIVIDE", G.dot(G.sub(pc, a), ab), G.math("MAXIMUM", G.dot(ab, ab), 1e-12))
    q = G.add(a, G.scale(ab, G.clamp(t, 0.0, 1.0)))
    d = G.sub(pc, q)
    dist = G.length(d)
    fall = G.math("SUBTRACT", 1.0, G.math("DIVIDE", dist, G.math("MAXIMUM", radius, 1e-9)))
    capsule = G.sample(cols, G.attr("hg_capsule", "BOOLEAN"), first, "BOOLEAN")
    push = G.switch("VECTOR", capsule, G.scale(d, fall), G.scale(G.norm(d), G.math("MULTIPLY", fall, UNIT)))
    hit = G.boolean("AND", G.math("LESS_THAN", dist, radius), G.math("GREATER_THAN", dist, 0.0))
    hit = G.boolean("AND", hit, G.int_eq(G.sample(cols, G.attr("hg_cont", "INT"), first, "INT"),
                                         G.attr("hg_cont", "INT")))
    G.put(col_out.inputs["Geometry"],
          G.store(col_in.outputs["Geometry"], "hg_push", G.add(G.attr("hg_push", "FLOAT_VECTOR"), push), "FLOAT_VECTOR",
                  selection=hit))
    G.put(col_out.inputs["Collider"], G.math("ADD", c, 1.0))
    pushed = col_out.outputs["Geometry"]

    down = G.xyz(z=gravity)                       # subtracted: the game's gravity points down its -Y (Blender -Z)
    # chain types (hair, stiff_hair, robe, hair_push, apron): forces, damping,
    # then the fixed length from the anchor
    forces = [G.sub(v0, down), G.scale(G.attr("hg_push", "FLOAT_VECTOR"), G.attr("hg_strength", "FLOAT"))]
    target = G.add(O, G.scale(X, length))                                   # stiff hair: spring to the rest shape
    forces.append(G.scale(G.scale(G.sub(target, pc), k_val), is_type["stiff_hair"]))
    forces.append(G.scale(G.scale(RZ, G.math("MULTIPLY", k_val, UNIT)), is_type["hair_push"]))
    # hair floor (scripted): pushed up by the depth below it
    _px, _py, pz_now = G.split(pc)
    below = G.math("MAXIMUM", G.math("SUBTRACT", gin.outputs["Hair Floor"], pz_now), 0.0)
    floored = G.boolean("AND", gin.outputs["Hair Floor On"], G.attr("hg_floor", "BOOLEAN"))
    forces.append(G.xyz(z=G.math("MULTIPLY", below, floored)))
    to_partner = G.sub(partner, pc)
    pdist = G.length(to_partner)
    has_partner = G.attr("hg_has_partner", "BOOLEAN")
    robe_k = G.math("DIVIDE", G.math("MULTIPLY", k_val, G.math("SUBTRACT", pdist, 1.9 * UNIT)),
                    G.math("MAXIMUM", pdist, 1e-9))
    forces.append(G.scale(G.scale(to_partner, robe_k), G.boolean("AND", is_type["robe"], has_partner)))
    apron_k = G.math("MULTIPLY", 0.8, G.math("SUBTRACT", 0.4379 * UNIT, pdist))
    forces.append(G.scale(G.scale(G.norm(G.sub(pc, partner)), apron_k), G.boolean("AND", is_type["apron"], has_partner)))
    p1 = G.add(pc, G.scale(G.add(*forces), damping))
    chain_p = G.add(anchor, G.scale(G.norm(G.sub(p1, anchor)), length))
    chain_v = G.sub(chain_p, G.switch("VECTOR", is_type["robe"], p0, pc))  # robes measure from the carried point

    # cone (bangs): may not swing behind the bone's Y plane, 30 degrees around X
    cp1 = G.add(p0, G.scale(G.sub(v0, down), damping))
    cd = G.sub(cp1, O)
    cd = G.sub(cd, G.scale(Y, G.math("MINIMUM", G.dot(cd, Y), 0.0)))
    cn = G.norm(cd)
    ang = G.math("ARCCOSINE", G.clamp(G.dot(cn, X), -1.0, 1.0))
    lim = math.radians(30.0)
    sin_ang = G.math("SINE", ang)
    over = G.boolean("AND", G.math("GREATER_THAN", ang, lim), G.math("GREATER_THAN", sin_ang, 1e-6))
    edge = G.scale(G.add(G.scale(cn, math.sin(lim)), G.scale(X, G.math("SINE", G.math("SUBTRACT", ang, lim)))),
                   G.math("DIVIDE", 1.0, G.math("MAXIMUM", sin_ang, 1e-6)))
    cone_p = G.add(O, G.scale(G.switch("VECTOR", over, cn, edge), length))

    # jiggle (chest): spring to the bone, offset kept in the bone's X/Y plane and boxed
    jp1 = G.add(p0, G.scale(G.add(v0, G.scale(G.sub(O, p0), k_val)), damping))
    jd = G.sub(jp1, O)
    jd = G.sub(jd, G.scale(Z, G.dot(jd, Z)))
    jx = G.clamp(G.dot(jd, X), -0.2 * UNIT, 0.2 * UNIT)
    jy = G.clamp(G.dot(jd, Y), -0.2 * UNIT, 0.3 * UNIT)
    jiggle_p = G.add(O, G.scale(X, jx), G.scale(Y, jy))

    # panels (skirt, sheet): gravity along the reference thigh's X axis,
    # direction kept within the node's cone around it, then the plane rule
    pp1 = G.add(p0, G.scale(G.add(v0, G.scale(RX, gravity)), damping))
    pn = G.norm(G.sub(pp1, anchor))
    pang = G.math("ARCCOSINE", G.clamp(G.dot(pn, RX), -1.0, 1.0))
    cone = G.attr("hg_cone", "FLOAT")
    psin = G.math("SINE", pang)
    pover = G.boolean("AND", G.math("GREATER_THAN", pang, cone), G.math("GREATER_THAN", psin, 1e-6))
    pedge = G.scale(G.add(G.scale(pn, G.math("SINE", cone)), G.scale(RX, G.math("SINE", G.math("SUBTRACT", pang, cone)))),
                    G.math("DIVIDE", 1.0, G.math("MAXIMUM", psin, 1e-6)))
    pd = G.scale(G.switch("VECTOR", pover, pn, pedge), length)
    pp2 = G.add(anchor, pd)
    px, py, pz = G.split(G.attr("hg_plane", "FLOAT_VECTOR"))
    normal = G.add(G.scale(RX, px), G.scale(RY, py), G.scale(RZ, pz))
    side = G.dot(pd, normal)
    # skirt: pushed back onto the plane when behind it
    skirt_p = G.switch("VECTOR", G.math("LESS_THAN", side, 0.0), pp2,
                       G.add(anchor, G.scale(G.norm(G.sub(G.sub(pp2, G.scale(normal, side)), anchor)), length)))
    # sheet: kept in the plane of the thigh axis and the normal, on its positive side
    sheet_p = G.add(anchor, G.scale(G.norm(G.add(G.scale(RX, G.dot(pd, RX)),
                                                 G.scale(normal, G.math("MAXIMUM", side, 0.0)))), length))

    new_p = chain_p
    for name, p in (("cone", cone_p), ("jiggle", jiggle_p), ("skirt", skirt_p), ("sheet", sheet_p)):
        new_p = G.switch("VECTOR", is_type[name], new_p, p)
    # velocity = movement over the step (chains: from the point before the
    # step, robes: from the carried point)
    local = G.boolean("OR", G.boolean("OR", is_type["cone"], is_type["jiggle"]),
                      G.boolean("OR", is_type["skirt"], is_type["sheet"]))
    new_v = G.switch("VECTOR", local, chain_v, G.sub(new_p, p0))
    staged = G.store(pushed, "hg_np", new_p, "FLOAT_VECTOR", selection=selected)
    staged = G.store(staged, "hg_nv", new_v, "FLOAT_VECTOR", selection=selected)
    staged = G.store(staged, "hg_na", anchor, "FLOAT_VECTOR", selection=selected)
    done = G.set_position(staged, G.attr("hg_np", "FLOAT_VECTOR"), selected)
    done = G.store(done, "hg_v", G.attr("hg_nv", "FLOAT_VECTOR"), "FLOAT_VECTOR", selection=selected)
    done = G.store(done, "hg_aprev", G.attr("hg_na", "FLOAT_VECTOR"), "FLOAT_VECTOR", selection=selected)
    G.put(node_out.inputs["Geometry"], done)
    G.put(node_out.inputs["Order"], G.math("ADD", order, 1.0))
    G.put(step_out.inputs["Geometry"], node_out.outputs["Geometry"])
    G.put(sim_out.inputs["Geometry"], G.store(G.store(G.store(step_out.outputs["Geometry"], "hg_O", O, "FLOAT_VECTOR"),
                                                      "hg_frame", scene_frame, "FLOAT"), "hg_jump", jump, "FLOAT"))
    result = sim_out.outputs["Geometry"]

    # the game's write-back frame: X from the anchor to the point, Y from an
    # "up" vector made perpendicular (Z = X x up, Y = Z x X). Up is the plane
    # normal for panels, the direction to the partner times the side sign
    # for aprons, and for the other chains the previous bone's written Y (a
    # chain start: its own animated Y), so it is built in node order
    wb_in, wb_out = G.repeat(G.store(result, "hg_wy", Y, "FLOAT_VECTOR"),
                             G.math("ADD", G.stat_max(result, G.attr("hg_order", "INT")), 1.0), counter="Order")
    wcur = wb_in.outputs["Geometry"]
    worder = wb_in.outputs["Order"]
    prev_index = G.attr("hg_prev", "INT")
    start = G.attr("hg_start", "BOOLEAN")
    wanchor = G.switch("VECTOR", start, G.sample(wcur, position, prev_index, "FLOAT_VECTOR"), O)
    aim = G.sub(position, wanchor)
    up = G.switch("VECTOR", start, G.sample(wcur, G.attr("hg_wy", "FLOAT_VECTOR"), prev_index, "FLOAT_VECTOR"), Y)
    up = G.switch("VECTOR", G.boolean("OR", is_type["skirt"], is_type["sheet"]), up, normal)
    up = G.switch("VECTOR", is_type["apron"], up,
                  G.scale(G.sub(G.sample(wcur, position, G.attr("hg_partner", "INT"), "FLOAT_VECTOR"), position),
                          G.attr("hg_side", "FLOAT")))
    written = G.norm(G.cross(G.cross(aim, up), aim))
    written = G.switch("VECTOR", is_type["jiggle"], written, Y)        # chests keep their animated axes
    G.put(wb_out.inputs["Geometry"], G.store(wcur, "hg_wy", written, "FLOAT_VECTOR",
                                             selection=G.int_eq(G.attr("hg_order", "INT"), worder)))
    G.put(wb_out.inputs["Order"], G.math("ADD", worder, 1.0))
    framed = wb_out.outputs["Geometry"]

    # hand the simulated positions and written Y axes back to the input mesh
    # (keeps its vertex groups)
    out = G.set_position(gin.outputs["Geometry"], G.sample(framed, position, index, "FLOAT_VECTOR"))
    out = G.store(out, "hg_wy", G.sample(framed, G.attr("hg_wy", "FLOAT_VECTOR"), index, "FLOAT_VECTOR"), "FLOAT_VECTOR")
    G.put(gout.inputs["Geometry"], out)
    return nt


def _physics_up_group():
    """Up points for the bones' roll: each node point moved one game unit
    along its written Y axis (read from the evaluated proxy)."""
    nt = bpy.data.node_groups.get(PHYSICS_UP_GROUP)
    if nt is not None:
        return nt
    nt = bpy.data.node_groups.new(PHYSICS_UP_GROUP, "GeometryNodeTree")
    nt.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    nt.interface.new_socket("Proxy", in_out="INPUT", socket_type="NodeSocketObject")
    nt.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    G = _Graph(nt)
    gin, gout = G.node("NodeGroupInput"), G.node("NodeGroupOutput")
    position = G.node("GeometryNodeInputPosition").outputs[0]
    index = G.node("GeometryNodeInputIndex").outputs[0]
    info = G.node("GeometryNodeObjectInfo", transform_space="ORIGINAL")
    G.put(info.inputs["Object"], gin.outputs["Proxy"])
    proxy = info.outputs["Geometry"]
    point = G.sample(proxy, position, index, "FLOAT_VECTOR")
    axis = G.sample(proxy, G.attr("hg_wy", "FLOAT_VECTOR"), index, "FLOAT_VECTOR")
    G.put(gout.inputs["Geometry"], G.set_position(gin.outputs["Geometry"], G.add(point, G.scale(axis, UNIT))))
    return nt


def set_gn_input(obj, mod, identifier: str, value, frame=None) -> str:
    """Set (and optionally key) a Geometry Nodes modifier input; returns its
    data path. Blender 5 exposes them as properties.inputs.<id>.value, 4.x
    as ID properties."""
    props = getattr(mod, "properties", None)
    if props is not None and hasattr(props, "inputs"):
        item = getattr(props.inputs, identifier)
        item.value = value
        path = item.path_from_id("value")
    else:
        mod[identifier] = value
        path = f'modifiers["{mod.name}"]["{identifier}"]'
    if frame is not None:
        obj.keyframe_insert(path, frame=frame)
    return path


def _hook(obj, arm, bone: str, group: str) -> None:
    mod = obj.modifiers.new(f"hook {bone}", "HOOK")
    mod.object = arm
    mod.subtarget = bone
    mod.vertex_group = group
    mod.matrix_inverse = arm.data.bones[bone].matrix_local.inverted()   # bind pose = no offset


def _helper_object(scene, arm, name: str, points: list, edges=()):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(p) for p in points], list(edges), [])
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    obj.parent = arm
    obj.display_type = "WIRE"
    obj.hide_render = True
    return obj


def _set_attributes(mesh, values: dict) -> None:
    for name, (dtype, data) in values.items():
        attr = mesh.attributes.new(name, dtype, "POINT")
        attr.data.foreach_set("vector" if dtype == "FLOAT_VECTOR" else "value",
                              [x for v in data for x in v] if dtype == "FLOAT_VECTOR" else data)


def add_physics(scene, rigs: list, cuts: list, report: list, event: str = "") -> None:
    tree = None
    for arm, _meshes, model in rigs:
        cls = PHYSICS_CLASSES.get(base_name(arm.name).split("_")[0].upper())
        if cls is None:
            continue
        bind = _game_frames(read_gltf_json(model))
        bones = arm.data.bones
        name = {i: f[0] for i, f in bind.items() if f[0] in bones}
        nodes, colliders = [], []
        for ci, con in enumerate(GAME_PHYSICS[cls]):
            local = {}
            for j, (kind, bone, prev, ln, k, cone, partner, weight, ref, plane, side) in enumerate(con["nodes"]):
                if bone not in name or (prev >= 0 and prev not in local) or (ref >= 0 and ref not in name):
                    continue
                if kind == "hair_push" and PUSH_BONE not in name:
                    continue
                local[j] = len(nodes)
                nodes.append({"kind": kind, "bone": bone, "prev": prev, "len": ln * UNIT, "k": k, "cone": cone,
                              "partner": partner, "weight": weight, "plane": plane or (0.0, 0.0, 0.0), "side": side,
                              "ref": ref if ref >= 0 else (PUSH_BONE if kind == "hair_push" else bone),
                              "cont": ci, "order": j, "grav": con["gravity"] * UNIT, "damp": con["damping"]})
            for n in nodes:
                if n["cont"] == ci:
                    n["prev"] = local.get(n["prev"], -1) if n["prev"] >= 0 else -1
                    n["partner"] = local.get(n["partner"], -1)
            for col in con["colliders"]:
                if col[0] == "sphere":
                    _s, bone, off, r = col
                    bone2, off2 = bone, off
                else:
                    _s, bone, off, bone2, off2, r = col
                if bone in name and bone2 in name:
                    colliders.append((bone, off, bone2, off2, r * UNIT, ci, col[0] == "capsule"))
        if not nodes:
            continue
        driven = {name[n["bone"]] for n in nodes}

        def hook_bone(index):                 # the nearest bone above that the simulation does not move
            b = bones[name[index]]
            while b is not None and b.name in driven:
                b = b.parent
            return b.name if b is not None else None

        def at(index, offset=(0.0, 0.0, 0.0)):
            _n, origin, axes = bind[index]
            return origin + sum((axes[i] * offset[i] * UNIT for i in range(3)), origin * 0.0)

        # bone frames (origin + one game unit along each axis), hooked to the body
        frame_of, fpoints, fhooks = {}, [], []
        reseed = RESEED.get(cls, {})
        for n in nodes:
            mode, bone, sign = reseed.get(n["cont"], (0, n["bone"], 0.0))
            if mode and bone not in name:
                mode, bone = 0, n["bone"]
            if sign == 0.0 and mode == 1:            # robe: first six nodes up the bone's Z, the rest down
                sign = 1.0 if n["order"] < 6 else -1.0
            n["rs"], n["rs_bone"], n["rs_sign"] = mode, bone, sign
        for index in sorted({n["bone"] for n in nodes} | {n["ref"] for n in nodes} | {n["rs_bone"] for n in nodes}):
            frame_of[index] = len(fpoints)
            fpoints += [at(index)] + [at(index, tuple(1.0 if i == k else 0.0 for i in range(3))) for k in range(3)]
            fhooks += [hook_bone(index)] * 4
        cpoints, chooks = [], []
        for bone, off, bone2, off2, _r, _c, _cap in colliders:
            cpoints += [at(bone, off), at(bone2, off2)]
            chooks += [hook_bone(bone), hook_bone(bone2)]
        if None in fhooks or None in chooks:
            report.append(f"physics: {arm.name}: skipped (no static bone to hook to)")
            continue

        # turn the driven bones' rest frame to the game's (Blender Y = game X,
        # the axis the write-back aims at the node; Blender Z = game Y, the
        # axis it sets the roll with); assembled scene only, nothing animates
        # these bones so the mesh does not move
        view_layer = bpy.context.view_layer
        active = view_layer.objects.active
        arm.hide_set(False)
        view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode="EDIT")
        for n in nodes:
            if n["kind"] != "jiggle":
                eb = arm.data.edit_bones[name[n["bone"]]]
                eb.use_connect = False
                eb.tail = eb.head + bind[n["bone"]][2][0] * max(eb.length, 0.02)
                eb.align_roll(bind[n["bone"]][2][1])
        bpy.ops.object.mode_set(mode="OBJECT")
        view_layer.objects.active = active
        bones = arm.data.bones

        frames = _helper_object(scene, arm, f"{arm.name}_physics_frames", fpoints)
        for bone in sorted(set(fhooks)):
            vg = frames.vertex_groups.new(name=f"hook {bone}")
            vg.add([i for i, h in enumerate(fhooks) if h == bone], 1.0, "REPLACE")
            _hook(frames, arm, bone, vg.name)
        for index, base in frame_of.items():
            frames.vertex_groups.new(name=f"origin {name[index]}").add([base], 1.0, "REPLACE")
        frames.hide_set(True)
        coll = _helper_object(scene, arm, f"{arm.name}_colliders", cpoints,
                              [(2 * i, 2 * i + 1) for i in range(len(colliders))])
        _set_attributes(coll.data, {
            "hg_radius": ("FLOAT", [c[4] for c in colliders for _ in (0, 1)]),
            "hg_cont": ("INT", [c[5] for c in colliders for _ in (0, 1)]),
            "hg_capsule": ("BOOLEAN", [c[6] for c in colliders for _ in (0, 1)])})
        for bone in sorted(set(chooks)):
            vg = coll.vertex_groups.new(name=f"hook {bone}")
            vg.add([i for i, h in enumerate(chooks) if h == bone], 1.0, "REPLACE")
            _hook(coll, arm, bone, vg.name)

        seeds = [bind[n["bone"]][1] + bind[n["bone"]][2][0] * (0.0 if n["kind"] == "jiggle" else n["len"])
                 for n in nodes]
        proxy = _helper_object(scene, arm, f"{arm.name}_physics", seeds,
                               [(n["prev"], i) for i, n in enumerate(nodes) if n["prev"] >= 0])
        _set_attributes(proxy.data, {
            "hg_type": ("INT", [NODE_TYPES[n["kind"]] for n in nodes]),
            "hg_order": ("INT", [n["order"] for n in nodes]),
            "hg_cont": ("INT", [n["cont"] for n in nodes]),
            "hg_start": ("BOOLEAN", [n["prev"] < 0 for n in nodes]),
            "hg_prev": ("INT", [n["prev"] if n["prev"] >= 0 else i for i, n in enumerate(nodes)]),
            "hg_partner": ("INT", [n["partner"] if n["partner"] >= 0 else i for i, n in enumerate(nodes)]),
            "hg_has_partner": ("BOOLEAN", [n["partner"] >= 0 for n in nodes]),
            "hg_len": ("FLOAT", [n["len"] for n in nodes]),
            "hg_k": ("FLOAT", [n["k"] for n in nodes]),
            "hg_cone": ("FLOAT", [n["cone"] for n in nodes]),
            "hg_weight": ("FLOAT", [n["weight"] for n in nodes]),
            "hg_grav": ("FLOAT", [n["grav"] for n in nodes]),
            "hg_damp": ("FLOAT", [n["damp"] for n in nodes]),
            "hg_strength": ("FLOAT", [PUSH_STRENGTH.get(n["kind"], 0.0) for n in nodes]),
            "hg_plane": ("FLOAT_VECTOR", [n["plane"] for n in nodes]),
            "hg_side": ("FLOAT", [n["side"] for n in nodes]),
            "hg_floor": ("BOOLEAN", [n["cont"] == HAIR_FLOOR_CONTAINER.get(cls, -1)
                                     and n["kind"] in ("hair", "hair_push") for n in nodes]),
            "hg_f": ("INT", [frame_of[n["bone"]] for n in nodes]),
            "hg_r": ("INT", [frame_of[n["ref"]] for n in nodes]),
            "hg_q": ("INT", [frame_of[n["rs_bone"]] for n in nodes]),
            "hg_rs": ("INT", [n["rs"] for n in nodes]),
            "hg_rs_sign": ("FLOAT", [n["rs_sign"] for n in nodes])})
        for i, n in enumerate(nodes):
            proxy.vertex_groups.new(name=f"tip {name[n['bone']]}").add([i], 1.0, "REPLACE")
        tree = tree or _physics_node_group()
        mod = proxy.modifiers.new("HG Physics", "NODES")
        mod.node_group = tree
        ids = {item.name: item.identifier for item in tree.interface.items_tree
               if getattr(item, "in_out", None) == "INPUT"}
        set_gn_input(proxy, mod, ids["Frames"], frames)
        # up points (each node point plus its written Y axis) for the bones' roll
        upper = _helper_object(scene, arm, f"{arm.name}_physics_up", seeds)
        for i, n in enumerate(nodes):
            upper.vertex_groups.new(name=f"up {name[n['bone']]}").add([i], 1.0, "REPLACE")
        up_mod = upper.modifiers.new("HG Physics Up", "NODES")
        up_mod.node_group = _physics_up_group()
        up_ids = {item.name: item.identifier for item in up_mod.node_group.interface.items_tree
                  if getattr(item, "in_out", None) == "INPUT"}
        set_gn_input(upper, up_mod, up_ids["Proxy"], proxy)
        upper.hide_set(True)
        set_gn_input(proxy, mod, ids["Colliders"], coll)
        # the game's reset (partial re-seed + settling steps) at every cut
        # (actors get their cut's motion); an actor that appears or is moved
        # between cuts restarts fully through the Teleport test
        starts = sorted({0} | {c["start_frame"] for c in cuts})
        for f in starts:
            for frame, value in ((f, True), (f + 1, False)):
                path = set_gn_input(proxy, mod, ids["Reset"], value, frame)
        constant_keys(proxy, path)
        # scripted hair floor (world height; the actors' armatures sit at the origin)
        floor_keys = EVENT_HAIR_FLOOR.get(event, []) if cls in HAIR_FLOOR_CONTAINER else []
        floor_keys = [(0, None)] + floor_keys if floor_keys else []
        for frame, height in floor_keys:
            on_path = set_gn_input(proxy, mod, ids["Hair Floor On"], height is not None, frame)
            if height is not None:
                height_path = set_gn_input(proxy, mod, ids["Hair Floor"], height * UNIT, frame)
                constant_keys(proxy, height_path)
            constant_keys(proxy, on_path)

        # write-back: origin onto the point the node hangs from (a chain
        # start: its own animated origin), X axis aimed at its point, Y axis
        # towards the written up direction; chest bones are moved onto
        # their point
        for i, n in enumerate(nodes):
            pb = arm.pose.bones[name[n["bone"]]]
            if n["kind"] == "jiggle":
                con = pb.constraints.new("COPY_LOCATION")
                con.name = "HG Physics"
                con.target, con.subtarget = proxy, f"tip {name[n['bone']]}"
                continue
            con = pb.constraints.new("COPY_LOCATION")
            con.name = "HG Physics origin"
            if n["prev"] >= 0 and n["kind"] not in ("cone",):
                con.target, con.subtarget = proxy, f"tip {name[nodes[n['prev']]['bone']]}"
            else:
                con.target, con.subtarget = frames, f"origin {name[n['bone']]}"
            con = pb.constraints.new("DAMPED_TRACK")
            con.name = "HG Physics"
            con.target, con.subtarget = proxy, f"tip {name[n['bone']]}"
            con.track_axis = "TRACK_Y"           # the re-aimed rest direction (game X)
            con = pb.constraints.new("LOCKED_TRACK")
            con.name = "HG Physics roll"
            con.target, con.subtarget = upper, f"up {name[n['bone']]}"
            con.lock_axis, con.track_axis = "LOCK_Y", "TRACK_Z"   # Z (game Y) towards the written Y
        counts = {}
        for n in nodes:
            counts[n["kind"]] = counts.get(n["kind"], 0) + 1
        report.append(f"physics: {arm.name}: game class {cls}, {len(nodes)} nodes "
                      f"({', '.join(f'{v} {k}' for k, v in sorted(counts.items()))}), {len(colliders)} colliders "
                      f"(live; modifier 'HG Physics' on {proxy.name})")


def bake_physics(scene, report: list) -> None:
    """Bake every HG Physics simulation over the scene's frame range and
    pack it into the .blend (Free the bake in the modifier's Bake panel to
    go back to the live simulation)."""
    import time
    t0 = time.time()
    done = 0
    for obj in scene.objects:
        mod = obj.modifiers.get("HG Physics") if obj.type == "MESH" else None
        if mod is None or mod.type != "NODES":
            continue
        mod.bake_target = "PACKED"
        for bake in mod.bakes:
            bake.bake_target = "INHERIT"
            bpy.ops.object.geometry_node_bake_single(session_uid=obj.session_uid, modifier_name=mod.name,
                                                     bake_id=bake.bake_id)
            done += 1
    report.append(f"physics: baked {done} simulation(s), frames {scene.frame_start}-{scene.frame_end}, "
                  f"in {time.time() - t0:.0f} s (packed)")


def hide(obj) -> None:
    obj.hide_set(True)
    obj.hide_render = True


def main(argv: list[str], from_cli: bool) -> None:
    opts = {"--root": None, "--save": None, "--room-anims": "ambient", "--resolution": "1440x1080",
            "--view-transform": "Standard"}
    flags = set()
    args = []
    i = 0
    while i < len(argv):
        if argv[i] in ("--keep-current", "--no-sound", "--no-movie", "--no-cut-lighting", "--shadows", "--blended",
                       "--no-physics", "--bake-physics"):
            flags.add(argv[i])
        elif argv[i] in opts:
            opts[argv[i]] = argv[i + 1]
            i += 1
        else:
            args.append(argv[i])
        i += 1
    cutscene = _find(args[0] if args else CUTSCENE)
    if not cutscene.is_file():
        raise SystemExit(f"cutscene file not found: {cutscene}")
    root = Path(opts["--root"]).resolve() if opts["--root"] else cutscene.parents[2]

    # From the Text Editor, never throw away the open file.
    if from_cli and "--keep-current" not in flags:
        bpy.ops.wm.read_homefile(use_empty=True)
    scene = bpy.context.scene
    # Keys are stored in seconds; the importer converts them with the scene
    # frame rate, so set 30 fps BEFORE importing to land on whole frames.
    scene.render.fps, scene.render.fps_base = FPS, 1.0

    # 1. the cutscene: camera, actor armatures, blend-shape carriers, room/prop stand-ins
    cut_objs = import_gltf(cutscene)
    camera = next(o for o in cut_objs if o.type == "CAMERA")
    actors = [o for o in cut_objs if "model_file" in o.keys()]
    room_refs = [o for o in cut_objs if "room_file" in o.keys()]
    report = []
    actor_objs, room_objs, rigs = [], [], []

    # 2. actors: import each character, hand it the cutscene action
    for actor in actors:
        model = root / actor["model_file"]
        if not model.is_file():
            report.append(f"{actor.name}: missing {model}")
            continue
        objs = import_gltf(model)
        actor_objs += objs
        arm = next((o for o in objs if o.type == "ARMATURE"), None)
        if arm is None:
            report.append(f"{actor.name}: no armature in {model.name}")
            continue
        # The cutscene armature's slot carries the bone keys and the object
        # scale keys that hide the actor while it is off stage.
        copy_animation(actor, arm)
        rigs.append((arm, [o for o in objs if o.type == "MESH"], model))
        carrier = next((c for c in actor.children if c.type == "MESH" and c.data.shape_keys), None)
        if carrier is not None and carrier.data.shape_keys.animation_data:
            names = {k.name for k in carrier.data.shape_keys.key_blocks[1:]}
            for mesh in (o for o in objs if o.type == "MESH" and o.data.shape_keys):
                if names & {k.name for k in mesh.data.shape_keys.key_blocks}:
                    copy_animation(carrier.data.shape_keys, mesh.data.shape_keys)
        hidden = set(actor.get("hidden_parts", []))
        for o in objs:
            if base_name(o.name) in hidden:
                hide(o)
        variant = actor.get("texture_variant", "default")
        if variant != "default" and apply_variant(objs, variant):
            report.append(f"{arm.name}: material variant {variant}")
        hide(actor)
        for c in actor.children_recursive:
            hide(c)
        scale_to_visibility(arm, [o for o in objs if o.type == "MESH"])
        report.append(f"{actor.name}: {model.relative_to(root)} -> {arm.name}")

    # 3. rooms: import, park under the stand-in (it carries the per-cut
    #    visibility when an event spans several rooms), move the props
    for ref in room_refs:
        room_file = root / ref["room_file"]
        if not room_file.is_file():
            report.append(f"{ref.name}: missing {room_file}")
            continue
        objs = import_gltf(room_file)
        room_objs += objs
        room_root = next(o for o in objs if o.parent is None and base_name(o.name) == base_name(ref.name))
        room_root.parent = ref
        by_name = {base_name(o.name): o for o in objs}
        for o in objs:
            if o.type == "CAMERA":          # gameplay cameras of the room
                hide(o)
            datas = [o]
            if o.type == "MESH" and o.data.shape_keys:
                datas.append(o.data.shape_keys)
            for d in datas:
                ad = d.animation_data
                if ad is None or ad.action is None:
                    continue
                ambient = base_name(ad.action.name).endswith(("_loop", "_spin"))
                if opts["--room-anims"] == "none" or (opts["--room-anims"] == "ambient" and not ambient):
                    ad.action = None        # doors, curtains, camera rails: gameplay-driven
                elif ambient:
                    for fc in fcurves_of(d):
                        if not any(m.type == "CYCLES" for m in fc.modifiers):
                            fc.modifiers.new("CYCLES")
        for helper in ref.children:
            target = by_name.get(base_name(helper.name))
            if target is not None and copy_animation(helper, target):
                hide(helper)
                report.append(f"prop -> {target.name}")
            elif target is None:
                report.append(f"{helper.name}: no matching object in {room_file.name}")
        report.append(f"room {ref.name}: {room_file.relative_to(root)}")

    # 4. camera, frame range, aspect
    scene.camera = camera
    action = camera.animation_data.action if camera.animation_data else None
    end = math.ceil(action.frame_range[1]) if action else scene.frame_end
    scene.frame_start, scene.frame_end = 0, end
    w, h = (int(v) for v in opts["--resolution"].lower().split("x"))
    scene.render.resolution_x, scene.render.resolution_y = w, h
    # 4b. cloth and hair: a live spring simulation driving the X bones
    if "--no-physics" not in flags:
        anim = (read_gltf_json(cutscene).get("animations") or [{}])[0].get("extras", {})
        add_physics(scene, rigs, anim.get("cuts", []), report, cutscene.stem.upper())

    # 5. per-cut character lighting (the rooms themselves are pre-lit)
    if "--no-cut-lighting" not in flags:
        apply_cut_lighting(scene, camera, read_gltf_json(cutscene), room_refs, root, report)

    # 6. match the game's rendering: no light shadows, depth-sorted translucency,
    #    colours shown as stored (the game has no tone mapping)
    match_game_rendering(actor_objs, room_objs, list(scene.objects), flags, report)
    scene.view_settings.view_transform = opts["--view-transform"]
    scene.view_settings.look = "None"

    # 7. the event movie: pre-rendered inserts or effect overlays
    movie = cutscene.with_suffix(".mp4")
    if "--no-movie" not in flags:
        add_movie_layer(scene, camera, read_gltf_json(cutscene), movie, report)

    # 8. soundtrack in the sequencer at frame 0
    sound = cutscene.with_suffix(".wav")
    if "--no-sound" not in flags and sound.is_file():
        seq = scene.sequence_editor or scene.sequence_editor_create()
        strips = seq.strips if hasattr(seq, "strips") else seq.sequences   # renamed in Blender 4.4
        strips.new_sound(name=sound.stem, filepath=str(sound), channel=1, frame_start=0)
        scene.sync_mode = "AUDIO_SYNC"
        report.append(f"sound: {sound.name} at frame 0 (playback synced to audio)")
    # 9. optionally bake the simulations (packed into the .blend), so any
    #    frame shows what playing from the start gives
    if "--bake-physics" in flags and "--no-physics" not in flags:
        bake_physics(scene, report)
    scene.frame_set(0)
    for line in report:
        print("[assemble]", line)
    if opts["--save"]:
        bpy.ops.wm.save_as_mainfile(filepath=str(Path(opts["--save"]).resolve()))


if "--" in sys.argv:
    main(sys.argv[sys.argv.index("--") + 1:], from_cli=True)
else:
    main([], from_cli=False)
