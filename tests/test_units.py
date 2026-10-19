"""Synthetic tests: no game data required."""
import math
import struct
import tempfile
import unittest
from pathlib import Path

from collections import Counter

from hgexport.export.alpha import AlphaSampler, assign_modes, elements, texel_mode
from hgexport.formats.audio import Pcm, decode_vag, sq_to_midi
from hgexport.formats.motion import parse_motion_bank
from hgexport.formats.pck import strip_triangles
from hgexport.formats.tex import palette_rgba, parse_tex_bytes
from hgexport.gltf.builder import GltfBuilder
from hgexport.gltf.reader import Gltf, validate
from hgexport.util.binary import FormatError
from hgexport.util.mathutil import euler_to_quat, look_at_quat, quat_rotate, quat_to_mat3
from hgexport.util.bleed import bleed_transparent
from hgexport.util.png import decode_png, encode_png


class MathTests(unittest.TestCase):
    def test_euler_order_is_z_y_x(self):
        rx, ry, rz = 0.3, -0.7, 1.1
        q = euler_to_quat(rx, ry, rz)

        def rot(axis, a):
            c, s = math.cos(a), math.sin(a)
            return {"x": [[1, 0, 0], [0, c, -s], [0, s, c]],
                    "y": [[c, 0, s], [0, 1, 0], [-s, 0, c]],
                    "z": [[c, -s, 0], [s, c, 0], [0, 0, 1]]}[axis]

        def mul(a, b):
            return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]

        expected = mul(rot("z", rz), mul(rot("y", ry), rot("x", rx)))
        got = quat_to_mat3(q)
        for i in range(3):
            for j in range(3):
                self.assertAlmostEqual(got[i][j], expected[i][j], places=9)

    def test_camera_looks_down_minus_z(self):
        q = look_at_quat((0, 0, 0), (5, 0, 0))
        fwd = quat_rotate(q, (0, 0, -1))
        self.assertAlmostEqual(fwd[0], 1.0, places=6)


class StripTests(unittest.TestCase):
    def test_restart_and_alternation(self):
        # two strips: 0-1-2-3 and 4-5-6
        tris = strip_triangles(bytes([1, 1, 0, 0, 1, 1, 0]))
        self.assertEqual(tris, [(0, 1, 2), (3, 2, 1), (4, 5, 6)])


class PngTests(unittest.TestCase):
    def test_roundtrip(self):
        px = bytes(range(256)) * 4
        w, h, out = decode_png(encode_png(16, 16, px))
        self.assertEqual((w, h, out), (16, 16, px))


class TexTests(unittest.TestCase):
    def test_psmt4_and_clut_order(self):
        clut = bytearray(1024)
        for i in range(256):
            clut[i * 4:i * 4 + 4] = bytes((i, 0, 0, 0x80))
        pal = palette_rgba(bytes(clut))
        self.assertEqual(pal[8][0], 16)    # logical 8 is stored at 16 (CSM1)
        self.assertEqual(pal[16][0], 8)
        self.assertEqual(pal[0][3], 255)   # 0x80 alpha -> opaque
        tex = struct.pack("<4I", 1, 0, 0, 0) + struct.pack("<4I", 0x14, 4 | (2 << 16), 0, 16)
        tex += bytes([0x21, 0x43, 0x65, 0x87]) + bytes(clut)
        img = parse_tex_bytes(tex)[0]
        self.assertEqual(list(img.indices), [1, 2, 3, 4, 5, 6, 7, 8])

    def test_rejects_trailing_bytes(self):
        tex = struct.pack("<4I", 1, 0, 0, 0) + struct.pack("<4I", 0x13, 1 | (1 << 16), 0, 16) + bytes(1025 + 4)
        with self.assertRaises(FormatError):
            parse_tex_bytes(tex)


def make_bank() -> bytes:
    """One entry; slot 1 has a group of 2 frames with one rotation track."""
    header = struct.pack("<4I", 1, 1, 0, 0)
    entry_at = 16
    group_at = 16 + 20
    words = [0, group_at - entry_at, 0, 0, 0]
    entry = struct.pack("<5I", *words)
    group = struct.pack("<3I", 1, 2, 12)
    track_at = group_at + 12
    payload_at = track_at + 12
    track = struct.pack("<iII", 3, 0, payload_at - track_at)
    payload = struct.pack("<6h", 16384, 0, 0, -16384, 0, 0)
    return header + entry + group + track + payload


class MotionTests(unittest.TestCase):
    def test_parse(self):
        bank = parse_motion_bank(make_bank())
        g = bank.entries[0].slots[1]
        self.assertEqual(g.frames, 2)
        t = g.tracks[0]
        self.assertEqual(t.target, 3)
        self.assertAlmostEqual(t.samples[0][0], math.pi / 2)
        self.assertAlmostEqual(t.samples[1][0], -math.pi / 2)

    def test_bounds(self):
        with self.assertRaises(FormatError):
            parse_motion_bank(make_bank()[:-4])



