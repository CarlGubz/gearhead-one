"""CalculiX (ccx) adapter: nonlinear / transient / buckling / contact analyses.

The native solver (simulation.py) covers linear static, steady thermal and modal analysis in pure
Python. Everything that needs a mature nonlinear FE code is delegated to CalculiX, which runs as a
separate process (so its GPL licence does not extend to this application):

    ccx_static             nonlinear static: bilinear plasticity, large deformation (NLGEOM)
    ccx_buckling           linear (eigenvalue) buckling: load factors + mode shapes
    ccx_frequency          natural frequencies (cross-check of the native modal solver)
    ccx_thermal_transient  transient heat conduction from an initial temperature
    ccx_dynamic            implicit transient dynamics (step or ramp load)
    ccx_contact            assemblies: surface-to-surface contact with friction (or bonded pairs)

Mesh: gmsh second-order tetrahedra (C3D10). Units: N, mm, tonne, s (so stress = MPa, energy = mJ,
conductivity W/(m K) == mW/(mm K)). Results are returned in the same surface-payload format as the
native solver, plus time histories where relevant.

Finding the solver: CCX_COMMAND (a full command; ``{dir}`` is replaced by the job directory), then
``ccx`` on PATH, then the docker image CCX_DOCKER_IMAGE (default ``cadweb/calculix``; build it with
``docker build -t cadweb/calculix docker/calculix``).
"""
from __future__ import annotations

import math
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from . import modeling3d
from . import simulation as native

KINDS: dict[str, str] = {
    "ccx_static": "Nonlinear static (plasticity / large deformation)",
    "ccx_buckling": "Linear buckling",
    "ccx_frequency": "Natural frequencies (CalculiX)",
    "ccx_thermal_transient": "Transient thermal",
    "ccx_dynamic": "Transient dynamic",
    "ccx_contact": "Contact (assembly)",
}
ASSEMBLY_KINDS = {"ccx_contact"}

# Abaqus / CalculiX C3D10 element faces on the corner nodes (0-based)
C3D10_FACES = ((0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0))
# gmsh tet10 edge-node order (01, 12, 20, 30, 32, 31) -> Abaqus (01, 12, 20, 03, 13, 23)
GMSH_TO_C3D10 = [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]


# ----------------------------------------------------------------- solver lookup
_LOOKUP: dict[str, Any] = {"t": 0.0, "cmd": None, "how": ""}


def solver_command(refresh: bool = False) -> tuple[Optional[list[str]], str]:
    """(command prefix, description) for running ccx, or (None, hint) when it is not available."""
    if not refresh and time.time() - _LOOKUP["t"] < 60:
        return _LOOKUP["cmd"], _LOOKUP["how"]
    cmd, how = None, ""
    env = os.environ.get("CCX_COMMAND")
    if env:
        cmd, how = shlex.split(env, posix=os.name != "nt"), "CCX_COMMAND"
    else:
        for name in ("ccx", "ccx_static", "ccx_2.22", "ccx_2.21", "ccx_2.20"):
            p = shutil.which(name)
            if p:
                cmd, how = [p], p
                break
    docker = shutil.which("docker")
    if cmd is None and docker:
        image = os.environ.get("CCX_DOCKER_IMAGE", "cadweb/calculix")
        try:
            ok = subprocess.run([docker, "image", "inspect", image], capture_output=True, timeout=20).returncode == 0
        except Exception:
            ok = False
        if ok:
            cmd, how = [docker, "run", "--rm", "-e", "OMP_NUM_THREADS={threads}", "-e", "CCX_NPROC_EQUATION_SOLVER={threads}",
                        "-v", "{dir}:/work", "-w", "/work", image], f"docker image {image}"
    if cmd:
        # absolute executable path: gmsh (loaded in this process) can rewrite the process PATH on Windows
        cmd = [shutil.which(cmd[0]) or cmd[0]] + cmd[1:]
    if cmd is None:
        how = ("CalculiX not found: install it (Debian/Ubuntu: apt install calculix-ccx; FreeCAD ships ccx.exe on "
               "Windows), set CCX_COMMAND, or build the image: docker build -t cadweb/calculix docker/calculix")
    _LOOKUP.update(t=time.time(), cmd=cmd, how=how)
    return cmd, how


def status() -> dict:
    cmd, how = solver_command()
    return {"available": cmd is not None, "solver": how if cmd else None, "hint": None if cmd else how, "kinds": KINDS}


