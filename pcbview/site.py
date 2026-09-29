"""site.py -- the pages: one viewer per board, and the files they load.

    <site>/index.html          the viewer (one board) or the board list
    <site>/<board>.html        a board's viewer, when there are several
    <site>/assets/             the front end, copied from pcbview/web
    <site>/<board>/layers/     layers.py
    <site>/<board>/3d/         model3d.py
    <site>/<board>/sch/        schematic.py
    <site>/site.json           what was built, from what, when

The page is static HTML with everything the build knows written into it --
the project tree, the tabs, the board's facts -- and the scripts only make it
move: tabs, pan and zoom, layers, the part pane.
"""
import json
import shutil
from datetime import date, datetime
from pathlib import Path

from .util import esc, natural

WEB = Path(__file__).resolve().parent.parent / "web"

ICON = {
    "chev": '<path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    "back": '<path d="M10 3 5 8l5 5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>',
    "menu": '<path d="M2 4h12M2 8h12M2 12h12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
    "folder": '<path d="M1 3.5h4.6l1.5 1.6H15v8.4H1z" fill="#fff"/>',
    "sheet": '<rect x="1" y="3" width="14" height="10.5" rx=".6" fill="#e5c987"/><path d="M9.5 9.8H15M9.5 9.8v3.7M12 9.8v3.7" stroke="#9a8450" stroke-width=".8"/>',
    "pcb": '<rect x="1" y="3" width="14" height="10.5" rx=".8" fill="#1fb182"/><rect x="8.3" y="5.2" width="4.6" height="4.6" fill="#fff"/><path d="M3 5.8h3M3 8.2h3M3 10.6h3M9.2 11.8v-2M11.9 11.8v-2" stroke="#fff" stroke-width="1.1"/>',
    "cube": '<path d="M8 1.6 14 5v6.2l-6 3.3-6-3.3V5zM2 5l6 3.3L14 5M8 8.3v6.2" fill="none" stroke="currentColor" stroke-width="1.15" stroke-linejoin="round"/>',
    "info": '<circle cx="8" cy="8" r="6.3" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M8 7.2v4.2M8 4.6v.1" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
    "layers": '<path d="M8 1.8 14.2 5 8 8.2 1.8 5z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/><path d="M1.8 8 8 11.2 14.2 8M1.8 11 8 14.2 14.2 11" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>',
    "full": '<path d="M9.5 2h4.5v4.5M14 2 9 7M6.5 14H2V9.5M2 14l5-5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>',
    "down": '<path d="M8 1.8v8.4M4.4 6.8 8 10.4l3.6-3.6M2 12v2.2h12V12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
    "gh": '<path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/>',
}
PROJ = ('<svg viewBox="0 0 34 34" aria-hidden="true"><path d="M12 2h11l7 7v20H12z" fill="#e3edf6"/>'
        '<path d="M23 2v7h7" fill="#b7c8d6"/><rect x="3" y="12.5" width="18.5" height="14" rx="1.5" fill="#1fb182"/>'
        '<rect x="11.2" y="15.3" width="7.4" height="7.4" fill="#fff"/><rect x="12.9" y="17" width="4" height="4" fill="#1fb182"/>'
        '<path d="M5.4 16h3.4M5.4 19.2h3.4M5.4 22.4h3.4M12.6 23.6v3M15 23.6v3M17.4 23.6v3" stroke="#fff" stroke-width="1.4"/></svg>')


def icon(name, cls=""):
    c = f' class="{cls}"' if cls else ""
    return f'<svg{c} viewBox="0 0 16 16" aria-hidden="true">{ICON[name]}</svg>'


def page_name(site, board):
    return "index.html" if len(site.boards) == 1 else f"{board.id}.html"


# ------------------------------------------------------------------ chrome --
def top(site, here):
    back = ""
    if site.back_href:
        back = (f'<a class="a3-back" href="{esc(site.back_href)}" title="{esc(site.back_label or "back")}" '
                f'aria-label="{esc(site.back_label or "back")}">{icon("back")}</a>')
    repo = (f'<a class="a3-btn primary" href="{esc(site.repo)}">{icon("gh")}<span>Repository</span></a>'
            if site.repo else "")
    return f"""<header class="a3-top">
  <button class="a3-menu" aria-label="project tree">{icon("menu")}</button>
  {back}
  <a class="a3-proj" href="index.html">{PROJ}<span><small>{esc(site.subtitle or "KiCad project")}</small><b>{esc(site.title)}</b></span></a>
  <div class="a3-sp"></div>
  {repo}
</header>"""


