"""board.py -- what the board file says about itself: the outline, the
stackup, the counts the pages quote.

The outline comes from pcbnew itself (GetBoardPolygonOutlines): arcs,
footprint-drawn edges and cut-outs are its problem, and it has solved them.
It runs in a child process, as pcbnew is not always itself again in a
process that has already loaded or exported a board. Everything else is read
from the s-expression directly.
"""
import json
import subprocess
import sys
from math import atan2, hypot, isclose, pi

from . import sexp

OUTLINE = r"""
import json, os, sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
ps = pcbnew.SHAPE_POLY_SET()
ok = b.GetBoardPolygonOutlines(ps)
mm = lambda c: [[round(c.CPoint(k).x / 1e6, 4), round(c.CPoint(k).y / 1e6, 4)]
                for k in range(c.PointCount())]
polys = []
for i in range(ps.OutlineCount()):
    polys.append([mm(ps.Outline(i))] + [mm(ps.Hole(i, h)) for h in range(ps.HoleCount(i))])
print(json.dumps({"ok": bool(ok), "polys": polys,
                  "thickness": b.GetDesignSettings().GetBoardThickness() / 1e6}))
sys.stdout.flush()
os._exit(0)
"""


def outline(pcb):
    """{"ok", "polys": [[outer, hole, ...], ...] in KiCad mm, "thickness"}, or
    None when pcbnew is not importable."""
    r = subprocess.run([sys.executable, "-c", OUTLINE, str(pcb)], capture_output=True, text=True)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        print("  ! pcbnew could not read the outline (%s); using the Edge.Cuts box"
              % (r.stderr.strip().splitlines() or ["no output"])[-1])
        return None


def bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


