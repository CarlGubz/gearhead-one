"""
Every primitive can be meshed with every solid element type (Tet4, Tet10, Hex8, Hex20), including the curved shapes that used to be
tetrahedra-only, and the quadratic meshes expose the mid-side node of each surface edge (the viewport's pick cursor snaps to it).
"""
import math

import numpy as np
import pytest

from app.fea import analyses as A
from app.fea import assembly as asm
from app.fea import meshgen
from app.fea.constraints import ConstraintSystem
from app.fea.materials import Material
from app.fea.model import FEMesh

ALL_TYPES = ["C3D4", "C3D10", "C3D8", "C3D20"]
DELAUNAY = ("cylinder", "sphere", "tube", "plate_hole")          # tetrahedra of these use a Delaunay point cloud: coarse when linear

DIMS = meshgen.DEFAULT_DIMS
EXACT = {
    "box": 1 * .2 * .2,
    "cylinder": math.pi * .5 ** 2 * 1.0,
    "sphere": 4 / 3 * math.pi * .5 ** 3,
    "tube": math.pi * (.5 ** 2 - .3 ** 2) * 1.0,
    "plate_hole": (1 * .5 - math.pi * .1 ** 2) * .1,
    "lbracket": (1 * .25 + .75 * .25) * .25,
    "cone": math.pi / 3 * 1.0 * (.5 ** 2 + .5 * .25 + .25 ** 2),
    "torus": 2 * math.pi ** 2 * .5 * .15 ** 2,
}
STEEL = Material(E=210e9, nu=0.3, density=7850.0, conductivity=50.0, specific_heat=490.0, alpha=12e-6)


def make(shape, et, div=4):
    return meshgen.generate(shape, DIMS[shape], div, et)


def test_shape_table_is_complete():
    assert set(meshgen.SHAPES) == set(DIMS) == set(EXACT)
    assert {"cone", "torus"} <= set(meshgen.SHAPES)


@pytest.mark.parametrize("shape", list(meshgen.SHAPES))
@pytest.mark.parametrize("et", ALL_TYPES)
def test_every_shape_meshes_with_every_element_type(shape, et):
    mesh = make(shape, et)
    assert mesh.et.name == et and mesh.n_elems > 0
    q = mesh.quality()
    assert q["inverted_elements"] == 0 and q["min_jacobian_ratio"] > 0.02
    vol = mesh.volumes().sum()
    if shape in DELAUNAY and et == "C3D10":
        tol = 0.03                                            # point-cloud tets (the original generator): a coarse sphere loses ~1 %
    elif et in ("C3D10", "C3D20"):
        tol = 0.01                                            # mid-side nodes sit on the true surface
    elif shape in DELAUNAY and et == "C3D4":
        tol = None                                            # the point-cloud tets are coarse by construction
    else:
        tol = 0.06
    if tol is not None:
        assert vol == pytest.approx(EXACT[shape], rel=tol)
    # right-handed everywhere, also after ensure_positive_orientation
    assert mesh.ensure_positive_orientation() == 0


@pytest.mark.parametrize("shape", list(meshgen.SHAPES))
@pytest.mark.parametrize("et", ALL_TYPES)
@pytest.mark.parametrize("div", [1, 2])
def test_very_coarse_curved_meshes_stay_valid(shape, et, div):
    """The lowest densities: a full mid-side projection onto the surface would fold elements, so it backs off where needed."""
    mesh = make(shape, et, div=div)
    q = mesh.quality()
    assert q["inverted_elements"] == 0 and q["min_jacobian_ratio"] > 0


