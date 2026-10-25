# How the exports were checked

An exporter for a game's private formats can easily produce files that load
fine but are subtly wrong: a texture with a shifted palette, a skeleton bent
by a few degrees, animations at the wrong speed. This document lists how
each part of hgexport was checked against something independent, and what
differences from the game are still known.

## What the exports were compared with

* **Noesis**, a model viewer with its own Haunting Ground support: reference
  exports of Hewie (`HEW_000`) and Fiona (`FIS_000`) for textures, meshes,
  skeletons and animations.
* **FFmpeg**, for the ADX audio and SFD movie formats.
* **The game itself:** an in-game screenshot, a video capture of all
  cutscenes, and recordings and memory snapshots made in the PCSX2 emulator.
* **The whole game:** every file of a type must decode consistently (for
  example, all 831 cutscene timelines must agree with their animation
  files), which catches mistakes a single example would hide.
* **Blender** (5.2), importing and rendering the exports.
* **The Khronos glTF validator**, the official checker for glTF files.

## File formats

| Area | Check | Result |
| --- | --- | --- |
| Textures (palette order, 4-bit pixel order, alpha scale) | Compared with Noesis for all of Hewie's and Fiona's sheets | Pixel-identical |
| Model geometry (positions, UVs, triangle strips) | Compared with Noesis | Positions and UVs bit-exact, identical triangles |
| Triangle orientation | Compared with the stored normals over all 329,042 triangles | 328,659 agree (better than Noesis) |
| Skeletons (parents, rotation order, positions) | Compared with Noesis | Rest pose identical to 1e-7 on every bone |
| Animation formats and speed | Compared with Noesis; frame counts from the data layout; cutscene length against its audio | Values agree within Noesis' own rounding; 30 frames per second |
| Extra animation files (MTN) | Parsed all 35 banks | All consistent |
| Faces and hands (blend shapes) | Cutscene face and hand controls against the shapes | Consistent; one control drives face and teeth together, as in the game |
| Rooms (geometry, placement, visibility groups, doors, objects) | Rendered a room through its own gameplay camera next to an in-game screenshot | Framing, geometry, textures and lighting match almost pixel for pixel |
| Cutscene timelines and animation | All 831 timeline/animation pairs | Frame counts and actors agree everywhere |
| ADX audio | Compared with FFmpeg | Bit-identical |
| Music (MIDI, SoundFont) | Structure, sample and loop bounds of every bank; loop markers | Valid; rendered music checked by spectrogram only |
| Animation markers (MRK) | All 3,782 records | Every record is as long as its animation (rounded up to 4) |
| Cutscene flags and settings (MARK.BIN, PARAMS.BIN) | All files; cut flags against the timelines | 743 of 770 cut flags fall exactly on a shot start; all 1,047 settings records decode |
| Foot data | Foot positions in the data against the posed skeleton for every animation | Match within the storage precision (0.006 units), apart from Fiona's animations 1802/1803 and two cutscene frames; contact flags are on exactly while the foot is planted |
| GAME_FIX props | Part chains, decoding as the game's draw code does, UVs against the texture | Chains end exactly at the next entry; edge lengths consistent; every UV lands on the painted part of the texture |

## The exports as a whole

* **Full export:** `python -m hgexport all` (1,230 jobs) finishes without
  failures, and every glTF file passes the built-in structural check
  (`python -m hgexport validate`).
* **Khronos glTF validator** (`tools/khronos_validate.js`, over all `.glb`
  files): no errors, warnings or hints. The remaining informational notes
  are by design: unused skins (animation-only files), empty nodes (stand-ins
  and groups) and an extension the validator does not fully check
  (`KHR_animation_pointer`, used for camera zoom).
* **Tests:** `python -m unittest discover -s tests` runs unit tests and, when
  the game data is present, tests against the real files.

## Behaviour checked against the game

* **Frame rate.** The game shows a new frame only every second video field,
  so it runs at 30 frames per second. Animated textures advance one cell per
  frame, and cutscenes match their soundtrack at 30 fps.
* **Camera field of view.** The room render above only matches the
  screenshot when the stored 60° is read as horizontal; cutscene cameras use
  the same convention.
* **Root motion.** Applying Fiona's walk animation's movement track keeps
  each planted foot within a few millimetres of where it touched down.
* **Cutscene rooms.** Each shot's actor positions were tested against the
  room's walkable floor: 733 of 831 shots stand in the listed room, 18 were
  moved to a neighbouring room, 80 camera-only shots follow the previous
  shot, and 30 shots with actors lying down or off the floor keep the listed
  room. Renders of these show everyone in the right place.
