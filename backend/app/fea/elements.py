"""
Element library: shape functions, Gauss rules and face topology.

Node ordering follows CalculiX/Abaqus so .inp files round-trip:

  C3D4   4-node tetrahedron
  C3D10  10-node tetrahedron; mid-nodes 5..10 sit on edges 1-2, 2-3, 3-1, 1-4, 2-4, 3-4
  C3D8   8-node brick; corners bottom 1-4 (counter-clockwise), top 5-8
  C3D20  20-node brick; mids 9-12 bottom edges (1-2,2-3,3-4,4-1),
         13-16 top edges (5-6,6-7,7-8,8-5), 17-20 verticals (1-5,2-6,3-7,4-8)

All functions are vectorised over Gauss points (leading axis).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Tuple

import numpy as np

# --------------------------------------------------------------- Gauss rules
def _line(n: int) -> Tuple[np.ndarray, np.ndarray]:
    return np.polynomial.legendre.leggauss(n)


def _hex_rule(n: int) -> Tuple[np.ndarray, np.ndarray]:
    x, w = _line(n)
    pts, wts = [], []
    for k in range(n):
        for j in range(n):
            for i in range(n):
                pts.append([x[i], x[j], x[k]])
                wts.append(w[i] * w[j] * w[k])
    return np.array(pts), np.array(wts)


def _quad_rule(n: int) -> Tuple[np.ndarray, np.ndarray]:
    x, w = _line(n)
    pts, wts = [], []
    for j in range(n):
        for i in range(n):
            pts.append([x[i], x[j]])
            wts.append(w[i] * w[j])
    return np.array(pts), np.array(wts)


_TET1 = (np.array([[0.25, 0.25, 0.25]]), np.array([1.0 / 6.0]))
_A, _B = 0.5854101966249685, 0.1381966011250105
_TET4PT = (np.array([[_B, _B, _B], [_A, _B, _B], [_B, _A, _B], [_B, _B, _A]]), np.full(4, 1.0 / 24.0))

# Degree-4 triangle rule (Dunavant), weights sum to 1/2 (area of the reference triangle)
_P1, _P2 = 0.445948490915965, 0.091576213509771
_TRI6PT = (
    np.array([[_P1, _P1], [1 - 2 * _P1, _P1], [_P1, 1 - 2 * _P1],
              [_P2, _P2], [1 - 2 * _P2, _P2], [_P2, 1 - 2 * _P2]]),
    np.array([0.223381589678011] * 3 + [0.109951743655322] * 3) / 2.0,
)
_TRI3PT = (np.array([[1 / 6, 1 / 6], [2 / 3, 1 / 6], [1 / 6, 2 / 3]]), np.full(3, 1.0 / 6.0))


# ---------------------------------------------------------- Solid elements
def _tet4(xi: np.ndarray):
    r, s, t = xi.T
    N = np.stack([1 - r - s - t, r, s, t], axis=1)
    dN = np.zeros((len(xi), 4, 3))
    dN[:, 0, :] = -1.0
    dN[:, 1, 0] = 1.0
    dN[:, 2, 1] = 1.0
    dN[:, 3, 2] = 1.0
    return N, dN


_DL = np.array([[-1.0, -1.0, -1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
_TET10_MIDS = [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)]


def _tet10(xi: np.ndarray):
    r, s, t = xi.T
    L = np.stack([1 - r - s - t, r, s, t], axis=1)  # (ng,4)
    ng = len(xi)
    N = np.zeros((ng, 10))
    dNdL = np.zeros((ng, 10, 4))
    for i in range(4):
        N[:, i] = L[:, i] * (2 * L[:, i] - 1)
        dNdL[:, i, i] = 4 * L[:, i] - 1
    for m, (i, j) in enumerate(_TET10_MIDS):
        N[:, 4 + m] = 4 * L[:, i] * L[:, j]
        dNdL[:, 4 + m, i] = 4 * L[:, j]
        dNdL[:, 4 + m, j] = 4 * L[:, i]
    dN = np.einsum("gnk,kj->gnj", dNdL, _DL)
    return N, dN


_HEX_NODES = np.array([
    [-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
    [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1],
    [0, -1, -1], [1, 0, -1], [0, 1, -1], [-1, 0, -1],
    [0, -1, 1], [1, 0, 1], [0, 1, 1], [-1, 0, 1],
    [-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0],
], dtype=float)


def _hex8(xi: np.ndarray):
    ng = len(xi)
    N = np.zeros((ng, 8))
    dN = np.zeros((ng, 8, 3))
    x, y, z = xi.T
    for i in range(8):
        a, b, c = _HEX_NODES[i]
        f, g, h = 1 + a * x, 1 + b * y, 1 + c * z
        N[:, i] = f * g * h / 8
        dN[:, i, 0] = a * g * h / 8
        dN[:, i, 1] = b * f * h / 8
        dN[:, i, 2] = c * f * g / 8
    return N, dN


def _hex20(xi: np.ndarray):
    ng = len(xi)
    N = np.zeros((ng, 20))
    dN = np.zeros((ng, 20, 3))
    x, y, z = xi.T
    for i in range(20):
        a, b, c = _HEX_NODES[i]
        if i < 8:
            f, g, h = 1 + a * x, 1 + b * y, 1 + c * z
            s = a * x + b * y + c * z - 2
            N[:, i] = f * g * h * s / 8
            dN[:, i, 0] = a * g * h * (s + f) / 8
            dN[:, i, 1] = b * f * h * (s + g) / 8
            dN[:, i, 2] = c * f * g * (s + h) / 8
        elif a == 0:
            g, h = 1 + b * y, 1 + c * z
            N[:, i] = (1 - x * x) * g * h / 4
            dN[:, i, 0] = -2 * x * g * h / 4
            dN[:, i, 1] = (1 - x * x) * b * h / 4
            dN[:, i, 2] = (1 - x * x) * g * c / 4
        elif b == 0:
            f, h = 1 + a * x, 1 + c * z
            N[:, i] = (1 - y * y) * f * h / 4
            dN[:, i, 0] = (1 - y * y) * a * h / 4
            dN[:, i, 1] = -2 * y * f * h / 4
            dN[:, i, 2] = (1 - y * y) * f * c / 4
        else:
            f, g = 1 + a * x, 1 + b * y
            N[:, i] = (1 - z * z) * f * g / 4
            dN[:, i, 0] = (1 - z * z) * a * g / 4
            dN[:, i, 1] = (1 - z * z) * f * b / 4
            dN[:, i, 2] = -2 * z * f * g / 4
    return N, dN


# ------------------------------------------------------------- Face elements
def _tri3(xi: np.ndarray):
    r, s = xi.T
    N = np.stack([1 - r - s, r, s], axis=1)
    dN = np.zeros((len(xi), 3, 2))
    dN[:, 0, :] = -1.0
    dN[:, 1, 0] = 1.0
    dN[:, 2, 1] = 1.0
    return N, dN


_DL2 = np.array([[-1.0, -1.0], [1.0, 0.0], [0.0, 1.0]])


def _tri6(xi: np.ndarray):
    r, s = xi.T
    L = np.stack([1 - r - s, r, s], axis=1)
    ng = len(xi)
    N = np.zeros((ng, 6))
    dNdL = np.zeros((ng, 6, 3))
    for i in range(3):
        N[:, i] = L[:, i] * (2 * L[:, i] - 1)
        dNdL[:, i, i] = 4 * L[:, i] - 1
    for m, (i, j) in enumerate([(0, 1), (1, 2), (2, 0)]):
        N[:, 3 + m] = 4 * L[:, i] * L[:, j]
        dNdL[:, 3 + m, i] = 4 * L[:, j]
        dNdL[:, 3 + m, j] = 4 * L[:, i]
    return N, np.einsum("gnk,kj->gnj", dNdL, _DL2)


_QUAD_NODES = np.array([[-1, -1], [1, -1], [1, 1], [-1, 1], [0, -1], [1, 0], [0, 1], [-1, 0]], dtype=float)


def _quad4(xi: np.ndarray):
    x, y = xi.T
    N = np.zeros((len(xi), 4))
    dN = np.zeros((len(xi), 4, 2))
    for i in range(4):
        a, b = _QUAD_NODES[i]
        f, g = 1 + a * x, 1 + b * y
        N[:, i] = f * g / 4
        dN[:, i, 0] = a * g / 4
        dN[:, i, 1] = b * f / 4
    return N, dN


def _quad8(xi: np.ndarray):
    x, y = xi.T
    N = np.zeros((len(xi), 8))
    dN = np.zeros((len(xi), 8, 2))
    for i in range(8):
        a, b = _QUAD_NODES[i]
        if i < 4:
            f, g = 1 + a * x, 1 + b * y
            s = a * x + b * y - 1
            N[:, i] = f * g * s / 4
            dN[:, i, 0] = a * g * (s + f) / 4
            dN[:, i, 1] = b * f * (s + g) / 4
        elif a == 0:
            g = 1 + b * y
            N[:, i] = (1 - x * x) * g / 2
            dN[:, i, 0] = -x * g
            dN[:, i, 1] = (1 - x * x) * b / 2
        else:
            f = 1 + a * x
            N[:, i] = (1 - y * y) * f / 2
            dN[:, i, 0] = (1 - y * y) * a / 2
            dN[:, i, 1] = -y * f
    return N, dN


# ----------------------------------------------------------------- Registry
@dataclass(frozen=True)
class FaceType:
    name: str
    n_nodes: int
    n_corner: int
    shape: Callable
    rule: Tuple[np.ndarray, np.ndarray]


FACE_TYPES = {
    "tri3": FaceType("tri3", 3, 3, _tri3, _TRI3PT),
    "tri6": FaceType("tri6", 6, 3, _tri6, _TRI6PT),
    "quad4": FaceType("quad4", 4, 4, _quad4, _quad_rule(2)),
    "quad8": FaceType("quad8", 8, 4, _quad8, _quad_rule(3)),
}


@dataclass(frozen=True)
class ElementType:
    name: str
    n_nodes: int
    n_corner: int
    kind: str  # "tet" | "hex"
    shape: Callable
    rule: Tuple[np.ndarray, np.ndarray]  # stiffness / general integration
    face_type: str
    faces: Tuple[Tuple[int, ...], ...]  # local node lists, corners first
    natural_nodes: np.ndarray  # (n_nodes, 3) natural coordinates of the nodes

    @property
    def n_dof(self) -> int:
        return 3 * self.n_nodes


_TET_CORNERS = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
_TET10_NAT = np.vstack([_TET_CORNERS] + [(_TET_CORNERS[i] + _TET_CORNERS[j]) / 2 for i, j in _TET10_MIDS])

ELEMENT_TYPES = {
    "C3D4": ElementType(
        "C3D4", 4, 4, "tet", _tet4, _TET1, "tri3",
        ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)), _TET_CORNERS),
    "C3D10": ElementType(
        "C3D10", 10, 4, "tet", _tet10, _TET4PT, "tri6",
        ((0, 1, 2, 4, 5, 6), (0, 1, 3, 4, 8, 7), (0, 2, 3, 6, 9, 7), (1, 2, 3, 5, 9, 8)), _TET10_NAT),
    "C3D8": ElementType(
        "C3D8", 8, 8, "hex", _hex8, _hex_rule(2), "quad4",
        ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)), _HEX_NODES[:8]),
    "C3D20": ElementType(
        "C3D20", 20, 8, "hex", _hex20, _hex_rule(3),
        "quad8",
        ((0, 3, 2, 1, 11, 10, 9, 8), (4, 5, 6, 7, 12, 13, 14, 15), (0, 1, 5, 4, 8, 17, 12, 16),
         (1, 2, 6, 5, 9, 18, 13, 17), (2, 3, 7, 6, 10, 19, 14, 18), (3, 0, 4, 7, 11, 16, 15, 19)), _HEX_NODES),
}

# Faces of C3D4/C3D8 are given by corner nodes only (see tuples above).
NAME_ALIASES = {"tet4": "C3D4", "tet10": "C3D10", "hex8": "C3D8", "hex20": "C3D20"}


def element_type(name: str) -> ElementType:
    name = NAME_ALIASES.get(name.lower(), name.upper())
    if name not in ELEMENT_TYPES:
        raise ValueError(f"Unsupported element type '{name}'. Supported: {', '.join(ELEMENT_TYPES)}")
    return ELEMENT_TYPES[name]


def tabulate(et: ElementType, rule=None):
    """Shape functions and derivatives at the Gauss points: (pts, wts, N[ng,nn], dN[ng,nn,3])."""
    pts, wts = rule if rule is not None else et.rule
    N, dN = et.shape(np.asarray(pts))
    return np.asarray(pts), np.asarray(wts), N, dN


def tabulate_face(ft: FaceType):
    pts, wts = ft.rule
    N, dN = ft.shape(np.asarray(pts))
    return np.asarray(pts), np.asarray(wts), N, dN
