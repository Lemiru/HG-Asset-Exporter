"""glTF 2.0 document builder writing either a single .glb or .gltf + .bin + PNGs."""
from __future__ import annotations

import hashlib
import json
import math
import struct
import sys
from array import array
from pathlib import Path
from typing import Any, Iterable, Sequence

FLOAT, UINT, USHORT, UBYTE = 5126, 5125, 5123, 5121
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
_CSIZE = {FLOAT: 4, UINT: 4, USHORT: 2, UBYTE: 1}
_ACODE = {FLOAT: "f", UINT: "I", USHORT: "H", UBYTE: "B"}

if sys.byteorder != "little":  # array.tobytes() is native-endian
    raise RuntimeError("hgexport requires a little-endian host")


def _flatten(rows: Iterable[Sequence[float]] | Iterable[float], width: int) -> list:
    if width == 1:
        return [r[0] if isinstance(r, (tuple, list)) else r for r in rows]
    flat: list = []
    for r in rows:
        flat.extend(r)
    return flat


def drop_flat_keys(times: Sequence[float], values: Sequence) -> tuple[list, list]:
    """Remove keys whose value equals both neighbours (lossless for LINEAR and
    STEP; the first and last key, hence the clip length, are kept)."""
    n = len(values)
    if n < 3:
        return list(times), list(values)
    keep = [0] + [i for i in range(1, n - 1)
                  if not (values[i] == values[i - 1] and values[i] == values[i + 1])] + [n - 1]
    return [times[i] for i in keep], [values[i] for i in keep]


