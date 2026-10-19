"""Load .glb/.gltf files, decode accessors and run structural validation."""
from __future__ import annotations

import json
import math
import struct
from pathlib import Path
from typing import Any

_FMT = {5126: ("f", 4), 5125: ("I", 4), 5123: ("H", 2), 5121: ("B", 1), 5122: ("h", 2), 5120: ("b", 1)}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16, "MAT3": 9, "MAT2": 4}
_NORM = {5121: 255.0, 5123: 65535.0, 5120: 127.0, 5122: 32767.0}


class Gltf:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        raw = self.path.read_bytes()
        self.images: dict[int, bytes] = {}
        if raw[:4] == b"glTF":
            _magic, _version, _length = struct.unpack_from("<III", raw, 0)
            pos = 12
            self.json: dict[str, Any] = {}
            self.buffers: list[bytes] = []
            while pos < len(raw):
                size, kind = struct.unpack_from("<II", raw, pos)
                chunk = raw[pos + 8:pos + 8 + size]
                if kind == 0x4E4F534A:
                    self.json = json.loads(chunk.decode("utf-8"))
                elif kind == 0x004E4942:
                    self.buffers.append(chunk)
                pos += 8 + size
        else:
            self.json = json.loads(raw.decode("utf-8"))
            self.buffers = [(self.path.parent / b["uri"]).read_bytes() for b in self.json.get("buffers", [])]

    def view_bytes(self, index: int) -> bytes:
        v = self.json["bufferViews"][index]
        start = v.get("byteOffset", 0)
        return self.buffers[v["buffer"]][start:start + v["byteLength"]]

    def image_bytes(self, index: int) -> bytes:
        img = self.json["images"][index]
        if "bufferView" in img:
            return self.view_bytes(img["bufferView"])
        return (self.path.parent / img["uri"]).read_bytes()

    def accessor(self, index: int) -> list[tuple]:
        a = self.json["accessors"][index]
        code, size = _FMT[a["componentType"]]
        n = _NCOMP[a["type"]]
        count = a["count"]
        if "bufferView" not in a:
            rows = [tuple([0] * n) for _ in range(count)]
        else:
            v = self.json["bufferViews"][a["bufferView"]]
            stride = v.get("byteStride", size * n)
            base = v.get("byteOffset", 0) + a.get("byteOffset", 0)
            buf = self.buffers[v["buffer"]]
            st = struct.Struct("<" + code * n)
            rows = [st.unpack_from(buf, base + i * stride) for i in range(count)]
        if a.get("normalized"):
            scale = _NORM[a["componentType"]]
            rows = [tuple(c / scale for c in r) for r in rows]
        return rows

    def scalars(self, index: int) -> list[float]:
        return [r[0] for r in self.accessor(index)]


