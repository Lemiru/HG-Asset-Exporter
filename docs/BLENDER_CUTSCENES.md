# Assembling a cutscene in Blender

*Haunting Ground* plays most of its cutscenes in real time: the game takes
the same character models and rooms you walk around in, and animates them
with a camera, character movement and moving props. The exporter keeps it
that way. A cutscene file (`cutscenes/EVxxxx/EVxxxx.glb`) contains only
what is unique to that cutscene:

* the camera, with its movement and zoom,
* the movement of every character in it (body, face and hands),
* the movement of props.

The characters and rooms are not copied into it; they are the same files you
find in `characters/` and `stages/`. This guide shows how the pieces
connect and how to put them together into one scene, either with one
command (section 2) or by hand (section 3).

You need **Blender 4.4 or newer** (tested with 5.2). Older versions lack
*action slots*, which the cutscene animation relies on (explained below).

> **Just want one file?** Export the cutscene with everything inside:
> `python -m hgexport cutscene EVxxxx out.glb --embed-models`, then import
> that single file. It is larger (characters and rooms are repeated in every
> cutscene) and you lose the extras the script adds (physics, sound, movie,
> lighting), but it needs no assembly.

---

## 1. How the files connect

### The files of one cutscene

Taking the cutscene `EV0014` (dinner with Daniella) as an example:

| File | What it holds |
| --- | --- |
| `cutscenes/EV0014/EV0014.glb` | the camera; one *stand-in* skeleton per character; one *blend-shape carrier* per character; empty *stand-ins* for the room and for every room object a prop moves; one animation (`EV0014`) that moves all of them |
| `cutscenes/EV0014/EV0014.wav` | the complete soundtrack (voices, music, effects), starting at frame 0 |
| `cutscenes/EV0014/EV0014.mp4` | the cutscene's movie layer, same length, no sound. It is black except where the game shows video: either full-screen pre-rendered shots that replace the 3D view (in EV0002, EV0004, EV0005, EV0008, EV0021) or effects laid over the 3D view (smoke, sparkles, fire). The extras of the cutscene say which kind it is and when it has content |
| `characters/O_FIN/FIN_000.glb` | Fiona: mesh, skeleton (`FIN_000_skeleton`), blend shapes, her gameplay animations, alternative texture sets |
| `characters/O_DNL/DNL_000.glb`, `characters/O_DNT/DNT_000.glb` | the other characters, built the same way |
| `stages/ST_010/ST_011.glb` | the room: geometry, doors, movable objects, gameplay cameras |

A **stand-in** is a placeholder in the cutscene file with the same name and
structure as the real object, so that its animation can be handed over.

### Everything is linked by name

The cutscene stores which file each stand-in belongs to as a *custom
property* (in Blender: *Object Properties > Custom Properties*):

```
EV0014.glb                                    other exports
──────────                                    ─────────────
EV0014                (empty, scene root)
├─ EV0014_camera      camera, animated
├─ A01_FIN_000        stand-in skeleton   ──► characters/O_FIN/FIN_000.glb
│  │  model_file      = characters/O_FIN/FIN_000.glb
│  │  texture_variant = cutscene
│  │  hidden_parts    = [earrings_inquisitor, ...]
│  │  bones J00, J01 ...                      same names as in FIN_000_skeleton
│  └─ A01_FIN_000_blendshapes  tiny mesh with the face and hand shape keys
│                                             same names as on the FIN_000 mesh
├─ A04_DNL_000 ...
├─ ST_011             empty stand-in      ──► stages/ST_010/ST_011.glb (its root is also "ST_011")
│  │  room_file       = stages/ST_010/ST_011.glb
│  ├─ ST_011_obj000_spoon   empty, animated  ──► room object "ST_011_obj000_spoon"
│  └─ ST_011_door0          empty, animated  ──► room door "ST_011_door0"
└─ prop_…             empty, animated      a prop with no matching room object
```

`A01` is the character's slot number in the cutscene. `model_file` and
`room_file` are paths relative to the export folder.