class Board:
    """The parsed board, and the facts every stage wants from it."""

    def __init__(self, path):
        self.path = path
        self.text = path.read_text()
        self.tree = sexp.parse(self.text)
        self.layers = []             # (name, type, user name) in file order
        for l in (sexp.find(self.tree, "layers") or [])[1:]:
            self.layers.append((sexp.unq(l[1]), l[2],
                                sexp.unq(l[3]) if len(l) > 3 else sexp.unq(l[1])))
        self.copper = [n for n, t, _ in self.layers if n.endswith(".Cu")]
        self.edges = self._edges()
        self._shape = None

    # ---- the outline ---------------------------------------------------------
    def _edges(self):
        """Edge.Cuts drawings on the board itself: [(kind, node)]."""
        out = []
        for n in self.tree:
            if isinstance(n, list) and n and n[0] in ("gr_line", "gr_arc", "gr_circle",
                                                        "gr_rect", "gr_poly"):
                ly = sexp.find(n, "layer")
                if ly and sexp.unq(ly[1]) == "Edge.Cuts":
                    out.append((n[0], n))
        return out

    def circle(self):
        """(cx, cy, r) if the outline is one circle (a round board), else None."""
        circles = [n for k, n in self.edges if k == "gr_circle"]
        others = [n for k, n in self.edges if k != "gr_circle"]
        if not circles:
            return None
        best = None
        for c in circles:
            (cx, cy), (ex, ey) = (tuple(map(float, sexp.find(c, k)[1:3])) for k in ("center", "end"))
            r = hypot(ex - cx, ey - cy)
            if not best or r > best[2]:
                best = (cx, cy, r)
        # everything else must lie inside it: cut-outs, not a second board
        for n in others:
            for k in ("start", "end", "mid"):
                pt = sexp.find(n, k)
                if pt and hypot(float(pt[1]) - best[0], float(pt[2]) - best[1]) > best[2] + 1e-3:
                    return None
        return best

    def shape(self):
        """The outline as polygons (outer ring then holes), in KiCad mm."""
        if self._shape is None:
            o = outline(self.path)
            if o and o["ok"] and o["polys"]:
                self._shape = o
            else:
                x0, y0, x1, y1 = self.edge_box()
                self._shape = {"ok": False, "thickness": None,
                               "polys": [[[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]]]}
        return self._shape

    def edge_box(self):
        """The Edge.Cuts drawings' box, from their end points."""
        pts = []
        for kind, n in self.edges:
            if kind == "gr_circle":
                (cx, cy), (ex, ey) = (tuple(map(float, sexp.find(n, k)[1:3])) for k in ("center", "end"))
                r = hypot(ex - cx, ey - cy)
                pts += [(cx - r, cy - r), (cx + r, cy + r)]
            elif kind == "gr_poly":
                pts += [(float(p[1]), float(p[2])) for p in sexp.findall(sexp.find(n, "pts"), "xy")]
            else:
                for k in ("start", "end", "mid"):
                    pt = sexp.find(n, k)
                    if pt:
                        pts.append((float(pt[1]), float(pt[2])))
        if not pts:                       # no outline at all: every footprint
            for fp in self.footprints():
                at = sexp.find(fp, "at")
                pts.append((float(at[1]), float(at[2])))
        return bbox(pts) if pts else (0, 0, 100, 100)

    def box(self):
        """The board's box in KiCad mm: (x0, y0, x1, y1)."""
        return bbox([p for poly in self.shape()["polys"] for p in poly[0]])

    # ---- the rest -------------------------------------------------------------
    def footprints(self):
        return sexp.findall(self.tree, "footprint")

    def title_block(self):
        tb = sexp.find(self.tree, "title_block") or []
        out = {}
        for n in tb[1:]:
            if isinstance(n, list) and len(n) > 1:
                key = n[0] if n[0] != "comment" else "comment" + n[1]
                out[key] = sexp.unq(n[-1])
        return out

    def stackup(self):
        """layer name -> {type, thickness mm, material}, where the file has a stackup."""
        setup = sexp.find(self.tree, "setup") or []
        st = sexp.find(setup, "stackup") or []
        out = {}
        for l in sexp.findall(st, "layer"):
            row = {"type": ""}
            for k in ("type", "thickness", "material"):
                n = sexp.find(l, k)
                if n:
                    row[k] = sexp.unq(n[1]) if k != "thickness" else float(n[1])
            out[sexp.unq(l[1])] = row
        return out

    def facts(self):
        """Per copper layer: tracks, length, pads, pour nets; and board-wide counts."""
        f = {ly: {"tracks": 0, "length_mm": 0.0, "pads": 0, "zones": []} for ly in self.copper}
        vias = 0

        def xy(node, head):
            n = sexp.find(node, head)
            return (float(n[1]), float(n[2])) if n else None

        def layer_of(node):
            n = sexp.find(node, "layer")
            return sexp.unq(n[1]) if n else None

        nets = {sexp.unq(n[2]) for n in sexp.findall(self.tree, "net") if len(n) > 2 and sexp.unq(n[2])}
        for n in self.tree:
            if not isinstance(n, list) or not n:
                continue
            head = n[0]
            if head == "segment":
                ly = layer_of(n)
                if ly in f:
                    (x1, y1), (x2, y2) = xy(n, "start"), xy(n, "end")
                    f[ly]["tracks"] += 1
                    f[ly]["length_mm"] += hypot(x2 - x1, y2 - y1)
            elif head == "arc":
                ly = layer_of(n)
                if ly in f:
                    f[ly]["tracks"] += 1
                    f[ly]["length_mm"] += arc_len(xy(n, "start"), xy(n, "mid"), xy(n, "end"))
            elif head == "via":
                vias += 1
            elif head == "zone":
                name = sexp.find(n, "net_name")
                net = sexp.unq(name[1]) if name and len(name) > 1 else ""
                if not net:
                    continue                          # a rule area carries no net
                attr = sexp.find(n, "attr")
                if attr and sexp.find(attr, "teardrop"):
                    continue                          # KiCad 9 keeps teardrops as zones
                lys = sexp.find(n, "layers") or sexp.find(n, "layer") or []
                for a in lys[1:]:
                    ly = sexp.unq(a)
                    targets = self.copper if ly == "*.Cu" else \
                        ["F.Cu", "B.Cu"] if ly == "F&B.Cu" else [ly]
                    for t in targets:
                        if t in f and net not in f[t]["zones"]:
                            f[t]["zones"].append(net)
            elif head == "footprint":
                for pad in sexp.findall(n, "pad"):
                    lys = sexp.find(pad, "layers") or []
                    names = [sexp.unq(a) for a in lys[1:]]
                    for ly in self.copper:
                        if ly in names or "*.Cu" in names or \
                                (ly in ("F.Cu", "B.Cu") and "F&B.Cu" in names):
                            f[ly]["pads"] += 1
        fps = self.footprints()
        side = [sexp.unq(sexp.find(fp, "layer")[1]) for fp in fps]
        return f, {"vias": vias, "nets": len(nets), "footprints": len(fps),
                   "front": side.count("F.Cu"), "back": side.count("B.Cu"),
                   "tracks": sum(v["tracks"] for v in f.values()),
                   "length_mm": round(sum(v["length_mm"] for v in f.values()), 1)}


def arc_len(a, m, b):
    """Length of the arc through three points; the chord if they are collinear."""
    if not (a and m and b):
        return 0.0
    (x1, y1), (x2, y2), (x3, y3) = a, m, b
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if isclose(d, 0.0, abs_tol=1e-9):
        return hypot(x3 - x1, y3 - y1)
    ux = ((x1 ** 2 + y1 ** 2) * (y2 - y3) + (x2 ** 2 + y2 ** 2) * (y3 - y1)
          + (x3 ** 2 + y3 ** 2) * (y1 - y2)) / d
    uy = ((x1 ** 2 + y1 ** 2) * (x3 - x2) + (x2 ** 2 + y2 ** 2) * (x1 - x3)
          + (x3 ** 2 + y3 ** 2) * (x2 - x1)) / d
    r = hypot(x1 - ux, y1 - uy)
    a1, a2, a3 = (atan2(y - uy, x - ux) for x, y in (a, m, b))
    sweep = (a3 - a1) % (2 * pi)
    if (a2 - a1) % (2 * pi) > sweep:                 # it goes the other way round
        sweep = 2 * pi - sweep
    return r * sweep
