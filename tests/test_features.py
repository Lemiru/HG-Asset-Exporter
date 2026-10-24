"""Tests for text, music/SoundFont, split models, rooms for cutscenes, cameras."""
import os
import struct
import tempfile
import unittest
from pathlib import Path

from hgexport.formats.text import parse_messages
from hgexport.gltf.reader import Gltf, validate

DATA = Path(os.environ.get("HG_DATA", Path(__file__).resolve().parent.parent / "data"))   # extracted DATA.CVM
HAVE_DATA = (DATA / "O_HEW" / "HEW_000.PCK").is_file()


def riff_chunks(blob: bytes, start: int, end: int) -> dict:
    out = {}
    pos = start
    while pos < end:
        tag = blob[pos:pos + 4]
        size = struct.unpack_from("<I", blob, pos + 4)[0]
        if tag == b"LIST":
            out[blob[pos + 8:pos + 12]] = riff_chunks(blob, pos + 12, pos + 8 + size)
        else:
            out[tag] = blob[pos + 8:pos + 8 + size]
        pos += 8 + size + (size & 1)
    return out


class TextTests(unittest.TestCase):
    def test_control_codes(self):
        body = b"Hello\x01World\x02Next \x03key\x04.\0"
        data = struct.pack("<HH", 0, 4) + body
        self.assertEqual(parse_messages(data), ["Hello\nWorld\n\nNext <em>key</em>."])


