"""
Large-deformation (NLGEOM) mechanics: total Lagrangian assembly for hyperelastic solids.

Kinematics at every Gauss point        F = I + sum_a u_a (x) grad0 N_a ,   E = 1/2 (F^T F - I)
Internal force                         f = int B_L(F)^T S dV0
Tangent                                K = int B_L^T C B_L dV0  +  int (grad N)^T S (grad N) dV0   (material + geometric)

Material models (selected by name)
  svk         Saint Venant-Kirchhoff,  S = lambda tr(E) I + 2 mu E        (large rotations, small-to-moderate strain)
  neohookean  compressible Neo-Hookean, S = mu (I - C^-1) + lambda ln(J) C^-1

Voigt order is [xx, yy, zz, xy, yz, zx] with engineering shear strains, as everywhere else in the solver.
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
from scipy import sparse

from .assembly import MechOut, PlasticState, _chunk_size, _csr, _scatter, dof_map
from .materials import Material, as_map
from .model import FEMesh

FINITE_MODELS = ("svk", "neohookean")
_VOIGT_PAIRS = [(0, 0), (1, 1), (2, 2), (0, 1), (1, 2), (2, 0)]


class FiniteStrainError(ValueError):
    """An element inverted (J <= 0) or the deformation became non-physical in a trial iterate."""


def lame(mat: Material) -> Tuple[float, float]:
    lam = mat.E * mat.nu / ((1 + mat.nu) * (1 - 2 * mat.nu))
    return lam, mat.G


def _voigt(T: np.ndarray) -> np.ndarray:
    """Symmetric 3x3 tensors (..., 3, 3) -> Voigt (..., 6) with TENSOR shear components."""
    return np.stack([T[..., i, j] for i, j in _VOIGT_PAIRS], axis=-1)


def _voigt_strain(E: np.ndarray) -> np.ndarray:
    """Green-Lagrange tensor -> Voigt with engineering shear."""
    v = _voigt(E)
    v[..., 3:] *= 2.0
    return v


def deformation_gradient(dNdX: np.ndarray, ue: np.ndarray) -> np.ndarray:
    """F[e,g,i,j] = delta_ij + sum_n u[e,n,i] dN[e,g,n,j]."""
    F = np.einsum("eni,egnj->egij", ue, dNdX)
    F[..., 0, 0] += 1.0
    F[..., 1, 1] += 1.0
    F[..., 2, 2] += 1.0
    return F


def build_BL(F: np.ndarray, dNdX: np.ndarray) -> np.ndarray:
    """Nonlinear strain-displacement matrices (ne, ng, 6, 3 nn): dE_voigt = B_L du."""
    ne, ng, nn, _ = dNdX.shape
    B = np.zeros((ne, ng, 6, nn, 3))
    d0, d1, d2 = dNdX[..., 0], dNdX[..., 1], dNdX[..., 2]
    f0, f1, f2 = F[..., :, 0], F[..., :, 1], F[..., :, 2]       # f_j[..., i] = F[i, j]
    B[:, :, 0] = f0[:, :, None, :] * d0[..., None]
    B[:, :, 1] = f1[:, :, None, :] * d1[..., None]
    B[:, :, 2] = f2[:, :, None, :] * d2[..., None]
    B[:, :, 3] = f0[:, :, None, :] * d1[..., None] + f1[:, :, None, :] * d0[..., None]
    B[:, :, 4] = f1[:, :, None, :] * d2[..., None] + f2[:, :, None, :] * d1[..., None]
    B[:, :, 5] = f2[:, :, None, :] * d0[..., None] + f0[:, :, None, :] * d2[..., None]
    return B.reshape(ne, ng, 6, 3 * nn)


def _svk(E: np.ndarray, mat: Material):
    """Returns S (tensor, (...,3,3)), D (6,6), energy density."""
    lam, mu = lame(mat)
    tr = E[..., 0, 0] + E[..., 1, 1] + E[..., 2, 2]
    eye = np.eye(3)
    S = lam * tr[..., None, None] * eye + 2.0 * mu * E
    W = 0.5 * lam * tr ** 2 + mu * np.einsum("...ij,...ij->...", E, E)
    return S, mat.D(), W


def _neo_hooke(C: np.ndarray, J: np.ndarray, mat: Material):
    """Returns S (tensor), D (..., 6, 6), energy density."""
    lam, mu = lame(mat)
    Ci = np.linalg.inv(C)
    lnJ = np.log(J)
    eye = np.eye(3)
    S = mu * (eye - Ci) + (lam * lnJ)[..., None, None] * Ci
    c = mu - lam * lnJ
    ne, ng = J.shape
    D = np.zeros((ne, ng, 6, 6))
    for a, (i, j) in enumerate(_VOIGT_PAIRS):
        for b, (k, l) in enumerate(_VOIGT_PAIRS):
            D[..., a, b] = lam * Ci[..., i, j] * Ci[..., k, l] + c * (Ci[..., i, k] * Ci[..., j, l] + Ci[..., i, l] * Ci[..., j, k])
    trC = C[..., 0, 0] + C[..., 1, 1] + C[..., 2, 2]
    W = 0.5 * mu * (trC - 3.0) - mu * lnJ + 0.5 * lam * lnJ ** 2
    return S, D, W


def finite_assemble(mesh: FEMesh, mat, u: np.ndarray, model: str = "svk", want_K: bool = True) -> MechOut:
    """
    Internal force and tangent stiffness for the displacement field `u` (3N) with finite-strain kinematics.
    `stress` in the result is the CAUCHY stress, `strain` the Green-Lagrange strain (engineering shear).
    """
    if model not in FINITE_MODELS:
        raise ValueError(f"Unknown finite-strain model '{model}'")
    mats = as_map(mat, mesh)
    n3 = 3 * mesh.n_nodes
    ng = len(mesh.et.rule[1])
    fint = np.zeros(n3)
    stress = np.zeros((mesh.n_elems, ng, 6))
    strain = np.zeros((mesh.n_elems, ng, 6))
    rows, cols, vals = [], [], []
    energy = 0.0

    for gmat, sl in mats.chunks(mesh, _chunk_size(mesh)):
        if gmat.plastic:
            raise ValueError("Large-deformation analysis does not support plasticity yet. Use a material without yield stress.")
        _, dNdX, wdet, _, _ = mesh.jacobian(sl)
        elems = mesh.elems[sl]
        dofs = dof_map(elems)
        ue = u[dofs].reshape(len(elems), -1, 3)
        F = deformation_gradient(dNdX, ue)
        J = np.linalg.det(F)
        if not np.all(np.isfinite(J)) or J.min() <= 1e-8:
            raise FiniteStrainError("An element is inverted or collapsed (volume ratio J <= 0). Use smaller increments or a softer load.")
        C = np.einsum("egki,egkj->egij", F, F)
        E = 0.5 * (C - np.eye(3))
        if model == "svk":
            S, D0, W = _svk(E, gmat)
            D = np.broadcast_to(D0, (len(elems), ng, 6, 6))
        else:
            S, D, W = _neo_hooke(C, J, gmat)
        Sv = _voigt(S)
        B = build_BL(F, dNdX)
        fe = np.einsum("eg,egia,egi->ea", wdet, B, Sv)
        np.add.at(fint, dofs.ravel(), fe.ravel())
        energy += float(np.einsum("eg,eg->", wdet, W))
        sig = np.einsum("egik,egkl,egjl->egij", F, S, F) / J[..., None, None]
        stress[sl] = _voigt(sig)
        strain[sl] = _voigt_strain(E)
        if want_K:
            DB = np.einsum("egij,egjb->egib", D, B)
            Ke = np.einsum("eg,egia,egib->eab", wdet, B, DB)
            g = np.einsum("egaj,egjk,egbk,eg->eab", dNdX, S, dNdX, wdet)      # geometric (initial-stress) part
            nn = g.shape[1]
            for i in range(3):
                Ke[:, i::3, i::3] += g
            r, c, v = _scatter(Ke, dofs)
            rows.append(r)
            cols.append(c)
            vals.append(v)
    K = _csr(rows, cols, vals, (n3, n3)) if want_K else None
    return MechOut(K, fint, stress, strain, None, energy)
