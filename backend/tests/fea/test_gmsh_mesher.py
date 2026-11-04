"""
The Gmsh mesher (fea/gmsh_options.py + gmsh_worker.py + cad.py): option validation, every primitive as Tet4 / Tet10 / Hex8 / Hex20,
the node-order conversions, size fields, optimisers and reading existing .msh files. The geometry kernel runs in a subprocess, so
these tests exercise exactly what the API does.
"""
import math

import numpy as np
import pytest

from app.fea import gmsh_options as go
from app.fea import meshgen
from app.fea.model import FEMesh

DIMS = meshgen.DEFAULT_DIMS
EXACT = {
    "box": 1 * .2 * .2, "cylinder": math.pi * .25, "sphere": 4 / 3 * math.pi * .125, "tube": math.pi * (.25 - .09),
    "plate_hole": (0.5 - math.pi * .01) * .1, "lbracket": (1 * .25 + .75 * .25) * .25,
    "cone": math.pi / 3 * (.25 + .125 + .0625), "torus": 2 * math.pi ** 2 * .5 * .15 ** 2,
}

# ---------------------------------------------------------------- options (no gmsh needed)


def test_defaults_and_element_types():
    o = go.normalize(None)
    assert o["family"] == "tet" and o["order"] == 2 and o["algorithm_3d"] == 1 and o["regions"] == []
    assert go.element_type_of(o) == "C3D10"
    for fam, order, name in [("tet", 1, "C3D4"), ("tet", 2, "C3D10"), ("hex", 1, "C3D8"), ("hex", 2, "C3D20")]:
        assert go.element_type_of(go.normalize({"family": fam, "order": order})) == name


@pytest.mark.parametrize("bad,msg", [
    ({"family": "prism"}, "family"), ({"order": 3}, "order"), ({"order": 1.5}, "order"), ({"size": -1}, "size"),
    ({"algorithm_2d": 9}, "algorithm_2d"), ({"algorithm_2d": 7}, "algorithm_2d"), ({"algorithm_3d": 2}, "algorithm_3d"),
    ({"hex_method": "magic"}, "hex_method"), ({"optimize": "x"}, "optimize"), ({"refine": 5}, "refine"),
    ({"size_factor": 0}, "size_factor"), ({"curvature_points": 99}, "curvature_points"), ({"bogus": 1}, "Unknown"),
    ({"size": "abc"}, "size"), ({"size": float("nan")}, "size"), ({"size": 0.1, "min_size": 0.5}, "min_size"),
    ({"regions": [{"shape": "cone"}]}, "shape"), ({"regions": [{"shape": "ball", "center": [0, 0, 0], "radius": 1}]}, "size_in"),
    ({"regions": [{"shape": "ball", "center": [0, 0], "radius": 1, "size_in": .1}]}, "center"),
    ({"regions": [{"shape": "box", "min": [1, 0, 0], "max": [0, 1, 1], "size_in": .1}]}, "min"),
    ({"regions": [{"shape": "ball", "center": [0, 0, 0], "radius": 0, "size_in": .1}]}, "radius"),
    ({"regions": [{"shape": "ball", "center": [0, 0, 0], "radius": 1, "size_in": .1}] * 9}, "regions"),
])
def test_invalid_options_are_rejected_with_a_message(bad, msg):
    with pytest.raises(go.OptionError) as e:
        go.normalize(bad)
    assert msg in str(e.value)


def test_catalogue_lists_every_choice():
    c = go.catalogue()
    assert {a["id"] for a in c["algorithms_3d"]} == {1, 4, 7, 9, 10}
    assert {a["id"] for a in c["recombine_algorithms"]} == {0, 1, 2, 3}
    assert c["defaults"]["family"] == "tet" and c["max_regions"] == go.MAX_REGIONS


# ------------------------------------------------------------------ meshing (needs gmsh)
gmsh = pytest.importorskip("gmsh")
from app.fea import cad  # noqa: E402


def mesh_of(shape, div=4, **opts):
    r = cad.mesh_primitive(shape, DIMS[shape], div, opts)
    m = FEMesh(r.nodes, r.elems, r.element_type)
    assert m.ensure_positive_orientation() >= 0
    return r, m


def test_gmsh_is_reported_available():
    assert cad.available() and cad.version()


@pytest.mark.parametrize("shape", list(meshgen.SHAPES))
@pytest.mark.parametrize("family", ["tet", "hex"])
def test_every_primitive_as_quadratic_tet_and_hex(shape, family):
    r, m = mesh_of(shape, family=family, order=2)
    assert r.element_type == ("C3D20" if family == "hex" else "C3D10")
    assert len(r.ends) == 1 and r.ends[-1] == m.n_elems
    assert m.volumes().sum() == pytest.approx(EXACT[shape], rel=0.005)
    q = m.quality()
    assert q["inverted_elements"] == 0 and q["min_jacobian_ratio"] > 0.02