def row(depth, ic, label, href=None, open_=None, here=False, cls=""):
    leaf = open_ is None
    tw = (f'<button class="a3-tw{" leaf" if leaf else ""}" aria-expanded="{str(bool(open_)).lower()}" '
          f'tabindex="{-1 if leaf else 0}" aria-label="expand or collapse">{icon("chev")}</button>')
    t = f'<a class="t" href="{esc(href)}">{esc(label)}</a>' if href else f'<span class="t">{esc(label)}</span>'
    return (f'<div class="a3-row{" here" if here else ""}{(" " + cls) if cls else ""}" style="--d:{depth}" '
            f'title="{esc(label)}">{tw}{icon(ic, "ic")}{t}</div>')


def tree(site, here, sheets):
    """The project tree: every board, and inside the one on screen its views
    and its sheets."""
    out = ['<ul>', '<li>' + row(0, "folder", "Boards", open_=True) + '<ul>']
    for b in site.boards:
        mine = b is here
        pg = page_name(site, b)
        out.append('<li>' + row(1, "pcb", b.name, href=pg, open_=mine, cls="page") +
                   f'<ul{"" if mine else " hidden"}>')
        views = [("overview", "info", "Overview")]
        if b.sch:
            views.append(("schematic", "sheet", "Schematic"))
        views += [("copper", "pcb", "PCB"), ("board3d", "cube", "3D")]
        for vid, ic, label in views:
            kids = sheets.get(b.id) if (vid == "schematic" and mine) else None
            many = kids and len(kids) > 1
            out.append('<li>' + row(2, ic, label, href=f"{pg}#{vid}", open_=True if many else None)
                       .replace('class="a3-row', f'data-view="{vid}" class="a3-row' if mine else 'class="a3-row'))
            if many:
                out.append("<ul>")
                for s in kids:
                    out.append('<li>' + row(3, "sheet", s["title"], href=f'{pg}#sch-{s["sheet"]}')
                               .replace('class="a3-row', f'data-sheet="{esc(s["sheet"])}" class="a3-row') + '</li>')
                out.append("</ul>")
            out.append('</li>')
        out.append('</ul></li>')
    out.append('</ul></li></ul>')
    return "\n".join(out)


def side(site, here, sheets):
    foot = ""
    if site.back_href:
        foot = (f'<div class="a3-foot"><a class="a3-btn" href="{esc(site.back_href)}">{icon("back")}'
                f'<span>{esc(site.back_label or "Back")}</span></a></div>')
    return f"""<nav class="a3-side" aria-label="project">
  <div class="a3-label">Project</div>
  <div class="a3-tree">{tree(site, here, sheets)}</div>
  {foot}
</nav>"""


def source_line(board):
    s = board.source
    if s["kind"] == "git":
        return (f'<code>{esc(s["pcb"])}</code> at <b>{esc(s["ref"])}</b> '
                f'<span class="sub">({esc(s["commit"])}, {esc(s["date"])})</span>')
    return f'<code>{esc(Path(s["pcb"]).name)}</code>'


# ------------------------------------------------------------------ panels --
def schematic_panel(board, sheets):
    if not board.sch:
        return ""
    n = len(sheets or [])
    keys = f"<b>1</b>&ndash;<b>{min(n, 9)}</b> pick a sheet, " if n > 1 else ""
    return f"""
  <div class="vw-panel" id="schematic" data-dir="{board.id}/sch/" role="tabpanel" hidden>
    <div class="cu sch">
      <div class="cu-panel">
        <p class="cu-head">Sheets</p>
        <div id="sch-sheets"></div>
        <div class="cu-row">
          <button data-sch="out" title="zoom out">&minus;</button>
          <button data-sch="fit" title="the whole sheet">fit</button>
          <button data-sch="in" title="zoom in">+</button>
          <span class="sub" id="sch-zoom">1.0&times;</span>
        </div>
        <div class="cu-row"><a class="a3-btn" id="sch-pdf" download hidden>{icon("down")}<span>PDF, every sheet</span></a></div>
        <p class="sub cu-note" id="sch-cap"></p>
        <p class="cu-hint">Keys: {keys}<b>+</b> and <b>&minus;</b> zoom, <b>0</b> fit.
        Drag to pan; scroll or double-click to zoom.</p>
      </div>
      <div class="cu-canvas">
        <div class="sch-plate" id="sch-plate">
          <div class="sch-pan" id="sch-pan"><img id="sch-img" alt="" draggable="false"></div>
          <p class="b3-msg" id="sch-msg" hidden></p>
        </div>
      </div>
    </div>
  </div>"""