def run_ccx(deck: str, timeout: float = 900.0) -> tuple[str, str, str]:
    """Run a CalculiX deck; returns (.dat text, .frd text, solver log)."""
    cmd, how = solver_command()
    if cmd is None:
        raise RuntimeError(how)
    with tempfile.TemporaryDirectory(prefix="cadweb-ccx-") as td:
        with open(os.path.join(td, "job.inp"), "w", encoding="ascii") as fh:
            fh.write(deck)
        # one solver thread by default: the multithreaded SPOOLES build in Debian / Ubuntu returns spurious,
        # run-to-run different eigenvalues (buckling) - correctness over speed. CCX_THREADS overrides.
        threads = os.environ.get("CCX_THREADS", "1")
        full = [c.replace("{dir}", td).replace("{threads}", threads) for c in cmd] + ["-i", "job"]
        env = {**os.environ, "OMP_NUM_THREADS": threads, "CCX_NPROC_EQUATION_SOLVER": threads}
        try:
            proc = subprocess.run(full, cwd=td, capture_output=True, text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"CalculiX did not finish within {timeout:.0f} s")
        log = (proc.stdout or "") + (proc.stderr or "")
        errors = [ln.strip() for ln in log.splitlines() if "*ERROR" in ln]
        read = lambda n: open(os.path.join(td, n), encoding="ascii", errors="replace").read() if os.path.exists(os.path.join(td, n)) else ""  # noqa: E731
        dat, frd = read("job.dat"), read("job.frd")
        for _ in range(20):  # bind-mounted output (docker on Windows / macOS) can land after the container exits
            if not frd or frd.rstrip().endswith("9999"):
                break
            time.sleep(0.1)
            dat, frd = read("job.dat"), read("job.frd")
        if errors or proc.returncode != 0 and not frd:
            raise RuntimeError("CalculiX: " + ("; ".join(dict.fromkeys(errors))[:900] if errors else log.strip()[-900:] or f"exit code {proc.returncode}"))
        return dat, frd, log


# ------------------------------------------------------------------ result files
@dataclass
class FrdBlock:
    name: str
    time: float
    comps: list[str]
    data: dict[int, list[float]]


def parse_frd(text: str) -> list[FrdBlock]:
    """Nodal result blocks of an ASCII .frd file."""
    blocks: list[FrdBlock] = []
    cur: Optional[FrdBlock] = None
    t = 0.0
    for line in text.splitlines():
        if line.startswith("  100C"):
            tok = line[5:].split()
            try:
                t = float(tok[2]) if len(tok) > 2 else 0.0
            except ValueError:
                t = 0.0
            cur = None
        elif line.startswith(" -4") and cur is None:
            cur = FrdBlock(line[3:].split()[0], t, [], {})
            blocks.append(cur)
        elif line.startswith(" -5") and cur is not None:
            cur.comps.append(line[3:].split()[0])
        elif line.startswith(" -1") and cur is not None:
            nid = int(line[3:13])
            body = line[13:]
            vals = [float(body[i:i + 12]) for i in range(0, len(body.rstrip()), 12) if body[i:i + 12].strip()]
            cur.data[nid] = vals
        elif line.startswith(" -2") and cur is not None and cur.data:
            body = line[13:]
            last = next(reversed(cur.data))
            cur.data[last] += [float(body[i:i + 12]) for i in range(0, len(body.rstrip()), 12) if body[i:i + 12].strip()]
        elif line.startswith(" -3"):
            cur = None
    return blocks


def _field(block: FrdBlock, n: int, ncomp: int) -> np.ndarray:
    out = np.zeros((n, ncomp))
    for nid, vals in block.data.items():
        if 1 <= nid <= n:
            out[nid - 1, : min(ncomp, len(vals))] = vals[:ncomp]
    return out


def parse_buckling_factors(dat: str) -> list[float]:
    i = dat.rfind("B U C K L I N G   F A C T O R   O U T P U T")
    out = []
    for ln in (dat[i:] if i >= 0 else "").splitlines()[1:]:
        parts = ln.split()
        if len(parts) == 2 and parts[0].isdigit():
            out.append(float(parts[1]))
        elif out and parts and not parts[0].isdigit():
            break
    return out


def parse_frequencies(dat: str) -> list[float]:
    m = re.search(r"E I G E N V A L U E\s+O U T P U T(.*?)(?:P A R T I C I P|\Z)", dat, re.S)
    out = []
    for ln in (m.group(1) if m else "").splitlines():
        parts = ln.split()
        if len(parts) >= 4 and parts[0].isdigit():
            try:
                out.append(float(parts[3]))
            except ValueError:
                pass
    return out


def parse_totals(dat: str, nset: str) -> Optional[list[float]]:
    """Last 'total force' line printed for a node set (*NODE PRINT, TOTALS=ONLY)."""
    pat = re.compile(r"total force \(fx,fy,fz\) for set " + re.escape(nset.upper()) + r" and time\s+\S+\s*\n\s*(\S+)\s+(\S+)\s+(\S+)", re.I)
    hits = pat.findall(dat)
    return [float(x) for x in hits[-1]] if hits else None


# ------------------------------------------------------------------------ bodies
@dataclass
class Body:
    name: str
    shape: Any  # part-local B-rep (selectors resolve here)
    M: np.ndarray  # 4x4 part -> world
    material: dict
    md: Any = None  # native.MeshData (order 2, world coordinates)
    node0: int = 0
    elem0: int = 0
    faces: dict = field(default_factory=dict)  # sorted corner triple -> (element id, face no)

    def world(self, p) -> np.ndarray:
        return (self.M @ np.array([p[0], p[1], p[2], 1.0]))[:3]


