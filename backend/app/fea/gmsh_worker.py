"""
Gmsh meshing worker. Runs in its own process: `python -m app.fea.gmsh_worker <job.json> <out.npz>`.

A separate process isolates the API server from the geometry kernel (crashes, hangs, memory) and lets the caller enforce a
timeout. One job meshes one of three sources with the full set of options of `gmsh_options`:

  {"kind": "primitive", "shape": "cylinder", "dims": {...}, "divisions": 4}      OpenCASCADE solid built from the dimensions
  {"kind": "file", "path": ".../model.step", "scale": 0.001}                     STEP / IGES / BREP / STL, scale = metres per file unit
  {"kind": "msh", "path": ".../mesh.msh", "scale": 1.0}                          an existing Gmsh mesh (any version, ASCII or binary)

Exit code 0 and the .npz written: nodes (n,3) in metres, elems (m, 4|8|10|20) in Abaqus node order, part_ends (k,) cumulative
element counts per solid, etype ('C3D4' | 'C3D10' | 'C3D8' | 'C3D20'), info (JSON text). Any failure prints one readable
line to stderr and exits with code 2.
"""
from __future__ import annotations

import json
import math
import os
import sys
from typing import Any, Dict, List, Tuple

import numpy as np

from app.fea import gmsh_options as go

STEP_EXT = (".step", ".stp")
IGES_EXT = (".iges", ".igs")
BREP_EXT = (".brep", ".brp")
STL_EXT = (".stl",)
SWEEPABLE = ("box", "cylinder", "tube", "plate_hole", "lbracket")   # a 2-D profile extruded along z gives bricks directly

# Gmsh -> Abaqus node order
_TET10 = [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]
_HEX20 = [0, 1, 2, 3, 4, 5, 6, 7, 8, 11, 13, 9, 16, 18, 19, 17, 10, 12, 14, 15]


class MeshError(Exception):
    pass


def _fail(msg: str):
    print(msg, file=sys.stderr)
    sys.exit(2)


