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
* pre-rendered movie shots missing (the movie layer is now placed in front
  of the camera);
* EV0014's tablecloth rendering black in EEVEE (soft shadow decals lying
  exactly on the cloth; rooms now keep *Blended* and the decals are lifted
  1 mm);
* Daniella's glass shard visible where the game shows none (it is now a
  separate object that cutscenes hide);
* colours darker than the game with Blender's default AgX view (*Standard*
  matches the capture's mid and bright tones).

### Transparency rules

When the per-triangle transparency rules were introduced (see
[FORMATS.md](FORMATS.md#how-models-are-drawn-and-what-that-means-for-transparency)),
12 frames each of EV0002, EV0004, EV0014, EV0017 and EV0021 were rendered
with the old and the new rules and compared pixel by pixel. One regression
was found and fixed (tinted glass turning partly opaque). The remaining
differences are a few pixels along edges, and one improvement: EV0021's
foliage, which the old rules drew with dark sorting bands.

## Known differences from the game

* **Optional room objects.** Some objects are switched on and off by the
  game's event scripts, which are not decoded; the exports show all of them
  (for example a second moon and early broken glass in EV0018). They are
  marked so they can be hidden.
* **Fog** is exported as data but not applied, so the capture's shadows are
  somewhat brighter than the renders.
* **Shadows in the capture** are 25–30 levels (of 255) brighter than in the
  renders, likely because of the capture's levels and the missing fog.
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
