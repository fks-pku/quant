from typing import Dict, List


def benjamini_hochberg(p_values: List[float], alpha: float = 0.05) -> List[Dict[str, float | bool]]:
    if not p_values:
        return []

    m = len(p_values)
    indexed = sorted((max(0.0, min(1.0, float(value))), index) for index, value in enumerate(p_values))
    adjusted_sorted = [0.0] * m
    running_min = 1.0

    for rank in range(m, 0, -1):
        p_value, _ = indexed[rank - 1]
        adjusted = min(running_min, p_value * m / rank)
        running_min = adjusted
        adjusted_sorted[rank - 1] = adjusted

    results: List[Dict[str, float | bool]] = [{} for _ in p_values]
    for rank, ((p_value, original_index), adjusted) in enumerate(zip(indexed, adjusted_sorted), start=1):
        results[original_index] = {
            "p_value": p_value,
            "rank": rank,
            "adjusted_p": min(1.0, adjusted),
            "significant": adjusted <= alpha,
        }
    return results
