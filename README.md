# hgexport: Haunting Ground asset exporter

This project has been created using Clause Opus 5.5.

**hgexport** turns the game files of *Haunting Ground* (Capcom, PlayStation 2,
2005; the US release, SLUS-210.75) into standard formats you can open in
Blender, Unity, Unreal or any other modern tool:

* characters, rooms and cutscenes become **glTF** scenes (`.glb`): meshes,
  skeletons, animations, cameras and lights;
* textures and images become **PNG**;
* voices, sound effects and music become **WAV**, **MIDI** and **SoundFont**
  files, and the game's movies become **MP4**;
* the game's text becomes **JSON** and plain text.

The exporter is plain Python (3.11 or newer, standard library only). Movies
need [FFmpeg](https://ffmpeg.org/) on your `PATH`; everything else works
without it.

The aim is a faithful copy of what the game ships. Nothing is
re-interpreted: models keep the game's own skeletons, animations play at the
game's 30 frames per second, and colours stay as stored. Behaviour that the
game computes while playing (cloth and hair physics, AI, foot placement) is
not baked into the files, but the data needed to reproduce it is exported,
and the included Blender script recreates the cloth and hair physics.

## Contents

1. [What you need](#what-you-need)
2. [Export everything](#export-everything)
3. [What you get](#what-you-get)
4. [Using the exports](#using-the-exports)
5. [Exporting single files](#exporting-single-files)
6. [Things worth knowing about the exports](#things-worth-knowing-about-the-exports)
7. [What is not included](#what-is-not-included)
8. [Project layout and tests](#project-layout-and-tests)
9. [Further reading](#further-reading)

## What you need

1. **Python 3.11 or newer.** No packages to install.
2. **The game's data, extracted.** Almost everything the game uses is packed
   in one archive on the disc, `DATA.CVM`. Extract it with any CVM extraction
   tool; you get a folder with sub-folders such as `O_FIN`, `ST_010`,
   `EV0002` and `BGM`. That folder is what hgexport reads (below it is called
   `DATA`). For tests, it is easiest to put or link it at `data/` inside this
   project.
3. **Optional:** FFmpeg for movies and cutscene audio, Blender 4.4 or newer
   (tested with 5.2) to assemble cutscenes.

## Export everything

From this project's folder:

```bash
python -m hgexport all path/to/DATA export
```

This writes the whole game into `export/` in a few minutes on a multi-core
machine (about 1,230 files and 2.3 GB). `export/manifest.json` lists what was
exported from which source file, with any warnings or failures.

To export only part of the game, add `--only-category` (one or more of
`characters stages cutscenes textures audio music text movies misc`) or
`--filter TEXT` (only source files whose path contains `TEXT`). A narrowed
run updates the matching entries in `manifest.json` and keeps the rest.

## What you get

| Folder | Contents |
| --- | --- |
| `characters/<folder>/` | One `.glb` per character model: mesh, skeleton, facial and hand blend shapes, every animation the game has for it. `<model>_shadow.glb` holds the simple shadow boxes the game uses for it. |
| `stages/<folder>/` | One `.glb` per room: geometry with its baked lighting, doors, movable objects, animated textures, gameplay camera paths, the lights the game uses for characters. `<room>_navmesh.glb` is the walkable floor; `<room>_zones.glb` the trigger lines and areas the game's scripts use. |
| `cutscenes/<event>/` | One `.glb` per real-time cutscene with its camera, the actors' animation and moving props, plus the soundtrack (`.wav`) and the event's movie layer (`.mp4`). The characters and rooms are not repeated inside; see [the Blender guide](docs/BLENDER_CUTSCENES.md). |
| `textures/<folder>/` | Every image in the game as PNG, in the same folder structure as the game data. |
| `audio/` | Voice and sound-effect recordings (`voice_sfx/`) and the instrument and effect banks (`banks/`) as WAV, with a JSON table per bank describing each sound effect. |
| `music/` | Each music track as MIDI (with loop points), a rendered WAV, and the game's instruments as SoundFont (`.sf2`). |
| `movies/` | The pre-rendered movies as MP4. |
| `text/` | Menu, item and room dialogue text as JSON and TXT. |
| `misc/` | Things that fit nowhere else: the memory-card icon (an animated Hewie), the game's always-loaded props (`GAME_FIX.glb`), the pursuers' door-opening curves, and one raw curve (`OBSTACLE.json`). |

### Names you will see

The game names its folders and files with short codes. Folders starting with
`O_` hold characters, `ST_` rooms, `EV` story cutscenes and `DE` mostly
death and game-over scenes. The main character codes:

| Code | Character |
| --- | --- |
| `FIN` | Fiona, the heroine, in her main outfit |
| `FIS`, `FIW` | Fiona early in the game (`FIS`: wrapped in a sheet) |
| `FIB`, `FIC`, `FIF`, `FIH`, `FIM`, `FIO` | Fiona's unlockable costumes |
| `HEW`, `HED`, `HEG` | Hewie, the dog |
| `DB0`, `DB1`, `DB2` | Debilitas |
| `DNL` | Daniella |
| `RCG`, `RCT` | Riccardo |
| `LRM`, `LRY`, `LRO`, `LRF`, `LRC`, `LRH` | Lorenzo in his different forms |
| `T00` | an early prototype of Fiona left on the disc |

The other folders hold minor characters, creatures and props. Inside a
folder, `_000` is the main model, `_200` the more detailed version used in
cutscenes, and `_001`, `_002`, … are animation sets for the same model (see
[Animation variants](#things-worth-knowing-about-the-exports)).

## Using the exports

**Characters and rooms** are ordinary glTF files: import them in Blender
(*File > Import > glTF 2.0*), Unity (with a glTF importer such as glTFast) or
Unreal (Interchange glTF import). Each character's animations come in as
separate clips (Blender: actions).

**Cutscenes** hold only what is unique to the cutscene and refer to the
character and room files by name, so they need assembling. The script
`tools/blender_assemble_cutscene.py` does it in one command:

```bash
blender --python tools/blender_assemble_cutscene.py -- export/cutscenes/EV0014/EV0014.glb
```

It imports the actors and rooms, connects the animation, applies the
cutscene's lighting, adds the soundtrack and movie layer and sets up the
game's cloth and hair physics. [docs/BLENDER_CUTSCENES.md](docs/BLENDER_CUTSCENES.md)
explains every step, how to do it by hand and what to keep in mind for
Unity and Unreal. If you prefer one self-contained file instead, export the
cutscene with `--embed-models` (see below).

## Exporting single files

Every part of the game can also be exported on its own. Paths are relative to
the extracted `DATA` folder:

```bash
python -m hgexport character O_FIN/FIN_000.PCK fiona.glb               # one character
python -m hgexport character O_FIN/FIN_000.PCK fiona.glb --family-mtn  # ... with all its extra animation files
python -m hgexport character O_HEW/HEW_000.PCK hewie_anims.glb --animations-only
python -m hgexport character O_FIN/FIN_003.PCK fin_003.glb --variant-of O_FIN/FIN_000.PCK
python -m hgexport stage ST_020/ST_021.PAC room.glb                    # one room
python -m hgexport cutscene EV0002 ev0002.glb                          # animation only
python -m hgexport cutscene EV0002 ev0002_full.glb --embed-models      # with actors and rooms inside
python -m hgexport tex O_FIN/FIN_000.TEX textures/                      # images to PNG
python -m hgexport audio BGM/S1_NORMALA.SQ music/                      # music: .mid + rendered .wav
python -m hgexport audio BGM/STAGE1_BANK.HD banks/                     # instrument bank: samples + .sf2
python -m hgexport info O_FIN/FIN_000.PCK                              # summary of any game file
python -m hgexport validate out/*.glb                                  # check exported glTF files
```

`python -m hgexport <command> --help` lists every option, for example the
output scale, which texture set to use, or `--separate-parts` (one object
per mesh piece instead of one body mesh).

## Things worth knowing about the exports

**Size and orientation.** The game measures in decimetres; exports are in
metres (`--scale` changes this). The game is Y-up like glTF, so nothing is
flipped. Rendering a room through the game's own camera reproduces an
in-game screenshot almost pixel for pixel.

**Bone names.** Bones that animations move are named `J00`, `J01`, … after
the slot number the game's animation data uses; bones that only the game's
physics moves (cloth, hair, chest) are named `X` plus their index. Because
the names follow the animation slots, an animation made for one outfit plays
on every other outfit of the same character.

**Animations** run at 30 frames per second, as in the game, and are named by
their number in the game's animation table (`003`, `017`). Animations from
the character's extra animation files get the file name in front
(`HEW_100_002`). Some animations come with a separate overlay that moves
only a few bones (ears, tail, mouth); when it cannot be merged with the main
animation it becomes its own clip (`017_layer3`). Clips play in place; add
`--root-motion` to get the movement through the room that the game applies
while walking or running.

**Animation variants.** Files such as `FIN_001`…`FIN_006` contain the same
model as `FIN_000` and differ only in a few animations: the ones Fiona plays
together with a particular pursuer (being grabbed, struggling free). They
are exported as animation-only files named after that pursuer
(`FIN_003_RCG_000.glb` = Fiona's set for Riccardo). Apply their clips to the
`FIN_000` skeleton. Hewie's `HEW_001`…`HEW_004` are his per-chapter
animation sets, named the same way.

**Faces and hands** are blend shapes (shape keys): `face_01`…`face_15` for
the facial expressions and `hand_l_01`…`hand_l_09` / `hand_r_01`…`hand_r_09`
for the hand poses. The default weights give the game's relaxed hand pose.
Gameplay animations of Fiona and Debilitas also animate the hands, as the
game does.

**Materials and transparency.** Characters have ordinary lit materials.
Rooms are pre-lit by the game's artists (the lighting is painted into the
vertex colours), so their materials are unlit. Transparency is decided per
triangle from the texture: solid parts are opaque, clean cut-outs (lace
holes, hair-card edges, foliage) use alpha clipping, and only genuinely soft
areas (hair ends, eyelashes, sheer lace, glass) are alpha-blended. This
keeps engines from sorting large opaque surfaces as transparent.
`--game-alpha` instead keeps the game's own, coarser choice. Alternative
texture sets (the detailed cutscene face, story-dependent versions) are
glTF *material variants*. Each material's extras name its source image, for
example `FIN_000.TEX#1`, which is `textures/O_FIN/FIN_000_1.png`.

**Lights.** The game lights only characters, using the room's ambient colour
plus up to 16 lights per room (it picks the three strongest for where the
character stands). They are exported as glTF punctual lights under
`<room>_lights`; brightness assumes the game's full value equals 2000 lux,
so adjust to taste.

**Cameras.** The game's field-of-view values are horizontal; exported
cameras use the matching vertical value for a 4:3 picture. Render cutscenes
at 4:3 (for example 1440 × 1080) to see what the game showed. Each room's
gameplay cameras follow a path; the `_rail` clips move them along it.

**Extra data.** Information that glTF has no field for is stored as *extras*
(Blender shows them as custom properties): per-frame animation markers, foot
contact ranges, which bones are which foot, cutscene cut lists, per-cut
lighting and fog values, controller vibration, and more.
[docs/FORMATS.md](docs/FORMATS.md) describes each of them.
`tools/blender_markers.py` turns markers and foot contacts into Blender
timeline markers and curves.

**Music.** The rendered WAVs use a simplified model of the PlayStation 2's
sound chip. The `.mid` + `.sf2` pair is the faithful version: play it with
any SoundFont player. The game plays its music without reverb, and so do the
renders.

## What is not included

* **Things the game decides while playing**: blinking, foot placement on
  uneven floors, cloth and hair physics and enemy behaviour are not baked
  into animations. The data that drives them is exported, and the Blender
  assembly script reproduces the cloth and hair physics (see
  [docs/SECONDARY_MOTION.md](docs/SECONDARY_MOTION.md)).
* **Which optional room objects a cutscene shows.** Some rooms contain
  objects that the game's event scripts switch on and off (a second moon, a
  pile of broken glass). Those scripts are not decoded, so all such objects
  are visible; they are marked so you can hide them (see the Blender guide).
* **Curtain colour changes** cannot be expressed in glTF; curtains get a
  generic open/close animation where the game's controller is unknown.
* **Room fog** is exported as data but not applied to the scenes.

## Project layout and tests

```
hgexport/          the exporter (run with python -m hgexport)
  formats/         readers for the game's file formats
  export/          turning decoded data into glTF, PNG, WAV ... (one module per kind of asset)
  gltf/            glTF writer and a structural validator
  data/            event_rooms.json: which room each cutscene plays in
  util/            binary reading, PNG encoding, maths
tests/             unit tests, plus tests against the real game data
tools/             Blender scripts (cutscene assembly, markers, checks) and helpers
docs/              documentation
```

Run the tests with:

```bash
python -m unittest discover -s tests -v
```

Tests that need the game data read it from `data/` in this folder (set
`HG_DATA` to use another folder) and are skipped when it is missing. A few
compare against reference exports made with Noesis, read from `research/`
(or `HG_RESEARCH`).

## Further reading

* [docs/BLENDER_CUTSCENES.md](docs/BLENDER_CUTSCENES.md): assembling and
  rendering cutscenes, in Blender and elsewhere.
* [docs/FORMATS.md](docs/FORMATS.md): how the game's files are built and
  what every exported extra means. A reference for anyone extending the
  exporter.
* [docs/SECONDARY_MOTION.md](docs/SECONDARY_MOTION.md): how the game moves
  cloth, hair and chests, and how the Blender script reproduces it.
* [docs/VERIFICATION.md](docs/VERIFICATION.md): how the exports were checked
  against the game and against independent tools.
