"""model3d.py -- the 3D tab: the board as a GLB, and what the part pane says.

Writes into <site>/<board>/3d/:

    board.glb      the board, its copper and every part that has a model
    board.json     what the caption says: part counts, which parts have no
                   model, which were substituted or found, file size
    parts.json     what the part pane says about a clicked part: its role,
                   LCSC number, footprint and its description, position and
                   rotation, attributes, other fields, and the net on every
                   pin. Read from the board file alone.

`kicad-cli pcb export glb` does the work, from a scratch copy of the board in
which every model path has been resolved to a STEP file that exists (see
models.py) -- the board file itself is never touched. What comes out of
kicad-cli is then rewritten, because as exported it is 25-30 MB and some
30,000 primitives, one per track, pad and via, each its own draw call:

  * primitives are merged per mesh and material, in chunks that keep 16-bit
    indices;
  * positions are quantized to 16 bits per axis over each mesh's box
    (KHR_mesh_quantization, which three.js reads natively), the dequantizing
    scale on a child node;
  * the board's own meshes (copper, pads, vias, laminate, mask, silk) lose
    their normals and are welded: they are all flat faces, and a glTF
    without normals is drawn flat-shaded by definition. Parts keep theirs,
    as 8-bit normals, because a capacitor can is round;
  * the board's colours, which KiCad writes as sRGB where glTF means linear,
    are converted (the parts' come through OCC already linear and are left
    alone), and the mask is made translucent the way pcbnew draws it.

Each part's node carries its reference, value, footprint and side in its
extras, which is what the viewer shows when a part is hovered. The result is
the same bytes for the same board: kicad-cli's timestamp is dropped.
"""
import csv
import json
import re
import struct
import sys
from datetime import date
from pathlib import Path

import numpy as np

from . import sexp
from .board import Board
from .config import scratch
from .models import Resolver, rewrite_models
from .util import natural, need_kicad, sh

GLB_FLAGS = ["--force", "--subst-models", "--include-tracks", "--include-pads",
             "--include-zones", "--include-silkscreen", "--include-soldermask"]

# fields every footprint has, shown in their own rows rather than as parameters
OWN_FIELDS = {"Reference", "Value", "Footprint", "Datasheet", "Description"}
# where a footprint may keep its LCSC / JLCPCB part number
LCSC_FIELDS = ("LCSC", "LCSC Part", "LCSC Part #", "LCSC#", "LCSC_PN", "JLCPCB Part #",
               "JLCPCB Part", "JLC", "JLCPCB", "LCSC Part Number")
MPN_FIELDS = ("MPN", "Manufacturer Part Number", "MFR.Part #", "Part Number", "PN")


# ---------------------------------------------------------------- parts ----
def props_of(fp):
    return {sexp.unq(p[1]): sexp.unq(p[2]) for p in sexp.findall(fp, "property")}


def side_of(fp):
    return "back" if sexp.unq(sexp.find(fp, "layer")[1]) == "B.Cu" else "front"


def parts_of(pcb):
    """ref -> {value, footprint, side, models} for every footprint."""
    parts = {}
    for fp in pcb.footprints():
        props = props_of(fp)
        parts[props.get("Reference", "?")] = {
            "value": props.get("Value", ""),
            "footprint": sexp.unq(fp[1]).split(":")[-1],
            "side": side_of(fp),
            "models": [sexp.unq(m[1]) for m in sexp.findall(fp, "model")],
        }
    return parts


def lcsc_sources(site):
    """(by (value, footprint), by designator) from the project's tables:
    a value,footprint,lcsc,mpn csv, and JLCPCB-style BOMs (Designator + LCSC)."""
    by_vf, by_ref = {}, {}
    if site.lcsc_csv and site.lcsc_csv.exists():
        rows = csv.DictReader(l for l in site.lcsc_csv.read_text().splitlines()
                              if l.strip() and not l.startswith("#"))
        for r in rows:
            by_vf[(r["value"], r["footprint"])] = {"lcsc": r["lcsc"], "mpn": r.get("mpn", "")}
    for bom in site.bom:
        if not bom.exists():
            print(f"  ! no BOM at {bom}")
            continue
        text = bom.read_text(encoding="utf-8-sig")
        for r in csv.DictReader(text.splitlines()):
            keys = {k.strip().lower(): v for k, v in r.items() if k}
            num = next((keys[k] for k in ("lcsc", "lcsc part", "lcsc part #", "jlcpcb part #",
                                          "jlcpcb part", "supplier part") if keys.get(k)), "")
            if not num:
                continue
            for ref in re.split(r"[,\s]+", keys.get("designator", "")):
                if ref:
                    by_ref[ref] = {"lcsc": num.strip(), "mpn": ""}
    return by_vf, by_ref


