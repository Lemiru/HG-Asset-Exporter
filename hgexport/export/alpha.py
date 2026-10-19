"""glTF alpha modes for model and room triangles.

The game's model and room renderers alpha-test every triangle (pixels with
texel alpha 0 are discarded, TEST ATST=NOTEQUAL AREF=0), write depth for
all of them, and enable GS alpha blending only for parts whose render flag
bit 0 is set; those are drawn after the opaque parts. That flag is set on
whole submeshes that are mostly plain opaque surfaces (a torso with a lace
hem, a skirt with a lace edge), so it is a poor guide for engines, which do
not write depth for blended materials and sort them per object.

Blended parts are therefore split by what the texture holds where each
triangle samples it (every texel bilinear filtering can reach):

* OPAQUE: all texels at least OPAQUE_ALPHA (the sheets use 0x70-0x7F, i.e.
  88-100 %, throughout solid cloth and hair). Only where fully opaque texels
  dominate, though: in an element (triangles joined by shared edge
  positions) whose texels are mostly 88-99 %, such as tinted glass, that
  translucency is real and only fully opaque triangles are OPAQUE. Triangles
  the caller marks translucent (vertex alpha fades) are never OPAQUE.
* MASK (cutoff 0.5): only texels at most CLEAR_ALPHA or at least
  OPAQUE_ALPHA, i.e. a cut-out.
* BLEND: anything in between (hair and fringe ends, eyelashes, sheer lace).

Then (triangles joined by shared edge positions) OPAQUE specks of at most
SPECK triangles enclosed by transparent ones, and MASK patches bordered only
by BLEND, take their surroundings' mode, so no material switches inside a
region without a texture reason.

Unblended parts are OPAQUE per element unless a texel they cover strictly is
0; then the element gets TEST, a MASK material with the game's cutoff (any
alpha above 0 is solid). No shipped character samples alpha 0 unblended.

``faithful`` keeps the game's split instead: blended parts are BLEND.
"""
from __future__ import annotations

import math
from typing import Callable, Hashable, Sequence

OPAQUE_ALPHA = 224              # texels at or above count as solid (GS 0x70)
CLEAR_ALPHA = 16                # texels at or below count as empty (GS 0x08)
SPECK = 2                       # largest enclosed OPAQUE region that is absorbed
MASK_CUTOFF = 0.5
TEST_CUTOFF = 0.5 / 255.0      # GS alpha test ATST=NOTEQUAL AREF=0


class AlphaSampler:
    """Texel alphas (8-bit) of one texture, sampled per triangle."""

    def __init__(self, width: int, height: int, alpha: bytes):
        self.w = width
        self.h = height
        self.alpha = alpha
        self.all_opaque = alpha.count(255) == len(alpha)

    def texels(self, uvs: Sequence[tuple[float, float]], reach: bool = False) -> list[int]:
        """Alphas of the texels whose centres lie inside the triangle (UV
        clamp addressing), or with ``reach`` within one texel of it (all
        that bilinear filtering can blend in); the texel nearest the centroid
        for triangles too small to contain one."""
        if self.all_opaque:
            return [255]
        w, h = self.w, self.h
        pts = [(u * w - 0.5, v * h - 0.5) for u, v in uvs]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        grow = 1 if reach else 0
        out: list[int] = []
        area = (pts[1][0] - pts[0][0]) * (pts[2][1] - pts[0][1]) - (pts[1][1] - pts[0][1]) * (pts[2][0] - pts[0][0])
        if abs(area) > 1e-9:
            sign = 1.0 if area > 0 else -1.0
            # Inward edge functions, each relaxed by the L-inf support of the reach.
            edges = [(a, b, grow * (abs(b[0] - a[0]) + abs(b[1] - a[1])))
                     for a, b in ((pts[0], pts[1]), (pts[1], pts[2]), (pts[2], pts[0]))]
            for y in range(max(0, math.ceil(min(ys) - grow)), min(h - 1, math.floor(max(ys) + grow)) + 1):
                row = y * w
                for x in range(max(0, math.ceil(min(xs) - grow)), min(w - 1, math.floor(max(xs) + grow)) + 1):
                    if all(sign * ((b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])) >= -d for a, b, d in edges):
                        out.append(self.alpha[row + x])
        if not out:
            cx = min(w - 1, max(0, round(sum(xs) / 3)))
            cy = min(h - 1, max(0, round(sum(ys) / 3)))
            out.append(self.alpha[cy * w + cx])
        return out


