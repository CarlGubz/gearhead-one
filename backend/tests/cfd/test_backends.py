"""Solver backends: case generation for OpenFOAM / FEniCSx / PyFR, fallback resolution, readers.

Runs of the real external tools are opt-in (they need OpenFOAM / DOLFINx / PyFR, e.g. the Docker
images): ``CFD_TEST_EXTERNAL=1 python -m pytest tests/test_backends.py``.
"""
import os
import py_compile
from pathlib import Path

import numpy as np
import pytest

from app.cfd.schemas import (Boundaries, Boundary, CaseConfig, Domain, HeatSource, Initial, Material, MeshSettings, Models,
                         Obstacle, Probe, RunSettings, SolverSettings)
from app.cfd.solver.backends import BACKENDS, BackendUnavailable, list_backends, resolve
from app.cfd.solver.backends.launcher import clear_cache
from app.cfd.solver.backends.openfoam import OpenFOAMCase, read_internal
from app.cfd.solver.backends.pyfr import read_vtu, write_gmsh, write_ini
from app.cfd.solver.grid3d import build_grid_nd, sample_nd
from app.cfd.solver.runner import Hooks
from app.cfd.templates import build_template

EXTERNAL = os.environ.get("CFD_TEST_EXTERNAL") == "1"


def sphere_case(**kw):
    return CaseConfig(dimension="3d", material=Material(density=1, viscosity=0.01, conductivity=0.02, specific_heat=1,
                                                        thermal_expansion=0.01),
                      domain=Domain(width=2, height=0.5, depth=0.5), mesh=MeshSettings(nx=20, ny=6, nz=6),
                      obstacles=[Obstacle(id="sph-1", shape="sphere", x=0.6, y=0.25, z=0.25, radius=0.1),
                                 Obstacle(id="blk", shape="rectangle", x=1.3, y=0.1, width=0.1, height=0.2, depth=0.2,
                                          thermal="fixed", temperature=1)],
                      boundaries=Boundaries(left=Boundary(type="inlet", velocity=1, profile="parabolic"), right=Boundary(type="outlet"),
                                            front=Boundary(type="symmetry")),
                      probes=[Probe(id="p", x=1, y=0.25)], **kw)


# --------------------------------------------------------------------------- registry / fallbacks


def test_backend_registry_and_fallbacks():
    clear_cache()
    ids = {b["id"] for b in list_backends()}
    assert ids == {"native", "openfoam", "fenicsx", "pyfr", "python-fem", "python-ac"}
    if EXTERNAL:
        pytest.skip("external tools enabled")
    for requested, expected in (("openfoam", "native"), ("fenicsx", "python-fem"), ("pyfr", "python-ac"), ("native", "native")):
        b, notes = resolve(CaseConfig(solver=SolverSettings(backend=requested)))
        assert b.id == expected and (bool(notes) == (requested != "native"))
    with pytest.raises(BackendUnavailable):
        resolve(CaseConfig(solver=SolverSettings(backend="openfoam", allow_fallback=False)))


def test_capability_checks():
    turb = build_template("backward_facing_step")[2]
    turb.models.turbulence = "k_omega_sst"
    assert any(m.level == "error" for m in BACKENDS["native"].check(turb))
    assert not any(m.level == "error" for m in BACKENDS["openfoam"].check(turb))
    hot = build_template("electronics_cooling")[2]
    assert any("isothermal" in m.message for m in BACKENDS["pyfr"].check(hot))


# --------------------------------------------------------------------------- OpenFOAM case generation


def test_openfoam_case_2d(tmp_path):
    case = build_template("cylinder_in_channel")[2]
    files = OpenFOAMCase(case).write(tmp_path)
    names = {p.relative_to(tmp_path).as_posix() for p in files}
    assert {"system/blockMeshDict", "system/controlDict", "system/fvSchemes", "system/fvSolution", "0.orig/U", "0.orig/p",
            "constant/transportProperties", "Allrun", "system/topoSetDict.obs_cyl"} <= names
    bm = (tmp_path / "system/blockMeshDict").read_text()
    assert "(220 41 1)" in bm and "type empty" in bm and "obs_cyl" in bm
    assert "cylinderToCell" in (tmp_path / "system/topoSetDict.obs_cyl").read_text()
    assert "application     pimpleFoam" in (tmp_path / "system/controlDict").read_text()
    assert "exprFixedValue" in (tmp_path / "0.orig/U").read_text()  # parabolic inlet
    allrun = (tmp_path / "Allrun").read_text()
    assert "subsetMesh -overwrite keep_obs_cyl -patch obs_cyl" in allrun and "\r" not in allrun