def plain(descr):
    """A footprint description without its sources. KiCad's library ones carry
    a body-size reference, a URL and "generated with kicad-footprint-generator",
    none of which says what the package is."""
    d = re.sub(r"\s*\((?:Body size|see)[^)]*\)", "", descr)
    d = re.sub(r",?\s*generated (?:with|by) kicad-footprint-generator.*$", "", d)
    d = re.sub(r"(?:,\s*|\s+)?(?:(?:see|datasheet:?)\s*)?https?://[^\s)]+", "", d, flags=re.I)
    d = re.sub(r"\(\s*[,;-]?\s*\)", "", d)
    d = re.sub(r"\(\s*[,;]?\s*", "(", d)
    return re.sub(r"\s{2,}", " ", d).strip(" ,")


def rotated(x, y, deg):
    from math import cos, radians, sin
    a = radians(deg)
    return x * cos(a) + y * sin(a), -x * sin(a) + y * cos(a)


def extent(fp, at, local=False):
    """The footprint's box: its courtyard where it has one, else its pads.
    In board mm (for the PCB tab's hit test), or with `local` in the
    footprint's own unrotated frame (for a stand-in box)."""
    ax, ay, rot = float(at[1]), float(at[2]), float(at[3]) if len(at) > 3 else 0.0
    pts = []
    for kind in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc"):
        for g in sexp.findall(fp, kind):
            ly = sexp.find(g, "layer")
            if not ly or not sexp.unq(ly[1]).endswith("CrtYd"):
                continue
            if kind == "fp_poly":
                pts += [(float(p[1]), float(p[2])) for p in sexp.findall(sexp.find(g, "pts"), "xy")]
            elif kind == "fp_circle":
                (cx, cy), (ex, ey) = (tuple(map(float, sexp.find(g, k)[1:3])) for k in ("center", "end"))
                r = ((ex - cx) ** 2 + (ey - cy) ** 2) ** .5
                pts += [(cx - r, cy - r), (cx + r, cy + r), (cx - r, cy + r), (cx + r, cy - r)]
            else:
                for k in ("start", "end", "mid"):
                    p = sexp.find(g, k)
                    if p:
                        pts.append((float(p[1]), float(p[2])))
                if kind == "fp_rect":
                    (sx, sy), (ex, ey) = pts[-2], pts[-1]
                    pts += [(sx, ey), (ex, sy)]
    if not pts:
        for pad in sexp.findall(fp, "pad"):
            pa, size = sexp.find(pad, "at"), sexp.find(pad, "size")
            if not (pa and size):
                continue
            px, py = float(pa[1]), float(pa[2])
            pr = float(pa[3]) if len(pa) > 3 else 0.0
            w, h = float(size[1]) / 2, float(size[2]) / 2
            for dx, dy in ((-w, -h), (w, -h), (w, h), (-w, h)):
                ddx, ddy = rotated(dx, dy, pr - rot)
                pts.append((px + ddx, py + ddy))
    if not pts:
        return None
    if local:
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)
    # footprint-local, KiCad's clockwise-negative rotation -> board mm
    out = []
    for x, y in pts:
        rx, ry = rotated(x, y, rot)
        out.append((ax + rx, ay + ry))
    xs, ys = [p[0] for p in out], [p[1] for p in out]
    return [round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)]