@pytest.mark.parametrize("shape", ["box", "cylinder", "tube", "plate_hole", "lbracket"])
def test_sweepable_shapes_are_swept_into_bricks(shape):
    r, _ = mesh_of(shape, family="hex", order=1)
    assert r.info["method"] == "sweep" and r.element_type == "C3D8"


@pytest.mark.parametrize("shape", ["sphere", "cone", "torus"])
def test_other_shapes_fall_back_to_subdividing_tets(shape):
    r, _ = mesh_of(shape, family="hex", order=1)
    assert r.info["method"] == "subdivide"


def test_sweeping_a_sphere_is_refused_and_subdividing_a_box_is_allowed():
    with pytest.raises(cad.CadError, match="cannot be swept"):
        cad.mesh_primitive("sphere", DIMS["sphere"], 4, {"family": "hex", "hex_method": "sweep"})
    r, m = mesh_of("box", family="hex", order=1, hex_method="subdivide")
    assert r.info["method"] == "subdivide" and m.volumes().sum() == pytest.approx(EXACT["box"], rel=1e-9)


@pytest.mark.parametrize("et", ["C3D10", "C3D20"])
def test_midside_nodes_are_in_abaqus_order(et):
    """On a straight-edged box every mid-side node must be at the centre of the edge its slot names (tests the Gmsh -> Abaqus permutation)."""
    r, m = mesh_of("box", family="hex" if et == "C3D20" else "tet", order=2, div=2)
    edges = meshgen._HEX_EDGES if et == "C3D20" else meshgen._TET_EDGES
    nc = m.et.n_corner
    for k, (i, j) in enumerate(edges):
        a, b, mid = m.nodes[m.elems[:, i]], m.nodes[m.elems[:, j]], m.nodes[m.elems[:, nc + k]]
        assert np.allclose(mid, 0.5 * (a + b), atol=1e-9), f"mid-side slot {k} is out of order"


def test_algorithms_all_produce_a_valid_mesh():
    for a3 in (1, 4, 7, 9, 10):
        r, m = mesh_of("cylinder", family="tet", order=1, algorithm_3d=a3)
        assert m.volumes().sum() == pytest.approx(EXACT["cylinder"], rel=0.03), a3
    for a2 in (1, 2, 5, 6, 8):
        r, m = mesh_of("cylinder", family="tet", order=1, algorithm_2d=a2)
        assert m.n_elems > 100, a2
    for rc in (1, 2, 3):
        r, m = mesh_of("cylinder", family="hex", order=1, recombine_algorithm=rc)
        assert r.element_type == "C3D8", rc


def test_a_recombination_that_leaves_triangles_explains_itself():
    with pytest.raises(cad.CadError, match="prisms|triangles"):
        cad.mesh_primitive("cylinder", DIMS["cylinder"], 4, {"family": "hex", "order": 1, "recombine_algorithm": 0})


def test_optimisers_refinement_and_straight_midside():
    base, mb = mesh_of("sphere", div=3, family="tet", order=1)
    for opt in ("netgen", "relocate", "untangle"):
        r, m = mesh_of("sphere", div=3, family="tet", order=1, optimize=opt)
        assert m.n_elems > 100 and m.quality()["inverted_elements"] == 0, opt
    r, m = mesh_of("sphere", div=3, family="tet", order=1, refine=1)
    assert m.n_elems == pytest.approx(8 * mb.n_elems, rel=0.01)                 # uniform refinement: every tet becomes eight
    curved, mc = mesh_of("cylinder", div=3, family="tet", order=2)
    straight, ms = mesh_of("cylinder", div=3, family="tet", order=2, straight_midside=True)
    assert mc.volumes().sum() > ms.volumes().sum()                              # straight edges cut the circle short
    for co in ("highorder", "elastic", "fast"):
        r, m = mesh_of("cylinder", div=3, family="tet", order=2, curve_optimize=co)
        assert m.volumes().sum() == pytest.approx(EXACT["cylinder"], rel=0.005), co


def test_size_options_and_curvature_control_the_element_count():
    coarse, _ = mesh_of("box", family="tet", order=1, size=0.2)
    fine, _ = mesh_of("box", family="tet", order=1, size=0.05)
    assert len(fine.elems) > 5 * len(coarse.elems)                                # 4x smaller edge: far more elements
    f1, _ = mesh_of("box", family="tet", order=1, size=0.1, size_factor=0.5)
    f2, _ = mesh_of("box", family="tet", order=1, size=0.1)
    assert len(f1.elems) > 4 * len(f2.elems)
    s0, _ = mesh_of("sphere", div=2, family="tet", order=1)
    s1, _ = mesh_of("sphere", div=2, family="tet", order=1, curvature_points=24)
    assert len(s1.elems) > len(s0.elems)