def _mesh_bodies(bodies: list[Body], size: Optional[float], max_elements: int) -> float:
    from . import assembly as asm

    diag = max(b.shape.BoundingBox().DiagonalLength for b in bodies)
    thin = min(min(bb.xlen, bb.ylen, bb.zlen) for bb in (b.shape.BoundingBox() for b in bodies))
    h = size or min(diag / 14.0, max(thin / 3.0, diag / 200.0))  # >= 3 quadratic elements through thin sections
    for _ in range(6):
        total = 0
        for b in bodies:
            ws = b.shape if np.allclose(b.M, np.eye(4)) else asm.apply_pose(b.shape, b.M)
            b.md = native._gmsh_mesh(ws, h, order=2)
            total += len(b.md.T)
        if total <= max_elements:
            break
        if size:
            raise ValueError(f"mesh has {total} elements (> {max_elements}); increase the mesh size")
        h *= 1.35
    else:
        raise ValueError("could not generate a mesh within the element limit; simplify the model")
    n0 = e0 = 0
    for b in bodies:
        b.node0, b.elem0 = n0, e0
        T10 = b.md.T10[:, _c3d10_order(b.md)]
        b.md.T10 = T10
        for k, (i, j, l) in enumerate(C3D10_FACES):
            for e, tri in enumerate(T10[:, [i, j, l]].tolist()):
                b.faces[tuple(sorted(tri))] = (e0 + e + 1, k + 1)
        n0 += len(b.md.P)
        e0 += len(b.md.T)
    return h


def _c3d10_order(md) -> list[int]:
    """gmsh -> Abaqus mid-node permutation, verified on the actual mesh (midside nodes sit on edges)."""
    T = md.T10[: min(len(md.T10), 50)]
    P = md.P
    want = [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)]
    best, err_best = None, math.inf
    for perm in (GMSH_TO_C3D10, list(range(10))):
        Tp = T[:, perm]
        err = sum(np.linalg.norm(P[Tp[:, 4 + k]] - 0.5 * (P[Tp[:, a]] + P[Tp[:, b]]), axis=1).sum() for k, (a, b) in enumerate(want))
        if err < err_best:
            best, err_best = perm, err
    return best


def _match_surfaces(b: Body, selector: str) -> list[dict]:
    """gmsh surfaces of body `b` that correspond to the B-rep faces selected (in part coordinates)."""
    faces = modeling3d.resolve_faces(b.shape, selector)
    diag = max(b.shape.BoundingBox().DiagonalLength, 1.0)
    out: list[dict] = []
    for f in faces:
        c = f.Center()
        cw, a = b.world([c.x, c.y, c.z]), f.Area()
        best = min(b.md.surfaces, key=lambda s: np.linalg.norm(s["center"] - cw) + abs(s["area"] - a) / max(a, 1e-9))
        if np.linalg.norm(best["center"] - cw) + abs(best["area"] - a) / max(a, 1e-9) > 0.05 * diag:
            raise ValueError(f"could not match face '{selector}' to the mesh")
        if not any(best is o for o in out):
            out.append(best)
    if not out:
        raise ValueError(f"selector '{selector}' matched no face")
    return out


def _surface_items(b: Body, surfs: list[dict]):
    """Global node ids (corner+mid), tri6 rows (global 1-based), element faces [(eid, k)]."""
    tris6 = np.concatenate([s["tris6"] for s in surfs]) + b.node0 + 1
    efaces = []
    for t in np.concatenate([s["tris"] for s in surfs]).tolist():
        hit = b.faces.get(tuple(sorted(t)))
        if hit:
            efaces.append(hit)
    return np.unique(tris6), tris6, efaces


# --------------------------------------------------------------------- deck
def _fmt(x: float) -> str:
    return f"{float(x):.9g}"


def _lines(rows, per=8) -> list[str]:
    rows = list(rows)
    return [", ".join(str(r) for r in rows[i:i + per]) for i in range(0, len(rows), per)]


class Deck:
    def __init__(self):
        self.model: list[str] = ["*HEADING", "cadweb CalculiX job"]
        self.step: list[str] = []
        self.n_sets = 0

    def nset(self, prefix: str, nodes) -> str:
        self.n_sets += 1
        name = f"{prefix}{self.n_sets}"
        self.model.append(f"*NSET, NSET={name}")
        self.model += _lines(int(n) for n in nodes)
        return name

    def surface(self, name: str, efaces) -> str:
        self.model.append(f"*SURFACE, NAME={name}, TYPE=ELEMENT")
        self.model += [f"{e}, S{k}" for e, k in efaces]
        return name

    def text(self) -> str:
        return "\n".join(self.model + self.step) + "\n"


def _write_mesh(deck: Deck, bodies: list[Body]):
    deck.model.append("*NODE, NSET=NALL")
    for b in bodies:
        for i, p in enumerate(b.md.P):
            deck.model.append(f"{b.node0 + i + 1}, {_fmt(p[0])}, {_fmt(p[1])}, {_fmt(p[2])}")
    for bi, b in enumerate(bodies, 1):
        deck.model.append(f"*ELEMENT, TYPE=C3D10, ELSET=B{bi}")
        T = b.md.T10 + b.node0 + 1
        for e, row in enumerate(T.tolist()):
            deck.model.append(f"{b.elem0 + e + 1}, " + ", ".join(str(n) for n in row))
    deck.model.append("*ELSET, ELSET=EALL")
    deck.model += _lines(f"B{i}" for i in range(1, len(bodies) + 1))


