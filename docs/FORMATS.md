# How the game's files work

This document describes the file formats of *Haunting Ground* as the
exporter reads them, and what each exported value means. It is a reference
for anyone who wants to extend the exporter, check its work or use the
extra data in the exports. You do not need it to use the exports.

Each section starts with what the file is for and where it ends up in the
export, followed by the layout and the details.

## Reading this document

**Notation.** All numbers in files are little-endian unless noted. `u8`,
`u16`, `u32` are unsigned integers of 8, 16 and 32 bits, `i16` and `i32`
signed ones, `f32` a 32-bit float. `+0x10` means "16 bytes after the start
of the structure being described". `3×i16/4096` means three 16-bit integers,
each divided by 4096 to get the real value.

**Game code addresses.** Values such as `0x1BDF80` are addresses in the
game's program (`SLUS_210.75`, the US release), given so that anyone with a
disassembler can find the code that reads the data. You can skip them.

**"Verified"** means checked against something independent: reference
exports made with the Noesis model viewer, FFmpeg, the game itself (an
in-game screenshot, a video capture, or a PlayStation 2 emulator), or exact
consistency across every file of that type in the game.

### A few PlayStation 2 terms

| Term | Meaning |
| --- | --- |
| GS | *Graphics Synthesizer*, the console's graphics chip. Its register settings decide how textures are drawn and blended. |
| Indexed texture, CLUT | Textures store a colour number per pixel (8 bits, or 4 bits) and a *colour look-up table* (CLUT, a palette) of 256 or 16 colours. |
| PSMT8, PSMT4 | The GS names for 8-bit and 4-bit indexed textures. |
| CSM1 order | The GS stores a 256-colour palette in blocks of 8 entries with blocks 2 and 3 of every 32 swapped. Entry *i* is found at position `(i & ~0x18) \| (i & 8) << 1 \| (i & 0x10) >> 1`. |
| Alpha 0x80 | The GS treats 128 (0x80) as fully opaque, not 255. Texture alpha is converted with `min(2a, 255)`. Vertex colours likewise use 0x80 for "1.0". |
| Triangle strip | Triangles stored as a run of vertices, each new vertex forming a triangle with the two before it. A marker starts a new strip. |
| VU1, VIF | The vector unit that transforms geometry, and the interface that feeds it data. Only relevant to the GAME_FIX props. |
| SPU2 | The sound chip. Its native sample format is PS-ADPCM ("VAG"). |

### Game terms used here

| Term | Meaning |
| --- | --- |
| Model family | A character's files share a code and folder: `O_FIN/FIN_000.PCK`, `FIN_000.TEX`, `FIN_200.TEX`, `FIN_100.MTN`, … |
| Bone palette | The list that maps animation *slot numbers* to the skeleton's bones. Animation data addresses bones by slot, which is why exported bone names are `J<slot>`. |
| X bones | Bones outside the palette. Animations never move them; the game's physics does (cloth, hair, chest). |
| Motion bank | A set of animations ("clips"), found in models, in separate `.MTN` files and in cutscenes. |
| Room | One playable area, stored as a `.PAC` file in a `ST_*` folder. |
| Event | A real-time cutscene, stored as a folder `EVxxxx` (story) or `DExxxx` (mostly death and game-over scenes). |
| Actor slot | The numbered role a character plays in a cutscene (slot 1 = the player, 2 = Hewie, …). |

## Overview: which file holds what

