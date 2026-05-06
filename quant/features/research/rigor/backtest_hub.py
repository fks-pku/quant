from statistics import mean
from typing import Any, Callable, Dict, List, Mapping, Optional

import pandas as pd

from quant.domain.ports import ExperimentStore
from quant.features.research.models import PurgedWalkForwardResult
from quant.features.research.rigor.cost_model import CostModel
from quant.features.research.rigor.purged_cv import generate_purged_walk_forward_splits
from quant.features.research.rigor.regime_detector import RegimeDetector


BacktestRunner = Callable[[str, Dict[str, Any]], Dict[str, Any]]


class RigorHub:
    def __init__(
        self,
        backtest_runner: BacktestRunner,
        config: Optional[Mapping[str, Any]] = None,
        experiment_store: Optional[ExperimentStore] = None,
    ):
        self.backtest_runner = backtest_runner
        self.config = dict(config or {})
        self.experiment_store = experiment_store
        self.cost_model = CostModel(self.config.get("cost_model", {}))
        self.regime_detector = RegimeDetector()

    def evaluate(
        self,
        strategy_id: str,
        symbols: List[str],
        start: str,
        end: str,
        run_id: Optional[str] = None,
    ) -> PurgedWalkForwardResult:
        dates = pd.date_range(start, end, freq="D")
        split_config = {
            "train_window_days": 252,
            "test_window_days": 63,
            "step_days": 63,
            "purge_days": 5,
            "embargo_days": 21,
            "min_train_observations": 126,
            **dict(self.config.get("purged_walkforward", {})),
        }
        splits = generate_purged_walk_forward_splits(dates, **split_config)
        if not splits:
            return PurgedWalkForwardResult(
                splits=(),
                aggregate_oos_sharpe=0.0,
                worst_oos_sharpe=0.0,
                deflated_sharpe_ratio=None,
                sharpe_degradation=0.0,
                pct_profitable_splits=0.0,
                is_viable=False,
            )

        evaluated_splits = []
        regimes = self.regime_detector.label_splits(splits)
        for index, split in enumerate(splits, start=1):
            regime = regimes[index - 1] if index - 1 < len(regimes) else None
            response = self.backtest_runner(
                strategy_id,
                {
                    "start": split["test_start"],
                    "end": split["test_end"],
                    "train_start": split["train_start"],
                    "train_end": split["train_end"],
                    "symbols": list(symbols),
                    "initial_cash": float(self.config.get("initial_cash", 100000)),
                    "cost_config": dict(self.config.get("cost_model", {})),
                    "run_label": f"walkforward_{index}",
                },
            ) or {}
            metrics = dict(response.get("metrics", {}))
            test_sharpe = self._metric(metrics, "sharpe", "sharpe_ratio")
            train_sharpe = self._metric(metrics, "train_sharpe", "in_sample_sharpe", default=test_sharpe)
            capacity_metrics = self._capacity_metrics(response.get("trades", []))
            split_result = {
                **split,
                "window_type": "oos",
                "window_label": f"walkforward_{index}",
                "train_sharpe": train_sharpe,
                "test_sharpe": test_sharpe,
                **capacity_metrics,
                "regime": regime.regime if regime is not None else "unknown",
                "regime_confidence": regime.confidence if regime is not None else 0.0,
                "errors": list(response.get("errors", [])),
            }
            evaluated_splits.append(split_result)
            self._record_metrics(run_id, strategy_id, split_result)

        test_sharpes = [float(split["test_sharpe"]) for split in evaluated_splits]
        train_sharpes = [float(split["train_sharpe"]) for split in evaluated_splits]
        aggregate = mean(test_sharpes) if test_sharpes else 0.0
        worst = min(test_sharpes) if test_sharpes else 0.0
        pct_profitable = sum(1 for value in test_sharpes if value > 0) / len(test_sharpes) if test_sharpes else 0.0
        train_mean = mean(train_sharpes) if train_sharpes else aggregate
        thresholds = dict(self.config.get("thresholds", {}))
        min_worst = float(thresholds.get("min_worst_oos_sharpe", 0.3))
        min_profitable = float(thresholds.get("min_profitable_splits_pct", 0.5))
        capacity_ok = all(bool(split.get("capacity_ok", True)) for split in evaluated_splits)

        return PurgedWalkForwardResult(
            splits=evaluated_splits,
            aggregate_oos_sharpe=aggregate,
            worst_oos_sharpe=worst,
            deflated_sharpe_ratio=None,
            sharpe_degradation=train_mean - aggregate,
            pct_profitable_splits=pct_profitable,
            is_viable=worst >= min_worst and pct_profitable >= min_profitable and capacity_ok,
        )

    @staticmethod
    def _metric(metrics: Mapping[str, Any], *names: str, default: float = 0.0) -> float:
        for name in names:
            if name in metrics:
                try:
                    return float(metrics[name])
                except (TypeError, ValueError):
                    return default
        return default

    def _capacity_metrics(self, trades: Any) -> Dict[str, Any]:
        if not trades:
            return {
                "capacity_ok": True,
                "capacity_checked": False,
                "capacity_adv_pct": 0.0,
                "cost_bps": 0.0,
            }
        capacity_ok = True
        capacity_checked = False
        capacity_adv_pct = 0.0
        cost_bps = 0.0
        for trade in trades:
            if not isinstance(trade, Mapping):
                continue
            if not self._has_adv(trade):
                continue
            capacity_checked = True
            estimate = self.cost_model.estimate_trade(
                trade_value=self._trade_value(trade),
                average_daily_volume=self._trade_adv(trade),
                price=float(trade.get("price", trade.get("fill_price", 0.0)) or 0.0),
                volatility=float(trade.get("volatility", 0.0) or 0.0),
            )
            capacity_adv_pct = max(capacity_adv_pct, estimate.capacity_adv_pct)
            cost_bps = max(cost_bps, estimate.total_bps)
            if not estimate.capacity_ok:
                capacity_ok = False
        return {
            "capacity_ok": capacity_ok,
            "capacity_checked": capacity_checked,
            "capacity_adv_pct": capacity_adv_pct,
            "cost_bps": cost_bps,
        }

    @staticmethod
    def _trade_value(trade: Mapping[str, Any]) -> float:
        if "trade_value" in trade:
            return float(trade.get("trade_value") or 0.0)
        if "value" in trade:
            return float(trade.get("value") or 0.0)
        quantity = abs(float(trade.get("quantity", trade.get("size", 0.0)) or 0.0))
        price = float(trade.get("price", trade.get("fill_price", 0.0)) or 0.0)
        return quantity * price

    @staticmethod
    def _trade_adv(trade: Mapping[str, Any]) -> float:
        for key in ("average_daily_volume", "adv", "dollar_adv", "average_daily_value"):
            if key in trade:
                return float(trade.get(key) or 0.0)
        return 0.0

    @staticmethod
    def _has_adv(trade: Mapping[str, Any]) -> bool:
        return any(key in trade for key in ("average_daily_volume", "adv", "dollar_adv", "average_daily_value"))

    def _record_metrics(self, run_id: Optional[str], strategy_id: str, split: Mapping[str, Any]) -> None:
        if self.experiment_store is None or not run_id:
            return
        window_label = str(split.get("window_label", ""))
        metrics = [
            {
                "strategy_id": strategy_id,
                "metric_name": "test_sharpe",
                "metric_value": float(split.get("test_sharpe", 0.0)),
                "window_type": "oos",
                "window_label": window_label,
            },
            {
                "strategy_id": strategy_id,
                "metric_name": "train_sharpe",
                "metric_value": float(split.get("train_sharpe", 0.0)),
                "window_type": "train",
                "window_label": window_label,
            },
        ]
        if split.get("capacity_checked"):
            metrics.extend(
                [
                    {
                        "strategy_id": strategy_id,
                        "metric_name": "capacity_ok",
                        "metric_value": 1.0 if split.get("capacity_ok") else 0.0,
                        "window_type": "capacity",
                        "window_label": window_label,
                    },
                    {
                        "strategy_id": strategy_id,
                        "metric_name": "capacity_adv_pct",
                        "metric_value": float(split.get("capacity_adv_pct", 0.0)),
                        "window_type": "capacity",
                        "window_label": window_label,
                    },
                    {
                        "strategy_id": strategy_id,
                        "metric_name": "cost_bps",
                        "metric_value": float(split.get("cost_bps", 0.0)),
                        "window_type": "capacity",
                        "window_label": window_label,
                    },
                ]
            )
        self.experiment_store.record_metrics(run_id, metrics)


