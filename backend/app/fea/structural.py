"""
Structural elements: truss (T3D2), 3-D Timoshenko beam (B31), flat-facet MITC4 shell (S4) and spring/dashpot (SPRING2).

DOF layout: 6 per node [ux, uy, uz, rx, ry, rz] in GLOBAL axes -> node n owns DOFs 6n .. 6n+5.
A structural model has ONE element type (all elements share the node count); each part carries its own material and section.

Element kernels here are vectorised over elements. Assembly, boundary conditions, analyses and result recovery live in
struct_runner.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

STRUCT_TYPES: Dict[str, int] = {"T3D2": 2, "B31": 2, "S4": 4, "SPRING2": 2}
LINE_TYPES = ("T3D2", "B31", "SPRING2")


class StructuralError(ValueError):
    """Problem with a structural model definition (bad section, degenerate element, ...)."""


def is_structural(name: Optional[str]) -> bool:
    return (name or "") in STRUCT_TYPES


# ---------------------------------------------------------------- sections
def _num(sec: dict, key: str, label: str, positive: bool = True) -> float:
    try:
        v = float(sec[key])
    except (KeyError, TypeError, ValueError):
        raise StructuralError(f"The section needs {label}.") from None
    if not np.isfinite(v) or (positive and v <= 0):
        raise StructuralError(f"The section {label} must be positive.")
    return v


def default_section(etype: str) -> dict:
    return {
        "T3D2": {"area": 1e-4},
        "B31": {"type": "rect", "b": 0.05, "h": 0.1},
        "S4": {"thickness": 0.01},
        "SPRING2": {"k": 1e6, "c": 0.0},
    }[etype]


def beam_section(sec: dict) -> Dict[str, float]:
    """Section constants of a beam: A, Iy (bending about local y: deflection along z), Iz, J, shear areas, fibre distances."""
    kind = str(sec.get("type") or "rect")
    if kind == "rect":
        b, h = _num(sec, "b", "width b"), _num(sec, "h", "height h")
        a, s = max(b, h), min(b, h)
        J = a * s ** 3 * (1.0 / 3.0 - 0.21 * (s / a) * (1.0 - s ** 4 / (12.0 * a ** 4)))
        A = b * h
        return dict(A=A, Iy=b * h ** 3 / 12.0, Iz=h * b ** 3 / 12.0, J=J, Asy=5.0 / 6.0 * A, Asz=5.0 / 6.0 * A,
                    cy=b / 2.0, cz=h / 2.0, rt=0.5 * np.hypot(b, h))
    if kind == "circle":
        d = _num(sec, "d", "diameter d")
        A = np.pi * d ** 2 / 4.0
        I = np.pi * d ** 4 / 64.0
        return dict(A=A, Iy=I, Iz=I, J=2.0 * I, Asy=0.9 * A, Asz=0.9 * A, cy=d / 2.0, cz=d / 2.0, rt=d / 2.0)
    if kind == "pipe":
        do, t = _num(sec, "d", "outer diameter d"), _num(sec, "t", "wall thickness t")
        if 2.0 * t >= do:
            raise StructuralError("Pipe wall thickness must be smaller than the outer radius.")
        di = do - 2.0 * t
        A = np.pi * (do ** 2 - di ** 2) / 4.0
        I = np.pi * (do ** 4 - di ** 4) / 64.0
        return dict(A=A, Iy=I, Iz=I, J=2.0 * I, Asy=0.5 * A, Asz=0.5 * A, cy=do / 2.0, cz=do / 2.0, rt=do / 2.0)
    if kind == "general":
        A = _num(sec, "A", "area A")
        Iy, Iz, J = _num(sec, "Iy", "Iy"), _num(sec, "Iz", "Iz"), _num(sec, "J", "torsion constant J")
        cy = float(sec.get("cy") or np.sqrt(Iz / A) * 2.0)
        cz = float(sec.get("cz") or np.sqrt(Iy / A) * 2.0)
        return dict(A=A, Iy=Iy, Iz=Iz, J=J, Asy=float(sec.get("Asy") or 5.0 / 6.0 * A), Asz=float(sec.get("Asz") or 5.0 / 6.0 * A),
                    cy=cy, cz=cz, rt=float(sec.get("rt") or max(cy, cz)))
    raise StructuralError("Beam section type must be rect, circle, pipe or general.")


def validate_section(etype: str, sec: dict) -> dict:
    """Return a cleaned section dict for the element type, or raise StructuralError."""
    sec = dict(sec or {})
    if etype == "T3D2":
        return {"area": _num(sec, "area", "area")}
    if etype == "S4":
        return {"thickness": _num(sec, "thickness", "thickness")}
    if etype == "SPRING2":
        k = _num(sec, "k", "stiffness k", positive=False)
        c = float(sec.get("c") or 0.0)
        if k < 0 or c < 0 or (k == 0 and c == 0):
            raise StructuralError("A spring/dashpot needs a positive stiffness k and/or damping c.")
        return {"k": k, "c": c}
    if etype == "B31":
        beam_section(sec)
        out = {k: v for k, v in sec.items() if k in ("type", "b", "h", "d", "t", "A", "Iy", "Iz", "J", "Asy", "Asz", "cy", "cz", "rt")}
        out["type"] = str(sec.get("type") or "rect")
        ori = sec.get("orientation")
        if ori is not None:
            ori = [float(x) for x in ori]
            if len(ori) != 3 or np.linalg.norm(ori) == 0:
                raise StructuralError("Beam orientation must be a non-zero 3-vector.")
            out["orientation"] = ori
        return out
    raise StructuralError(f"Unknown structural element type '{etype}'")


# --------------------------------------------------------------- geometry
def beam_frames(X1: np.ndarray, X2: np.ndarray, ref: Optional[np.ndarray] = None) -> Tuple[np.ndarray, np.ndarray]:
    """Local axes of beams: returns (R (ne,3,3) with rows x', y', z', L (ne,)). `ref` is the approximate local y' direction."""
    d = X2 - X1
    L = np.linalg.norm(d, axis=1)
    if np.any(L <= 1e-12):
        raise StructuralError("An element has zero length (two nodes at the same place).")
    ex = d / L[:, None]
    ne = len(L)
    if ref is None:
        ref = np.tile(np.array([0.0, 1.0, 0.0]), (ne, 1))
    else:
        ref = np.broadcast_to(np.asarray(ref, dtype=float), (ne, 3)).copy()
    # where the reference is (nearly) parallel to the axis, pick another one
    par = np.abs(np.einsum("ij,ij->i", ex, ref / np.linalg.norm(ref, axis=1, keepdims=True))) > 0.999
    ref[par] = np.where(np.abs(ex[par][:, 2:3]) > 0.9, np.array([1.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0]))
    ez = np.cross(ex, ref)
    ez /= np.linalg.norm(ez, axis=1, keepdims=True)
    ey = np.cross(ez, ex)
    return np.stack([ex, ey, ez], axis=1), L


def _blockdiag_T(R: np.ndarray, nblocks: int) -> np.ndarray:
    ne = R.shape[0]
    T = np.zeros((ne, 3 * nblocks, 3 * nblocks))
    for b in range(nblocks):
        T[:, 3 * b:3 * b + 3, 3 * b:3 * b + 3] = R
    return T


# ------------------------------------------------------------------- truss
def truss_local(E, A, L) -> np.ndarray:
    k = E * A / L
    K = np.zeros((len(L), 2, 2))
    K[:, 0, 0] = K[:, 1, 1] = k
    K[:, 0, 1] = K[:, 1, 0] = -k
    return K


def axial_global(k: np.ndarray, ex: np.ndarray) -> np.ndarray:
    """(ne,) axial stiffness and unit axes -> (ne,6,6) global matrices on [u1 u2] translations."""
    P = np.einsum("ei,ej->eij", ex, ex)
    Ke = np.zeros((len(k), 6, 6))
    Ke[:, :3, :3] = Ke[:, 3:, 3:] = P * k[:, None, None]
    Ke[:, :3, 3:] = Ke[:, 3:, :3] = -P * k[:, None, None]
    return Ke


def axial_geometric(N: np.ndarray, L: np.ndarray, ex: np.ndarray) -> np.ndarray:
    """Geometric stiffness of a truss carrying axial force N (tension positive): (N/L) (I - e e^T)."""
    P = (np.eye(3)[None] - np.einsum("ei,ej->eij", ex, ex)) * (N / L)[:, None, None]
    Ke = np.zeros((len(L), 6, 6))
    Ke[:, :3, :3] = Ke[:, 3:, 3:] = P
    Ke[:, :3, 3:] = Ke[:, 3:, :3] = -P
    return Ke


# -------------------------------------------------------------------- beam
def beam_local_K(E, G, A, Iy, Iz, J, Asy, Asz, L) -> np.ndarray:
    """12x12 Timoshenko beam stiffness in local axes (shear deformation through the shear areas Asy, Asz)."""
    ne = len(L)
    K = np.zeros((ne, 12, 12))
    ea = E * A / L
    gj = G * J / L
    K[:, 0, 0] = K[:, 6, 6] = ea
    K[:, 0, 6] = K[:, 6, 0] = -ea
    K[:, 3, 3] = K[:, 9, 9] = gj
    K[:, 3, 9] = K[:, 9, 3] = -gj
    L2, L3 = L ** 2, L ** 3

    def plane(idx, EI, As, sign):
        phi = 12.0 * EI / (G * As * L2)
        c = EI / ((1 + phi) * L3)
        s = sign
        m = np.zeros((ne, 4, 4))
        m[:, 0, 0] = m[:, 2, 2] = 12.0
        m[:, 0, 2] = m[:, 2, 0] = -12.0
        m[:, 0, 1] = m[:, 1, 0] = 6.0 * L * s
        m[:, 0, 3] = m[:, 3, 0] = 6.0 * L * s
        m[:, 1, 2] = m[:, 2, 1] = -6.0 * L * s
        m[:, 2, 3] = m[:, 3, 2] = -6.0 * L * s
        m[:, 1, 1] = (4.0 + phi) * L2
        m[:, 3, 3] = (4.0 + phi) * L2
        m[:, 1, 3] = m[:, 3, 1] = (2.0 - phi) * L2
        m *= c[:, None, None]
        K[:, idx[:, None], idx[None, :]] += m

    plane(np.array([1, 5, 7, 11]), E * Iz, Asy, +1.0)     # bending in the x-y plane (v, theta_z)
    plane(np.array([2, 4, 8, 10]), E * Iy, Asz, -1.0)     # bending in the x-z plane (w, theta_y)
    return K


def beam_local_M(rho, A, Iy, Iz, L) -> np.ndarray:
    """Consistent 12x12 mass matrix (translation + torsional inertia), local axes."""
    ne = len(L)
    M = np.zeros((ne, 12, 12))
    m = rho * A * L
    M[:, 0, 0] = M[:, 6, 6] = m / 3.0
    M[:, 0, 6] = M[:, 6, 0] = m / 6.0
    ip = rho * (Iy + Iz) * L
    M[:, 3, 3] = M[:, 9, 9] = ip / 3.0
    M[:, 3, 9] = M[:, 9, 3] = ip / 6.0
    L2 = L ** 2
    c = m / 420.0

    def plane(idx, sign):
        b = np.zeros((ne, 4, 4))
        b[:, 0, 0] = b[:, 2, 2] = 156.0
        b[:, 0, 2] = b[:, 2, 0] = 54.0
        b[:, 0, 1] = b[:, 1, 0] = 22.0 * L * sign
        b[:, 2, 3] = b[:, 3, 2] = -22.0 * L * sign
        b[:, 0, 3] = b[:, 3, 0] = -13.0 * L * sign
        b[:, 1, 2] = b[:, 2, 1] = 13.0 * L * sign
        b[:, 1, 1] = b[:, 3, 3] = 4.0 * L2
        b[:, 1, 3] = b[:, 3, 1] = -3.0 * L2
        b *= c[:, None, None]
        M[:, idx[:, None], idx[None, :]] += b

    plane(np.array([1, 5, 7, 11]), +1.0)
    plane(np.array([2, 4, 8, 10]), -1.0)
    return M


def beam_local_Kg(N, Ip_over_A, L) -> np.ndarray:
    """Geometric stiffness of a beam with constant axial force N (tension positive); local axes, 12x12."""
    ne = len(L)
    K = np.zeros((ne, 12, 12))
    c = N / (30.0 * L)
    L2 = L ** 2

    def plane(idx, sign):
        g = np.zeros((ne, 4, 4))
        g[:, 0, 0] = g[:, 2, 2] = 36.0
        g[:, 0, 2] = g[:, 2, 0] = -36.0
        g[:, 0, 1] = g[:, 1, 0] = 3.0 * L * sign
        g[:, 0, 3] = g[:, 3, 0] = 3.0 * L * sign
        g[:, 1, 2] = g[:, 2, 1] = -3.0 * L * sign
        g[:, 2, 3] = g[:, 3, 2] = -3.0 * L * sign
        g[:, 1, 1] = g[:, 3, 3] = 4.0 * L2
        g[:, 1, 3] = g[:, 3, 1] = -L2
        g *= c[:, None, None]
        K[:, idx[:, None], idx[None, :]] += g

    plane(np.array([1, 5, 7, 11]), +1.0)
    plane(np.array([2, 4, 8, 10]), -1.0)
    t = N * Ip_over_A / L                      # torsion-axial coupling term
    K[:, 3, 3] += t
    K[:, 9, 9] += t
    K[:, 3, 9] -= t
    K[:, 9, 3] -= t
    return K


def beam_equivalent_load(q_local: np.ndarray, L: np.ndarray) -> np.ndarray:
    """Fixed-end equivalent nodal load (ne,12) of a uniform distributed load q_local (ne,3) per unit length, local axes."""
    ne = len(L)
    f = np.zeros((ne, 12))
    qx, qy, qz = q_local[:, 0], q_local[:, 1], q_local[:, 2]
    f[:, 0] = f[:, 6] = qx * L / 2.0
    f[:, 1] = f[:, 7] = qy * L / 2.0
    f[:, 5] = qy * L ** 2 / 12.0
    f[:, 11] = -qy * L ** 2 / 12.0
    f[:, 2] = f[:, 8] = qz * L / 2.0
    f[:, 4] = -qz * L ** 2 / 12.0
    f[:, 10] = qz * L ** 2 / 12.0
    return f


# ------------------------------------------------------------------- shell
_GP = 1.0 / np.sqrt(3.0)
_SHELL_GAUSS = np.array([[-_GP, -_GP], [_GP, -_GP], [_GP, _GP], [-_GP, _GP]])
_NODE_NAT = np.array([[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]])


def _q4(xi: np.ndarray):
    """Bilinear shape functions N (np,4) and natural derivatives dN (np,4,2) at points xi (np,2)."""
    r, s = xi[:, 0:1], xi[:, 1:2]
    rn, sn = _NODE_NAT[:, 0][None], _NODE_NAT[:, 1][None]
    N = 0.25 * (1 + rn * r) * (1 + sn * s)
    dN = np.stack([0.25 * rn * (1 + sn * s), 0.25 * sn * (1 + rn * r)], axis=2)
    return N, dN


def shell_frames(X: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Local frame of every quad from its corner coordinates X (ne,4,3): returns (R (ne,3,3) rows e1,e2,e3,
    local 2-D node coordinates (ne,4,2), element centre (ne,3)). e3 follows the right-hand rule of the node order.
    """
    c = X.mean(axis=1)
    n = np.cross(X[:, 2] - X[:, 0], X[:, 3] - X[:, 1])
    ln = np.linalg.norm(n, axis=1)
    if np.any(ln < 1e-14):
        raise StructuralError("A shell element is degenerate (zero area).")
    e3 = n / ln[:, None]
    # local x = projection of the global X axis on the element plane (global Y where the shell is normal to X), so
    # stress resultants of neighbouring elements are expressed in a consistent frame
    ref = np.tile(np.array([1.0, 0.0, 0.0]), (len(e3), 1))
    ref[np.abs(e3[:, 0]) > 0.9] = np.array([0.0, 1.0, 0.0])
    a = ref - np.einsum("ei,ei->e", ref, e3)[:, None] * e3
    e1 = a / np.linalg.norm(a, axis=1, keepdims=True)
    e2 = np.cross(e3, e1)
    R = np.stack([e1, e2, e3], axis=1)
    xy = np.einsum("eni,eji->enj", X - c[:, None, :], R)[:, :, :2]
    return R, xy, c


@dataclass
class ShellKin:
    """Strain-displacement operators of all shell elements at the 2x2 Gauss points (local 2-D axes)."""
    Bc: np.ndarray     # (ne,4,3,8)   membrane, compatible, dofs (u1 v1 .. u4 v4)
    Bi: np.ndarray     # (ne,4,3,4)   membrane, incompatible modes
    Bb: np.ndarray     # (ne,4,3,12)  bending curvature, dofs (w1 tx1 ty1 .. )
    Bs: np.ndarray     # (ne,4,2,12)  MITC4 transverse shear
    wdet: np.ndarray   # (ne,4)
    area: np.ndarray   # (ne,)


def shell_kinematics(xy: np.ndarray) -> ShellKin:
    ne = len(xy)
    N, dN = _q4(_SHELL_GAUSS)                                   # (4,4), (4,4,2)
    J = np.einsum("gnk,enj->egkj", dN, xy)                      # rows: xi, eta ; cols: x, y
    detJ = np.linalg.det(J)
    if np.any(detJ <= 0):
        raise StructuralError("A shell element is inverted or badly distorted (check the node order).")
    Ji = np.linalg.inv(J)
    dNdx = np.einsum("egjk,gnk->egnj", Ji, dN)                  # (ne,4,4,2): d/dx, d/dy
    wdet = detJ                                                 # Gauss weights are 1 for the 2x2 rule

    Bc = np.zeros((ne, 4, 3, 8))
    Bc[..., 0, 0::2] = dNdx[..., 0]
    Bc[..., 1, 1::2] = dNdx[..., 1]
    Bc[..., 2, 0::2] = dNdx[..., 1]
    Bc[..., 2, 1::2] = dNdx[..., 0]

    # incompatible modes (1-xi^2), (1-eta^2) with the element-centre Jacobian (Taylor et al.) for the patch test
    _, dN0 = _q4(np.zeros((1, 2)))
    J0 = np.einsum("nk,enj->ekj", dN0[0], xy)
    detJ0 = np.linalg.det(J0)
    J0i = np.linalg.inv(J0)
    Bi = np.zeros((ne, 4, 3, 4))
    for g, (r, s) in enumerate(_SHELL_GAUSS):
        d1 = np.einsum("ejk,k->ej", J0i, np.array([-2.0 * r, 0.0]))     # grad of (1 - xi^2)
        d2 = np.einsum("ejk,k->ej", J0i, np.array([0.0, -2.0 * s]))     # grad of (1 - eta^2)
        scale = (detJ0 / detJ[:, g])
        Bi[:, g, 0, 0] = d1[:, 0] * scale
        Bi[:, g, 2, 0] = d1[:, 1] * scale
        Bi[:, g, 0, 1] = d2[:, 0] * scale
        Bi[:, g, 2, 1] = d2[:, 1] * scale
        Bi[:, g, 1, 2] = d1[:, 1] * scale
        Bi[:, g, 2, 2] = d1[:, 0] * scale
        Bi[:, g, 1, 3] = d2[:, 1] * scale
        Bi[:, g, 2, 3] = d2[:, 0] * scale

    Bb = np.zeros((ne, 4, 3, 12))
    Bb[..., 0, 2::3] = dNdx[..., 0]            # kappa_x  = d(ty)/dx
    Bb[..., 1, 1::3] = -dNdx[..., 1]           # kappa_y  = -d(tx)/dy
    Bb[..., 2, 2::3] = dNdx[..., 1]            # kappa_xy = d(ty)/dy - d(tx)/dx
    Bb[..., 2, 1::3] = -dNdx[..., 0]

    # MITC4 transverse shear: covariant strains tied at the mid-sides, interpolated linearly
    def nat_row(pt, comp):
        Np, dNp = _q4(np.array([pt]))
        Jp = np.einsum("nk,enj->ekj", dNp[0], xy)               # (ne,2,2)
        row = np.zeros((ne, 12))
        row[:, 0::3] = dNp[0][:, comp][None, :]
        row[:, 1::3] = -Np[0][None, :] * Jp[:, comp, 1][:, None]
        row[:, 2::3] = Np[0][None, :] * Jp[:, comp, 0][:, None]
        return row

    rA, rB = nat_row((0.0, 1.0), 0), nat_row((0.0, -1.0), 0)     # gamma_xi,z   at A(0,1), B(0,-1)
    rC, rD = nat_row((1.0, 0.0), 1), nat_row((-1.0, 0.0), 1)     # gamma_eta,z  at C(1,0), D(-1,0)
    Bs = np.zeros((ne, 4, 2, 12))
    for g, (r, s) in enumerate(_SHELL_GAUSS):
        nat = np.zeros((ne, 2, 12))
        nat[:, 0] = 0.5 * (1 + s) * rA + 0.5 * (1 - s) * rB
        nat[:, 1] = 0.5 * (1 + r) * rC + 0.5 * (1 - r) * rD
        Bs[:, g] = np.einsum("ejk,ekn->ejn", Ji[:, g], nat)
    area = wdet.sum(axis=1)
    return ShellKin(Bc, Bi, Bb, Bs, wdet, area)


def shell_constitutive(E, nu, t):
    """Membrane D_m (3x3 per element, includes t), bending D_b, shear stiffness scalar kappa*G*t."""
    c = E / (1 - nu ** 2)
    Dm = np.zeros((len(E), 3, 3))
    Dm[:, 0, 0] = Dm[:, 1, 1] = 1.0
    Dm[:, 0, 1] = Dm[:, 1, 0] = nu
    Dm[:, 2, 2] = (1 - nu) / 2.0
    Db = Dm * (c * t ** 3 / 12.0)[:, None, None]
    Dm = Dm * (c * t)[:, None, None]
    G = E / (2 * (1 + nu))
    return Dm, Db, (5.0 / 6.0) * G * t


def shell_local_K(kin: ShellKin, E, nu, t) -> Tuple[np.ndarray, np.ndarray]:
    """
    Local 24x24 stiffness (dofs per node u v w tx ty tz) and the condensation matrix
    `alpha = -Hu` (ne,4,8) that recovers the incompatible-mode amplitudes from the membrane displacements.
    """
    ne = len(E)
    Dm, Db, ks = shell_constitutive(E, nu, t)
    wd = kin.wdet
    Kcc = np.einsum("eg,egia,eij,egjb->eab", wd, kin.Bc, Dm, kin.Bc)
    Kci = np.einsum("eg,egia,eij,egjb->eab", wd, kin.Bc, Dm, kin.Bi)
    Kii = np.einsum("eg,egia,eij,egjb->eab", wd, kin.Bi, Dm, kin.Bi)
    H = np.linalg.solve(Kii, np.transpose(Kci, (0, 2, 1)))            # (ne,4,8)
    Km = Kcc - np.einsum("eab,ebc->eac", Kci, H)
    Kb = np.einsum("eg,egia,eij,egjb->eab", wd, kin.Bb, Db, kin.Bb)
    Ks = np.einsum("eg,egia,egib->eab", wd, kin.Bs, kin.Bs) * ks[:, None, None]
    K = np.zeros((ne, 24, 24))
    mi = np.array([6 * i + c for i in range(4) for c in (0, 1)])
    pi = np.array([6 * i + c for i in range(4) for c in (2, 3, 4)])
    K[:, mi[:, None], mi[None, :]] += Km
    K[:, pi[:, None], pi[None, :]] += Kb + Ks
    # drilling stabilisation: small stiffness against the rotation about the element normal
    kd = 1e-3 * Db[:, 0, 0] * 1.0
    for i in range(4):
        K[:, 6 * i + 5, 6 * i + 5] += kd
    return K, -H


def shell_local_M(xy, kin: ShellKin, rho, t) -> np.ndarray:
    """Consistent translational mass (+ small lumped rotary inertia), local 24x24."""
    ne = len(rho)
    N, _ = _q4(_SHELL_GAUSS)
    Mn = np.einsum("eg,gi,gj->eij", kin.wdet, N, N) * (rho * t)[:, None, None]
    M = np.zeros((ne, 24, 24))
    for i in range(4):
        for j in range(4):
            for c in range(3):
                M[:, 6 * i + c, 6 * j + c] = Mn[:, i, j]
    ri = rho * t ** 3 * kin.area / 48.0 + rho * t * kin.area * 1e-6 * kin.area / 4.0
    for i in range(4):
        for c in (3, 4, 5):
            M[:, 6 * i + c, 6 * i + c] = ri
    return M


def shell_pressure_local(kin: ShellKin, p: np.ndarray) -> np.ndarray:
    """Equivalent nodal forces (ne,24) of a uniform pressure p (ne,) acting along +local z (the element normal)."""
    N, _ = _q4(_SHELL_GAUSS)
    w = np.einsum("eg,gi->ei", kin.wdet, N) * p[:, None]
    f = np.zeros((len(p), 24))
    f[:, 2::6] = w
    return f


def shell_stress_resultants(kin: ShellKin, E, nu, t, ue_local: np.ndarray, alpha_map: np.ndarray):
    """
    Membrane forces n (ne,4,3) [per unit length: nx, ny, nxy], moments m (ne,4,3), shear forces q (ne,4,2) at the Gauss points.
    ue_local (ne,24) are the element DOFs in the local frame.
    """
    Dm, Db, ks = shell_constitutive(E, nu, t)
    mi = np.array([6 * i + c for i in range(4) for c in (0, 1)])
    pi = np.array([6 * i + c for i in range(4) for c in (2, 3, 4)])
    um, up = ue_local[:, mi], ue_local[:, pi]
    alpha = np.einsum("eab,eb->ea", alpha_map, um)
    eps_m = np.einsum("egib,eb->egi", kin.Bc, um) + np.einsum("egia,ea->egi", kin.Bi, alpha)
    kap = np.einsum("egib,eb->egi", kin.Bb, up)
    gam = np.einsum("egib,eb->egi", kin.Bs, up)
    n = np.einsum("eij,egj->egi", Dm, eps_m)
    m = np.einsum("eij,egj->egi", Db, kap)
    q = gam * ks[:, None, None]
    return n, m, q


def shell_gauss_to_nodes(vals: np.ndarray) -> np.ndarray:
    """Bilinear extrapolation of Gauss-point values (ne,4,...) to the element corners (ne,4,...)."""
    N, _ = _q4(_SHELL_GAUSS)                           # (g, i): N_i(gp_g)
    A = np.linalg.inv(N)                               # corner values = inv(N) @ gauss values  (since gauss = N @ corner)
    return np.einsum("ig,eg...->ei...", A, vals)
