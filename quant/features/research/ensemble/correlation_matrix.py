import logging
from typing import Any, Dict, List

import numpy as np

logger = logging.getLogger(__name__)


def compute_correlation_matrix(equity_curves: Dict[str, List[float]]) -> Dict[str, Any]:
    ids = list(equity_curves.keys())
    n = len(ids)
    if n < 2:
        return {"ids": ids, "matrix": [[1.0]] if n == 1 else [], "mean_correlation": 0.0}

    returns = {}
    for sid in ids:
        curve = equity_curves[sid]
        if len(curve) < 2:
            returns[sid] = []
        else:
            returns[sid] = [
                (curve[i] - curve[i - 1]) / max(abs(curve[i - 1]), 1e-10)
                for i in range(1, len(curve))
            ]

    min_len = min(len(r) for r in returns.values()) if returns else 0
    if min_len < 2:
        matrix = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
        return {"ids": ids, "matrix": matrix, "mean_correlation": 0.0}

    aligned = np.array([returns[sid][:min_len] for sid in ids])
    corr = np.corrcoef(aligned)
    matrix = corr.tolist()

    off_diag = [corr[i][j] for i in range(n) for j in range(i + 1, n)]
    mean_corr = float(np.mean(off_diag)) if off_diag else 0.0

    return {"ids": ids, "matrix": matrix, "mean_correlation": mean_corr}


def compute_effective_n(corr_matrix: List[List[float]]) -> float:
    n = len(corr_matrix)
    if n < 2:
        return float(n)
    off_diag = [corr_matrix[i][j] for i in range(n) for j in range(i + 1, n)]
    mean_corr = np.mean(off_diag) if off_diag else 0.0
    if mean_corr >= 1.0:
        return 1.0
    return n / (1 + (n - 1) * mean_corr)