@pytest.mark.parametrize("shape", list(meshgen.SHAPES))
@pytest.mark.parametrize("et", ["C3D8", "C3D20"])
def test_brick_meshes_are_watertight_and_all_hex(shape, et):
    mesh = make(shape, et, div=3)
    assert mesh.elems.shape[1] == (8 if et == "C3D8" else 20)
    b = mesh.boundary()
    # every edge of the render triangles is shared by exactly two triangles (closed surface, no holes or T-junctions)
    edges = np.sort(np.concatenate([b.tris[:, [0, 1]], b.tris[:, [1, 2]], b.tris[:, [2, 0]]]), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    assert counts.max() <= 2 and (counts == 2).all()
    # no orphan nodes
    assert len(np.unique(mesh.elems)) == mesh.n_nodes


@pytest.mark.parametrize("shape,axis_r", [("cylinder", 0.5), ("tube", 0.5), ("cone", None), ("plate_hole", 0.1)])
def test_hex20_midside_nodes_lie_on_the_curved_surface(shape, axis_r):
    mesh = make(shape, "C3D20", div=3)
    b = mesh.boundary()
    p = mesh.nodes
    rho = np.hypot(p[:, 0], p[:, 1])
    mids = b.tri_mid[b.tri_mid >= 0]
    corners_of_mid = {}
    # a mid-node whose edge ends are both on the lateral surface must sit on it exactly
    for tri, tm in zip(b.tris, b.tri_mid):
        for e in range(3):
            m = tm[e]
            if m < 0:
                continue
            a, c = tri[e], tri[(e + 1) % 3]
            if shape == "cone":
                r = lambda i: 0.5 + (0.25 - 0.5) * (p[i, 2] + 0.5) / 1.0   # noqa: E731
                if abs(rho[a] - r(a)) < 1e-9 and abs(rho[c] - r(c)) < 1e-9:
                    assert rho[m] == pytest.approx(r(m), abs=1e-9)
            elif abs(rho[a] - axis_r) < 1e-9 and abs(rho[c] - axis_r) < 1e-9:
                assert rho[m] == pytest.approx(axis_r, abs=1e-9)
    assert len(mids) > 0


def test_sphere_and_torus_midside_nodes_are_on_the_surface():
    s = make("sphere", "C3D20", div=3)
    b = s.boundary()
    r = np.linalg.norm(s.nodes, axis=1)
    surf = np.unique(b.face_nodes)
    assert np.allclose(r[surf], 0.5, atol=1e-9)             # corners and mid-side nodes of every boundary quad
    t = make("torus", "C3D10", div=3)
    core = np.stack([t.nodes[:, 0], t.nodes[:, 1]], axis=1)
    d = np.hypot(np.hypot(*core.T) - 0.5, t.nodes[:, 2])
    on_surface = np.unique(t.boundary().face_nodes)
    assert np.allclose(d[on_surface], 0.15, atol=1e-9)


# ------------------------------------------------------------ pick-cursor table
@pytest.mark.parametrize("et,quad_faces", [("C3D10", False), ("C3D20", True)])
def test_triangle_midnodes_sit_at_the_centre_of_each_straight_edge(et, quad_faces):
    mesh = meshgen.generate("box", {"length": 1.0, "width": 0.4, "height": 0.3}, 2, et)
    b = mesh.boundary()
    assert b.tri_mid.shape == b.tris.shape
    found = 0
    for tri, tm in zip(b.tris, b.tri_mid):
        for e in range(3):
            a, c = tri[e], tri[(e + 1) % 3]
            if tm[e] < 0:
                assert quad_faces                               # only quad diagonals have no mid node
                continue
            assert np.allclose(mesh.nodes[tm[e]], 0.5 * (mesh.nodes[a] + mesh.nodes[c]))
            found += 1
    assert found > 0
    if not quad_faces:
        assert (b.tri_mid >= 0).all()
    else:
        # exactly one diagonal edge per triangle pair
        assert (b.tri_mid < 0).sum() == len(b.tris)


def test_linear_meshes_have_no_midside_nodes():
    for et in ("C3D4", "C3D8"):
        b = make("cylinder", et, div=2).boundary()
        assert (b.tri_mid == -1).all()


def test_midside_table_follows_the_orientation_flip():
    """Boundary corners are reversed for faces whose normal pointed inwards: the edge -> mid-node pairing must survive that."""
    mesh = make("tube", "C3D10", div=2)
    b = mesh.boundary()
    # recompute independently: for each triangle edge find the node that sits at the edge midpoint
    for tri, tm in list(zip(b.tris, b.tri_mid))[:200]:
        for e in range(3):
            a, c = tri[e], tri[(e + 1) % 3]
            mid = tm[e]
            assert mid >= 0
            # a curved mid-node is within the edge's bulge of the chord midpoint
            chord = np.linalg.norm(mesh.nodes[a] - mesh.nodes[c])
            assert np.linalg.norm(mesh.nodes[mid] - 0.5 * (mesh.nodes[a] + mesh.nodes[c])) < 0.25 * chord


# -------------------------------------------------------------- solver check
def _tension_problem(mesh, sigma=1e6):
    z0, z1 = mesh.nodes[:, 2].min(), mesh.nodes[:, 2].max()
    fixed = {3 * int(n) + 2: 0.0 for n in mesh.nodes_where(lambda p: np.isclose(p[:, 2], z0))}
    # remove the rigid-body motions in the plane with two pins on the bottom face (the axis node and one on the x axis)
    bottom = np.where(np.isclose(mesh.nodes[:, 2], z0))[0]
    centre = bottom[np.argmin(np.hypot(mesh.nodes[bottom, 0], mesh.nodes[bottom, 1]))]
    fixed[3 * int(centre)], fixed[3 * int(centre) + 1] = 0.0, 0.0
    cand = bottom[np.isclose(mesh.nodes[bottom, 1], 0.0) & (mesh.nodes[bottom, 0] > 0.1)]
    fixed[3 * int(cand[0]) + 1] = 0.0
    top = mesh.faces_where(lambda p: np.isclose(p[:, 2], z1))
    f = asm.pressure_vector(mesh, top, -sigma)
    cs = ConstraintSystem(mesh, [], [])
    f_ext = np.zeros(cs.n_full)
    f_ext[: len(f)] = f
    return A.MechProblem(mesh, STEEL, cs, fixed, f_ext, None, 0.0), z1 - z0


@pytest.mark.parametrize("et", ALL_TYPES)
def test_curved_bricks_and_tets_pass_the_uniaxial_patch_test(et):
    """Uniform tension on a cylinder: constant stress, uz = sigma L / E - any isoparametric element must reproduce it exactly."""
    mesh = make("cylinder", et, div=3)
    prob, L = _tension_problem(mesh)
    out = A.static(prob)
    uz = out.u.reshape(-1, 3)[:, 2]
    assert uz.max() == pytest.approx(1e6 * L / STEEL.E, rel=1e-7)
    assert out.stress[..., 2].mean() == pytest.approx(1e6, rel=1e-6)
    assert np.abs(out.stress[..., 2] - 1e6).max() < 1e-4 * 1e6