def test_refinement_regions_concentrate_elements_where_asked():
    plain, _ = mesh_of("box", div=2, family="tet", order=1)
    ball = {"shape": "ball", "center": [0.5, 0, 0], "radius": 0.08, "size_in": 0.02}
    r, m = mesh_of("box", div=2, family="tet", order=1, regions=[ball])
    assert len(r.elems) > 3 * len(plain.elems)
    c = r.nodes[r.elems].mean(axis=1)
    vol = m.volumes()
    near = np.linalg.norm(c - [0.5, 0, 0], axis=1) < 0.1
    assert vol[near].mean() < 0.1 * vol[~near].mean()                             # tiny elements at the point, large elsewhere
    box = {"shape": "box", "min": [0.3, -0.1, -0.1], "max": [0.5, 0.1, 0.1], "size_in": 0.02, "size_out": 0.1}
    r2, _ = mesh_of("box", div=2, family="tet", order=1, regions=[box])
    assert len(r2.elems) > 3 * len(plain.elems)
    # bricks too (subdivided sphere)
    rb, mb = mesh_of("sphere", div=2, family="hex", order=1, regions=[{"shape": "ball", "center": [0.5, 0, 0], "radius": 0.2, "size_in": 0.04}])
    assert rb.element_type == "C3D8" and mb.quality()["inverted_elements"] == 0


def test_bad_geometry_and_sizes_give_readable_errors():
    with pytest.raises(cad.CadError, match="inner radius"):
        cad.mesh_primitive("tube", {"outer_radius": .3, "inner_radius": .5, "height": 1}, 4, {})
    with pytest.raises(cad.CadError, match="Dimension"):
        cad.mesh_primitive("box", {"length": -1}, 4, {})
    with pytest.raises(cad.CadError, match="Unknown primitive"):
        cad.mesh_primitive("teapot", {}, 4, {})
    with pytest.raises(cad.CadError, match="too small"):
        cad.mesh_primitive("box", DIMS["box"], 4, {"size": 1e-4})
    with pytest.raises(cad.CadError, match="algorithm_3d"):
        cad.mesh_primitive("box", DIMS["box"], 4, {"algorithm_3d": 3})


# ---------------------------------------------------------- existing mesh files
def write_meshes(tmp_path):
    out = {}
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    try:
        gmsh.model.add("x")
        gmsh.model.occ.addCylinder(0, 0, 0, 0, 0, 0.06, 0.012)
        gmsh.model.occ.synchronize()
        gmsh.option.setNumber("Mesh.MeshSizeMax", 0.01)
        gmsh.model.mesh.generate(3)
        for name, ver, binary in [("v2.msh", 2.2, 0), ("v4.msh", 4.1, 0), ("v4b.msh", 4.1, 1)]:
            gmsh.option.setNumber("Mesh.MshFileVersion", ver)
            gmsh.option.setNumber("Mesh.Binary", binary)
            gmsh.write(str(tmp_path / name))
            out[name] = (tmp_path / name).read_bytes()
        gmsh.model.mesh.setOrder(2)
        gmsh.option.setNumber("Mesh.MshFileVersion", 4.1)
        gmsh.option.setNumber("Mesh.Binary", 0)
        gmsh.write(str(tmp_path / "o2.msh"))
        out["o2.msh"] = (tmp_path / "o2.msh").read_bytes()
        # a prism mesh: the same base extruded without recombining the triangles
        gmsh.clear()
        gmsh.model.add("p")
        d = gmsh.model.occ.addDisk(0, 0, 0, 0.012, 0.012)
        gmsh.model.occ.extrude([(2, d)], 0, 0, 0.06, numElements=[3], heights=[1], recombine=True)
        gmsh.model.occ.synchronize()
        gmsh.option.setNumber("Mesh.MeshSizeMax", 0.01)
        gmsh.model.mesh.generate(3)
        gmsh.write(str(tmp_path / "prism.msh"))
        out["prism.msh"] = (tmp_path / "prism.msh").read_bytes()
    finally:
        gmsh.finalize()
    return out


@pytest.fixture(scope="module")
def msh_files(tmp_path_factory):
    return write_meshes(tmp_path_factory.mktemp("msh"))


@pytest.mark.parametrize("name,etype", [("v2.msh", "C3D4"), ("v4.msh", "C3D4"), ("v4b.msh", "C3D4"), ("o2.msh", "C3D10")])
def test_existing_gmsh_meshes_are_read_in_any_version_and_order(msh_files, name, etype):
    r = cad.mesh_msh(msh_files[name], "m")
    assert r.element_type == etype
    m = FEMesh(r.nodes, r.elems, r.element_type)
    m.ensure_positive_orientation()
    assert m.volumes().sum() == pytest.approx(math.pi * 0.012 ** 2 * 0.06, rel=0.05)


def test_unsupported_or_empty_mesh_files_are_explained(msh_files):
    with pytest.raises(cad.CadError, match="prisms|not support"):
        cad.mesh_msh(msh_files["prism.msh"], "m")
    with pytest.raises(cad.CadError):
        cad.mesh_msh(b"", "m")
    with pytest.raises(cad.CadError, match="read the Gmsh mesh|no nodes|no volume"):
        cad.mesh_msh(b"not a mesh at all", "m")
