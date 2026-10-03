# pcbview

KiCad boards on the web: a **schematic / PCB / 3D** viewer laid out after Altium 365's,
generated from the board files by KiCad's own exporters.

```sh
~/Software/pcbview/bin/pcbview build pcbview.toml      # or: PYTHONPATH=~/Software/pcbview python3 -m pcbview ...
```

turns a KiCad project into a static site (no server code, no build step for the page) with:

- **SCH** — every sheet as `kicad-cli sch export svg` plots it, the hierarchy read from the
  files (a sheet used twice is plotted twice, as KiCad numbers its pages), zoom until the
  sheet's millimetre is ~40 px, and the PDF of the whole schematic.
- **PCB** — every copper layer, plus silkscreen and fab overlays, plotted in KiCad's own
  coordinates so they stack in register. Pick a layer to bring it to the front, dim the rest,
  mirror to look from the back, grid them side by side. Drag to pan, wheel or pinch to zoom. The cursor reads out the x and y
  pcbnew shows (and radius and angle on a round board). Click a part for the part pane.
- **3D** — the board as `kicad-cli pcb export glb` makes it, merged and quantized from ~30 MB
  to ~8. Hover a part for its reference, click for the part pane, double-click or type a
  reference to fly to it; top / bottom / tilted / edge views; toggle parts, copper, mask,
  silk, laminate.
- **Part pane** — value, LCSC number (linked to LCSC and JLCPCB), footprint and its
  description, position, rotation, side, height, every other field, and the net on every
  pin. Shared by the PCB and 3D tabs: a part chosen in one is chosen in the other.
- **Overview** — where the files came from (a git tag and commit, if so), size, stackup,
  counts, and exactly which parts have no 3D model and why.

It began as servodrive's project-page viewer (`~/pcb/servodrive`: `shell.js`, `copper.js`,
`sch.js`, `board3d.js`, `tools/plot_layers.py`, `tools/export_3d.py`,
`tools/flatten_step.py`), and servodrive's pages now run on it, embedded. Not to be
confused with `~/Software/boardvis`, which turns an IPC-2581 export into printable
assembly drawings.

## Where it is used

| Project | Config | Site |
|---|---|---|
| rp2350-motor-controller, rev A as fabricated (tag `rev-A-fab`) | `~/pcb/rp2350-motor-controller/pcbview.toml` | `review/viewer/`, served with the review site |
| servodrive boards A and S, embedded in the project's own pages | `~/pcb/servodrive/pcbview.toml` | `viewer/`, written into `index.html` and `single.html`; `tools/regen.py` builds it |
| three KiCad demo projects | `examples/kicad-demos.toml` | `demo/kicad/` |

This project's own page (`proj up pcbview`) is the status page, with the examples under it.

## Needs

KiCad 9's `kicad-cli` on the PATH, KiCad's Python module (`pcbnew`, for the board outline;
without it the outline falls back to the Edge.Cuts box), Python 3.11+ and numpy. The page
itself needs only a static file server and a browser with WebGL.

## The config file

```toml
title = "RP2350 Motor Controller"       # the top bar
subtitle = "sequoia-hope"               # the small line above it
out = "review/viewer"                   # the site, relative to this file
back = { href = "../index.html", label = "Review hub" }   # optional back link
repo = "https://github.com/..."         # optional Repository button

[[boards]]                              # one or more
id = "rev-a"                            # directory and page name
name = "Rev A — as fabricated"
git = "rev-A-fab"                       # optional: read the files at this ref
repo = "."                              # with git: the repository (default: this file's)
pcb = "hardware/rp2350_driver.kicad_pcb"   # with git: relative to the repo root
sch = "hardware/rp2350_driver.kicad_sch"   # optional; the root sheet
note = "..."                            # the Overview's first paragraph
front = "Top"                           # optional names for the faces,
back = "Bottom"                         #   and notes shown under them
front_note = ""; back_note = ""
polar = true                            # optional: r/θ readout (default: only if the outline is a circle)
project_dir = "..."                     # optional: what ${KIPRJMOD} means (default: the board's directory)
embed = "board.html"                    # optional: also write the viewer into this page (see Embedding)

[boards.sheets.01_power]                # optional: a sheet's name and what is on it, by its path
title = "Power supply"                  #   (names down the hierarchy joined with /; "root" is the top);
desc = "the bucks and the bus input"    #   otherwise the sheet's name and its title block's comment

[parts]                                 # optional
role_field = "servodrive_role"          # a footprint field to show under the value
lcsc_csv = "hardware/parts/lcsc.csv"    # value,footprint,lcsc,mpn
bom = ["hardware/production/bom.csv"]   # JLCPCB-style BOMs: Designator + LCSC columns

[layers.notes]                          # optional: a note per layer, by name
"In2.Cu" = "The power plane."

[models."DFN-8_L3.0-W3.0-P0.65-BL-EP.wrl"]   # a stand-in, matched on the end of the path
path = "${KICAD9_3DMODEL_DIR}/Package_DFN_QFN.3dshapes/DFN-8-1EP_3x3mm_P0.65mm_EP1.55x2.4mm.step"
offset = [0, 0, 0]                      # KiCad's model offset, mm
rotate = [0, 0, -90]                    # KiCad's model rotation: clockwise-positive
```

A part's LCSC number comes from its own field first (`LCSC`, `LCSC Part`, `JLCPCB Part #`
and the like), then a BOM by designator, then the value/footprint table.

**Git sources.** With `git`, the board, every `.kicad_sch` beside it and the project files are
written from that ref into a scratch directory, so the page shows exactly the tagged design
even while the working tree moves on. `${KIPRJMOD}` in a model path still means the board's
directory in the working tree, where the project keeps its models.

