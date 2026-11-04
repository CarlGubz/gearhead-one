"""
Linear solvers for the symmetric positive-definite systems the engine produces.

* `DirectSolver`  sparse LU (fill-reducing ordering chosen from the matrix density).
* `SchwarzPCG`    preconditioned conjugate gradients with a **two-level additive Schwarz** preconditioner:
                  overlapping subdomain solves (each factorised directly) + a coarse space spanned by the rigid-body
                  motions (or constants for scalar problems) of every subdomain. The preconditioner is symmetric
                  positive definite by construction, so CG is valid, and memory stays far below one global factorisation.

`make_solver(options, n, geometry)` picks one: `auto` uses the iterative solver above `auto_threshold` free DOFs.
No external dependency beyond SciPy.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from scipy import sparse
from scipy.sparse import linalg as spla


class LinearSolveError(RuntimeError):
    """The linear system could not be solved (singular, or the iteration did not converge)."""


@dataclass
class SolverOptions:
    mode: str = "auto"                 # auto | direct | iterative
    tol: float = 1e-8                  # relative residual for CG
    max_iter: int = 1000
    auto_threshold: int = 80_000       # free DOFs above which `auto` goes iterative
    direct_fallback_limit: int = 150_000   # if CG fails, retry with LU only below this size
    block_dofs: int = 3000             # target subdomain size (before overlap)
    stats: Dict[str, object] = field(default_factory=dict)

    def note(self, **kw) -> None:
        s = self.stats
        for k, v in kw.items():
            if k in ("iterations", "solves", "build_s", "solve_s"):
                s[k] = s.get(k, 0) + v
            else:
                s[k] = v

    def summary(self) -> str:
        s = self.stats
        if not s:
            return ""
        if s.get("method") == "direct":
            return f"Linear solver: sparse LU ({s.get('n', 0)} equations)"
        return (f"Linear solver: two-level Schwarz PCG ({s.get('n', 0)} equations, {s.get('blocks', 0)} subdomains, "
                f"{s.get('solves', 0)} solve(s), {s.get('iterations', 0)} iterations, "
                f"last relative residual {s.get('residual', 0):.1e})")


@dataclass
class Geometry:
    """Where every unknown lives (used only to build the iterative preconditioner)."""
    xyz: np.ndarray                    # (n, 3) position of each unknown
    comp: Optional[np.ndarray] = None  # (n,) 0/1/2 displacement component, or None for scalar unknowns
    node: Optional[np.ndarray] = None  # (n,) node index (keeps a node's components together)

    def subset(self, idx: np.ndarray) -> "Geometry":
        return Geometry(self.xyz[idx], None if self.comp is None else self.comp[idx],
                        None if self.node is None else self.node[idx])


# ------------------------------------------------------------------ direct
def _splu(A: sparse.spmatrix):
    A = A.tocsc()
    spec = "MMD_AT_PLUS_A" if A.nnz / max(A.shape[0], 1) < 110 else "COLAMD"
    return spla.splu(A, permc_spec=spec)


class DirectSolver:
    method = "direct"

    def __init__(self, A: sparse.spmatrix, opts: Optional[SolverOptions] = None):
        t = time.time()
        try:
            self.lu = _splu(A)
        except RuntimeError as exc:
            raise LinearSolveError("singular matrix") from exc
        if opts is not None:
            opts.note(method="direct", n=A.shape[0], build_s=time.time() - t)

    def solve(self, b: np.ndarray) -> np.ndarray:
        return self.lu.solve(b)


# ---------------------------------------------------------------- iterative
def _rcb_blocks(geo: Geometry, target: int) -> List[np.ndarray]:
    """Recursive coordinate bisection of the unknowns into blocks of about `target` unknowns."""
    n = len(geo.xyz)
    node = geo.node if geo.node is not None else np.arange(n)
    comp = geo.comp if geo.comp is not None else np.zeros(n, dtype=int)
    out: List[np.ndarray] = []
    stack = [np.arange(n)]
    while stack:
        idx = stack.pop()
        if len(idx) <= target:
            out.append(idx)
            continue
        pts = geo.xyz[idx]
        axis = int(np.argmax(pts.max(axis=0) - pts.min(axis=0)))
        order = np.lexsort((comp[idx], node[idx], pts[:, axis]))  # a node's components stay adjacent
        idx = idx[order]
        # never cut between the components of one node
        half = len(idx) // 2
        while 0 < half < len(idx) and node[idx[half]] == node[idx[half - 1]]:
            half += 1
        if half <= 0 or half >= len(idx):
            out.append(idx)
            continue
        stack.append(idx[:half])
        stack.append(idx[half:])
    return out


def _coarse_space(A: sparse.csr_matrix, geo: Geometry, blocks: List[np.ndarray]):
    """Rigid-body (vector) or constant (scalar) vectors of every block as columns of a sparse Z."""
    n = A.shape[0]
    rows, cols, vals = [], [], []
    vector = geo.comp is not None
    ncol = 0
    for idx in blocks:
        p = geo.xyz[idx]
        c = p.mean(axis=0)
        if vector:
            comp = geo.comp[idx]
            r = p - c
            modes = []
            for k in range(3):                                   # translations
                modes.append((comp == k).astype(float))
            modes.append(np.where(comp == 0, -r[:, 1], np.where(comp == 1, r[:, 0], 0.0)))   # about z
            modes.append(np.where(comp == 1, -r[:, 2], np.where(comp == 2, r[:, 1], 0.0)))   # about x
            modes.append(np.where(comp == 2, -r[:, 0], np.where(comp == 0, r[:, 2], 0.0)))   # about y
        else:
            modes = [np.ones(len(idx))]
        for m in modes:
            if np.any(m != 0):
                rows.append(idx)
                cols.append(np.full(len(idx), ncol))
                vals.append(m)
                ncol += 1
    Z = sparse.coo_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(n, ncol)).tocsr()
    return Z


class SchwarzPCG:
    method = "iterative"

    def __init__(self, A: sparse.spmatrix, geo: Geometry, opts: SolverOptions):
        t0 = time.time()
        A = A.tocsr()
        self.A, self.opts = A, opts
        n = A.shape[0]
        blocks = _rcb_blocks(geo, max(500, int(opts.block_dofs)))
        self.sub = []
        for idx in blocks:
            ext = np.unique(A[idx].indices)          # one layer of overlap through the matrix graph
            Aloc = A[ext][:, ext].tocsc()
            try:
                self.sub.append((ext, _splu(Aloc)))
            except RuntimeError as exc:
                raise LinearSolveError("a subdomain matrix is singular") from exc
        Z = _coarse_space(A, geo, blocks)
        A0 = (Z.T @ A @ Z).toarray()
        w, V = np.linalg.eigh(0.5 * (A0 + A0.T))
        keep = w > 1e-10 * w.max()
        self.Z = Z
        self.coarse = (V[:, keep] / w[keep]) @ V[:, keep].T       # pseudo-inverse of the coarse operator
        opts.note(method="iterative", n=n, blocks=len(blocks), build_s=time.time() - t0)

    def _apply(self, r: np.ndarray) -> np.ndarray:
        z = self.Z @ (self.coarse @ (self.Z.T @ r))
        for ext, lu in self.sub:
            z[ext] += lu.solve(r[ext])
        return z

    def solve(self, b: np.ndarray) -> np.ndarray:
        n = len(b)
        M = spla.LinearOperator((n, n), matvec=self._apply, dtype=float)
        count = [0]

        def cb(_):
            count[0] += 1

        t = time.time()
        x, info = spla.cg(self.A, b, rtol=self.opts.tol, atol=0.0, maxiter=self.opts.max_iter, M=M, callback=cb)
        res = float(np.linalg.norm(self.A @ x - b) / max(np.linalg.norm(b), 1e-300))
        self.opts.note(iterations=count[0], solves=1, solve_s=time.time() - t, residual=res)
        if info != 0 and res > 100 * self.opts.tol:
            raise LinearSolveError(f"PCG did not converge in {count[0]} iterations (relative residual {res:.1e})")
        return x


# ------------------------------------------------------------------ factory
class _Fallback:
    """Iterative solve with a direct fallback when CG stalls and the system is small enough."""

    method = "iterative"

    def __init__(self, A, geo, opts):
        self.A, self.geo, self.opts = A, geo, opts
        self.iter = SchwarzPCG(A, geo, opts)
        self.direct = None

    def solve(self, b):
        try:
            return self.iter.solve(b)
        except LinearSolveError:
            if self.A.shape[0] > self.opts.direct_fallback_limit:
                raise
            if self.direct is None:
                self.direct = DirectSolver(self.A, self.opts)
            return self.direct.solve(b)


def make_solver(A: sparse.spmatrix, opts: Optional[SolverOptions] = None, geo: Optional[Geometry] = None):
    """Factorise/prepare `A` according to the options. Raises LinearSolveError for singular systems."""
    opts = opts or SolverOptions()
    n = A.shape[0]
    iterative = opts.mode == "iterative" or (opts.mode == "auto" and n > opts.auto_threshold)
    if iterative and geo is not None and len(geo.xyz) == n:
        return _Fallback(A.tocsr(), geo, opts)
    return DirectSolver(A, opts)
