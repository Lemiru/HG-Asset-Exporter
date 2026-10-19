"""Import a glTF/GLB into Blender (headless), report its contents as JSON and
optionally render textured previews.

    blender -b --factory-startup -P tools/blender_check.py -- FILE.glb OUT.json [--render OUT.png]
        [--frame N] [--action NAME] [--view front|side|top|iso|camera] [--size 768]
"""
import json
import math
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
src, report_path = argv[0], argv[1]
opts = {"--also": None, "--eye": None, "--target": None, "--fov": "50", "--engine": "workbench", "--render": None, "--frame": None, "--action": None, "--view": "front", "--size": "768",
        "--shading": "texture"}
i = 2
while i < len(argv):
    opts[argv[i]] = argv[i + 1]
    i += 2

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.context.scene.render.fps = 30
result = {"file": src}
try:
    bpy.ops.import_scene.gltf(filepath=src)
    main_objs = set(bpy.context.scene.objects)
    for extra in (opts["--also"] or "").split(";"):
        if extra:
            bpy.ops.import_scene.gltf(filepath=extra)
except Exception as exc:  # pragma: no cover - reported to caller
    result["import_error"] = str(exc)
    json.dump(result, open(report_path, "w"), indent=1)
    raise SystemExit(1)

objs = list(bpy.context.scene.objects)
shapes = {pb.custom_shape for a in objs if a.type == "ARMATURE" for pb in a.pose.bones if pb.custom_shape}
meshes = [o for o in objs if o.type == "MESH" and o not in shapes]
arms = [o for o in objs if o.type == "ARMATURE"]
result["objects"] = len(objs)
result["meshes"] = [{"name": o.name, "verts": len(o.data.vertices), "polys": len(o.data.polygons),
                     "materials": [m.name for m in o.data.materials if m],
                     "shape_keys": len(o.data.shape_keys.key_blocks) if o.data.shape_keys else 0,
                     "vertex_groups": len(o.vertex_groups)} for o in meshes]
result["armatures"] = [{"name": a.name, "bones": len(a.data.bones)} for a in arms]
result["actions"] = sorted(a.name for a in bpy.data.actions)
result["cameras"] = [o.name for o in objs if o.type == "CAMERA"]
result["images"] = len(bpy.data.images)

if opts["--render"]:
    scene = bpy.context.scene
    if opts["--action"] and arms:
        act = bpy.data.actions.get(opts["--action"])
        if act:
            arms[0].animation_data_create()
            arms[0].animation_data.action = act
            result["active_action"] = act.name
    if opts["--frame"] is not None:
        scene.frame_set(int(opts["--frame"]))
        for a in bpy.data.actions:
            pass
    bpy.context.view_layer.update()
    pts = []
    for o in meshes:
        dg = bpy.context.evaluated_depsgraph_get()
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        pts += [ev.matrix_world @ v.co for v in me.vertices]
        ev.to_mesh_clear()
    if pts:
        lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
        hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    else:
        lo, hi = Vector((-1, -1, -1)), Vector((1, 1, 1))
    result["bounds"] = [list(lo), list(hi)]
    center = (lo + hi) / 2
    size = max((hi - lo).length, 1e-3)
    view = opts["--view"]
    cam_obj = None
    if view == "camera":
        cams = [o for o in bpy.context.scene.objects if o.type == "CAMERA"]
        cam_obj = cams[0] if cams else None
    if opts["--eye"]:
        # glTF coordinates (Y up) -> Blender (Z up): (x, y, z) -> (x, -z, y)
        def conv(t):
            x, y, z = (float(v) for v in t.split(","))
            return Vector((x, -z, y))
        cam_data = bpy.data.cameras.new("eye")
        cam_obj = bpy.data.objects.new("eye", cam_data)
        scene.collection.objects.link(cam_obj)
        cam_obj.location = conv(opts["--eye"])
        cam_obj.rotation_euler = (conv(opts["--target"]) - cam_obj.location).to_track_quat("-Z", "Y").to_euler()
        cam_data.sensor_fit = "VERTICAL"
        cam_data.angle_y = math.radians(float(opts["--fov"]))
        cam_data.clip_start = 0.01
        cam_data.clip_end = 1000
        view = "camera"
    if cam_obj is None:
        cam_data = bpy.data.cameras.new("preview")
        cam_obj = bpy.data.objects.new("preview", cam_data)
        scene.collection.objects.link(cam_obj)
        dirs = {"front": Vector((0, -1, 0.15)), "side": Vector((1, 0, 0.15)), "back": Vector((0, 1, 0.15)),
                "top": Vector((0, -0.01, 1)), "iso": Vector((1, -1, 0.8))}
        d = dirs.get(view, dirs["front"]).normalized()
        cam_obj.location = center + d * size * 1.4
        cam_obj.rotation_euler = (center - cam_obj.location).to_track_quat("-Z", "Y").to_euler()
        cam_data.lens = 50
        cam_data.clip_end = size * 10
    scene.camera = cam_obj
    scene.render.engine = "BLENDER_EEVEE" if opts["--engine"] == "eevee" else "BLENDER_WORKBENCH"
    if opts["--engine"] == "eevee":
        try:
            scene.eevee.taa_render_samples = 16
        except AttributeError:
            pass
        scene.view_settings.view_transform = "Standard"
    shading = scene.display.shading
    shading.light = "FLAT" if opts["--shading"] == "flat" else "STUDIO"
    shading.color_type = "TEXTURE"
    shading.show_backface_culling = False
    scene.render.resolution_x = int(opts["--size"])
    scene.render.resolution_y = int(int(opts["--size"]) * (3 / 4 if view == "camera" else 1))
    scene.render.film_transparent = False
    scene.world = bpy.data.worlds.new("w")
    scene.render.filepath = opts["--render"]
    bpy.ops.render.render(write_still=True)
    result["render"] = opts["--render"]

json.dump(result, open(report_path, "w"), indent=1)
