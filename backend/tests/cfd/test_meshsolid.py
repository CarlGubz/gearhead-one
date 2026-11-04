"""Imported solids ("mesh" obstacles): rasterisation, backends, decimation."""
import numpy as np

from app.cfd.schemas import Boundaries, Boundary, CaseConfig, Domain, MeshSettings, Obstacle
from app.cfd.solver import meshsolid
from app.cfd.solver.backends.openfoam import OpenFOAMCase
from app.cfd.solver.grid3d import build_grid_nd


def box_surface(lx, ly, lz):
    V = np.array([[x, y, z] for x in (-lx / 2, lx / 2) for y in (-ly / 2, ly / 2) for z in (-lz / 2, lz / 2)])
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    T = [t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))]
    return V, np.array(T)


def sphere_surface(r, n=24):
    th, ph = np.linspace(0, np.pi, n), np.linspace(0, 2 * np.pi, 2 * n, endpoint=False)
    V = [[0, 0, r]] + [[r * np.sin(t) * np.cos(p), r * np.sin(t) * np.sin(p), r * np.cos(t)] for t in th[1:-1] for p in ph] + [[0, 0, -r]]
    m = len(ph)
    T = [(0, 1 + k, 1 + (k + 1) % m) for k in range(m)]
    for i in range(len(th) - 3):
        for k in range(m):
            a, b = 1 + i * m + k, 1 + i * m + (k + 1) % m
            T += [(a, a + m, b), (b, a + m, b + m)]
    last = len(V) - 1
    base = 1 + (len(th) - 3) * m
    T += [(base + k, last, base + (k + 1) % m) for k in range(m)]
    return np.array(V), np.array(T)


def case_with(ob, dim="3d"):
    return CaseConfig(dimension=dim, domain=Domain(width=1, height=1, depth=1), mesh=MeshSettings(nx=40, ny=40, nz=40),
                      obstacles=[ob], boundaries=Boundaries(left=Boundary(type="inlet", velocity=1), right=Boundary(type="outlet")))


def test_mesh_box_rasterises_like_the_rectangle_obstacle():
    V, T = box_surface(0.3, 0.2, 0.4)
    mesh = Obstacle(id="m", shape="mesh", x=0.5, y=0.5, z=0.5, vertices=V.tolist(), triangles=T.tolist())
    rect = Obstacle(id="r", shape="rectangle", x=0.5, y=0.5, z=0.5, width=0.3, height=0.2, depth=0.4)
    a, b = build_grid_nd(case_with(mesh)).solid, build_grid_nd(case_with(rect)).solid
    assert a.sum() > 0 and (a == b).all()


def test_mesh_sphere_matches_the_analytic_sphere():
    V, T = sphere_surface(0.25, 40)
    mesh = Obstacle(id="m", shape="mesh", x=0.5, y=0.5, z=0.5, vertices=V.tolist(), triangles=T.tolist())
    sph = Obstacle(id="s", shape="sphere", x=0.5, y=0.5, z=0.5, radius=0.25)
    a, b = build_grid_nd(case_with(mesh)).solid, build_grid_nd(case_with(sph)).solid
    assert abs(int(a.sum()) - int(b.sum())) / b.sum() < 0.03       # polygonal sphere is slightly smaller
    # 2-D: the section through the centre is the circle
    a2, b2 = build_grid_nd(case_with(mesh, "2d")).solid, build_grid_nd(case_with(sph, "2d")).solid
    assert abs(int(a2.sum()) - int(b2.sum())) / b2.sum() < 0.03


def test_openfoam_selects_the_mesh_obstacle_with_boxes_on_cell_faces():
    V, T = box_surface(0.3, 0.2, 0.4)
    case = case_with(Obstacle(id="m", shape="mesh", x=0.5, y=0.5, z=0.5, vertices=V.tolist(), triangles=T.tolist()))
    src = OpenFOAMCase(case)._source(case.obstacles[0])
    assert src.startswith("source boxToCell; boxes (")


def test_decimation_keeps_the_solid():
    V, T = sphere_surface(0.25, 60)
    V2, T2 = meshsolid.decimate(V, T, 1500)
    assert len(T2) <= 1500 < len(T)
    xs = (np.arange(40) + 0.5) / 40 - 0.5
    full = meshsolid.inside_axes(V, T, xs, xs, xs).sum()
    coarse = meshsolid.inside_axes(V2, T2, xs, xs, xs).sum()
    assert abs(int(full) - int(coarse)) / full < 0.05
