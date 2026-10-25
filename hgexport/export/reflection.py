"""Character reflections in windows, mirrors and floors.

Some rooms' own code (not the event scripts) registers a reflection effect
(method table 0x472F60) in effect slot 26 or 27 of the room-effects manager,
with a parameter block: four corners of a quad (game units, the same space
as the room), a strength (0-128, 0x80 = 1) and flag bits, and 1.0 for the
horizontal ones (floors). Each frame the effect

1. copies the picture drawn so far, point-sampled, into a 256 x 224 buffer
   (colour only; its alpha is cleared to 0) and clears a depth buffer of
   its own;
2. draws the characters it accepts into it, mirrored in the quad's plane
   (the image is drawn through a mirrored camera and flipped back at the
   end), tested only against each other, so the room never hides them;
3. softens the buffer: a copy blended with four copies shifted by one
   half-size pixel diagonally at 32/128 each in turn, then the sharp
   characters blended back at 96/128 where they were drawn;
4. blends it over the frame at strength/128, only where the characters were
   drawn (texture alpha test != 0), before the tint and the fog.

The game does not clip the reflection to the quad. It accepts a character
(Reflection_CharSlot 0x317920) when bone 0, projected through the mirrored
camera to the 512 x 448 frame (so where its mirror image lands), with a box
of +-10 x +-30 pixels around it for Fiona (character slot 0), +-20 x +-15
for Hewie (slot 1) and +-20 x +-30 for the others, overlaps the rectangle
around the projected quad corners.

The parameter blocks were read from the rooms' code (constants stored before
the register call 0x266C70); when the effect is live depends on the room's
code and the event, so cutscene extras carry measured frame ranges.
"""
from __future__ import annotations

_WINDOW = [[0.491, 22.0, 69.502], [9.502, 22.0, 60.491], [0.491, 0.0, 69.502], [9.502, 0.0, 60.491]]
_DOOR = [[40.491, 30.0, -10.498], [49.502, 30.0, -19.509], [40.491, 1.5, -10.498], [49.502, 1.5, -19.509]]

# room -> [(effect slot, corners, strength, flag bits (top nibble of the strength word), horizontal)]
_ROOMS: dict[int, list[tuple]] = {
    0x21: [(26, _DOOR, 64, 2, False), (27, _WINDOW, 64, 1, False)],
    0x32: [(26, _DOOR, 64, 2, False), (27, _WINDOW, 64, 1, False)],
    0xE0: [(26, _DOOR, 64, 2, False), (27, _WINDOW, 64, 1, False)],
    0x31: [(27, _WINDOW, 64, 0, False)],
    0x49: [(27, [[-43.0, 30.053, -15.124], [-43.0, 30.053, 5.068], [-43.0, 10.053, -15.124],
                 [-43.0, 10.053, 5.068]], 64, 0, False)],
    0x4B: [(27, [[-63.65, 21.0, 104.5], [-55.65, 21.0, 104.5], [-63.65, 4.0, 104.5], [-55.65, 4.0, 104.5]],
            128, 0, False)],
    0x4E: [(27, [[-44.0, 71.0, 60.0], [-36.0, 71.0, 60.0], [-44.0, 54.0, 60.0], [-36.0, 54.0, 60.0]],
            128, 0, False)],
    0x2E: [(27, [[-15.959, 111.0, -2.0], [-15.959, 111.0, 6.0], [-15.959, 91.0, -2.0], [-15.959, 91.0, 6.0]],
            128, 0, False)],
    0x55: [(27, [[30.0, 45.0, -30.0], [-30.0, 45.0, -30.0], [30.0, 45.0, 30.0], [-30.0, 45.0, 30.0]],
            128, 0, True)],
    0x60: [(27, [[10.0, -0.2, -10.0], [-10.0, -0.2, -10.0], [10.0, -0.2, 10.0], [-10.0, -0.2, 10.0]],
            128, 0, True)],
    0x6A: [(27, [[120.0, 21.0, 47.0], [120.0, 21.0, 39.0], [120.0, 4.0, 47.0], [120.0, 4.0, 39.0]],
            128, 0, False)],
}

REFLECTION_NOTE = ("reflections: quads the room's code registers as reflection effects (effect slot, corners in "
                   "the room's space, strength 0-128, flag bits, horizontal). The game draws the characters "
                   "mirrored in the quad's plane over the picture at strength/128 where they are drawn, softened, "
                   "at half resolution, without clipping them to the quad, for characters whose root bone is near "
                   "the quad on screen (export/reflection.py).")

# Live frame ranges measured in the movie gallery (slot -> [[first, last], ...]): the effect's slot
# watched while each event in these rooms played. EV0002 and DE002F have none; in EV0017 room 0x60's
# floor is live at strength 0 (not drawn), in EV0018 at 128 until frame 840.
EVENT_REFLECTIONS: dict[str, dict[int, list[list[int]]]] = {
    "EV0003": {26: [[0, 2310]], 27: [[0, 2310]]},
    "EV0005": {27: [[2006, 2084]]},
    "EV0007": {26: [[0, 2017]], 27: [[0, 2017]]},
    "EV0008": {26: [[0, 1929]], 27: [[0, 1929]]},
    "EV0015": {26: [[0, 4523]], 27: [[0, 4523]]},
    "EV0018": {27: [[0, 840]]},
    "DE001D": {27: [[0, 1409]]},
    "DE002B": {27: [[810, 1499]]},
    "DE002D": {27: [[0, 1004]]},
}


def room_reflections(stage_name: str, scale: float) -> list[dict]:
    """The room's reflection quads in export units."""
    try:
        room = int(stage_name.upper().removeprefix("ST_"), 16)
    except ValueError:
        return []
    return [{"slot": slot, "corners": [[round(c * scale, 5) for c in p] for p in corners], "strength": strength,
             "flags": flags, "horizontal": horizontal}
            for slot, corners, strength, flags, horizontal in _ROOMS.get(room, [])]


def event_reflections(event: str) -> list[dict]:
    return [{"slot": slot, "frames": frames} for slot, frames in sorted(EVENT_REFLECTIONS.get(event.upper(), {}).items())]