class AlphaModeTests(unittest.TestCase):
    def test_elements_join_by_position(self):
        pos = [(0, 0), (1, 0), (0, 1), (1, 0), (1, 1), (5, 5), (6, 5), (5, 6)]   # 3 duplicates 1 (UV seam)
        groups = elements([(0, 1, 2), (3, 4, 2), (5, 6, 7)], lambda v: pos[v])
        self.assertEqual(sorted(map(sorted, groups)), [[0, 1], [2]])

    def test_sampler_reads_texels_inside_triangle(self):
        alpha = bytes([0, 255, 255, 255]) * 4          # 4x4, column 0 transparent
        s = AlphaSampler(4, 4, alpha)
        self.assertNotIn(0, s.texels([(0.5, 0.0), (1.0, 0.0), (1.0, 1.0)]))
        self.assertIn(0, s.texels([(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]))

    def test_texel_modes(self):
        self.assertEqual(texel_mode([255, 254, 236]), "OPAQUE")
        self.assertEqual(texel_mode([255, 0, 4]), "MASK")
        self.assertEqual(texel_mode([255, 128]), "BLEND")

    def test_assign_modes_split_and_cleanup(self):
        # 6 x 3 grid of quads (2 triangles each); texels set per quad.
        pos = [(x, y) for y in range(4) for x in range(7)]
        tris, quad = [], []
        for y in range(3):
            for x in range(6):
                a, b, c, d = y * 7 + x, y * 7 + x + 1, (y + 1) * 7 + x, (y + 1) * 7 + x + 1
                tris += [(a, b, c), (b, d, c)]
                quad += [(x, y)] * 2
        def texels(i, reach):
            x, y = quad[i]
            if (x, y) == (3, 1):
                return [255]          # opaque speck inside the soft area
            if x >= 2:
                return [128, 255]     # soft
            if (x, y) == (1, 1):
                return [0, 255]       # cut-out next to solid texels
            return [255]
        modes = assign_modes(tris, lambda v: pos[v], texels, blend=True)
        at = lambda x, y: {modes[i] for i, q in enumerate(quad) if q == (x, y)}
        self.assertEqual(at(0, 0), {"OPAQUE"})
        self.assertEqual(at(1, 1), {"MASK"})     # borders opaque, so it stays a cut-out
        self.assertEqual(at(3, 1), {"BLEND"})    # enclosed opaque speck absorbed
        self.assertEqual(at(5, 2), {"BLEND"})
        self.assertEqual(set(assign_modes(tris, lambda v: pos[v], lambda i, r: [0, 255], blend=False)), {"TEST"})
        self.assertEqual(set(assign_modes(tris, lambda v: pos[v], texels, blend=True, faithful=True)), {"BLEND"})

    def test_bleed_fills_transparent_rgb_only(self):
        # 3x1: red visible, black transparent, blue visible; 4th row texel far away
        px = bytes([255, 0, 0, 255, 0, 0, 0, 0, 0, 0, 255, 128])
        out = bleed_transparent(3, 1, px)
        self.assertEqual(out[3::4], px[3::4])                 # alpha untouched
        self.assertEqual(out[4:7], bytes([128, 0, 128]))       # average of both neighbours
        far = bytes([10, 20, 30, 255] + [0, 0, 0, 0] * 4)
        self.assertEqual(bleed_transparent(5, 1, far)[16:19], bytes([10, 20, 30]))
        self.assertEqual(bleed_transparent(1, 1, bytes(4)), bytes(4))   # nothing visible: unchanged


class GltfTests(unittest.TestCase):
    def test_builder_roundtrip_glb_and_gltf(self):
        for suffix in (".glb", ".gltf"):
            b = GltfBuilder()
            pos = b.positions([(0, 0, 0), (1, 0, 0), (0, 1, 0)])
            idx = b.indices([0, 1, 2])
            mesh = b.mesh({"primitives": [{"attributes": {"POSITION": pos}, "indices": idx}]})
            b.node({"mesh": mesh}, root=True)
            img = b.image(encode_png(1, 1, b"\xff\0\0\xff"), "red")
            b.material({"pbrMetallicRoughness": {"baseColorTexture": {"index": b.texture(img)}}})
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / ("t" + suffix)
                b.write(path)
                self.assertEqual(validate(path), [])
                g = Gltf(path)
                self.assertEqual(g.accessor(pos)[1], (1.0, 0.0, 0.0))
                self.assertEqual(decode_png(g.image_bytes(0))[2], b"\xff\0\0\xff")

    def test_rejects_nan(self):
        with self.assertRaises(ValueError):
            GltfBuilder().positions([(float("nan"), 0, 0)])


class AudioTests(unittest.TestCase):
    def test_vag_end_flag_and_wav(self):
        frame = bytes([0x00, 0x00]) + bytes(14)
        last = bytes([0x00, 0x01]) + bytes(14)
        pcm = decode_vag(frame + last + frame, 0, 48, 22050)
        self.assertEqual(len(pcm.samples), 56)
        wav = pcm.wav()
        self.assertEqual(wav[:4], b"RIFF")
        self.assertEqual(len(wav), 44 + 112)

    def test_sq_running_status_compression(self):
        events = bytes([0x00, 0x90, 0x3C, 0xE4,      # note on, flag -> next event has no delta
                        0x80, 0xBC,                    # note off (key only), flag
                        0xFF, 0x2F, 0x00])             # no delta after a flagged event
        seq = struct.pack("<IH", 0, 96) + events
        chunk = b"IECSidiM" + struct.pack("<II", 20 + len(seq), 0) + struct.pack("<I", 20) + seq
        midi = sq_to_midi(chunk)
        self.assertTrue(midi.startswith(b"MThd"))
        track = midi[22:]
        self.assertEqual(track, bytes([0x00, 0x90, 0x3C, 0x64, 0x00, 0x80, 0x3C, 0x40, 0x00, 0xFF, 0x2F, 0x00]))


class KeyReductionTests(unittest.TestCase):
    def test_drop_flat_keys(self):
        from hgexport.gltf.builder import drop_flat_keys
        t, v = drop_flat_keys([0, 1, 2, 3, 4, 5], [1, 1, 1, 2, 2, 2])
        self.assertEqual((t, v), ([0, 2, 3, 5], [1, 1, 2, 2]))
        self.assertEqual(drop_flat_keys([0, 1, 2], [(0, 0), (0, 0), (0, 0)]), ([0, 2], [(0, 0), (0, 0)]))


if __name__ == "__main__":
    unittest.main()
