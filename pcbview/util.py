"""Small helpers every stage uses: running kicad-cli, ordering references,
and trimming the SVG KiCad writes."""
import re
import shutil
import subprocess
import sys
from pathlib import Path

NUM = r"-?\d+(?:\.\d+)?"


def sh(cmd, cwd=None, check=True):
    """Run a command, capturing its output; exit with both streams on failure."""
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, cwd=cwd)
    if check and r.returncode:
        sys.exit("FAILED: %s\n%s%s" % (" ".join(map(str, cmd)), r.stdout, r.stderr))
    return r


def need_kicad():
    if not shutil.which("kicad-cli"):
        sys.exit("kicad-cli is not on the PATH: pcbview drives KiCad 9's own exporters")


def natural(ref):
    """Sort key for references: C2 before C10, and U7 after TP9."""
    m = re.match(r"([A-Za-z_#]*)(\d*)(.*)", ref)
    return (m.group(1), int(m.group(2) or 0), m.group(3))


def svg_body(text):
    """The drawing, without the xml preamble, the doctype, the <svg> element
    itself and the exporter's title and desc (which carry a timestamp)."""
    i = text.index(">", text.index("<svg")) + 1
    t = text[i:text.rindex("</svg>")]
    return re.sub(r"<(title|desc)>.*?</\1>", "", t, flags=re.S)


def shrink(text):
    """Three decimals is a micron; the exporter writes four and a lot of air."""
    text = re.sub(r"(\d+\.\d{4,})",
                  lambda m: ("%.3f" % float(m.group(1))).rstrip("0").rstrip("."), text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return re.sub(r"\n{2,}", "\n", text).strip() + "\n"


def page_of(text):
    """(width mm, height mm) of an exported svg."""
    w = re.search(r'width="(%s)mm"' % NUM, text)
    h = re.search(r'height="(%s)mm"' % NUM, text)
    if not (w and h):
        sys.exit("cannot read the page size out of a KiCad svg export")
    return float(w.group(1)), float(h.group(1))


def drawn(text):
    """Whether an exported svg draws anything at all."""
    return bool(re.search(r"<(path|circle|polyline|polygon|rect|text|line)\b", svg_body(text)))


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def rel(path, base):
    """path relative to base where it can be, else as given."""
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def shown(path, base):
    """A path as a page may print it: relative to the project (`base`) where
    it is inside it, from ~ where it is under the home directory -- a site is
    often published, and the machine's own layout is no business of it."""
    path = Path(path)
    for root, pre in ((Path(base), ""), (Path.home(), "~/")):
        try:
            return pre + path.relative_to(root).as_posix()
        except ValueError:
            pass
    return str(path)