def _write_materials(deck: Deck, bodies: list[Body], cfg: dict, thermal: bool):
    plast = cfg.get("plasticity") or {}
    for bi, b in enumerate(bodies, 1):
        m = b.material
        deck.model += [f"*MATERIAL, NAME=M{bi}", "*ELASTIC", f"{_fmt(m['E'])}, {_fmt(m['nu'])}", "*DENSITY", _fmt(m["rho"])]
        if thermal:
            deck.model += ["*CONDUCTIVITY", _fmt(m["k_mw"]), "*SPECIFIC HEAT", _fmt(m["cp"] * 1e6)]
        if plast.get("enabled"):
            Et = float(plast.get("tangent_modulus") or m["E"] * 0.01)
            if not 0 <= Et < m["E"]:
                raise ValueError("tangent modulus must be between 0 and Young's modulus")
            H = m["E"] * Et / (m["E"] - Et)
            sy = float(plast.get("yield_strength") or m["yield"])
            ep_max = float(plast.get("max_plastic_strain") or 0.5)
            deck.model += ["*PLASTIC", f"{_fmt(sy)}, 0.0", f"{_fmt(sy + H * ep_max)}, {_fmt(ep_max)}"]
        deck.model += [f"*SOLID SECTION, ELSET=B{bi}, MATERIAL=M{bi}"]


def _body_for(bodies: list[Body], row: dict, by_comp: dict[int, Body]) -> Body:
    if len(bodies) == 1:
        return bodies[0]
    cid = row.get("component")
    if cid is None or int(cid) not in by_comp:
        raise ValueError("each boundary condition of an assembly analysis needs a component")
    return by_comp[int(cid)]


def _structural_bcs(deck: Deck, bodies: list[Body], by_comp, cfg: dict, amplitude: Optional[str] = None) -> dict:
    """*BOUNDARY / *CLOAD / *DLOAD lines into deck.step; returns bookkeeping for the summary."""
    info = {"fixed_sets": [], "applied": np.zeros(3)}
    rows = []
    for sup in cfg.get("fixed", []):
        b = _body_for(bodies, sup, by_comp)
        nodes, _, _ = _surface_items(b, _match_surfaces(b, sup["face"]))
        name = deck.nset("FIX", nodes)
        dofs = sup.get("dofs", [True, True, True])
        info["fixed_sets"].append((name, [bool(d) for d in dofs]))
        rows += [f"{name}, {d + 1}, {d + 1}" for d in range(3) if dofs[d]]
    if not rows:
        raise ValueError("add at least one fixed support")
    deck.step += ["*BOUNDARY"] + rows
    amp = f", AMPLITUDE={amplitude}" if amplitude else ""
    loads: dict[int, np.ndarray] = {}
    for ld in cfg.get("forces", []):
        b = _body_for(bodies, ld, by_comp)
        _, tris6, _ = _surface_items(b, _match_surfaces(b, ld["face"]))
        P = b.md.P
        corners = tris6[:, :3] - 1 - b.node0
        a = P[corners[:, 1]] - P[corners[:, 0]]
        c = P[corners[:, 2]] - P[corners[:, 0]]
        area = 0.5 * np.linalg.norm(np.cross(a, c), axis=1)
        F = np.array(ld["vector"], dtype=float)
        tvec = F / area.sum()
        for k in range(len(tris6)):  # quadratic triangle, uniform traction: mid-side nodes carry A/3 each
            for n in tris6[k, 3:]:
                loads[int(n)] = loads.get(int(n), np.zeros(3)) + tvec * area[k] / 3.0
        info["applied"] += F
    if loads:
        deck.step.append(f"*CLOAD{amp}")
        for n, v in sorted(loads.items()):
            deck.step += [f"{n}, {d + 1}, {_fmt(v[d])}" for d in range(3) if abs(v[d]) > 0]
    dl = []
    for pr in cfg.get("pressures", []):
        b = _body_for(bodies, pr, by_comp)
        surfs = _match_surfaces(b, pr["face"])
        _, _, efaces = _surface_items(b, surfs)
        dl += [f"{e}, P{k}, {_fmt(pr['value'])}" for e, k in efaces]
        faces = modeling3d.resolve_faces(b.shape, pr["face"])
        if all(f.geomType() == "PLANE" for f in faces):  # exact resultant (mesh chords under-count area at holes)
            na = sum((np.array(f.normalAt(f.Center()).toTuple()) * f.Area() for f in faces), np.zeros(3))
            na = b.M[:3, :3] @ na
        else:
            tris = np.concatenate([s["tris"] for s in surfs])
            na = (native._outward_normals(b.md, tris) * native._tri_areas_normals(b.md.P, tris)[0][:, None]).sum(axis=0)
        info["applied"] += -float(pr["value"]) * na  # positive pressure pushes inward
    grav = cfg.get("gravity") or {}
    if grav.get("enabled"):
        g = np.array(grav.get("vector", [0, 0, -native.G_MM_S2]), dtype=float)
        gm = float(np.linalg.norm(g))
        if gm > 0:
            dl.append(f"EALL, GRAV, {_fmt(gm)}, {_fmt(g[0] / gm)}, {_fmt(g[1] / gm)}, {_fmt(g[2] / gm)}")
            for b in bodies:
                info["applied"] += b.material["rho"] * b.shape.Volume() * g
    if dl:
        deck.step += [f"*DLOAD{amp}"] + dl
    if not loads and not dl:
        raise ValueError("no loads applied: add a force, pressure or enable gravity")
    return info


