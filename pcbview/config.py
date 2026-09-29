"""The project file: which boards, where their files come from, and what the
pages say about them.

    title = "RP2350 Motor Controller"
    out = "viewer"                       # the site, relative to this file
    back = { href = "../index.html", label = "Review hub" }

    [[boards]]
    id = "rev-a"
    name = "Rev A"
    git = "rev-A-fab"                    # read the files at this ref
    pcb = "hardware/rp2350_driver.kicad_pcb"
    sch = "hardware/rp2350_driver.kicad_sch"

A board's files are read either from disk (paths relative to this file) or,
with `git`, out of the repository at that ref -- a tag pins the page to what
was built, where a working-tree copy drifts. They are written to a scratch
directory for kicad-cli; `${KIPRJMOD}` in a model path still means the board's
directory in the working tree, where the project keeps its models.

See README.md for every key.
"""
import atexit
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .util import shown

# the files a board's directory may need besides the board and its sheets:
# the project (text variables, rules), a drawing sheet, the design rules
BESIDE = (".kicad_pro", ".kicad_wks", ".kicad_dru")


@dataclass
class Board:
    id: str
    name: str
    pcb: Path                    # the file kicad-cli reads (maybe a scratch copy)
    sch: Path | None
    project_dir: Path            # what ${KIPRJMOD} means for its 3D models
    source: dict                 # where it came from, for the page
    note: str = ""
    front: str = "Front"         # what the two faces are called
    back: str = "Back"
    front_note: str = ""         # e.g. "the outward face"
    back_note: str = ""
    polar: bool | None = None    # radius/angle readout; None = only if round
    embed: Path | None = None    # a page of the project's own to write the viewer into
    sheets: dict = field(default_factory=dict)       # sheet path -> {title, desc}


@dataclass
class Site:
    base: Path                   # the config file's directory
    title: str
    out: Path
    boards: list
    subtitle: str = ""
    back_href: str = ""
    back_label: str = ""
    repo: str = ""
    models: dict = field(default_factory=dict)       # tail -> (path, offset, rotate)
    role_field: str = ""
    lcsc_csv: Path | None = None
    bom: list = field(default_factory=list)
    layer_notes: dict = field(default_factory=dict)
    config: Path | None = None


def _git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if r.returncode:
        sys.exit("git %s failed in %s:\n%s" % (" ".join(args), repo, r.stderr.decode()))
    return r.stdout


_SCRATCH = []


def scratch(prefix):
    d = Path(tempfile.mkdtemp(prefix=prefix))
    _SCRATCH.append(d)
    return d


@atexit.register
def _clean():
    for d in _SCRATCH:
        shutil.rmtree(d, ignore_errors=True)


def from_git(repo, ref, pcb, sch):
    """Write the board, its sheets and the project files beside them, as they
    are at `ref`, into a scratch copy of the same directory layout. Returns
    (pcb, sch, source, the repository's top directory)."""
    repo = Path(_git(repo, "rev-parse", "--show-toplevel").decode().strip())
    commit = _git(repo, "rev-parse", ref + "^{commit}").decode().strip()
    when, subject = _git(repo, "log", "-1", "--format=%cs%x00%s", commit).decode().strip().split("\0", 1)
    root = scratch("pcbview_git_")
    want = {pcb} | ({sch} if sch else set())
    dirs = {str(Path(p).parent) for p in want}
    for d in dirs:
        listing = _git(repo, "ls-tree", "--name-only", commit, *([d + "/"] if d != "." else []))
        for name in listing.decode().splitlines():
            if name.endswith(".kicad_sch") or name.endswith(BESIDE) or name in want:
                want.add(name)
    for name in sorted(want):
        dst = root / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(_git(repo, "show", "%s:%s" % (commit, name)))
    src = {"kind": "git", "repo": str(repo), "ref": ref, "commit": commit[:7],
           "date": when, "subject": subject, "pcb": pcb, "sch": sch}
    return root / pcb, (root / sch if sch else None), src, repo


def load(path):
    path = Path(path).resolve()
    raw = tomllib.loads(path.read_text())
    base = path.parent

    def p(v):
        return (base / Path(v).expanduser()).resolve() if v else None

    boards = []
    for b in raw.get("boards", []):
        bid = b["id"]
        if "git" in b:
            repo = p(b.get("repo", "."))
            pcb, sch, src, top = from_git(repo, b["git"], b["pcb"], b.get("sch"))
            src["repo"] = shown(top, base) if top != base else "."
            project = (top / b["pcb"]).parent
        else:
            pcb, sch = p(b["pcb"]), p(b.get("sch"))
            project = pcb.parent
            src = {"kind": "file", "pcb": shown(pcb, base), "sch": shown(sch, base) if sch else None}
        if not pcb.exists():
            sys.exit(f"board {bid}: no board file at {pcb}")
        if sch and not sch.exists():
            sys.exit(f"board {bid}: no schematic at {sch}")
        boards.append(Board(
            id=bid, name=b.get("name", bid), pcb=pcb, sch=sch,
            project_dir=p(b["project_dir"]) if "project_dir" in b else project,
            source=src, note=b.get("note", ""),
            front=b.get("front", "Front"), back=b.get("back", "Back"),
            front_note=b.get("front_note", ""), back_note=b.get("back_note", ""),
            polar=b.get("polar"), embed=p(b.get("embed")), sheets=b.get("sheets", {})))
    if not boards:
        sys.exit(f"{path}: no [[boards]]")
    if len({b.id for b in boards}) != len(boards):
        sys.exit(f"{path}: two boards share an id")

    models = {}
    for tail, m in raw.get("models", {}).items():
        target = m["path"]
        if not target.startswith("${"):
            target = str(p(target))
        models[tail] = (target, tuple(m["offset"]) if "offset" in m else None,
                        tuple(m["rotate"]) if "rotate" in m else None)

    parts = raw.get("parts", {})
    back = raw.get("back", {})
    return Site(
        base=base, config=path, title=raw.get("title", boards[0].name),
        subtitle=raw.get("subtitle", ""), out=p(raw.get("out", "pcbview-site")),
        boards=boards, back_href=back.get("href", ""), back_label=back.get("label", ""),
        repo=raw.get("repo", ""), models=models,
        role_field=parts.get("role_field", ""),
        lcsc_csv=p(parts.get("lcsc_csv")),
        bom=[p(x) for x in parts.get("bom", [])],
        layer_notes=raw.get("layers", {}).get("notes", {}))


def quick(pcb, sch=None, out=None, title=None):
    """A one-board site without a config file."""
    pcb = Path(pcb).resolve()
    if sch is None:
        guess = pcb.with_suffix(".kicad_sch")
        sch = guess if guess.exists() else None
    sch = Path(sch).resolve() if sch else None
    board = Board(id=pcb.stem, name=title or pcb.stem, pcb=pcb, sch=sch,
                  project_dir=pcb.parent,
                  source={"kind": "file", "pcb": shown(pcb, Path.cwd()),
                          "sch": shown(sch, Path.cwd()) if sch else None})
    return Site(base=Path.cwd(), title=title or pcb.stem,
                out=Path(out or (pcb.parent / "pcbview-site")).resolve(), boards=[board])
