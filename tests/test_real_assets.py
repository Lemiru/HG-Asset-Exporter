"""Checks against the real game files and independent references.

Skipped when the extracted data is absent. By default the game data is
read from ``data/`` and the Noesis reference exports from ``research/`` in
the project folder; set HG_DATA / HG_RESEARCH to use other folders.
"""
import glob
import os
import tempfile
import unittest
from pathlib import Path

from hgexport.formats.dp import parse_dh, parse_dp
from hgexport.formats.motion import parse_motion_bank
from hgexport.formats.pck import FIXED12, parse_pck, strip_triangles
from hgexport.formats.stage import parse_stage
from hgexport.formats.tex import parse_tex
from hgexport.gltf.reader import Gltf, validate
from hgexport.util.mathutil import euler_to_quat
from hgexport.util.png import decode_png

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("HG_DATA", ROOT / "data"))
RESEARCH = Path(os.environ.get("HG_RESEARCH", ROOT / "research"))
HAVE_DATA = (DATA / "O_HEW" / "HEW_000.PCK").is_file()
HAVE_NOESIS = (RESEARCH / "HEW_000_noesis.gltf").is_file()


@unittest.skipUnless(HAVE_DATA, "game data not available")
class CorpusParsing(unittest.TestCase):
    def test_all_textures(self):
        n = sum(len(parse_tex(p)) for p in DATA.rglob("*.TEX"))
        self.assertEqual(n, 326)

    def test_all_models(self):
        files = sorted(DATA.glob("O_*/*.PCK"))
        self.assertEqual(len(files), 115)
        tris = 0
        for p in files:
            m = parse_pck(p)
            tris += sum(len(strip_triangles(x.restart)) for x in m.meshes)
        self.assertGreater(tris, 300000)

    def test_motion_banks(self):
        ok = 0
        for p in DATA.rglob("*.MTN"):
            if "_D000" in p.name or p.name == "OBSTACLE.MTN":
                continue
            parse_motion_bank(p.read_bytes(), name=p.name)
            ok += 1
        self.assertEqual(ok, 35)

    def test_all_stages(self):
        files = sorted(DATA.glob("ST_*/*.PAC"))
        self.assertEqual(len(files), 168)
        objects = sum(len(parse_stage(p).objects) for p in files[::7])
        self.assertGreater(objects, 0)

    def test_cutscenes_agree_with_cut_lists(self):
        for dh in sorted(DATA.glob("*/*.DH"))[::6]:
            timeline = parse_dh(dh)
            dps = sorted(dh.parent.glob("CUT*.DP"))
            for cut in timeline.cuts:
                cd = parse_dp(dps[cut.index])
                self.assertEqual(cd.frames, cut.frames)
                self.assertEqual(sorted(cd.actors), cut.actors)


@unittest.skipUnless(HAVE_DATA and HAVE_NOESIS, "Noesis reference not available")
class NoesisReference(unittest.TestCase):
    def setUp(self):
        self.ref = Gltf(RESEARCH / "HEW_000_noesis.gltf")
        self.model = parse_pck(DATA / "O_HEW" / "HEW_000.PCK")

    def test_textures_match_pixel_for_pixel(self):
        for i, img in enumerate(parse_tex(DATA / "O_HEW" / "HEW_000.TEX")):
            w, h, ref = decode_png((RESEARCH / f"HEW_000_noesis0_tex{i:02d}.png").read_bytes())
            px = img.rgba()
            flipped = b"".join(px[(h - 1 - y) * w * 4:(h - y) * w * 4] for y in range(h))
            self.assertEqual(flipped, ref)

    def test_positions_uvs_and_triangles(self):
        meshes = self.ref.json["meshes"]
        checked = 0
        for mesh, ref in zip(self.model.meshes, meshes):
            prim = ref["primitives"][0]
            pos = self.ref.accessor(prim["attributes"]["POSITION"])
            if len(pos) != len(mesh.positions):
                continue      # Noesis drops unreferenced vertices for a few meshes
            for a, b in zip(mesh.positions, pos):
                self.assertTrue(all(abs(x * FIXED12 - y) < 1e-6 for x, y in zip(a, b)))
            idx = self.ref.scalars(prim["indices"])
            ours = {tuple(sorted(t)) for t in strip_triangles(mesh.restart)}
            theirs = {tuple(sorted(idx[i:i + 3])) for i in range(0, len(idx), 3)}
            self.assertEqual(ours, theirs)
            checked += 1
        self.assertGreater(checked, 18)

    def test_bind_pose(self):
        nodes = {n["name"]: n for n in self.ref.json["nodes"]}
        for bone in self.model.bones:
            ref = nodes[f"Bone_{bone.index}"]
            q = euler_to_quat(*bone.euler)
            r = ref.get("rotation", [0, 0, 0, 1])
            self.assertLess(min(sum(abs(a - b) for a, b in zip(q, r)), sum(abs(a + b) for a, b in zip(q, r))), 1e-5)


@unittest.skipUnless(HAVE_DATA, "game data not available")
class ExportSmoke(unittest.TestCase):
    def test_character_stage_cutscene(self):
        from hgexport.export.character import export_character
        from hgexport.export.cutscene import export_event
        from hgexport.export.stage import export_stage
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            rep = export_character(DATA / "O_FIS" / "FIS_000.PCK", out / "fis.glb")
            self.assertEqual(rep["morph_targets"], 33)
            self.assertEqual(validate(out / "fis.glb"), [])
            export_stage(DATA / "ST_020" / "ST_021.PAC", out / "st.gltf")
            self.assertEqual(validate(out / "st.gltf"), [])
            rep = export_event(DATA / "DE0000", out / "de.glb")
            self.assertEqual(validate(out / "de.glb"), [])
            self.assertEqual(rep["frames"], 270)


if __name__ == "__main__":
    unittest.main()
