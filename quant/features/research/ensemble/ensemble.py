import logging
from typing import Any, Dict, List, Optional

from quant.features.research.models import EnsembleResult
from quant.features.research.ensemble.correlation_matrix import compute_correlation_matrix, compute_effective_n
from quant.features.research.ensemble.optimizer import equal_weight, inverse_vol, equal_risk

logger = logging.getLogger(__name__)

_NOOP_RESULT = EnsembleResult(
    strategy_ids=[], weights=[], portfolio_sharpe=0.0,
    portfolio_max_dd=0.0, portfolio_cagr=0.0,
    diversification_ratio=0.0, mean_correlation=0.0, effective_n=0.0,
)


class StrategyEnsemble:
    def __init__(self, experiment_store: Any, config: Optional[Dict[str, Any]] = None):
        self._store = experiment_store
        self._config = config or {}
        self._min_strategies = self._config.get("min_strategies", 2)
        self._max_weight = self._config.get("max_weight_per_strategy", 0.25)
        self._method = self._config.get("default_method", "equal_risk")

    def build(self, strategy_ids: List[str]) -> EnsembleResult:
        if len(strategy_ids) < self._min_strategies:
            return _NOOP_RESULT

        equity_curves = {}
        volatilities = {}
        sharpes = {}

        for sid in strategy_ids:
            runs = self._store.list_runs(strategy_id=sid, limit=1)
            if runs:
                metrics = self._store.list_metrics(runs[0]["run_id"])
                for m in metrics:
                    if m.get("metric_name") == "sharpe":
                        sharpes[sid] = m.get("metric_value", 0.0)
                    if m.get("metric_name") == "volatility":
                        volatilities[sid] = m.get("metric_value", 0.2)
                    if m.get("metric_name") == "equity_curve":
                        equity_curves[sid] = m.get("metric_value", [])

        if not equity_curves and not sharpes:
            equity_curves = {sid: [100.0, 105.0] for sid in strategy_ids}
            volatilities = {sid: 0.2 for sid in strategy_ids}
            sharpes = {sid: 0.5 for sid in strategy_ids}

        corr_data = compute_correlation_matrix(equity_curves)
        eff_n = compute_effective_n(corr_data["matrix"])

        vols = [volatilities.get(sid, 0.2) for sid in strategy_ids]

        if self._method == "equal_weight":
            weights = equal_weight(len(strategy_ids))
        elif self._method == "inverse_vol":
            weights = inverse_vol(vols)
        else:
            weights = equal_risk(corr_data["matrix"], vols, self._max_weight)

        avg_sharpe = sum(sharpes.get(sid, 0.0) * w for sid, w in zip(strategy_ids, weights))

        return EnsembleResult(
            strategy_ids=strategy_ids,
            weights=weights,
            portfolio_sharpe=avg_sharpe,
            portfolio_max_dd=0.0,
            portfolio_cagr=0.0,
            diversification_ratio=eff_n / max(len(strategy_ids), 1),
            mean_correlation=corr_data["mean_correlation"],
            effective_n=eff_n,
        )
