"""Per-part shine ("specular") masks.

The game draws some model parts with lit VU1 programs that add a specular
term (max(R.V, 0)^4 x each light's strength and diffuse term) to the vertex
alpha. Each model part has two bytes in the model object (+0x98 + 2 x part):
a draw mode (4 = lit) and the strength (128 = 1.0). They are not stored in
the model files; every character class writes fixed values in its
"model loaded" routine (Fiona's main outfit: EventHumanModel_Loaded
0x2F7920; Debilitas: DebilitasModel_Loaded; ...). The values below were read
from those routines and checked against live save states (FIN, FIS, FIH,
DB0). The term only goes into the frame's alpha, and the game clears that
alpha after drawing the characters, before the bloom reads it (export/look.py),
so it never shows in normal play; the exports leave it out of the materials
unless asked (``--specular``).

Part numbers count the model's skinned meshes, then its rigid meshes, then
its blend-shape pieces (the drawers' per-part index). A family's numbered
variants and its _200 cutscene model share the _000 layout.
"""
from __future__ import annotations

import math

from ..formats.pck import PckModel, strip_triangles

# family -> {part: strength}; 128 = the game's 1.0
SHINE: dict[str, dict[int, int]] = {
    # Fiona, main outfit (EventHumanModel 0x470540)
    "FIN": {2: 192, 8: 64, 9: 64, 10: 64, 13: 64, 14: 64, 15: 64, 16: 64, 38: 64, 39: 64, 40: 64},
    # Fiona in the sheet (FionaModel 0x470620)
    "FIS": {k: 64 for k in (0, 1, 2, 3, 4, 5, 7, 8, 9, 20, 21, 22)},
    # costumes: Costume2 FIH, Costume3 FIO, Costume6 FIB, Costume7 FIC, Costume8 FIF
    "FIH": {0: 192, **{k: 64 for k in (6, 7, 8, 9, 10, 11, 14, 15, 16, 17, 49, 50, 51)}},
    "FIO": {k: 64 for k in (2, 3, 4, 5, 13, 14, 15, 16, 17, 18, 38, 39, 40, 41)},
    "FIB": {**{k: 64 for k in (0, 1, 2, 11, 12, 26)}, **{k: 128 for k in (3, 4, 5, 6, 7, 8, 9, 10, 24, 27, 28)}},
    "FIC": {k: 64 for k in (8, 9, 10, 11, 12, 13, 36)},
    "FIF": {19: 64},
    # Daniella (DaniellaModel 0x4702D0; 17 and 31 are her glass shard)
    "DNL": {0: 64, 26: 64, 33: 64, 34: 64, 35: 64, 29: 192, 17: 255, 31: 255},
    # Debilitas (DebilitasModel 0x46C0A0)
    "DB0": {**{k: 64 for k in (2, 3, 4, 5, 21, 22, 23)}, **{k: 128 for k in (14, 18, 19, 20)}},
    # Riccardo (RiccardoModel 0x470480, Kind23Model 0x473BD0)
    "RCG": {17: 192, 24: 64, 25: 64, 26: 64},
    "RCT": {k: 64 for k in (0, 1, 22, 23, 24)},
    # Lorenzo (Kind09Model 0x471C20, Lorenzo2Model 0x471CE0, LorenzoModel 0x471DA0)
    "LRM": {2: 64, 3: 64, 4: 64, 5: 192, 22: 192, 25: 64, 26: 64, 27: 64},
    "LRY": {0: 64, 1: 64, 2: 64, 21: 64, 22: 64, 23: 64, 7: 192, 18: 192},
    "LRO": {0: 64, 1: 64, 2: 64, 24: 64, 25: 64, 26: 64, 3: 192, 22: 192},
}

# The game's highlight, (R.V)^4, as GGX: Blinn-Phong exponent 4 x 4 = 16,
# alpha^2 = 2 / (16 + 2), roughness = sqrt(alpha) (= sqrt(1/3) = 0.58).
SHINE_ROUGHNESS = math.sqrt(2.0 / 6.0)


def shine_f0(strength: int) -> float:
    """Reflectance at normal incidence that gives the game's highlight peak.
    At its peak the game adds strength/128 x the light to a white surface's
    diffuse light x 1; GGX at that roughness (alpha = 1/3) peaks at
    D F0 / 4 = 9 F0 / (4 pi) against Lambert's 1 / pi, so F0 = 4/9 x strength."""
    return min(0.95, 4.0 / 9.0 * strength / 128.0)