# ------------------------------------------------------------------ geometry
def _read_stl_header(path: str) -> int:
    """Number of facets in a binary or ASCII STL (cheap sanity check before meshing)."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(84)
    if len(head) >= 84:
        n = int.from_bytes(head[80:84], "little")
        if 84 + 50 * n == size:
            return n
    with open(path, "rb") as f:
        return f.read().count(b"endfacet")


def _import_file(gmsh, path: str, ext: str) -> List[int]:
    if ext in STEP_EXT + IGES_EXT + BREP_EXT:
        try:
            gmsh.model.occ.importShapes(path)
            gmsh.model.occ.synchronize()
        except Exception as exc:  # noqa: BLE001
            raise MeshError(f"Could not read the CAD file ({str(exc).splitlines()[0][:120]}).") from exc
        vols = [t for _, t in gmsh.model.getEntities(3)]
        if vols:
            return vols
        # surface-only files (typical for IGES): sew the faces into one closed shell and make a solid of it
        surfs = [t for _, t in gmsh.model.getEntities(2)]
        if not surfs:
            raise MeshError("The file contains no solid bodies or surfaces. Export solids from your CAD program.")
        try:
            loop = gmsh.model.occ.addSurfaceLoop(surfs, sewing=True)
            gmsh.model.occ.addVolume([loop])
            gmsh.model.occ.synchronize()
        except Exception as exc:  # noqa: BLE001
            raise MeshError("The file holds surfaces that do not form one closed shell, so no solid could be built. "
                            "Export a solid body from your CAD program.") from exc
        vols = [t for _, t in gmsh.model.getEntities(3)]
        if not vols:
            raise MeshError("The surfaces in the file do not enclose a volume. Export a solid body.")
        return vols
    if ext in STL_EXT:
        n_facets = _read_stl_header(path)
        if n_facets == 0:
            raise MeshError("The STL file contains no triangles.")
        if n_facets > 120_000:
            raise MeshError(f"The STL has {n_facets:,} facets (limit 120,000). Decimate it first.")
        try:
            gmsh.merge(path)
            gmsh.model.mesh.classifySurfaces(math.radians(40.0), True, True, math.pi)
            gmsh.model.mesh.createGeometry()
            surfaces = [t for _, t in gmsh.model.getEntities(2)]
            if not surfaces:
                raise MeshError("No surface was found in the STL file.")
            loop = gmsh.model.geo.addSurfaceLoop(surfaces)
            gmsh.model.geo.addVolume([loop])
            gmsh.model.geo.synchronize()
        except MeshError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MeshError(f"Could not build a solid from the STL surface ({str(exc).splitlines()[0][:120]}).") from exc
        return [t for _, t in gmsh.model.getEntities(3)]
    raise MeshError("Unsupported CAD format. Use .step/.stp, .iges/.igs, .brep or .stl.")


def _solid(occ, shape: str, d: Dict[str, float]) -> List[int]:
    """OpenCASCADE solid of a primitive, centred like the built-in generator (L-bracket: corner at the origin)."""
    g = lambda k, dv: float(d.get(k, dv))  # noqa: E731
    if shape == "box":
        L, W, H = g("length", 1.0), g("width", 1.0), g("height", 1.0)
        return [occ.addBox(-L / 2, -W / 2, -H / 2, L, W, H)]
    if shape == "cylinder":
        r, h = g("radius", 0.5), g("height", 1.0)
        return [occ.addCylinder(0, 0, -h / 2, 0, 0, h, r)]
    if shape == "sphere":
        return [occ.addSphere(0, 0, 0, g("radius", 0.5))]
    if shape == "tube":
        ro, ri, h = g("outer_radius", 0.5), g("inner_radius", 0.3), g("height", 1.0)
        if ri >= ro:
            raise MeshError("Tube inner radius must be smaller than the outer radius")
        out, _ = occ.cut([(3, occ.addCylinder(0, 0, -h / 2, 0, 0, h, ro))], [(3, occ.addCylinder(0, 0, -h, 0, 0, 2 * h, ri))])
        return [t for _, t in out]
    if shape == "plate_hole":
        L, W, T, rh = g("length", 1.0), g("width", 0.5), g("thickness", 0.1), g("hole_radius", 0.1)
        if 2 * rh >= min(L, W):
            raise MeshError("Hole diameter must be smaller than the plate width")
        out, _ = occ.cut([(3, occ.addBox(-L / 2, -W / 2, -T / 2, L, W, T))], [(3, occ.addCylinder(0, 0, -T, 0, 0, 2 * T, rh))])
        return [t for _, t in out]
    if shape == "lbracket":
        a, b, t, dp = g("length", 1.0), g("height", 1.0), g("thickness", 0.25), g("depth", 0.25)
        if t >= min(a, b):
            raise MeshError("L-bracket leg thickness must be smaller than its length and height")
        out, _ = occ.cut([(3, occ.addBox(0, 0, 0, a, b, dp))], [(3, occ.addBox(t, t, -dp, a - t, b - t, 3 * dp))])
        return [x for _, x in out]
    if shape == "cone":
        rb, rt, h = g("bottom_radius", 0.5), g("top_radius", 0.25), g("height", 1.0)
        return [occ.addCone(0, 0, -h / 2, 0, 0, h, rb, rt)]
    if shape == "torus":
        R, r = g("major_radius", 0.5), g("minor_radius", 0.15)
        if r >= R:
            raise MeshError("Torus minor radius must be smaller than the major radius")
        return [occ.addTorus(0, 0, 0, R, r)]
    raise MeshError(f"Unknown primitive shape '{shape}'")


def _profile(occ, shape: str, d: Dict[str, float]) -> Tuple[List[int], float, float]:
    """2-D profile (face tags), base height z0 and extrusion length of a sweepable primitive."""
    g = lambda k, dv: float(d.get(k, dv))  # noqa: E731
    if shape == "box":
        L, W, H = g("length", 1.0), g("width", 1.0), g("height", 1.0)
        return [occ.addRectangle(-L / 2, -W / 2, -H / 2, L, W)], -H / 2, H
    if shape == "cylinder":
        r, h = g("radius", 0.5), g("height", 1.0)
        return [occ.addDisk(0, 0, -h / 2, r, r)], -h / 2, h
    if shape == "tube":
        ro, ri, h = g("outer_radius", 0.5), g("inner_radius", 0.3), g("height", 1.0)
        if ri >= ro:
            raise MeshError("Tube inner radius must be smaller than the outer radius")
        out, _ = occ.cut([(2, occ.addDisk(0, 0, -h / 2, ro, ro))], [(2, occ.addDisk(0, 0, -h / 2, ri, ri))])
        return [t for _, t in out], -h / 2, h
    if shape == "plate_hole":
        L, W, T, rh = g("length", 1.0), g("width", 0.5), g("thickness", 0.1), g("hole_radius", 0.1)
        if 2 * rh >= min(L, W):
            raise MeshError("Hole diameter must be smaller than the plate width")
        out, _ = occ.cut([(2, occ.addRectangle(-L / 2, -W / 2, -T / 2, L, W))], [(2, occ.addDisk(0, 0, -T / 2, rh, rh))])
        return [t for _, t in out], -T / 2, T
    if shape == "lbracket":
        a, b, t, dp = g("length", 1.0), g("height", 1.0), g("thickness", 0.25), g("depth", 0.25)
        if t >= min(a, b):
            raise MeshError("L-bracket leg thickness must be smaller than its length and height")
        out, _ = occ.cut([(2, occ.addRectangle(0, 0, 0, a, b))], [(2, occ.addRectangle(t, t, 0, a - t, b - t))])
        return [x for _, x in out], 0.0, dp
    raise MeshError(f"'{shape}' cannot be swept")


def characteristic_length(shape: str, d: Dict[str, float]) -> float:
    """Smallest meaningful dimension of a primitive: element size = this / divisions when no size is given."""
    g = lambda k, dv: float(d.get(k, dv))  # noqa: E731
    if shape == "box":
        return min(g("length", 1.0), g("width", 1.0), g("height", 1.0))
    if shape == "cylinder":
        return min(2 * g("radius", 0.5), g("height", 1.0))
    if shape == "sphere":
        return 2 * g("radius", 0.5)
    if shape == "tube":
        return min(g("outer_radius", 0.5) - g("inner_radius", 0.3), g("height", 1.0))
    if shape == "plate_hole":
        return min(g("length", 1.0), g("width", 0.5), g("thickness", 0.1))
    if shape == "lbracket":
        return min(g("thickness", 0.25), g("depth", 0.25))
    if shape == "cone":
        return min(g("bottom_radius", 0.5) + g("top_radius", 0.25), g("height", 1.0))
    if shape == "torus":
        return 2 * g("minor_radius", 0.15)
    raise MeshError(f"Unknown primitive shape '{shape}'")


# -------------------------------------------------------------------- fields
def _add_regions(gmsh, regions, to_model, far: float, factor: float) -> None:
    """Local size fields (balls and boxes) combined with a Min field and used as the background mesh."""
    F = gmsh.model.mesh.field
    ids = []
    for r in regions:
        vin = to_model(r["size_in"]) * factor
        vout = (to_model(r["size_out"]) * factor) if r["size_out"] else far
        if r["shape"] == "ball":
            f = F.add("Ball")
            for k, v in zip(("XCenter", "YCenter", "ZCenter"), r["center"]):
                F.setNumber(f, k, to_model(v))
            F.setNumber(f, "Radius", to_model(r["radius"]))
            thick = to_model(r["thickness"]) if r["thickness"] else 2 * to_model(r["radius"])
        else:
            f = F.add("Box")
            for k, lo, hi in zip("XYZ", r["min"], r["max"]):
                F.setNumber(f, f"{k}Min", to_model(lo))
                F.setNumber(f, f"{k}Max", to_model(hi))
            thick = to_model(r["thickness"]) if r["thickness"] else 3 * vin
        F.setNumber(f, "VIn", vin)
        F.setNumber(f, "VOut", vout)
        F.setNumber(f, "Thickness", thick)
        ids.append(f)
    base = F.add("MathEval")
    F.setString(base, "F", repr(far))
    ids.append(base)
    m = F.add("Min")
    F.setNumbers(m, "FieldsList", ids)
    F.setAsBackgroundMesh(m)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)


# ------------------------------------------------------------------ meshing
def _collect(gmsh, vols, etype: str, nodes: np.ndarray, tags_sorted, tag_order, accept) -> Tuple[np.ndarray, np.ndarray, Dict[int, int]]:
    """Gather the elements of the accepted Gmsh type(s); returns (elements, cumulative ends per solid, rejected type counts)."""
    parts, ends, rejected, total = [], [], {}, 0
    for vol in sorted(vols):
        types, _, enodes = gmsh.model.mesh.getElements(3, vol)
        for t, en in zip(types, enodes):
            t = int(t)
            nn = {4: 4, 11: 10, 5: 8, 17: 20, 12: 27, 6: 6, 7: 5, 3: 4, 8: 3, 9: 6}.get(t)
            if nn is None:
                rejected[t] = rejected.get(t, 0) + 1
                continue
            arr = np.asarray(en, dtype=np.int64).reshape(-1, nn)
            if t not in accept:
                rejected[t] = rejected.get(t, 0) + len(arr)
                continue
            arr = tag_order[np.searchsorted(tags_sorted, arr)]                 # gmsh node tags -> 0-based rows
            if t == 12:
                arr = arr[:, :20]                                              # Hex27: the first 20 nodes are the serendipity set
            if t in (11,):
                arr = arr[:, _TET10]
            if t in (17, 12):
                arr = arr[:, _HEX20]
            if etype in ("C3D4", "C3D10"):
                # discrete (STL) geometry can yield flat sliver tetrahedra with zero volume: they carry no volume, drop them
                p = nodes[arr[:, :4]]
                v = np.abs(np.einsum("ei,ei->e", np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), p[:, 3] - p[:, 0])) / 6.0
                if len(v):
                    arr = arr[v > 1e-6 * np.median(v)]
            parts.append(arr)
            total += len(arr)
        if total and (not ends or ends[-1] != total):
            ends.append(total)
    return (np.vstack(parts) if parts else np.zeros((0, 1), dtype=np.int64)), np.array(ends, dtype=np.int64), rejected


def _describe_rejected(rejected: Dict[int, int]) -> str:
    names = {6: "prisms", 7: "pyramids", 9: "prisms", 4: "tetrahedra", 5: "hexahedra", 11: "tet10", 17: "hex20", 12: "hex27"}
    return ", ".join(f"{n} {names.get(t, 'type ' + str(t))}" for t, n in rejected.items())


def _has_inverted(nodes: np.ndarray, elems: np.ndarray, etype: str) -> bool:
    """True if a quadratic element is folded: its Jacobian changes sign inside the element (checked with the solver's own element)."""
    from app.fea.model import FEMesh
    mesh = FEMesh(nodes, elems, etype)
    for sl in mesh.chunks():
        _, _, _, detJ, _ = mesh.jacobian(sl)
        if ((detJ.min(axis=1) <= 0) & (detJ.max(axis=1) >= 0)).any():
            return True
    return False


def build(job: Dict[str, Any], straight: bool = False):
    import gmsh

    src = job["source"]
    kind = src["kind"]
    opts = go.normalize(job.get("options"))
    hexa = opts["family"] == "hex"
    order = opts["order"]
    scale = float(src.get("scale", 1.0))
    to_model = lambda metres: metres / scale  # noqa: E731    gmsh works in the file's own length unit
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("General.NumThreads", 1)
        gmsh.model.add("m")
        use_sweep = False
        size_m = opts["size"]

        if kind == "msh":
            return _read_msh(gmsh, src["path"], scale)

        if kind == "primitive":
            shape, dims = src["shape"], {k: float(v) for k, v in (src.get("dims") or {}).items()}
            div = max(1, int(src.get("divisions", 4)))
            method = opts["hex_method"]
            if hexa and method == "sweep" and shape not in SWEEPABLE:
                raise MeshError(f"A {shape} cannot be swept into bricks; use the 'subdivide' brick method.")
            use_sweep = hexa and shape in SWEEPABLE and method in ("auto", "sweep")
            if not size_m:
                size_m = characteristic_length(shape, dims) / div
            factor = 2.0 if (hexa and not use_sweep) else 1.0                  # subdividing a tet makes 4 bricks of half its edge
            if use_sweep:
                faces, z0, height = _profile(gmsh.model.occ, shape, dims)
                gmsh.model.occ.synchronize()
                nz = max(1, int(round(height / size_m)))
                vols = []
                for f in faces:
                    gmsh.model.mesh.setRecombine(2, f)
                    out = gmsh.model.occ.extrude([(2, f)], 0, 0, height, numElements=[nz], heights=[1], recombine=True)
                    vols += [t for d, t in out if d == 3]
                gmsh.model.occ.synchronize()
            else:
                vols = _solid(gmsh.model.occ, shape, dims)
                gmsh.model.occ.synchronize()
            vols = [t for _, t in gmsh.model.getEntities(3)]
        else:
            path = src["path"]
            ext = os.path.splitext(path)[1].lower()
            vols = _import_file(gmsh, path, ext)
            factor = 2.0 if hexa else 1.0
            if ext in STL_EXT:
                straight = True                                                # facets are straight: keep mid-side nodes on them

        # bounding box -> sanity checks on the requested element size
        xmin, ymin, zmin, xmax, ymax, zmax = gmsh.model.getBoundingBox(-1, -1)
        diag = math.sqrt((xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2)
        if diag <= 0:
            raise MeshError("The geometry has zero size.")
        size = to_model(size_m) if size_m else diag / 12.0
        if diag / size > 150:
            raise MeshError(f"Element size {size_m:g} m is too small for a part this large (more than 150 elements across). Use a larger size.")
        if size > diag:
            size = diag / 2.0
        far = size * factor
        near = to_model(opts["min_size"]) * factor if opts["min_size"] else far * 0.15
        if opts["regions"]:
            near = min(near, min(to_model(r["size_in"]) for r in opts["regions"]) * factor)
        gmsh.option.setNumber("Mesh.MeshSizeMax", far)
        gmsh.option.setNumber("Mesh.MeshSizeMin", near)
        gmsh.option.setNumber("Mesh.MeshSizeFactor", opts["size_factor"])
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", opts["curvature_points"])
        gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 1 if opts["size_from_points"] else 0)
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 1 if opts["extend_from_boundary"] else 0)
        if opts["regions"]:
            _add_regions(gmsh, opts["regions"], to_model, far, factor)
        gmsh.option.setNumber("Mesh.Algorithm", opts["algorithm_2d"])
        gmsh.option.setNumber("Mesh.Algorithm3D", opts["algorithm_3d"])
        gmsh.option.setNumber("Mesh.RecombinationAlgorithm", opts["recombine_algorithm"])
        gmsh.option.setNumber("Mesh.Smoothing", opts["smoothing_steps"])

        try:
            gmsh.model.mesh.generate(3)
        except Exception as exc:  # noqa: BLE001
            raise MeshError(f"Volume meshing failed ({str(exc).splitlines()[0][:100]}). For STL files the surface must be closed "
                            "(watertight) without self-intersections; otherwise try a slightly larger element size or "
                            "another 3-D algorithm.") from exc
        opt = go.OPTIMIZERS[opts["optimize"]]
        if opt:
            try:
                gmsh.model.mesh.optimize(opt)
            except Exception as exc:  # noqa: BLE001
                raise MeshError(f"The '{opts['optimize']}' optimiser failed ({str(exc).splitlines()[0][:100]}).") from exc
        for _ in range(opts["refine"]):
            gmsh.model.mesh.refine()
        if hexa and not use_sweep:
            # new nodes are placed on the CAD surface unless straight edges are asked for; on a coarse curved mesh that can
            # fold a brick, which is why a folded result is rebuilt with straight placement (see main)
            gmsh.option.setNumber("Mesh.SecondOrderLinear", 1 if (straight or opts["straight_midside"]) else 0)
            gmsh.option.setNumber("Mesh.SubdivisionAlgorithm", 2)              # all hexahedra
            gmsh.model.mesh.refine()
        if order == 2:
            gmsh.option.setNumber("Mesh.SecondOrderLinear", 1 if (straight or opts["straight_midside"]) else 0)
            gmsh.option.setNumber("Mesh.SecondOrderIncomplete", 1)             # Hex20 (serendipity), not Hex27
            gmsh.model.mesh.setOrder(2)
            if opts["curve_optimize"] != "none" and not (straight or opts["straight_midside"]):
                try:
                    gmsh.option.setNumber("Mesh.HighOrderOptimize", go.CURVE_OPTIMIZERS[opts["curve_optimize"]])
                    gmsh.model.mesh.optimize("HighOrder")
                except Exception:  # noqa: BLE001 - optimisation is best effort, the mesh itself is valid
                    pass

        etype = go.element_type_of(opts)
        accept = {"C3D4": (4,), "C3D10": (11,), "C3D8": (5,), "C3D20": (17,)}[etype]
        tags, coords, _ = gmsh.model.mesh.getNodes()
        tags = np.asarray(tags, dtype=np.int64)
        tag_order = np.argsort(tags)
        tags_sorted = tags[tag_order]
        nodes = np.asarray(coords, dtype=float).reshape(-1, 3) * scale
        elems, ends, rejected = _collect(gmsh, vols, etype, nodes, tags_sorted, tag_order, accept)
        if rejected:
            if use_sweep and (6 in rejected or 9 in rejected):
                hint = ("The quad recombination left triangles, which sweep into prisms. Choose a full-quad recombination "
                        "algorithm (Simple full-quad or Blossom full-quad) or the 'subdivide' brick method.")
            else:
                hint = "Use the 'subdivide' brick method or a tetrahedral mesh." if hexa else "Try another 3-D algorithm."
            raise MeshError(f"Gmsh produced elements the solver does not support ({_describe_rejected(rejected)}). " + hint)
        if not len(elems):
            raise MeshError("Meshing produced no elements.")
        info = {"gmsh": gmsh.__version__, "family": opts["family"], "order": order, "size_m": float(size * scale),
                "method": "sweep" if use_sweep else ("subdivide" if hexa else "tet"), "straight_midside": bool(straight)}
        return _finish(nodes, elems, ends, etype, info)
    finally:
        gmsh.finalize()