def _thermal_bcs(deck: Deck, bodies: list[Body], by_comp, cfg: dict):
    rows = []
    for ft in cfg.get("fixed_temps", []):
        b = _body_for(bodies, ft, by_comp)
        nodes, _, _ = _surface_items(b, _match_surfaces(b, ft["face"]))
        rows.append(f"{deck.nset('FT', nodes)}, 11, 11, {_fmt(ft['temperature'])}")
    if rows:
        deck.step += ["*BOUNDARY"] + rows
    flux = []
    for fl in cfg.get("heat_flux", []):  # W/m^2 -> mW/mm^2
        b = _body_for(bodies, fl, by_comp)
        _, _, efaces = _surface_items(b, _match_surfaces(b, fl["face"]))
        flux += [f"{e}, S{k}, {_fmt(float(fl['value']) * 1e-3)}" for e, k in efaces]
    if flux:
        deck.step += ["*DFLUX"] + flux
    film = []
    for cv in cfg.get("convection", []):
        b = _body_for(bodies, cv, by_comp)
        _, _, efaces = _surface_items(b, _match_surfaces(b, cv["face"]))
        film += [f"{e}, F{k}, {_fmt(cv.get('ambient', 20.0))}, {_fmt(float(cv['h']) * 1e-3)}" for e, k in efaces]
    if film:
        deck.step += ["*FILM"] + film
    if not rows and not film and not flux:
        raise ValueError("add a fixed temperature, heat flux or convection boundary condition")


# ---------------------------------------------------------------- contact pairs
def detect_contacts(bodies: list[Body], tol: Optional[float] = None) -> list[dict]:
    """Coincident planar / cylindrical B-rep faces between different bodies (world coordinates)."""
    from . import assembly as asm

    diag = max(b.shape.BoundingBox().DiagonalLength for b in bodies)
    tol = tol or (1e-3 * diag + 0.02)
    world = [b.shape if np.allclose(b.M, np.eye(4)) else asm.apply_pose(b.shape, b.M) for b in bodies]
    faces = [[(fi, f, f.geomType(), f.BoundingBox()) for fi, f in enumerate(w.Faces())] for w in world]
    pairs = []
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    def cyl(f):
        c = BRepAdaptor_Surface(f.wrapped).Cylinder()
        ax = c.Axis()
        return np.array([ax.Location().X(), ax.Location().Y(), ax.Location().Z()]), np.array([ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()]), c.Radius()

    for i in range(len(bodies)):
        for j in range(i + 1, len(bodies)):
            for fa_i, fa, ga, ba in faces[i]:
                for fb_i, fb, gb, bb in faces[j]:
                    if ga != gb or ga not in ("PLANE", "CYLINDER"):
                        continue
                    if ba.xmax + tol < bb.xmin or bb.xmax + tol < ba.xmin or ba.ymax + tol < bb.ymin or bb.ymax + tol < ba.ymin \
                            or ba.zmax + tol < bb.zmin or bb.zmax + tol < ba.zmin:
                        continue
                    if ga == "PLANE":
                        ca, cb = fa.Center(), fb.Center()
                        na, nb = fa.normalAt(ca), fb.normalAt(cb)
                        if na.dot(nb) > -0.999 or abs((cb - ca).dot(na)) > tol:
                            continue
                        # overlapping footprint: the intersection of the bounding boxes must have area
                        ov = [min(ba.xmax, bb.xmax) - max(ba.xmin, bb.xmin), min(ba.ymax, bb.ymax) - max(ba.ymin, bb.ymin),
                              min(ba.zmax, bb.zmax) - max(ba.zmin, bb.zmin)]
                        if sorted(ov)[1] <= tol:
                            continue
                    else:
                        pa, da, ra = cyl(fa)
                        pb, db, rb = cyl(fb)
                        if abs(abs(da @ db) - 1) > 1e-4 or abs(ra - rb) > max(tol, 0.01 * max(ra, rb)):
                            continue
                        off = (pb - pa) - da * ((pb - pa) @ da)
                        if np.linalg.norm(off) > tol:
                            continue
                    pairs.append({"a": i, "b": j, "a_face": fa_i, "b_face": fb_i, "kind": ga.lower(),
                                  "area": min(fa.Area(), fb.Area())})
    return pairs


