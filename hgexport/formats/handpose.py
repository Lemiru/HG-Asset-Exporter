"""Hand poses driven by MRK marker bits 4 and 8 (gameplay clips).

Gameplay clips carry no hand-mixer controller tracks (cutscenes use -11 /
-12); instead the game's per-frame routine 0x2DCB40 reads the clip's MRK
bytes for the current frame and the next four, and drives the two hand
mixers of the character's morph state (+56/+60/+64 = hand descriptor 1, the
-11 layout; +72/+76/+80 = descriptor 2, -12): ``shape A, shape B, blend``,
mixed as ``shape[A] * (1 - blend) + shape[B] * blend``.

* Bit 4 toggles hand 1, bit 8 toggles hand 2 (state flags +2140 / +2141).
  At clip time 0 both hands reset to the default pose (shape 1, set by the
  shared initialiser 0x1F1FE0) and the toggles clear.
* Each clip has a type byte per lookup row in a per-character table in the
  executable (controller +2164, rows found by animation ID via 0x1F4710):
  high nibble = hand 1, low nibble = hand 2. The type selects a five-step
  ramp in the table at 0x4157A0: types 7..15 blend from shape 0 to shape
  ``type - 6`` (type 8: 0 -> 1 -> 2), types 1..6 hold shape 0.
* For the nearest marked frame ``k`` frames ahead (k = 0..4, later k win),
  the ramp step is ``4 - k`` while the toggle is off and ``k`` while it is
  on (so the pose eases in over the four frames before a marker and out
  before the next one); the toggle flips on the marked frame itself.

Only the Fiona costume sets (table 0x3D5CC0, 120 rows) and Debilitas (DB0-2,
table 0x3D89A0, 85 rows) have such tables; elsewhere bits 4/8 are not used
by this routine.
"""
from __future__ import annotations

DEFAULT_SHAPE = 1
# type byte per lookup row (executable tables at 0x3D5CC0 / 0x3D89A0, byte 2 of 6-byte rows)
HAND_TYPES = {
    "fiona": bytes.fromhex(
        "000000000000000000000000002600000000220202202022222222000000002220022222022022000000000000000000"
        "101010100000220000224444220005022020202222222222221510101001012100002222222222002222222222222222"
        "226622262222222244444444000000000000000022000000"),
    "debilitas": bytes.fromhex(
        "000b000000000000000000000000000000000000080880007777777777777777900990009999000000000000000000"
        "000000000e000000ee0000000000009000000000000000000000000000000000000000000000"),
}
FAMILY_TABLE = {**{f: "fiona" for f in ("FIB", "FIC", "FIF", "FIH", "FIN", "FIO")},
                **{f: "debilitas" for f in ("DB0", "DB1", "DB2")}}


def _ramp(t: int) -> list[tuple[int, int, float]]:
    """Table 0x4157A0: five (shape A, shape B, blend) steps per type."""
    if t == 0:
        return [(0, 0, 0.0)] * 5
    if t == 1:
        return [(0, 0, w) for w in (0.0, 0.5, 0.0, 0.5, 1.0)]
    if t == 8:
        return [(0, 2, 0.0), (0, 1, 0.5), (1, 1, 1.0), (1, 2, 0.5), (2, 2, 1.0)]
    b = t - 6 if t >= 7 else 0
    return [(0, b, k / 4) for k in range(5)]


RAMPS = [_ramp(t) for t in range(16)]


def hand_table(model_name: str) -> bytes | None:
    return HAND_TYPES.get(FAMILY_TABLE.get(model_name[:3].upper(), ""))


def simulate(marks: bytes, frames: int, type_byte: int) -> list[list[tuple[int, int, float]]]:
    """Per frame, the (shape A, shape B, blend) of hand 1 and hand 2."""
    types = (type_byte >> 4, type_byte & 15)
    state = [[DEFAULT_SHAPE, DEFAULT_SHAPE, 0.0], [DEFAULT_SHAPE, DEFAULT_SHAPE, 0.0]]
    toggled = [False, False]
    out = []
    for f in range(frames):
        for side, bit in ((0, 4), (1, 8)):
            t, flag = types[side], toggled[side]
            for k in range(5):
                at = f + k
                if at >= frames or at >= len(marks) or not marks[at] & bit:
                    continue
                a, b, w = RAMPS[t][k if flag else 4 - k]
                state[side] = [a if (t < 7 or a) else DEFAULT_SHAPE, b if (t < 7 or b) else DEFAULT_SHAPE, w]
                if k == 0:
                    flag = not flag
            toggled[side] = flag
        out.append([tuple(state[0]), tuple(state[1])])
    return out


def hand_weights(poses: list[list[tuple[int, int, float]]]) -> list[list[dict[int, float]]]:
    """Per frame and hand: {shape index: weight} (shape 0 = the base mesh)."""
    rows = []
    for frame in poses:
        hands = []
        for a, b, w in frame:
            weights: dict[int, float] = {}
            weights[a] = weights.get(a, 0.0) + (1.0 - w)
            weights[b] = weights.get(b, 0.0) + w
            hands.append(weights)
        rows.append(hands)
    return rows