def test_openfoam_case_3d_physics_selection(tmp_path):
    c = sphere_case(models=Models(energy=True, buoyancy=True, gravity_y=-9.81, reference_temperature=0, turbulence="k_epsilon"),
                    heat_sources=[HeatSource(id="h", x=1, y=0.25, width=0.1, height=0.1, power_density=100)])
    ofc = OpenFOAMCase(c)
    ofc.write(tmp_path)
    assert ofc.solver == "buoyantBoussinesqSimpleFoam"
    assert "sphereToCell" in (tmp_path / "system/topoSetDict.obs_sph_1").read_text()
    assert "(20 6 6)" in (tmp_path / "system/blockMeshDict").read_text()
    assert {"k", "epsilon", "nut", "alphat", "p_rgh", "T"} <= {p.name for p in (tmp_path / "0.orig").iterdir()}
    assert "beta" in (tmp_path / "constant/transportProperties").read_text()
    assert "RASModel        kEpsilon" in (tmp_path / "constant/turbulenceProperties").read_text()
    assert "scalarSemiImplicitSource" in (tmp_path / "system/fvOptions").read_text()
    t = (tmp_path / "0.orig/T").read_text()
    assert "obs_blk" in t and "uniform 1;" in t
    # forced convection uses simpleFoam + scalarTransport
    c.models.buoyancy = False
    assert OpenFOAMCase(c).solver == "simpleFoam" and "scalarTransport" in OpenFOAMCase(c).control_dict()
    assert OpenFOAMCase(c, "compressible").solver == "simpleFoam"
    c.models.buoyancy = True
    assert OpenFOAMCase(c, "compressible").solver == "buoyantSimpleFoam"


def test_openfoam_field_reader(tmp_path):
    f = tmp_path / "U"
    f.write_text("FoamFile\n{\n    version 2.0;\n    format ascii;\n    class volVectorField;\n    object U;\n}\n"
                 "dimensions [0 1 -1 0 0 0 0];\ninternalField nonuniform List<vector>\n3\n(\n(1 2 3)\n(4 5 6)\n(7 8 9)\n)\n;\n"
                 "boundaryField\n{\n}\n")
    arr = read_internal(f, 3)
    assert arr.shape == (3, 3) and arr[2, 1] == 8


# --------------------------------------------------------------------------- FEniCSx / PyFR decks


def test_fenicsx_export(tmp_path):
    files = BACKENDS["fenicsx"].export_case(sphere_case(), tmp_path)
    assert {p.name for p in files} == {"case.json", "run_fenicsx.py", "README.txt"}
    py_compile.compile(str(tmp_path / "run_fenicsx.py"), doraise=True)


def test_pyfr_mesh_and_config(tmp_path):
    c = sphere_case()
    info = write_gmsh(c, tmp_path / "mesh.msh")
    g = build_grid_nd(c)
    assert info["elements"] == g.n_fluid
    with_cells = {f"obs_{ob.id}".replace("-", "_") for k, ob in enumerate(c.obstacles) if (g.solid_id == k).any()}
    assert "obs_sph_1" in with_cells
    assert set(info["boundaries"]) == {"left", "right", "bottom", "top", "back", "front"} | with_cells
    msh = (tmp_path / "mesh.msh").read_text()
    assert '3 1 "fluid"' in msh and "$Elements" in msh
    ini = write_ini(c, tmp_path / "config.ini", info["boundaries"])
    text = (tmp_path / "config.ini").read_text()
    assert "system = ac-navier-stokes" in text and "[soln-bcs-left]" in text and "type = ac-out-fp" in text
    assert "type = slp-wall" in text and "[solver-dual-time-integrator-multip]" in text
    out = (ini["tend"] / (c.run.snapshot_count - 1)) if c.run.mode == "transient" else ini["tend"]
    assert abs(out / ini["dt"] - round(out / ini["dt"])) < 1e-9


def test_vtu_reader_meshio_path(tmp_path):
    import meshio

    pts = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], float)
    meshio.Mesh(pts, [("quad", np.array([[0, 1, 2, 3]]))], point_data={"Velocity": pts[:, :2], "Pressure": pts[:, 0]}).write(tmp_path / "a.vtu")
    v = read_vtu(tmp_path / "a.vtu")
    assert v["points"].shape == (4, 3) and list(v["offsets"]) == [4] and "Pressure" in v["point_data"]


# --------------------------------------------------------------------------- real external runs (opt-in)


def _ghia_centre(case, r):
    g = build_grid_nd(case)
    return sample_nd(r.frames[-1]["u"][:, :, 0], g, (0.5, 0.5))


@pytest.mark.skipif(not EXTERNAL, reason="set CFD_TEST_EXTERNAL=1 with OpenFOAM / DOLFINx / PyFR available")
@pytest.mark.parametrize("backend,n,tol", [("openfoam", 32, 0.01), ("fenicsx", 32, 0.015), ("pyfr", 12, 0.01)])
def test_external_backend_cavity(tmp_path, backend, n, tol):
    case = build_template("lid_driven_cavity")[2]
    case.mesh.nx = case.mesh.ny = n
    case.solver.order = 3 if backend == "pyfr" else 2
    b = BACKENDS[backend]
    assert b.availability().ok, b.availability().detail
    r = b.run(case, Hooks(), tmp_path)
    assert r.status == "completed" and r.summary["backend"] == backend
    assert _ghia_centre(case, r) == pytest.approx(-0.2058, abs=tol)