def _write_contacts(deck: Deck, bodies: list[Body], cfg: dict) -> list[dict]:
    from . import assembly as asm

    pairs = detect_contacts(bodies) if cfg.get("contacts", "auto") == "auto" else []
    for row in cfg.get("contact_pairs", []) or []:  # manual pairs: {a:{component,face}, b:{component,face}, type, friction}
        pairs.append({"manual": row})
    if not pairs:
        raise ValueError("no touching faces found between the components: mate them first, or define contact pairs")
    mu = float(cfg.get("friction", 0.2))
    default_type = cfg.get("contact_type", "contact")
    E_min = min(b.material["E"] for b in bodies)
    used = []
    for k, pr in enumerate(pairs, 1):
        if "manual" in pr:
            row = pr["manual"]
            by_comp = cfg["_by_comp"]
            ba, bb = by_comp[int(row["a"]["component"])], by_comp[int(row["b"]["component"])]
            sa, sb = _match_surfaces(ba, row["a"]["face"]), _match_surfaces(bb, row["b"]["face"])
            typ, fr = row.get("type", default_type), float(row.get("friction", mu))
        else:
            ba, bb = bodies[pr["a"]], bodies[pr["b"]]
            wa = ba.shape if np.allclose(ba.M, np.eye(4)) else asm.apply_pose(ba.shape, ba.M)
            wb = bb.shape if np.allclose(bb.M, np.eye(4)) else asm.apply_pose(bb.shape, bb.M)
            fa, fb = wa.Faces()[pr["a_face"]], wb.Faces()[pr["b_face"]]
            sa, sb = [_nearest_surface(ba, fa)], [_nearest_surface(bb, fb)]
            typ, fr = default_type, mu
        _, _, ea = _surface_items(ba, sa)
        _, _, eb = _surface_items(bb, sb)
        if not ea or not eb:
            continue
        area_a, area_b = sum(s["area"] for s in sa), sum(s["area"] for s in sb)
        (slave, master) = (ea, eb) if area_a <= area_b else (eb, ea)
        s_name, m_name = deck.surface(f"CS{k}", slave), deck.surface(f"CM{k}", master)
        if typ == "bonded":
            deck.model += [f"*TIE, NAME=T{k}, ADJUST=NO", f"{s_name}, {m_name}"]
        else:
            K = 20.0 * E_min
            deck.model += [f"*CONTACT PAIR, INTERACTION=SI{k}, TYPE=SURFACE TO SURFACE", f"{s_name}, {m_name}",
                           f"*SURFACE INTERACTION, NAME=SI{k}", "*SURFACE BEHAVIOR, PRESSURE-OVERCLOSURE=LINEAR",
                           f"{_fmt(K)}, 0.1"]
            if fr > 0:
                deck.model += ["*FRICTION", f"{_fmt(fr)}, {_fmt(K)}"]
        used.append({"pair": k, "type": typ, "friction": fr, "bodies": [ba.name, bb.name], "kind": pr.get("kind", "manual")})
    if not used:
        raise ValueError("contact faces could not be matched to the mesh")
    return used


def _nearest_surface(b: Body, face) -> dict:
    c = face.Center()
    cw, a = np.array([c.x, c.y, c.z]), face.Area()
    return min(b.md.surfaces, key=lambda s: np.linalg.norm(s["center"] - cw) + abs(s["area"] - a) / max(a, 1e-9))


# ------------------------------------------------------------------- results
def _display_mesh(bodies: list[Body]):
    P = np.concatenate([b.md.P for b in bodies])
    T = np.concatenate([b.md.T + b.node0 for b in bodies])
    surfaces = [{**s, "tris": s["tris"] + b.node0} for b in bodies for s in b.md.surfaces]
    return native.MeshData(P, T, surfaces)


def _von_mises(S: np.ndarray) -> np.ndarray:
    sxx, syy, szz, sxy, syz, szx = (S[:, i] for i in range(6))
    return np.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2) + 3 * (sxy ** 2 + syz ** 2 + szx ** 2))


def _last(blocks: list[FrdBlock], name: str) -> Optional[FrdBlock]:
    for b in reversed(blocks):
        if b.name == name:
            return b
    return None


def _structural_result(blocks, md, N, diag, extra_fields: Optional[dict] = None):
    disp_b, s_b = _last(blocks, "DISP"), _last(blocks, "STRESS")
    if disp_b is None:
        raise RuntimeError("CalculiX produced no displacement results (did the analysis converge?)")
    U = _field(disp_b, N, 3)
    disp = np.linalg.norm(U, axis=1)
    fields = {"displacement": disp}
    vm = None
    if s_b is not None:
        vm = _von_mises(_field(s_b, N, 6))
        fields = {"von_mises": vm, **fields}
    fields.update(extra_fields or {})
    payload = native._surface_payload(md, fields, U)
    dmax = float(disp.max())
    payload["suggested_deformation_scale"] = float(0.05 * diag / dmax) if dmax > 0 else 1.0
    return payload, U, disp, vm


# ----------------------------------------------------------------------- run
def _material(name: str) -> dict:
    m = native._material_si(name)
    raw = modeling3d.material(name)
    return {**m, "k_mw": raw["k"], "cp": raw["cp"]}


