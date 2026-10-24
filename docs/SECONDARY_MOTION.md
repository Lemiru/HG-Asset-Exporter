# Cloth, hair and chest movement ("secondary motion")

Skirts, aprons, coats, ponytails, braids and chests in *Haunting Ground*
are not animated by hand. The animators only keyed the main body; while the
game runs, small physics simulations make the loose parts swing, hang and
bounce in response to it. This is called *secondary motion*.

The exported files keep those parts in their rest pose, because the motion
is computed by the game and not stored anywhere. Instead, this document
describes exactly how the game computes it, and
`tools/blender_assemble_cutscene.py` rebuilds the same simulation in Blender
(see [the last section](#the-blender-reproduction)).

Everything here was worked out from the game's program (`SLUS_210.75`) and
checked against the running game in the PCSX2 emulator: the decoded steps,
fed the game's own state, reproduce the game's next frame to within float
rounding (see [How it was checked](#how-it-was-checked)). Addresses such as
`0x2ECE50` point to the code in the program, for anyone who wants to look
it up; you can skip them.

## The idea in short

* Each simulated bone gets a **node**: a point with a position and a
  velocity, hanging from its parent bone (or from the previous node of a
  chain) at a fixed distance.
* Nodes are grouped in **containers** that share gravity, damping and a
  list of **colliders** (spheres and capsules attached to body bones, which
  push nodes away so cloth and hair do not pass through the body).
* Once per frame (30 per second) the game updates the colliders, moves every
  node one step according to its type, and then turns each simulated bone
  to point at its node (the **write-back**).
* When a character's animation changes abruptly (every cut in a cutscene),
  the game **resets**: some chains are put back into a default position, and
  30 steps are run at once so everything settles.

Distances are in game units (1 unit = 10 cm) and speeds per frame. A gravity
of 0.1 units per frame² is about 9 m/s².

## Where it runs

The character's bone update (`0x1F5D70`) first poses every bone: bones with
animation get their keyed rotation, all others (the `X` bones) their rest
pose. Then the character's physics driver runs (for Fiona `0x2F81A0`):

1. update each container's colliders (`0x2EE8A0`);
2. step each container's nodes once (`0x2EE900`); after a reset, re-seed some
   chains and run 30 steps instead;
3. write the nodes back into the bones (`0x2EE840`).

## Data structures

**Container** (inside the character object; set up by `0x2EE960`):

| Offset | Meaning |
| --- | --- |
| +0…+8 | gravity vector, subtracted from the velocity every step |
| +16 | damping: the velocity is multiplied by it every step |
| +20 | the model it belongs to |
| +24 | first collider |
| +28 / +32 | floor height / floor switched on (hair only: nodes are pushed up out of the floor) |
| +48 | first node |

**Node** (80 bytes; set up by `0x2EE6D0`):

| Offset | Meaning |
| --- | --- |
| +0 | position |
| +16 | velocity |
| +32 | chain start: the node hangs from its own bone instead of from the previous node |
| +36 | the bone it moves |
| +44 | previous node in the chain |
| +64 | length (hair, cloth) or spring strength (chest) |
| +68 | reference bone (cloth panels: the thigh they hang from) |
| +72…+96 | type-specific values |
| +76 | cone half-angle (cloth panels, radians) |

**Colliders** are spheres or capsules:

* **Sphere** (64 bytes; set up by `0x2EE690(collider, bone, offset, radius)`):
  centred at an offset from a bone. A node closer than the radius is pushed
  by `d × (1 − |d|/r) × strength`, where `d` points from the centre to the
  node (`0x2EE5C0`).
* **Capsule** (112 bytes; `0x2EE530(collider, bone A, bone B, offset A,
  radius, offset B)`): a line between two points on two bones. The push uses
  the closest point on the line and the *unit* direction:
  `normalize(d) × (1 − |d|/r) × strength` (`0x2EE220`).

## Node types

Each type has its own step and write-back code:

| Type | Step | Write-back | Used for |
| --- | --- | --- | --- |
| hair chain | `0x2ECE50` | `0x2EE710` | ponytails, braids, locks |
| cone piece | `0x2EE970` | `0x2EE710` | single swinging pieces (Fiona's bangs and bow) |
| jiggle | `0x2EEDA0` | `0x2EED20` | chests |
| sheet panel | `0x315F00` | `0x315E00` | the sheet Fiona wears at the start (FIS) |
| skirt panel | `0x2F8550` | `0x2F8450` | Fiona's skirt (FIN) |
| coat panel | `0x311D70` | `0x311C70` | Lorenzo's coat (same step as the skirt) |
| apron chain | `0x2EF0A0` | `0x2EEF90` | Daniella's apron |
| head-pushed hair | `0x3168F0` | `0x2EE710` | Daniella's hair |
| stiff hair | `0x31EA10` | `0x2EE710` | Riccardo's hair |
| robe chain | `0x31EB70` | `0x2EE710` | Riccardo's robe |

In the formulas, `p` is the node's position, `v` its velocity, and the
*anchor* is the point the node hangs from (its own bone's position for a
chain start, otherwise the previous node).

**Hair chain** — a plain chain of points, no spring back to a rest shape:
`v −= gravity; v += collider pushes; [floor]; v ×= damping; p += v;
p = anchor + normalize(p − anchor) × length; v = p − p_before`.

**Cone piece** — like a one-node chain, but limited: the piece cannot swing
behind its bone (the component along the bone's −Y axis is dropped, which
keeps Fiona's bangs out of her face) and its direction is held within 30° of
the bone's X axis.

**Jiggle** — a spring: `v += (bone position − p) × k; v ×= damping; p += v`,
with the offset from the bone kept within a small box in the bone's X/Y
plane (X between −0.2 and 0.2, Y between −0.2 and 0.3 units). The bone
position is the bone's animated position before any write-back. The
write-back only moves the bone to the node.

**Sheet, skirt and coat panels** — the "gravity" pulls **along the thigh**
the panel belongs to (down the leg), so panels hang along the leg and swing
within a cone around it: `v += thigh X × g; v ×= damping; p += v`, then the
direction from the anchor is clamped to the node's cone angle around the
thigh and the length restored.

* Fiona's **skirt** adds a **plane**: each node has a direction in the
  thigh's frame (back panels (0, 1, 0), front (0, −1, 0), sides (0, 0, ±1)),
  and a node that crosses that plane is pushed back onto it, so the cloth
  can never pass through the leg it hangs from.
* The **sheet** reduces the node's offset to the plane spanned by the thigh
  axis and the node's direction, keeping only the outward part:
  `p = anchor + normalize(X (d·X) + n max(d·n, 0)) × length`.

**Stiff hair** — a hair chain with a spring towards the rest shape:
`v −= gravity; v += (bone origin + bone X × length − p) × k; v ×= damping;
p += v`, then the length is restored.

**Robe chain** (96-byte nodes) — carried along by its anchor's movement
(`p += (anchor − previous anchor) × 0.6`), then gravity, collider pushes and
a spring that keeps it 1.9 units from a partner node:
`v += (partner − p) × k × (|partner − p| − 1.9) / |partner − p|`; damping
and length as usual.

**Head-pushed hair** — `v += head Z axis × k` (head bone 31), then as a hair
chain.

**Apron chain** (112-byte nodes) — carried by its anchor (`× weight`),
gravity, collider pushes at three times the strength, and a spring that
keeps it about 0.44 units from a partner node on the other side:
`v += normalize(p − partner) × 0.8 × (0.4379 − |p − partner|)`; damping and
length as usual.

### Write-back

The write-back sets each simulated bone's whole orientation: its origin goes
to the anchor, its X axis points at the node, and its roll comes from an
"up" direction (Z = X × up, Y = Z × X). The up direction depends on the
type:

* hair, cone, stiff hair, robe, head-pushed hair: the previous bone's Y axis
  after its own write-back (a chain start: its own animated Y axis);
* skirt, coat and sheet panels: the node's plane direction in the thigh's
  frame;
* apron: (partner − node) × side (±1);
* chest: only the origin moves.

The game's `X` bones point along their own X axis, and the rest pose of
Fiona's skirt bones is exactly the thigh's frame turned onto the plane
direction.

### Resets

On a reset each character re-seeds some chains with zero velocity (all
other nodes keep their position and velocity), then runs 30 steps:

* Fiona (FIN, FIS): the ponytail hangs straight down along the head bone's
  −Z axis (head bone 33, or 53 for FIS);
* Riccardo (RCG, RCT): the robe along bone 1's Z axis (nodes 1–6 forward,
  7–12 backward);
* Daniella: the apron along bone 0's Z axis, the hair along the head's Z
  axis plus gravity;
* Debilitas and Lorenzo: nothing is re-seeded.

Character code triggers a reset whenever the character's animation changes
abruptly. In cutscenes that is every cut and every jump in the animation
inside a cut.

### Hair floor

The hair types can push nodes up out of a floor at the container's height
when its floor is switched on. Cutscene scripts switch it on and off (the
command handler at `0x2ABBC0`); in EV0004 it is on at height 0.25 from frame
4415, when Fiona falls.

## Characters and parameters

Characters with physics, their game class, and what is simulated:

| Character (models) | Class | Simulated parts |
| --- | --- | --- |
| Fiona (FIN, FIW) | `0x470540` | skirt, bangs (bone 41), ponytail, chest, bow (bone 48) |
| Fiona (FIS) | `0x470620` | sheet, front lock (bone 61), ponytail, chest |
| Daniella (DNL) | `0x4702D0` | apron, hair (two locks), chest, two cone pieces (47, 48) |
| Riccardo (RCG) | `0x470480` | hair 22–25, robe 10–21, stiff hair 38–41 |
| Riccardo (RCT) | `0x473BD0` | hair 22–25, robe 10–21 |
| Lorenzo (LRM) | `0x471C20` | hair 47–52, coat 6–33 |
| Lorenzo (LRY) | `0x471CE0` | coat 6–33 |
| Lorenzo (LRO) | `0x471DA0` | hair 23–28 |
| Debilitas (DB0) | `0x46C0A0` | a hanging chain of bones 10–13 |
| other Fiona costumes | `0x475AE0`, `0x475BC0`, `0x478490`, `0x479420`, `0x476F50` | cone, jiggle, skirt and hair nodes (not yet extracted) |

The parameters were extracted by running each character's set-up code in a
small emulator of the console's processor and reading the result; Fiona's
match the values in the running game exactly. They are listed in
`GAME_PHYSICS` in `tools/blender_assemble_cutscene.py`. Main values ("len" =
segment length, "cone" = cone half-angle):

| Character | Part | Bones | Parameters |
| --- | --- | --- | --- |
| Fiona (FIN, FIW) | ponytail (hair chain) | 37–40 | len 0.45, gravity 0.1, damping 0.8; six sphere colliders of radius 1.0 on the head and shoulders |
| Fiona (FIN) | skirt (panels with planes) | 11–22 | len 0.409, cone 20° (upper) / 40° (lower), gravity 0.2 along the thigh, damping 0.9 |
| Fiona (FIN) | bangs, bow (cone pieces) | 41, 48 | len 1.0, gravity 0.1, damping 0.99 |
| Fiona (FIN) | chest (jiggle) | 25 | spring 0.9, damping 0.75 |
| Fiona (FIS) | sheet (panels) | 8 chains of 4 from bone 11 | len 1.0, cone 20° / 40° / 60° / 80° down the chain, gravity 0.4, damping 0.6 |
| Fiona (FIS) | ponytail | 57–60 | len 0.45, gravity 0.1, damping 0.8; six colliders |
| Fiona (FIS) | front lock (cone), chest | 61, 45 | as FIN |
| Fiona (FIB costume) | hair | 24–27 | len 0.45, gravity 0.1, damping 0.8; colliders around the head (found in the code, not used by the Blender script yet) |
| Daniella | hair, two locks | 34–36, 37–39 | gravity 0.3, damping 0.5; colliders on the head |
| Daniella | apron, two chains of 5 | 11–15, 16–20 | gravity 0.8, damping 0.5; partner spacing 1.2, carry weights 0.8 / 0.6 / 0.4 / 0.2 |
| Daniella | chest | 23 | spring 0.9, damping 0.75 |
| Daniella | cone pieces | 47, 48 | gravity 0.1, damping 0.99 |
| Lorenzo (LRM, LRY) | coat, 6 chains of 4 hanging from thighs and knees | 6–33 | len 2.125, cones 20°–80° (LRM) or 5°–20° (LRY) |
| Lorenzo (LRM, LRO) | hair | 47–52 / 23–28 | len 0.554, gravity 0.1, damping 0.8; colliders around the head |
| Debilitas | hanging chain | 10–13 | len 1.25, gravity 0.4, damping 0.99; colliders of radius 1.8 on bone 2 |

Notes: Debilitas' nodes 11–13 all hang from node 10 (his set-up links each
of them to the first node). The cone angles of Fiona's sheet are not set by
its set-up code and were read from the running game.

## How it was checked

* **A memory snapshot** of the running game (Fiona in the sheet, FIS) shows
  the containers holding exactly the values written by the set-up code, the
  sheet chains' first bones turned by exactly the thigh's rotation and the
  ponytail bones in varied positions.
* **A recording of gameplay** in PCSX2 (443 frames of Fiona's character
  object and bones, read through the emulator's PINE interface). Running the
  decoded step on frame *n* and comparing with frame *n* + 1 (Fiona is about
  17 units tall):

  | Type | Bones | Median error (units) |
  | --- | --- | --- |
  | hair chain | ponytail 37–40 | 0.000007 |
  | skirt panel | 11–22 | 0.000013 |
  | cone piece | bangs 41, bow 48 | 0.000004, 0.000002 |
  | jiggle | chest 25 | 0.000006 |

  These are rounding errors: the formulas and values above are the game's.
* **A recording of a cutscene** (EV0004) in PCSX2. The physics runs during
  cutscenes with the same steps, once per frame. The Blender reproduction
  matches Fiona's nodes to within 1 mm in every shot where she is visible
  (except the first second, which depends on the state she brings from
  gameplay), Riccardo's to within 3.5 mm, and Debilitas' to about 1 cm (the
  game poses his bones slightly differently, 5 mm at the hip).

## The Blender reproduction

`tools/blender_assemble_cutscene.py` adds the simulation to every assembled
character that has physics (`--no-physics` leaves it out). Nothing is baked
into keyframes; it runs live, like any Blender simulation:

* **Objects.** `<character>_physics` holds one point per node, carrying the
  game's parameters, and the *HG Physics* Geometry Nodes modifier. Two helper
  objects follow the body: `…_physics_frames` (each simulated bone's animated
  position before write-back) and `…_colliders` (the collision shapes).
* **Steps.** A simulation zone runs the node types above, once per frame in
  the game's node order. Nodes are stepped in a few passes ("levels")
  rather than one by one: a node waits for its anchor and for a partner the
  game updates before it, and is not stepped after a partner the game
  updates after it, so every node reads exactly what it reads in the game.
  The results are bit-for-bit those of the one-by-one order, about 2.5
  times faster (EV0004: 9 ms per frame for three characters instead of
  23 ms).
* **Write-back.** In the assembled scene only, the simulated bones' rest
  orientation is turned to match the game's (Blender's Y axis along the
  game's X axis, Z along the game's Y axis; the mesh does not move). Three
  bone constraints then copy the game's write-back: *Copy Location* puts the
  bone on its anchor, *Damped Track* aims it at its node, and *Locked Track*
  sets its roll from the game's up direction, supplied by a third helper
  object (`…_physics_up`). Chest bones are only moved onto their node.
* **Resets.** At every cut (the keyed *Reset* input) and at jumps in the
  animation inside a cut (the body moving more than *Snap*, 3 cm, and more
  than *Snap Ratio* times its previous frame's movement), the game's reset
  runs: the character's re-seeding rules, then *Settle Steps* (30) steps. A
  full restart, with every node re-seeded, happens at frame 0 and when a
  character jumps more than *Teleport* (0.25 m) inside a shot.
* **Hidden characters.** Characters that a shot does not show are hidden
  with visibility keys on their meshes, so their simulation keeps running,
  as in the game.
* **Hair floor.** The floor is keyed where the game's scripts switch it on
  (EV0004 from frame 4415).
* **Playback.** When Blender skips frames during playback, the simulation
  runs as many steps as frames went by (up to *Max Skip*, 4), so it keeps
  the game's speed. A bigger jump, or stepping backwards, restarts it at that
  frame, settled like the start of a shot. The result is plausible but only
  matches the game after about 20 frames. `--bake-physics` pre-computes the
  whole cutscene so every frame is exact.

The reproduction was checked twice: the bones' resulting orientations
follow the write-back rules exactly (frame by frame on EV0004, DE0012 and
EV0002), and an independent re-implementation of the steps in Python, fed
the same bone positions, agrees with the Blender nodes to within 0.003 mm.
