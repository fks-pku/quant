from typing import List

import numpy as np


def benjamini_hochberg(p_values: List[float], alpha: float = 0.05) -> List[bool]:
    n = len(p_values)
    if n == 0:
        return []
    sorted_indices = np.argsort(p_values)
    sorted_p = np.array(p_values)[sorted_indices]
    thresholds = (np.arange(1, n + 1) / n) * alpha
    significant = sorted_p <= thresholds
    for i in range(n - 2, -1, -1):
        significant[i] = significant[i] or significant[i + 1]
    result = [False] * n
    for idx, orig_idx in enumerate(sorted_indices):
        result[orig_idx] = significant[idx]
    return result
