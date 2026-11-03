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


def to_array(url):
    import numpy as np
    from PIL import Image
    png = base64.b64decode(url.split(",", 1)[1])
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(int)


class Viewer(unittest.TestCase):
    """The demo's site, open on its 3D tab in a headless browser."""
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


@unittest.skipUnless(has_browser() and shutil.which("kicad-cli") and DEMO.exists(),
                     "needs Playwright, Pillow, kicad-cli and KiCad's demos")
class Turning(Viewer):
    def share(self, which, eye, up):
        """How much the objects `which` picks add to the picture from here, per pixel."""
        urls = self.page.evaluate("""([which, eye, up]) => {
            const v = document.getElementById('board3d').pv3d, objs = eval(which)(v);
            v.look(eye, up);
            const shown = v.frame();
            objs.forEach(o => { o.visible = false; });
            const hidden = v.frame();
            objs.forEach(o => { o.visible = true; });
            return [shown, hidden];
        }""", [which, eye, up])
        a, b = map(to_array, urls)
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


@unittest.skipUnless(has_browser() and shutil.which("kicad-cli") and DEMO.exists(),
                     "needs Playwright, Pillow, kicad-cli and KiCad's demos")
class Framing(Viewer):
    """The board comes up whole, and the wheel does nothing but zoom."""

    def setUp(self):
        self.page.set_viewport_size({"width": 1000, "height": 700})
        self.page.reload()
        self.page.wait_for_function("document.getElementById('board3d').pv3d", timeout=120_000)
        self.page.wait_for_timeout(300)

    def picture(self):
        """The frame as an array, once the view has stopped moving."""
        self.page.wait_for_timeout(600)
        url = self.page.evaluate("document.getElementById('board3d').pv3d.frame()")
        return to_array(url)

    def assert_whole_and_filling(self, what):
        import numpy as np
        im = self.picture()
        off = abs(im - im[0, 0]).max(axis=2) > 8          # anything but the background
        edge = off[0].sum() + off[-1].sum() + off[:, 0].sum() + off[:, -1].sum()
        self.assertEqual(int(edge), 0, f"{what}: the board runs off the picture")
        h, w = off.shape
        span = max(np.ptp(np.nonzero(off.any(axis=0))) / w, np.ptp(np.nonzero(off.any(axis=1))) / h)
        # not all of it: the view is centred on the laminate, and a connector may hang off one side
        self.assertGreater(span, 0.7, f"{what}: the board is small in the picture")

    def test_loads_whole_at_any_shape(self):
        for w, h in ((1000, 700), (1600, 600), (700, 900), (420, 800)):
            with self.subTest(width=w, height=h):
                self.page.set_viewport_size({"width": w, "height": h})
                self.assert_whole_and_filling(f"{w}x{h}")

    def test_every_view_button_fits(self):
        for name in ("front", "back", "tilt", "edge"):
            with self.subTest(view=name):
                self.page.locator(f'#board3d [data-view="{name}"]').click()
                self.assert_whole_and_filling(name)

    def camera(self):
        return self.page.evaluate("""() => {
            const v = document.getElementById('board3d').pv3d, c = v.camera, t = v.controls.target;
            return { d: c.position.distanceTo(t), t: t.toArray(), q: c.quaternion.toArray() };
        }""")

    def over_the_stage(self):
        box = self.page.locator("#b3-stage").bounding_box()
        return box["x"] + box["width"] * 0.7, box["y"] + box["height"] * 0.6      # not its centre

    def wheel(self, notches, dy):
        self.page.mouse.move(*self.over_the_stage())
        for _ in range(notches):
            self.page.mouse.wheel(0, dy)
            self.page.wait_for_timeout(40)
        self.page.wait_for_timeout(150)

    def test_zooming_only_zooms(self):
        was = self.camera()
        for dy in (120, -120):
            self.wheel(4, dy)
            now = self.camera()
            self.assertEqual(now["t"], was["t"], "the wheel moved the point turned round")
            for a, b in zip(now["q"], was["q"]):
                self.assertAlmostEqual(a, b, places=5, msg="the wheel turned the view")

    def test_a_notch_is_a_gentle_step_and_stops_at_once(self):
        was = self.camera()["d"]
        self.wheel(1, 120)
        now = self.camera()["d"]
        self.assertTrue(1.05 < now / was < 1.3, f"one notch took {was} to {now}")
        self.page.wait_for_timeout(300)
        self.assertEqual(self.camera()["d"], now, "still zooming after the wheel stopped")

    def test_zooming_out_stops_with_the_board_in_sight(self):
        self.wheel(60, 120)
        big = self.camera()["d"]
        self.wheel(10, 120)
        self.assertEqual(self.camera()["d"], big, "no limit to zooming out")
        off = abs((im := self.picture()) - im[0, 0]).max(axis=2) > 8
        self.assertGreater(int(off.sum()), 400, "zoomed out until the board is gone")

    def test_a_drag_stops_when_the_hand_does(self):
        x, y = self.over_the_stage()
        self.page.mouse.move(x, y)
        self.page.mouse.down()
        self.page.mouse.move(x + 120, y + 30, steps=4)
        self.page.mouse.up()
        self.page.wait_for_timeout(100)
        was = self.camera()["q"]
        self.page.wait_for_timeout(400)
        self.assertEqual(self.camera()["q"], was, "the board kept turning after the drag")

    def test_a_small_move_up_close_is_drawn(self):
        # the controls report a move only past a millimetre, and a board is in metres
        self.wheel(16, -120)
        stage = self.page.locator("#b3-stage")
        self.page.wait_for_timeout(200)
        before = stage.screenshot()
        self.wheel(1, -120)
        self.assertNotEqual(stage.screenshot(), before, "the camera moved and nothing was drawn")


if __name__ == "__main__":
    unittest.main()
