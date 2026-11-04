"""CalculiX adapter. Parser tests always run; solver tests run when ccx (or the docker image) is available."""
import math
import time

import numpy as np
import pytest

from app.cad.services import calculix as cx
from app.cad.services import modeling3d as m

HAVE_CCX = cx.solver_command(refresh=True)[0] is not None
needs_ccx = pytest.mark.skipif(not HAVE_CCX, reason="CalculiX not installed (see docker/calculix)")


def box(l, w, h):
    return m.rebuild([{"id": "a", "type": "box", "params": {**m.default_params("box"), "length": l, "width": w, "height": h}}],
                     m.BuildContext({}, {})).shape


FRD = """    1C
    1UUSER
  100CL  101 1.000000000       3                     0    1           1
 -4  DISP        4    1
 -5  D1          1    2    1    0
 -5  D2          1    2    2    0
 -5  D3          1    2    3    0
 -5  ALL         1    2    0    0    1ALL
 -1         1 1.00000E+00 2.00000E+00-3.00000E+00
 -1         2 0.00000E+00 0.00000E+00 5.00000E-01
 -3
 9999
"""


def test_parse_frd_and_dat():
    blocks = cx.parse_frd(FRD)
    assert len(blocks) == 1 and blocks[0].name == "DISP" and blocks[0].time == 1.0
    assert blocks[0].data[1] == [1.0, 2.0, -3.0]
    U = cx._field(blocks[0], 2, 3)
    assert U[1, 2] == 0.5
    dat = "\n     B U C K L I N G   F A C T O R   O U T P U T\n\n MODE NO       BUCKLING\n                FACTOR\n\n      1   0.4098E+02\n      2   0.4099E+02\n"
    assert cx.parse_buckling_factors(dat) == [40.98, 40.99]
    tot = " total force (fx,fy,fz) for set FIX1 and time  0.1000000E+01\n\n       -1.0E-09  2.0E-10  1.000000E+02\n"
    assert cx.parse_totals(tot, "FIX1") == [-1e-09, 2e-10, 100.0]


def test_status_reports_hint_or_solver():
    st = cx.status()
    assert set(st) >= {"available", "kinds"} and (st["available"] or "CalculiX" in st["hint"])


@needs_ccx
def test_cantilever_static_matches_beam_theory():
    r = cx.run("ccx_static", [{"name": "bar", "shape": box(100, 10, 10), "material": "steel"}],
               {"mesh_size": 3.5, "fixed": [{"face": "<X"}], "forces": [{"face": ">X", "vector": [0, 0, -100]}]}, 120000)
    s = r["summary"]
    assert s["max_displacement_mm"] == pytest.approx(0.200, rel=0.03)  # PL^3/3EI
    assert s["max_von_mises_mpa"] == pytest.approx(60, rel=0.12)  # Mc/I at the root (C3D10 resolves the peak)
    assert s["equilibrium_error_n"] < 1e-3
    assert r["mesh"]["element_type"] == "C3D10"


@needs_ccx
def test_buckling_matches_euler_and_is_repeatable():
    cfg = {"fixed": [{"face": "<X"}], "modes": 2, "forces": [{"face": ">X", "vector": [-1000, 0, 0]}]}
    a = cx.run("ccx_buckling", [{"name": "bar", "shape": box(100, 10, 10), "material": "steel"}], cfg, 120000)
    b = cx.run("ccx_buckling", [{"name": "bar", "shape": box(100, 10, 10), "material": "steel"}], cfg, 120000)
    euler = math.pi ** 2 * 200000 * (10 ** 4 / 12) / (4 * 100 ** 2) / 1000  # cantilever, factor on 1 kN
    assert a["summary"]["critical_factor"] == pytest.approx(euler, rel=0.03)
    assert a["summary"]["buckling_factors"] == b["summary"]["buckling_factors"]
    assert len(a["modes"]) == 2


