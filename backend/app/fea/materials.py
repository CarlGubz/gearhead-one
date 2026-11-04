"""Material models: isotropic elasticity, thermal properties, J2 plasticity (bilinear hardening)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np

VOIGT_W = np.array([1.0, 1.0, 1.0, 2.0, 2.0, 2.0])  # tensor-product weights for [xx,yy,zz,xy,yz,zx]
M_VEC = np.array([1.0, 1.0, 1.0, 0.0, 0.0, 0.0])
I_S = np.diag([1.0, 1.0, 1.0, 0.5, 0.5, 0.5])


@dataclass
class Material:
    E: float = 210e9
    nu: float = 0.3
    density: float = 7850.0
    conductivity: float = 50.0       # W/(m K)
    specific_heat: float = 490.0     # J/(kg K)
    alpha: float = 0.0               # coefficient of thermal expansion, 1/K
    yield_stress: float = 0.0        # Pa; 0 = purely elastic
    hardening: float = 0.0           # H = d(sigma_y)/d(eps_p), Pa (isotropic, linear)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "Material":
        d = d or {}
        g = lambda k, default: float(d[k]) if d.get(k) is not None else default  # noqa: E731
        mat = cls(
            E=g("E", 210e9), nu=g("nu", 0.3), density=g("density", 7850.0),
            conductivity=g("conductivity", 50.0), specific_heat=g("specific_heat", 490.0),
            alpha=g("alpha", 0.0), yield_stress=g("yield_stress", 0.0), hardening=g("hardening", 0.0),
        )
        mat.validate()
        return mat

    def validate(self) -> None:
        if self.E <= 0:
            raise ValueError("Young's modulus must be positive")
        if not -1.0 < self.nu < 0.5:
            raise ValueError("Poisson's ratio must be between -1 and 0.5 (exclusive)")
        if self.density < 0 or self.conductivity < 0 or self.specific_heat < 0:
            raise ValueError("Density, conductivity and specific heat cannot be negative")
        if self.yield_stress < 0 or self.hardening < 0:
            raise ValueError("Yield stress and hardening modulus cannot be negative")

    @property
    def G(self) -> float:
        return self.E / (2 * (1 + self.nu))

    @property
    def K(self) -> float:
        return self.E / (3 * (1 - 2 * self.nu))

    @property
    def plastic(self) -> bool:
        return self.yield_stress > 0

    def D(self) -> np.ndarray:
        """6x6 elasticity matrix, stress = D * strain (engineering shear strains)."""
        lam = self.E * self.nu / ((1 + self.nu) * (1 - 2 * self.nu))
        mu = self.G
        D = np.zeros((6, 6))
        D[:3, :3] = lam
        D[0, 0] = D[1, 1] = D[2, 2] = lam + 2 * mu
        D[3, 3] = D[4, 4] = D[5, 5] = mu
        return D


def von_mises(sig: np.ndarray) -> np.ndarray:
    sxx, syy, szz, sxy, syz, szx = (sig[..., i] for i in range(6))
    return np.sqrt(np.maximum(
        0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2) + 3 * (sxy ** 2 + syz ** 2 + szx ** 2), 0.0))


def principal(sig: np.ndarray) -> np.ndarray:
    """Eigenvalues (ascending: S3, S2, S1) of the stress tensor for sig[..., 6]."""
    T = np.zeros(sig.shape[:-1] + (3, 3))
    T[..., 0, 0], T[..., 1, 1], T[..., 2, 2] = sig[..., 0], sig[..., 1], sig[..., 2]
    T[..., 0, 1] = T[..., 1, 0] = sig[..., 3]
    T[..., 1, 2] = T[..., 2, 1] = sig[..., 4]
    T[..., 0, 2] = T[..., 2, 0] = sig[..., 5]
    return np.linalg.eigvalsh(T)


def j2_update(eps: np.ndarray, eps_p: np.ndarray, alpha: np.ndarray, mat: Material
              ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Radial-return update for J2 plasticity with linear isotropic hardening.

    eps, eps_p: (..., 6) total (mechanical) and plastic strain, engineering shear.
    alpha:      (...) accumulated equivalent plastic strain.
    Returns (stress, eps_p_new, alpha_new, D_tangent[..., 6, 6]) with the consistent
    (algorithmic) tangent so global Newton converges quadratically.
    """
    D = mat.D()
    G, Kb, H, sy0 = mat.G, mat.K, mat.hardening, mat.yield_stress
    sig_tr = np.einsum("ij,...j->...i", D, eps - eps_p)
    p = sig_tr[..., :3].sum(axis=-1) / 3.0
    s = sig_tr - p[..., None] * M_VEC
    ss = np.einsum("...i,i->...", s * s, VOIGT_W)
    q = np.sqrt(1.5 * ss)
    f = q - (sy0 + H * alpha)
    yielding = f > 1e-12 * max(sy0, 1.0)

    dgam = np.where(yielding, f / (3 * G + H), 0.0)
    q_safe = np.where(q > 0, q, 1.0)
    sig = sig_tr - (3 * G * dgam / q_safe)[..., None] * s
    d_ep = (1.5 * dgam / q_safe)[..., None] * s * VOIGT_W  # engineering-shear plastic strain increment
    eps_p_new = eps_p + d_ep
    alpha_new = alpha + dgam

    # consistent tangent
    theta = np.where(yielding, 1.0 - 3 * G * dgam / q_safe, 1.0)
    theta_bar = np.where(yielding, 3 * G / (3 * G + H) - (1.0 - theta), 0.0)
    norm_s = np.sqrt(np.where(ss > 0, ss, 1.0))
    n = s / norm_s[..., None]
    mm = np.outer(M_VEC, M_VEC)
    Dt = (Kb * mm + (2 * G * theta)[..., None, None] * (I_S - mm / 3.0)
          - (2 * G * theta_bar)[..., None, None] * (n[..., :, None] * n[..., None, :]))
    return sig, eps_p_new, alpha_new, Dt


class MaterialMap:
    """
    Per-element material assignment: `groups` is a list of (Material, element-index-array) covering every
    element exactly once. A plain Material is treated as one group covering the whole mesh (`as_map`).
    """

    def __init__(self, n_elems: int, groups):
        self.n_elems = int(n_elems)
        self.groups = [(m, None if idx is None else np.asarray(idx, dtype=np.int64)) for m, idx in groups]
        covered = sum(self.n_elems if idx is None else len(idx) for _, idx in self.groups)
        if covered != self.n_elems:
            raise ValueError("Materials must be assigned to every element exactly once")
        self._props = {}

    @classmethod
    def single(cls, mat: Material, n_elems: int) -> "MaterialMap":
        return cls(n_elems, [(mat, None)])

    @property
    def plastic(self) -> bool:
        return any(m.plastic for m, _ in self.groups)

    @property
    def default(self) -> Material:
        return self.groups[0][0]

    def prop(self, name: str) -> np.ndarray:
        """Per-element array of a material property (e.g. 'density', 'conductivity')."""
        if name not in self._props:
            arr = np.empty(self.n_elems)
            for m, idx in self.groups:
                arr[slice(None) if idx is None else idx] = getattr(m, name)
            self._props[name] = arr
        return self._props[name]

    def chunks(self, mesh, size: int):
        """Yield (material, chunk) where chunk is a slice (uniform model) or an index array."""
        for m, idx in self.groups:
            for ch in mesh.chunks(size, idx):
                yield m, ch


def as_map(mat, mesh) -> MaterialMap:
    return mat if isinstance(mat, MaterialMap) else MaterialMap.single(mat, mesh.n_elems)