### How the animation is stored

All animation of a cutscene is **one Blender action** named after it
(`EV0014`). Since Blender 4.4 one action can animate several objects; each
object's part of it is a **slot**, named after the object it was made for:

| Slot | Animates | Give it to |
| --- | --- | --- |
| `EV0014_camera` | camera position and rotation | (already on the camera) |
| the camera data's slot | the lens (zoom), changing at each cut | (already on the camera) |
| `A01_FIN_000` | every bone of Fiona, and whether she is visible | the `FIN_000_skeleton` armature |
| `A01_FIN_000_blendshapes` | her face and hand shape keys | the shape keys of the `FIN_000` mesh |
| `ST_011_obj000_spoon` | the spoon's position and rotation, and in which shots it is visible | the room object `ST_011_obj000_spoon` |
| `ST_011_door0` | the door's swing (only in cutscenes that move a door) | the room door `ST_011_door0` |
| `ST_011` (only in cutscenes with several rooms) | whether the room is visible | through parenting, see step 7 |

Two conventions make the hand-over work:

* **Bones have the same names** in the cutscene and in the character file
  (`J00`, `J01`, …). An action that moves `J05` on the stand-in moves `J05`
  on the real skeleton. Bones named `X..` are never animated; the game moves
  them with its physics (cloth, hair, chest).
* **Visibility is animated as scale.** A character who is not in a shot is
  scaled to 0 for that shot, and so is a room that is not in use. glTF has
  no visibility setting that can be animated, so this is the portable way
  to switch things on and off.

---

## 2. The quick way: one command

`tools/blender_assemble_cutscene.py` does everything in section 3 and adds a
few things that make the result look like the game:

```bash
blender --python tools/blender_assemble_cutscene.py -- export/cutscenes/EV0014/EV0014.glb
```

Blender opens with the finished scene. To save a `.blend` file without
opening Blender's window:

```bash
blender -b --python tools/blender_assemble_cutscene.py -- export/cutscenes/EV0014/EV0014.glb --save EV0014.blend
```

