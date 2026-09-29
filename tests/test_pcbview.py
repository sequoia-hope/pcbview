"""pcbview's tests.

    python3 -m unittest discover tests          (from the repository root)

The end-to-end test builds KiCad's complex_hierarchy demo into a scratch
directory; it is skipped where kicad-cli or the demo is not installed.
"""
import json
import shutil
import sys
import tempfile
import unittest
from math import pi
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pcbview import config, sexp                       # noqa: E402
from pcbview.board import arc_len                      # noqa: E402
from pcbview.model3d import extent, rotated            # noqa: E402
from pcbview.models import Resolver, rewrite_models    # noqa: E402
from pcbview.site import span                          # noqa: E402

DEMO = Path("/usr/share/kicad/demos/complex_hierarchy/complex_hierarchy.kicad_pcb")


class Sexp(unittest.TestCase):
    def test_round_trip_atoms(self):
        t = sexp.parse('(a (b "x \\"y\\"") 3)')
        self.assertEqual(t[0], "a")
        self.assertEqual(sexp.unq(t[1][1]), 'x "y"')

    def test_stray_paren_is_read_past(self):
        # KiCad's own RoyalBlue54L-Feather demo has one; KiCad reads on
        t = sexp.parse("(root (a 1) (b 2)) filter 0.9) (c 3))")
        self.assertEqual([n[0] for n in t[1:] if isinstance(n, list)], ["a", "b", "c"])

    def test_unbalanced_open_still_fails(self):
        with self.assertRaises(ValueError):
            sexp.parse("(root (a 1)")


class Geometry(unittest.TestCase):
    def test_arc_len_semicircle(self):
        self.assertAlmostEqual(arc_len((1, 0), (0, 1), (-1, 0)), pi, places=9)
        self.assertAlmostEqual(arc_len((1, 0), (0, -1), (-1, 0)), pi, places=9)

    def test_rotation_is_kicads(self):
        # +90 in KiCad turns +x towards screen-up, which is -y
        x, y = rotated(1, 0, 90)
        self.assertAlmostEqual(x, 0, places=9)
        self.assertAlmostEqual(y, -1, places=9)

    def test_extent_courtyard_rotated(self):
        fp = sexp.parse('(footprint "x" (at 10 20 90) (layer "F.Cu")'
                        ' (fp_rect (start -2 -1) (end 2 1) (layer "F.CrtYd")))')
        at = sexp.find(fp, "at")
        self.assertEqual(extent(fp, at, local=True), (-2.0, -1.0, 2.0, 1.0))
        self.assertEqual(extent(fp, at), [9.0, 18.0, 11.0, 22.0])

    def test_extent_pads_when_no_courtyard(self):
        # the pad angle in the file includes the footprint's own
        fp = sexp.parse('(footprint "x" (at 0 0 90) (layer "F.Cu")'
                        ' (pad "1" smd rect (at -1 0 90) (size 0.5 0.4) (layers "F.Cu"))'
                        ' (pad "2" smd rect (at 1 0 90) (size 0.5 0.4) (layers "F.Cu")))')
        self.assertEqual(extent(fp, sexp.find(fp, "at"), local=True), (-1.25, -0.2, 1.25, 0.2))