def copper_panel(board):
    return f"""
  <div class="vw-panel" id="copper" data-dir="{board.id}/layers/" data-parts="{board.id}/3d/parts.json" role="tabpanel">
    <div class="cu">
      <div class="cu-panel">
        <div id="cu-layers"></div>
        <p class="cu-hint" style="margin-top:.3rem">The dot puts a layer on top at full
        opacity; the box says whether it is drawn at all.</p>
        <div class="cu-row">
          <button data-act="all" title="show every layer">all</button>
          <button data-act="none" title="show only the selected layer">none</button>
          <button data-act="solo" title="the selected layer and the outline only">solo</button>
          <button data-act="grid" title="the copper layers side by side">grid</button>
        </div>
        <label class="cu-ctl">opacity of the unselected copper
          <input type="range" id="cu-dim" min="0" max="60" value="16"></label>
        <label class="cu-ctl"><input type="checkbox" id="cu-mirror">
          view from the back (mirrored)</label>
        <div class="cu-row">
          <button data-act="out" title="zoom out">&minus;</button>
          <button data-act="fit" title="fit the board">fit</button>
          <button data-act="in" title="zoom in">+</button>
          <span class="sub" id="cu-zoom">1.0&times;</span>
        </div>
        <p class="cu-note" id="cu-note"></p>
        <p class="cu-read" id="cu-read"></p>
        <p class="cu-hint">Keys: <b>1</b>&ndash;<b>9</b> pick a layer, <b>s</b> solo,
        <b>a</b> all, <b>m</b> mirror, <b>0</b> fit. Click a part for its pane;
        coordinates are pcbnew&rsquo;s, in millimetres.</p>
        <p class="cu-hint" id="cu-stats"></p>
      </div>
      <div class="cu-canvas">
        <div class="cu-plate" id="cu-plate">
          <div class="cu-pan" id="cu-pan"><div class="cu-flip" id="cu-flip"></div></div>
          <div class="cu-scale"><i id="cu-bar"></i><span id="cu-barlab"></span></div>
          <div class="cu-back" id="cu-back"></div>
          <p class="b3-msg" id="cu-fallback" hidden></p>
        </div>
        <div class="cu-grid" id="cu-gridbox" hidden></div>
      </div>
      <aside class="b3-info pv-pane" id="cu-info" aria-label="the selected part" hidden>
        <div class="b3-ih"><b class="pv-iref"></b><button class="b3-x" title="close"
          aria-label="close the part pane">&times;</button></div>
        <div class="b3-ib pv-ib"></div>
      </aside>
    </div>
  </div>"""


def board3d_panel(board):
    return f"""
  <div class="vw-panel" id="board3d" data-dir="{board.id}/3d/" role="tabpanel" hidden>
    <div class="b3">
      <div class="b3-bar">
        <div class="cu-row" role="group" aria-label="view">
          <button data-view="front" title="the {esc(board.front.lower())} face, as pcbnew draws it">{esc(board.front.lower())}</button>
          <button data-view="back" title="the {esc(board.back.lower())} face, turned over left to right">{esc(board.back.lower())}</button>
          <button data-view="tilt" title="from the front, tilted towards you">tilted</button>
          <button data-view="edge" title="edge on: how tall each face stands">edge</button>
        </div>
        <div class="b3-layers" id="b3-layers"></div>
        <label class="b3-find">find <input id="b3-find" list="b3-refs" placeholder="U1"
          disabled spellcheck="false" autocomplete="off" title="a reference, e.g. U1"></label>
        <datalist id="b3-refs"></datalist>
      </div>
      <div class="b3-body">
        <div class="b3-stage" id="b3-stage">
          <p class="b3-msg" id="b3-msg">The model loads when this tab is open.</p>
          <div class="b3-tag" id="b3-tag" hidden></div>
        </div>
        <aside class="b3-info pv-pane" id="b3-info" aria-label="the selected part" hidden>
          <div class="b3-ih"><b class="pv-iref"></b><button class="b3-x" title="close"
            aria-label="close the part pane">&times;</button></div>
          <div class="b3-ib pv-ib"></div>
        </aside>
      </div>
      <p class="b3-facts" id="b3-facts"></p>
    </div>
  </div>"""