* **Animation-only cutscenes.** Assembled in Blender, every bone position
  and face weight is identical to the self-contained export
  (`tools/blender_retarget_check.py`), and renders are identical.
* **Animation variants.** The animation-only exports of Fiona's and Hewie's
  variant sets, applied to the main model, reproduce the variant's own pose
  exactly.
* **Music reverb.** The game switches on reverb at volume 0 and never raises
  it, so the dry renders match the game.
* **Cloth and hair physics.** The decoded simulation matches recordings of
  the running game to within float rounding, and the Blender reproduction
  matches a recorded cutscene to within a millimetre for Fiona. See
  [SECONDARY_MOTION.md](SECONDARY_MOTION.md#how-it-was-checked).
* **Menu drawing.** How the item menu draws its pictures was traced from an
  emulator save state and checked on screenshot pixels (see
  [FORMATS.md](FORMATS.md#how-the-menu-draws-its-textures)).

## Cutscene renders against the game

Cutscenes were assembled with `tools/blender_assemble_cutscene.py`, rendered
in EEVEE and Cycles, and compared with a video capture of the game. The
capture was lined up with each cutscene automatically, by matching its
camera cuts with the cutscene's shot list. Checked: EV0002, EV0003, EV0004,
EV0008, EV0010, EV0014, EV0018, EV0027 and EV1034.

Camera framing, cut timing and which characters are visible in each shot
match. Differences found on the way, and fixed:

* lighting too bright and cold: one room light had a 48 m range (point
  light strength is now matched at a sensible distance), and the per-shot
  lighting was not yet applied;
* jagged see-through triangles on Fiona's dress and hair in EEVEE (now the
  *Dithered* transparency mode);
* hard shadows from hair across faces (the game's lights cast no shadows, so
  they are switched off);
* pre-rendered movie shots missing (the movie is now drawn over the
  picture);
* EV0014's tablecloth rendering black in EEVEE (soft shadow decals lying
  exactly on the cloth; rooms now keep *Blended* and the decals are lifted
  1 mm);
* Daniella's glass shard visible where the game shows none (it is now a
  separate object that cutscenes hide);
* colours darker than the game with Blender's default AgX view (*Standard*
  matches the capture's mid and bright tones).

### Doors, props and fog

Three behaviours of the game's cutscene player were taken from the OpenHuey
decompilation (github.com/ArnautDaniel/OpenHuey) and checked against the
game's own code and the data: doors that cutscenes swing, room objects that
are shown only in their shots, and fog. Rendered: EV0014's dining-room door
opens as Fiona peeks round it, EV0008's door closes behind her, DE0041's door
swings open onto the corridor, and EV0004's event double door opens and
closes as Fiona comes through (the gameplay door in the same doorway is
hidden meanwhile). The first fog version followed the native port's
reading and washed every shot in grey; the game's palette ramp halves the
alpha and runs in 1 / distance. With that, EV0004's dark far corridor
measures 12–24 (on 0–255) against 21–24 in the capture (44–52 before the
correction, 7–13 without fog), and an EV0014 close-up's dark corner, cheek
and hair all move towards the capture's values.

**Skirt.** In EV0004 frames 200–244 Fiona's skirt points match the PCSX2
recording to within 1 mm, the posed skirt bones match the recorded bone
matrices (positions exact, axes within 0.02°), and all 472 vertices of the
skirt meshes, skinned in Blender, match the game's own skinning of the
recorded bones exactly. Where the thigh passes through the skirt, the game's
mesh does the same.

### Screen look (tint, lamps, bloom)

The tint and bloom were read from the GS packets the game builds each frame
(found in memory over PCSX2's PINE interface) and checked on paused frames:
with a test colour, the tint's added light grows linearly with its colour and
alpha, and its gain against the blurred picture is 2.6–2.9 where the decoded
passes give 3. Switching each effect off over PINE showed that only the
rooms' glow geometry feeds the bloom (four close-ups of Fiona and Daniella
gave none) and measured the fog: on EV0002 frame 419 its alpha is 0.32
across the picture in the game and in the Blender assembly alike.

EV0002 frames 419, 1299 and 2339 were captured from the movie gallery and
rendered with each `--look`. On the wide room shot (419), with tint, fog and
bloom off, our picture matches the game's (mean 36 against 36 on 0–255);
with them on, `hybrid` and `emulate` average (57, 49, 43) against the game's
(56, 48, 42). The first version converted the
render to display values with a 2.2 power instead of the sRGB curve the
view transform uses, which made the tint 1.4 times too strong in dark
pictures; fixed.

