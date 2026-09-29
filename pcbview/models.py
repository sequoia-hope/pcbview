"""models.py -- find the STEP file behind every footprint's 3D model.

kicad-cli's GLB exporter reads a model path as the board file writes it, and a
board that has lived through a few KiCad versions writes them several ways:
`${KICAD6_3DMODEL_DIR}` and `${KICAD8_3DMODEL_DIR}` for the same library,
`.wrl` files the exporter cannot mesh, `${KIPRJMOD}` that means the board's
own directory (which a scratch copy is not), an absolute path into a sibling
project, a variable the converter that made the footprint invented. What it
cannot resolve it drops, with one line naming the part.

So every path is resolved here first, in order:

  1. a substitution from the project file ([models] "tail" = {path, offset,
     rotate}) -- a stand-in where the footprint names nothing usable;
  2. the path with its variables expanded: KIPRJMOD is the board's directory
     in the working tree, every KICADn_3DMODEL_DIR (and KISYS3DMOD) is the
     KiCad 9 library, anything else comes from KiCad's own settings or the
     environment; a relative path is relative to the board;
  3. a STEP beside it with the same name, for a .wrl;
  4. a STEP of the same name anywhere under the board's directory.

and the board copy the exporter reads carries the absolute path. What is still
missing is reported, with the reason.
"""
import json
import os
import re
from pathlib import Path

STEP = (".step", ".stp", ".stpz", ".iges", ".igs")
VAR = re.compile(r"\$\{([^}]+)\}|\$\(([^)]+)\)")
LIBVAR = re.compile(r"^(KICAD\d*_3DMODEL_DIR|KISYS3DMOD)$")

# a model block, up to the paren closing it: the one at its own indentation
# (a lazy match to the first ")" would stop inside the nested offset block)
MODEL_BLOCK = re.compile(r'^([ \t]*)\(model "([^"]*)"(.*?)\n\1\)', re.S | re.M)


def kicad_library():
    """Where KiCad 9's 3D model library is installed."""
    env = os.environ.get("KICAD9_3DMODEL_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    for d in ("/usr/share/kicad/3dmodels", "/usr/local/share/kicad/3dmodels",
              "/app/share/kicad/3dmodels", "~/.local/share/kicad/9.0/3dmodels",
              "/Applications/KiCad/KiCad.app/Contents/SharedSupport/3dmodels",
              "C:/Program Files/KiCad/9.0/share/kicad/3dmodels"):
        p = Path(d).expanduser()
        if p.is_dir():
            return p
    return None


def user_vars():
    """The path variables set in KiCad's own preferences."""
    for v in ("9.0", "8.0"):
        f = Path(f"~/.config/kicad/{v}/kicad_common.json").expanduser()
        if f.exists():
            try:
                return json.loads(f.read_text()).get("environment", {}).get("vars") or {}
            except (ValueError, AttributeError):
                return {}
    return {}


class Resolver:
    def __init__(self, project_dir, subst=None):
        self.project = Path(project_dir)
        self.lib = kicad_library()
        self.subst = subst or {}
        self.vars = {**user_vars(), "KIPRJMOD": str(self.project)}
        self._index = None

    def expand(self, raw):
        """(path, names of the variables it used that are unknown, names of
        older KiCad's library variables it used)"""
        unknown, legacy = [], []

        def one(m):
            name = m.group(1) or m.group(2)
            if name == "KIPRJMOD":
                return self.vars["KIPRJMOD"]
            if LIBVAR.match(name) and self.lib:
                if name != "KICAD9_3DMODEL_DIR":
                    legacy.append(name)
                return str(self.lib)
            v = self.vars.get(name) or os.environ.get(name)
            if v:
                return v
            unknown.append(name)
            return m.group(0)
        return VAR.sub(one, raw), unknown, legacy

    def by_name(self, stem):
        """STEP files under the board's directory, by lower-case stem."""
        if self._index is None:
            self._index = {}
            for f in self.project.rglob("*"):
                if f.suffix.lower() in STEP and f.is_file():
                    self._index.setdefault(f.stem.lower(), f)
        return self._index.get(stem.lower())

    def resolve(self, raw):
        """What to hand the exporter for this model path:
        {status, path, offset, rotate, why}; status is one of
        ok, resolved, found, substituted, missing."""
        for tail, (target, offset, rotate) in self.subst.items():
            if raw.endswith(tail):
                t = self.expand(target)[0]
                return {"status": "substituted", "path": t, "offset": offset,
                        "rotate": rotate, "why": "stand-in from the project file",
                        "exists": Path(t).exists()}
        path, unknown, legacy = self.expand(raw)
        p = Path(path)
        if not p.is_absolute():
            p = self.project / p
        # KiCad itself swaps a .wrl for the STEP beside it (--subst-models);
        # an older KiCad's library variable is pcbview's doing
        status = "resolved" if legacy else "ok"
        why = ("${%s} read as KiCad 9's library" % legacy[0]) if legacy else ""
        if p.suffix.lower() in STEP and p.is_file():
            return {"status": status, "path": str(p), "why": why}
        for ext in (".step", ".stp", ".STEP", ".STP"):
            q = p.with_suffix(ext)
            if q.is_file():
                return {"status": status, "path": str(q), "why": why or "the STEP beside the .wrl"}
        q = self.by_name(p.stem)
        if q:
            return {"status": "found", "path": str(q),
                    "why": "a STEP of the same name in the project"}
        why = ("unknown variable " + ", ".join("${%s}" % u for u in unknown)) if unknown \
            else ("only a %s, which the exporter cannot mesh" % p.suffix) if p.exists() \
            else "no such file"
        if not unknown and not p.exists():
            for alt in (p.with_suffix(".wrl"), self.project / "parts" / (p.stem + ".wrl")):
                if alt.exists():
                    why = "only a .wrl, which the exporter cannot mesh"
        return {"status": "missing", "path": raw, "why": why}


def rewrite_models(text, resolver):
    """The board text with every model path made absolute and every
    substitution applied; returns (text, {model path as written: resolution})."""
    seen = {}

    def one(m):
        ind, raw, body = m.group(1), m.group(2), m.group(3)
        r = seen.get(raw) or resolver.resolve(raw)
        seen[raw] = r
        if r["status"] == "missing":
            return m.group(0)
        if r.get("offset") is not None:
            body = re.sub(r"\(offset\s*\(xyz [^)]*\)\s*\)", "(offset (xyz %g %g %g))" % r["offset"], body)
        if r.get("rotate") is not None:
            body = re.sub(r"\(rotate\s*\(xyz [^)]*\)\s*\)", "(rotate (xyz %g %g %g))" % r["rotate"], body)
        if r["status"] == "substituted":
            body = re.sub(r"\(scale\s*\(xyz [^)]*\)\s*\)", "(scale (xyz 1 1 1))", body)
        return '%s(model "%s"%s\n%s)' % (ind, r["path"].replace("\\", "/"), body, ind)

    text = MODEL_BLOCK.sub(one, text)
    return text, seen
