"""Check that an animation-only cutscene drives separately exported models.

    blender -b --factory-startup -P tools/blender_retarget_check.py -- CUTSCENE.glb CHARACTER.glb ACTOR
        EMBEDDED.glb OUT.json [--frames 10,200,400] [--render OUT_%d.png]

Imports the character export and the slim cutscene, assigns the cutscene
actor's armature action and blend-shape action to the character, and compares
world-space bone heads per frame with the embedded cutscene export of the same
event (which carries the bones under ``ACTOR_`` prefixed names). ACTOR is the
actor group name, e.g. ``A01_FIS_000``.
"""
import json
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
cut_path, char_path, actor, embedded_path, out_path = argv[:5]
opts = {"--frames": "10,200,400", "--render": None}
for i in range(5, len(argv), 2):
    opts[argv[i]] = argv[i + 1]
frames = [int(f) for f in opts["--frames"].split(",")]

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.fps = 30


def import_new(path):
    before = set(scene.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    return [o for o in scene.objects if o not in before]


char_objs = import_new(char_path)
cut_objs = import_new(cut_path)
emb_objs = import_new(embedded_path)

char_arm = next(o for o in char_objs if o.type == "ARMATURE")


def chain(o):
    while o is not None:
        yield o
        o = o.parent


cut_arm = next(o for o in cut_objs if o.type == "ARMATURE" and any(p.name.startswith(actor) for p in chain(o)))
emb_arm = next(o for o in emb_objs if o.type == "ARMATURE" and any(b.name.startswith(actor + "_") for b in o.data.bones))
result = {"character_armature": char_arm.name, "cutscene_armature": cut_arm.name, "embedded_armature": emb_arm.name}

# retarget: same bone names, so the action binds directly
char_arm.animation_data_create()
char_arm.animation_data.action = cut_arm.animation_data.action
if cut_arm.animation_data.action_slot if hasattr(cut_arm.animation_data, "action_slot") else False:
    char_arm.animation_data.action_slot = cut_arm.animation_data.action_slot
carrier = next((o for o in cut_objs if o.type == "MESH" and o.name.startswith(actor) and o.data.shape_keys), None)
body = next((o for o in char_objs if o.type == "MESH" and o.data.shape_keys
             and any(k.name.startswith("face_01") for k in o.data.shape_keys.key_blocks)), None)
if carrier and body and carrier.data.shape_keys.animation_data:
    sk = body.data.shape_keys
    sk.animation_data_create()
    sk.animation_data.action = carrier.data.shape_keys.animation_data.action
    if hasattr(sk.animation_data, "action_slot"):
        sk.animation_data.action_slot = carrier.data.shape_keys.animation_data.action_slot
result["blendshapes_retargeted"] = bool(carrier and body)

errors = []
shape_err = []
for f in frames:
    scene.frame_set(f)
    worst = 0.0
    for pb in char_arm.pose.bones:
        other = emb_arm.pose.bones.get(f"{actor}_{pb.name}")
        if other is None:
            continue
        a = char_arm.matrix_world @ pb.head
        b = emb_arm.matrix_world @ other.head
        worst = max(worst, (a - b).length)
    head = char_arm.matrix_world @ char_arm.pose.bones["J00"].head
    hand = max(char_arm.pose.bones, key=lambda pb: pb.name)
    errors.append({"frame": f, "max_bone_error_m": worst, "J00": [round(c, 3) for c in head],
                   hand.name: [round(c, 3) for c in char_arm.matrix_world @ hand.head]})
    if carrier and body:
        emb_mesh = next((o for o in emb_objs if o.type == "MESH" and o.data.shape_keys
                         and any(k.name.startswith("face_01") for k in o.data.shape_keys.key_blocks)
                         and any(p.name.startswith(actor) for p in chain(o))), None)
        if emb_mesh:
            diff = max(abs(k.value - emb_mesh.data.shape_keys.key_blocks[k.name].value)
                       for k in body.data.shape_keys.key_blocks[1:])
            shape_err.append({"frame": f, "max_weight_diff": diff,
                              "active": {k.name: round(k.value, 3) for k in body.data.shape_keys.key_blocks[1:] if k.value > 1e-4}})
result["bone_errors"] = errors
result["shape_errors"] = shape_err
json.dump(result, open(out_path, "w"), indent=1)