You can also run it from inside Blender: open the script in the *Text
Editor*, put the cutscene's path into `CUTSCENE = r"..."` near the top
(a relative path is read from this project's folder) and press *Run
Script*. Run that way, it adds the cutscene to the file you have open.

Options, all placed after the cutscene path:

| Option | Effect |
| --- | --- |
| `--save FILE.blend` | save the assembled scene |
| `--root DIR` | the export folder, if the cutscene file was moved out of it |
| `--room-anims ambient\|all\|none` | which room animations to keep (default `ambient`: animated textures and fans loop, doors and curtains stay still) |
| `--keep-current` | add to the open file instead of starting a new one |
| `--resolution WxH` | render size (default 1440x1080; the game is 4:3) |
| `--no-sound` | leave out the soundtrack |
| `--no-movie` | leave out the movie layer |
| `--no-reflections` | leave out the character reflections in windows, mirrors and floors |
| `--no-character-shadows` | leave out the characters' shadows on the room |
| `--no-letterbox` | leave out the black bars over the top and bottom of the picture |
| `--end-fade` | add the movie gallery's fade to black over the event's last 14 frames |
| `--start-fade` | add the movie gallery's fade in from black over the event's first 17 frames |
| `--no-cut-lighting` | keep the rooms' lights instead of the per-shot lighting |
| `--no-fog` | leave out the game's fog |
| `--shadows` | let lights cast shadows (the game's do not) |
| `--blended` | keep Blender's *Blended* mode for character transparency |
| `--render-space display\|linear` | render in display values like the PS2 (default `display`), or in Blender's scene-linear light |
| `--view-transform T` | colour management view (default *Raw* with `--render-space display`, else *Standard*) |
| `--no-physics` | leave cloth and hair in their rest pose |
| `--bake-physics` | pre-compute the physics for the whole cutscene |

Renders through the cutscene camera are identical to those of the
self-contained export (checked on EV0002, EV0014 and DE002F).

### What the script adds

**Sound.** The soundtrack (`EVxxxx.wav`) is placed in the sequencer at
frame 0, with playback set to follow the audio.

**Letterbox and end fade.** The game shows every cutscene with black bars
over the top and bottom eighth of the 4:3 picture (56 of its 448 lines,
leaving a 16:9 picture); the script adds them last in the compositor, over
everything (`--no-letterbox` leaves them out). With `--end-fade` it also
adds the fade to black the movie gallery plays at the end of every event:
from 14 frames before the end the picture is darkened by 1/16 per frame
(the event ends at 3/16 and the gallery cuts to black); `--start-fade` adds
its fade in (black up to frame 1, then 1/16 brighter per frame). Whether
the story plays the same fades was not checked, so they are off by default.

**Per-shot lighting.** The game changes how characters are lit at every cut:
it scales the room's lights, changes the ambient light and adds up to two
directional lights that turn with the camera. The script keys the same
changes (two sun lamps `cut_light0/1` follow the camera). Rooms have their
lighting painted in, so this only affects characters.

**Character shading.** The game adds the ambient and its lights into a
vertex colour (128 = full) and multiplies the texture's on-screen value by
it, so a character lit at half strength shows half the texture's screen
brightness. Lighting in linear light, as Blender's materials do, makes the
same light look much brighter (about twice in dim cutscenes). By default
(`--character-shading game`) the script gives the characters' materials a
shading group (*HG Game Shading*) that does it the game's way: the lights'
diffuse sum (from *Shader to RGB*) plus the cut's ambient, times the texture
in display values. The lights are set in the game's units for this (a light
of colour 128 facing a surface gives 1.0) and the world is black, as the
ambient is in the group. *Shader to RGB* only works in EEVEE; for Cycles use
`--character-shading physical`, which keeps glTF's lit materials and the
export's light scale (brighter than the game). The game uses only the three
strongest lights per character and fades point lights linearly over their
range; the script uses all of the room's lights with Blender's inverse-square
falloff, matched to the game's at half the range.

**Display values (render space).** The PS2 draws everything in display
values: it interpolates the vertex colours across a triangle, multiplies the
texture by them and blends translucent layers, all on the 0–255 values that
go to the screen. Blender does these in linear light, which brightens
exactly the things the rooms are made of: their painted-in lighting is
mostly dark gradients (interpolated in linear light the middle of a
gradient from 0 to 40 shows about 28 instead of 20), and their soft light
falloffs are translucent copies of a surface over a black one (a layer at
50 % shows at 75 % over black). By default (`--render-space display`) the
script therefore renders in display values: textures are read raw, the
vertex colours are stored as display values, the texture × vertex colour
and the character shading round down to whole 0–255 steps as the GS's
integer arithmetic does, and so do the compositor's passes. The frame holds
display values, so the view transform is *Raw* (the viewport and PNG
renders show the game's values; EXR renders hold display values, not
scene-linear light). The modern look converts to linear only for its HDR
lamps, bloom and shoulder. `--render-space linear` keeps Blender's
scene-linear materials (with *Standard*), and `--character-shading
physical` needs it.

**Game-like rendering.**

* The game's lights cast no shadows on the characters, so the script
  turns the lights' shadows off (the characters' shadows on the room are
  drawn separately, see *Character shadows*).
  Otherwise hair strands and low-poly edges throw hard shadows across
  faces.
* Characters' transparent materials are switched from *Blended* to
  *Dithered*. In *Blended* mode, Blender's EEVEE does not sort the
  transparent triangles of one mesh, so the inside of a hair shell or a
  skirt shows through its front as jagged triangles. *Dithered* sorts them
  properly, as the game does. Cycles is not affected.
* Rooms keep *Blended*. Their transparent parts are mostly soft shadows
  painted onto surfaces (under plates and cutlery), which must blend over
  the surface beneath. The script also lifts them 1 mm off that surface so
  Cycles does not hide them.
