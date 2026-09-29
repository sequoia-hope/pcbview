"""layers.py -- the PCB tab: one SVG per layer, stacked in register.

Writes into <site>/<board>/layers/:

    f.svg in1.svg ... b.svg   one transparent SVG per copper layer
    edge.svg                  the outline, drawn over everything
    silk_f.svg silk_b.svg     silkscreen, front and back (off at first)
    fab_f.svg fab_b.svg       part bodies and references, front and back
    body.svg                  the board itself, filled, under everything
    layers.json               the panel's facts: colour, track count and
                              length, pad count, pour nets, a note, and the
                              crop in KiCad millimetres

Every layer is exported by `kicad-cli pcb export svg` on the board's own page
(--page-size-mode 1), which draws in KiCad's coordinates: one SVG unit is one
millimetre from the page origin, exactly the numbers pcbnew shows. So the
layers register with each other and with the board body drawn here from the
outline, and the viewer turns a cursor into the same x and y as pcbnew. Each
file is then cropped to the board's box.

The export keeps KiCad's theme colours, so a layer is the colour it is in
pcbnew; drill holes come out white on copper and black elsewhere and are
repainted in the viewer's plate colour, so a hole reads as a hole.
SVG rather than PNG because the point of the thing is to zoom in on a track.
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

from .board import Board
from .config import scratch
from .util import need_kicad, page_of, sh, shrink, svg_body

# the grey canvas Altium 365's viewer draws a board on; drill holes are
# painted in it so they punch through the board body to the background
PLATE = "#c8c8c8"
BODY = "#1b1d21"            # the laminate: dark, as KiCad's layer colours expect

# Overlays drawn alongside the copper, in panel order: (layer, slug, kind).
# The outline is always drawn; the rest start switched off.
OVERLAYS = [("Edge.Cuts", "edge", "outline"),
            ("F.SilkS", "silk_f", "silk"), ("B.SilkS", "silk_b", "silk"),
            ("F.Fab", "fab_f", "fab"), ("B.Fab", "fab_b", "fab")]

# A layer keeps the colour pcbnew draws it in, so the page and the editor
# agree -- except the fab layers, annotation drawn over whatever copper is
# underneath, which have to stay readable there. KiCad's B.Fab in particular
# is darker than the copper it is read against.
OVERRIDE = {"F.Fab": "#DDE1E8", "B.Fab": "#96A2CE"}

NOTES = {
    "Edge.Cuts": "The outline and every cut-out in it.",
    "F.SilkS": "Silkscreen, front: reference designators and markings as printed.",
    "B.SilkS": "Silkscreen, back, seen through the board (mirror the view to read it).",
    "F.Fab": "Part bodies and reference designators, front: what each piece of copper belongs to.",
    "B.Fab": "Part bodies and reference designators, back.",
}


def copper_note(name, index, count, stack):
    """What a copper layer is, from its place in the stack and its thickness."""
    if name == "F.Cu":
        what = "Top copper"
    elif name == "B.Cu":
        what = "Bottom copper"
    else:
        what = "Inner layer %d of %d" % (index, count - 2)
    t = stack.get(name, {}).get("thickness")
    if t:
        um = t * 1000
        oz = um / 34.8
        std = min((0.5, 1, 2, 3, 4), key=lambda o: abs(o - oz))
        what += ", %g µm" % round(um, 1) + (" (%g oz)" % std if abs(std - oz) / std < 0.12 else "")
    return what + "."


def recolour(text, kind):
    """Repaint the drill holes in the plate colour. KiCad draws them white on
    a copper layer and black everywhere else; on a copper layer black is only
    the exporter's empty opening group, which draws nothing."""
    text = text.replace("#FFFFFF", PLATE)
    if kind != "copper":
        text = text.replace("#000000", PLATE)
    return text


def dominant(text, skip):
    """The colour the layer is actually drawn in: the one the most elements
    use, ignoring the plate and the exporter's boilerplate."""
    counts = {}
    for c in re.findall(r"#[0-9A-Fa-f]{6}", text):
        c = c.upper()
        if c not in skip:
            counts[c] = counts.get(c, 0) + 1
    return max(counts, key=counts.get) if counts else None


def body_svg(polys, view):
    """The board, filled: outer rings and their holes as one even-odd path."""
    d = []
    for poly in polys:
        for ring in poly:
            d.append("M" + " L".join("%.4g %.4g" % (x, y) for x, y in ring) + "Z")
    return ('<svg xmlns="http://www.w3.org/2000/svg" version="1.1" %s>\n'
            '<path fill="%s" fill-rule="evenodd" d="%s"/>\n</svg>\n' % (view, BODY, " ".join(d)))


