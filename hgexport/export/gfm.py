"""GAME_FIX.GFM props -> one glTF with the four objects (formats/gfm.py)."""
from __future__ import annotations

from pathlib import Path

from ..formats.gfm import TEXTURE_IMAGE, parse_gfm
from ..formats.tex import parse_tex
from ..gltf.builder import GltfBuilder
from ..util.bleed import bleed_transparent
from ..util.png import encode_png
from .alpha import MASK_CUTOFF, AlphaSampler, assign_modes
from .stage import _A_LUT, _RGB_LUT

CROP = 256              # every part samples the left 256 x 256 of the 512 x 256 image


def _palette_rgba(image, palette: int) -> bytes:
    pal = image.palette[16 * palette:16 * palette + 16]
    rows = bytearray()
    for y in range(image.height):
        line = image.indices[y * image.width:y * image.width + CROP]
        rows += b"".join(pal[i & 15] for i in line)
    return bytes(rows)


def export_gfm(gfm: Path | str, out: Path | str, scale: float = 0.1, bleed: bool = False) -> dict:
    gfm = Path(gfm)
    entries = parse_gfm(gfm)
    image = parse_tex(gfm.with_suffix(".TEX"))[TEXTURE_IMAGE]
    palettes = [k for k in range(16) if any(c[3] for c in image.palette[16 * k:16 * k + 16])]
    b = GltfBuilder("hgexport gfm")
    b.extensions_used.add("KHR_materials_unlit")
    sampler = b.sampler(wrap_s=33071, wrap_t=33071)
    rgba = {k: _palette_rgba(image, k) for k in palettes}
    samplers = {k: AlphaSampler(CROP, image.height, rgba[k][3::4]) for k in palettes}
    textures: dict[int, int] = {}
    materials: dict[tuple[int, str], int] = {}

    def material(k: int, mode: str) -> int:
        """Palette k with a glTF alpha mode (export/alpha.py; all parts are
        drawn with GS blending)."""
        if (k, mode) not in materials:
            if k not in textures:
                png = encode_png(CROP, image.height, bleed_transparent(CROP, image.height, rgba[k]) if bleed else rgba[k])
                textures[k] = b.texture(b.image(png, f"GAME_FIX_2_pal{k}"),
                                        sampler)
            mat = {"name": f"gfm_palette{k}_{mode.lower()}", "doubleSided": True,
                   "pbrMetallicRoughness": {"baseColorTexture": {"index": textures[k]}, "metallicFactor": 0.0},
                   "extensions": {"KHR_materials_unlit": {}}}
            if mode != "OPAQUE":
                mat["alphaMode"] = mode
            if mode == "MASK":
                mat["alphaCutoff"] = MASK_CUTOFF
            materials[(k, mode)] = b.material(mat)
        return materials[(k, mode)]
    b.doc.setdefault("extensions", {})["KHR_materials_variants"] = {
        "variants": [{"name": f"palette{k}"} for k in palettes]}
    b.extensions_used.add("KHR_materials_variants")
    b.doc["asset"]["extras"] = {
        "source": gfm.name, "texture": f"{gfm.with_suffix('.TEX').name} image {TEXTURE_IMAGE}",
        "note": "resident props drawn with GAME_FIX.TEX image 2; each part's palette is its stored index plus a "
                "base the owning game object adds (tinted / flashing copies); the variants switch every part to "
                "one palette (palette1 = natural colours)"}
    report = {"objects": len(entries), "parts": 0, "triangles": 0, "palettes": palettes}
    for e, parts in enumerate(entries):
        root = b.node({"name": f"gfm{e}", "extras": {"entry": e}}, root=True)
        for k, part in enumerate(parts):
            tris = part.triangles()
            uvs = [(u * 512 / CROP, v) for u, v in part.uvs]
            attrs = {
                "POSITION": b.positions([tuple(c * scale for c in p) for p in part.positions]),
                "TEXCOORD_0": b.vec2(uvs),
                "COLOR_0": b.colors_u8([(_RGB_LUT[r], _RGB_LUT[g], _RGB_LUT[bb], _A_LUT[a])
                                        for r, g, bb, a in part.colors])}
            # Modes per palette (palette 5 is an alpha ramp); triangles with the
            # same modes in every palette share a primitive.
            modes = {}
            for pal in palettes:
                def texels(i: int, reach: bool, pal=pal) -> list[int]:
                    t = tris[i]
                    scale_a = min(1.0, sum(part.colors[v][3] for v in t) / (3 * 0x80))
                    return [min(255, round(a * scale_a)) for a in samplers[pal].texels([uvs[v] for v in t], reach)]
                modes[pal] = assign_modes(tris, lambda v: part.positions[v], texels, blend=True)
            default = part.palette if part.palette in palettes else palettes[0]
            groups: dict[tuple[str, ...], list[int]] = {}
            for i in range(len(tris)):
                groups.setdefault(tuple(modes[pal][i] for pal in palettes), []).append(i)
            order = {"OPAQUE": 0, "MASK": 1, "BLEND": 2}
            primitives = []
            for sig in sorted(groups, key=lambda sig: order[sig[palettes.index(default)]]):
                mapping: dict[int, list[int]] = {}
                for i, pal in enumerate(palettes):
                    mapping.setdefault(material(pal, sig[i]), []).append(i)
                primitives.append({
                    "attributes": attrs, "indices": b.indices([v for i in groups[sig] for v in tris[i]]), "mode": 4,
                    "material": material(default, sig[palettes.index(default)]),
                    "extensions": {"KHR_materials_variants": {"mappings": [
                        {"material": m, "variants": v} for m, v in mapping.items()]}}})
            name = f"gfm{e}_part{k}"
            node = b.node({"name": name, "mesh": b.mesh({"name": name, "primitives": primitives}),
                           "extras": {"palette": part.palette}})
            b.add_child(root, node)
            report["parts"] += 1
            report["triangles"] += len(tris)
    b.write(Path(out))
    return report
