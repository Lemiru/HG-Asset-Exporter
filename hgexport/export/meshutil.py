"""Geometry helpers: vertex welding and smooth normal reconstruction."""
from __future__ import annotations

import math
from typing import Hashable, Sequence


class VertexWelder:
    """Accumulates vertices, merging exact duplicates (by key) into one index."""

    def __init__(self):
        self.index: dict[Hashable, int] = {}
        self.rows: list[tuple] = []

    def add(self, key: Hashable, row: tuple) -> int:
        found = self.index.get(key)
        if found is not None:
            return found
        self.index[key] = len(self.rows)
        self.rows.append(row)
        return len(self.rows) - 1


def face_normal(a: Sequence[float], b: Sequence[float], c: Sequence[float]) -> tuple[float, float, float]:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    return (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)


def normalize(v: Sequence[float], fallback=(0.0, 1.0, 0.0)) -> tuple[float, float, float]:
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if n < 1e-20:
        return tuple(fallback)  # type: ignore[return-value]
    return (v[0] / n, v[1] / n, v[2] / n)


def smooth_normals(positions: Sequence[Sequence[float]], triangles: Sequence[tuple[int, int, int]],
                   keys: Sequence[Hashable], fallback: Sequence[Sequence[float]]) -> list[tuple[float, float, float]]:
    """Area-weighted normals shared by all vertices with the same ``key``
    (typically the exact fixed-point position). Vertices not used by any
    triangle keep their ``fallback`` normal."""
    acc: dict[Hashable, list[float]] = {}
    for i, j, k in triangles:
        n = face_normal(positions[i], positions[j], positions[k])
        for v in (i, j, k):
            t = acc.setdefault(keys[v], [0.0, 0.0, 0.0])
            t[0] += n[0]
            t[1] += n[1]
            t[2] += n[2]
    out = []
    for v, key in enumerate(keys):
        t = acc.get(key)
        out.append(normalize(t, normalize(fallback[v])) if t else normalize(fallback[v]))
    return out
