import logging
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from quant.features.research.models import PurgedWalkForwardResult, CostEstimate
from quant.features.research.rigor.purged_cv import generate_purged_walkforward_splits
from quant.features.research.rigor.cost_model import estimate_costs
from quant.features.research.rigor.dsr import compute_dsr

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
        calendar = pd.bdate_range(start=start, end=end)
        n_obs = len(calendar)
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
        return_series = []
        for split in splits:
            dated_split = self._attach_split_dates(split, calendar)
            request = {
                "start": dated_split["test_start_date"],
                "end": dated_split["test_end_date"],
                "train_start": split["train_start"],
                "train_end": split["train_end"],
                "test_start": split["test_start"],
                "test_end": split["test_end"],
                "train_start_date": dated_split["train_start_date"],
                "train_end_date": dated_split["train_end_date"],
                "test_start_date": dated_split["test_start_date"],
                "test_end_date": dated_split["test_end_date"],
                "symbols": symbols,
                "initial_cash": initial_cash,
                "cost_config": self._config.get("cost_model", {}),
                "run_label": f"{strategy_id}_split_{dated_split['train_start_date']}_{dated_split['test_end_date']}",
            }
            try:
                response = self._runner(strategy_id, request)
                if isinstance(response, dict) and "returns" in response:
                    response = dict(response)
                    returns = self._extract_oos_returns(
                        response["returns"],
                        dated_split["test_start_date"],
                        dated_split["test_end_date"],
                    )
                    response["returns"] = returns
                    if not returns.empty:
                        return_series.append(returns)
                split.update(dated_split)
                split["response"] = response
                test_sharpe = response.get("metrics", {}).get("sharpe", 0.0) if isinstance(response, dict) else 0.0
                split["test_sharpe"] = test_sharpe
                split_results.append(split)
            except TypeError:
                raise
            except Exception as e:
                logger.warning(f"Walk-forward split failed: {e}")
                split.update(dated_split)
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
        dsr = None
        if return_series:
            combined_returns = pd.concat(return_series).sort_index()
            if isinstance(combined_returns.index, pd.DatetimeIndex):
                combined_returns = combined_returns[~combined_returns.index.duplicated(keep="first")]
            dsr = compute_dsr(combined_returns, n_trials=self._n_trials())

        return PurgedWalkForwardResult(
            splits=split_results,
            aggregate_oos_sharpe=aggregate,
            worst_oos_sharpe=worst_oos,
            deflated_sharpe_ratio=dsr,
            sharpe_degradation=degradation,
            pct_profitable_splits=profitable,
            is_viable=is_viable,
        )

    def _n_trials(self) -> int:
        if self._experiment_store is None or not hasattr(self._experiment_store, "list_runs"):
            return 1
        try:
            return max(1, len(self._experiment_store.list_runs(limit=100)))
        except Exception as e:
            logger.warning(f"Experiment run count unavailable: {e}")
            return 1

    @staticmethod
    def _estimate_observations(start: str, end: str) -> int:
        try:
            from datetime import datetime
            s = datetime.strptime(str(start), "%Y-%m-%d")
            e = datetime.strptime(str(end), "%Y-%m-%d")
            return int((e - s).days * 5 / 7)
        except Exception:
            return 0

    @staticmethod
    def _attach_split_dates(split: Dict[str, Any], calendar: pd.DatetimeIndex) -> Dict[str, str]:
        return {
            "train_start_date": calendar[split["train_start"]].strftime("%Y-%m-%d"),
            "train_end_date": calendar[split["train_end"]].strftime("%Y-%m-%d"),
            "test_start_date": calendar[split["test_start"]].strftime("%Y-%m-%d"),
            "test_end_date": calendar[split["test_end"]].strftime("%Y-%m-%d"),
        }

    @staticmethod
    def _extract_oos_returns(values: Any, start: str, end: str) -> pd.Series:
        if isinstance(values, pd.Series):
            returns = values.copy()
        elif isinstance(values, pd.DataFrame):
            if "returns" in values.columns:
                returns = values["returns"].copy()
            elif "return" in values.columns:
                returns = values["return"].copy()
            else:
                returns = values.iloc[:, 0].copy()
        else:
            returns = pd.Series(values)

        returns = pd.Series(returns).dropna()
        if returns.empty:
            return returns.astype(float)

        if RigorHub._is_datetime_like_index(returns.index):
            returns.index = pd.to_datetime(returns.index)
            returns = returns.sort_index()
            returns = returns.loc[(returns.index >= pd.Timestamp(start)) & (returns.index <= pd.Timestamp(end))]
            returns = returns[~returns.index.duplicated(keep="first")]
            return returns.astype(float)

        returns = returns.reset_index(drop=True)
        return returns.astype(float)

    @staticmethod
    def _is_datetime_like_index(index: Any) -> bool:
        if isinstance(index, pd.DatetimeIndex):
            return True
        if isinstance(index, pd.RangeIndex):
            return False
        if getattr(index, "inferred_type", "") in {"integer", "floating"}:
            return False
        parsed = pd.to_datetime(index, errors="coerce")
        return bool(pd.Series(parsed).notna().all())
