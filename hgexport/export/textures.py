"""Texture selection for characters and glTF material construction."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..formats.tex import IndexedImage, parse_tex
from ..gltf.builder import GltfBuilder
from ..util.bleed import bleed_transparent
from ..util.png import encode_png
from .alpha import MASK_CUTOFF, TEST_CUTOFF, AlphaSampler

CLAMP = 33071
REPEAT = 10497


@dataclass
class TextureSet:
    images: list[IndexedImage]
    sources: list[str]          # per component: "FILE.TEX#n"
    label: str


def _family(stem: str) -> str:
    return stem.split("_")[0].upper()


def character_texture_sets(pck: Path) -> dict[str, TextureSet]:
    """Texture sets usable by a PCK, keyed by variant name; "default" first.

    * Gameplay models use the family ``*_000.TEX`` (or a same-named TEX with
      the full component count).
    * ``*_2xx.TEX`` files hold one high-resolution replacement for component
      0. The game installs ``*_200.TEX`` this way when an actor enters a
      cutscene, so ``*_200.PCK`` cutscene models use it by default.
    * ``*_000H.TEX`` / ``*_200H.TEX`` are alternate sheets selected by a
      global flag; they are offered as variants.
    """
    folder = pck.parent
    stem = pck.stem.upper()
    family = _family(stem)

    def load(name: str) -> list[IndexedImage] | None:
        path = folder / name
        return parse_tex(path) if path.is_file() else None

    base_name = None
    for candidate in (f"{stem}.TEX", f"{family}_000.TEX"):
        imgs = load(candidate)
        if imgs and len(imgs) > 1 or (imgs and candidate.endswith("_000.TEX")):
            base_name = candidate
            break
    if base_name is None:
        return {}
    base = load(base_name) or []
    sets: dict[str, TextureSet] = {}

    def make(label: str, images: list[IndexedImage], replace: str | None, rimg: IndexedImage | None,
             base_label: str = base_name) -> TextureSet:
        imgs = list(images)
        sources = [f"{base_label}#{i}" for i in range(len(imgs))]
        if replace and rimg is not None:
            imgs[0] = rimg
            sources[0] = f"{replace}#0"
        return TextureSet(imgs, sources, label)

    replacements = sorted(p.name for p in folder.glob(f"{family}_2*.TEX") if not p.stem.upper().endswith("H"))
    cut_name = f"{family}_200.TEX"
    cut = load(cut_name)
    is_cutscene_model = stem.endswith("_200")
    if is_cutscene_model and cut:
        sets["default"] = make("cutscene (_200 face/body sheet)", base, cut_name, cut[0])
        sets["gameplay"] = make("gameplay", base, None, None)
    else:
        sets["default"] = make("gameplay", base, None, None)
        if cut:
            sets["cutscene"] = make("cutscene (_200 sheet)", base, cut_name, cut[0])
    for name in replacements:
        if name.upper() == cut_name:
            continue
        rep = load(name)
        if rep and len(rep) == 1:
            sets[Path(name).stem.lower()] = make(f"alternate {name}", base, name, rep[0])
    alt_base_name = base_name.replace(".TEX", "H.TEX")
    alt_base = load(alt_base_name)
    if alt_base and len(alt_base) == len(base):
        cut_h = load(f"{family}_200H.TEX")
        if is_cutscene_model and (cut_h or cut):
            src = cut_h or cut
            sets["H"] = make("H flag", alt_base, f"{family}_200H.TEX" if cut_h else cut_name, src[0],
                             alt_base_name)
        else:
            sets["H"] = make("H flag", alt_base, None, None, alt_base_name)
    return sets


class MaterialFactory:
    """Creates glTF materials for (texture component, alpha mode) pairs,
    embedding each decoded texture once."""

    def __init__(self, builder: GltfBuilder, images: list[IndexedImage], prefix: str,
                 sources: list[str] | None = None, unlit: bool = False, bleed: bool = False):
        self.b = builder
        self.bleed = bleed              # fill RGB under alpha-0 texels (util/bleed.py)
        self.images = images
        self.prefix = prefix
        self.sources = sources or [""] * len(images)
        self.unlit = unlit
        self._tex: dict[tuple, int] = {}
        self._images: dict[int, int] = {}
        self._mat: dict[tuple, int] = {}
        self._rgba: dict[int, bytes] = {}
        self._samplers: dict[bool, int] = {}
        self._samplers_alpha: dict[int, AlphaSampler] = {}
        self.shine: dict[int, bytes] = {}   # component -> grey shine mask (export/shine.py)
        self.specular = False               # put the shine masks into the materials
        self._shine_tex: dict[int, int] = {}

    def rgba(self, component: int) -> bytes:
        if component not in self._rgba:
            self._rgba[component] = self.images[component].rgba()
        return self._rgba[component]

    def texture(self, component: int, repeat: bool = False) -> int:
        key = (component, repeat)
        if key not in self._tex:
            if repeat not in self._samplers:
                wrap = REPEAT if repeat else CLAMP
                self._samplers[repeat] = self.b.sampler(wrap_s=wrap, wrap_t=wrap)
            image = self._images.get(component)
            if image is None:
                img = self.images[component]
                rgba = self.rgba(component)
                if self.bleed:
                    rgba = bleed_transparent(img.width, img.height, rgba)
                png = encode_png(img.width, img.height, rgba)
                image = self._images[component] = self.b.image(png, f"{self.prefix}_tex{component:02d}")
            self._tex[key] = self.b.texture(image, self._samplers[repeat])
        return self._tex[key]

    def shine_texture(self, component: int) -> int:
        """One texture for KHR_materials_specular (alpha: specular, the shine
        strength relative to the sheet's strongest) and the metal/roughness slot
        (green: roughness of the game's broad highlight where shiny, 1
        elsewhere; blue: metal 0)."""
        if component not in self._shine_tex:
            from .shine import SHINE_ROUGHNESS
            img, mask = self.images[component], self.shine[component]
            rough, top = round(SHINE_ROUGHNESS * 255), max(mask)
            px = bytearray(4 * len(mask))
            for i, v in enumerate(mask):
                px[4 * i + 1] = rough if v else 255
                px[4 * i + 3] = round(255 * v / top)
            image = self.b.image(encode_png(img.width, img.height, bytes(px)), f"{self.prefix}_shine{component:02d}")
            if False not in self._samplers:
                self._samplers[False] = self.b.sampler(wrap_s=CLAMP, wrap_t=CLAMP)
            self._shine_tex[component] = self.b.texture(image, self._samplers[False])
        return self._shine_tex[component]

    def sampler(self, component: int) -> AlphaSampler | None:
        if not 0 <= component < len(self.images):
            return None
        if component not in self._samplers_alpha:
            img = self.images[component]
            self._samplers_alpha[component] = AlphaSampler(img.width, img.height, self.rgba(component)[3::4])
        return self._samplers_alpha[component]

    def material(self, component: int, mode: str, *, double_sided: bool = True, name: str | None = None,
                 extras: dict | None = None, repeat: bool = False) -> int:
        """``mode`` is a glTF alphaMode (OPAQUE, MASK or BLEND) or TEST: MASK at
        the game's alpha-test cutoff (export/alpha.py)."""
        key = (component, mode, double_sided, name, repeat)
        if key in self._mat:
            return self._mat[key]
        if not 0 <= component < len(self.images):
            mat = {"name": name or f"{self.prefix}_untextured",
                   "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0.0,
                                            "roughnessFactor": 1.0},
                   "doubleSided": double_sided}
        else:
            mat = {"name": name or f"{self.prefix}_m{component:02d}_{mode.lower()}",
                   "pbrMetallicRoughness": {"baseColorTexture": {"index": self.texture(component, repeat)},
                                            "metallicFactor": 0.0, "roughnessFactor": 1.0},
                   "doubleSided": double_sided}
            if mode != "OPAQUE":
                mat["alphaMode"] = "MASK" if mode == "TEST" else mode
            if mode == "MASK":
                mat["alphaCutoff"] = MASK_CUTOFF
            elif mode == "TEST":
                # The GS alpha test passes any non-zero alpha (ATST=NOTEQUAL, AREF=0).
                mat["alphaCutoff"] = TEST_CUTOFF
            mat["extras"] = {"source_texture": self.sources[component]}
            if self.specular:
                # the game's shine (export/shine.py): specular only where the parts shine
                # (the sheet's strongest shine sets the reflectance through the IOR)
                ext = mat.setdefault("extensions", {})
                if component in self.shine:
                    from .shine import f0_ior, shine_f0
                    tex = self.shine_texture(component)
                    mat["pbrMetallicRoughness"]["metallicRoughnessTexture"] = {"index": tex}
                    # tinted by the texture, as the game's glow brightens the
                    # frame by its own colour (dark cloth stays dark, detail stays)
                    ext["KHR_materials_specular"] = {"specularFactor": 1.0, "specularTexture": {"index": tex},
                                                     "specularColorTexture": {"index": self.texture(component, repeat)}}
                    ext["KHR_materials_ior"] = {"ior": round(f0_ior(shine_f0(max(self.shine[component]))), 3)}
                    self.b.extensions_used.add("KHR_materials_ior")
                else:
                    ext["KHR_materials_specular"] = {"specularFactor": 0.0}
                self.b.extensions_used.add("KHR_materials_specular")
        if self.unlit:
            mat.setdefault("extensions", {})["KHR_materials_unlit"] = {}
        if extras:
            mat.setdefault("extras", {}).update(extras)
        self._mat[key] = self.b.material(mat)
        return self._mat[key]