No config at all: `bin/pcbview quick board.kicad_pcb -o site/` (the schematic is the
`.kicad_sch` of the same name, if there is one).

Paths the pages print (the Overview's source, `site.json`, `built.json`) are relative to
the config file's directory, or from `~`, never the machine's absolute paths: a site is
often published.

## Embedding

A project that has pages of its own can have the viewer written into one, instead of (as
well as) linking to the site's own page. Give the board `embed = "page.html"` and put two
markers in the page where the viewer goes:

```html
<!-- pcbview:begin -->
<!-- pcbview:end -->
<div class="vw-about" data-pv-note="copper">what the page says about the PCB tab</div>
```

Every build replaces what is between the markers with the viewer: `assets/viewer.css`,
the panels (`<section class="vw" data-pv-embed>`), the scripts and the import map, all
with paths relative to the page, the data read from the site. Nothing else on the page is
touched, and a page without the markers stops the build. Only one viewer to a page (the
panels have ids).

Embedded, the viewer keeps to itself: a tab changes nothing but the viewer (no history
entry), a `#hash` that names a view (`#copper`, `#sch-<sheet>`, ...) opens it and scrolls
to it, and any other hash is the page's. Keys work only while the viewer is on screen.
Elements anywhere on the page marked `data-pv-note="<view>"` (schematic, copper, board3d,
overview) are shown only while that view is open. `window.PV.view(id)` opens a view from
the page's own script.

`viewer.css` styles only what is inside `section.vw`, and gives its palette at zero
specificity, so a page that sets `--card`, `--chrome`, `--accent`, `--canvas` and the rest
on `:root` recolours it. Its height is `--pv-embed-h` (default: the window less 6rem).

## Commands

```sh
bin/pcbview build pcbview.toml                  # every board, every tab
bin/pcbview build pcbview.toml --only 3d        # stages: sch, layers, 3d (others kept from the last run)
bin/pcbview build pcbview.toml --board rev-a    # some boards
bin/pcbview build pcbview.toml --only 3d --parts-only   # the part pane's data, not the model
bin/pcbview models pcbview.toml                 # where every 3D model resolves, and why not
bin/pcbview quick board.kicad_pcb -o site/
```

## What comes out

```
<out>/index.html              the viewer (one board), or the board list (several)
<out>/<board>.html            each board's viewer, when there are several
<out>/assets/                 viewer.css (the viewer), pcbview.css (the app page round it), app.js,
                              viewer.js, parts.js, copper.js, sch.js, board3d.js, three.js
<out>/<board>/sch/            <root>[-<sheet>...].svg, <root>.pdf, sheets.json
<out>/<board>/layers/         f.svg in1.svg ... b.svg, edge.svg, silk_*.svg, fab_*.svg, body.svg, layers.json
<out>/<board>/3d/             board.glb, board.json (the caption), parts.json (the part pane)
<out>/<board>/built.json      what each stage returned, and when
```

A #hash opens a view: `#schematic` (`#sch`), `#copper` (`#pcb`), `#board3d` (`#3d`),
`#overview` (`#info`), or a sheet: `#sch-<sheet path>`.

## How the 3D model gets made

kicad-cli's GLB exporter takes a model path as the board file writes it and silently drops a
part it cannot resolve. A board that has lived through a few KiCad versions writes them
several ways, so every path is resolved first (`pcbview/models.py`):

1. a stand-in from `[models]`;
2. variables expanded — `${KIPRJMOD}` is the board's directory, every `${KICADn_3DMODEL_DIR}`
   (and `${KISYS3DMOD}`) is KiCad 9's library, anything else from KiCad's settings or the
   environment;
3. the STEP beside a `.wrl` (the exporter meshes STEP, not VRML);
4. a STEP of the same name anywhere under the board's directory.

Then, after the export (`pcbview/model3d.py`):

- a part whose model loads but meshes as nothing — so far always a STEP assembly of
  assemblies, which kicad-cli 9.0.8 writes as empty nodes without a word (KiCad's own
  `SOT-89-3.step` and `L_Sunlord_SWPA4030S.step` are like this) — has its model flattened
  (`pcbview/flatten.py`, cached in `~/.cache/pcbview/flat/`) and the board is exported again;
- a part still without geometry is drawn as a translucent orange box the size of its
  courtyard, marked in the part pane, and listed in the Overview with the path that failed;
- the glTF is rewritten small: primitives merged per material, positions quantized to 16
  bits (`KHR_mesh_quantization`), the board's flat meshes welded without normals, the
  board's colours converted from the sRGB KiCad writes to the linear glTF means (the parts'
  come through OCC linear already, and are left alone), the mask made translucent.

Working out a stand-in's rotation: compare the footprint's pads with the stand-in's own KiCad
footprint, in the library frame (a bottom-side footprint's pads are stored flipped: negate y).
KiCad writes model rotations clockwise-positive, so a quarter turn anticlockwise is `-90`.
The rp2350 config has worked examples.

## Layout

```
pcbview/          the Python package
  __main__.py     the command line
  config.py       the TOML file, git sources
  board.py        the board's outline (via pcbnew), stackup, counts
  layers.py       the PCB tab's plots
  schematic.py    the SCH tab's plots
  model3d.py      the 3D tab's GLB and the part pane's data
  models.py       3D model path resolution
  flatten.py      STEP assembly flattening
  site.py         the pages
  sexp.py         a KiCad s-expression reader (tolerates a stray paren, as KiCad does)
web/              the front end, copied into every site: viewer.js runs the tabs, app.js
                  the page round them (tree, breadcrumb), parts.js the part pane,
                  gesture.js the plates' drag / pinch / tap (the 3D tab's
                  TrackballControls has its own)
examples/         configs for the example sites
tests/            python3 -m unittest discover tests
```