class Models(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "parts").mkdir()
        (self.dir / "parts" / "only.wrl").write_text("#VRML")
        (self.dir / "parts" / "both.wrl").write_text("#VRML")
        (self.dir / "parts" / "both.step").write_text("ISO-10303-21;")
        (self.dir / "deep").mkdir()
        (self.dir / "deep" / "Elsewhere.STEP").write_text("ISO-10303-21;")
        self.r = Resolver(self.dir, {"sub/thing.wrl": (str(self.dir / "parts" / "both.step"), (0, 0, 0), (0, 0, -90))})

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_step_beside_wrl(self):
        res = self.r.resolve("${KIPRJMOD}/parts/both.wrl")
        self.assertEqual(res["status"], "ok")
        self.assertTrue(res["path"].endswith("both.step"))

    def test_wrl_only_is_missing_with_a_reason(self):
        res = self.r.resolve("${KIPRJMOD}/parts/only.wrl")
        self.assertEqual(res["status"], "missing")
        self.assertIn(".wrl", res["why"])

    def test_found_by_name(self):
        res = self.r.resolve("${SOME_CONVERTER_DIR}/x.3dshapes/elsewhere.wrl")
        self.assertEqual(res["status"], "found")

    def test_unknown_variable(self):
        res = self.r.resolve("${NOPE}/x/nothing.wrl")
        self.assertEqual(res["status"], "missing")
        self.assertIn("${NOPE}", res["why"])

    def test_legacy_library_variable(self):
        if not self.r.lib:
            self.skipTest("no KiCad 3D library installed")
        res = self.r.resolve("${KICAD6_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0402_1005Metric.wrl")
        self.assertEqual(res["status"], "resolved")
        self.assertTrue(Path(res["path"]).exists())

    def test_substitution_rewrites_the_block(self):
        text = ('\t(footprint "x"\n\t\t(model "${EASY}/sub/thing.wrl"\n\t\t\t(offset\n\t\t\t\t(xyz 1 2 3)\n'
                '\t\t\t)\n\t\t\t(scale\n\t\t\t\t(xyz 0.3937 0.3937 0.3937)\n\t\t\t)\n\t\t\t(rotate\n'
                '\t\t\t\t(xyz 0 0 90)\n\t\t\t)\n\t\t)\n\t)\n')
        out, seen = rewrite_models(text, self.r)
        self.assertIn("both.step", out)
        self.assertIn("(offset (xyz 0 0 0))", out)
        self.assertIn("(rotate (xyz 0 0 -90))", out)
        self.assertIn("(scale (xyz 1 1 1))", out)
        self.assertEqual(seen["${EASY}/sub/thing.wrl"]["status"], "substituted")


class Pages(unittest.TestCase):
    def test_span(self):
        self.assertEqual(span(["C1", "C2", "C3", "C7", "R1"]), "C1&ndash;C3, C7, R1")
        self.assertEqual(span(["U2", "U1"]), "U1, U2")


@unittest.skipUnless(shutil.which("kicad-cli") and DEMO.exists(), "needs kicad-cli and KiCad's demos")
class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pcbview.__main__ import build
        cls.out = Path(tempfile.mkdtemp())
        build(config.quick(DEMO, out=cls.out, title="complex"))
        cls.board = cls.out / DEMO.stem

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out)

    def test_page(self):
        html = (self.out / "index.html").read_text()
        for needle in ('data-tab="schematic"', 'data-tab="copper"', 'data-tab="board3d"', 'id="overview"'):
            self.assertIn(needle, html)
        for asset in ("app.js", "parts.js", "copper.js", "sch.js", "board3d.js", "pcbview.css",
                      "vendor/three.module.js"):
            self.assertTrue((self.out / "assets" / asset).exists(), asset)

    def test_sheets(self):
        sheets = json.loads((self.board / "sch" / "sheets.json").read_text())["sheets"]
        self.assertEqual([s["sheet"] for s in sheets], ["root", "ampli_ht_vertical", "ampli_ht_horizontal"])
        for s in sheets:
            self.assertTrue((self.board / "sch" / s["file"]).exists())

    def test_layers_register(self):
        meta = json.loads((self.board / "layers" / "layers.json").read_text())
        copper = [l for l in meta["layers"] if l["kind"] == "copper"]
        self.assertEqual(len(copper), 2)
        view = 'viewBox="%g %g %g %g"' % tuple(meta["view_mm"])
        for l in meta["layers"]:
            self.assertIn(view, (self.board / "layers" / l["file"]).read_text()[:400], l["name"])
        self.assertIn(view, (self.board / "layers" / "body.svg").read_text())

    def test_parts_and_model(self):
        parts = json.loads((self.board / "3d" / "parts.json").read_text())["parts"]
        self.assertGreater(len(parts), 50)
        self.assertTrue(all(p["box"] for p in parts.values()))
        facts = json.loads((self.board / "3d" / "board.json").read_text())
        self.assertEqual(facts["parts"], len(parts))
        self.assertGreater((self.board / "3d" / "board.glb").stat().st_size, 100_000)


if __name__ == "__main__":
    unittest.main()