class GltfBuilder:
    def __init__(self, generator: str = "hgexport"):
        self.doc: dict[str, Any] = {"asset": {"version": "2.0", "generator": generator},
                                    "scene": 0, "scenes": [{"nodes": []}]}
        self.blob = bytearray()
        self._view_cache: dict[tuple, int] = {}
        self._accessor_cache: dict[tuple, int] = {}
        self._image_payloads: list[tuple[str, bytes]] = []
        self.extensions_used: set[str] = set()

    # ------------------------------------------------------------------ lists
    def _list(self, key: str) -> list:
        return self.doc.setdefault(key, [])

    def _append(self, key: str, item: dict) -> int:
        items = self._list(key)
        items.append(item)
        return len(items) - 1

    # ---------------------------------------------------------------- buffers
    def buffer_view(self, payload: bytes, target: int | None = None, stride: int | None = None,
                    dedupe: bool = True) -> int:
        key = (hashlib.sha1(payload).digest(), len(payload), target, stride)
        if dedupe and key in self._view_cache:
            return self._view_cache[key]
        while len(self.blob) % 4:
            self.blob.append(0)
        view: dict[str, Any] = {"buffer": 0, "byteOffset": len(self.blob), "byteLength": len(payload)}
        if target is not None:
            view["target"] = target
        if stride is not None:
            view["byteStride"] = stride
        self.blob += payload
        index = self._append("bufferViews", view)
        if dedupe:
            self._view_cache[key] = index
        return index

    def accessor(self, values: Iterable, kind: str, component: int = FLOAT, *,
                 bounds: bool = False, normalized: bool = False, target: int | None = None,
                 dedupe: bool = True) -> int:
        width = _NCOMP[kind]
        flat = _flatten(values, width)
        if width > 1 and len(flat) % width:
            raise ValueError("accessor data is not a whole number of elements")
        count = len(flat) // width
        if count == 0:
            raise ValueError("empty accessor")
        if component == FLOAT and not all(math.isfinite(v) for v in flat):
            raise ValueError("non-finite value in float accessor")
        payload = array(_ACODE[component], flat).tobytes()
        if component in (UBYTE, USHORT) and target == ARRAY_BUFFER and (_CSIZE[component] * width) % 4:
            # Vertex attribute elements must be 4-byte aligned: pad each element.
            elem = _CSIZE[component] * width
            padded = (elem + 3) & ~3
            out = bytearray()
            for i in range(count):
                out += payload[i * elem:(i + 1) * elem] + bytes(padded - elem)
            payload = bytes(out)
            view = self.buffer_view(payload, target, stride=padded, dedupe=dedupe)
        elif target == ARRAY_BUFFER:
            # Vertex data: an explicit (tight) stride keeps the view valid when
            # identical payloads are shared by several accessors.
            view = self.buffer_view(payload, target, stride=_CSIZE[component] * width, dedupe=dedupe)
        else:
            view = self.buffer_view(payload, target, dedupe=dedupe)
        acc: dict[str, Any] = {"bufferView": view, "componentType": component, "count": count, "type": kind}
        if normalized:
            acc["normalized"] = True
        if bounds:
            acc["min"] = [min(flat[i::width]) for i in range(width)]
            acc["max"] = [max(flat[i::width]) for i in range(width)]
            if component == FLOAT:
                # Bounds must enclose float32-rounded values exactly.
                acc["min"] = [struct.unpack("<f", struct.pack("<f", v))[0] for v in acc["min"]]
                acc["max"] = [struct.unpack("<f", struct.pack("<f", v))[0] for v in acc["max"]]
        key = (view, component, count, kind, normalized, bounds)
        if dedupe and key in self._accessor_cache:
            return self._accessor_cache[key]
        index = self._append("accessors", acc)
        if dedupe:
            self._accessor_cache[key] = index
        return index

    # Convenience wrappers ---------------------------------------------------
    def positions(self, values: Sequence[Sequence[float]]) -> int:
        return self.accessor(values, "VEC3", bounds=True, target=ARRAY_BUFFER)

    def vec3(self, values, *, target: int | None = ARRAY_BUFFER) -> int:
        return self.accessor(values, "VEC3", target=target)

    def vec2(self, values) -> int:
        return self.accessor(values, "VEC2", target=ARRAY_BUFFER)

    def vec4(self, values, *, target: int | None = ARRAY_BUFFER) -> int:
        return self.accessor(values, "VEC4", target=target)

    def colors_u8(self, values) -> int:
        return self.accessor(values, "VEC4", UBYTE, normalized=True, target=ARRAY_BUFFER)

    def joints(self, values) -> int:
        big = any(j > 255 for row in values for j in row)
        return self.accessor(values, "VEC4", USHORT if big else UBYTE, target=ARRAY_BUFFER)

    def indices(self, values: Sequence[int]) -> int:
        big = max(values) > 65535
        return self.accessor(values, "SCALAR", UINT if big else USHORT, target=ELEMENT_ARRAY_BUFFER)

    def times(self, values: Sequence[float]) -> int:
        return self.accessor(values, "SCALAR", bounds=True)

    def anim_sampler(self, times: Sequence[float], values: Sequence, kind: str,
                     interpolation: str = "LINEAR") -> dict:
        """Sampler with flat runs removed. ``kind`` "WEIGHTS" takes one row of
        morph weights per key."""
        times, values = drop_flat_keys(times, values)
        if kind == "WEIGHTS":
            output = self.accessor([w for row in values for w in row], "SCALAR")
        else:
            output = self.anim_output(values, kind)
        return {"input": self.times(times), "output": output, "interpolation": interpolation}

    def anim_output(self, values, kind: str) -> int:
        return self.accessor(values, kind)

    def mat4s(self, matrices: Sequence[Sequence[float]]) -> int:
        return self.accessor(matrices, "MAT4")

    # ------------------------------------------------------------ resources
    def image(self, png: bytes, name: str) -> int:
        self._image_payloads.append((name, png))
        return self._append("images", {"name": name})

    def texture(self, image: int, sampler: int | None = None) -> int:
        tex: dict[str, Any] = {"source": image}
        if sampler is not None:
            tex["sampler"] = sampler
        return self._append("textures", tex)

    def sampler(self, *, wrap_s: int = 10497, wrap_t: int = 10497,
                mag: int = 9729, min_: int = 9729) -> int:
        return self._append("samplers", {"magFilter": mag, "minFilter": min_, "wrapS": wrap_s, "wrapT": wrap_t})

    def material(self, mat: dict) -> int:
        for ext in mat.get("extensions", {}):
            self.extensions_used.add(ext)
        return self._append("materials", mat)

    def mesh(self, mesh: dict) -> int:
        return self._append("meshes", mesh)

    def node(self, node: dict, *, root: bool = False) -> int:
        index = self._append("nodes", node)
        if root:
            self.doc["scenes"][0]["nodes"].append(index)
        return index

    def add_child(self, parent: int, child: int) -> None:
        self.doc["nodes"][parent].setdefault("children", []).append(child)

    def skin(self, skin: dict) -> int:
        return self._append("skins", skin)

    def camera(self, cam: dict) -> int:
        return self._append("cameras", cam)

    def animation(self, anim: dict) -> int:
        return self._append("animations", anim)

    def has(self, key: str) -> bool:
        return bool(self.doc.get(key))

    # ------------------------------------------------------------------ write
    def write(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = json.loads(json.dumps(self.doc))  # deep copy; finalise below
        if self.extensions_used:
            doc["extensionsUsed"] = sorted(self.extensions_used)
        blob = bytearray(self.blob)
        glb = path.suffix.lower() == ".glb"
        images = doc.get("images", [])
        for (name, png), img in zip(self._image_payloads, images):
            if glb:
                while len(blob) % 4:
                    blob.append(0)
                doc.setdefault("bufferViews", []).append(
                    {"buffer": 0, "byteOffset": len(blob), "byteLength": len(png)})
                blob += png
                img["bufferView"] = len(doc["bufferViews"]) - 1
                img["mimeType"] = "image/png"
            else:
                filename = f"{path.stem}_{name}.png"
                (path.parent / filename).write_bytes(png)
                img["uri"] = filename
        while len(blob) % 4:
            blob.append(0)
        if blob:
            doc["buffers"] = [{"byteLength": len(blob)}]
            if not glb:
                bin_name = path.stem + ".bin"
                (path.parent / bin_name).write_bytes(blob)
                doc["buffers"][0]["uri"] = bin_name
        _strip_empty(doc)
        text = json.dumps(doc, separators=(",", ":"), ensure_ascii=True)
        if not glb:
            path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
            return path
        jbytes = text.encode("utf-8")
        jbytes += b" " * ((4 - len(jbytes) % 4) % 4)
        total = 12 + 8 + len(jbytes) + (8 + len(blob) if blob else 0)
        with open(path, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, total))
            f.write(struct.pack("<II", len(jbytes), 0x4E4F534A))
            f.write(jbytes)
            if blob:
                f.write(struct.pack("<II", len(blob), 0x004E4942))
                f.write(blob)
        return path


def _strip_empty(doc: dict) -> None:
    for key in [k for k, v in doc.items() if isinstance(v, list) and not v and k != "scenes"]:
        del doc[key]