def _finish(nodes, elems, ends, etype, info):
    # drop nodes that no element uses (e.g. geometry vertices of non-meshed entities)
    keep = np.unique(elems)
    remap = -np.ones(len(nodes), dtype=np.int64)
    remap[keep] = np.arange(len(keep))
    return nodes[keep], remap[elems], ends, etype, info


def _read_msh(gmsh, path: str, scale: float):
    """An existing mesh: every volume becomes a part. Tet4 / Tet10 / Hex8 / Hex20 (Hex27 is reduced to Hex20)."""
    try:
        gmsh.open(path)
    except Exception as exc:  # noqa: BLE001
        raise MeshError(f"Could not read the Gmsh mesh file ({str(exc).splitlines()[0][:120]}).") from exc
    tags, coords, _ = gmsh.model.mesh.getNodes()
    if not len(tags):
        raise MeshError("The mesh file contains no nodes.")
    tags = np.asarray(tags, dtype=np.int64)
    order = np.argsort(tags)
    nodes = np.asarray(coords, dtype=float).reshape(-1, 3) * scale
    vols = [t for _, t in gmsh.model.getEntities(3)]
    present = {}
    for v in vols or [-1]:
        for t, tg in zip(*gmsh.model.mesh.getElements(3, v)[:2]):
            present[int(t)] = present.get(int(t), 0) + len(tg)
    if not present:
        raise MeshError("The mesh file contains no volume elements (only surface or line elements).")
    families = {4: "C3D4", 11: "C3D10", 5: "C3D8", 17: "C3D20", 12: "C3D20"}
    bad = {t: n for t, n in present.items() if t not in families}
    if bad:
        raise MeshError(f"The mesh contains elements the solver does not support ({_describe_rejected(bad)}). "
                        "Supported: tetrahedra (4/10 nodes) and hexahedra (8/20 nodes).")
    kinds = {families[t] for t in present}
    if len(kinds) > 1:
        raise MeshError("The mesh mixes element types (" + ", ".join(sorted(kinds)) + "). Export one element type.")
    etype = kinds.pop()
    accept = tuple(t for t in present)
    elems, ends, _ = _collect(gmsh, vols or [-1], etype, nodes, tags[order], order, accept)
    if not len(elems):
        raise MeshError("The mesh contains no usable volume elements.")
    return _finish(nodes, elems, ends, etype, {"gmsh": gmsh.__version__, "method": "msh", "family": "hex" if etype in ("C3D8", "C3D20") else "tet",
                                              "order": 2 if etype in ("C3D10", "C3D20") else 1})


def main(argv) -> int:
    if len(argv) != 3:
        _fail("usage: gmsh_worker <job.json> <out.npz>")
    with open(argv[1], "r", encoding="utf-8") as f:
        job = json.load(f)
    try:
        nodes, elems, ends, etype, info = build(job)
        if etype != "C3D4" and job["source"]["kind"] != "msh" and _has_inverted(nodes, elems, etype):
            nodes, elems, ends, etype, info = build(job, straight=True)       # curved elements folded: use straight edges
    except (MeshError, go.OptionError) as exc:
        _fail(str(exc))
    except ImportError:
        _fail("Gmsh is not installed on the server (pip install gmsh).")
    except Exception as exc:  # noqa: BLE001
        _fail(f"Gmsh meshing failed: {str(exc).splitlines()[0][:200]}")
    np.savez(argv[2], nodes=nodes, elems=elems, part_ends=ends, etype=np.array(etype), info=np.array(json.dumps(info)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