def validate(path: Path | str) -> list[str]:
    """Return a list of structural problems (empty when the file is valid)."""
    g = Gltf(path)
    j = g.json
    errors: list[str] = []
    accessors = j.get("accessors", [])
    views = j.get("bufferViews", [])
    nodes = j.get("nodes", [])

    def acc_ok(i: Any, where: str) -> bool:
        if not isinstance(i, int) or not 0 <= i < len(accessors):
            errors.append(f"{where}: invalid accessor {i}")
            return False
        return True

    for i, v in enumerate(views):
        b = v.get("buffer", 0)
        if b >= len(g.buffers) or v.get("byteOffset", 0) + v["byteLength"] > len(g.buffers[b]):
            errors.append(f"bufferView {i} exceeds buffer")
    for i, a in enumerate(accessors):
        if "bufferView" in a:
            v = views[a["bufferView"]]
            _code, size = _FMT[a["componentType"]]
            n = _NCOMP[a["type"]]
            stride = v.get("byteStride", size * n)
            need = a.get("byteOffset", 0) + stride * (a["count"] - 1) + size * n
            if need > v["byteLength"]:
                errors.append(f"accessor {i} exceeds its bufferView")
        if a["componentType"] == 5126 and "bufferView" in a:
            rows = g.accessor(i)
            if any(not math.isfinite(c) for r in rows for c in r):
                errors.append(f"accessor {i} contains non-finite values")
            if "min" in a:
                for k in range(len(a["min"])):
                    lo = min(r[k] for r in rows)
                    hi = max(r[k] for r in rows)
                    if lo < a["min"][k] - 1e-6 or hi > a["max"][k] + 1e-6:
                        errors.append(f"accessor {i} min/max do not enclose data")
                        break
    for mi, mesh in enumerate(j.get("meshes", [])):
        target_counts = set()
        for pi, p in enumerate(mesh["primitives"]):
            where = f"mesh {mi} primitive {pi}"
            attrs = p["attributes"]
            if "POSITION" not in attrs:
                errors.append(f"{where}: no POSITION")
                continue
            if not acc_ok(attrs["POSITION"], where):
                continue
            pa = accessors[attrs["POSITION"]]
            if "min" not in pa:
                errors.append(f"{where}: POSITION lacks min/max")
            count = pa["count"]
            for name, ai in attrs.items():
                if acc_ok(ai, where) and accessors[ai]["count"] != count:
                    errors.append(f"{where}: attribute {name} count mismatch")
            if "indices" in p and acc_ok(p["indices"], where):
                idx = g.scalars(p["indices"])
                if len(idx) % 3 and p.get("mode", 4) == 4:
                    errors.append(f"{where}: index count not a multiple of 3")
                if idx and max(idx) >= count:
                    errors.append(f"{where}: index out of range")
            if "JOINTS_0" in attrs and "WEIGHTS_0" in attrs:
                for row in g.accessor(attrs["WEIGHTS_0"])[:2000]:
                    if abs(sum(row) - 1.0) > 2e-3:
                        errors.append(f"{where}: skin weights do not sum to 1")
                        break
            targets = p.get("targets", [])
            target_counts.add(len(targets))
            for t in targets:
                for name, ai in t.items():
                    if acc_ok(ai, where) and accessors[ai]["count"] != count:
                        errors.append(f"{where}: morph target {name} count mismatch")
            if "material" in p and p["material"] >= len(j.get("materials", [])):
                errors.append(f"{where}: invalid material")
        if len(target_counts) > 1:
            errors.append(f"mesh {mi}: primitives have different morph target counts")
        if "weights" in mesh and target_counts and len(mesh["weights"]) != max(target_counts):
            errors.append(f"mesh {mi}: default weights length differs from target count")
    parents: dict[int, int] = {}
    for ni, n in enumerate(nodes):
        for c in n.get("children", []):
            if c in parents:
                errors.append(f"node {c} has several parents")
            parents[c] = ni
        if "matrix" in n and any(k in n for k in ("translation", "rotation", "scale")):
            errors.append(f"node {ni} has both matrix and TRS")
        if "skin" in n and "mesh" not in n:
            errors.append(f"node {ni} has skin but no mesh")
    for si, s in enumerate(j.get("skins", [])):
        if "inverseBindMatrices" in s and acc_ok(s["inverseBindMatrices"], f"skin {si}"):
            if accessors[s["inverseBindMatrices"]]["count"] != len(s["joints"]):
                errors.append(f"skin {si}: inverse bind count mismatch")
    for ai, anim in enumerate(j.get("animations", [])):
        for ci, ch in enumerate(anim["channels"]):
            s = anim["samplers"][ch["sampler"]]
            t = ch["target"]
            where = f"animation {ai} ({anim.get('name')}) channel {ci}"
            if t.get("path") == "pointer":
                if not acc_ok(s["input"], where) or not acc_ok(s["output"], where):
                    continue
                if accessors[s["input"]]["count"] != accessors[s["output"]]["count"]:
                    errors.append(f"{where}: pointer output count mismatch")
                continue
            if t.get("node") is None or t["node"] >= len(nodes):
                errors.append(f"{where}: bad node")
                continue
            if "matrix" in nodes[t["node"]]:
                errors.append(f"{where}: animates a matrix node")
            if not acc_ok(s["input"], where) or not acc_ok(s["output"], where):
                continue
            inp = accessors[s["input"]]
            if "min" not in inp:
                errors.append(f"{where}: input lacks min/max")
            times = g.scalars(s["input"])
            if any(b <= a for a, b in zip(times, times[1:])):
                errors.append(f"{where}: input times not strictly increasing")
            n_out = accessors[s["output"]]["count"]
            mult = 3 if s.get("interpolation") == "CUBICSPLINE" else 1
            if t["path"] == "weights":
                mesh = j["meshes"][nodes[t["node"]]["mesh"]]
                mult *= len(mesh["primitives"][0].get("targets", []))
            if n_out != inp["count"] * mult:
                errors.append(f"{where}: output count {n_out} != input {inp['count']} x {mult}")
    for scene in j.get("scenes", []):
        for n in scene.get("nodes", []):
            if n in parents:
                errors.append(f"scene root {n} is also a child")
    return errors