* The colour view is set to *Raw* (display values, above) or, with
  `--render-space linear`, *Standard*. Blender's default (AgX) darkens and
  greys the colours; the game shows them as stored.
* Highlights are left as exported. The default character files have no
  specular highlight, as in the game's normal picture. For the game's
  highlights on skin and metal, export the characters with `--specular`
  (`python -m hgexport all ... --only-category characters --specular`).
  The script then uses the materials as they are.

**Screen look and fog.** Each room has a colour mood that the game paints
over the finished picture: a *tint* (a soft coloured glow plus local
contrast; blue at night), the *fog*, and a *bloom* around its lamps (see
[FORMATS.md](FORMATS.md#screen-look-tint-glow-geometry-and-bloom)). The
script rebuilds them in the compositor (*HG Screen*), keyed per shot, in
the game's order: tint, fog, the lamps put back on top (they are drawn
after both in the game), and the bloom from the lamps. The game works on
display values, so the compositor converts the render to sRGB first and
back at the end.

* Fog: beyond the fog's start distance a colour is laid over the picture,
  changing from the near to the far colour with the distance (from the depth
  pass), as the game's palette ramp does. Some shots keep the characters
  clear of it; a shader output (`hg_character`) marks them.
* The lamps are the rooms' `_glow` objects (and `g_chandelie`); a shader
  output (`hg_glow`) marks them.
* `--look` chooses how the tint and the bloom are made:
  * `hybrid` (default): the game's formulas, strengths and order, with
    smooth Gaussian blurs sized to the render resolution;
  * `emulate`: also the game's half-size buffers, point sampling and 8-tap
    blurs, so the glow is as blocky as the original's;
  * `modern`: the tint as in `hybrid` (it is a colour grade with local
    contrast, which needs the game's curve, see section 5), and the lamps as
    HDR emission put back after the tint and the fog, with a smooth bloom
    over a threshold in linear light and a soft highlight shoulder that keeps
    their colour;
  * `none`: fog only.
  The blur sizes follow `--resolution` at the time the script runs; run it
  again after changing the render size.
* `--time-of-day` chooses the tint the rooms show: `story` (default) as the
  cutscene plays in the story (warm only for the opening EV0002 and
  EV0003), `gallery` as the game's movie gallery shows it (the rooms' own
  values and what the event's replay script sets: warm for eleven events of
  the first area, a blue bloom for DE0003 and DE0011), `warm`, or `night`
  (the rooms' own values).

These effects only show in renders and in the viewport's compositor preview.

**The movie layer.** The game draws the event movie over the finished
picture, after the tint, fog and bloom, stretched over the whole frame; the
script does the same in the compositor (the *effect_movie* image node).
Full-screen pre-rendered shots replace the picture during their cuts.
Effect movies (lens flares, smoke, sparkles) are blended in with an alpha
the game takes from each pixel's brightness with the event's luminance key
(`alpha_key` in the movie extras): pixels darker than its low value leave
the picture as it is, brighter than its high value cover it. So the dim
parts barely show: DE000C's lens flare is a faint haze with a brighter
core, not the full movie. A movie strip in
Blender's sequencer would replace the render, so it is not used.

**Reflections.** Some rooms show the characters mirrored in a window, a
mirror or the floor (for example Fiona at the bedroom window in EV0005).
The game draws them without clipping them to the glass: the mirror image
of every character it accepts, over the picture at the room's strength,
softened and at half resolution, before the tint and fog. The script does
the same: for each mirror that is live in the event (`reflections` in the
cutscene extras, `room_effects.reflections` in the room), a view layer
*HG reflection <slot>* holds a mirrored instance of every actor and of
the lights (the game lights a character before mirroring it), the
compositor blends that layer in, and an actor's instance is scaled to 0 on
frames where the game would not draw it: where the mirror image of its
bone 0, with a box of 20 by 60 pixels of the 512 × 448 frame around it
(Hewie 40 by 30), misses the rectangle around the mirror on screen. Each
mirror is one more render layer, so these events render slower.

**Character shadows.** The game darkens the room behind each character
with stencil volumes of its shadow boxes (the `<model>_shadow` export),
cast from one of the room's lights. The script imports each actor's boxes,
binds them to its skeleton and gives the actor two view layers that render
only the room's opaque faces in a plain material: *HG shadow <actor>*, lit
by a sun the boxes cast from, and *HG shadow <actor> lit*, where the room
itself casts from a point light between the character and the chosen
light. The compositor darkens the picture, after the tint and the fog,
where the first layer is in shadow, the second is not (the room does not
stand between the surface and the character) and no character is drawn.
The sun's direction (from the chosen light to the character), the point
light and the strength are keyed per frame from the game's rule (FORMATS.md);
characters are never darkened, nor are translucent surfaces such as window
glass. Each actor with shadow boxes adds two render layers.

**Cloth and hair physics.** The game does not animate cloth, hair or chests
with keyframes; it simulates them while the game runs (see
[SECONDARY_MOTION.md](SECONDARY_MOTION.md)). The exported files leave these
bones (the `X` bones) in their rest pose. The script rebuilds the game's own
simulation, with the game's own settings, as a live Geometry Nodes setup:

* Every character with physics (Fiona's main, early and sheet outfits,
  Daniella, Riccardo, Lorenzo, Debilitas) gets an object
  `<character>_physics` carrying the *HG Physics* modifier. Two helper
  objects follow the body: `…_physics_frames` and `…_colliders`.
* It covers ponytails and braids, Fiona's skirt, sheet, bangs and bow,
  Daniella's apron and hair, Riccardo's robe and hair, Lorenzo's coat and
  hair, a hanging part on Debilitas and the chest movement.
* It steps once per frame at 30 fps and settles at every cut, the way the
  game does. Characters off screen stay simulated, as in the game.

Like any Blender simulation it is computed frame by frame. **Play the
timeline from frame 0, or bake it, before rendering a single frame or a
range that does not start at 0.** Jumping to a frame restarts the
simulation there; the result is plausible but only matches the game after
about 20 frames. To make every frame exact, use `--bake-physics` (EV0004:
about 7 minutes, 40 MB more in the .blend), or bake in the modifier's *Bake*
panel. Free the bake there to go back to the live simulation, for example
after changing the animation. A command-line render of the whole animation
(`blender -b file.blend -a`) steps through every frame and needs no bake.

Compared with a recording of the game running in an emulator, Fiona's skirt,
ponytail, bangs, bow and chest and Riccardo's robe and hair follow the game
to within a millimetre in every visible shot.

**Engines.** In Unity or Unreal the same rendering advice applies: let the
characters' transparent materials write depth (or use dithered or
alpha-to-coverage transparency), draw the rooms' transparent materials after
the opaque ones without depth writes, and switch off shadows from the room
lights on characters. Export the rooms with `--light-units unitless` for
Godot and Unity's URP, which use glTF light intensities as they are (the
default physical units are about 2000 times too bright there); this script
works out its light energies from the game values, so it takes rooms in
either units.

