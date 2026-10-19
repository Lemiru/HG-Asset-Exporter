"""Foot controls: motion-bank controller tracks -2..-6 (docs/FORMATS.md).

* -2 / -3 (codec 1): model-space positions of the two foot bones, the same
  values the skeleton gives (within the 1/256 storage step; i16 storage, so
  values beyond +-128 units wrap around) in every clip except a few, so they
  are exported only where they differ.
* -4 (codec 1): ground-contact flags, x = foot of -2, y = foot of -3, z = an
  extra flag set for whole clips in two DB rig clips (meaning unknown).
* -5 / -6: Hewie's contact flags, hind paws (x, y) and front paws (x, y);
  these rigs have no point tracks.

The game's contact query 0x1F8B50 samples the flag track of a foot pair and
treats a component > 0 as "planted"; leg IK (Hewie: 0x1F8450, chains on
bones 8-11, 12-15, 21-23, 26-28) then fits planted feet to the floor. A flag
is 1 exactly while the foot rests on the ground in the authored motion.
"""
from __future__ import annotations

from ..formats.motion import Group, MotionBank
from ..formats.pck import PckModel
from ..util.mathutil import euler_to_quat, mat4_mul, trs_matrix

FOOT_NOTE = ("controller tracks -2..-6: 'foot_contact' = {bone: [[first, last frame], ...]} while the foot is "
             "planted (the game fits planted feet to the floor with leg IK); 'foot_points' = {bone: [[frame, x, y, z], "
             "...]} authored model-space foot positions, listed only for the frames where they differ from the "
             "skeleton's own bone position (elsewhere they are identical)")
POINT_TARGETS = (-2, -3)
# Hewie rigs (HEW/HED/HEG): ends of the leg IK chains set up by 0x1F8450.
HEWIE_PAWS = {(-5, 0): 11, (-5, 1): 15, (-6, 0): 23, (-6, 1): 28}
HEWIE_CHAINS = ((8, 9, 10, 11), (12, 13, 14, 15), (21, 22, 23), (26, 27, 28))
TOLERANCE = 0.02            # game units; points are stored in 1/256 steps


def intervals(values: list[bool]) -> list[list[int]]:
    out: list[list[int]] = []
    for f, on in enumerate(values):
        if on and out and out[-1][1] == f - 1:
            out[-1][1] = f
        elif on:
            out.append([f, f])
    return out


def _same(a, b) -> bool:
    """Equal within the storage step, modulo the i16/256 range (points beyond
    +-128 units wrap around, e.g. actors placed far out in cutscene rooms)."""
    return all(abs((x - y + 128.0) % 256.0 - 128.0) < TOLERANCE for x, y in zip(a, b))


def _samples(track, n: int) -> list[tuple]:
    return track.samples if len(track.samples) == n else track.samples[:1] * n