def run(site, board, out):
    """Plot one board's layers into out/layers/."""
    need_kicad()
    pcb = Board(board.pcb)
    dest = out / "layers"
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.glob("*.svg"):
        old.unlink()

    user = {n: u for n, _, u in pcb.layers}
    plan = [(ly, ly.split(".")[0].lower(), "copper") for ly in pcb.copper]
    plan += [o for o in OVERLAYS if o[0] in user]

    raw, page = {}, None
    tmp = scratch("pcbview_layers_")
    for ly, slug, kind in plan:
        svg = tmp / f"{slug}.svg"
        sh(["kicad-cli", "pcb", "export", "svg", "--mode-single", "--layers", ly,
            "--page-size-mode", "1", "--exclude-drawing-sheet", "-o", svg, board.pcb])
        text = svg.read_text()
        p = page_of(text)
        if page and p != page:
            sys.exit(f"layer {ly} came out on page {p}, not {page}: the layers would not register")
        page, raw[slug] = p, text

    # crop to the board's own box, so the board fills the frame
    x0, y0, x1, y1 = pcb.box()
    margin = max(0.75, 0.02 * max(x1 - x0, y1 - y0))
    # to the micron, which is what shrink() leaves in every layer file: the
    # layers, the body and layers.json then name the very same box
    vx, vy, vw, vh = (round(v, 3) for v in (x0 - margin, y0 - margin,
                                            x1 - x0 + 2 * margin, y1 - y0 + 2 * margin))
    view = 'width="%gmm" height="%gmm" viewBox="%g %g %g %g"' % (vw, vh, vx, vy, vw, vh)

    facts, totals = pcb.facts()
    stack = pcb.stackup()
    notes = site.layer_notes
    layers, total = [], 0
    for ly, slug, kind in plan:
        text = raw[slug]
        colour_drawn = dominant(text, {"#FFFFFF", "#000000", PLATE.upper()})
        if kind not in ("copper", "outline") and not colour_drawn:
            continue                # an overlay with nothing on it but the drill marks
        colour_drawn = colour_drawn or "#888888"
        colour = OVERRIDE.get(ly, colour_drawn)
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" version="1.1" %s>%s</svg>'
               % (view, recolour(svg_body(text), kind).replace(colour_drawn, colour)))
        path = dest / f"{slug}.svg"
        path.write_text(shrink(svg))
        total += path.stat().st_size
        name = user.get(ly, ly)
        note = notes.get(name) or notes.get(ly) or (
            copper_note(ly, pcb.copper.index(ly), len(pcb.copper), stack)
            if kind == "copper" else NOTES.get(ly, ""))
        row = {"name": name, "slug": slug, "kind": kind, "file": path.name,
               "color": colour, "note": note}
        if kind == "copper":
            f = facts[ly]
            row.update(tracks=f["tracks"], length_mm=round(f["length_mm"], 1),
                       pads=f["pads"], zones=f["zones"])
        layers.append(row)
        print("  %-12s %-8s %8d bytes  %s" % (name, colour, path.stat().st_size,
              ("%d tracks, %.0f mm, %d pads, pour %s" % (row["tracks"], row["length_mm"], row["pads"],
               ", ".join(row["zones"]) or "none")) if kind == "copper" else kind))

    shape = pcb.shape()
    (dest / "body.svg").write_text(body_svg(shape["polys"], view))
    ring = pcb.circle()
    polar = board.polar if board.polar is not None else bool(ring)
    meta = {"board": board.pcb.name, "generated": date.today().isoformat(),
            "view_mm": [vx, vy, vw, vh],
            "size_mm": [round(x1 - x0, 3), round(y1 - y0, 3)],
            "round": {"centre": [ring[0], ring[1]], "dia": round(2 * ring[2], 3)} if ring else None,
            "polar": [ring[0], ring[1]] if polar and ring else None,
            "thickness": shape.get("thickness"),
            "plate": PLATE, "body": "body.svg", **totals, "layers": layers}
    (dest / "layers.json").write_text(json.dumps(meta, indent=1) + "\n")
    print("  view %.2f x %.2f mm, board %.2f x %.2f mm, %d vias, %.1f MB of layers"
          % (vw, vh, x1 - x0, y1 - y0, totals["vias"], total / 1e6))
    return meta
