"""The 3D tab in a real browser: nothing may pop as the board turns.

    .venv/bin/python -m unittest tests.test_browser      (see "Tests" in README.md)

Builds KiCad's StickHub demo from a copy of the board alone, so its project-local
models are missing and those parts are drawn as translucent boxes, then walks the
camera past each face of the board. What a thing adds to the picture (the frame
with it, less the frame without it) changes smoothly as it turns, unless it pops.
Pages are answered from the disk through the browser's request routing, so no
server and no port are involved. Skipped where Playwright, kicad-cli or the demo
is not installed.
"""
import base64
import importlib.util
import io
import mimetypes
import shutil
import sys
import tempfile
import unittest
from math import cos, radians, sin
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pcbview import config                             # noqa: E402
from pcbview.model3d import read_glb, write_glb        # noqa: E402

DEMO = Path("/usr/share/kicad/demos/stickhub/StickHub.kicad_pcb")
HOST = "http://pcbview.test/"
TILTS = range(-12, 13)              # degrees off face-on: a pop shows as one big step
AZIMUTHS = (25, 115)                # the way the board is tipped: where it pops depends on it
MAX_STEP = 0.10                     # the most its share may move in a degree, of its mean
SILK = "v => v.layers.silkscreen"
BOXES = "v => [...v.parts.values()].filter(p => p.userData.placeholder)"


def has_browser():
    return all(importlib.util.find_spec(m) for m in ("playwright", "PIL", "numpy"))


def look_past_face(ny, azimuth, tilt):
    """(direction to the eye, up): tilted off the face whose normal is +y or -y."""
    a, t = radians(azimuth), radians(tilt)
    return [cos(a) * sin(t), ny * cos(t), sin(a) * sin(t)], [0, 0, -1]


@unittest.skipUnless(has_browser() and shutil.which("kicad-cli") and DEMO.exists(),
                     "needs Playwright, Pillow, kicad-cli and KiCad's demos")
class Turning(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        from pcbview.__main__ import build
        cls.dir = Path(tempfile.mkdtemp())
        pcb = cls.dir / DEMO.name
        shutil.copy(DEMO, pcb)
        build(config.quick(pcb, out=cls.dir / "site", title="turning"), only=["3d"])
        cls.age_the_model(cls.dir / "site" / pcb.stem / "3d" / "board.glb")
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                                                   "--ignore-gpu-blocklist"])
        cls.page = cls.browser.new_page(viewport={"width": 1000, "height": 700})
        cls.errors = []
        cls.page.on("pageerror", lambda e: cls.errors.append(str(e)))
        cls.page.route(HOST + "**", cls.answer)
        cls.page.goto(HOST + "index.html#3d")
        cls.page.wait_for_function("document.getElementById('board3d').pv3d", timeout=120_000)

    @staticmethod
    def age_the_model(glb):
        """Translucent silk, as a model built before model3d.py made it opaque has:
        sites get new viewer code without a new model, so the viewer must cope."""
        g, blob = read_glb(glb)
        for n in g["nodes"]:
            if n.get("extras", {}).get("layer") == "silkscreen":
                for p in g["meshes"][g["nodes"][n["children"][-1]]["mesh"]]["primitives"]:
                    m = g["materials"][p["material"]]
                    m["alphaMode"] = "BLEND"
                    m["pbrMetallicRoughness"]["baseColorFactor"][3] = 0.9
        write_glb(glb, g, blob)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        shutil.rmtree(cls.dir)

    @classmethod
    def answer(cls, route):
        path = cls.dir / "site" / urlparse(route.request.url).path.lstrip("/")
        if not path.is_file():
            return route.fulfill(status=404)
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        route.fulfill(status=200, content_type=kind, body=path.read_bytes())

    def share(self, which, eye, up):
        """How much the objects `which` picks add to the picture from here, per pixel."""
        import numpy as np
        from PIL import Image
        urls = self.page.evaluate("""([which, eye, up]) => {
            const v = document.getElementById('board3d').pv3d, objs = eval(which)(v);
            v.look(eye, up);
            const shown = v.frame();
            objs.forEach(o => { o.visible = false; });
            const hidden = v.frame();
            objs.forEach(o => { o.visible = true; });
            return [shown, hidden];
        }""", [which, eye, up])
        a, b = (np.asarray(Image.open(io.BytesIO(base64.b64decode(u.split(",", 1)[1]))).convert("RGB")).astype(int)
                for u in urls)
        return float(abs(a - b).mean())

    def assert_no_pop(self, which, what):
        for ny, face in ((1, "front"), (-1, "back")):
            for az in AZIMUTHS:
                with self.subTest(face=face, azimuth=az):
                    got = [self.share(which, *look_past_face(ny, az, t)) for t in TILTS]
                    mean = sum(got) / len(got)
                    self.assertGreater(mean, 0.001, f"{face} {what}: not on screen")
                    worst = max(abs(a - b) for a, b in zip(got, got[1:]))
                    self.assertLess(worst / mean, MAX_STEP, f"{face} {what} pops: "
                                    + " ".join(f"{x:.3f}" for x in got))

    def test_silkscreen_does_not_pop(self):
        self.assert_no_pop(SILK, "silkscreen")

    def test_placeholder_boxes_do_not_pop(self):
        self.assert_no_pop(BOXES, "boxes")

    def test_the_mask_is_drawn_before_anything_else_translucent(self):
        # three.js sorts translucent meshes by how far each one's origin is from
        # the eye, and the origins of a board's sheets and its parts swap places
        # near face-on. Only a fixed order holds, whatever the board.
        drawn = self.page.evaluate("""() => {
            const out = [];
            document.getElementById('board3d').pv3d.scene.traverse(m => {
                if (!m.isMesh || !m.material.transparent) return;
                let o = m;
                while (o && !o.userData.layer && !o.userData.ref) o = o.parent;
                out.push({ layer: o && o.userData.layer, ref: o && o.userData.ref, order: m.renderOrder });
            });
            return out;
        }""")
        masks = [d["order"] for d in drawn if d["layer"] == "soldermask"]
        others = [d for d in drawn if d["layer"] != "soldermask"]
        self.assertTrue(masks, "no translucent mask")
        self.assertTrue(others, "no translucent part: this board has no boxes")
        self.assertFalse([d for d in others if d["layer"]], "only the mask may be translucent on the board")
        self.assertLess(max(masks), min(d["order"] for d in others))

    def test_no_page_errors(self):
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