| Files | What they are | Section | Exported to |
| --- | --- | --- | --- |
| `*.TEX` | textures | [Textures](#1-textures-and-images) | `textures/` |
| `O_*/*.PCK`, `O_T00/*.MDL` | character models with their animations | [Character models](#2-character-models-pck) | `characters/` |
| `*.MTN` | extra animation banks | [Animation](#3-animation-motion-banks) | inside the character files |
| `O_*/*.MRK` | per-frame animation markers | [Animation](#3-animation-motion-banks) | character clip extras |
| `ST_*/*.PAC` | rooms | [Rooms](#4-rooms-pac) | `stages/`, room textures in `textures/` |
| `O_*/*_D000.MTN` | door-opening curves | [Rooms](#4-rooms-pac) | room clips, `misc/doors/` |
| `EV*/`, `DE*/` (`.DH`, `.DP`, `MARK.BIN`, `PARAMS.BIN`, `.SFD`) | real-time cutscenes | [Cutscenes](#5-cutscenes) | `cutscenes/` |
| `*.ADX`, `*.HD` + `*.BD`, `*.SDT`, `BGM/*.SQ` | voices, sound banks, effect tables, music | [Sound and music](#6-sound-and-music) | `audio/`, `music/` |
| `*.SFD` | movies | [Sound and music](#6-sound-and-music) | `movies/`, cutscene `.mp4` |
| `SUBSCR/MSG_*.BIN`, room text | text | [Text](#7-text) | `text/` |
| `SUBSCR/ART*.LEN`, `THUMBNAIL.TXS`, `SYSTEM/*.BIN` | gallery art, thumbnails, logos | [Textures](#1-textures-and-images) | `textures/` |
| `GAME_FIX.GFM`, `OBSTACLE.MTN`, `SUBSCR/ICON*` | always-loaded props, a raw curve, the memory-card icon | [Other files](#8-other-files) | `misc/` |

---

## 1. Textures and images

### TEX files

*229 files, 326 images. Verified pixel-exact against Noesis. Exported as
`textures/<folder>/<file>.png`, or `<file>_<n>.png` when a file holds
several images.*

```
u32 count, 12 bytes padding
count × { u32 gs_format (0x13 PSMT8 | 0x14 PSMT4), u16 width, u16 height,
          u32 layout (unused), u32 payload offset (from this descriptor) }
payload: colour indices (8 bits per pixel, or 4 bits with the low nibble first),
         then a 1024-byte palette in CSM1 order
```

Indices are stored row by row (not in the GS's internal block layout). Row 0
is the top of the image and UV (0, 0) its top-left corner, as in glTF, so
nothing needs flipping.

**Texture sets of a character.** `X_000.TEX` holds all of a model family's
textures ("components"). `X_200.TEX` is a single 512×512 replacement for
component 0 (the face) that the game installs for cutscenes. `X_000H.TEX` /
`X_200H.TEX` are alternative sheets the game selects with a story flag, and
`X_201.TEX`, … are further replacements for component 0. The character
exports offer all of these as glTF material variants.

**Extra palettes.** Eleven 4-bit images (in `GAME_FIX.TEX`, the SUBSCR files
`MSG_BASE`, `SUBBASE`, `SUBSAVE`, `SUBMG`, `SYN_SLOT`, `GAL_MOVIE`, and the
SYSTEM files `AVOID`, `TITLE`) hold several 16-colour palettes in one
palette block. The game picks one per use (tinted fonts, highlighted
buttons). The export writes palette 0 as the main PNG and each further
palette as `<image>_palK.png`.

### How the menu draws its textures

Traced from an emulator save state of the item menu (GS register writes in
memory, compared with video memory and the screenshot). **The exporter does
not apply any of this**; it explains why some raw PNGs look unlike the game.

* **Where the menu's images come from:** `SUBSCR/SUBBACK.TEX` holds the
  parchment pages with their tab headers (Special, Items, File, Map,
  Comment, Options); `SUBSCR/SUBBASE.TEX` the small item icons, windows,
  frames and arrows; `SUBSCR/MSG_SUB.TEX` the font; `ITEMxx/ITEM_*.TEX` the
  large item pictures. All of them are in the export.
* **Item pictures are stored as colour negatives** and drawn by
  subtracting half of the picture from the parchment:
  *screen = background − texture / 2*, limited at 0 (GS blend
  `Cd − Cs·64/128`). On a light background a negative becomes a positive,
  sepia-toned picture. Checked on screenshot pixels: parchment
  (131, 116, 90) with a stored pixel (48, 144, 146) gives (107, 44, 17); the
  screenshot shows (101, 35, 8). The natural colours of a picture on its own
  are the inverse, 255 − value.
* **The font** is drawn the same way (`Cd − Cs`), so it appears as dark ink.
* **`SUBBASE`** is drawn with palettes 1, 3 and 4. Palette 0, the exported
  main PNG (washed-out yellow), is not used by the menu; palettes 3 and 4
  are the natural parchment colours, 1 the bright highlight, 2 the green
  highlight.
* **`GAME_FIX.TEX`**: image 2's palette 0 is grey and palette 1 the natural
  colouring (see [GAME_FIX.GFM](#game_fixgfm-always-loaded-props)); image 1
  (effects) has a neutral grey palette 0 that is tinted when drawn, and
  palettes 1–11 carry the effect colours; image 0 (insects) is dark in every
  palette.

### Gallery art, thumbnails and system images

* **Gallery art** (`SUBSCR/ART00`–`ART05.LEN`, 48 images): a table of
  `u32` offsets (the first offset equals the table size; 8 entries per
  file). Each entry is run-length coded 8-bit indices (a byte `c ≥ 0x80`
  repeats the next byte `c − 0x80` times; otherwise `c` literal bytes
  follow), then a 1 KiB palette in CSM1 order. 512 pixels wide: 21 portraits
  of 512×896 and 27 screens of 512×448, in PlayStation 2 pixels (shown on a
  4:3 screen, so about 1.17× wider). Exported as `textures/SUBSCR/ARTnn_k.png`.
* **Gallery thumbnails** (`SUBSCR/THUMBNAIL.TXS`): `u32` count, `u32`
  offsets, one TEX record each: 110 cutscene stills, 128×128 4-bit, the
  picture in the upper part. Exported as `textures/SUBSCR/THUMBNAIL_nnn.png`.
* **System images** (`SYSTEM/LOGO_CRI.BIN`, `LOGO_DOLBY.BIN`,
  `TITLE_BACK.BIN`): no header, just indices followed by the palette; the
  sizes are fixed in the game's code. `LOGO_CRI`: 640×448, 8-bit (the
  Sofdec / ADX logos). `LOGO_DOLBY`: 720×540, 8-bit (Dolby Pro Logic II
  splash, shown scaled to 640×448). `TITLE_BACK`: 640×448, 4-bit (title
  background). Exported to `textures/SYSTEM/`.
* **Room textures** live inside each room file (slot 9, see
  [Rooms](#4-rooms-pac)) and are exported as `textures/ST_xxx/<room>_<n>.png`.
* **The memory-card icon texture** lives inside `SUBSCR/ICON00.ICO` (see
  [Other files](#memory-card-icon)) and is exported as
  `textures/SUBSCR/ICON00.png`.

---

## 2. Character models (PCK)

*115 files. Verified against Noesis: positions, UVs, triangles and bones
exact. Exported as `characters/<folder>/<model>.glb`.*

A PCK file holds one model: its skeleton, its meshes, its blend shapes
(faces, hands), simple shadow shapes and its built-in animations.

### Layout

```
header: +0x04 model   +0x08 blend shapes ("morph section")   +0x0C shadow volumes   +0x10 motion bank
model:  u32 bone count, then offsets (relative to the model) of the skinned
        mesh table, the rigid mesh table and the bone palette
bones:  bone count × 0x70 bytes: i32 parent, Euler angles XYZ at +0x10,
        translation at +0x20, inverse bind matrix (4×4, column-major as in glTF) at +0x30
palette: u8 count, count × u8 bone index (animation slot → bone)
```

**Skinned meshes** (table entries of 0x30 bytes; count at +0, entries from
+0x10) are bent by several bones. Each entry holds the vertex count, then
offsets (from the entry) to: positions (+4), UVs (+8, `2×i16/32768`),
normals (+12, `3×i16/32768`), bone weights (+16, `u16/32768` per
influence), bone numbers (+20, `u8 >> 2` = position in the mesh's own bone
list), strip markers (+24); then the texture number (+28), render flags
(+32, bit 0 = alpha blending), the mesh's bone list count (+36) and offset
(+40), and the number of influences per vertex (+44).

**Rigid meshes** (entries of 0x20 bytes) follow a single bone: positions,
UVs, normals, strip markers (+16), texture (+20), flags (+24), parent bone
(+28).

**Positions** are a `3×i32` start value followed by `3×i16` differences, each
vertex adding to the previous one; divide by 4096.

**Triangle strips.** A marked vertex starts a new strip. From the first
unmarked vertex on, triangles alternate between `(v−2, v−1, v)` and
`(v, v−1, v−2)`, giving counter-clockwise triangles that face outwards
(this agrees with the stored normals for 99.9 % of 329,000 triangles).

**Inverse bind matrices.** The stored matrices are what the game skins with.
They differ from the skeleton's rest pose for the Debilitas models
(DB0/DB1/DB2, by 1.9°) and slightly for Hewie, so the exporter uses the
stored ones.

### Blend shapes ("morphs")

Count at +0, then 0x40-byte descriptors from +0x10: number of shapes, vertex
count, UV offset, strip marker offset (byte markers, or `i32` sign markers
when the topology mode is 1), a table with each shape's position and normal
offsets, parent bone, texture, topology mode, flags and an origin (`3×i32`
at +0x30). Shape positions are `3×i16` plus the origin, divided by 4096;
normals are stored in the order Y, Z, X.

Characters use descriptors 0 and 3 for the face and the teeth/eyelashes (16
shapes each) and 1 and 2 for the left and right hand (10 shapes each). The
game drives face and teeth with the same controls, so the export gives them
shared targets named by number and region: `face_01_mouth`…`face_06_mouth`,
`face_07_blink_R`/`_L` and `face_08_blink_L`/`_R` (one eyelid each; their
order differs per character, so the side comes from the geometry: faces look
along +Z with Y up, so the character's left is +X), `face_09_brow`…
`face_12_brow` and `face_13_expression`…`face_15_expression` (measured on every
character with a face). Hands
become `hand_l_01`… and
`hand_r_01`…. The game's resting hand pose is shape 1 of each hand, which is
the exported default.

### How models are drawn (and what that means for transparency)

The game draws each model in two passes (code at `0x1BDF80`): first every
mesh whose render flag bit 0 is clear, then those with it set. The draw
setup (`0x2B72D0`) uses the same rules for both passes: pixels whose texture
alpha is 0 are discarded, depth is tested and written, and bit 0 only turns
on alpha blending. So unflagged meshes are solid with cut-outs, and flagged
meshes are blended but still write depth, after the rest of the model.

The flag marks whole meshes that are mostly solid (Fiona's torso with its
lace hem, skirt panels with lace edges), which would make engines treat
large opaque areas as transparent. The exporter therefore decides
transparency per triangle from the texture pixels it can sample
(`hgexport/export/alpha.py`): opaque if all are at least 88 % opaque (where
fully opaque pixels dominate the piece; on uniformly tinted glass only
100 % counts), clipped if they are only nearly empty or nearly solid,
blended otherwise. No unflagged mesh in the game samples alpha 0, and the
flagged ones are solid except along hems, fringes and hair ends.

A byte per mesh in the character object (`+0x98 + 2i`) is combined with the
mesh's flags when drawing; its bit 1 hides the mesh. The game uses this to
switch accessories on and off. Fiona's are numbered, not named: the setter
at `0x2F6E60` hides every mesh of a slot and shows the number's meshes,
1–4 the earrings (0 none) and 5–9 the neck (5 the plain one she wears by
default). The export names them `<slot>_<item>` and keeps the number and
the item's name (the item list in `MSG_BASE`, entries 390–397) in the
object's extras:

| Object | Number | Item | Meshes |
| --- | --- | --- | --- |
| `earrings_inquisitor` | 1 | Inquisitor Earrings | skinned 32 |
| `earrings_philosopher` | 2 | Philosopher Earrings | rigid 2 |
| `earrings_alchemist` | 3 | Alchemist Earrings | rigid 3 |
| `earrings_fairy` | 4 | Fairy Earrings | skinned 33 |
| `neck_default` | 5 | — | skinned 3, 4 |
| `neck_jade` | 6 | Jade Choker | skinned 5 |
| `neck_ruby` | 7 | Ruby Choker | skinned 22 |
| `neck_diamond` | 8 | Diamond Choker | skinned 6, 7 |
| `neck_obsidian` | 9 | Obsidian Choker | skinned 23, 24 |

The value 4 in the same byte selects lit drawing programs that add a
highlight (`max(R·V, 0)^4` times each light's strength), and the byte after
it is the highlight's strength (128 = 1.0). These "shine" bytes are not in
the model files: each character class writes fixed values when its model
has loaded (Fiona's main outfit: `EventHumanModel_Loaded`, `0x2F7920`). The
values were read from those routines and checked in emulator save states;
`hgexport/export/shine.py` lists them for Fiona's outfits, Daniella,
Debilitas, Riccardo and Lorenzo. Meshes are numbered skinned first, then
rigid, then blend-shape pieces.

In the game the highlight only goes into the pixel's alpha, and the game
clears the frame's alpha after drawing the characters, before its bloom
reads it (see [Screen look](#screen-look-tint-glow-geometry-and-bloom)), so
it never shows in normal play. The exporter therefore paints it into grey masks, one per texture sheet
(`textures/<folder>/<model>_mNN_shine.png`, grey 128 = 1.0), and uses it in
materials only when asked (`--specular`). Shiny sheets then get a roughness
of 0.58 where the mask is set (the game's exponent 4 as a GGX roughness) and
a reflectance chosen so that the highlight's peak matches the game's: at its
peak the game adds strength × the light, which GGX at that roughness reaches
with F0 = 4/9 × strength (0.22 for the common strength 64). The sheet's
strongest shine sets F0 through `KHR_materials_ior` (IOR 2.78 for 64, 5.0 for
128), and `KHR_materials_specular` scales it by the mask and tints it by the
texture itself (`specularColorTexture` = the base colour), as the game's
bloom would brighten the frame by its own colour. A white highlight of that
strength turns dark cloth grey (the stocking tops share the skin's strength)
and washes the pendant's detail out. Sheets without shiny meshes get no
specular at all.

### Shadow volumes

*Exported as `characters/<folder>/<model>_shadow.glb`, or inside the model
with `character --shadow-volumes`.*

The game casts character shadows with stencil shadow volumes: simple boxes,
one per bone, extruded from one room light's direction (see "Character
shadows" under the screen effects). Layout: `u32`
count, 12 bytes padding, 32-byte descriptors (`u32 bone, u32 faces`, offsets
of the face, edge and vertex lists); faces are 48 bytes (4 vertex numbers, 4
edge numbers, a normal), edges pairs of `u32`, vertices `float4` in the
bone's own space. All 2,086 volumes in 109 models are closed boxes (8
vertices, 6 quads). The export holds one mesh with the boxes bound rigidly
to their bones; they are proxies, not visible geometry.

### The prototype Fiona (O_T00)

`O_T00` holds the same blocks as separate files: `.MDL` (model), `.SHP`
(blend shapes), `.SHD` (shadow volumes), `.MTN` (animations). Put behind a
PCK header they form an ordinary PCK: an April 2004 prototype of Fiona with
68 bones and 116 animations.

### Model variants

**Fiona's `X_000`…`X_006`** (in `O_FIN` and every costume folder `O_FIB`,
`O_FIC`, `O_FIF`, `O_FIH`, `O_FIO`) share mesh and skeleton. They differ only
in the animations 5120, 5121, 5123, 5124 and 5376: Fiona's half of the
animations she plays together with a pursuer (the pursuer's halves are its
animations 6400, 6401, 6403, 6404 and 6657, with matching lengths). `_006`
also has one extra animation, 3845.

| File | Pursuer it pairs with (matching animation lengths) |
| --- | --- |
| `_000` | Debilitas DB0/DB1/DB2 (3 of 5) |
| `_001` | Daniella DNL_000 (5/5) |
| `_002` | Daniella DNL_001 (5/5) |
| `_003` | Riccardo RCG_000 (5/5) |
| `_004` | Riccardo RCT_000 (5/5) |
| `_005` | Lorenzo LRO_000 (3/4; neither has the 5376/6657 pair) |
| `_006` | Lorenzo LRY_000 (4/5) |

The game picks the set from story progress (loader `0x1A4110`, flags of the
game-state object `0x44E4D8`). The exports are animation-only files named
after the pursuer (`FIN_003_RCG_000.glb`), with the base model and pursuer
in the file's extras (`paired_with`).

**`DNL_001`** is Daniella's later-phase character: a separate game class
with its own data, used from the same story point that selects Fiona's set
2, whose paired animations it matches. Its export names `FI*_002` as
`paired_with`.

**Hewie's `HEW_001`…`HEW_004`** (also `HED`, `HEG`) change his
attack and command animations (8704–8729) per story chapter. They are not
paired with pursuer animations; by the shared story flags `_001` belongs to
Daniella's chapter, `_002` to Riccardo's, `_003` to Lorenzo's LRO chapter,
`_004` to his LRY chapter, and `_000` to Debilitas'. The exports are
named after the chapter's pursuer and carry it as `chapter`.

---

## 3. Animation (motion banks)

*All 98 model banks, 35 MTN files and 995 cutscene actor banks use this
layout. Exported as glTF animations in the character and cutscene files.*

### Layout

```
+0x00 u32 entry count   +0x04 u32 number of used groups   +0x08 0   +0x0C offset of the lookup table
+0x10 entry count × 5 u32: offsets of the entry's five groups ("slots"), relative to the entry (0 = empty)
group:  u32 track count, u32 frame count, u32 offset of the track table (from the group)
track:  i32 target, u32 flags (codec = flags & 0xFF; bit 16 = one constant sample),
        u32 offset of the samples (from the track record)
lookup: u32 count, 12 bytes padding, count × { u32 resource id, i32 entry } (the last match wins)
```

An *entry* is one animation. Its slot 0 holds the controller tracks (negative
targets, see below); slots 1–4 hold bone tracks (target = bone palette
slot). Slots 2–4 are usually small overlays (ears, tail, mouth); the export
merges them into the main clip when they have the same length and move
other bones, and otherwise exports them as `<clip>_layerN`.

**Sample formats ("codecs"):**

| Codec | Per frame | Use |
| --- | --- | --- |
| 0 | `3×i16·π/32768` | rotation |
| 1 | `3×i16/256` | position |
| 2, 4 | `3×i16` rotation + `3×i16/256` translation | bone with movement |
| 3 | `4×i16/32767` | four controls |
| 5 | `i16` shape A, `i16` shape B, `i16/32767` mix | hand pose mixer |
| 7 | `6×f32` | rotation + translation |

Rotations are Euler angles applied X first, then Y, then Z (`Rz·Ry·Rx`).
Animations run at 30 frames per second (verified against cutscene audio).
`*_D000.MTN` files use a different format (door curves, see
[Rooms](#doors)); `OBSTACLE.MTN` is a plain curve (see
[Other files](#obstaclemtn)).

### Controller tracks

| Target | Meaning | Exported as |
| --- | --- | --- |
| −1 | movement through the room: per-frame turn and step | movement of a ground-level `root` joint, the skeleton's top bone (last in the skin's joint list, identity bind; off with `--no-root-motion`; never in cutscene files) |
| −2, −3 | positions of the two foot bones | `foot_points` (only where they differ, see below) |
| −4 | foot contact flags | `foot_contact` |
| −5, −6 | Hewie's paw contacts (hind, front) | `foot_contact` |
| −7…−10 | facial controls (16 values; control *k* drives shape *k* of every face piece) | `face_01_…`…`face_15_…` weights |
| −11, −12 | hand mixers for hand 1 and 2: `shape[A]·(1−mix) + shape[B]·mix` | `hand_l_…`, `hand_r_…` weights |

**Feet (−2…−6).** Tracks −2 and −3 store where the rig's two foot bones are
(Fiona and most humans: J10 and J22; Riccardo J12 and J24; young Lorenzo J11
and J23). In every shipped animation they equal the posed skeleton's feet
within the storage precision, so the export leaves them out, except in
Fiona's animations 1802 and 1803 (all costumes; the points were not updated
after the pose was edited) and one frame each in one cutscene (EV1034),
listed as `foot_points` = `{bone: [[frame, x, y, z], …]}`. Track −4 holds
contact flags (x for the foot of −2, y for −3; its z is never read): a flag
is 1 exactly while the foot is planted. Hewie's rigs use −5 (hind paws J32,
J33) and −6 (front paws J10, J11) instead. The game uses them to fit planted
feet to the floor (contact query `0x1F8B50`, leg adjustment for Hewie at
`0x1F8450`). Exported as `foot_contact` = `{bone: [[first frame, last frame],
…]}` in the clip extras; the asset extras (`foot_controls`) say which bones
each track refers to. `tools/blender_markers.py` turns the contacts into
stepped `contact_<bone>` curves.

### Animation markers (MRK files)

*36 files. Exported per clip as the extras `markers`.*

`O_*/*.MRK`: `u32` count, count × `u32` animation id (same ids and order as
the model's lookup table), count × `u32` absolute offset (0 = none; clips
may share data), then one byte per frame, padded to 4 bytes. The game reads
the byte of the current frame (`0x1F4770`) and tests single bits:

* **Bits 4 and 8: hand poses** for hand 1 and hand 2 (code `0x2DCB40`). At
  the start of a clip both hands go to their resting shape; each marked frame
  switches that hand between its resting shape and a target shape, easing
  over the four frames before the mark. The target shape comes from a table
  per animation (Fiona's costumes: 120 rows, Debilitas: 85 rows). Exported as
  `hand_poses` (`[frame, A, B, mix]` per hand) and, in full character files,
  as hand blend-shape weight keys. Other characters' bits 4 and 8 are not
  read by this code.
* **Bits 1, 2, 16, 32: gameplay cues.** Bit 1 (one frame per clip) gives a
  pursuer a 50 % chance to send a cue (an event other game code reacts to)
  at its position; bit 2 sends a
  request to the actor-interaction code once per clip; bits 16 and 32 are
  tested by character state code. Their exact effects are not named.

---

## 4. Rooms (PAC)

*168 files. Exported as `stages/<folder>/<room>.glb`, plus
`<room>_navmesh.glb` (walkable floor) and `<room>_zones.glb` (script
triggers). Rendering a room through the game's own camera reproduces an
in-game screenshot almost pixel for pixel.*

A PAC file starts with a 0x800-byte table of offsets, one per *slot*; each
slot's data ends where the next different offset begins.

| Slot | Contents | Exported as |
| --- | --- | --- |
| 0 | navigation floor triangles | navmesh file |
| 1 | the floor's open edges (walls) | `walls` in the navmesh file |
| 2 | trigger lines and areas for room scripts | zones file |
| 3 | room geometry | room meshes |
| 4 | character lighting | lights and `character_lighting` extras |
| 5 | gameplay camera start settings | camera nodes |
| 6 | gameplay camera paths | camera `_rail` clips |
| 7 | door placements | door nodes |
| 8 | door meshes | door nodes |
| 9 | textures | room materials, `textures/ST_xxx/` |
| 10 | room text | `text/rooms/` |
| 11 | a table that is empty in every room | — |
| 12 | named (movable) objects | `<room>_objNNN_<name>` nodes |
| 13 | screen effects (tint, bloom, fog, depth effect) | `room_effects` extras |
| 14 | switchable floor groups | `floor_groupNN` in the navmesh file |
| 15 | authored object motion | `<object>_motion<k>` clips |
| 16 | per-triangle navigation overrides | navmesh file |

### Geometry (slot 3) and draw records

Room geometry, doors and rigid named objects use the same **draw record**:

```
i32 vertex count, i32 texture, u32 control, i32 extra
f32[16] matrix (row-major; translation in elements 3, 7, 11)
UVs 2×f32 | align to 16 | colours 4×u8 (0x80 = 1.0) | align | positions 3×f32 + u32 (bit 15 = strip restart)
```

Slot 3 starts with six group offsets; each group is a run of records ended
by `−1, −1`. Groups 0 and 1 are normal geometry, group 2 the **glow
geometry** (lamps, candle flames, lamp shades: drawn after the screen tint
and fog and marking the bloom, see [Screen look](#screen-look-tint-glow-geometry-and-bloom);
exported as `<room>_glow`), group 4 animated textures,
group 5 blended effects drawn without depth writes (`extra` bit 8 =
additive). Control bit 0 turns on alpha blending; the top byte is a
**visibility selector** that event scripts switch on and off (0 = always
visible). Objects with a selector are exported in `…_selNNN` nodes with the
`visibility_selector` extra.

Untextured records (texture −1: mostly the glow geometry's bulbs) show the
vertex colour itself on a 0–255 scale; textured ones multiply the texture
by it with 0x80 = 1.0.

The vertex colours carry the room's painted-in lighting (0x80 = full
brightness); the export converts them to linear `COLOR_0` and uses unlit
materials. The GS interpolates them, multiplies the texture by them
(`(T × V) >> 7`, rounding down) and blends translucent layers in display
values; renderers that do these in linear light show dark gradients and
fading layers brighter than the game (5–15 % in dark rooms; see the Blender
guide's *render space*). Transparency follows the same per-triangle rules as for
characters, with the vertex alpha taken into account (fades never become
opaque); baked floor shadows and effects stay blended.

### Animated textures

Group 4 records are quads whose UVs show one cell of a texture atlas. The
control word holds the cell count (bits 16–23), `extra & 15` how many frames
each cell stays, and `(extra >> 4) & 15` a mode (2 = play once and hold the
last cell; others loop; some modes also pick blend setups). The game
advances a cell every `hold` drawn frames (`0x25CB50`); every shipped atlas
has hold 1 and modes 0 or 6, so they run at 30 cells per second. Exported as
`<room>_atlasNN` with one child per cell and a looping clip.

### Doors

Slot 7 holds 8 offsets to door placements: `i32 resource, f32[3] position,
f32[3] second point, f32[3] Euler angles`, then two counted id lists. Slot 8
holds 8 `i32` values; door *i*'s meshes start at `slot start + value + 16·i`
and are runs of draw records ended by 16 bytes of `0xFF`.

**Door curves** (`O_*/*_D000.MTN`): count, offsets, then per curve 3 floats,
a sample count and one yaw angle (degrees) per game update. Even curves open
(0 → −90°), odd ones close. Every character who opens doors has a bank:
Fiona (8 curves, used for the room door clips) and the pursuers DB1, DB2,
DNL, LRF, LRO, LRY, RCG and RCT (4 each). The game keeps the player's bank
and the current pursuer's bank loaded and picks a curve pair in gameplay
code. Pursuer banks are exported once as `misc/doors/X_D000.glb` (clips on a
`door` pivot that turns about +Y, added to the door's own angle), or into a
room with `stage --pursuer-doors`.

### Textures (slot 9)

Count, then 16-byte descriptors (format, width, height, pixel size, palette
size, and flags whose upper 24 bits are the data offset), then 8-bit indices
and a CSM1 palette per texture.

### Named objects (slots 12 and 15)

Slot 12: four category counts, 16 bytes padding, offsets. Each object has a
16-character name, Euler angles at +0x10, a translation at +0x20, then either
one rigid draw record (category 2) or two packed shapes (category 3:
`3×i16` + origin, divided by 128, colours per shape, `2×u16/32768` UVs, byte
strip markers), exported as a mesh with one blend shape.

Slot 15 holds authored motion for some of them: `u32` count, offsets to
`name[16], u32 channel count, u32 table offset`; channels are `u32 frames,
u32 type, u32 data offset` with type 7 = Euler + position (6 floats), 8 =
Euler, 9 = position, as absolute transforms per game update. 29 objects in 15
rooms (levers, drawers, door pairs, a lid, spikes, a statue, a tadpole);
exported as `<object>_motion<k>` clips.

### Gameplay cameras (slots 5 and 6)

Slot 5 has 32 bytes per camera: start position, horizontal field of view in
degrees (60), target. Slot 6: count, then pairs of curve and key counts;
16-byte aligned 32-byte cubic keys (parameter, value, 4 handle values, mode,
word). Each camera has six curves (position x, y, z and target x, y, z) of
eleven keys over a progress value from 1 to 100. Exported as cameras with a
`_rail` clip that runs the progress at one step per frame. The game's field
of view is horizontal; exported cameras use the matching vertical value for
4:3.

### Character lighting (slot 4)

*Exported as `KHR_lights_punctual` lights under `<room>_lights`, the
ambient colour as `character_lighting` in the room's extras and the
per-triangle masks as `light_masks` in the navmesh file.*

Rooms are lit by the artists' painted vertex colours; only characters are
lit at run time. Layout: `u32` count (up to 16), `f32` ambient RGB, then
48-byte lights: `f32 x, y, z, 1; f32 R, G, B, w; f32 range, 1/range,
range², param` (colours on a 0–255 scale, `w` an intensity multiplier, mostly
0.3–1.4). Range 0 means a directional light coming from the stored point.
Each floor triangle's last word (+76) is a 16-bit mask of the lights that
may reach a character standing on it; the game uses the three strongest of
those (strength = (0.3 R + 0.6 G + 0.1 B) × w × (range − distance) / range).
The character's vertex colour is then the ambient plus each of the three
lights' colour × w × the falloff × max(N · L, 0) (direction from the light to
the character's position), with 128 = 1.0, and the GS multiplies the
texture's display value by it: an ambient of 128 shows the texture as it is
(checked in an emulator). Cutscenes scale the lights, raise the ambient and
put their two camera lights (no falloff) into slots 2 and 1 (PARAMS.BIN,
type 0).
882 lights in 166 rooms (795 point, 87 directional). The export assumes the
game's full value of 128 equals 2000 lux and matches the game's linear
falloff at half the range (at most 2.5 m), so wide lights are not too bright
up close. Blender's importer turns that into 128 ≈ 0.93 on a white surface
(2000 / (683 π)); Godot and Unity URP put the glTF number straight into the
light's energy, where 1 lights a white surface to 1, so `--light-units
unitless` writes 128 = 1 and, for point lights, also divides by those
engines' range fade (1 − (d / range)⁴)² at the matching distance (rendered in
Godot 4.7: ST_011's main lamp 0.397 at 2.5 m, the game 0.398; ST_000's
first directional light 0.227, the game 0.229). The lights group's extras
say which units a file uses (`light_units`).

### Navigation and triggers (slots 0, 1, 2, 14, 16)

* **Slot 0, floor:** `u32` count, 12 bytes padding, 80-byte triangles (three
  `float4` corners, three `i32` neighbours, `u32` flags, three centroid
  distances, the light mask). Slot 16 has one override byte per triangle.
* **Slot 1, walls:** `u32` count, 12 bytes padding, 16-byte records
  `u32 triangle, u32 edge e, u32 corner e, u32 corner (e+1) mod 3`: the
  floor's open edges. The game intersects movement and sight lines with
  them (`0x17B9B0`) and stops at the nearest. 14,728 records. Exported as a
  `walls` line mesh in the navmesh file.
* **Slot 2, triggers:** a table of offsets (the index is the number room
  scripts use) to records `u32 type, 0, f32 height, 0` followed by points;
  type 0 is a line the player can cross, type 1 a four-cornered area. 1,188
  lines and 1,105 areas, exported as `<room>_zones.glb`.
* **Slot 14, floor groups:** `u32` group count, offsets, then groups of
  triangle numbers that scripts switch on and off together. 223 groups,
  exported as `floor_groupNN` nodes in the navmesh file.

### Screen effects (slot 13)

Offsets at +8, +12, +16 and +20 to the settings of four screen effects: the
**tint** (two RGBA colours and a mode byte), the **bloom** (an RGBA colour
and a mode byte), fog (same layout as the cutscene fog below) and a depth
effect (as in cutscenes). Two rooms point past the end of their file; those
values read as zero. Exported as `room_effects` (`tint`, `bloom`, `fog`,
`depth_effect`) in the room's extras. When a room loads, the game creates its
fog from the two colours and the two distances only (the rest of the block
is ignored), so a room's fog always covers everything; see [Fog](#fog) for
how it is drawn. The Blender assembly script applies all of them to
cutscenes.

#### Screen look: tint, glow geometry and bloom

These give each room its colour mood. The game draws them in display values
(0–255) over the finished 3D picture, in this order (renderer layers; read
from the GS packets the game builds and checked on paused frames in an
emulator):

1. **Tint** (layer 0x20), once for each of its two colours that has alpha.
   The frame is point-sampled to half size (256 × 224); that copy plus a
   quarter of each of 8 copies shifted by (±1, ±1), (±2, 0) and (0, ±2)
   half-size pixels gives a glow buffer G, about 3 × a blur of the picture,
   clamped at 255. G is enlarged again (bilinear), multiplied by the colour
   (128 = 1.0), and:
   * colour 1 adds `G × colour1 × alpha1/256` to the frame (a soft glow);
   * colour 2 adds `(frame − G × colour2) × alpha2/256` (local contrast;
     darkening where `3 × colour2` is above 1.0). Its G is taken after
     colour 1.
   The mode byte (0 in every room) would skip the blur. Taken together
   (with blur(blur) as blur), the two passes are a per-channel curve on the
   blurred picture plus a fixed detail gain: `frame' = F(B) + (1 + alpha2/256)
   × (frame − B)`, where F follows the clamps above; the exporter writes F
   sampled as `room_effects.engine_look.<variant>.grade`.
2. **Fog** (layer 0x21; [Fog](#fog)).
3. Layer 0x25 clears the frame's alpha and turns on FBA, so everything drawn
   until layer 0x27 gets alpha ≥ 0x80. Layer 0x26 draws the **glow
   geometry**: room geometry group 2 (125 rooms; mostly untextured bulbs and
   flames) and room objects whose name starts with `g_` (only
   `g_chandelie` in ST_020). It is drawn normally, after the tint and fog,
   so lamps keep their own colour. Characters are drawn before layer 0x25,
   so nothing of theirs reaches the bloom.
4. **Bloom** (layer 0x28): the frame's pixels with alpha ≠ 0 (the glow
   geometry) are point-sampled to half size (A); B = 0.5 × the sum of 8
   copies of A shifted as above; then A += 0.125 × the sum of 8 copies of B
   shifted by (±3, 0), (0, ±3) and (±2, ±2); A, enlarged, adds
   `A × colour × alpha/256` to the frame. Modes 1 and 4 would subtract and
   2–4 pulse fixed colours; no room or script uses them.

**Night and warm.** The rooms' own tints (mostly a blue first colour) are
the night look. When a room is entered, its event script can replace the
tint (command 0x33 to effect 0x1F) and the bloom (command 0x9B) under a story
condition. Rooms 0x09–0x2D (most of the first area) switch to a warm tint
until story flag 2 is set (room 0x21 until flag 1); rooms 0xE0–0xE9 while the
story phase variable 0x2E is 1; rooms 0x00 and 0xE9 also get a strong blue
bloom then. Flag 2 is set when Fiona first leaves room 0x21 after EV0002,
flag 1 by an examine event in room 0x12, so only EV0002 and EV0003 play in
the warm look in the story.

The script that plays a cutscene can set them too, before the cutscene or
right after it waits for the cutscene to start, which is still before its
first frame: EV0033 plays in room 0x35 with a blue-grey tint and a violet
bloom its script sets (DE0048's script sets room 0x92's own values again).

**The movie gallery** (room 0x37) switches on the entry (progress variable
0x2A) to the event's room (command 0x3A, `3A hi lo`) and then runs one of
that room's actions (command 0x92; the switch at 0x3D6C18 is shared by the
gallery's actions): a replay script near the end of the room's action list
that plays just that cutscene. The rooms' enter scripts do not run, and the
story flags are all clear, so a cutscene shows its room's own (night) look
unless the replay script changes it: the warm tint in EV0002, EV0003,
DE000E, DE0000, DE0001, DE0002, DE001B, DE0010, DE000F and EV0004, the deep
warm tint in DE000A, the blue bloom in DE0003 and DE0011, and EV0033's look.
Everything else, EV0014 in room 0x11 for example, plays in the night look.
For events that can play in several rooms the gallery's switch picks one
(DE000A in room 0x25, DE0040 in 0x8A, EV0030 in 0x8D); the exporter prefers
it when the floor fits tie.

The exporter lists the replacements as `room_effects.script_variants` with
their conditions (`hgexport/export/look.py`): `story_flag_clear` /
`story_phase` for the enter scripts (story only), `story_events` and
`gallery_events` for the scripts that play an event. Each cutscene's story
flags are `story_state` in the cutscene's extras.

**Reflections.** Eleven rooms' own code (not their scripts) registers a
reflection effect (method table 0x472F60) in effect slot 26 or 27 with a
parameter block: the four corners of a quad in the room's space, a strength
(0x80 = 1) with flag bits in its top nibble, and 1.0 for floors. The
bedroom copies 0x21, 0x32 and 0xE0 have a door (slot 26) and a window
(slot 27) at 64; room 0x31 the window at 64; 0x49 a window at 64; 0x2E,
0x4B, 0x4E and 0x6A mirrors at 128; 0x55 and 0x60 horizontal quads at 128
(the strength of 0x60's is set at run time: 0 in EV0017, 128 in EV0018).
Each frame the effect copies the picture drawn so far, point-sampled, into
a 256 × 224 buffer (colour only, alpha 0) with a depth buffer of its own;
draws the characters it accepts there in render mode 23 (character field
+0x152C) through a mirrored camera, lit as they stand, tested only
against each other; blends the buffer with four copies shifted by one
pixel diagonally at 32/128 each in turn and the sharp characters back at
96/128 where they were drawn; and blends it, flipped back, over the frame
at strength/128 where the alpha is not 0, before the tint and the fog
(GS packets read on EV0005 frame 2053). It accepts a character when bone
0, projected through the mirrored camera to the 512 × 448 frame, with a
box of ±10 × ±30 pixels around it (Fiona, slot 0; Hewie ±20 × ±15, others
±20 × ±30), overlaps the rectangle around the projected corners. When the
effect is live depends on the room's code and the event; the exporter
carries frame ranges watched in the movie gallery (`reflections` in the
cutscene extras, `hgexport/export/reflection.py`), for example cut 9
(frames 2006–2084) of EV0005, and the whole of EV0003 and EV0007.

**Character shadows.** Every character has a shadow object (method table
0x46B1C0) whose draw method (Shadow_Draw 0x1F2B80) stencils the
character's shadow boxes (the PCK's shadow volumes) into the alpha of the
256 × 224 work buffer: the alpha is cleared to 127, and each volume
triangle, drawn with the depth test against the picture, reads that alpha
as an 8-bit texture (PSMT8H) through one of two palettes that add or
subtract one (blocks 0x3F30 / 0x3F34). A sprite then marks the pixels the
count left at 128 or more (destination alpha test) with the shadow's
alpha, which the object stores at +724 (as `alpha << 24 | 0x808080`):
`alpha = int(64 × intensity × u⁴)`, `u = 1 − d² / range²`, for a room light
(the room's slot-4 position, intensity and range), d being the distance
from the light to the character's bone J02 (Hewie's point is 2.81 units
lower). Read while EV0007 and EV0014 played, the stored value follows this
exactly (25.5 → 25, 16.9 → 16, 3.25 → 3, 6.7 → 6).

Measured on held frames by editing one light's record: only lights with a
non-zero fourth value (exported as `param_unused`) cast; that value is the
volumes' length in game units (EV0007's fireplace lamp at 75: at 60 the
floor shadow is the same, at 40 and 35 it covers 65 % and 50 % of it, at
25 nothing); there is no shadow when the alpha is 7 or less (8 and 9 still
cast); and the darkening, with the fog and the tint switched off, grows
with the alpha (0.43 at alpha 10, 0.86 at 20, full at 27) and is then
softened by the fog and deepened by the tint (0.26 with both). Its exact
form was not decoded: frame 300 of the same event darkens 0.42 raw at
alpha 20. The game's final darkening on the frames checked is close to
`64 × intensity × u / 128` of the finished picture (EV0007 frames 300 and
900: 0.70 and 0.735 against 0.682 and 0.731), which is what the assembly
uses, for the light that gives the largest value among those whose stored
alpha would reach 8. In EV0014 the alphas are 3 and 6 and the game draws
no shadow; neither does the assembly.

The volumes are extruded in one direction, from the light to that bone,
not radially: Debilitas's shadow from the fireplace lamp lies on the floor
in the game and in the assembly, where a point-light shadow climbs the
walls. Characters are not darkened, and neither are surfaces the room
itself hides from the character (on EV0007 frame 300 the bed's top and the
side facing Debilitas are shaded, the front of the bed and the floor beyond
it are not) or translucent ones (window glass). The assembly tests that
visibility from a point 0.8 of the way from the bone to the light, which
reproduces the checked frames, and leaves the volumes unlimited in length.

**Optional objects (visibility groups).** The room geometry's records carry
a visibility group in the top byte of their control word (exported as
`…_selNNN` nodes). When a room loads, the game switches every group off;
room event scripts switch them on and off with script command 0x4C, which
sets group *a* + 1 + *b* (the progress method `+0x68`, `0x39BAE0`, on the
room manager's mask at `+0x6C`). A gameplay save state shows groups 23 and
25 on in the room it was made in. Which groups a cutscene shows therefore
depends on the room scripts, which the exporter does not read yet, so all
groups are exported visible.

---

## 5. Cutscenes

*121 events with 832 shots. Exported as `cutscenes/<event>/<event>.glb`
with the soundtrack and movie layer. Timing verified against the audio:
30 frames per second.*

The game draws every cutscene letterboxed: black bars over the top and
bottom 56 of the 448 lines (a 640 × 336, 16:9 picture); the camera's field
of view covers the whole 4:3 frame under them. In the movie gallery, every
event fades in from black (black up to frame 1, full at frame 17) and to
black at its end (the last 14 frames), 1/16 per frame, on the game's clock.

Each event folder holds a timeline (`.DH`), the animation data (`.DP`),
per-frame flags (`MARK.BIN`), per-shot settings (`PARAMS.BIN`) and a movie
with the soundtrack (`.SFD`).

### Timeline (DH)

`u16 frames, u16 shots`, then 8 × `u32` offsets of prop-group name lists (0
= none; each list is `u32 count, count × char[16]`, the names of the
objects DP sections 40–47 move; exported as `prop_groups`). From 0x24, one
12-byte record per shot: `u32 mask` (bit 0 = camera, bit *n* = actor slot
*n*), `u8 door mask` (bit *k* = DP section 32 + *k* turns room door *k*),
`u8 prop-group mask` (bit *k* = DP section 40 + *k*), `u16 0`, `u16 start,
u16 end` (frames).

### Animation data (DP)

48 section offsets. Section 0 is the camera (`type, frames, data offset, 0`,
then per frame: position, target, field of view in radians, roll). Sections
1–31 are the actors' animations, one motion bank each (the slot number is
the section number). Sections 32–39 and 40–47 hold named tracks: `count`,
28-byte descriptors (frames, type 2, data offset, `name[16]`), then per
frame 12-byte samples (sections 32–39) or 24-byte samples (Euler +
translation, sections 40–47). All 831 DH/DP pairs agree on frame counts and
actors.

**Doors (sections 32–39).** In a shot whose door mask has bit *k*, the game
turns room door *k* every frame to the second float of the section's sample
(its heading in radians; the director's `Doors_TurnTo`, `0x221A90`). The
heading replaces the door's placement angle and stays after the shot. The
track's name (`null5`, `doornull0`, …) plays no part. Only section 32 is
used, in 17 shots of EV0008, EV0014, DE0041 and DE0042. Exported as rotation
keys on a stand-in named like the room's door node (`ST_011_door0`).

**Actor slots** (from the game's actor table): 1 the player (her current
outfit), 2 Hewie, 3 DB0, 4 DNL, 5 RCG, 6 LRM, 7 LRY, 8 LRO, 9 LRF, 10 GLM,
11 FIN_200, 12 FIW_200, 13 and 14 CRW, 15 WHC, 16 SGM, 17 FS0, 18 RCT,
19 SHT, 20 WIR, 21 DNT, 22 LRC, 23 LRH, 24 FIM_200, 25 HND.

Slot 1 is the live player object, so it shows whatever Fiona is wearing.
Before her change of clothes the player is a separate class whose model is
`O_FIS/FIS_000.PCK`: EV0002 and the first shots of EV0003 show FIS, and
EV0003 continues with slots 12 (FIW_200) and 11 (FIN_200).

**Props (sections 40–47).** Props whose names match one of the room's
named objects animate that object (exported under the room stand-in with
the room object's name); the others are exported as `prop_<name>` empties
(`prop_<name>_2`, `_3` … when a section lists the same name again).
At the start of every shot the game also sets the objects' visibility
(`Cutscene_StartShot`, `0x2CC130`): the objects listed in a prop group are
shown in the shots whose prop-group mask has that group, and hidden in the
other shots if the cutscene uses that group at all. Some of these objects
are doors of their own (`ev_door`, `ev_door_a` / `ev_ddor_b`,
`demo_door0`–`2`) standing in a gameplay door's doorway (EV0004, EV0011,
EV0027, EV0028); the export hides the gameplay door in the shots that show
its event door, else the closed gameplay door covers the swinging one. The export keys this as
scale (0 = hidden) on the room-object stand-ins (379 objects in 76 events).

### Which room a cutscene plays in

Cutscene files do not say which room they belong to. The game's room
handlers list the events they play: room slot *k* is installed by the code at
`0x20A5C8 + 16k`, and the slots map in order to the game's room numbers
0x00–0x37, 0x40–0x70, 0x80–0x88, 0x8A, 0x8C–0x8F, 0x91–0x9B, 0xC0–0xC8,
0xD0–0xD9, 0xE0–0xE9 and 0x100–0x10B (171 rooms). `tools/derive_event_rooms.py`
reads this from the game's program into `hgexport/data/event_rooms.json`.
The exporter then checks each shot's actor positions against the room's
walkable floor (733 of 831 shots confirmed, 18 moved to a neighbouring room
of the same folder, 80 camera-only shots follow the previous one, 30 death
scene shots with actors lying down keep the listed room).

### Per-frame flags (MARK.BIN)

One `u16` per event frame (a few DE files are 136-frame placeholders).
Exported as `marks`, plus the decoded `vibration` and `script_sync`.

* **Bit 0:** camera cut (743 of 770 fall exactly on a DH shot start).
* **Bit 2:** small vibration motor on. **Bits 3–4:** large motor strength
  (0, 85, 170 or 255). Sent to the controller whenever they change; used
  mostly in death scenes.
* **Other bits** are counted per frame and serve as sync points for the
  event's script, which waits for a given bit (bits 1 and 5–15; bit 11 also
  toggles a game flag). The scripts are compiled into the game's program,
  so what each sync point triggers is not decoded. Exported as
  `script_sync` (`[frame, [bit numbers]]`).

### Per-shot settings (PARAMS.BIN)

`u32` count, 12 bytes padding, 48-byte records `u8 1, u8 type, u16 shot, 12
bytes padding, 32 bytes data`, applied at the start of the shot (code
`0x2CA130`). Exported as `cut_parameters`.

* **Type 0, character lighting** (700 records): room light colours are
  scaled by `+24`, the ambient colour `+20` is added, and up to two
  directional lights (colours `+16` and `+36`, angles `+28, +32` and
  `+40, +44`) replace room light slots 2 and 1. A light shines along the
  camera's view direction turned by the first angle about the camera's
  horizontal axis (positive: down, the light comes from above) and by the
  second about the up axis (positive: to the right, the light comes from
  the left); checked against single-light captures, see VERIFICATION. The
  Blender script applies this.
* **Type 1, depth effect** (62 records): four distances, probably
  depth-of-field ranges.
* **Type 2, fog** (285 records): two colours, start and end distance
  (`+0`, `+4`, `+8`, `+12`), a layer word (`+16`) and six bytes that switch
  a light group on characters (not part of the fog). The record replaces the
  current fog (at first the room's) until the next one.

### Fog

The game draws fog as one pass over the finished picture (code
`0x2BB3E0`): it reads the depth buffer as an 8-bit image through a
256-colour palette and lays the result over the picture. The palette
(`Renderer_BuildPalette` with generator 2, `Palette_Colour` `0x1B8910`) runs
from the far colour at entry 0 to the near colour at entry 255, and **halves
the alpha** (with 128 = fully covered, the fog covers at most half). The
depth buffer holds a value linear in 1 / distance (`Camera_ZValue`
`0x121C50`), mapped so the ramp spans the fog's start and end distances. So
for a pixel at distance *d*, with *t* = (1/start − 1/*d*) / (1/start −
1/end), limited to 0–1 (the near colour in front of the start: measured on
DE000C 499, where Fiona is 1 m from the camera and the fog starts at
1.1 m), the colour is the
near colour blended towards the far colour by *t* and covers alpha / 256 of
the picture, in display values; the far colour where nothing was drawn. A
non-zero layer word draws the fog in layer 9, before the characters (layer
10), so they stay clear; with 0 it is drawn last, over everything. The
Blender assembly script rebuilds this in the compositor. (The native port
draws the ramp linearly in distance and at full alpha, which makes it much
too strong; checked against the video capture of EV0004.)

### Movie and soundtrack (SFD)

An MPEG-2 stream with CRI ADX audio (FFmpeg reads it). Event movies carry
the cutscene's soundtrack plus a picture that is black except where it has
content. There are two kinds:

* **Inserts** (512×224 movies in EV0002, EV0004, EV0005, EV0008, EV0021):
  full-screen pre-rendered shots. Their non-black stretches coincide
  exactly with shots that have only a camera and no actors or props; the
  game shows the film instead of the 3D view there.
* **Overlays** (256×224, all other events): effects drawn over black
  (smoke, glass sparkles, fire, lightning, X-ray views, lens flares).

The game decodes a frame per event frame and draws it in renderer layer
0x2B, after the tint, fog and bloom: 16-pixel strips are uploaded to the
GS (at block 0x3000) and drawn as one sprite over the whole 512×448 frame.
An insert is drawn opaque (blending off). An overlay is alpha-blended,
(movie − picture) × A / 128 + picture, with an alpha the movie player
writes per pixel from the decoded luma Y (16–235) and the event's
luminance key (low, high): A = 128 × (Y − low) / (high − low), limited to
0–128 (error 0.3 against the frames in the game's memory in DE000C and
EV0014). Black and dim parts leave the picture as it is. The key sits in
the player's settings (`0x3EA1E4`; the library's default is 31–100), is
fixed for an event, and differs between events: 16–32 for most, 16–255 for
twelve, single values such as 40–200 (DE000C) or 50–255 (EV0014). Where the
game takes it from was not found (not the event's files, the SFD or a table
in the program), so the exporter carries the keys read from the game for
the 109 events in the movie gallery (`MOVIE_ALPHA_KEYS`; variants not in
the gallery take their base event's) as `alpha_key` in the movie extras.
The exported MP4 decodes to the same colours (mean difference 0.3).

Exported as a lossless WAV (the soundtrack) and a video-only MP4, described
by `movie` in the cutscene's extras: `mode` (`inserts` or `overlay`),
`content_frames` (non-black frame ranges) and, for inserts, `insert_cuts`.

---

## 6. Sound and music

* **ADX** (voices, effects, cutscene audio): CRI ADPCM with 18-byte blocks
  per channel and loop points in the header. The decoder's output is
  bit-identical to FFmpeg's. Exported as WAV with loop points.
* **HD/BD sound banks:** Sony's bank format. The `.HD` holds chunks named
  `Vers`, `Head`, `Vagi`, `Smpl`, `Sset`, `Prog`; `Vagi` lists each sample's
  offset in the `.BD`, sample rate and loop flag. The `.BD` is PS-ADPCM
  (16-byte frames of 28 samples, flags 1 = end, 2 = loop, 4 = loop start).
  **Instruments:** `Prog` lists programs (split table, volume, pan, …),
  20-byte splits (sample set, key range, pitch bend, key follow, volume,
  pan, transpose, detune), `Sset` sample sets (velocity range, samples),
  42-byte `Smpl` records (sample, root note at +0x0B, detune, pan, volume at
  +0x10, SPU2 envelope at +0x12). Exported as WAVs per sample and, for music
  banks, as a SoundFont (`.sf2`).
* **SQ music sequences:** Sony's sequence format. The `IECSidiM` chunk holds
  a MIDI stream with two twists: when bit 7 is set on an event's last data
  byte, the next event has no delay value, and note-off events carry only
  the key. Loop points are NRPN 99 set to 0 and 1. Exported as MIDI with
  loop markers and as a rendered WAV.
* **SDT sound-effect tables** (130 files, one per effect bank): 16-byte
  records, the effect id is the record number, all-zero records are unused
  (1,143 used). Byte 0 version (1); byte 2 priority (bits 0–6) and tie rule
  (bit 7); byte 3 voice (bits 0–5) and, in bit 7, "the next record plays as
  another layer"; byte 4 pan (bits 0–6, used when bit 7 is set; 0, 64, 127 =
  left, centre, right); byte 5 volume offset (signed); byte 6 transpose
  (signed); byte 8 program; byte 9 sample (the program's split for key
  12 + n); byte 10 unused. Exported next to the bank's WAVs as
  `<bank>_effects.json`. Three effects of `ST1_058` name samples the bank
  does not have.
* **Reverb:** the game switches on the sound chip's "studio B" reverb at
  volume 0 and never raises it, so music plays without reverb. The rendered
  WAVs match that.
* **Movies (SFD):** see [the cutscene movie](#movie-and-soundtrack-sfd).
  Stand-alone movies are exported as MP4 with AAC audio.

---

## 7. Text

`SUBSCR/MSG_*.BIN` and room slot 10: `u16` highest message number, `u16`
offsets, then zero-terminated ASCII text. Control bytes: 0x01 new line, 0x02
new page, 0x03 and 0x04 highlighting codes. Exported as JSON and
plain text.

---

## 8. Other files

### GAME_FIX.GFM (always-loaded props)

*Exported as `misc/GAME_FIX.glb`.*

Four small props the game keeps loaded, drawn with `GAME_FIX.TEX` image 2:
a hanging ring with a pendulum over an octagonal floor symbol and a soft
ground disc (about 2.2 × 1.3 m), two small gems and a small hooded figure
(about 0.5 m).

* `u32` entry offsets (4; the first equals the table size). An entry is a
  chain of parts, each aligned to 16 bytes after the previous one. A part
  header holds the vertex count *n*, a palette number, the part count (in
  the first part), 0, four offsets (UV, colour, position, flag arrays) and a
  start position (`i32 x, y, z, w`).
* **UV:** *n* × `u16 s, t`, divided by 32768 over the 512×256 image.
  **Colour:** *n* × RGBA bytes (0x80 = 1.0). **Position:** *n* × `i16 x, y, z`
  differences, each vertex adding to the previous one, starting from the
  header's position; divide by 16. **Flag:** *n* bytes; 1 = no triangle ends
  at this vertex.
* Drawn as blended triangle strips. Only the palette changes between parts:
  palette 1 is the natural colouring, 2–6 are tinted versions (palette 5
  also fades the alpha). Exported with the palettes as material variants;
  each variant uses the transparency mode right for its palette.

### OBSTACLE.MTN

A single 30-sample curve (0 → 0.77 → 0): `u32` count, `u32` offsets, then
per curve `u32 n` and *n* floats. Gameplay code uses it while the player
plays a certain group of animations near specially flagged floor; the flag
is set while the game runs, not stored in any file. Because it only makes
sense inside gameplay, it is exported raw as `misc/OBSTACLE.json`.

### Memory-card icon

*Exported as `misc/ICON00.glb` (animated model) and
`textures/SUBSCR/ICON00.png` (texture).*

`SUBSCR/ICON.SYS` and `SUBSCR/ICON00.ICO` are the standard PlayStation 2
memory-card browser formats. `ICON.SYS` holds the save's title in Shift-JIS
(ＨＡＵＮＴＩＮＧ ＧＲＯＵＮＤ), background colours and lights (all zero),
ambient light 0.3 and the icon file names. `ICON00.ICO` holds 6 shapes of
1,338 vertices (446 triangles; `i16/4096`, Y pointing down), normals, UVs,
a constant vertex colour, a 60-frame loop that blends shape 5 → 4 → … → 0,
and a 128×128 16-bit texture. It shows Hewie walking. Exported Y-up, with
shape 0 as the mesh, shapes 1–5 as blend shapes, the loop as weight keys at
60 frames per second (the browser's rate) and `ICON.SYS` in the extras.

### Frame rate

The game counts video fields (60 per second) and only shows a new frame
after at least two have passed (`0x1BEEF0`), so it renders at most 30
frames per second. Animations, cutscenes and animated textures are all
exported at 30 frames per second.