def run(kind: str, bodies_in: list[dict], cfg: dict, max_elements: int, timeout: float = 900.0) -> dict:
    """bodies_in: [{name, shape (part-local), matrix (4x4 or None), material, component}]"""
    if kind not in KINDS:
        raise ValueError(f"unknown CalculiX analysis '{kind}'")
    t0 = time.time()
    bodies = [Body(b["name"], b["shape"], np.array(b.get("matrix") if b.get("matrix") is not None else np.eye(4), dtype=float),
                   _material(b["material"])) for b in bodies_in]
    by_comp = {int(b["component"]): body for b, body in zip(bodies_in, bodies) if b.get("component") is not None}
    cfg = {**cfg, "_by_comp": by_comp}
    size = _mesh_bodies(bodies, float(cfg.get("mesh_size") or 0) or None, max_elements)
    N = sum(len(b.md.P) for b in bodies)
    md = _display_mesh(bodies)
    diag = max(b.shape.BoundingBox().DiagonalLength for b in bodies)
    deck = Deck()
    _write_mesh(deck, bodies)
    thermal = kind == "ccx_thermal_transient"
    _write_materials(deck, bodies, cfg, thermal)
    contacts = _write_contacts(deck, bodies, cfg) if kind == "ccx_contact" else []

    summary: dict[str, Any] = {}
    res: dict[str, Any] = {"kind": kind}
    plastic = bool((cfg.get("plasticity") or {}).get("enabled"))
    if kind in ("ccx_static", "ccx_contact"):
        nl = ", NLGEOM" if cfg.get("nlgeom") else ""
        inc = max(1, int(cfg.get("increments", 10 if (plastic or kind == "ccx_contact" or nl) else 1)))
        deck.step += [f"*STEP{nl}, INC={max(100, inc * 20)}", "*STATIC", f"{_fmt(1.0 / inc)}, 1.0, {_fmt(1e-5 / inc)}, {_fmt(1.0 / inc)}"]
        info = _structural_bcs(deck, bodies, by_comp, cfg)
        deck.step += ["*NODE FILE", "U", "*EL FILE", "S" + (", PEEQ" if plastic else "")]
        if kind == "ccx_contact":
            deck.step += ["*CONTACT FILE", "CDIS, CSTR"]
        for s, _ in info["fixed_sets"]:
            deck.step += [f"*NODE PRINT, NSET={s}, TOTALS=ONLY", "RF"]
        deck.step.append("*END STEP")
        dat, frd, _ = run_ccx(deck.text(), timeout)
        blocks = parse_frd(frd)
        extra = {}
        pe = _last(blocks, "PE")
        if plastic and pe is not None:
            extra["plastic_strain"] = _field(pe, N, 1)[:, 0]
        cs = _last(blocks, "CONTACT")
        if cs is not None and len(cs.comps) >= 4:
            extra["contact_pressure"] = _field(cs, N, len(cs.comps))[:, 3]
        payload, U, disp, vm = _structural_result(blocks, md, N, diag, extra)
        reaction = np.zeros(3)
        for s, dofs in info["fixed_sets"]:
            r = parse_totals(dat, s)
            if r:
                reaction += np.array(r) * np.array(dofs, dtype=float)  # free directions carry applied loads, not reactions
        ymin = min(b.material["yield"] for b in bodies)
        vmax = float(vm.max()) if vm is not None else None
        summary = {
            "max_von_mises_mpa": vmax, "max_displacement_mm": float(disp.max()),
            "yield_strength_mpa": ymin, "safety_factor": (ymin / vmax) if vmax else None,
            "reaction_force_n": reaction.tolist(), "applied_load_n": info["applied"].tolist(),
            "equilibrium_error_n": float(np.linalg.norm(reaction + info["applied"])),
            "nonlinear_geometry": bool(cfg.get("nlgeom")), "plasticity": plastic,
            "load_increments_written": len({b.time for b in blocks if b.name == "DISP"}),
        }
        if "plastic_strain" in extra:
            summary["max_plastic_strain"] = float(extra["plastic_strain"].max())
            summary["yielded"] = bool(extra["plastic_strain"].max() > 0)
        if "contact_pressure" in extra:
            summary["max_contact_pressure_mpa"] = float(np.abs(extra["contact_pressure"]).max())
        if contacts:
            summary["contact_pairs"] = contacts
        res["surface"] = payload
    elif kind in ("ccx_buckling", "ccx_frequency"):
        n = int(cfg.get("modes", 4))
        if kind == "ccx_buckling":
            deck.step += ["*STEP", "*BUCKLE", f"{n}, 1e-6, {max(4 * n, n + 20)}, 2000"]  # tight ARPACK tolerance: no spurious modes
            _structural_bcs(deck, bodies, by_comp, cfg)
        else:
            deck.step += ["*STEP", "*FREQUENCY", str(n)]
            rows = []
            for sup in cfg.get("fixed", []):
                b = _body_for(bodies, sup, by_comp)
                nodes, _, _ = _surface_items(b, _match_surfaces(b, sup["face"]))
                name = deck.nset("FIX", nodes)
                rows += [f"{name}, {d + 1}, {d + 1}" for d in range(3) if sup.get("dofs", [True] * 3)[d]]
            if rows:
                deck.step += ["*BOUNDARY"] + rows
        deck.step += ["*NODE FILE", "U", "*END STEP"]
        dat, frd, _ = run_ccx(deck.text(), timeout)
        blocks = [b for b in parse_frd(frd) if b.name == "DISP"]
        values = parse_buckling_factors(dat) if kind == "ccx_buckling" else parse_frequencies(dat)
        if values and len(blocks) > len(values):
            blocks = blocks[-len(values):]  # buckling writes the reference (pre-load) solution first
        modes = []
        for i, b in enumerate(blocks[: min(len(blocks), 6)]):
            U = _field(b, N, 3)
            mag = np.linalg.norm(U, axis=1)
            scale = 0.08 * diag / (mag.max() or 1.0)
            entry = {"surface": native._surface_payload(md, {"displacement": mag / (mag.max() or 1.0)}, U * scale)}
            if i < len(values):
                entry["buckling_factor" if kind == "ccx_buckling" else "frequency_hz"] = values[i]
            modes.append(entry)
        if kind == "ccx_buckling":
            summary = {"buckling_factors": values, "critical_factor": min((v for v in values if v > 0), default=None),
                       "note": "critical load = factor x applied load"}
        else:
            summary = {"frequencies_hz": values}
        res["modes"] = modes
    elif kind == "ccx_thermal_transient":
        total = float(cfg.get("time_period", 60.0))
        dt = float(cfg.get("time_step", total / 50))
        nstep = max(1, int(round(total / dt)))
        deck.model += ["*INITIAL CONDITIONS, TYPE=TEMPERATURE", f"NALL, {_fmt(cfg.get('initial_temperature', 20.0))}"]
        deck.step += [f"*STEP, INC={nstep + 10}", "*HEAT TRANSFER, DIRECT", f"{_fmt(dt)}, {_fmt(total)}"]
        _thermal_bcs(deck, bodies, by_comp, cfg)
        deck.step += [f"*NODE FILE, FREQUENCY={max(1, nstep // 40)}", "NT", "*END STEP"]
        dat, frd, _ = run_ccx(deck.text(), timeout)
        blocks = [b for b in parse_frd(frd) if b.name == "NDTEMP"]
        if not blocks:
            raise RuntimeError("CalculiX produced no temperature results")
        hist = []
        for b in blocks:
            Tn = _field(b, N, 1)[:, 0]
            hist.append({"time_s": b.time, "min_c": float(Tn.min()), "max_c": float(Tn.max()), "mean_c": float(Tn.mean())})
        Tf = _field(blocks[-1], N, 1)[:, 0]
        summary = {"min_temperature_c": float(Tf.min()), "max_temperature_c": float(Tf.max()), "time_s": blocks[-1].time}
        res["surface"] = native._surface_payload(md, {"temperature": Tf})
        res["history"] = hist
    elif kind == "ccx_dynamic":
        total = float(cfg.get("time_period", 0.01))
        dt = float(cfg.get("time_step", total / 100))
        nstep = max(1, int(round(total / dt)))
        prof = cfg.get("load_profile", "step")
        deck.model += ["*AMPLITUDE, NAME=A1", "0.0, 1.0, 1.0e9, 1.0" if prof == "step" else f"0.0, 0.0, {_fmt(total)}, 1.0"]
        deck.step += [f"*STEP, INC={nstep + 10}", "*DYNAMIC, DIRECT, ALPHA=-0.05", f"{_fmt(dt)}, {_fmt(total)}"]
        _structural_bcs(deck, bodies, by_comp, cfg, amplitude="A1")
        deck.step += [f"*NODE FILE, FREQUENCY={max(1, nstep // 60)}", "U", "*EL FILE, FREQUENCY=" + str(max(1, nstep // 60)), "S", "*END STEP"]
        dat, frd, _ = run_ccx(deck.text(), timeout)
        blocks = parse_frd(frd)
        disp_blocks = [b for b in blocks if b.name == "DISP"]
        if not disp_blocks:
            raise RuntimeError("CalculiX produced no displacement results")
        hist = []
        peak_i, peak = 0, -1.0
        for i, b in enumerate(disp_blocks):
            d = float(np.linalg.norm(_field(b, N, 3), axis=1).max())
            hist.append({"time_s": b.time, "max_displacement_mm": d})
            if d > peak:
                peak_i, peak = i, d
        # show the instant of peak deflection
        t_peak = disp_blocks[peak_i].time
        at_peak = [b for b in blocks if abs(b.time - t_peak) < 1e-12]
        payload, U, disp, vm = _structural_result(at_peak, md, N, diag)
        summary = {"peak_displacement_mm": peak, "peak_time_s": t_peak,
                   "max_von_mises_at_peak_mpa": float(vm.max()) if vm is not None else None, "load_profile": prof}
        res["surface"] = payload
        res["history"] = hist
    res["summary"] = summary
    res["mesh"] = {"nodes": int(N), "elements": int(sum(len(b.md.T) for b in bodies)), "element_size_mm": float(size),
                   "element_type": "C3D10", "bodies": len(bodies)}
    res["material"] = {"name": bodies_in[0]["material"], **{k: bodies[0].material[k] for k in ("E", "nu", "yield", "label")}}
    res["solver"] = {"name": "CalculiX", "command": solver_command()[1]}
    res["solve_time_s"] = round(time.time() - t0, 3)
    return res