def details_of(site, board, pcb):
    """ref -> everything the part pane shows, for every footprint."""
    by_vf, by_ref = lcsc_sources(site)
    out = {}
    for fp in pcb.footprints():
        props = props_of(fp)
        ref = props.get("Reference", "?")
        at = sexp.find(fp, "at")
        descr, attr = sexp.find(fp, "descr"), sexp.find(fp, "attr")
        pins = {}
        for pad in sexp.findall(fp, "pad"):
            num = sexp.unq(pad[1])
            if not num:                                  # thermal and paste pads
                continue
            net = sexp.find(pad, "net")
            name = sexp.unq(net[-1]) if net else ""
            if not pins.get(num):
                pins[num] = name
        lib, _, name = sexp.unq(fp[1]).rpartition(":")
        num = next((props[k] for k in LCSC_FIELDS if props.get(k, "").strip()), "").strip()
        if num:
            buy = {"lcsc": num, "mpn": next((props[k] for k in MPN_FIELDS if props.get(k)), "")}
        else:
            buy = by_ref.get(ref) or by_vf.get((props.get("Value", ""), name)) or {"lcsc": "", "mpn": ""}
        own = OWN_FIELDS | set(LCSC_FIELDS) | ({site.role_field} if site.role_field else set())
        out[ref] = {
            "value": props.get("Value", ""), "footprint": name, "lib": lib,
            "descr": plain(sexp.unq(descr[1])) if descr else "",
            "side": side_of(fp),
            "x": round(float(at[1]), 4), "y": round(float(at[2]), 4),
            "rot": float(at[3]) if len(at) > 3 else 0.0,
            "box": extent(fp, at),
            "attrs": attr[1:] if attr else [],
            "role": props.get(site.role_field, "") if site.role_field else "",
            "datasheet": props.get("Datasheet", "").strip("~ "),
            "description": props.get("Description", ""),
            "fields": {k: v for k, v in props.items() if k not in own and v and v != "~"},
            **buy,
            "pins": [[n, pins[n]] for n in sorted(pins, key=natural)],
        }
    return out


def write_details(site, board, pcb, dest):
    """parts.json, one part to a line, in reference order, and no date in it:
    re-running it on an unchanged board is not a diff."""
    parts = details_of(site, board, pcb)
    ring = pcb.circle()
    polar = board.polar if board.polar is not None else bool(ring)
    head = {"board": board.pcb.name,
            "polar": [ring[0], ring[1]] if polar and ring else None,
            "faces": {"front": [board.front, board.front_note], "back": [board.back, board.back_note]}}
    lines = [json.dumps(r, separators=(",", ":")) + ": " + json.dumps(parts[r], separators=(",", ":"))
             for r in sorted(parts, key=natural)]
    path = dest / "parts.json"
    path.write_text(json.dumps(head)[:-1] + ',\n "parts": {\n  ' + ",\n  ".join(lines) + "\n }\n}\n")
    json.loads(path.read_text())                         # it is JSON
    unbought = sorted((r for r, p in parts.items() if not p["lcsc"] and
                       not {"exclude_from_bom", "exclude_from_pos_files", "dnp"} & set(p["attrs"])),
                      key=natural)
    print("  parts.json  %d parts, %d pins; %d with an LCSC number%s"
          % (len(parts), sum(len(p["pins"]) for p in parts.values()),
             sum(1 for p in parts.values() if p["lcsc"]),
             ("; none for " + " ".join(unbought[:12]) + (" and %d more" % (len(unbought) - 12)
                                                         if len(unbought) > 12 else "")) if unbought else ""))
    return parts


# ------------------------------------------------------------------ glb ----
def parts_meshed(g):
    """name -> meshes under that node, for every named node."""
    nodes = g["nodes"]

    def count(i):
        n = nodes[i]
        return ("mesh" in n) + sum(count(c) for c in n.get("children", []))
    return {n["name"]: count(i) for i, n in enumerate(nodes) if n.get("name")}


def read_glb(path):
    d = path.read_bytes()
    magic, _, total = struct.unpack_from("<III", d, 0)
    assert magic == 0x46546C67, f"{path} is not a GLB"
    off, chunks = 12, []
    while off < total:
        n, _ = struct.unpack_from("<II", d, off)
        chunks.append(d[off + 8: off + 8 + n])
        off += 8 + n
    return json.loads(chunks[0]), chunks[1]