---

## 3. Doing it by hand

These are the steps the script performs. Doing them once by hand is the
best way to understand the setup, and you need them to assemble a cutscene
into a scene of your own.

### Step 0: Prepare the scene

1. *File > New > General*, then delete the default cube, light and camera
   (or start from an empty file).
2. **Set the frame rate to 30 before importing anything:** *Properties >
   Output > Format > Frame Rate > 30*. The glTF file stores times in
   seconds, and the importer turns them into frame numbers using the
   current frame rate. At Blender's default 24 fps, keys land between frames
   and cuts blur. If you forgot, delete everything and import again.
3. Use the **same import settings for every file**; the defaults are fine.
   In particular *Bones & Skin > Bone Dir* must be the same for the cutscene
   and the characters, because it changes how bone rotations are read.

### Step 1: Import the cutscene

*File > Import > glTF 2.0*, pick `EV0014.glb`. You get the objects shown in
the diagram above. `Icosphere` objects in the outliner are the importer's
bone display shapes; ignore them.

Click `A01_FIN_000` and look at *Object Properties > Custom Properties*. It
tells you which file to import next (`model_file`), which texture set the
cutscene uses (`texture_variant`) and, for Fiona, which jewellery to hide
(`hidden_parts`). The room stand-in `ST_011` has `room_file`.

