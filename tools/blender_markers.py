"""Show exported frame markers and foot contacts in Blender.

Blender's glTF importer drops animation extras, where the character exports
keep the per-clip MRK markers and foot contact intervals. This script reads
them straight from the file and adds the markers as pose markers to the
imported actions (visible in the Action Editor), the foot contacts as stepped
0/1 custom-property curves ``contact_<bone>`` on the armature (one set per
action, e.g. to drive the influence of a foot IK constraint), and turns a
cutscene's event marks (root node extras) into scene timeline markers.

    blender --python tools/blender_markers.py -- FILE.glb [FILE2.glb ...] [--import]

Without --import the files must already be imported in the open scene (run
it from the Text Editor by setting FILES below). Marker names are the bit
values in hex, e.g. ``0x8``; cut marks (bit 0) are named ``cut``.
"""
import json
import re
import struct
import sys
from pathlib import Path

import bpy

FILES: list[str] = []      # for use from the Text Editor


def read_gltf_json(path: Path) -> dict:
    data = path.read_bytes()
    if data[:4] == b"glTF":
        length, kind = struct.unpack_from("<II", data, 12)
        return json.loads(data[20:20 + length])
    return json.loads(data)


def find_action(name: str):
    """Imported actions keep the glTF animation name (plus .001... on clashes);
    prefer the most recently imported one."""
    hits = [a for a in bpy.data.actions if re.sub(r"\.\d{3}$", "", a.name) == name]
    return hits[-1] if hits else None


def add_clip_markers(doc: dict) -> int:
    count = 0
    for anim in doc.get("animations", []):
        marks = (anim.get("extras") or {}).get("markers")
        action = find_action(anim.get("name", "")) if marks else None
        if action is None:
            continue
        for marker in list(action.pose_markers):
            if marker.name.startswith("0x"):
                action.pose_markers.remove(marker)
        for frame, bits in marks:
            action.pose_markers.new(f"0x{bits:X}").frame = frame
            count += 1
    return count


def contact_armature(bones: set[str]):
    """The most recently imported armature that has all these bones."""
    hits = [o for o in bpy.context.scene.objects
            if o.type == "ARMATURE" and bones <= {b.name for b in o.data.bones}]
    return hits[-1] if hits else None


def add_contact_curves(doc: dict) -> int:
    clips = [(a, (a.get("extras") or {}).get("foot_contact")) for a in doc.get("animations", [])]
    clips = [(a, c) for a, c in clips if c]
    if not clips:
        return 0
    arm = contact_armature({bone for _a, c in clips for bone in c if not bone.startswith("-")})
    if arm is None:
        return 0
    ad = arm.animation_data or arm.animation_data_create()
    previous = (ad.action, ad.action_slot)
    count = 0
    names = sorted({bone for _a, c in clips for bone in c})
    for bone in names:
        arm[f"contact_{bone}"] = 0.0
    for anim, contact in clips:
        action = find_action(anim.get("name", ""))
        if action is None:
            continue
        ad.action = action                       # fcurve_ensure_for_datablock uses the assigned slot
        if action.slots:
            ad.action_slot = action.slots[0]
        frames = (anim.get("extras") or {}).get("frames", 1)
        for bone in names:
            path = f'["contact_{bone}"]'
            fc = action.fcurve_ensure_for_datablock(arm, path)
            fc.keyframe_points.clear()
            keys = {0: 0.0}
            for first, last in contact.get(bone, []):
                keys[first] = 1.0
                if last + 1 < frames:
                    keys[last + 1] = 0.0
            for frame, value in sorted(keys.items()):
                kp = fc.keyframe_points.insert(frame, value)
                kp.interpolation = "CONSTANT"
        count += 1
    ad.action, ad.action_slot = previous[0], previous[1] if previous[0] else None
    return count


def add_event_marks(doc: dict) -> int:
    scene = bpy.context.scene
    count = 0
    for node in doc.get("nodes", []):
        marks = (node.get("extras") or {}).get("marks")
        if not marks:
            continue
        for frame, bits in marks:
            name = "cut" if bits == 1 else (f"cut+0x{bits & ~1:X}" if bits & 1 else f"0x{bits:X}")
            scene.timeline_markers.new(name, frame=frame)
            count += 1
    return count


def main(argv: list[str]) -> None:
    do_import = "--import" in argv
    files = [Path(a) for a in argv if not a.startswith("--")] or [Path(f) for f in FILES]
    if do_import:
        bpy.context.scene.render.fps = 30
    for path in files:
        if do_import:
            bpy.ops.import_scene.gltf(filepath=str(path))
        doc = read_gltf_json(path)
        print(f"[markers] {path.name}: {add_clip_markers(doc)} clip markers, {add_event_marks(doc)} timeline marks, "
              f"{add_contact_curves(doc)} actions with foot contact curves")


main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
