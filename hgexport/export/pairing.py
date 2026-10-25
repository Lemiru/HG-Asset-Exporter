"""Find the room (stage PAC) each cutscene cut takes place in.

Primary source: ``data/event_rooms.json``, derived from the executable by
``tools/derive_event_rooms.py`` (each room handler lists the events it can
start). When an event belongs to several rooms, or a cut's actors do not fit
the listed room, each cut is scored by how many sampled actor root positions
stand on a room's navigation floor at a plausible height. Camera-only cuts
inherit the room of the nearest cut with actors.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..formats.dp import CutData
from ..formats.stage import parse_navigation, parse_pac

CELL = 20.0
_EVENT_ROOMS = Path(__file__).resolve().parent.parent / "data" / "event_rooms.json"

# The room the movie gallery plays an event in, for events the table lists in
# several rooms (room 0x37's script switches on the gallery entry, command 0x3A).
GALLERY_ROOMS = {"DE000A": "ST_025", "DE0007": "ST_01C", "DE0017": "ST_01E", "DE0009": "ST_017",
                 "EV0014": "ST_011", "DE0026": "ST_066", "EV0019": "ST_047", "DE0027": "ST_04A",
                 "DE002A": "ST_106", "EV0021": "ST_10B", "DE0040": "ST_08A", "DE0042": "ST_085",
                 "EV0030": "ST_08D", "EV0034": "ST_034"}


@lru_cache(maxsize=1)
def event_rooms() -> dict[str, list[str]]:
    try:
        return json.loads(_EVENT_ROOMS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


@dataclass
class NavIndex:
    pac: Path
    grid: dict[tuple[int, int], list[tuple]]

    def floor_height(self, x: float, z: float) -> float | None:
        best = None
        for (ax, ay, az), (bx, by, bz), (cx, cy, cz) in self.grid.get((int(x // CELL), int(z // CELL)), ()):
            d = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz)
            if abs(d) < 1e-9:
                continue
            l1 = ((bz - cz) * (x - cx) + (cx - bx) * (z - cz)) / d
            l2 = ((cz - az) * (x - cx) + (ax - cx) * (z - cz)) / d
            l3 = 1 - l1 - l2
            if min(l1, l2, l3) >= -1e-4:
                y = l1 * ay + l2 * by + l3 * cy
                best = y if best is None else max(best, y)
        return best


@lru_cache(maxsize=None)
def nav_index(pac: str) -> NavIndex | None:
    tris = parse_navigation(parse_pac(pac))
    if not tris:
        return None
    grid: dict[tuple[int, int], list[tuple]] = {}
    for t in tris:
        xs = [v[0] for v in t.vertices]
        zs = [v[2] for v in t.vertices]
        for gx in range(int(min(xs) // CELL), int(max(xs) // CELL) + 1):
            for gz in range(int(min(zs) // CELL), int(max(zs) // CELL) + 1):
                grid.setdefault((gx, gz), []).append(t.vertices)
    return NavIndex(Path(pac), grid)


def actor_roots(cut: CutData, palettes: dict[int, list[int]], roots: dict[int, int],
                step: int = 5) -> list[tuple[float, float, float]]:
    """World positions of each actor's root bone, sampled every ``step`` frames."""
    points = []
    for slot, bank in cut.actors.items():
        palette, root = palettes.get(slot), roots.get(slot, 0)
        if palette is None:
            continue
        for g in bank.entries[0].slots[1:]:
            if not g:
                continue
            for t in g.tracks:
                if 0 <= t.target < len(palette) and palette[t.target] == root and \
                        (t.has_translation or t.codec == 1):
                    off = 0 if t.codec == 1 else 3
                    for s in t.samples[::step]:
                        points.append((s[off], s[off + 1], s[off + 2]))
    return points


def floor_fit(pac: Path, points: list[tuple[float, float, float]]) -> float:
    index = nav_index(str(pac))
    if index is None or not points:
        return 0.0
    hits = 0
    for x, y, z in points:
        floor = index.floor_height(x, z)
        if floor is not None and -6.0 <= y - floor <= 25.0:
            hits += 1
    return hits / len(points)


def rooms_for_cuts(data_root: Path, event: str, cuts: list[CutData], palettes: dict[int, list[int]],
                   roots: dict[int, int]) -> list[tuple[Path | None, float, str]]:
    """(PAC, floor fit, method) for every cut."""
    listed = [data_root / f"ST_{int(r[3:], 16) & ~7:03X}" / f"{r}.PAC" for r in event_rooms().get(event, [])]
    listed = [p for p in listed if p.is_file()]
    # Fallback candidates: the listed rooms' families (rooms of one folder are
    # neighbours); only events missing from the table search every room.
    if listed:
        everything = sorted({pac for folder in {p.parent for p in listed} for pac in folder.glob("*.PAC")})
    else:
        everything = sorted(data_root.glob("ST_*/*.PAC"))
    out: list[tuple[Path | None, float, str]] = []
    points = [actor_roots(cut, palettes, roots) for cut in cuts]
    # One room for the whole event when it fits: listed rooms ordered by
    # aggregate fit over all cuts; ties go to the gallery's room, then the
    # table order (the sort is stable).
    if len(listed) > 1:
        total = {pac: sum(floor_fit(pac, p) * len(p) for p in points) for pac in listed}
        gallery = GALLERY_ROOMS.get(event.upper())
        listed.sort(key=lambda pac: (-total[pac], pac.stem != gallery))
    for cut, pts in zip(cuts, points):
        if not pts:
            out.append((None, 0.0, "no actors"))
            continue
        if listed and floor_fit(listed[0], pts) >= 0.5:
            out.append((listed[0], floor_fit(listed[0], pts), "executable"))
            continue
        best, fit = None, -1.0
        for pac in listed:
            f = floor_fit(pac, pts)
            if f > fit + 1e-9:
                best, fit = pac, f
        method = "executable"
        if best is None or fit < 0.5:
            # Not in the listed room(s): search every room, accept a clear fit.
            alt, alt_fit = None, -1.0
            for pac in everything:
                f = floor_fit(pac, pts)
                if f > alt_fit + 1e-9:
                    alt, alt_fit = pac, f
            if alt is not None and alt_fit >= 0.9 and alt_fit > fit:
                best, fit, method = alt, alt_fit, ("floor fit (neighbouring room)" if listed
                                                   else "floor fit (event not in executable table)")
            elif best is None:
                method = "unresolved"
        out.append((best, max(fit, 0.0), method))
    resolved = [i for i, (p, _f, _m) in enumerate(out) if p is not None]
    for i, (pac, fit, method) in enumerate(out):
        if pac is None:
            if resolved:
                j = min(resolved, key=lambda k: abs(k - i))
                out[i] = (out[j][0], 0.0, "inherited from neighbouring cut")
            elif listed:
                out[i] = (listed[0], 0.0, "executable (camera only)")
    return out
