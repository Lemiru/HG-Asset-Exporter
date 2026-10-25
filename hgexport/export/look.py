"""The rooms' screen "look": tint, glow geometry and bloom, and the story
states that switch them.

Every room file (PAC slot 13) holds a **tint** (two RGBA colours and a mode)
and most a **bloom** colour. The game draws them over the finished 3D picture
in display values (0-255; colours and alphas use 128 = 1.0, the effects take
alpha / 2 as their strength):

* tint, layer 0x20, once per colour that has alpha (Tint_Draw, packet built by
  0x2685F0): the frame is point-sampled to half size, and the half-size copy
  plus a quarter of each of 8 copies shifted by (+-1, +-1), (+-2, 0), (0, +-2)
  half-size pixels makes a glow buffer G (about 3 x a blur of the frame,
  clamped at 255). Colour 1 adds G x colour1 x alpha1/2 to the frame; colour 2
  adds (frame - G x colour2) x alpha2/2 (more local contrast, and darker for
  bright colour-2 values). The second G is taken after the first colour.
* fog, layer 0x21 (see the Blender guide).
* layer 0x25 clears the frame's alpha and turns on FBA (every pixel drawn
  gets alpha >= 0x80); layer 0x26 draws the **glow geometry** - room geometry
  group 2 and room objects whose name starts with "g_" - over the tinted,
  fogged picture (so lamps keep their own colour); 0x27 turns FBA off.
  Characters are drawn before 0x25, so their shine (export/shine.py) never
  reaches the bloom.
* bloom, layer 0x28 (ScreenBlend_Draw -> Bloom_Start, packet 0x2699D0): the
  frame's pixels with alpha != 0 (the glow geometry) are point-sampled to half
  size (A), B = 0.5 x the sum of A shifted by (+-1, +-1), (+-2, 0), (0, +-2),
  then A += 0.125 x the sum of B shifted by (+-3, 0), (0, +-3), (+-2, +-2), and
  A scaled back up (bilinear) adds A x colour x alpha/2 to the frame. Modes 1
  and 4 subtract and 2-4 pulse fixed colours; no room or script uses them.

Room event scripts (compiled into the program) replace the tint (command 0x33
to effect 0x1F) and the bloom (command 0x9B) when a room is entered, under a
story condition. The rooms' own tints are the night look; the variants below
are a warm look used at the start of the story. The values were read from the
rooms' enter scripts.

The script that plays a cutscene can also set them, before the cutscene or
right after its wait for the cutscene to start (still before its first
frame): EV0033's story script does. The movie gallery (room 0x37) switches on
the entry (progress variable 0x2A) to the event's room (command 0x3A) and
runs one of that room's actions (command 0x92, a shared switch at 0x3D6C18):
a replay script that plays only that cutscene. The rooms' enter scripts do
not run there, so the gallery shows each room's own look, changed only by
what its replay script sets (the warm tint in eleven events of the first
area, the blue bloom in DE0003 and DE0011, EV0033's look).
"""
from __future__ import annotations

_WARM = {"colors": [[132, 96, 60, 80], [36, 42, 42, 70]], "mode": 0}
_WARM_DEEP = {"colors": [[172, 96, 38, 80], [25, 31, 18, 128]], "mode": 0}
_BLUE_BLOOM = {"color": [128, 192, 242, 170], "mode": 0}

_EV0033 = {"tint": {"colors": [[68, 78, 108, 100], [102, 108, 96, 70]], "mode": 0},
           "bloom": {"color": [176, 144, 200, 186], "mode": 0}}

# condition: story flag n not set yet / story phase variable 0x2E == v (enter
# scripts, so story only); story_events / gallery_events: the script that
# plays one of these events in the story / the gallery's replay script
_EARLY = {"story_flag_clear": 2}

# Events whose gallery replay script sets the warm tint (room -> events).
_GALLERY_WARM = {0x09: ["DE001B", "DE0010"], 0x0F: ["EV0004"], 0x12: ["DE000F"], 0x20: ["DE000E"],
                 0x21: ["EV0002", "EV0003"], 0x23: ["DE0000"], 0x24: ["DE0001", "DE0002"]}