@unittest.skipUnless(HAVE_DATA, "game data not available")
class MusicTests(unittest.TestCase):
    def test_soundfont_structure(self):
        from hgexport.export.soundfont import build_sf2
        hd = (DATA / "BGM" / "STAGE1_BANK.HD").read_bytes()
        bd = (DATA / "BGM" / "STAGE1_BANK.BD").read_bytes()
        sf2 = build_sf2(hd, bd, "STAGE1_BANK")
        self.assertEqual(sf2[:4], b"RIFF")
        self.assertEqual(struct.unpack_from("<I", sf2, 4)[0], len(sf2) - 8)
        top = riff_chunks(sf2, 12, len(sf2))
        pdta = top[b"pdta"]
        self.assertEqual(len(pdta[b"phdr"]) % 38, 0)
        self.assertEqual(len(pdta[b"inst"]) % 22, 0)
        self.assertEqual(len(pdta[b"igen"]) % 4, 0)
        self.assertEqual(len(pdta[b"shdr"]) % 46, 0)
        self.assertEqual(len(pdta[b"phdr"]) // 38 - 1, 19)      # 19 programs + terminator
        n_samples = len(pdta[b"shdr"]) // 46 - 1
        smpl = len(top[b"sdta"][b"smpl"]) // 2
        for i in range(n_samples):
            start, end, ls, le = struct.unpack_from("<4I", pdta[b"shdr"], i * 46 + 20)
            self.assertTrue(start <= ls <= le <= end <= smpl)

    def test_render_has_loop_and_signal(self):
        from hgexport.export.music import render
        from hgexport.formats.audio import parse_sq
        seq = parse_sq((DATA / "BGM" / "S3_CHASE.SQ").read_bytes())
        pcm = render(seq, (DATA / "BGM" / "STAGE3_BANK.HD").read_bytes(),
                     (DATA / "BGM" / "STAGE3_BANK.BD").read_bytes())
        self.assertEqual(pcm.channels, 2)
        self.assertIsNotNone(pcm.loop)
        self.assertGreater(max(abs(x) for x in pcm.samples[::97]), 1000)


@unittest.skipUnless(HAVE_DATA, "game data not available")
class ModelAndSceneTests(unittest.TestCase):
    def test_split_model_t00(self):
        from hgexport.formats.pck import parse_pck
        m = parse_pck(DATA / "O_T00" / "T00_000.MDL")
        self.assertEqual((len(m.bones), len(m.morphs), len(m.motion.entries)), (68, 4, 116))

    def test_event_room_table(self):
        from hgexport.export.pairing import event_rooms
        self.assertEqual(event_rooms()["EV0002"], ["ST_021"])
        self.assertEqual(event_rooms()["EV0010"], ["ST_023"])

    def test_cutscene_includes_room_and_binds_props(self):
        from hgexport.export.cutscene import export_event
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "ev.glb"
            rep = export_event(DATA / "EV0010", out)
            self.assertEqual({r["room"] for r in rep["rooms"]}, {"ST_023"})
            self.assertIn("a_nozoki", rep["props_bound_to_room_objects"])
            self.assertEqual(validate(out), [])

    def test_cutscene_default_is_animation_only(self):
        from hgexport.export.cutscene import CutsceneOptions, export_event
        with tempfile.TemporaryDirectory() as tmp:
            lean, full = Path(tmp) / "lean.glb", Path(tmp) / "full.glb"
            export_event(DATA / "EV0010", lean)
            export_event(DATA / "EV0010", full, CutsceneOptions(embed_models=True))
            self.assertEqual(validate(lean), [])
            g, f = Gltf(lean), Gltf(full)
            self.assertNotIn("images", g.json)
            self.assertIn("images", f.json)
            self.assertLess(lean.stat().st_size * 2, full.stat().st_size)
            nodes = {n["name"]: n for n in g.json["nodes"]}
            # bones named like the character export, room objects like the stage export
            self.assertIn("J00", nodes)
            self.assertEqual(nodes["ST_023"]["extras"]["room_file"], "stages/ST_020/ST_023.glb")
            self.assertIn("ST_023_obj001_a_nozoki", nodes)
            self.assertEqual(nodes["A04_DNL_000"]["extras"]["model_file"], "characters/O_DNL/DNL_000.glb")
            self.assertEqual(len(g.json["skins"]), 2)
            animated = {c["target"].get("node") for c in g.json["animations"][0]["channels"]}
            self.assertIn(g.json["nodes"].index(nodes["ST_023_obj001_a_nozoki"]), animated)

    def test_player_wears_sheet_until_clothes_change(self):
        from hgexport.export.cutscene import CutsceneOptions, _resolve_model
        opts = CutsceneOptions()
        for event, model in (("EV0002", "FIS_000"), ("EV0003", "FIS_000"), ("EV0004", "FIN_000")):
            self.assertEqual(_resolve_model(DATA, event, 1, opts).stem, model)

    def test_markers_and_event_parameters(self):
        from hgexport.formats.dp import parse_dh
        from hgexport.formats.marks import parse_event_marks, parse_event_params, parse_mrk
        mrk = parse_mrk(DATA / "O_FIN" / "FIN_000.MRK")
        self.assertEqual(len(mrk), 119)
        self.assertEqual(len(mrk[0x707]), 24)                 # 22 frames padded to 4
        timeline = parse_dh(DATA / "EV0002" / "EV0002.DH")
        marks = parse_event_marks(DATA / "EV0002" / "MARK.BIN", timeline.frames)
        self.assertEqual(len(marks), timeline.frames)
        cut_starts = {c.start for c in timeline.cuts}
        bit0 = [f for f, v in enumerate(marks) if v & 1]
        self.assertGreater(sum(f in cut_starts for f in bit0) / len(bit0), 0.9)
        params = parse_event_params(DATA / "EV0002" / "PARAMS.BIN")
        self.assertEqual(len(params), 41)
        self.assertEqual(params[0]["lights"][0]["color"], [15, 14, 13])

    def test_gallery_art_and_thumbnails(self):
        from hgexport.formats.gallery import parse_art_pack, parse_thumbnails
        sizes = [(img.width, img.height) for f in range(6)
                 for img in parse_art_pack(DATA / "SUBSCR" / f"ART{f:02d}.LEN")]
        self.assertEqual(len(sizes), 48)
        self.assertEqual(sizes.count((512, 896)), 21)
        self.assertEqual(sizes.count((512, 448)), 27)
        thumbs = parse_thumbnails(DATA / "SUBSCR" / "THUMBNAIL.TXS")
        self.assertEqual((len(thumbs), thumbs[0].width, thumbs[0].height), (110, 128, 128))

    def test_sound_effect_table(self):
        from hgexport.formats.soundtable import parse_sdt
        recs = parse_sdt(DATA / "SYSTEM" / "TITLE.SDT")
        used = [r for r in recs if r]
        self.assertEqual([r["pan"] for r in used], [0, 127, 0, 127])     # stereo pairs
        self.assertEqual([r["sample"] for r in used], [0, 1, 2, 3])
        self.assertEqual(used[0]["voice"], 36)

    def test_cutscene_doors_and_prop_groups(self):
        """DP section 32 turns room door 0 (DH door mask bit 0); prop-group
        objects are shown only in the cuts that animate their group."""
        from hgexport.export.cutscene import export_event
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "ev.glb"
            rep = export_event(DATA / "EV0014", out)
            self.assertEqual(rep["doors_animated"], ["ST_011_door0"])
            self.assertEqual(rep["props_shown_per_cut"], ["spoon"])
            doc = Gltf(out).json
            nodes = doc["nodes"]
            paths = {(nodes[c["target"]["node"]]["name"], c["target"]["path"])
                     for c in doc["animations"][0]["channels"] if "node" in c["target"]}
            self.assertIn(("ST_011_door0", "rotation"), paths)
            self.assertIn(("ST_011_obj000_spoon", "scale"), paths)
            self.assertNotIn("prop_doornull0", {n.get("name") for n in nodes})
            story = next(n["extras"]["story_state"] for n in nodes if "story_state" in n.get("extras", {}))
            self.assertEqual(story["story_flags"], [1, 2])          # after the opening: the rooms' night look
            self.assertEqual(validate(out), [])

    def test_shadow_volumes(self):
        from hgexport.export.character import export_shadow_volumes
        from hgexport.formats.shadow import parse_shadow_volumes, pck_bytes
        vols = parse_shadow_volumes(pck_bytes(DATA / "O_HEW" / "HEW_000.PCK"))
        self.assertEqual(len(vols), 29)
        self.assertTrue(all(len(v.vertices) == 8 and len(v.quads) == 6 for v in vols))
        self.assertEqual(vols[0].bone, 30)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.glb"
            self.assertEqual(export_shadow_volumes(DATA / "O_FIN" / "FIN_000.PCK", out)["shadow_volumes"], 19)
            self.assertEqual(validate(out), [])

    def test_foot_controls(self):
        from hgexport.export.character import _bone_names
        from hgexport.export.feet import FootControls, intervals
        from hgexport.formats.pck import parse_pck
        self.assertEqual(intervals([True, True, False, True]), [[0, 1], [3, 3]])
        fin = parse_pck(DATA / "O_FIN" / "FIN_000.PCK")
        feet = FootControls(fin, _bone_names(fin, ""), 0.1)
        self.assertEqual(feet.info()["points"], {"-2": "J10", "-3": "J22"})
        walk = fin.motion.entries[7]
        extras = feet.clip_extras(walk.slots[0], [g for g in walk.slots[1:] if g], walk.slots[0].frames)
        self.assertEqual(extras["foot_contact"], {"J10": [[0, 8], [24, 33]], "J22": [[8, 27]]})
        self.assertNotIn("foot_points", extras)          # identical to the skeleton
        odd = fin.motion.entries[41]                     # authored points differ from the pose
        self.assertEqual(len(feet.clip_extras(odd.slots[0], [odd.slots[1]], 30)["foot_points"]["J10"]), 30)
        hew = parse_pck(DATA / "O_HEW" / "HEW_000.PCK")
        self.assertEqual(FootControls(hew, _bone_names(hew, ""), 0.1).info()["contact_flags"],
                         {"-5": ["J32", "J33"], "-6": ["J10", "J11"]})

    def test_game_fix_props(self):
        from hgexport.export.gfm import export_gfm
        from hgexport.export.media import extra_palettes
        from hgexport.formats.gfm import parse_gfm
        from hgexport.formats.tex import parse_tex
        entries = parse_gfm(DATA / "GAME_FIX.GFM")
        self.assertEqual([len(e) for e in entries], [5, 1, 1, 1])
        self.assertEqual([sum(len(p.positions) for p in e) for e in entries], [1066, 46, 733, 64])
        self.assertEqual(entries[2][0].palette, 1)
        self.assertEqual(entries[0][0].positions[0], (-72 / 16, 128 / 16, 0.0))   # VIF ROW start
        self.assertEqual(extra_palettes(parse_tex(DATA / "GAME_FIX.TEX")[2]), [1, 2, 3, 4, 5, 6])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "g.glb"
            self.assertEqual(export_gfm(DATA / "GAME_FIX.GFM", out)["triangles"], 1159)
            self.assertEqual(validate(out), [])

    def test_system_raw_images(self):
        from hgexport.formats.rawimage import parse_raw_image
        dolby = parse_raw_image(DATA / "SYSTEM" / "LOGO_DOLBY.BIN")
        self.assertEqual((dolby.width, dolby.height), (720, 540))
        back = parse_raw_image(DATA / "SYSTEM" / "TITLE_BACK.BIN")
        self.assertEqual((back.width, back.height, len(back.indices)), (640, 448, 640 * 448))
        self.assertEqual(max(back.indices), 15)

    def test_memory_card_icon(self):
        from hgexport.export.psicon import export_icon
        from hgexport.formats.psicon import parse_icon, parse_icon_sys
        icon = parse_icon(DATA / "SUBSCR" / "ICON00.ICO")
        self.assertEqual((len(icon.shapes), len(icon.normals), icon.frame_length), (6, 1338, 60))
        self.assertEqual(icon.shapes[5], icon.shapes[0])            # seamless loop
        info = parse_icon_sys(DATA / "SUBSCR" / "ICON.SYS")
        self.assertEqual(info.title, "ＨＡＵＮＴＩＮＧ　ＧＲＯＵＮＤ")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "i.glb"
            self.assertEqual(export_icon(DATA / "SUBSCR" / "ICON00.ICO", out)["triangles"], 446)
            self.assertEqual(validate(out), [])

    def test_pursuer_door_motion(self):
        from hgexport.export.stage import export_door_motion
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "d.glb"
            rep = export_door_motion(DATA / "O_RCG" / "RCG_D000.MTN", out)
            self.assertEqual(rep["frames"], [66, 80, 80, 64])
            self.assertEqual([a["name"] for a in Gltf(out).json["animations"]],
                             ["RCG_open_1", "RCG_close_1", "RCG_open_2", "RCG_close_2"])
            self.assertEqual(validate(out), [])

    def test_room_lighting(self):
        from hgexport.export.stage import StageOptions, export_stage
        from hgexport.formats.stage import parse_navigation, parse_pac, parse_room_lighting
        pac = parse_pac(DATA / "ST_000" / "ST_000.PAC")
        lit = parse_room_lighting(pac)
        self.assertEqual([round(c, 1) for c in lit.ambient], [20.4, 20.4, 23.4])
        self.assertEqual([l.directional for l in lit.lights], [True, True, False, False])
        self.assertEqual(round(lit.lights[2].range, 1), 50.0)
        masks = [t.light_mask for t in parse_navigation(pac)]
        self.assertTrue(all(m < 1 << len(lit.lights) for m in masks) and any(masks))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "r.glb"
            self.assertEqual(export_stage(DATA / "ST_000" / "ST_000.PAC", out, StageOptions())["lights"], 4)
            doc = Gltf(out).json
            self.assertEqual([l["type"] for l in doc["extensions"]["KHR_lights_punctual"]["lights"]],
                             ["directional", "directional", "point", "point"])
            self.assertEqual(validate(out), [])

    def test_wall_edges(self):
        from hgexport.export.stage import export_navmesh
        from hgexport.formats.roomdata import parse_walls
        from hgexport.formats.stage import parse_navigation, parse_pac
        pac = parse_pac(DATA / "ST_020" / "ST_021.PAC")
        tris = parse_navigation(pac)
        walls = parse_walls(pac)
        self.assertEqual(len(walls), 44)
        for tri, edge, a, b in walls:
            self.assertEqual((a, b), (edge, (edge + 1) % 3))
            self.assertEqual(tris[tri].neighbors[edge], -1)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "n.glb"
            self.assertEqual(export_navmesh(DATA / "ST_020" / "ST_021.PAC", out)["walls"], 44)
            self.assertEqual(validate(out), [])

    def test_mrk_hand_poses(self):
        from hgexport.export.character import export_character
        from hgexport.formats.handpose import hand_table, simulate
        self.assertEqual(len(hand_table("FIN_000")), 120)
        self.assertIsNone(hand_table("RCG_000"))
        marks = bytes(12) + bytes([4]) + bytes(7) + bytes([4]) + bytes(5)
        poses = simulate(marks, len(marks), 0x90)          # hand 1 type 9: shape 1 -> shape 3
        self.assertEqual(poses[0][0], (1, 1, 0.0))
        self.assertEqual(poses[8][0], (1, 3, 0.0))
        self.assertEqual(poses[12][0], (1, 3, 1.0))
        self.assertEqual(poses[20][0], (1, 3, 0.0))
        self.assertEqual(poses[5][1], (1, 1, 0.0))         # hand 2 untouched
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "f.glb"
            rep = export_character(DATA / "O_FIN" / "FIN_000.PCK", out)
            self.assertEqual(rep["hand_pose_clips"], 71)
            self.assertEqual(validate(out), [])

    def test_dh_prop_groups(self):
        from hgexport.formats.dp import parse_dh
        timeline = parse_dh(DATA / "DE0002" / "DE0002.DH")
        self.assertEqual(timeline.prop_groups[0], ["Golem_off_00"])
        self.assertEqual(timeline.prop_groups[6], ["plate"])
        self.assertEqual(parse_dh(DATA / "DE0020" / "DE0020.DH").prop_groups[0],
                         ["insect", "insect_l_wing", "insectr_wing"])

    def test_event_vibration(self):
        from hgexport.formats.marks import parse_event_marks, vibration
        self.assertEqual(vibration([0, 0b01000, 0b11100, 0]), [[0, 0, 0], [1, 0, 85], [2, 1, 255], [3, 0, 0]])
        self.assertEqual(vibration([1, 0, 1]), [])
        from hgexport.formats.marks import script_sync
        self.assertEqual(script_sync([1, 2, 0x11c, 0x100]), [[1, [1]], [2, [8]], [3, [8]]])
        rumble = vibration(parse_event_marks(DATA / "DE0001" / "MARK.BIN", 10000))
        self.assertEqual(rumble[1:4], [[120, 0, 85], [202, 0, 0], [264, 1, 255]])

    def test_room_slots(self):
        from hgexport.formats.roomdata import (parse_floor_groups, parse_object_motion, parse_room_effects,
                                               parse_zones)
        from hgexport.formats.stage import parse_pac
        st021 = parse_pac(DATA / "ST_020" / "ST_021.PAC")
        zones = {z.index: z for z in parse_zones(st021)}
        self.assertEqual(zones[14].kind, "boundary")             # EV0002 trigger line
        self.assertEqual(zones[14].points[0], (-51.0, 0.0, -20.0))
        self.assertEqual(zones[16].kind, "area")                 # EV0003 trigger area
        self.assertIn("fog", parse_room_effects(st021))
        self.assertEqual(len(parse_floor_groups(parse_pac(DATA / "ST_000" / "ST_004.PAC"))), 2)
        lever = parse_object_motion(parse_pac(DATA / "ST_000" / "ST_002.PAC"))[0]
        self.assertEqual((lever.name, lever.channels[0].kind, len(lever.channels[0].frames)),
                         ("lever", "rotation", 101))
        self.assertAlmostEqual(lever.channels[0].frames[-1][2], -1.047, places=3)

    def test_stage_camera_rails_and_navmesh(self):
        from hgexport.export.stage import StageOptions, export_navmesh, export_stage
        with tempfile.TemporaryDirectory() as tmp:
            rep = export_stage(DATA / "ST_020" / "ST_021.PAC", Path(tmp) / "st.glb", StageOptions())
            self.assertIn("ST_021_cam1_rail", rep["clips"])
            g = Gltf(Path(tmp) / "st.glb")
            cams = [c for c in g.json["cameras"]]
            # 60 degree horizontal FOV at 4:3 -> ~46.8 degree vertical
            self.assertAlmostEqual(cams[0]["perspective"]["yfov"], 0.8170, places=3)
            self.assertEqual(export_navmesh(DATA / "ST_020" / "ST_021.PAC", Path(tmp) / "nav.glb")["triangles"], 304)

    def test_root_motion_moves_forward(self):
        from hgexport.export.character import CharacterOptions, export_character
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "fin.glb"
            export_character(DATA / "O_FIN" / "FIN_000.PCK", out, CharacterOptions(root_motion=True))
            g = Gltf(out)
            motion = next(i for i, n in enumerate(g.json["nodes"]) if n["name"].endswith("_motion"))
            clip = next(a for a in g.json["animations"] if a["name"] == "010")
            ch = next(c for c in clip["channels"] if c["target"] == {"node": motion, "path": "translation"})
            end = g.accessor(clip["samplers"][ch["sampler"]]["output"])[-1]
            self.assertGreater(end[2], 1.0)

    def test_obstacle_curve(self):
        from hgexport.formats.stage import parse_curve_bank
        curves = parse_curve_bank(DATA / "OBSTACLE.MTN")
        self.assertEqual([len(c) for c in curves], [30])
        self.assertAlmostEqual(max(curves[0]), 0.769, places=3)

    def test_animation_variant_holds_only_changed_clips(self):
        from hgexport.export.character import export_animation_variant, variant_base
        src = DATA / "O_FIN" / "FIN_006.PCK"
        self.assertEqual(variant_base(src).name, "FIN_000.PCK")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "v.glb"
            rep = export_animation_variant(src, variant_base(src), out)
            self.assertEqual(rep["paired_with"], "LRY_000")
            self.assertEqual(rep["replaced"], ["107", "108", "109", "110", "114"])
            self.assertEqual(rep["added"], ["r0F05"])
            g = Gltf(out)
            self.assertNotIn("meshes", g.json)
            self.assertEqual(len(g.json["skins"]), 1)
            self.assertEqual(validate(out), [])

    def test_fiona_accessories_are_separate(self):
        from hgexport.export.character import export_character
        with tempfile.TemporaryDirectory() as tmp:
            rep = export_character(DATA / "O_FIN" / "FIN_002.PCK", Path(tmp) / "f.glb")
            self.assertEqual(len(rep["parts"]), 9)

    def test_daniella_shard_is_separate(self):
        from hgexport.export.character import export_character
        from hgexport.export.cutscene import cutscene_hidden_parts
        with tempfile.TemporaryDirectory() as tmp:
            rep = export_character(DATA / "O_DNL" / "DNL_000.PCK", Path(tmp) / "d.glb")
            self.assertEqual(rep["parts"], ["Glass Shard"])
            g = Gltf(Path(tmp) / "d.glb")
            self.assertIn("Glass Shard", [n.get("name") for n in g.json["nodes"]])
        self.assertEqual(cutscene_hidden_parts("DNL_000", "EV0002"), ("Glass Shard",))
        self.assertIn("Jade Choker", cutscene_hidden_parts("FIN_000", "EV0002"))
        self.assertEqual(cutscene_hidden_parts("HEW_000", "EV0002"), ())

    def test_room_look_and_glow_geometry(self):
        from hgexport.export.look import cutscene_story_state, room_look, room_variants, tint_grade
        from hgexport.export.stage import StageOptions, export_stage
        from hgexport.formats.roomdata import parse_room_effects
        from hgexport.formats.stage import parse_pac
        fx = parse_room_effects(parse_pac(DATA / "ST_020" / "ST_021.PAC"))
        self.assertEqual(fx["tint"]["colors"], [[138, 156, 196, 106], [88, 82, 78, 58]])
        self.assertEqual(fx["bloom"]["color"], [118, 86, 62, 74])
        variants = room_variants("ST_021")
        warm = [[132, 96, 60, 80], [36, 42, 42, 70]]
        self.assertEqual(room_look(fx, variants, cutscene_story_state("EV0002"))["tint"]["colors"], warm)
        self.assertEqual(room_look(fx, variants, cutscene_story_state("EV0007"))["tint"], fx["tint"])
        self.assertEqual(room_look(fx, variants, None)["tint"], fx["tint"])
        # room 0x0C: the second pass's glow buffer clips, so a flat 0.23 rises to 0.426 in red
        hall = {"colors": [[102, 116, 160, 116], [74, 86, 96, 138]]}
        self.assertAlmostEqual(tint_grade(hall, 0.23)[0], 0.4258, places=3)
        self.assertEqual(tint_grade(None, 0.5), [0.5, 0.5, 0.5])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "st.glb"
            export_stage(DATA / "ST_020" / "ST_021.PAC", out, StageOptions(emissive_glow=4.0))
            g = Gltf(out).json
            root = g["nodes"][g["scenes"][0]["nodes"][0]]
            self.assertEqual(root["extras"]["room_effects"]["script_variants"][0]["when"], {"story_flag_clear": 1})
            engine = root["extras"]["room_effects"]["engine_look"]
            self.assertEqual(sorted(engine), ["night", "warm"])
            self.assertEqual(len(engine["night"]["grade"]), 33)
            self.assertEqual(engine["night"]["grade"][0], [0.0, 0.0, 0.0])
            self.assertEqual(engine["night"]["detail_gain"], round(1 + 58 / 256, 4))
            self.assertGreater(engine["night"]["lamp_bloom_intensity"], 0)
            glow = next(n for n in g["nodes"] if n.get("name") == "ST_021_glow")
            mats = [g["materials"][p["material"]] for p in g["meshes"][glow["mesh"]]["primitives"]]
            self.assertTrue(mats and all(m["name"].endswith("_glow") and m["extras"]["glow"] for m in mats))
            self.assertTrue(all("KHR_materials_emissive_strength" in m["extensions"] for m in mats))
            self.assertEqual(validate(out), [])

    def test_shine_masks_and_specular(self):
        from hgexport.export.character import CharacterOptions, export_character
        from hgexport.export.shine import export_shine_masks
        src = DATA / "O_FIN" / "FIN_000.PCK"
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(export_shine_masks(src, tmp), ["FIN_000_m00_shine.png", "FIN_000_m01_shine.png"])
            self.assertEqual(export_shine_masks(DATA / "O_HEW" / "HEW_000.PCK", tmp), [])
            plain, spec = Path(tmp) / "a.glb", Path(tmp) / "b.glb"
            export_character(src, plain, CharacterOptions(animations=False))
            export_character(src, spec, CharacterOptions(animations=False, specular=True))
            self.assertNotIn("KHR_materials_specular", Gltf(plain).json.get("extensionsUsed", []))
            g = Gltf(spec).json
            self.assertIn("KHR_materials_specular", g["extensionsUsed"])
            mats = {m["name"]: m for m in g["materials"]}
            shiny = mats["FIN_000_m00_opaque"]
            ext = shiny["extensions"]["KHR_materials_specular"]
            self.assertIn("specularTexture", ext)
            self.assertEqual(ext["specularColorTexture"], shiny["pbrMetallicRoughness"]["baseColorTexture"])
            self.assertIn("metallicRoughnessTexture", shiny["pbrMetallicRoughness"])
            self.assertAlmostEqual(shiny["extensions"]["KHR_materials_ior"]["ior"], 2.784, places=3)   # strength 64
            self.assertEqual(mats["FIN_000_m02_opaque"]["extensions"]["KHR_materials_specular"], {"specularFactor": 0.0})
            self.assertEqual(validate(spec), [])


if __name__ == "__main__":
    unittest.main()