def span(refs):
    """["C1","C2","C3","C7"] -> "C1–C3, C7"."""
    import re
    out, i = [], 0
    refs = sorted(refs, key=natural)
    while i < len(refs):
        m = re.match(r"^([A-Za-z_]*)(\d+)$", refs[i])
        j = i
        if m:
            while j + 1 < len(refs):
                n = re.match(r"^([A-Za-z_]*)(\d+)$", refs[j + 1])
                if not n or n.group(1) != m.group(1) or int(n.group(2)) != int(re.search(r"\d+$", refs[j]).group()) + 1:
                    break
                j += 1
        out.append(f"{refs[i]}&ndash;{refs[j]}" if j > i + 1 else ", ".join(refs[i:j + 1]))
        i = j + 1
    return ", ".join(out)


def overview_panel(site, board, built):
    lay, m3, sch = built.get("layers"), built.get("3d"), built.get("sch")
    s = board.source
    rows = []
    if s["kind"] == "git":
        rows.append(("Source", f'<code>{esc(s["pcb"])}</code>' +
                     (f'<br><code>{esc(s["sch"])}</code>' if s.get("sch") else "") +
                     f'<small>at <b>{esc(s["ref"])}</b> &rarr; {esc(s["commit"])}, {esc(s["date"])}: '
                     f'&ldquo;{esc(s["subject"])}&rdquo;</small>'))
    else:
        rows.append(("Source", f'<code>{esc(s["pcb"])}</code>' +
                     (f'<br><code>{esc(s["sch"])}</code>' if s.get("sch") else "")))
    if lay:
        w, h = lay["size_mm"]
        size = (f'&Oslash;{lay["round"]["dia"]:g} mm' if lay.get("round")
                else f"{w:g} &times; {h:g} mm")
        rows.append(("Board", size + f'<small>{len([l for l in lay["layers"] if l["kind"] == "copper"])} copper layers'
                     + (f', {lay["thickness"]:g} mm thick' if lay.get("thickness") else "") + "</small>"))
        rows.append(("Parts", f'{lay["footprints"]} footprints'
                     f'<small>{lay["front"]} on the {esc(board.front.lower())}, {lay["back"]} on the {esc(board.back.lower())}</small>'))
        rows.append(("Copper", f'{lay["nets"]} nets, {lay["tracks"]:,} track segments, {lay["vias"]:,} vias'
                     f'<small>{lay["length_mm"]:,.0f} mm of routed track</small>'))
    if sch:
        rows.append(("Schematic", f'{len(sch["sheets"])} sheet{"s" * (len(sch["sheets"]) != 1)}'
                     f'<small>{esc(", ".join(x["paper"] for x in sch["sheets"] if x["paper"]))}</small>'))
    if m3:
        note = f'{m3["modelled"]} of {m3["parts"]} parts drawn from a model'
        sub = []
        how = m3.get("resolved_how", {})
        if how.get("legacy"):
            sub.append(f'{how["legacy"]} through an older KiCad&rsquo;s library variable '
                       f'(<code>${{KICAD6_3DMODEL_DIR}}</code> and so on), read as KiCad 9&rsquo;s library')
        if how.get("found"):
            sub.append(f'{how["found"]} through a STEP of the same name found in the project')
        if m3["substituted"]:
            sub.append(f'{span(m3["substituted"])} with a stand-in from the project file')
        if m3.get("flattened"):
            sub.append(f'{span(m3["flattened"])}: a STEP assembly the exporter meshes as nothing, flattened first')
        if m3["no_model"]:
            sub.append(f'{len(m3["no_model"])} name no model ({span(m3["no_model"])})')
        rows.append(("3D", note + "".join(f"<small>{x}</small>" for x in sub)))
        if m3.get("boxes"):
            rows.append(("Drawn as boxes", "".join(
                f'<span class="warn">{span(g["refs"])}</span><small><code>{esc(g["model"])}</code>: '
                f'{esc(g["why"])}</small>' for g in m3["boxes"]) +
                '<small>a box the size of the courtyard stands in for each; a <code>[models]</code> '
                'entry in the project file can name a stand-in model</small>'))
    dl = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
    note = f'<p class="pv-lede">{esc(board.note)}</p>' if board.note else ""
    cmd = (f"python3 -m pcbview build {esc(site.config.name)}" if site.config else
           f"python3 -m pcbview quick {esc(board.pcb.name)}")
    return f"""
  <div class="vw-panel pv-over" id="overview" role="tabpanel" hidden>
    <div class="pv-card">
      <h1>{esc(board.name)}</h1>
      {note}
      <dl class="pv-facts">{dl}</dl>
      <p class="cu-hint">Built {esc(built["when"])} by pcbview from the KiCad files above:
      <code>{cmd}</code>. Nothing here is drawn by hand &mdash; the layer plots, the model and the
      sheets are KiCad&rsquo;s own exports.</p>
    </div>
  </div>"""