@needs_ccx
def test_plasticity_caps_stress():
    r = cx.run("ccx_static", [{"name": "bar", "shape": box(100, 10, 10), "material": "steel"}],
               {"mesh_size": 4, "fixed": [{"face": "<X"}], "forces": [{"face": ">X", "vector": [0, 0, -500]}],
                "plasticity": {"enabled": True, "tangent_modulus": 2000}, "increments": 5}, 120000)
    s = r["summary"]
    assert s["yielded"] and s["max_plastic_strain"] > 0
    assert s["max_von_mises_mpa"] < 320  # elastic solution would be ~300 MPa at the surface, plus the root peak
    assert "plastic_strain" in r["surface"]["fields"]


@needs_ccx
def test_transient_thermal_history():
    r = cx.run("ccx_thermal_transient", [{"name": "bar", "shape": box(60, 10, 10), "material": "aluminum"}],
               {"mesh_size": 5, "fixed_temps": [{"face": "<X", "temperature": 100}], "initial_temperature": 20,
                "time_period": 120, "time_step": 10}, 120000)
    hist = r["history"]
    assert hist[0]["max_c"] == pytest.approx(100, abs=1e-6)
    assert [h["mean_c"] for h in hist] == sorted(h["mean_c"] for h in hist)  # heats up monotonically
    assert 20 < r["summary"]["min_temperature_c"] < 100


@needs_ccx
def test_contact_between_components(client, project):
    base, blk = box(40, 40, 10), box(20, 20, 10)
    M = np.eye(4)
    M[:3, 3] = [10, 10, 10]
    r = cx.run("ccx_contact", [{"name": "base", "shape": base, "material": "steel", "component": 1},
                               {"name": "block", "shape": blk, "material": "aluminum", "component": 2, "matrix": M}],
               {"mesh_size": 6, "increments": 4,
                "fixed": [{"component": 1, "face": "<Z"}, {"component": 2, "face": ">Z", "dofs": [True, True, False]}],
                "pressures": [{"component": 2, "face": ">Z", "value": 50}]}, 120000)
    s = r["summary"]
    assert s["contact_pairs"][0]["kind"] == "plane"
    assert s["reaction_force_n"][2] == pytest.approx(50 * 400, rel=1e-3)
    assert s["equilibrium_error_n"] < 1.0
    assert s["max_contact_pressure_mpa"] > 25


@needs_ccx
def test_assembly_simulation_api(client, project):
    def part(name, l, w, h):
        pid = client.post(f"/api/projects/{project}/parts", json={"name": name}).json()["id"]
        client.post(f"/api/parts/{pid}/features", json={"type": "box", "params": {"length": l, "width": w, "height": h}})
        return pid

    a = client.post(f"/api/projects/{project}/assemblies", json={"name": "stack"}).json()["id"]
    c1 = client.post(f"/api/assemblies/{a}/components", json={"part_id": part("base", 30, 30, 8)}).json()["id"]
    c2 = client.post(f"/api/assemblies/{a}/components", json={"part_id": part("cap", 15, 15, 8), "position": [5, 5, 8]}).json()["id"]
    assert client.post(f"/api/assemblies/{a}/simulations", json={"kind": "ccx_static"}).status_code == 422
    job = client.post(f"/api/assemblies/{a}/simulations", json={"kind": "ccx_contact", "config": {
        "mesh_size": 6, "increments": 3,
        "fixed": [{"component": c1, "face": "<Z"}, {"component": c2, "face": ">Z", "dofs": [True, True, False]}],
        "pressures": [{"component": c2, "face": ">Z", "value": 10}]}}).json()
    for _ in range(600):
        j = client.get(f"/api/simulations/{job['id']}").json()
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    assert j["status"] == "done", j["error"]
    assert j["assembly_id"] == a and j["summary"]["reaction_force_n"][2] == pytest.approx(10 * 225, rel=1e-3)
    assert client.get(f"/api/assemblies/{a}/simulations").json()[0]["id"] == job["id"]