For `--look modern`, gains, blooms and sharpens in linear light were tried
first, as engines would set them up, against 11 captured frames of four
cutscenes (EV0002 warm, EV0005 blue night, DE000C in room 0x0C's hall,
EV0019): a 2.2 power for display-to-linear made rooms 8 % too bright, the
glow buffers' clamps were missing (bright areas too dark where colour 2
dominates, too bright where colour 1 does) and the best version was still
15–24 levels off in the hall (brown, the chandeliers' blue gone) and lost
5–25 % of the local contrast. The tint is a curve per channel (see FORMATS
"Screen look"), so `modern` keeps the game's tint and changes only the lamps.
Before the light-direction fix below, `hybrid` and `modern` were within
0.3–3 levels of the game's frame means except EV0005's close-ups (6–8 levels
bright); the frames were held over PINE (the director's SetFrame vtable entry pointed
at its GetFrame) and the gallery driven through the pad's button profile in
memory, with no keyboard input.

### Character lighting

With the screen effects and fog off, EV0002's close-ups were 25–45 % brighter
in the Blender assembly than in the game. Forcing the lighting over PINE
(room lights and camera lights off, a known ambient) showed how the game
shades characters: twice the ambient gives twice the screen value, and an
ambient of 128 shows the texture as it is. The game multiplies the texture's
display value by its light sum; Blender's linear lighting made the same
numbers look about twice as bright. With the game's shading
(`--character-shading game`), Fiona's back in frame 1299 measures (54, 47,
42) against the game's (55, 48, 43), and the whole frames 419, 1299 and 2339
with `--look hybrid` average within 2–6 % of the game.

The cut lights' directions had both angles' signs reversed: with one camera
light forced on over PINE (colour 200; ambient, room lights, tint and fog
off), EV0005 frame 399 lit Fiona's back and hair instead of her face and
chest. With the signs flipped (a positive first angle turns the light's
direction down, a positive second to the right) the isolated renders match
the game in EV0005 399 (both lights) and DE000C 199 and 499 (a top-down and
a low shot; per-pixel difference 15 against 74, 3.3 against 5.8, 14 against
36), and the lit chest has the game's brightness (184 against 182). Turning
about the camera's up axis or the world's could not be told apart in these
shots. With the real lighting and no tint or fog, Fiona in EV0005 399 now
measures (57, 53, 40) against the game's (57, 53, 40). Still open: in EV0005
399 the room (the wall at the right) was about 17 % brighter than the
game's before the tint, which the room's tint roughly doubles (fixed by the
display render space below). In DE000C 779 the whole picture, not Fiona,
was at 0.6 of Blender's: see *End of an event* below.

### Render space

The bright wall in EV0005 399 is a translucent copy of the wall (vertex
alpha 59–92 of 128) over a copy with vertex colour 0. Probed pixel by pixel
(ray casts in Blender for the layers, texel and vertex colour), the game
shows the layer's colour times its alpha (66 × 0.53 ≈ 35; captured 33–40),
blended in display values; Blender blended in linear light (46–56). With
every textured vertex colour of the room set to 64 in the game's memory, the
game showed the texture × 0.5 within 1–2 % of Blender's display-value
render, so the modulate itself works on display values; but with the real
vertex colours the rooms were still 5–15 % too bright, from interpolating
dark gradients in linear light. Rendering in display values (vertex colours
stored as display values, textures raw, the GS's rounding down after the
modulate and each screen pass) brought the untinted frames of EV0002,
EV0005 and DE000C from −3…+4 levels to +0.4…+1.3, and with the looks on
the 11 frames of four cutscenes from −2.5…+9.8 to +0.7…+2.6 (DE000C 499
and 779 aside, +5…+8, see below), with the local contrast at 101–108 % of
the game's instead of 108–119 %.

### Fog in front of its start, and the end of an event

DE000C 499 (a low shot with Fiona 0.9–1.0 m from the camera; the cut's fog
starts at 1.1 m) matched the game without the tint and without the fog,
but with both Fiona was 128 against 106: the script left everything in
front of the fog's start unfogged. Solving each pixel of the game's
fog-only and fog-off captures for the fog palette entry gives entry 255
(the near colour at the near alpha) for all of Fiona, so the ramp is
clamped there; with that, the frame is within 3 levels and Fiona within 4.
All 11 frames of the four cutscenes are then within +0.7…+3.6 levels of the
game, local contrast 103–106 %.