def board_page(site, board, built, sheets):
    tabs = []
    if board.sch:
        tabs.append('<button role="tab" data-tab="schematic" aria-controls="schematic" aria-selected="false">SCH</button>')
    tabs.append('<button role="tab" data-tab="copper" aria-controls="copper" aria-selected="true">PCB</button>')
    tabs.append('<button role="tab" data-tab="board3d" aria-controls="board3d" aria-selected="false">3D</button>')
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{esc(site.title)} &mdash; {esc(board.name)}</title>
<link rel="stylesheet" href="assets/pcbview.css">
<script src="assets/parts.js" defer></script>
<script src="assets/app.js" defer></script>
<script src="assets/copper.js" defer></script>
<script src="assets/sch.js" defer></script>
<script type="importmap">{{"imports": {{"three": "./assets/vendor/three.module.js"}}}}</script>
<script type="module" src="assets/board3d.js"></script>
</head>
<body>
{top(site, board)}
{side(site, board, sheets)}
<div class="a3-bar">
  <span class="a3-crumb">{icon("pcb")}<b>{esc(board.name)}</b><span class="sep">/</span><span id="pv-view">PCB</span></span>
  <span class="a3-sp"></span>
  <span class="a3-page">{source_line(board)}</span>
</div>
<main class="pv-main">
<section class="vw" aria-label="{esc(board.name)}: schematic, PCB and 3D">
  <div class="vw-bar">
    <div class="vw-left"><button data-vw="layers" aria-pressed="true"
      title="show or hide the side panel">{icon("layers")}<span>Panel</span></button></div>
    <div class="vw-tabs" role="tablist" aria-label="view">{"".join(tabs)}</div>
    <div class="vw-tools">
      <button data-vw="info" title="about this board" aria-label="about this board">{icon("info")}</button>
      <button data-vw="full" title="full screen" aria-label="full screen">{icon("full")}</button>
    </div>
  </div>
  {schematic_panel(board, sheets.get(board.id))}
  {copper_panel(board)}
  {board3d_panel(board)}
  {overview_panel(site, board, built)}
</section>
</main>
</body>
</html>
"""


def list_page(site, built):
    cards = []
    for b in site.boards:
        lay = built[b.id].get("layers") or {}
        w, h = lay.get("size_mm", [0, 0])
        facts = (f'{w:g} &times; {h:g} mm &middot; {len([l for l in lay.get("layers", []) if l["kind"] == "copper"])} layers'
                 f' &middot; {lay.get("footprints", 0)} parts') if lay else ""
        cards.append(f"""<a class="pv-board" href="{b.id}.html">
  <div class="pv-thumb"><img src="{b.id}/layers/body.svg" alt=""><img src="{b.id}/layers/f.svg" alt="">
  <img src="{b.id}/layers/edge.svg" alt=""></div>
  <b>{esc(b.name)}</b><span>{facts}</span><span>{source_line(b)}</span>
</a>""")
    first = site.boards[0]
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{esc(site.title)}</title>
<link rel="stylesheet" href="assets/pcbview.css">
<script src="assets/app.js" defer></script>
</head>
<body class="pv-list">
{top(site, None)}
{side(site, None, {})}
<main class="pv-main pv-boards">
{"".join(cards)}
</main>
</body>
</html>
"""


# ------------------------------------------------------------------- build --
def copy_assets(out):
    dest = out / "assets"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(WEB, dest, ignore=shutil.ignore_patterns("*.md", ".*"))


def write(site, built):
    """Every page, from what the stages returned: built[board id] =
    {"layers": meta, "3d": meta, "sch": index, "when": ...}."""
    out = site.out
    copy_assets(out)
    sheets = {b.id: (built[b.id].get("sch") or {}).get("sheets", []) for b in site.boards}
    for b in site.boards:
        (out / page_name(site, b)).write_text(board_page(site, b, built[b.id], sheets))
    if len(site.boards) > 1:
        (out / "index.html").write_text(list_page(site, built))
    info = {"title": site.title, "built": datetime.now().isoformat(timespec="seconds"),
            "config": str(site.config) if site.config else None,
            "boards": [{"id": b.id, "name": b.name, "page": page_name(site, b),
                        "source": b.source} for b in site.boards]}
    (out / "site.json").write_text(json.dumps(info, indent=1) + "\n")
