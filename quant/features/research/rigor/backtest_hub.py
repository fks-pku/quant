import logging
from typing import Any, Callable, Dict, List, Optional

from quant.features.research.models import PurgedWalkForwardResult, CostEstimate
from quant.features.research.rigor.purged_cv import generate_purged_walkforward_splits
from quant.features.research.rigor.cost_model import estimate_costs

logger = logging.getLogger(__name__)


class RigorHub:
    def __init__(
        self,
        backtest_runner: Callable,
        config: Optional[Dict[str, Any]] = None,
        experiment_store: Any = None,
        artifact_store: Any = None,
    ):
        self._runner = backtest_runner
        self._config = config or {}
        self._experiment_store = experiment_store
        self._artifact_store = artifact_store

        wf = self._config.get("purged_walkforward", {})
        self._train_window = wf.get("train_window_days", 252)
        self._test_window = wf.get("test_window_days", 63)
        self._step_days = wf.get("step_days", 63)
        self._purge_days = wf.get("purge_days", 5)
        self._embargo_days = wf.get("embargo_days", 21)
        self._min_train = wf.get("min_train_observations", 126)

        thresholds = self._config.get("thresholds", {})
        self._min_worst_oos_sharpe = thresholds.get("min_worst_oos_sharpe", 0.3)
        self._min_profitable_pct = thresholds.get("min_profitable_splits_pct", 0.5)

    def run_walkforward(
        self,
        strategy_id: str,
        symbols: List[str],
        start: str,
        end: str,
        initial_cash: float = 100000,
    ) -> PurgedWalkForwardResult:
        n_obs = self._estimate_observations(start, end)
        splits = generate_purged_walkforward_splits(
            n_observations=n_obs,
            train_window=self._train_window,
            test_window=self._test_window,
            step_days=self._step_days,
            purge_days=self._purge_days,
            embargo_days=self._embargo_days,
            min_train_observations=self._min_train,
        )

        if not splits:
            return PurgedWalkForwardResult(
                splits=[], aggregate_oos_sharpe=0.0, worst_oos_sharpe=0.0,
                deflated_sharpe_ratio=None, sharpe_degradation=0.0,
                pct_profitable_splits=0.0, is_viable=False,
            )

        split_results = []
        for split in splits:
            request = {
                "start": split["train_start"],
                "end": split["test_end"],
                "symbols": symbols,
                "initial_cash": initial_cash,
                "cost_config": self._config.get("cost_model", {}),
                "run_label": f"{strategy_id}_split_{split['train_start']}_{split['test_end']}",
            }
            try:
                response = self._runner(strategy_id, request)
                test_sharpe = response.get("metrics", {}).get("sharpe", 0.0) if isinstance(response, dict) else 0.0
                split["test_sharpe"] = test_sharpe
                split_results.append(split)
            except Exception as e:
                logger.warning(f"Walk-forward split failed: {e}")
                split["test_sharpe"] = 0.0
                split_results.append(split)

        test_sharpes = [s["test_sharpe"] for s in split_results]
        worst_oos = min(test_sharpes) if test_sharpes else 0.0
        aggregate = sum(test_sharpes) / len(test_sharpes) if test_sharpes else 0.0
        profitable = sum(1 for s in test_sharpes if s > 0) / len(test_sharpes) if test_sharpes else 0.0
        degradation = aggregate - worst_oos if aggregate > 0 else 0.0

        is_viable = (
            worst_oos >= self._min_worst_oos_sharpe
            and profitable >= self._min_profitable_pct
        )

        return PurgedWalkForwardResult(
            splits=split_results,
            aggregate_oos_sharpe=aggregate,
            worst_oos_sharpe=worst_oos,
            deflated_sharpe_ratio=None,
            sharpe_degradation=degradation,
            pct_profitable_splits=profitable,
            is_viable=is_viable,
        )

    @staticmethod
    def _estimate_observations(start: str, end: str) -> int:
        try:
            from datetime import datetime
            s = datetime.strptime(str(start), "%Y-%m-%d")
            e = datetime.strptime(str(end), "%Y-%m-%d")
            return int((e - s).days * 5 / 7)
        except Exception:
            return 0