### Step 2: Import a character

Import the file named in `model_file`, e.g. `characters/O_FIN/FIN_000.glb`.
You get:

* `FIN_000_skeleton`, the armature;
* `FIN_000`, the body mesh with its shape keys;
* for Fiona, her jewellery as separate objects (`neck_default`,
  `neck_jade`, `earrings_inquisitor`, …; see FORMATS.md);
* her gameplay animations as actions, stored as **muted** NLA tracks so
  they do not interfere.

### Step 3: Give the skeleton the cutscene movement

1. Select `FIN_000_skeleton`.
2. *Properties > Object > Animation*: set **Action** to `EV0014` and
   **Slot** to `A01_FIN_000`. (The *Dope Sheet > Action Editor* header has
   the same two fields.)

Scrub the timeline: Fiona now moves like the stand-in, including vanishing
(scale 0) in shots she is not in. If she stays in her rest pose, the slot is
still empty; an action without a slot animates nothing.

### Step 4: Give the face and hands their animation

1. Select the body mesh `FIN_000`.
2. In the *Dope Sheet*, switch the mode to **Shape Key Editor** and set
   **Action** `EV0014`, **Slot** `A01_FIN_000_blendshapes`.

The tiny mesh `A01_FIN_000_blendshapes` only exists to carry these curves:
it has the same shape-key names as the body (`face_01_mouth`…`face_15_expression`,
`hand_l_01`…, `hand_r_01`…). Characters without shape keys (e.g.
`DNT_000`) have no carrier; skip this step for them.

In the Python console (*Scripting* workspace), steps 3 and 4 are:

```python
import bpy
O = bpy.data.objects

def take(dst, src):            # dst gets the action and slot that animate src
    d = dst.animation_data_create()
    d.action = src.animation_data.action
    d.action_slot = src.animation_data.action_slot

take(O["FIN_000_skeleton"], O["A01_FIN_000"])
take(O["FIN_000"].data.shape_keys, O["A01_FIN_000_blendshapes"].data.shape_keys)
```

### Step 5: Cutscene textures and jewellery

**Textures.** Most characters have a more detailed face texture for
cutscenes. The character file contains it as a glTF *material variant*
called `cutscene`; `texture_variant` tells you whether the cutscene uses it.
Only the face texture (`m00`) changes, but the body uses it in several
material slots. In the body's *Material* slots, replace every
`FIN_000_m00_…` material by its twin **`FIN_000_cutscene_m00_…`** (`_opaque`,
`_mask`, `_blend`). The twins are already in the file after import.

Be careful with the *glTF Variants* panel in the sidebar when several
characters are in the file: Blender keeps only the variant list of the
**last** imported file, and *Display Variant* applies to every mesh. After
importing Hewie (variants `default, hew_201, hew_202`) and then Daniella
(`default, cutscene, …`), choosing `cutscene` would also switch Hewie to
`hew_201`. Swapping the materials by hand avoids this (the script does so
after each import). In the console, for one character:

```python
for s in O["FIN_000"].material_slots:
    if s.material:
        twin = bpy.data.materials.get(s.material.name.replace("FIN_000_", "FIN_000_cutscene_", 1))
        if twin:
            s.material = twin
```

**Jewellery and the glass shard.** Fiona's file contains all her unlockable
earrings and chokers as separate objects, and Daniella's has her glass shard
as the object `glass_shard`. Cutscenes show only Fiona's default neck
accessory and no shard: hide the objects listed in `hidden_parts` (both the
eye and the camera icon in the outliner), or delete them.

### Step 6: Hide the stand-ins, repeat for every character