The movie gallery fades every event to black at its end: DE000C (795
frames) and EV0019 (1934 frames) are normal 18 frames before the end and
black 4 frames before it, both by the same curve. Captured while playing
(the frame hold stops the event's clock but not the fade), the picture at
frame f is (frames + 2 − f) / 16 of its brightness from 14 frames before the
end; `--end-fade` reproduces it within 0.3 % on DE000C 776–792. Whether the
story plays it too was not checked.

### Event movies

A blind comparison of a PCSX2 recording of DE000C and the Blender assembly
showed the game's lens flare (the event's overlay movie) far fainter than
the movie itself. With the event held over PINE, the movie still plays,
and the difference between grabs shows the flare where the full-frame movie
puts it. The GS packets show how it gets there: the frame is uploaded in
16-pixel strips and drawn in renderer layer 0x2B, after the tint, fog and
bloom, as one sprite over the frame, alpha-blended with the alpha the
decoded frame carries. Read from the game's memory, that alpha follows
the pixel's luma exactly (error 0.3 of 128), with a threshold and slope
that differ between events but not within one: they are the movie player's
luminance key (low, high), 40–200 in DE000C and 50–255 in EV0014, used as
128 × (Y − low) / (high − low) on the decoded luma Y. Reading the key while
each of the gallery's events played gave the table the exporter carries.
The colours match the exported MP4 (mean difference 0.3). With the
assembly blending the movie that way, DE000C frame 150 (where the
flare is bright) comes within 0.3–2.2 levels of the game in the flare's
regions, against 1.5–6.6 without the movie; at frame 60 the flare is too
faint to change them; in EV0014 (steam over a plate, key 50–255) the
pixels the movie touches are 10 levels from the game against 14 without
it (the held game frame runs a few movie frames ahead). EV0005's
pre-rendered insert is drawn in the same layer with
blending off, so inserts replace the picture after the screen effects too.

The letterbox bars cover rows 0–95 and 679–774 of the 775-row captured
picture in every captured frame of the four cutscenes, i.e. 56 of the 448
lines at each edge; the assembly's bars cover the same eighth.

### Time of day in the movie gallery

The gallery showed EV0014 in room 0x11's night look although the story
flags read clear (progress block +0x1C) and the room's enter script sets
the warm tint while flag 2 is clear. Polling the tint slot while the
gallery loaded the event showed it going from the gallery's own look
straight to the room's night values, never warm: the enter scripts do not
run there. Room 0x37 (the gallery) switches on the entry (progress variable
0x2A) to the event's room and runs one of that room's actions through a
shared switch; those actions are short replay scripts at the end of each
room's action list, and some of them set the warm tint (or, in room 0x00,
the blue bloom) before the cutscene. Decoding both switches for all 112
entries and reading the tint and bloom slots while each entry played
(frames 20–80) gave the same answer for every entry that goes through a
room (109 of 109; the FILM entry and the two ending variants of EV0034
are played without the room switch): the room's own values,
replaced only by what the replay script sets. Polling the slots showed
EV0033's tint and bloom set before its first frame; its story script sets
them in the same place as its replay script. The exporter's `gallery` time of day now
follows these scripts, and the room pick prefers the gallery's room when
several rooms fit equally (DE000A plays in room 0x25 there, not 0x22).

### Reflections