CTYPE = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16,
         5125: np.uint32, 5126: np.float32}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def accessor(g, blob, i):
    """Accessor i as a (count, width) array, whatever its stride."""
    a = g["accessors"][i]
    v = g["bufferViews"][a["bufferView"]]
    dt, w = np.dtype(CTYPE[a["componentType"]]), WIDTH[a["type"]]
    stride = v.get("byteStride", dt.itemsize * w)
    start = v.get("byteOffset", 0) + a.get("byteOffset", 0)
    return np.ndarray((a["count"], w), dt, buffer=blob, offset=start,
                      strides=(stride, dt.itemsize)).copy()


class Writer:
    """The new binary chunk: one bufferView per accessor, 4-byte aligned."""

    def __init__(self):
        self.blob, self.views, self.accessors = bytearray(), [], []

    def add(self, arr, ctype, kind, target, stride=None, normalized=False, lo=None, hi=None):
        self.blob += b"\0" * (-len(self.blob) % 4)
        view = {"buffer": 0, "byteOffset": len(self.blob), "byteLength": arr.nbytes, "target": target}
        if stride:
            view["byteStride"] = stride
        self.blob += arr.tobytes()
        self.views.append(view)
        acc = {"bufferView": len(self.views) - 1, "componentType": ctype,
               "count": len(arr), "type": kind}
        if normalized:
            acc["normalized"] = True
        if lo is not None:
            acc["min"], acc["max"] = lo, hi
        self.accessors.append(acc)
        return len(self.accessors) - 1


def pieces(tris, limit=65535):
    """Split a triangle list so no piece indexes more than `limit` vertices."""
    if tris.max(initial=-1) < limit:
        yield tris
        return
    seen, start = set(), 0
    for k, t in enumerate(tris.tolist()):
        new = [v for v in t if v not in seen]
        if len(seen) + len(new) > limit:
            yield tris[start:k]
            seen, start = set(t), k
        else:
            seen.update(new)
    yield tris[start:]


def rebuild_mesh(g, blob, mesh, flat, w):
    """One mesh merged, welded and quantized: (primitives, centre, half, triangles)."""
    groups = {}
    for p in mesh["primitives"]:
        if p.get("mode", 4) != 4:
            continue                                     # lines and points: nothing of ours
        pos = accessor(g, blob, p["attributes"]["POSITION"]).astype(np.float64)
        nrm = accessor(g, blob, p["attributes"]["NORMAL"]).astype(np.float64) \
            if "NORMAL" in p["attributes"] else np.zeros_like(pos)
        if "indices" in p:
            idx = accessor(g, blob, p["indices"]).reshape(-1, 3).astype(np.int64)
        else:
            idx = np.arange(len(pos), dtype=np.int64).reshape(-1, 3)
        groups.setdefault(p.get("material"), []).append((pos, nrm, idx))
    if not groups:
        return [], [0, 0, 0], 1.0, 0
    every = np.concatenate([pos for grp in groups.values() for pos, _, _ in grp])
    lo, hi = every.min(0), every.max(0)
    centre, half = (lo + hi) / 2, max(float((hi - lo).max()) / 2, 1e-9)

    prims, tris = [], 0
    for mat, grp in groups.items():
        base, P, N, T = 0, [], [], []
        for pos, nrm, idx in grp:
            P.append(pos); N.append(nrm); T.append(idx + base); base += len(pos)
        P, N, T = np.concatenate(P), np.concatenate(N), np.concatenate(T)
        qp = np.round((P - centre) / half * 32767).clip(-32767, 32767).astype(np.int16)
        qn = None
        if flat:
            key = qp
        else:
            n = N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)
            qn = np.round(n * 127).clip(-127, 127).astype(np.int8)
            key = np.concatenate([qp, qn.astype(np.int16)], axis=1)
        _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
        T = inv.reshape(-1)[T]
        T = T[(T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 0] != T[:, 2])]
        vp, vn = qp[first], None if qn is None else qn[first]
        for piece in pieces(T):
            if not len(piece):
                continue
            used, local = np.unique(piece, return_inverse=True)
            pp = np.zeros((len(used), 4), np.int16)      # padded to an 8-byte stride
            pp[:, :3] = vp[used]
            attrs = {"POSITION": w.add(pp, 5122, "VEC3", 34962, stride=8, normalized=True,
                                       lo=pp[:, :3].min(0).tolist(), hi=pp[:, :3].max(0).tolist())}
            if vn is not None:
                nn = np.zeros((len(used), 4), np.int8)    # padded to a 4-byte stride
                nn[:, :3] = vn[used]
                attrs["NORMAL"] = w.add(nn, 5120, "VEC3", 34962, stride=4, normalized=True)
            prim = {"attributes": attrs, "mode": 4,
                    "indices": w.add(local.reshape(-1).astype(np.uint16), 5123, "SCALAR", 34963)}
            if mat is not None:
                prim["material"] = mat
            prims.append(prim)
            tris += len(piece)
    return prims, centre.tolist(), half, tris