def serialize_backtest_trades(trades: Any, bars: Any) -> List[Dict[str, Any]]:
    if not trades:
        return []
    bars_df = pd.DataFrame(bars).copy()
    adv_by_symbol: Dict[str, float] = {}
    vol_by_symbol: Dict[str, float] = {}
    if not bars_df.empty and {"symbol", "close", "volume"}.issubset(bars_df.columns):
        bars_df["close"] = pd.to_numeric(bars_df["close"], errors="coerce")
        bars_df["volume"] = pd.to_numeric(bars_df["volume"], errors="coerce")
        bars_df["dollar_volume"] = bars_df["close"] * bars_df["volume"]
        adv_by_symbol = bars_df.groupby("symbol")["dollar_volume"].mean().fillna(0.0).to_dict()
        sort_columns = ["symbol", _date_column(bars_df)]
        returns = bars_df.sort_values(sort_columns).groupby("symbol")["close"].pct_change()
        bars_df["return"] = returns
        vol_by_symbol = bars_df.groupby("symbol")["return"].std().fillna(0.0).to_dict()

    rows: List[Dict[str, Any]] = []
    for trade in trades:
        symbol = str(_get_attr(trade, "symbol", ""))
        quantity = abs(float(_get_attr(trade, "quantity", 0.0) or 0.0))
        price = float(_get_attr(trade, "fill_price", 0.0) or _get_attr(trade, "exit_price", 0.0) or _get_attr(trade, "price", 0.0) or 0.0)
        rows.append(
            {
                "symbol": symbol,
                "side": str(_get_attr(trade, "side", "")),
                "quantity": quantity,
                "price": price,
                "trade_value": quantity * price,
                "average_daily_volume": float(adv_by_symbol.get(symbol, 0.0)),
                "volatility": float(vol_by_symbol.get(symbol, 0.0)),
            }
        )
    return rows


def _get_attr(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _date_column(bars_df: pd.DataFrame) -> str:
    for name in ("date", "datetime", "timestamp", "time"):
        if name in bars_df.columns:
            return name
    return str(bars_df.columns[0])
