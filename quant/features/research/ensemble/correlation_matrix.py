from typing import Any, Dict, List

import pandas as pd


class CorrelationMatrixBuilder:
    def build(self, returns_by_strategy: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        frame = self._to_frame(returns_by_strategy)
        if frame.shape[1] < 2 or frame.empty:
            return {"strategy_ids": list(returns_by_strategy.keys()), "matrix": []}

        corr = frame.corr().fillna(0.0)
        for strategy_id in corr.columns:
            corr.loc[strategy_id, strategy_id] = 1.0
        strategy_ids = list(corr.columns)
        return {
            "strategy_ids": strategy_ids,
            "matrix": [[float(corr.loc[row, col]) for col in strategy_ids] for row in strategy_ids],
        }

    def aligned_frame(self, returns_by_strategy: Dict[str, List[Dict[str, Any]]]) -> pd.DataFrame:
        return self._to_frame(returns_by_strategy)

    def _to_frame(self, returns_by_strategy: Dict[str, List[Dict[str, Any]]]) -> pd.DataFrame:
        series = {}
        for strategy_id, rows in returns_by_strategy.items():
            values = []
            index = []
            for position, row in enumerate(rows):
                values.append(float(row["return"]))
                index.append(str(row.get("date", position)))
            if values:
                series[strategy_id] = pd.Series(values, index=index, dtype="float64")
        if not series:
            return pd.DataFrame()
        return pd.DataFrame(series).dropna(axis=0, how="any")