def f0_ior(f0: float) -> float:
    """Index of refraction with that reflectance, ((n - 1) / (n + 1))^2 = F0."""
    r = math.sqrt(f0)
    return (1.0 + r) / (1.0 - r)


def part_shine(model: PckModel) -> dict[tuple[str, int], int]:
    """(kind, index) -> strength for the model's shiny parts, kind "skinned",
    "rigid" or "morph"; empty when the family has no table."""
    table = SHINE.get(model.name.split("_")[0].upper())
    if not table:
        return {}
    skinned = sorted(m.index for m in model.meshes if m.kind == "skinned")
    rigid = sorted(m.index for m in model.meshes if m.kind == "rigid")
    morphs = sorted(d.index for d in model.morphs)
    parts = [("skinned", i) for i in skinned] + [("rigid", i) for i in rigid] + [("morph", i) for i in morphs]
    return {parts[k]: v for k, v in table.items() if k < len(parts)}


def _paint(mask: bytearray, w: int, h: int, uvs, value: int, seen: bytearray | None = None) -> None:
    """Fill one UV triangle (grown by one texel, as bilinear filtering reads)
    with ``value`` (the larger value wins)."""
    pts = [(u * w - 0.5, v * h - 0.5) for u, v in uvs]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    area = (pts[1][0] - pts[0][0]) * (pts[2][1] - pts[0][1]) - (pts[1][1] - pts[0][1]) * (pts[2][0] - pts[0][0])
    sign = 1.0 if area >= 0 else -1.0
    edges = [(a, b, abs(b[0] - a[0]) + abs(b[1] - a[1])) for a, b in ((pts[0], pts[1]), (pts[1], pts[2]), (pts[2], pts[0]))]
    for y in range(max(0, math.ceil(min(ys) - 1)), min(h - 1, math.floor(max(ys) + 1)) + 1):
        for x in range(max(0, math.ceil(min(xs) - 1)), min(w - 1, math.floor(max(xs) + 1)) + 1):
            if all(sign * ((b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])) >= -d for a, b, d in edges):
                k = y * w + x
                if seen is not None:
                    seen[k] = 1
                elif value > mask[k]:
                    mask[k] = value


def shine_masks(model: PckModel, images: list) -> tuple[dict[int, bytes], dict[int, int]]:
    """Grey masks per texture component (texel = the strength of the shiny
    part using it, 128 = the game's 1.0), and per component the number of
    shiny texels that unshiny parts also use (they would shine too)."""
    shiny = part_shine(model)
    if not shiny:
        return {}, {}
    masks: dict[int, bytearray] = {}
    plain: dict[int, bytearray] = {}
    sources = [(m.kind, m.index, m.material, m.uvs, m.restart) for m in model.meshes] + \
              [("morph", d.index, d.material, d.uvs, d.restart) for d in model.morphs]
    for kind, index, material, uvs, restart in sources:
        if not 0 <= material < len(images):
            continue
        img = images[material]
        w, h = img.width, img.height
        value = shiny.get((kind, index))
        target = masks if value else plain
        buf = target.setdefault(material, bytearray(w * h))
        for tri in strip_triangles(restart):
            if value:
                _paint(buf, w, h, [uvs[v] for v in tri], value)
            else:
                _paint(buf, w, h, [uvs[v] for v in tri], 0, seen=buf)
    overlap = {c: sum(1 for a, b in zip(m, plain[c]) if a and b) for c, m in masks.items() if c in plain}
    return {c: bytes(m) for c, m in masks.items()}, overlap


def export_shine_masks(pck, out_dir) -> list[str]:
    """Write the model's shine masks as grey PNGs ``<model>_mNN_shine.png``
    (NN = texture component, as in the material names; 128 = the game's 1.0)
    for its default texture set. Returns the file names."""
    from pathlib import Path
    from ..formats.pck import parse_pck
    from ..util.png import encode_png
    from .textures import character_texture_sets
    pck, out_dir = Path(pck), Path(out_dir)
    model = parse_pck(pck)
    sets = character_texture_sets(pck)
    if not sets or not part_shine(model):
        return []
    images = sets["default"].images
    masks, _overlap = shine_masks(model, images)
    written = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for c, mask in sorted(masks.items()):
        img = images[c]
        name = f"{model.name}_m{c:02d}_shine.png"
        (out_dir / name).write_bytes(encode_png(img.width, img.height, bytes(b for v in mask for b in (v, v, v, 255))))
        written.append(name)
    return written
