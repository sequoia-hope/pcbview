"""pcbview command line.

    python3 -m pcbview build pcbview.toml            # every board, every tab
    python3 -m pcbview build pcbview.toml --only 3d --board rev-a
    python3 -m pcbview quick board.kicad_pcb -o site/   # no config file
    python3 -m pcbview models pcbview.toml           # where each 3D model resolves
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from . import config, layers, model3d, schematic, site as pages
from .board import Board
from .models import Resolver
from .util import natural

STAGES = ("sch", "layers", "3d")


def build(site, only=None, boards=None, parts_only=False):
    only = set(only or STAGES)
    site.out.mkdir(parents=True, exist_ok=True)
    built = {}
    for b in site.boards:
        out = site.out / b.id
        old = out / "built.json"
        prev = json.loads(old.read_text()) if old.exists() else {}
        if boards and b.id not in boards:
            built[b.id] = prev
            continue
        print(f"{b.name} ({b.id}): {b.pcb.name}" +
              (f" at {b.source['ref']} ({b.source['commit']})" if b.source["kind"] == "git" else ""))
        out.mkdir(parents=True, exist_ok=True)
        rec = dict(prev)
        t = time.time()
        if "sch" in only:
            rec["sch"] = schematic.run(site, b, out)
        if "layers" in only:
            rec["layers"] = layers.run(site, b, out)
        if "3d" in only:
            got = model3d.run(site, b, out, parts_only=parts_only)
            if got:
                rec["3d"] = got
        rec["when"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        rec["source"] = b.source
        old.write_text(json.dumps(rec, indent=1) + "\n")
        built[b.id] = rec
        print(f"  done in {time.time() - t:.0f} s")
    pages.write(site, built)
    print(f"site: {site.out / 'index.html'}")


def models_report(target, board_ids=None):
    """Where every footprint's 3D model resolves: for a .kicad_pcb, or for
    the boards of a pcbview.toml (with its stand-ins and git sources)."""
    from . import sexp
    if str(target).endswith(".toml"):
        site = config.load(target)
        jobs = [(b.pcb, b.project_dir, site.models, b.id) for b in site.boards
                if not board_ids or b.id in board_ids]
    else:
        pcb = Path(target).resolve()
        jobs = [(pcb, pcb.parent, {}, pcb.stem)]
    for pcb, project, subst, name in jobs:
        board = Board(pcb)
        r = Resolver(project, subst)
        seen = {}
        for fp in board.footprints():
            ref = next((sexp.unq(p[2]) for p in sexp.findall(fp, "property")
                        if sexp.unq(p[1]) == "Reference"), "?")
            for m in sexp.findall(fp, "model"):
                seen.setdefault(sexp.unq(m[1]), []).append(ref)
        print(f"{name}: {len(seen)} model paths")
        order = {"missing": 0, "substituted": 1, "found": 2, "resolved": 3, "ok": 4}
        for raw in sorted(seen, key=lambda k: (order[r.resolve(k)["status"]], k)):
            res = r.resolve(raw)
            refs = sorted(seen[raw], key=natural)
            print("  %-11s %s\n              %s  -> %s%s" % (
                res["status"], raw, " ".join(refs[:10]) + (" ..." if len(refs) > 10 else ""),
                res["path"] if res["status"] != "missing" else "(nothing)",
                ("  [" + res["why"] + "]") if res.get("why") else ""))


def main():
    ap = argparse.ArgumentParser(prog="pcbview", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build the site a pcbview.toml describes")
    b.add_argument("config")
    b.add_argument("--only", help="comma-separated stages: " + ",".join(STAGES))
    b.add_argument("--board", help="comma-separated board ids (default: all)")
    b.add_argument("--parts-only", action="store_true",
                   help="with the 3d stage: rewrite parts.json, not the model")
    q = sub.add_parser("quick", help="one board, no config file")
    q.add_argument("pcb")
    q.add_argument("--sch")
    q.add_argument("-o", "--out")
    q.add_argument("--title")
    m = sub.add_parser("models", help="where each footprint's 3D model resolves")
    m.add_argument("target", help="a .kicad_pcb, or a pcbview.toml (its boards and stand-ins)")
    m.add_argument("--board", help="comma-separated board ids, with a pcbview.toml")
    a = ap.parse_args()

    if a.cmd == "build":
        site = config.load(a.config)
        only = a.only.split(",") if a.only else None
        if only and set(only) - set(STAGES):
            sys.exit("unknown stage: " + ", ".join(set(only) - set(STAGES)))
        build(site, only, a.board.split(",") if a.board else None, a.parts_only)
    elif a.cmd == "quick":
        build(config.quick(a.pcb, a.sch, a.out, a.title))
    elif a.cmd == "models":
        models_report(a.target, a.board.split(",") if a.board else None)


if __name__ == "__main__":
    main()