def elements(tris: Sequence[tuple[int, int, int]], key: Callable[[int], Hashable]) -> list[list[int]]:
    """Triangle indices grouped into connected elements; vertices are joined
    by ``key`` (their position), so UV and normal seams do not split them."""
    parent: dict = {}

    def find(x):
        root = x
        while parent.setdefault(root, root) != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    for a, b, c in tris:
        ra = find(key(a))
        for v in (b, c):
            rv = find(key(v))
            if rv != ra:
                parent[rv] = ra
    groups: dict = {}
    for i, t in enumerate(tris):
        groups.setdefault(find(key(t[0])), []).append(i)
    return list(groups.values())


def texel_mode(alphas: Sequence[int], solid: int = OPAQUE_ALPHA) -> str:
    """OPAQUE, MASK or BLEND for one blended triangle from its texels;
    ``solid`` is the lowest alpha that counts as opaque."""
    if min(alphas) >= solid:
        return "OPAQUE"
    if all(a <= CLEAR_ALPHA or a >= solid for a in alphas):
        return "MASK"
    return "BLEND"


def assign_modes(tris: Sequence[tuple[int, int, int]], key: Callable[[int], Hashable],
                 texels: Callable[[int, bool], Sequence[int]], blend: bool, faithful: bool = False,
                 translucent: Callable[[int], bool] | None = None) -> list[str]:
    """Alpha mode per triangle (OPAQUE, TEST, MASK or BLEND). ``texels(i,
    reach)`` gives the alphas triangle i samples (AlphaSampler.texels);
    ``translucent(i)`` marks triangles that must not be OPAQUE."""
    if blend and faithful:
        return ["BLEND"] * len(tris)
    groups = elements(tris, key)
    modes = [""] * len(tris)
    if not blend:
        for element in groups:
            mode = "TEST" if any(0 in texels(i, False) for i in element) else "OPAQUE"
            for i in element:
                modes[i] = mode
        return modes
    for element in groups:
        samples = {i: texels(i, True) for i in element}
        full = sum(a == 255 for t in samples.values() for a in t)
        near = sum(OPAQUE_ALPHA <= a < 255 for t in samples.values() for a in t)
        solid = OPAQUE_ALPHA if full >= near else 255
        for i, alphas in samples.items():
            mode = texel_mode(alphas, solid)
            if mode == "OPAQUE" and translucent is not None and translucent(i):
                mode = "BLEND"
            modes[i] = mode
    # Edge neighbours (by vertex position), for the clean-up of enclosed patches.
    by_edge: dict = {}
    for i, t in enumerate(tris):
        k = [key(v) for v in t]
        for a, b in ((0, 1), (1, 2), (2, 0)):
            by_edge.setdefault(frozenset((k[a], k[b])), []).append(i)
    neighbours: list[set[int]] = [set() for _ in tris]
    open_edge = [False] * len(tris)
    for faces in by_edge.values():
        for i in faces:
            neighbours[i].update(f for f in faces if f != i)
            open_edge[i] |= len(faces) == 1
    for absorb, into in ((("OPAQUE",), ("MASK", "BLEND")), (("MASK",), ("BLEND",))):
        seen = set()
        for start in range(len(tris)):
            if start in seen or modes[start] not in absorb:
                continue
            region, stack, border = {start}, [start], set()
            while stack:
                i = stack.pop()
                for n in neighbours[i]:
                    if modes[n] == modes[start]:
                        if n not in region:
                            region.add(n)
                            stack.append(n)
                    else:
                        border.add(modes[n])
            seen |= region
            # Opaque specks only when enclosed (no open edge): an opaque tip of
            # a hair card or fringe stays opaque.
            enclosed = absorb[0] != "OPAQUE" or (len(region) <= SPECK and not any(open_edge[i] for i in region))
            if enclosed and border and border <= set(into):
                mode = "BLEND" if "BLEND" in border else border.pop()
                for i in region:
                    modes[i] = mode
    return modes
