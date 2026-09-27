"""The small amount of linear algebra the risk engine needs, in pure Python.

``numpy`` is an *optional* dependency of this project - the core install is DuckDB and pytz -
and a module that gates trades must not need an extra to run. A book is 12 to 40 positions, so
the matrices here are at most 40x40 and every operation below is microseconds. Nothing is
clever; everything is checked.

Symmetric positive-definite matrices are the only kind that appear, because they are
covariance matrices. That is used deliberately: Cholesky both solves systems and *proves* the
matrix is a valid covariance, so a failure to factorise is a caught estimation error rather
than a wrong answer computed confidently.
"""
from __future__ import annotations

import math

Matrix = list[list[float]]
Vector = list[float]


class NotPositiveDefinite(ValueError):
    """Raised when a matrix that must be a covariance is not one.

    This is a real and common failure, not a theoretical one: a sample covariance matrix
    estimated from fewer observations than assets is singular by construction, and one
    estimated from barely more is numerically indefinite. Refusing here is the point - the
    alternative is a portfolio variance that comes back negative and gets square-rooted.
    """


def mat_vec(a: Matrix, x: Vector) -> Vector:
    if not a or len(a[0]) != len(x):
        raise ValueError(f"shape mismatch: {len(a)}x{len(a[0]) if a else 0} by {len(x)}")
    return [sum(row[j] * x[j] for j in range(len(x))) for row in a]


def quad_form(a: Matrix, x: Vector) -> float:
    """x'Ax. For a covariance matrix and weights this is the portfolio variance."""
    return sum(x[i] * v for i, v in enumerate(mat_vec(a, x)))


def cholesky(a: Matrix) -> Matrix:
    """Lower-triangular L with L L' = A. Raises if A is not positive definite."""
    n = len(a)
    if any(len(row) != n for row in a):
        raise ValueError("matrix must be square")
    lo: Matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = a[i][j] - sum(lo[i][k] * lo[j][k] for k in range(j))
            if i == j:
                if s <= 0:
                    raise NotPositiveDefinite(
                        f"leading minor {i + 1} is not positive (pivot {s:.3e}); the matrix "
                        f"is singular or indefinite, which for a covariance estimate means "
                        f"too few observations for the number of assets")
                lo[i][j] = math.sqrt(s)
            else:
                lo[i][j] = s / lo[j][j]
    return lo


def solve_spd(a: Matrix, b: Vector) -> Vector:
    """Solve A x = b for symmetric positive-definite A, by Cholesky."""
    lo = cholesky(a)
    n = len(b)
    y = [0.0] * n
    for i in range(n):
        y[i] = (b[i] - sum(lo[i][k] * y[k] for k in range(i))) / lo[i][i]
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (y[i] - sum(lo[k][i] * x[k] for k in range(i + 1, n))) / lo[i][i]
    return x


def is_pd(a: Matrix) -> bool:
    try:
        cholesky(a)
    except (NotPositiveDefinite, ValueError):
        return False
    return True


def nearest_pd(a: Matrix, *, jitter: float = 1e-10, tries: int = 40) -> Matrix:
    """Add the smallest ridge to the diagonal that makes ``a`` factorise.

    A last resort, and it is reported rather than silent: the caller is told how much was
    added, because a matrix needing a large ridge is one whose correlations are not estimated
    well enough to size a position from.
    """
    n = len(a)
    scale = sum(a[i][i] for i in range(n)) / n if n else 1.0
    eps = jitter * max(scale, 1e-12)
    out = [row[:] for row in a]
    for _ in range(tries):
        if is_pd(out):
            return out
        for i in range(n):
            out[i][i] += eps
        eps *= 10
    raise NotPositiveDefinite("no ridge up to 1e30 x scale made the matrix factorise")


def eigen_sym(a: Matrix, *, sweeps: int = 100, tol: float = 1e-12) -> tuple[Vector, Matrix]:
    """Eigenvalues and eigenvectors of a symmetric matrix, by the Jacobi rotation method.

    Returned sorted descending. Jacobi is slow for large matrices and exact enough for small
    symmetric ones, which is all that occurs here; it is used for the principal-component view
    of a covariance matrix, where the first eigenvalue's share of the trace says how much of
    the book's risk is a single common factor.
    """
    n = len(a)
    v: Matrix = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    m = [row[:] for row in a]
    for _ in range(sweeps):
        off = math.sqrt(sum(m[i][j] ** 2 for i in range(n) for j in range(n) if i != j))
        if off < tol:
            break
        for p in range(n - 1):
            for q in range(p + 1, n):
                if abs(m[p][q]) < tol:
                    continue
                theta = (m[q][q] - m[p][p]) / (2 * m[p][q])
                t = (math.copysign(1.0, theta)
                     / (abs(theta) + math.sqrt(theta * theta + 1)))
                c = 1 / math.sqrt(t * t + 1)
                s = t * c
                for k in range(n):
                    mkp, mkq = m[k][p], m[k][q]
                    m[k][p] = c * mkp - s * mkq
                    m[k][q] = s * mkp + c * mkq
                for k in range(n):
                    mpk, mqk = m[p][k], m[q][k]
                    m[p][k] = c * mpk - s * mqk
                    m[q][k] = s * mpk + c * mqk
                for k in range(n):
                    vkp, vkq = v[k][p], v[k][q]
                    v[k][p] = c * vkp - s * vkq
                    v[k][q] = s * vkp + c * vkq
    vals = [m[i][i] for i in range(n)]
    order = sorted(range(n), key=lambda i: -vals[i])
    return ([vals[i] for i in order],
            [[v[r][i] for i in order] for r in range(n)])


def corr_from_cov(cov: Matrix) -> Matrix:
    n = len(cov)
    sd = [math.sqrt(cov[i][i]) if cov[i][i] > 0 else 0.0 for i in range(n)]
    return [[(cov[i][j] / (sd[i] * sd[j]) if sd[i] > 0 and sd[j] > 0 else 0.0)
             for j in range(n)] for i in range(n)]
