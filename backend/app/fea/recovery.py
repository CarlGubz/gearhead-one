"""Result recovery: Gauss-point -> nodal extrapolation and derived scalar fields."""
from __future__ import annotations

from typing import Dict

import numpy as np

from .elements import tabulate
from .materials import Material, principal, von_mises
from .model import FEMesh

_EXTRAP_CACHE: Dict[str, np.ndarray] = {}


def _extrapolation_matrix(mesh: FEMesh) -> np.ndarray:
    """(nn, ng) matrix mapping Gauss-point values to nodal values by a per-element linear fit."""
    et = mesh.et
    if et.name in _EXTRAP_CACHE:
        return _EXTRAP_CACHE[et.name]
    pts, _, _, _ = tabulate(et)
    ng, nn = len(pts), et.n_nodes
    if ng < 4:
        A = np.full((nn, ng), 1.0 / ng)
    else:
        P = np.hstack([np.ones((ng, 1)), pts])
        Pn = np.hstack([np.ones((nn, 1)), et.natural_nodes])
        A = Pn @ np.linalg.pinv(P)
    _EXTRAP_CACHE[et.name] = A
    return A


def to_nodes(mesh: FEMesh, gp: np.ndarray) -> np.ndarray:
    """
    Extrapolate Gauss-point data gp[ne, ng, ...] to nodes and average over the elements sharing
    each node. Returns array of shape (N, ...).
    """
    A = _extrapolation_matrix(mesh)
    shape = gp.shape[2:]
    flat = gp.reshape(gp.shape[0], gp.shape[1], -1)
    out = np.zeros((mesh.n_nodes, flat.shape[2]))
    count = np.zeros(mesh.n_nodes)
    for sl in mesh.chunks(4000):
        vals = np.einsum("ng,egk->enk", A, flat[sl])
        ids = mesh.elems[sl].ravel()
        np.add.at(out, ids, vals.reshape(-1, flat.shape[2]))
        np.add.at(count, ids, 1.0)
    out /= np.maximum(count, 1.0)[:, None]
    return out.reshape((mesh.n_nodes,) + shape)


def tresca(sig: np.ndarray) -> np.ndarray:
    p = principal(sig)
    return p[..., 2] - p[..., 0]


def stress_fields(mesh: FEMesh, stress_gp: np.ndarray, strain_gp: np.ndarray, mat: Material,
                  alpha_p: np.ndarray = None) -> Dict[str, np.ndarray]:
    """Nodal result fields from Gauss-point stress/strain (engineering-shear Voigt, order xx,yy,zz,xy,yz,zx)."""
    vm = von_mises(stress_gp)
    pr = principal(stress_gp)
    tr = pr[..., 2] - pr[..., 0]
    # elastic strain energy density 0.5 sigma:eps  (engineering shear => plain dot product)
    sed = 0.5 * np.einsum("egi,egi->eg", stress_gp, strain_gp)
    eq_strain = np.sqrt(np.maximum(
        (2.0 / 3.0) * (strain_gp[..., 0] ** 2 + strain_gp[..., 1] ** 2 + strain_gp[..., 2] ** 2
                       + 0.5 * (strain_gp[..., 3] ** 2 + strain_gp[..., 4] ** 2 + strain_gp[..., 5] ** 2)), 0.0))
    out = {
        "von_mises": to_nodes(mesh, vm),
        "tresca": to_nodes(mesh, tr),
        "principal_max": to_nodes(mesh, pr[..., 2]),
        "principal_mid": to_nodes(mesh, pr[..., 1]),
        "principal_min": to_nodes(mesh, pr[..., 0]),
        "strain_energy_density": to_nodes(mesh, sed),
        "equivalent_strain": to_nodes(mesh, eq_strain),
    }
    comps = to_nodes(mesh, stress_gp)
    for i, name in enumerate(["sxx", "syy", "szz", "sxy", "syz", "szx"]):
        out[name] = comps[:, i]
    ecomp = to_nodes(mesh, strain_gp)
    for i, name in enumerate(["exx", "eyy", "ezz", "exy", "eyz", "ezx"]):
        out[name] = ecomp[:, i]
    if alpha_p is not None:
        out["plastic_strain"] = to_nodes(mesh, alpha_p)
    return out


def heat_flux_fields(mesh: FEMesh, T: np.ndarray, k) -> Dict[str, np.ndarray]:
    """Nodal heat-flux components and magnitude, q = -k grad T (k scalar or per element)."""
    ng = len(mesh.et.rule[1])
    k_e = np.broadcast_to(np.asarray(k, dtype=float), (mesh.n_elems,))
    q = np.zeros((mesh.n_elems, ng, 3))
    for sl in mesh.chunks(4000):
        _, dNdx, _, _, _ = mesh.jacobian(sl)
        q[sl] = -k_e[sl][:, None, None] * np.einsum("egnj,en->egj", dNdx, T[mesh.elems[sl]])
    qn = to_nodes(mesh, q)
    return {"flux_x": qn[:, 0], "flux_y": qn[:, 1], "flux_z": qn[:, 2], "flux_magnitude": np.linalg.norm(qn, axis=1)}
