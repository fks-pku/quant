import math
from statistics import NormalDist
from typing import Any, Optional

import numpy as np
import pandas as pd


def compute_dsr(returns: Any, n_trials: int = 1, risk_free_rate: float = 0.0) -> Optional[float]:
    series = pd.Series(returns).dropna()
    if len(series) < 30:
        return None

    values = series.astype(float).to_numpy()
    excess = values - float(risk_free_rate) / 252.0
    mean = float(np.mean(excess))
    std = float(np.std(excess, ddof=1))
    if std <= 0.0 or not math.isfinite(std):
        return 0.0

    sr_single = mean / std
    if sr_single <= 0.0 or not math.isfinite(sr_single):
        return 0.0

    trials = max(1, int(n_trials or 1))
    skew = float(pd.Series(excess).skew())
    kurtosis = float(pd.Series(excess).kurtosis() + 3.0)
    if not math.isfinite(skew):
        skew = 0.0
    if not math.isfinite(kurtosis) or kurtosis <= 0.0:
        kurtosis = 3.0

    v_single = (1.0 - skew * sr_single + ((kurtosis - 1.0) / 4.0) * sr_single * sr_single) / (len(excess) - 1.0)
    if v_single <= 0.0 or not math.isfinite(v_single):
        return 0.0

    e_max = _expected_max_sharpe(trials, v_single)
    z_score = (sr_single - e_max) / math.sqrt(v_single)

    if not math.isfinite(z_score):
        return 0.0
    return max(0.0, min(1.0, _normal_cdf(z_score)))


def _expected_max_sharpe(n_trials: int, v_single: float) -> float:
    if n_trials <= 1:
        return 0.0
    euler_gamma = 0.5772156649015329
    first = _normal_ppf(1.0 - 1.0 / n_trials)
    second = _normal_ppf(1.0 - 1.0 / (n_trials * math.e))
    e_max = math.sqrt(v_single) * ((1.0 - euler_gamma) * first + euler_gamma * second)
    return e_max if math.isfinite(e_max) else 0.0


def _normal_cdf(value: float) -> float:
    try:
        from scipy.stats import norm

        return float(norm.cdf(value))
    except Exception:
        return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _normal_ppf(probability: float) -> float:
    probability = min(max(probability, 1e-12), 1.0 - 1e-12)
    try:
        from scipy.stats import norm

        return float(norm.ppf(probability))
    except Exception:
        return float(NormalDist().inv_cdf(probability))
