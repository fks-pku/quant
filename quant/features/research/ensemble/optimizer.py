import logging
from typing import List

import numpy as np

logger = logging.getLogger(__name__)


def equal_weight(n: int) -> List[float]:
    if n == 0:
        return []
    return [1.0 / n] * n


def inverse_vol(volatilities: List[float]) -> List[float]:
    if not volatilities:
        return []
    inv = [1.0 / max(v, 1e-10) for v in volatilities]
    total = sum(inv)
    return [w / total for w in inv]


def _effective_max_weight(max_weight: float, n: int) -> float:
    try:
        cap = float(max_weight)
    except (TypeError, ValueError):
        cap = 1.0
    if not np.isfinite(cap):
        cap = 1.0
    return min(max(cap, 1.0 / n), 1.0)


def _cap_and_normalize(raw_weights: np.ndarray, max_weight: float) -> np.ndarray:
    n = len(raw_weights)
    if n == 0:
        return raw_weights

    cap = _effective_max_weight(max_weight, n)
    weights = np.asarray(raw_weights, dtype=float)
    weights = np.where(np.isfinite(weights) & (weights > 0.0), weights, 0.0)
    total = float(weights.sum())
    if total <= 0.0:
        weights = np.ones(n, dtype=float) / n
    else:
        weights = weights / total

    for _ in range(n):
        over = weights > cap + 1e-12
        if not np.any(over):
            break
        excess = float(np.sum(weights[over] - cap))
        weights[over] = cap
        under = ~over
        if not np.any(under):
            break
        under_total = float(weights[under].sum())
        if under_total <= 0.0:
            weights[under] = excess / int(np.sum(under))
        else:
            weights[under] += weights[under] / under_total * excess

    weights = np.minimum(weights, cap)
    total = float(weights.sum())
    if total <= 0.0:
        return np.ones(n, dtype=float) / n
    return weights / total


def _inverse_vol_with_cap(volatilities: List[float], max_weight: float) -> List[float]:
    inv = np.array([1.0 / max(v, 1e-10) for v in volatilities], dtype=float)
    return _cap_and_normalize(inv, max_weight).tolist()


def _build_covariance_matrix(corr_matrix: List[List[float]], volatilities: List[float]):
    n = len(volatilities)
    try:
        corr = np.asarray(corr_matrix, dtype=float)
        vols = np.asarray(volatilities, dtype=float)
    except (TypeError, ValueError):
        return None
    if corr.shape != (n, n) or vols.shape != (n,):
        return None
    corr = np.nan_to_num(corr, nan=0.0, posinf=0.0, neginf=0.0)
    corr = (corr + corr.T) / 2.0
    np.fill_diagonal(corr, 1.0)
    vols = np.where(np.isfinite(vols) & (vols > 0.0), vols, 1e-10)
    return corr * np.outer(vols, vols)


def equal_risk(corr_matrix: List[List[float]], volatilities: List[float], max_weight: float = 0.25) -> List[float]:
    n = len(volatilities)
    if n == 0:
        return []
    if n == 1:
        return [1.0]

    cov = _build_covariance_matrix(corr_matrix, volatilities)
    if cov is None:
        return _inverse_vol_with_cap(volatilities, max_weight)

    fallback = _inverse_vol_with_cap(volatilities, max_weight)
    x0 = np.asarray(fallback, dtype=float)
    cap = _effective_max_weight(max_weight, n)

    try:
        from scipy.optimize import minimize
    except ImportError:
        return fallback

    def objective(weights):
        weights = np.asarray(weights, dtype=float)
        portfolio_var = float(weights @ cov @ weights)
        if not np.isfinite(portfolio_var) or portfolio_var <= 1e-20:
            return 1e6
        risk_shares = weights * (cov @ weights) / portfolio_var
        if not np.all(np.isfinite(risk_shares)):
            return 1e6
        target = 1.0 / n
        return float(np.sum((risk_shares - target) ** 2))

    try:
        result = minimize(
            objective,
            x0,
            method="SLSQP",
            bounds=[(0.0, cap)] * n,
            constraints=({"type": "eq", "fun": lambda weights: float(np.sum(weights) - 1.0)},),
            options={"maxiter": 1000, "ftol": 1e-12},
        )
    except Exception as exc:
        logger.warning("ERC optimizer failed; using inverse-vol fallback: %s", exc)
        return fallback

    if not getattr(result, "success", False):
        return fallback

    weights = _cap_and_normalize(np.asarray(result.x, dtype=float), max_weight)
    if len(weights) != n or not np.all(np.isfinite(weights)):
        return fallback
    return weights.tolist()