def linear(c):
    """An sRGB component, 0..1, as glTF's linear."""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


BOX_COLOUR = [0.85, 0.42, 0.08, 0.8]        # linear; an orange no part is


def box_mesh(w, sx, sy, sz):
    """A box, sx x sz on the board and sy tall, standing on y = 0: flat
    shaded, so four corners per face with the face's normal."""
    x, z = sx / 2, sz / 2
    faces = [((1, 0, 0), [(x, 0, -z), (x, sy, -z), (x, sy, z), (x, 0, z)]),
             ((-1, 0, 0), [(-x, 0, z), (-x, sy, z), (-x, sy, -z), (-x, 0, -z)]),
             ((0, 1, 0), [(-x, sy, -z), (-x, sy, z), (x, sy, z), (x, sy, -z)]),
             ((0, -1, 0), [(-x, 0, z), (-x, 0, -z), (x, 0, -z), (x, 0, z)]),
             ((0, 0, 1), [(x, 0, z), (x, sy, z), (-x, sy, z), (-x, 0, z)]),
             ((0, 0, -1), [(-x, 0, -z), (-x, sy, -z), (x, sy, -z), (x, 0, -z)])]
    P = np.array([v for _, vs in faces for v in vs], np.float32)
    N = np.array([n for n, vs in faces for _ in vs], np.float32)
    T = np.array([[4 * k, 4 * k + 1, 4 * k + 2, 4 * k, 4 * k + 2, 4 * k + 3] for k in range(6)],
                 np.uint16).reshape(-1)
    return {"POSITION": w.add(P, 5126, "VEC3", 34962, lo=P.min(0).tolist(), hi=P.max(0).tolist()),
            "NORMAL": w.add(N, 5126, "VEC3", 34962)}, w.add(T, 5123, "SCALAR", 34963)


