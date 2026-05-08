import logging
from typing import Any, Dict, List

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


def equal_risk(corr_matrix: List[List[float]], volatilities: List[float], max_weight: float = 0.25) -> List[float]:
    n = len(volatilities)
    if n == 0:
        return []
    if n == 1:
        return [1.0]

    inv_vol = [1.0 / max(v, 1e-10) for v in volatilities]
    raw = np.array(inv_vol)
    weights = raw / raw.sum()

    for _ in range(100):
        over = weights > max_weight + 1e-12
        if not np.any(over):
            break
        weights = np.minimum(weights, max_weight)
        weights = weights / weights.sum()

    return weights.tolist()