Hide `A01_FIN_000` and its carrier (eye and camera icons), or delete them.
The action stays, because the real skeleton now uses it. Repeat steps 2 to 6
for every `Axx_…` object.

### Step 7: The room

1. Import the file named in `room_file` (`stages/ST_010/ST_011.glb`). Its
   top object is also called `ST_011`, so Blender renames it
   **`ST_011.001`**. That is expected; everything inside keeps its name.
2. Parent it to the stand-in: click `ST_011.001`, Shift-click the stand-in
   `ST_011`, *Ctrl+P > Object (Keep Transform)*. Both sit at the origin, so
   nothing moves. Some cutscenes use several rooms (EV0002 uses ST_020 and
   ST_021); they overlap at the same place, and this parenting is what shows
   the right one in each shot. Do it for every room.
3. Rooms come with their gameplay animations (doors, curtains, the gameplay
   camera paths), which do not belong in the cutscene. If any of these
   objects has an action (*Object > Animation*, or for curtains the mesh's
   *Shape Key Editor*), clear it with the X button. Hide the room's own
   cameras (`ST_011_cam0`, …).
4. Animated textures (`ST_xxx_atlasNN`) and fans (`…_fan…`) come with one
   cycle of their loop. To keep them running, select them, open the *Graph
   Editor*, select all channels and use *Channel > Extrapolation Mode > Make
   Cyclic (F-Modifier)*.

### Step 8: Props

Every empty under the room stand-in (such as `ST_011_obj000_spoon`) carries
the movement of the room object with the same name. Select the room's object
(`ST_011_obj000_spoon.001` if the name clashed) and in *Object > Animation*
set **Action** `EV0014`, **Slot** `ST_011_obj000_spoon`. Then hide the empty.
Outside the shots that move it, the object stays where the room file puts it;
like in the game, objects the cutscene animates are hidden (scaled to 0) in
the shots that do not use them. Doors that a cutscene swings (`ST_011_door0`)
work the same way: give the door's slot to the room's door object (after
clearing its gameplay action, step 7).

Empties named `prop_…` directly under the cutscene root are props without a
room object (spots the game used to place effects or attached items). They
are already animated: parent anything you like to them, or ignore them.

### Step 9: Camera, timing and sound

1. **Active camera:** select `EV0014_camera`, *View > Cameras > Set Active
   Object as Camera* (Ctrl+Numpad 0).
2. **Picture shape 4:3:** *Output > Format > Resolution* 1440 × 1080 (or
   640 × 480). The game rendered 4:3 and the camera's zoom is set for that.
   A 16:9 picture shows more at the sides than the game did.