class FootControls:
    def __init__(self, model: PckModel, names: list[str], scale: float):
        self.model, self.names, self.scale = model, names, scale
        self.points: dict[int, int] = {}                 # point target -> bone
        self.contacts: dict[tuple[int, int], int] = {}  # (flag target, component) -> bone
        bones = model.bones
        if len(bones) > 28 and all(bones[c[k]].parent == c[k - 1] for c in HEWIE_CHAINS for k in range(1, len(c))):
            self.contacts.update(HEWIE_PAWS)
        if model.motion:
            self._detect(model.motion)

    # ------------------------------------------------------------- skeleton
    def _chain(self, bone: int) -> list[int]:
        out = []
        while bone >= 0:
            out.append(bone)
            bone = self.model.bones[bone].parent
        return out[::-1]

    def _joint_values(self, groups: list[Group], n: int) -> dict[int, tuple]:
        pal = self.model.palette
        out: dict[int, tuple] = {}
        for g in groups:
            for t in g.tracks:
                if 0 <= t.target < len(pal) and pal[t.target] not in out:
                    out[pal[t.target]] = (t.codec, t.has_translation, _samples(t, n))
        return out

    def _position(self, bone: int, values: dict[int, tuple], f: int) -> tuple[float, float, float]:
        m = None
        for b in self._chain(bone):
            rest = self.model.bones[b]
            euler, trans = rest.euler, rest.translation
            if b in values:
                codec, has_t, sm = values[b]
                v = sm[f]
                if codec == 1:
                    trans = v[:3]
                else:
                    euler = v[:3]
                    if has_t:
                        trans = v[3:6]
            local = trs_matrix(trans, euler_to_quat(*euler))
            m = local if m is None else mat4_mul(m, local)
        return (m[12], m[13], m[14])

    def _vote(self, g0: Group, joints: list[Group], votes: dict[int, dict[int, int]]) -> None:
        if not joints or any(g.frames != g0.frames for g in joints):
            return
        tracks = {t.target: t for t in g0.tracks}
        moving = [t for t in votes if t in tracks and not tracks[t].constant]
        if not moving:
            return
        n = g0.frames
        values = self._joint_values(joints, n)
        frames = sorted({0, n // 2, n - 1})
        for target in moving:
            sm = _samples(tracks[target], n)
            for bone in range(len(self.model.bones)):
                if all(_same(self._position(bone, values, f), sm[f]) for f in frames):
                    votes[target][bone] = votes[target].get(bone, 0) + 1
                    break

    def _adopt(self, votes: dict[int, dict[int, int]]) -> None:
        for target, v in votes.items():
            if v and target not in self.points:
                self.points[target] = max(v, key=v.get)
                self.contacts[(-4, POINT_TARGETS.index(target))] = self.points[target]

    def _detect(self, bank: MotionBank) -> None:
        """Point bone per target: the bone whose position matches the track in
        moving clips (identical in all shipped rigs except a few clips)."""
        votes: dict[int, dict[int, int]] = {t: {} for t in POINT_TARGETS}
        for entry in bank.entries:
            if entry.slots[0]:
                self._vote(entry.slots[0], [g for g in entry.slots[1:] if g], votes)
            if all(sum(v.values()) >= 3 for v in votes.values()):
                break
        self._adopt(votes)

    # --------------------------------------------------------------- export
    def info(self) -> dict:
        flags: dict[str, list] = {}
        for (target, k), bone in sorted(self.contacts.items(), reverse=True):
            flags.setdefault(str(target), [None, None])[k] = self.names[bone]
        if self.points:
            flags.setdefault("-4", [None, None]).append("unknown")
        return {"note": FOOT_NOTE,
                "points": {str(t): self.names[b] for t, b in sorted(self.points.items(), reverse=True)},
                "contact_flags": flags}

    def clip_extras(self, controls: Group | None, joints: list[Group], n: int) -> dict:
        """Clip extras for one motion entry of ``n`` frames."""
        if controls is None:
            return {}
        tracks = {t.target: t for t in controls.tracks}
        unmapped = [t for t in POINT_TARGETS if t in tracks and t not in self.points]
        if unmapped:            # rig whose own clips have no point tracks (e.g. cutscene-only data)
            votes: dict[int, dict[int, int]] = {t: {} for t in unmapped}
            self._vote(controls, joints, votes)
            self._adopt(votes)
        out: dict = {}
        contact: dict[str, list] = {}
        for (target, k), bone in self.contacts.items():
            if target in tracks:
                spans = intervals([s[k] > 0 for s in _samples(tracks[target], n)])
                if spans:
                    contact[self.names[bone]] = spans
        for target in (-4, -5, -6):
            if target not in tracks:
                continue
            for k in range(3):
                if (target, k) in self.contacts:
                    continue
                spans = intervals([s[k] > 0 for s in _samples(tracks[target], n)])
                if spans:
                    contact[f"{target}.{'xyz'[k]}"] = spans
        if contact:
            out["foot_contact"] = contact
        points: dict[str, list] = {}
        values = None
        for target in POINT_TARGETS:
            if target not in tracks:
                continue
            sm = _samples(tracks[target], n)
            bone = self.points.get(target)
            frames = range(n)
            if bone is not None and joints and all(g.frames == n for g in joints):
                values = values or self._joint_values(joints, n)
                frames = [f for f in frames if not _same(self._position(bone, values, f), sm[f])]
            if frames:
                key = self.names[bone] if bone is not None else str(target)
                points[key] = [[f] + [round(c * self.scale, 5) for c in sm[f][:3]] for f in frames]
        if points:
            out["foot_points"] = points
        return out
