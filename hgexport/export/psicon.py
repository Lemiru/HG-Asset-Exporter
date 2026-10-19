"""Memory-card icon (ICON.SYS + ICONnn.ICO) -> glTF: shape 0 as the mesh, the
other animation shapes as morph targets, the browser loop as weight keys."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from ..formats.psicon import parse_icon, parse_icon_sys
from ..gltf.builder import GltfBuilder
from ..util.png import encode_png

ICON_FPS = 60.0     # browser frame units (assumed: one per 60 Hz field)


def _weight(keys: list[tuple[float, float]], t: float) -> float:
    if not keys or t < keys[0][0] or t > keys[-1][0]:
        return 0.0
    for (t0, w0), (t1, w1) in zip(keys, keys[1:]):
        if t0 <= t <= t1:
            return w0 if t1 == t0 else w0 + (w1 - w0) * (t - t0) / (t1 - t0)
    return keys[-1][1]


def export_icon(ico: Path | str, out: Path | str, scale: float = 1.0) -> dict:
    ico = Path(ico)
    icon = parse_icon(ico)
    sys_file = ico.with_name("ICON.SYS")
    flip = lambda v: (v[0] * scale, -v[1] * scale, -v[2] * scale)      # icon space is Y-down
    b = GltfBuilder("hgexport icon")
    image = b.image(encode_png(128, 128, icon.texture), ico.stem)
    mat = b.material({"name": f"{ico.stem}_material", "doubleSided": True,
                      "pbrMetallicRoughness": {"baseColorTexture": {"index": b.texture(image, b.sampler())},
                                               "metallicFactor": 0.0}})
    base = [flip(p) for p in icon.shapes[0]]
    targets = []
    for shape in icon.shapes[1:]:
        targets.append({"POSITION": b.positions([tuple(a - c for a, c in zip(flip(p), q))
                                                 for p, q in zip(shape, base)])})
    n = len(base)
    prim = {"attributes": {"POSITION": b.positions(base),
                           "NORMAL": b.vec3([(x, -y, -z) for x, y, z in icon.normals]),
                           "TEXCOORD_0": b.vec2(icon.uvs),
                           "COLOR_0": b.colors_u8([(r, g, bl, 255) for r, g, bl, _a in icon.colors])},
            "indices": b.indices(list(range(n))), "mode": 4, "material": mat}
    if targets:
        prim["targets"] = targets
    names = [f"shape{k}" for k in range(1, len(icon.shapes))]
    mesh = b.mesh({"name": ico.stem, "primitives": [prim], "weights": [0.0] * len(targets),
                   "extras": {"targetNames": names}})
    node = b.node({"name": ico.stem, "mesh": mesh}, root=True)
    times = sorted({t for keys in icon.keys.values() for t, _w in keys})
    if targets and times:
        rows = [[_weight(icon.keys.get(s, []), t) for s in range(1, len(icon.shapes))] for t in times]
        b.animation({"name": f"{ico.stem}_loop", "samplers": [b.anim_sampler([t / ICON_FPS for t in times], rows,
                                                                              "WEIGHTS", "LINEAR")],
                     "channels": [{"sampler": 0, "target": {"node": node, "path": "weights"}}],
                     "extras": {"frame_length": icon.frame_length, "speed": icon.speed, "fps": ICON_FPS,
                                "loop": True, "keys": {str(k): v for k, v in icon.keys.items()}}})
    extras = {"source": ico.name, "vertex_color_raw": icon.colors[0] if len({*icon.colors}) == 1 else None}
    if sys_file.is_file():
        extras["icon_sys"] = asdict(parse_icon_sys(sys_file))
    b.doc["asset"]["extras"] = extras
    b.write(Path(out))
    return {"triangles": n // 3, "shapes": len(icon.shapes), "keys": sum(len(v) for v in icon.keys.values())}
