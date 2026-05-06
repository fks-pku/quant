from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from quant.domain.ports import ExperimentStore, ResearchArtifactStore
from quant.features.research.ensemble.correlation_matrix import CorrelationMatrixBuilder
from quant.features.research.ensemble.optimizer import EnsembleOptimizer


class ResearchEnsembleBuilder:
    def __init__(
        self,
        experiment_store: ExperimentStore,
        artifact_store: ResearchArtifactStore,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.experiment_store = experiment_store
        self.artifact_store = artifact_store
        self.config = config or {}
        self.matrix_builder = CorrelationMatrixBuilder()
        self.optimizer = EnsembleOptimizer(max_weight=float(self.config.get("max_weight", 1.0)))

    def build(self, strategy_ids: Optional[Iterable[str]] = None, run_id: str = "") -> Dict[str, Any]:
        ids = list(strategy_ids or self._discover_strategy_ids())
        if len(ids) < 2:
            return self._no_op(ids, "At least two strategies are required")

        returns_by_strategy = {}
        metrics_by_strategy = {}
        for strategy_id in ids:
            loaded = self._load_strategy_returns(strategy_id)
            if loaded["returns"]:
                returns_by_strategy[strategy_id] = loaded["returns"]
                metrics_by_strategy[strategy_id] = loaded["metrics"]

        if len(returns_by_strategy) < 2:
            return self._no_op(list(returns_by_strategy.keys()), "At least two strategies with return artifacts are required")

        returns = self.matrix_builder.aligned_frame(returns_by_strategy)
        if returns.shape[1] < 2 or returns.empty:
            return self._no_op(list(returns_by_strategy.keys()), "Strategy returns do not overlap")

        correlation = self.matrix_builder.build(returns_by_strategy)
        weights = {
            "equal_weight": self.optimizer.equal_weight(list(returns.columns)),
            "inverse_vol": self.optimizer.inverse_vol(returns),
            "equal_risk": self.optimizer.equal_risk(returns),
        }
        equal_risk_weights = weights["equal_risk"]
        portfolio = self._portfolio_metrics(returns, equal_risk_weights, metrics_by_strategy)
        result = {
            "no_op": False,
            "reason": "",
            "strategy_ids": list(returns.columns),
            "correlation_matrix": correlation,
            "effective_n": self.optimizer.effective_n(equal_risk_weights),
            "weights": weights,
            "portfolio": portfolio,
            "metrics": metrics_by_strategy,
            "methods": ["equal_weight", "inverse_vol", "equal_risk"],
        }
        if self.config.get("max_sharpe_enabled") and len(returns) >= int(self.config.get("min_optimizer_samples", 252)):
            result["methods"].append("max_sharpe")
        if run_id:
            self.artifact_store.save_json(run_id, "ensemble", result)
        return result

    def _discover_strategy_ids(self) -> List[str]:
        ids = []
        for run in self.experiment_store.list_runs(limit=int(self.config.get("run_limit", 100))):
            strategy_id = str(run.get("strategy_id", ""))
            if not strategy_id or strategy_id == "research_pipeline" or strategy_id in ids:
                continue
            ids.append(strategy_id)
        return ids

    def _load_strategy_returns(self, strategy_id: str) -> Dict[str, Any]:
        runs = self.experiment_store.list_runs(strategy_id=strategy_id, limit=int(self.config.get("runs_per_strategy", 5)))
        for run in runs:
            run_id = str(run.get("run_id", ""))
            returns = self._load_run_returns(run_id)
            if returns:
                return {"returns": returns, "metrics": self._metrics_for_run(run_id)}
        return {"returns": [], "metrics": {}}

    def _load_run_returns(self, run_id: str) -> List[Dict[str, Any]]:
        for artifact in self.experiment_store.get_artifacts(run_id):
            name = str(artifact.get("name", "")).lower()
            artifact_type = str(artifact.get("artifact_type", "")).lower()
            if not any(token in name for token in ("return", "equity", "curve")) and artifact_type not in {"returns", "equity_curve"}:
                continue
            data = self.artifact_store.load_artifact(str(artifact.get("artifact_id") or artifact.get("path")))
            returns = self._extract_returns(data)
            if returns:
                return returns
        return []

    def _extract_returns(self, data: Any) -> List[Dict[str, Any]]:
        if isinstance(data, dict):
            for key in ("returns", "daily_returns", "equity_curve", "curve"):
                if key in data:
                    return self._extract_returns(data[key])
            return []
        if not isinstance(data, list) or not data:
            return []
        if all(isinstance(item, (int, float)) for item in data):
            return [{"date": str(index), "return": float(value)} for index, value in enumerate(data)]
        if not all(isinstance(item, dict) for item in data):
            return []
        direct_returns = self._direct_returns(data)
        if direct_returns:
            return direct_returns
        return self._equity_returns(data)

    def _direct_returns(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        output = []
        for index, row in enumerate(rows):
            value = self._first_number(row, ("return", "returns", "daily_return", "pct_return"))
            if value is None:
                return []
            output.append({"date": str(row.get("date") or row.get("timestamp") or index), "return": value})
        return output

    def _equity_returns(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        values = []
        dates = []
        for index, row in enumerate(rows):
            value = self._first_number(row, ("equity", "portfolio_value", "value", "nav"))
            if value is None:
                return []
            values.append(value)
            dates.append(str(row.get("date") or row.get("timestamp") or index))
        output = []
        for index in range(1, len(values)):
            previous = values[index - 1]
            if previous == 0.0:
                continue
            output.append({"date": dates[index], "return": (values[index] / previous) - 1.0})
        return output

    def _metrics_for_run(self, run_id: str) -> Dict[str, float]:
        metrics = {}
        for metric in self.experiment_store.list_metrics(run_id):
            name = str(metric.get("metric_name", ""))
            if not name:
                continue
            metrics[name] = float(metric.get("metric_value", 0.0))
        return metrics

    def _portfolio_metrics(
        self,
        returns: pd.DataFrame,
        weights: Dict[str, float],
        metrics_by_strategy: Dict[str, Dict[str, float]],
    ) -> Dict[str, float]:
        vector = np.array([weights.get(strategy_id, 0.0) for strategy_id in returns.columns], dtype="float64")
        portfolio_returns = returns.to_numpy(dtype="float64").dot(vector)
        if portfolio_returns.size == 0:
            return {"sharpe": 0.0, "max_drawdown": 0.0, "cagr": 0.0, "diversification_ratio": 0.0}
        mean = float(np.mean(portfolio_returns))
        vol = float(np.std(portfolio_returns, ddof=1)) if portfolio_returns.size > 1 else 0.0
        sharpe = (mean / vol * np.sqrt(252.0)) if vol > 0.0 else 0.0
        equity = pd.Series((1.0 + portfolio_returns).cumprod())
        drawdown = equity / equity.cummax() - 1.0
        cagr = float(equity.iloc[-1] ** (252.0 / max(1, len(equity))) - 1.0)
        weighted_vol = sum(weights.get(strategy_id, 0.0) * float(returns[strategy_id].std()) for strategy_id in returns.columns)
        portfolio_vol = float(np.std(portfolio_returns, ddof=1)) if portfolio_returns.size > 1 else 0.0
        diversification_ratio = weighted_vol / portfolio_vol if portfolio_vol > 0.0 else 0.0
        return {
            "sharpe": float(sharpe),
            "max_drawdown": float(drawdown.min()),
            "cagr": cagr,
            "diversification_ratio": float(diversification_ratio),
        }

    def _no_op(self, strategy_ids: List[str], reason: str) -> Dict[str, Any]:
        return {
            "no_op": True,
            "reason": reason,
            "strategy_ids": strategy_ids,
            "correlation_matrix": {"strategy_ids": strategy_ids, "matrix": []},
            "effective_n": 0.0,
            "weights": {},
            "portfolio": {},
            "metrics": {},
            "methods": [],
        }

    def _first_number(self, row: Dict[str, Any], keys: Iterable[str]) -> Optional[float]:
        for key in keys:
            if key in row and row[key] is not None:
                return float(row[key])
        return None