def _variants() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}

    def add(room, variant):
        out.setdefault(f"ST_{room:03X}", []).append(variant)
    for room, events in _GALLERY_WARM.items():
        add(room, {"name": "warm", "when": {"gallery_events": events}, "tint": _WARM})
    add(0x25, {"name": "warm", "when": {"gallery_events": ["DE000A"]}, "tint": _WARM_DEEP})
    add(0x00, {"name": "blue_bloom", "when": {"gallery_events": ["DE0003", "DE0011"]}, "bloom": _BLUE_BLOOM})
    add(0x35, {"name": "EV0033", "when": {"story_events": ["EV0033"], "gallery_events": ["EV0033"]}, **_EV0033})
    for room in (0x09, 0x0F, 0x10, 0x11, 0x12, 0x14, 0x20, 0x22, 0x23, 0x24):
        add(room, {"name": "warm", "when": _EARLY, "tint": _WARM})
    for room in (0x01, 0x13, 0x25, 0x2A, 0x2B, 0x2D):
        add(room, {"name": "warm", "when": _EARLY, "tint": _WARM_DEEP})
    add(0x00, {"name": "warm", "when": _EARLY, "tint": _WARM_DEEP, "bloom": _BLUE_BLOOM})
    add(0x21, {"name": "warm", "when": {"story_flag_clear": 1}, "tint": _WARM})
    for room in range(0xE0, 0xE6):
        add(room, {"name": "warm", "when": {"story_phase": 1}, "tint": _WARM})
    add(0xE9, {"name": "warm", "when": {"story_phase": 1}, "tint": _WARM_DEEP, "bloom": _BLUE_BLOOM})
    return out


ROOM_VARIANTS = _variants()

# Story flag 2 is set when Fiona first leaves room 0x21 after EV0002 (its
# action script 8); flag 1 by an examine event in room 0x12 (action script 4).
# Only these cutscenes play before them.
_STORY_START = {"EV0002", "EV0003"}

LOOK_NOTE = ("room_effects.tint / .bloom: the room's own screen look (RGBA, 128 = 1.0; the effects use alpha/2 "
             "as strength), drawn in display values over the 3D picture (export/look.py, docs/FORMATS.md "
             "'Screen look'). script_variants: replacements the room's enter script makes under a story "
             "condition (story_flag_clear: n = before story flag n is set; story_phase: v = phase variable "
             "0x2E is v; these enter-script conditions hold only in the story) or for some events "
             "(story_events: the script that plays the event in the story sets it; gallery_events: the "
             "movie gallery's replay script does - the gallery shows the room's own values otherwise). "
             "The room's own values are the night look. engine_look: the same for engines (the tint "
             "as a grade curve with a detail gain, and a scene-linear bloom of the emissive lamps), for the night "
             "look and each variant (look.engine_look).")


def room_variants(stage_name: str) -> list[dict]:
    return ROOM_VARIANTS.get(stage_name.upper(), [])


def cutscene_story_state(event: str) -> dict:
    """The story flags (1, 2) and phase a cutscene plays with in the story."""
    if event.upper() in _STORY_START:
        return {"event": event.upper(), "story_flags": [], "story_phase": 0}
    return {"event": event.upper(), "story_flags": [1, 2], "story_phase": 0}


def gallery_state(event: str) -> dict:
    """The state the movie gallery plays a cutscene in."""
    return {"event": event.upper(), "gallery": True}


def condition_holds(when: dict, state: dict) -> bool:
    event = state.get("event")
    if state.get("gallery"):
        return event in when.get("gallery_events", ())
    if "story_events" in when:
        return event in when["story_events"]
    if "story_flag_clear" not in when and "story_phase" not in when:
        return False                    # gallery only
    flags = set(state.get("story_flags", []))
    if "story_flag_clear" in when and when["story_flag_clear"] in flags:
        return False
    if "story_phase" in when and when["story_phase"] != state.get("story_phase"):
        return False
    return True


# Blur sizes of the game's passes, in fractions of the picture width: the
# tint's glow buffer (Gaussian sigma about 1.1 half-size pixels of the
# 256-pixel buffer) and the bloom's spread (sigma about 2.3). The engine's lamp
# bloom is a chain of Gaussian levels (weights 0.6 / 0.3 / 0.1 at sigma,
# 2 sigma, 4 sigma), like the game's two-pass spread with its wider tail; its
# first sigma is set so that half its energy lies within the same radius as
# the game's (r50 = 1.5379 sigma against 1.1774 for one Gaussian).
BLOOM_LEVELS = ((0.6, 1.0), (0.3, 2.0), (0.1, 4.0))
_TINT_SIGMA, _BLOOM_SIGMA = 1.1 / 256.0, 2.3 / 256.0
BLOOM_RADIUS = 1.1774 * _BLOOM_SIGMA
LAMP_EMISSION = 4.0          # glow geometry emission (scene-linear), the export's --emissive-glow default
LAMP_THRESHOLD = 1.0
GRADE_SAMPLES = 33