3. **Frame range:** start at **0**, end at the last key of the action (for
   example 3488 for EV0002; `manifest.json` lists every cutscene's length).
4. **Sound:** in the *Video Editing* workspace, *Add > Sound*, pick
   `EV0014.wav` and place it at frame 0. Set the timeline's *Playback >
   Sync* to **Sync to Audio**. For the movie layer, see *The movie layer* in
   section 2.

Press play. Cuts happen instantly, characters appear and disappear with
them, and props move inside the room, as in the game.

### Optional: markers and per-shot data

The cutscene's root object (`EV0014`) carries more data as custom
properties: `marks` (per-frame flags; bit 0 marks a camera cut),
`cut_parameters` (per-shot lighting, fog and depth effect, explained in
`cut_parameters_note`), the controller vibration and more. To see the marks
on the timeline, and the characters' animation markers in the Action Editor,
run:

```bash
blender --python tools/blender_markers.py -- export/cutscenes/EV0014/EV0014.glb export/characters/O_FIN/FIN_000.glb --import
```

---

## 4. Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| A character stands in its rest pose | The action is set but its **Slot** is empty (step 3) |
| Motion stutters, cuts blend over a frame | The frame rate was not 30 when importing; set it and import again (step 0) |
| Limbs twist oddly | Different *Bone Dir* import settings for cutscene and character (step 0) |
| The face does not move | The shape-key action or slot is not set on the body mesh (step 4) |
| Wrong textures after using the glTF Variants panel | The variant list belongs to one import; swap materials by hand (step 5) |
| Earrings, chokers or Daniella's shard appear where the game shows none | Hide the objects listed in `hidden_parts` (step 5) |
| A shot shows the room or an empty view where the game shows a pre-rendered film | The movie layer is missing (section 2, *The movie layer*) |
| Colours darker and greyer than in the game | The view transform is AgX; use *Raw* for the script's display-value scene (*Standard* with `--render-space linear`) (*Render Properties > Color Management*) |
| Two moons, stray glass shards or props the game does not show | Room objects named `…_selNNN` (custom property `visibility_selector`) are switched on and off by the game's event scripts, which are not decoded, so all are shown. Hide the ones the shot does not need (EV0018: `ST_060_main_sel023` is the second moon, `ST_060_effects_sel021` the glass debris before the dome breaks) |
| Two rooms on top of each other | The rooms are not parented to their stand-ins (step 7) |
| Doors swing or curtains move on their own | Clear the room's gameplay actions (step 7) |
| A prop does not move | Give the prop's slot to the room object, not to the empty (step 8) |
| The picture is wider than the game's | The resolution is not 4:3 (step 9) |
| Cloth or hair jumps or looks wrong on a single frame | The physics was not played from frame 0; play from the start or bake (section 2) |
| Objects named `….001` | Normal: stand-ins and real objects share names on purpose |

## 5. Other programs

The same three links work in Unity, Unreal or any glTF tool: bones match by
name, shape keys match by name, and room objects match by object name.
Engines that bind animation to an object's *path* in the hierarchy (Unity's
Animator, for example) need the clip's paths changed from the stand-in
(`A01_FIN_000/...`) to the character's own hierarchy; there, the
self-contained export (`--embed-models`) is the simpler choice.

**The screen look in an engine.** The room file's `room_effects` extras hold
the game's values (colours and alphas on 0–255), the warm variant in
`script_variants` for the cutscenes whose `story_state` calls for it, and
`engine_look`, the same look prepared for engines, per variant (`night` is
the rooms' own values):

| Game effect | Engine setting | `engine_look` values |
|---|---|---|
| Tint | a colour grade on display values with local contrast (a custom post effect, or a LUT, see below) | `grade`: the response curve F per channel at 33 even steps of display value; `detail_gain`; `tint_sigma` |
| Lamps (`<room>_glow`) | export the rooms with `--emissive-glow` (emissive materials, strength `lamp_emission`, 4) | — |
| Bloom (`bloom`) | Bloom over a threshold (only the lamps exceed it) | `lamp_bloom_threshold` 1, `lamp_bloom_tint`, `lamp_bloom_intensity`, a chain of Gaussian levels `lamp_bloom_levels` from `lamp_bloom_sigma` (`lamp_bloom_radius` is its half-energy radius) |
| Fog (`fog`) | Distance fog from the near to the far colour, alpha / 256 | start and end in game units (0.1 m); the game's ramp is linear in 1 / distance |

Sizes are fractions of the picture width. The tint is exactly

    display' = F(B) + detail_gain × (display − B),   B = gaussian(display, tint_sigma)

per channel, in display values (0–1) after tone mapping: flat areas follow
the curve, detail finer than the blur is scaled by `detail_gain`. It cannot
be made from a gain, a bloom and a sharpen in linear light: the game's glow
buffers clip (in some rooms the darkening stops above display value 0.16)
and the sRGB curve makes a display gain tone-dependent. Tried on four
cutscenes, the best such decomposition was within 2 levels of the game in
warm and night rooms but 15–24 levels off (brown, the chandeliers' blue
gone) in room 0x0C's hall, and lost 5–25 % of the local contrast. Applying
`grade` as an ordinary colour-grading LUT gives F(display) instead: flat
areas exact, detail with the curve's slope instead of `detail_gain`.
