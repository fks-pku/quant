import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from quant.features.research.models import PurgedWalkForwardResult
from quant.features.research.rigor.purged_cv import generate_purged_walkforward_splits
from quant.features.research.rigor.cost_model import estimate_costs
from quant.features.research.rigor.dsr import compute_dsr
from quant.features.research.rigor.regime_detector import label_split_regime, compute_regime_breakdown

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
        self._parallel_workers = self._resolve_parallel_workers(wf.get("parallel_workers", 1))
        self._prefetch_data = bool(wf.get("prefetch_data", False))

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
        benchmark_data: Any = None,
        strategy_archive_dir: str = "",
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
                pct_profitable_splits=0.0, is_viable=False, capacity_ok=False,
            )

        split_results = []
        return_series = []
        for split_result, returns in self._run_split_jobs(
            strategy_id=strategy_id,
            symbols=symbols,
            initial_cash=initial_cash,
            splits=splits,
            calendar=calendar,
            benchmark_data=benchmark_data,
            run_start=str(start),
            run_end=str(end),
            strategy_archive_dir=strategy_archive_dir,
        ):
            split_results.append(split_result)
            if split_result.get("has_trades", True) and returns is not None and not returns.empty:
                return_series.append(returns)

        evaluated_split_results = [s for s in split_results if s.get("has_trades", True)]
        test_sharpes = [s["test_sharpe"] for s in evaluated_split_results]
        worst_oos = min(test_sharpes) if test_sharpes else 0.0
        aggregate = sum(test_sharpes) / len(test_sharpes) if test_sharpes else 0.0
        profitable = sum(1 for s in test_sharpes if s > 0) / len(test_sharpes) if test_sharpes else 0.0
        degradation = aggregate - worst_oos if aggregate > 0 else 0.0
        capacity_ok = self._check_capacity(split_results)
        regime_breakdown = compute_regime_breakdown(evaluated_split_results) if benchmark_data is not None else {}
        bull_only_warning = regime_breakdown.get("bear", {}).get("sharpe", 0.0) < -0.5

        is_viable = (
            bool(test_sharpes)
            and worst_oos >= self._min_worst_oos_sharpe
            and profitable >= self._min_profitable_pct
            and capacity_ok
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
            capacity_ok=capacity_ok,
            regime_breakdown=regime_breakdown,
            bull_only_warning=bull_only_warning,
            evaluated_splits=len(evaluated_split_results),
            no_trade_splits=sum(1 for split in split_results if split.get("has_trades") is False),
            total_splits=len(split_results),
        )

    def _run_split_jobs(
        self,
        strategy_id: str,
        symbols: List[str],
        initial_cash: float,
        splits: List[Dict[str, Any]],
        calendar: pd.DatetimeIndex,
        benchmark_data: Any,
        run_start: str,
        run_end: str,
        strategy_archive_dir: str = "",
    ) -> List[Tuple[Dict[str, Any], Optional[pd.Series]]]:
        jobs = [
            (
                strategy_id,
                symbols,
                initial_cash,
                dict(split),
                self._attach_split_dates(split, calendar),
                benchmark_data,
                run_start,
                run_end,
                self._prefetch_data,
                strategy_archive_dir,
            )
            for split in splits
        ]
        workers = min(self._parallel_workers, len(jobs))
        if workers <= 1:
            return [self._run_split_job(job) for job in jobs]
        logger.info("Running purged walk-forward with %d parallel workers across %d splits", workers, len(jobs))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            return list(executor.map(self._run_split_job, jobs))

    def _run_split_job(
        self,
        job: Tuple[str, List[str], float, Dict[str, Any], Dict[str, str], Any, str, str, bool, str],
    ) -> Tuple[Dict[str, Any], Optional[pd.Series]]:
        strategy_id, symbols, initial_cash, split, dated_split, benchmark_data, run_start, run_end, prefetch_data, strategy_archive_dir = job
        request = {
            "start": dated_split["test_start_date"],
            "end": dated_split["test_end_date"],
            "walkforward_start_date": run_start,
            "walkforward_end_date": run_end,
            "walkforward_prefetch_data": prefetch_data,
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
            "strategy_archive_dir": strategy_archive_dir,
            "cost_config": self._config.get("cost_model", {}),
            "run_label": f"{strategy_id}_split_{dated_split['train_start_date']}_{dated_split['test_end_date']}",
        }
        try:
            response = self._runner(strategy_id, request)
            returns = None
            if isinstance(response, dict) and "returns" in response:
                response = dict(response)
                returns = self._extract_oos_returns(
                    response["returns"],
                    dated_split["test_start_date"],
                    dated_split["test_end_date"],
                )
                response["returns"] = returns
            split.update(dated_split)
            split["response"] = response
            split["test_sharpe"] = response.get("metrics", {}).get("sharpe", 0.0) if isinstance(response, dict) else 0.0
            trade_count = self._response_trade_count(response)
            split["trade_count"] = trade_count
            split["has_trades"] = True if trade_count is None else trade_count > 0
            self._attach_regime_label(split, benchmark_data)
            return split, returns
        except TypeError:
            raise
        except Exception as e:
            logger.warning(f"Walk-forward split failed: {e}")
            split.update(dated_split)
            split["test_sharpe"] = 0.0
            split["trade_count"] = None
            split["has_trades"] = True
            self._attach_regime_label(split, benchmark_data)
            return split, None

    @staticmethod
    def _response_trade_count(response: Any) -> Optional[int]:
        if not isinstance(response, dict):
            return None
        trades = response.get("trades")
        if isinstance(trades, list):
            return len(trades)
        metrics = response.get("metrics")
        if isinstance(metrics, dict):
            for key in ("total_trades", "trade_count", "n_trades"):
                value = metrics.get(key)
                try:
                    return max(0, int(value))
                except (TypeError, ValueError):
                    continue
        return None

    def _n_trials(self) -> int:
        if self._experiment_store is None or not hasattr(self._experiment_store, "list_runs"):
            return 1
        try:
            return max(1, len(self._experiment_store.list_runs(limit=100)))
        except Exception as e:
            logger.warning(f"Experiment run count unavailable: {e}")
            return 1

    @staticmethod
    def _resolve_parallel_workers(value: Any) -> int:
        try:
            workers = int(value)
        except (TypeError, ValueError):
            return 1
        return max(1, workers)

    def _check_capacity(self, split_results: List[Dict[str, Any]]) -> bool:
        saw_trades = False
        for split in split_results:
            response = split.get("response", {})
            trades = response.get("trades", []) if isinstance(response, dict) else []
            for trade in trades:
                saw_trades = True
                cost = estimate_costs(
                    trade_value=float(trade.get("trade_value", 0.0)),
                    avg_daily_volume=float(trade.get("avg_daily_volume", 0.0)),
                    price=float(trade.get("price", 100.0)),
                    volatility=float(trade.get("volatility", 0.2)),
                    config=self._config.get("cost_model", {}),
                )
                if not cost.capacity_ok:
                    logger.info("Capacity gate fail: trades_present=True")
                    return False
        logger.info(f"Capacity gate pass: trades_present={saw_trades}")
        return True

    @staticmethod
    def _attach_regime_label(split: Dict[str, Any], benchmark_data: Any) -> None:
        if benchmark_data is not None:
            split["regime"] = label_split_regime(split, benchmark_data)

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