def rewrite(g, blob, name, parts, boxes=()):
    """The exported glTF made small; returns (gltf, binary chunk, stats).
    `boxes` are parts with no usable model, drawn as a box the size of their
    courtyard: (ref, x mm, y mm, rot deg, side, (x0, y0, x1, y1) local, height mm)."""
    w, meshes, frames = Writer(), [], []
    stats = {"triangles": 0, "primitives": 0}
    materials = [json.loads(json.dumps(m)) for m in g.get("materials", [])]

    # The board's own colours are KiCad's display colours -- sRGB -- written
    # where glTF means linear; the parts' come through OCC already linear.
    # A material both use (a part the same colour as the mask, say) is split
    # in two, so each can be treated as what it is.
    def is_board(mesh):
        return mesh.get("name", "").startswith(name + "_")
    board_mats = {p.get("material") for m in g["meshes"] if is_board(m) for p in m["primitives"]}
    part_mats = {p.get("material") for m in g["meshes"] if not is_board(m) for p in m["primitives"]}
    split = {}
    for i in (board_mats & part_mats) - {None}:
        split[i] = len(materials)
        materials.append(json.loads(json.dumps(materials[i])))
    for m in g["meshes"]:
        if not is_board(m):
            for p in m["primitives"]:
                if p.get("material") in split:
                    p["material"] = split[p["material"]]

    for mesh in g["meshes"]:
        prims, centre, half, tris = rebuild_mesh(g, blob, mesh, is_board(mesh), w)
        meshes.append({"name": mesh.get("name", ""), "primitives": prims})
        frames.append((centre, half))
        stats["triangles"] += tris
        stats["primitives"] += len(prims)

    # each mesh hangs off a child node that undoes the quantization; the scale
    # is uniform so the normals need no correcting
    nodes = [dict(n) for n in g["nodes"]]
    for n in list(nodes):
        if "mesh" in n:
            m = n.pop("mesh")
            centre, half = frames[m]
            nodes.append({"mesh": m, "translation": centre, "scale": [half] * 3})
            n.setdefault("children", []).append(len(nodes) - 1)

    # the parts and the board's layers, named for the viewer
    tops = [c for r in g["scenes"][g.get("scene", 0)]["nodes"]
            for c in [r] + nodes[r].get("children", [])]
    for i in tops:
        n = nodes[i]
        ref = n.get("name")
        if ref in parts:
            p = parts[ref]
            n["extras"] = {"ref": ref, "value": p["value"], "footprint": p["footprint"],
                           "side": p["side"]}
        elif ref and ref.startswith("=>") and n.get("children"):
            kid = nodes[n["children"][-1]]
            if "mesh" in kid:
                layer = meshes[kid["mesh"]]["name"][len(name) + 1:]
                n["name"] = layer
                n["extras"] = {"layer": layer}

    board_mats = {p.get("material") for m in meshes if m["name"].startswith(name + "_")
                  for p in m["primitives"]}
    shiny = {p.get("material") for m in meshes
             if m["name"][len(name) + 1:] in ("copper", "pad", "via") and m["name"].startswith(name + "_")
             for p in m["primitives"]}
    for i, mat in enumerate(materials):
        pbr = mat.setdefault("pbrMetallicRoughness", {})
        pbr["metallicFactor"], pbr["roughnessFactor"] = (0.55, 0.35) if i in shiny else (0.0, 0.6)
        rgba = pbr.get("baseColorFactor", [1] * 4)
        if i in board_mats:
            pbr["baseColorFactor"] = [round(linear(c), 6) for c in rgba[:3]] + rgba[3:]
        if rgba[3] < 1:                     # the mask's alpha, which glTF ignores unless asked
            mat["alphaMode"] = "BLEND"
            pbr["roughnessFactor"] = 0.3

    # the parts with no model, as boxes on their face of the board
    if boxes:
        face = {"front": [], "back": []}
        for n in g["nodes"]:
            if n.get("name") in parts and "translation" in n:
                face[parts[n["name"]]["side"]].append(n["translation"][1])
        top = float(np.median(face["front"])) if face["front"] else 0.0016
        bottom = float(np.median(face["back"])) if face["back"] else 0.0
        materials.append({"name": "no model", "alphaMode": "BLEND",
                          "pbrMetallicRoughness": {"baseColorFactor": BOX_COLOUR,
                                                   "metallicFactor": 0.0, "roughnessFactor": 0.7}})
        mat = len(materials) - 1
        root = g["scenes"][g.get("scene", 0)]["nodes"][0]
        from math import cos, radians, sin
        for ref, x, y, rot, side, (x0, y0, x1, y1), height in boxes:
            sx, sz, sy = (x1 - x0) / 1000, (y1 - y0) / 1000, height / 1000
            attrs, idx = box_mesh(w, sx, sy, sz)
            meshes.append({"name": "no model " + ref,
                           "primitives": [{"attributes": attrs, "indices": idx, "mode": 4, "material": mat}]})
            # the box's own centre, off the footprint origin, turned with it
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            a = radians(rot)
            ox, oy = cx * cos(a) + cy * sin(a), -cx * sin(a) + cy * cos(a)
            yaw = [0.0, sin(a / 2), 0.0, cos(a / 2)]           # +y is KiCad's anticlockwise
            flip = side == "back"
            node = {"name": ref, "mesh": len(meshes) - 1,
                    "translation": [(x + ox) / 1000, bottom if flip else top, (y + oy) / 1000],
                    # the back: turned over about x, then the same yaw
                    "rotation": [cos(a / 2), 0.0, -sin(a / 2), 0.0] if flip else yaw,
                    "extras": {"ref": ref, "value": parts[ref]["value"],
                               "footprint": parts[ref]["footprint"], "side": side, "placeholder": True}}
            nodes.append(node)
            nodes[root].setdefault("children", []).append(len(nodes) - 1)
            stats["triangles"] += 12
            stats["primitives"] += 1

    asset = dict(g["asset"])
    asset["extras"] = {k: v for k, v in asset.get("extras", {}).items()
                       if k in ("pcb_name", "generator")}
    out = {"asset": asset, "scene": g.get("scene", 0), "scenes": g["scenes"],
           "nodes": nodes, "meshes": meshes, "materials": materials,
           "accessors": w.accessors, "bufferViews": w.views,
           "buffers": [{"byteLength": len(w.blob)}],
           "extensionsUsed": ["KHR_mesh_quantization"],
           "extensionsRequired": ["KHR_mesh_quantization"]}
    return out, bytes(w.blob), stats


