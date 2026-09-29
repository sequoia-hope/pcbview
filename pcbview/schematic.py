"""schematic.py -- the SCH tab: every sheet as KiCad plots it.

Writes into <site>/<board>/sch/:

    <root>.svg, <root>-<sheet>.svg ...   one SVG per sheet instance, as
                                         `kicad-cli sch export svg` names them
    <root>.pdf                           the whole schematic, for printing
    sheets.json                          the sheet list: title, what is on it,
                                         paper, page number, file

The hierarchy is read from the files themselves: each (sheet ...) block names
its instance and its file, and KiCad names an instance's plot after the path
of sheet names down to it. A sheet used twice is plotted twice, once per
instance, as KiCad numbers its pages.
"""
import json
import re
from pathlib import Path

from . import sexp
from .config import scratch
from .util import need_kicad, sh, shrink


def prop(node, *names):
    for p in sexp.findall(node, "property"):
        if sexp.unq(p[1]) in names:
            return sexp.unq(p[2])
    return ""


def read(path, cache={}):
    if path not in cache:
        cache[path] = sexp.parse(path.read_text())
    return cache[path]


def page_no(sheet_node):
    """The page number KiCad gave this instance, where it recorded one."""
    for pg in sexp.walk(sheet_node, "page"):
        v = sexp.unq(pg[1]) if len(pg) > 1 else ""
        if v:
            return v
    return ""


def hierarchy(root):
    """[(names down to it, file, page)] for every sheet instance, root first."""
    out = [((), root, "1")]

    def walk(path, names):
        tree = read(path)
        for s in sexp.findall(tree, "sheet"):
            name = prop(s, "Sheetname", "Sheet name")
            file = prop(s, "Sheetfile", "Sheet file")
            if not file:
                continue
            f = (path.parent / file).resolve()
            if not f.exists():
                print(f"  ! sheet {name!r} names {file}, which is not there")
                continue
            here = names + (name,)
            out.append((here, f, page_no(s)))
            walk(f, here)
    walk(root, ())
    return out


def describe(path):
    """(title, comment, symbols, paper) from a sheet file."""
    tree = read(path)
    tb = sexp.find(tree, "title_block") or []
    title = comment = ""
    for n in tb[1:]:
        if isinstance(n, list) and n[0] == "title":
            title = sexp.unq(n[1])
        if isinstance(n, list) and n[0] == "comment" and not comment:
            comment = sexp.unq(n[-1])
    symbols = [s for s in sexp.findall(tree, "symbol")
               if not prop(s, "Reference").startswith("#")]
    paper = sexp.find(tree, "paper")
    return title, comment, len(symbols), (sexp.unq(paper[1]) if paper else "")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def run(site, board, out):
    dest = out / "sch"
    if not board.sch:
        return None
    need_kicad()
    dest.mkdir(parents=True, exist_ok=True)
    for old in list(dest.glob("*.svg")) + list(dest.glob("*.pdf")):
        old.unlink()
    root = board.sch
    tmp = scratch("pcbview_sch_")
    # with the frame and title block: a sheet is read as a drawing
    sh(["kicad-cli", "sch", "export", "svg", "-o", tmp, root], cwd=root.parent)
    plotted = {f.name: f for f in tmp.glob("*.svg")}
    # KiCad sanitises names for the file system: match on letters and digits
    by_slug = {slug(n[:-4]): n for n in plotted}

    sheets, used = [], set()
    for names, f, page in hierarchy(root):
        want = "-".join((root.stem,) + names) + ".svg"
        name = want if want in plotted else by_slug.get(slug(want[:-4]))
        if not name or name in used:
            print(f"  ! no plot for sheet {'/'.join(names) or 'root'} (looked for {want})")
            continue
        used.add(name)
        text = plotted[name].read_text()
        w = re.search(r'width="([\d.]+)mm"', text)
        h = re.search(r'height="([\d.]+)mm"', text)
        (dest / name).write_text(shrink(text))
        title, comment, count, paper = describe(f)
        label = names[-1] if names else (title or root.stem)
        desc = comment or (title if names and title and title != label else "")
        desc = (desc + " · " if desc else "") + f"{count} symbols"
        sheets.append({"sheet": "/".join(names) or "root", "title": label, "desc": desc,
                       "file": name, "source": f.name, "paper": paper, "page": page,
                       "size_mm": [float(w.group(1)), float(h.group(1))] if w and h else None})
    for name in sorted(set(plotted) - used):          # anything the walk did not name
        (dest / name).write_text(shrink(plotted[name].read_text()))
        sheets.append({"sheet": name[:-4], "title": name[:-4], "desc": "", "file": name,
                       "source": "", "paper": "", "page": "", "size_mm": None})

    def order(s):
        try:
            return (0, float(s["page"]))
        except ValueError:
            return (1, 0)
    sheets.sort(key=order)

    pdf = dest / f"{root.stem}.pdf"
    sh(["kicad-cli", "sch", "export", "pdf", "-o", pdf, root], cwd=root.parent, check=False)
    index = {"schematic": root.name, "pdf": pdf.name if pdf.exists() else None, "sheets": sheets}
    (dest / "sheets.json").write_text(json.dumps(index, indent=1) + "\n")
    size = sum((dest / s["file"]).stat().st_size for s in sheets)
    print("  schematic   %d sheet%s, %.1f MB of SVG%s" % (len(sheets), "s" * (len(sheets) != 1),
          size / 1e6, ", PDF" if index["pdf"] else ""))
    return index
