from typing import Dict, Iterable, List

import numpy as np
import pandas as pd


class EnsembleOptimizer:
    def __init__(self, max_weight: float = 1.0):
        self.max_weight = float(max_weight or 1.0)

    def equal_weight(self, strategy_ids: Iterable[str]) -> Dict[str, float]:
        ids = list(strategy_ids)
        if not ids:
            return {}
        raw = np.repeat(1.0 / len(ids), len(ids))
        return self._weights(ids, self._apply_cap(raw))

    def inverse_vol(self, returns: pd.DataFrame) -> Dict[str, float]:
        if returns.empty:
            return {}
        vol = returns.std(axis=0).replace(0.0, np.nan)
        inv = (1.0 / vol).replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy(dtype="float64")
        if float(inv.sum()) <= 0.0:
            return self.equal_weight(list(returns.columns))
        return self._weights(list(returns.columns), self._apply_cap(inv / inv.sum()))

    def equal_risk(self, returns: pd.DataFrame) -> Dict[str, float]:
        return self.inverse_vol(returns)

    def effective_n(self, weights: Dict[str, float]) -> float:
        values = np.array(list(weights.values()), dtype="float64")
        denom = float(np.square(values).sum())
        if denom <= 0.0:
            return 0.0
        return float(1.0 / denom)

    def _apply_cap(self, weights: np.ndarray) -> np.ndarray:
        if weights.size == 0:
            return weights
        cap = max(self.max_weight, 1.0 / weights.size)
        capped = np.minimum(weights.astype("float64"), cap)
        for _ in range(weights.size * 2):
            remainder = 1.0 - float(capped.sum())
            if abs(remainder) <= 1e-12:
                break
            room = cap - capped
            open_mask = room > 1e-12
            if not bool(open_mask.any()):
                break
            addition = np.minimum(room[open_mask], remainder / int(open_mask.sum()))
            capped[open_mask] += addition
        total = float(capped.sum())
        if total <= 0.0:
            return np.repeat(1.0 / weights.size, weights.size)
        return capped / total

    def _weights(self, strategy_ids: List[str], values: np.ndarray) -> Dict[str, float]:
        return {strategy_id: float(value) for strategy_id, value in zip(strategy_ids, values)}
