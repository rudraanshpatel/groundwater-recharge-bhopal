"""Analytic Hierarchy Process (AHP) for thematic layer weights.

Saaty's method: experts fill a pairwise comparison matrix A where A[i, j]
says how much more important criterion i is than criterion j on the 1-9
scale, with A[j, i] = 1 / A[i, j]. The weights are the normalised principal
eigenvector of A. Judgements are rarely perfectly consistent, so we measure

    lambda_max  principal eigenvalue (equals n for a perfectly consistent matrix)
    CI = (lambda_max - n) / (n - 1)          consistency index
    CR = CI / RI(n)                          consistency ratio

where RI is Saaty's random index. A matrix is accepted when CR < 0.10.

Run ``python -m backend.ahp`` to print the default matrix and its weights.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping, Sequence

import numpy as np

from backend import config

# Saaty's random consistency index for n = 1..15.
RANDOM_INDEX = {
    1: 0.0, 2: 0.0, 3: 0.58, 4: 0.90, 5: 1.12, 6: 1.24, 7: 1.32, 8: 1.41,
    9: 1.45, 10: 1.49, 11: 1.51, 12: 1.48, 13: 1.56, 14: 1.57, 15: 1.59,
}
CR_THRESHOLD = 0.10

# Default pairwise matrix, rows/columns in the order of config.LAYERS:
# rainfall, geology, slope, soil, lulc, drainage_density, lineament_density.
# Judgements follow the ranking common in the central Indian literature
# (rainfall > geology > slope > soil > LULC = drainage > lineament). Its
# eigenvector weights are 0.254, 0.206, 0.160, 0.120, 0.091, 0.091, 0.078,
# within 0.01 of config.DEFAULT_WEIGHTS, with CR = 0.012.
DEFAULT_CRITERIA = list(config.LAYERS)
DEFAULT_MATRIX = np.array(
    [
        [1,   1,   2,   2,   3, 3, 3],
        [1,   1,   1,   2,   2, 2, 3],
        [1/2, 1,   1,   1,   2, 2, 2],
        [1/2, 1/2, 1,   1,   1, 1, 2],
        [1/3, 1/2, 1/2, 1,   1, 1, 1],
        [1/3, 1/2, 1/2, 1,   1, 1, 1],
        [1/3, 1/3, 1/2, 1/2, 1, 1, 1],
    ],
    dtype=float,
)


class AHPError(ValueError):
    """Raised for a malformed pairwise comparison matrix."""


@dataclass
class AHPResult:
    """Outcome of an AHP weight computation."""

    criteria: list[str]
    matrix: list[list[float]]
    weights: dict[str, float]
    lambda_max: float
    consistency_index: float
    random_index: float
    consistency_ratio: float
    is_consistent: bool

    def to_dict(self) -> dict:
        return asdict(self)


def validate_matrix(matrix: np.ndarray, tol: float = 1e-3) -> np.ndarray:
    """Check that ``matrix`` is a valid reciprocal pairwise comparison matrix.

    Returns the matrix as a float array. Raises AHPError when it is not square,
    has non-positive entries, a diagonal other than 1, or A[j, i] != 1/A[i, j].
    """
    a = np.asarray(matrix, dtype=float)
    if a.ndim != 2 or a.shape[0] != a.shape[1]:
        raise AHPError(f"Pairwise matrix must be square, got shape {a.shape}")
    if a.shape[0] < 2:
        raise AHPError("AHP needs at least two criteria")
    if not np.all(np.isfinite(a)) or np.any(a <= 0):
        raise AHPError("All pairwise entries must be positive finite numbers")
    if not np.allclose(np.diag(a), 1.0, atol=tol):
        raise AHPError("Diagonal entries must all be 1")
    if not np.allclose(a * a.T, 1.0, atol=tol):
        bad = np.argwhere(~np.isclose(a * a.T, 1.0, atol=tol))[0]
        raise AHPError(
            f"Matrix is not reciprocal: A[{bad[0]},{bad[1]}]={a[bad[0], bad[1]]:.4g} "
            f"but A[{bad[1]},{bad[0]}]={a[bad[1], bad[0]]:.4g}"
        )
    return a


def principal_eigen(matrix: np.ndarray) -> tuple[float, np.ndarray]:
    """Return (lambda_max, normalised principal eigenvector) of ``matrix``."""
    eigvals, eigvecs = np.linalg.eig(matrix)
    k = int(np.argmax(eigvals.real))
    lam = float(eigvals[k].real)
    vec = np.abs(eigvecs[:, k].real)   # Perron vector has one sign; abs fixes it
    return lam, vec / vec.sum()


def geometric_mean_weights(matrix: np.ndarray) -> np.ndarray:
    """Row geometric-mean approximation of the AHP weights (a cross-check)."""
    a = validate_matrix(matrix)
    gm = np.exp(np.log(a).mean(axis=1))
    return gm / gm.sum()


def compute_ahp(matrix: np.ndarray | Sequence[Sequence[float]] = DEFAULT_MATRIX,
                criteria: Sequence[str] = DEFAULT_CRITERIA) -> AHPResult:
    """Compute weights and consistency statistics for a pairwise matrix."""
    a = validate_matrix(matrix)
    n = a.shape[0]
    if len(criteria) != n:
        raise AHPError(f"{len(criteria)} criteria given for a {n}x{n} matrix")
    if n not in RANDOM_INDEX:
        raise AHPError(f"No random index tabulated for n={n} (max 15)")

    lam, w = principal_eigen(a)
    ci = (lam - n) / (n - 1)
    ri = RANDOM_INDEX[n]
    # For n <= 2 every reciprocal matrix is consistent, RI is 0.
    cr = 0.0 if ri == 0 else ci / ri
    cr = max(cr, 0.0)   # tiny negative values are floating-point noise

    return AHPResult(
        criteria=list(criteria),
        matrix=a.round(6).tolist(),
        weights={c: float(round(x, 6)) for c, x in zip(criteria, w)},
        lambda_max=round(lam, 6),
        consistency_index=round(max(ci, 0.0), 6),
        random_index=ri,
        consistency_ratio=round(cr, 6),
        is_consistent=cr < CR_THRESHOLD,
    )


def matrix_from_weights(weights: Mapping[str, float],
                        criteria: Sequence[str] | None = None) -> np.ndarray:
    """Build the perfectly consistent matrix A[i, j] = w_i / w_j (CR = 0)."""
    criteria = list(criteria or weights.keys())
    w = np.array([float(weights[c]) for c in criteria])
    if np.any(w <= 0):
        raise AHPError("Weights must all be positive")
    return w[:, None] / w[None, :]


def normalize_weights(weights: Mapping[str, float],
                      criteria: Sequence[str] | None = None) -> dict[str, float]:
    """Scale non-negative weights so they sum to 1. Missing criteria get 0."""
    criteria = list(criteria or weights.keys())
    vals = np.array([max(float(weights.get(c, 0.0)), 0.0) for c in criteria])
    total = vals.sum()
    if total <= 0:
        raise AHPError("At least one weight must be positive")
    return {c: float(v / total) for c, v in zip(criteria, vals)}


def default_result() -> AHPResult:
    """AHP result for the built-in default matrix."""
    return compute_ahp(DEFAULT_MATRIX, DEFAULT_CRITERIA)


if __name__ == "__main__":
    res = default_result()
    print("Criteria:", ", ".join(res.criteria))
    for c in res.criteria:
        print(f"  {c:<20s} {res.weights[c]:.3f}   (target {config.DEFAULT_WEIGHTS[c]:.2f})")
    print(f"lambda_max = {res.lambda_max:.4f}")
    print(f"CI = {res.consistency_index:.4f}, RI = {res.random_index}, CR = {res.consistency_ratio:.4f}")
    print("Consistent (CR < 0.10):", res.is_consistent)