Paused at Fiona's reflection in EV0005 (frame 2053), the reflection effect
was live in effect slot 27 with the window quad the room's code builds;
pointing its draw method at a stub removed the reflection and nothing
else. The GS packets of that frame give the passes described in
FORMATS.md. Watching the slot while the event played showed it live only
in cut 9 (frames 2006–2084), and the same watch over the other events in
rooms with reflections gave the exporter's frame ranges. A first assembly
lit the mirrored instances with the scene's lights and came out brighter
than the game (the reflected colour, recovered from the blend, averaged 89
levels against 72, with a wider spread): the game lights the character
before mirroring it, so its front, turned away from the camera lights,
stays dim. With mirrored lights the reflection averages 68 levels
(quartiles 62–73 against the game's 67–77), and Fiona's mirrored hand
meets the real one on the glass where the game puts it. In EV0007 and
EV0018 the effect is live but changes nothing visible in the frames
checked, in the game or the assembly.

### Character shadows

Pointing the shadow object's draw method at a stub (EV0007, held) removed
the floor and bed shadows and nothing else; zeroing each room light's
intensity in turn showed that only light 3 (the fireplace lamp) casts
Debilitas's, although light 4 is also in range. The darkening measured
on the floor (0.735 at frame 900, 0.70 at frame 300) is the formula in
FORMATS.md applied to the finished picture. The assembly gives 0.732 at
frame 900 with the shadow in the same place and of the same size (21,600
pixels against 22,500); a first version that darkened before the tint gave
0.833, and one that cast from a point light at the lamp darkened the walls
and the pillar the game leaves alone. Unlimited volumes also covered the
front of the bed and the floor beyond it at frame 300, Fiona's shadow fell
on the window glass in EV0005 frame 2053 and on the window frame at 1750,
where the game shows none (her shadow is active there, alpha 24.6). The
game leaves out what the room hides: with translucent faces dropped from
the shadow layers and the room tested for visibility from a point 0.8 of
the way from the bone to the light (from the bone itself, the sloping
bedcover faced away; from the lamp, the fireplace hid the near floor the
game shades at frame 900), the bed's front, the floor beyond it and both
EV0005 shadows go, the bed-top patch, the strip down its side and the
floor patch beside Debilitas stay (2,700–3,200 pixels against 5,200: the
game's patches are somewhat larger), and frame 900 is unchanged (0.733).
In EV0014 the first version drew Fiona's arm on the tablecloth (frame 400)
where the game shows nothing: the shadow objects there store alphas of 3
and 6, and editing EV0007's lamp showed that the game draws no shadow at 7
or less, so the assembly now applies that threshold (EV0014 frames 400 and
1000 then have no shadow, as in the game; the EV0007 and EV0005 frames are
unchanged).

### Transparency rules

When the per-triangle transparency rules were introduced (see
[FORMATS.md](FORMATS.md#how-models-are-drawn-and-what-that-means-for-transparency)),
12 frames each of EV0002, EV0004, EV0014, EV0017 and EV0021 were rendered
with the old and the new rules and compared pixel by pixel. One regression
was found and fixed (tinted glass turning partly opaque). The remaining
differences are a few pixels along edges, and one improvement: EV0021's
foliage, which the old rules drew with dark sorting bands.

## Known differences from the game

* **Lights per character.** The game lights a character with the three
  strongest of the lights that reach its floor triangle, with a linear falloff;
  the Blender script uses all of the room's lights with an inverse-square one
  (matched at half the range). In EV0002 the room lights are dimmed to a tenth,
  so it hardly matters there; Daniella in frame 1299 is 12 % brighter than in
  the game.
* **Character shadows are approximate in extent.** The game's rule for
  where a shadow volume stops was not decoded; the assembly's visibility
  test reproduces EV0007 frames 300 and 900 and EV0005, with somewhat
  smaller patches at frame 300 (see *Character shadows* above);
  `--no-character-shadows` leaves them out.
* **Optional room objects.** Some objects are switched on and off by the
  rooms' event scripts (command 0x4C), which the exporter does not read yet;
  the exports show all of them (for example a second moon and early broken
  glass in EV0018). They are marked so they can be hidden. In DE000C a glow
  panel in group 27 (`ST_00C_glow_sel027`) covers the stained-glass window
  the game shows.
* **The first second of a cutscene's physics** depends on the state the
  characters bring from gameplay, which a cutscene file does not contain.
* **Debilitas' physics** differs by about 1 cm, because the game poses his
  bones slightly differently.
* **Death scenes** were not in the video capture; whether the glass shard
  should be hidden there is unchecked.

## Pitfalls the exporter handles

These are easy to get wrong and are worth knowing if you write your own
tools:

* **Vertex colours use 128 for full brightness.** Reading them as 0–255
  makes rooms half as bright. The exporter maps `min(1, value/128)` through
  the sRGB-to-linear curve, because glTF vertex colours are linear.
* **Cutscene models (`_200`) only replace the face texture.** Their own TEX
  file holds a single image; the rest comes from the family's `_000.TEX`.
* **One file is truncated:** `SHT_200.PCK`'s last blend-shape normals run 4
  bytes past the end of the file. The exporter fills the missing bytes with
  zeros.
* **Use the stored inverse bind matrices.** Recomputing them from the rest
  pose bends the Debilitas models by about 1.9°.
* **Face and teeth share their controls.** Giving them separate blend shapes
  forces users to drive pairs of weights by hand.