def write_glb(path, gltf, blob):
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    blob += b"\0" * (-len(blob) % 4)
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 28 + len(js) + len(blob))
                     + struct.pack("<II", len(js), 0x4E4F534A) + js
                     + struct.pack("<II", len(blob), 0x004E4942) + blob)


# ---------------------------------------------------------------- flatten --
CACHE = Path("~/.cache/pcbview/flat").expanduser()


def flattened(step):
    """A flat copy of a STEP assembly, cached by the source's content; None
    if it will not flatten."""
    import hashlib
    from .flatten import FlattenError, flatten
    data = step.read_bytes()
    out = CACHE / ("%s-%s.step" % (step.stem, hashlib.sha1(data).hexdigest()[:10]))
    if out.exists():
        return str(out)
    try:
        text, n, dropped = flatten(data.decode("latin-1"), step.stem)
    except (FlattenError, ValueError, IndexError) as e:
        print(f"  ! could not flatten {step.name}: {e}")
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    out.write_bytes(text.encode("latin-1"))
    return str(out)


# ------------------------------------------------------------------ run ----
def run(site, board, out, parts_only=False):
    dest = out / "3d"
    dest.mkdir(parents=True, exist_ok=True)
    pcb = Board(board.pcb)
    write_details(site, board, pcb, dest)
    if parts_only:
        return None
    need_kicad()
    parts = parts_of(pcb)
    text, seen = rewrite_models(pcb.text, Resolver(board.project_dir, site.models))

    tmp = scratch("pcbview_3d_")
    copy = tmp / board.pcb.name                    # same name: the meshes are named after it
    raw = tmp / "raw.glb"

    def export(text):
        copy.write_text(text)
        r = sh(["kicad-cli", "pcb", "export", "glb", *GLB_FLAGS, "-o", raw, copy])
        lost = set(re.findall(r"Could not add 3D model for (\S+)\.", r.stdout + r.stderr))
        g, blob = read_glb(raw)
        return g, blob, lost, parts_meshed(g)

    g, blob, lost, meshed = export(text)
    # a model that loads and meshes as nothing is, so far always, a STEP
    # assembly of assemblies: flatten it, and export again with the flat copy
    empty = [ref for ref, p in parts.items() if p["models"] and ref not in lost
             and meshed.get(ref, 0) == 0]
    flat = {}
    for ref in empty:
        for m in parts[ref]["models"]:
            res = seen.get(m)
            if res and res["status"] != "missing" and res["path"] not in flat:
                flat[res["path"]] = flattened(Path(res["path"]))
    flat = {k: v for k, v in flat.items() if v}
    if flat:
        for src, dst in flat.items():
            text = text.replace('(model "%s"' % src, '(model "%s"' % dst)
            for res in seen.values():
                if res.get("path") == src:
                    res["flattened"] = True
        print("  flattened   %s" % ", ".join(Path(k).name for k in flat))
        g, blob, lost, meshed = export(text)
    raw_bytes = raw.stat().st_size

    # parts with no model that meshes, drawn as a box the size of their courtyard
    placed = {props_of(fp).get("Reference", "?"): fp for fp in pcb.footprints()}
    boxes = []
    for ref, p in parts.items():
        if p["models"] and (ref in lost or meshed.get(ref, 0) == 0):
            fp = placed[ref]
            at = sexp.find(fp, "at")
            lb = extent(fp, at, local=True)
            if not lb:
                continue
            x0, y0, x1, y1 = lb
            inset = 0.2 if min(x1 - x0, y1 - y0) > 1.0 else 0.05     # courtyard excess
            lb = (x0 + inset, y0 + inset, x1 - inset, y1 - inset)
            h = min(max(0.35 * min(lb[2] - lb[0], lb[3] - lb[1]), 0.3), 2.5)
            boxes.append((ref, float(at[1]), float(at[2]), float(at[3]) if len(at) > 3 else 0.0,
                          p["side"], lb, h))

    gltf, blob, stats = rewrite(g, blob, board.pcb.stem, parts, boxes)
    glb = dest / "board.glb"
    write_glb(glb, gltf, blob)

    # what became of every part's model, for the caption and the report
    by = {"none": [], "missing": [], "empty": [], "substituted": [], "resolved": [], "ok": []}
    why = {}
    boxed = {b[0] for b in boxes}
    for ref, p in parts.items():
        shown = [m for m in p["models"]]
        if not shown:
            by["none"].append(ref)
            continue
        res = [seen.get(m) or {"status": "missing", "why": "not resolved"} for m in shown]
        if ref in lost or all(x["status"] == "missing" for x in res):
            by["missing"].append(ref)
            why[ref] = "; ".join(sorted({x["why"] for x in res if x["status"] == "missing"}) or
                                 {"kicad-cli could not load it"})
            continue
        if meshed.get(ref, 0) == 0:
            by["empty"].append(ref)
            continue
        st = {x["status"] for x in res}
        if any(x.get("flattened") for x in res):
            by.setdefault("flattened", []).append(ref)
        if "substituted" in st:
            by["substituted"].append(ref)
        elif st & {"resolved", "found"}:
            by["resolved"].append(ref)
        else:
            by["ok"].append(ref)
    for k in by:
        by[k].sort(key=natural)
    missing_models = sorted({m for ref in by["missing"] for m in parts[ref]["models"]
                             if (seen.get(m) or {}).get("status") == "missing"})
    # the boxed parts, by the model path that failed them
    groups = {}
    for ref in by["missing"] + by["empty"]:
        bad = [m for m in parts[ref]["models"] if (seen.get(m) or {}).get("status") == "missing"] \
            or parts[ref]["models"][:1]
        key = bad[0] if bad else ""
        g = groups.setdefault(key, {"model": key, "why": (seen.get(key) or {}).get("why", "")
                                    if ref in by["missing"] else "loaded, but meshed as nothing",
                                    "refs": []})
        g["refs"].append(ref)
    how = {"legacy": 0, "found": 0}
    for ref in by["resolved"]:
        st = {(seen.get(m) or {}).get("status") for m in parts[ref]["models"]}
        how["found" if "found" in st else "legacy"] += 1
    meta = {"board": board.pcb.name, "generated": date.today().isoformat(),
            "glb": glb.name, "bytes": glb.stat().st_size, "kicad_bytes": raw_bytes,
            "parts": len(parts),
            "modelled": len(by["ok"]) + len(by["resolved"]) + len(by["substituted"]),
            "no_model": by["none"], "missing": by["missing"], "empty": by["empty"],
            "substituted": by["substituted"], "resolved": by["resolved"],
            "flattened": sorted(by.get("flattened", []), key=natural), "boxed": sorted(boxed, key=natural),
            "why": why, "missing_models": missing_models,
            "boxes": sorted(groups.values(), key=lambda g: natural(g["refs"][0])), "resolved_how": how,
            "triangles": stats["triangles"], "primitives": stats["primitives"]}
    (dest / "board.json").write_text(json.dumps(meta, indent=1) + "\n")
    print("  board.glb   %.1f MB (kicad-cli's %.1f MB), %d triangles in %d primitives"
          % (glb.stat().st_size / 1e6, raw_bytes / 1e6, stats["triangles"], stats["primitives"]))
    print("              %d of %d parts modelled (%d via a resolved path, %d substituted); "
          "no model: %d; missing: %s%s"
          % (meta["modelled"], len(parts), len(by["resolved"]), len(by["substituted"]),
             len(by["none"]), " ".join(by["missing"]) or "none",
             ("; EMPTY: " + " ".join(by["empty"])) if by["empty"] else ""))
    for m in missing_models:
        refs = [r for r in by["missing"] if m in parts[r]["models"]]
        print("    ! %-70s %s  (%s)" % (m, " ".join(refs), (seen.get(m) or {}).get("why", "")))
    return meta