def tint_grade(tint: dict | None, v: float) -> list[float]:
    """The tint's response F(v) per channel for a flat area of display value
    v (0-1), clamped like the game: each glow buffer G = min(3 blur, 1) and
    each G x colour stop at 1, and the frame stays within 0-1 after each
    pass."""
    colors = (tint or {}).get("colors")
    if not colors:
        return [v] * 3
    (r1, g1, b1, a1), (r2, g2, b2, a2) = colors
    k1, k2 = a1 / 256.0, a2 / 256.0
    out = []
    for c1, c2 in zip((r1, g1, b1), (r2, g2, b2)):
        s1 = min(v + k1 * min(min(3.0 * v, 1.0) * c1 / 128.0, 1.0), 1.0)
        s2 = s1 + k2 * (s1 - min(min(3.0 * s1, 1.0) * c2 / 128.0, 1.0))
        out.append(min(max(s2, 0.0), 1.0))
    return out


def engine_look(tint: dict | None, bloom: dict | None) -> dict:
    """The room's look for engines: the tint as a grade with local contrast,
    and a bloom of the emissive lamps.

    The tint's two passes (blur(blur) taken as blur) come to

        S' = F(B) + detail_gain (S - B),   B = gaussian(S, tint_sigma)

    per channel in display values (0-1): flat areas follow the response
    curve F (tint_grade, sampled at GRADE_SAMPLES even steps: usable as a
    colour-grading LUT) and detail finer than the blur is scaled by
    detail_gain = 1 + k2. No gain, bloom and sharpen in linear light can
    stand in for it: the glow buffers' clamps bend F (in a room like 0x0C
    the second pass stops darkening above display value 0.16), and the sRGB
    curve turns a display gain into a tone-dependent linear one. An engine
    applying a LUT to the picture itself gets F(S), which differs only in
    the detail's contrast (F' instead of detail_gain).

    The lamps' bloom is matched to the game's at the lamp's edge (where the
    game adds about 2 kb cb of the lamp's colour, with linear light taken
    as display value squared), for lamps emitting LAMP_EMISSION x their
    colour over a threshold of 1."""
    out: dict = {"note": "tint: display' = grade(blur) + detail_gain x (display - blur) per channel, display values "
                         "0-1, blur = gaussian(display, tint_sigma); grade: F at display values i / (n - 1). "
                         "lamps: scene-linear bloom of emission above lamp_bloom_threshold. Radii are half-energy "
                         "radii and sigmas Gaussian standard deviations, as fractions of the picture width; "
                         "lamp_bloom_levels: (weight, sigma multiple) of each blur level"}
    curve = [tint_grade(tint, i / (GRADE_SAMPLES - 1)) for i in range(GRADE_SAMPLES)]
    out["grade"] = [[round(c, 4) for c in rgb] for rgb in curve]
    colors = (tint or {}).get("colors")
    out["detail_gain"] = round(1.0 + colors[1][3] / 256.0, 4) if colors else 1.0
    out["tint_sigma"] = round(_TINT_SIGMA, 5)
    lamp = [0.0] * 3
    if bloom and bloom.get("color") and not bloom.get("mode"):
        r, g_, b_, a = bloom["color"]
        kb = a / 256.0
        lamp = [2.0 * (2.0 * kb * c / 128.0) ** 2.0 for c in (r, g_, b_)]
    lpeak = max(lamp)
    out["lamp_emission"] = LAMP_EMISSION
    out["lamp_bloom_threshold"] = LAMP_THRESHOLD
    out["lamp_bloom_tint"] = [round(v / lpeak, 4) if lpeak else 1.0 for v in lamp]
    out["lamp_bloom_intensity"] = round(lpeak, 4)
    out["lamp_bloom_levels"] = [list(lv) for lv in BLOOM_LEVELS]
    out["lamp_bloom_radius"] = round(BLOOM_RADIUS, 5)
    out["lamp_bloom_sigma"] = round(BLOOM_RADIUS / 1.5379, 5)
    return out


def engine_looks(effects: dict, variants: list[dict]) -> dict:
    """engine_look for the room's own values ("night") and each script variant."""
    looks = {"night": engine_look(effects.get("tint"), effects.get("bloom"))}
    for v in variants:
        looks[v["name"]] = engine_look(v.get("tint", effects.get("tint")), v.get("bloom", effects.get("bloom")))
    return looks


def room_look(effects: dict, variants: list[dict], state: dict | None) -> dict:
    """The tint and bloom a room shows in story state `state` (None: the room's
    own values)."""
    look = {"tint": effects.get("tint"), "bloom": effects.get("bloom")}
    for v in variants if state is not None else ():
        if condition_holds(v["when"], state):
            look.update({k: v[k] for k in ("tint", "bloom") if k in v})
            break
    return look
