"""Walk-forward analysis framework with 6m train / 1m test / monthly step."""

import itertools
import math
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Any, Callable, Optional, Tuple
import pandas as pd
import numpy as np

from quant.domain.ports.event_publisher import EventPublisher

from quant.features.backtest.engine import Backtester
from quant.features.backtest.entities import BacktestResult
from quant.features.backtest.analytics import calculate_sharpe, calculate_max_drawdown
from quant.features.backtest.data_provider import DataFrameProvider

logger = logging.getLogger(__name__)


@dataclass
class WFWindowResult:
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_sharpe: float
    test_sharpe: float
    test_return: float
    test_max_dd: float
    params: Dict[str, Any]
    train_trades: int = 0
    test_trades: int = 0
    param_trials: int = 0


@dataclass
class WFResult:
    windows: List[WFWindowResult]
    aggregate_sharpe: float
    aggregate_max_dd: float
    consistency: float
    best_params: Dict[str, Any]
    sharpe_degradation: float
    avg_train_sharpe: float
    avg_test_sharpe: float
    test_sharpe_std: float
    pct_profitable: float
    is_viable: bool
    param_trials: int = 0
    tested_param_sets: List[Dict[str, Any]] = field(default_factory=list)
    selected_params_by_window: List[Dict[str, Any]] = field(default_factory=list)
    parameter_stability: float = 0.0
    min_train_trades: int = 0
    min_test_trades: int = 0
    multiple_testing_adjusted_alpha: float = 0.05
    oos_p_value: float = 1.0
    oos_p_value_adjusted: float = 1.0
    viability_warnings: List[str] = field(default_factory=list)

    @classmethod
    def empty(cls) -> "WFResult":
        return cls(
            windows=[],
            aggregate_sharpe=0.0,
            aggregate_max_dd=0.0,
            consistency=0.0,
            best_params={},
            sharpe_degradation=0.0,
            avg_train_sharpe=0.0,
            avg_test_sharpe=0.0,
            test_sharpe_std=0.0,
            pct_profitable=0.0,
            is_viable=False,
        )


class WalkForwardEngine:
    """Walk-forward analysis engine with configurable train/test windows."""

    def __init__(
        self,
        train_window_days: int = 126,
        test_window_days: int = 21,
        step_days: int = 21,
        rebalance_freq: str = "monthly",
        min_trades: int = 30,
        lot_sizes: Optional[Dict[str, int]] = None,
        ipo_dates: Optional[Dict[str, date]] = None,
        portfolio_class: Optional[type] = None,
        risk_engine_class: Optional[type] = None,
        sub_portfolio_class: Optional[type] = None,
        event_bus: Optional[EventPublisher] = None,
    ):
        self.train_window_days = train_window_days
        self.test_window_days = test_window_days
        self.step_days = step_days
        self.rebalance_freq = rebalance_freq
        self.min_trades = min_trades
        self.lot_sizes = lot_sizes or {}
        self.ipo_dates = ipo_dates or {}
        if portfolio_class is None or risk_engine_class is None or sub_portfolio_class is None:
            raise ValueError(
                "portfolio_class, risk_engine_class, and sub_portfolio_class are required. "
                "Use quant.features.trading.Portfolio, RiskEngine, and SubPortfolio."
            )
        self.portfolio_class = portfolio_class
        self.risk_engine_class = risk_engine_class
        self.sub_portfolio_class = sub_portfolio_class
        self._event_bus = event_bus

    def run(
        self,
        strategy_factory: Callable[[Dict], Any],
        data: pd.DataFrame,
        param_grid: Dict[str, List[Any]],
        initial_cash: float = 100000,
        config: Optional[Dict[str, Any]] = None
    ) -> WFResult:
        """
        Run walk-forward analysis using trading-day-based windows.
        
        Args:
            strategy_factory: Function that creates strategy with given params
            data: DataFrame with columns [timestamp, symbol, open, high, low, close, volume]
            param_grid: Dict of parameter names to list of values to grid search
            initial_cash: Starting capital
            config: Optional config dict for backtester
            
        Returns:
            WFResult with window results and aggregate statistics
        """
        config = config or {}
        window_results: List[WFWindowResult] = []
        total_param_trials = 0
        tested_param_sets: List[Dict[str, Any]] = []
        seen_param_sets = set()

        if data is None or data.empty:
            return WFResult.empty()
        if 'timestamp' not in data.columns:
            logger.warning("Data missing 'timestamp' column — cannot run walk-forward")
            return WFResult.empty()

        if not pd.api.types.is_datetime64_any_dtype(data['timestamp']):
            data = data.copy()
            data['timestamp'] = pd.to_datetime(data['timestamp'])
        
        data = data.sort_values('timestamp')
        unique_dates = sorted(data['timestamp'].dt.normalize().unique())
        if hasattr(unique_dates[0], 'to_pydatetime'):
            unique_dates = [d.to_pydatetime() for d in unique_dates]
        n_dates = len(unique_dates)
        
        if n_dates < self.train_window_days + self.test_window_days:
            return WFResult.empty()
        
        step_idx = 0
        while True:
            train_start_idx = step_idx
            train_end_idx = train_start_idx + self.train_window_days
            test_start_idx = train_end_idx
            test_end_idx = test_start_idx + self.test_window_days
            
            if test_end_idx > n_dates:
                break
            
            train_start = unique_dates[train_start_idx]
            train_end = unique_dates[train_end_idx]
            test_start = unique_dates[test_start_idx]
            test_end = unique_dates[test_end_idx] if test_end_idx < n_dates else unique_dates[-1] + timedelta(days=1)
            
            train_data = data[(data['timestamp'] >= train_start) & (data['timestamp'] < train_end)]
            test_data = data[(data['timestamp'] >= test_start) & (data['timestamp'] < test_end)]
            
            if len(train_data) < 50 or len(test_data) < 10:
                step_idx += self.step_days
                continue
            
            best_params, best_train_sharpe, window_tested_params, window_param_trials, train_trades = self._find_best_params(
                strategy_factory, train_data, param_grid, initial_cash, config
            )
            total_param_trials += window_param_trials
            for params in window_tested_params:
                key = self._param_key(params)
                if key not in seen_param_sets:
                    seen_param_sets.add(key)
                    tested_param_sets.append(params)
            
            if best_train_sharpe == float('-inf'):
                step_idx += self.step_days
                continue
            
            strategy = strategy_factory(best_params)

            try:
                test_result = self._run_single_backtest(
                    config or {}, strategy, test_data, initial_cash
                )
            except Exception as e:
                logger.warning("WalkForward test backtest failed for params %s: %s", best_params, e)
                step_idx += self.step_days
                continue

            test_max_dd = test_result.max_drawdown_pct
            test_trades = len(test_result.trades)
            
            window_results.append(WFWindowResult(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                train_sharpe=best_train_sharpe,
                test_sharpe=test_result.sharpe_ratio,
                test_return=test_result.total_return,
                test_max_dd=test_max_dd,
                params=best_params,
                train_trades=train_trades,
                test_trades=test_trades,
                param_trials=window_param_trials,
            ))
            
            step_idx += self.step_days
        
        if not window_results:
            return WFResult.empty()
        
        aggregate_sharpe = np.mean([w.test_sharpe for w in window_results])
        aggregate_max_dd = max(w.test_max_dd for w in window_results) if window_results else 0.0
        consistency = len([w for w in window_results if w.test_return > 0]) / len(window_results)
        
        train_sharpes = [w.train_sharpe for w in window_results]
        test_sharpes = [w.test_sharpe for w in window_results]
        
        avg_train = float(np.mean(train_sharpes)) if train_sharpes else 0.0
        avg_test = float(np.mean(test_sharpes)) if test_sharpes else 0.0
        test_std = float(np.std(test_sharpes, ddof=1)) if len(test_sharpes) > 1 else 0.0
        
        if avg_train > 0:
            sharpe_degradation = min(1.0, max(0.0, 1.0 - (avg_test / avg_train)))
        elif avg_test > 0:
            sharpe_degradation = 0.0
        else:
            sharpe_degradation = 1.0
        pct_profitable = float(len([w for w in window_results if w.test_return > 0]) / len(window_results)) if window_results else 0.0

        selected_params_by_window = [w.params for w in window_results]
        parameter_stability = self._calculate_parameter_stability(selected_params_by_window)
        min_train_trades = min((w.train_trades for w in window_results), default=0)
        min_test_trades = min((w.test_trades for w in window_results), default=0)
        adjusted_alpha = 0.05 / max(1, total_param_trials)
        oos_p_value = self._calculate_oos_p_value(test_sharpes)
        adjusted_p_value = min(1.0, oos_p_value * max(1, total_param_trials))

        viability_warnings = self._collect_viability_warnings(
            avg_test=avg_test,
            sharpe_degradation=sharpe_degradation,
            pct_profitable=pct_profitable,
            window_count=len(window_results),
            min_train_trades=min_train_trades,
            min_test_trades=min_test_trades,
            parameter_stability=parameter_stability,
            adjusted_p_value=adjusted_p_value,
        )
        is_viable = len(viability_warnings) == 0

        param_tuples = [self._param_key(w.params) for w in window_results if w.params]
        if param_tuples:
            most_common_tuple, _ = Counter(param_tuples).most_common(1)[0]
            best_params = next(
                w.params for w in window_results
                if w.params and self._param_key(w.params) == most_common_tuple
            ).copy()
        else:
            best_params = {}
        
        return WFResult(
            windows=window_results,
            aggregate_sharpe=float(aggregate_sharpe),
            aggregate_max_dd=float(aggregate_max_dd),
            consistency=float(consistency),
            best_params=best_params,
            sharpe_degradation=float(sharpe_degradation),
            avg_train_sharpe=avg_train,
            avg_test_sharpe=avg_test,
            test_sharpe_std=test_std,
            pct_profitable=pct_profitable,
            is_viable=is_viable,
            param_trials=total_param_trials,
            tested_param_sets=tested_param_sets,
            selected_params_by_window=selected_params_by_window,
            parameter_stability=parameter_stability,
            min_train_trades=min_train_trades,
            min_test_trades=min_test_trades,
            multiple_testing_adjusted_alpha=adjusted_alpha,
            oos_p_value=oos_p_value,
            oos_p_value_adjusted=adjusted_p_value,
            viability_warnings=viability_warnings,
        )

    def _find_best_params(
        self,
        strategy_factory: Callable[[Dict], Any],
        train_data: pd.DataFrame,
        param_grid: Dict[str, List[Any]],
        initial_cash: float,
        config: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], float, List[Dict[str, Any]], int, int]:
        """Find best params using grid search on training data."""
        param_names = list(param_grid.keys())
        param_values = [param_grid[name] for name in param_names]
        tested_params = [
            dict(zip(param_names, values))
            for values in itertools.product(*param_values)
        ]
        best_params: Dict[str, Any] = {}
        best_sharpe = float('-inf')
        best_train_trades = 0
        
        for params in tested_params:
            try:
                strategy = strategy_factory(params)
                result = self._run_single_backtest(config, strategy, train_data, initial_cash)
                trade_count = len(result.trades)

                if trade_count < self.min_trades:
                    continue
                
                if result.sharpe_ratio > best_sharpe:
                    best_sharpe = result.sharpe_ratio
                    best_params = params
                    best_train_trades = trade_count
            except (ValueError, TypeError, RuntimeError) as e:
                logger.warning("WalkForward grid search failed for params %s: %s", params, e)
                continue
        
        return best_params, best_sharpe, tested_params, len(tested_params), best_train_trades

    @staticmethod
    def _param_key(params: Dict[str, Any]) -> Tuple:
        try:
            return tuple(sorted(params.items()))
        except TypeError:
            return tuple(sorted((k, repr(v)) for k, v in params.items()))

    @staticmethod
    def _calculate_parameter_stability(selected_params: List[Dict[str, Any]]) -> float:
        if not selected_params:
            return 0.0
        if all(not params for params in selected_params):
            return 1.0
        keys = [WalkForwardEngine._param_key(params) for params in selected_params]
        _, count = Counter(keys).most_common(1)[0]
        return float(count / len(keys))

    @staticmethod
    def _calculate_oos_p_value(test_sharpes: List[float]) -> float:
        if len(test_sharpes) < 2:
            return 1.0
        avg = float(np.mean(test_sharpes))
        std = float(np.std(test_sharpes, ddof=1))
        if std <= 1e-12:
            return 0.0 if avg > 0 else 1.0
        z_score = avg / (std / math.sqrt(len(test_sharpes)))
        return float(math.erfc(abs(z_score) / math.sqrt(2)))

    def _collect_viability_warnings(
        self,
        avg_test: float,
        sharpe_degradation: float,
        pct_profitable: float,
        window_count: int,
        min_train_trades: int,
        min_test_trades: int,
        parameter_stability: float,
        adjusted_p_value: float,
    ) -> List[str]:
        warnings: List[str] = []
        if avg_test <= 0.5:
            warnings.append(f"avg test Sharpe {avg_test:.3f} <= 0.5")
        if sharpe_degradation >= 0.5:
            warnings.append(f"Sharpe degradation {sharpe_degradation:.3f} >= 0.5")
        if pct_profitable <= 0.5:
            warnings.append(f"profitable window ratio {pct_profitable:.3f} <= 0.5")
        if window_count < 2:
            warnings.append("need at least 2 out-of-sample windows for stability")
        if min_train_trades < self.min_trades:
            warnings.append(f"min train trades {min_train_trades} below required {self.min_trades}")
        if min_test_trades < self.min_trades:
            warnings.append(f"min test trades {min_test_trades} below required {self.min_trades}")
        if parameter_stability < 0.5:
            warnings.append(f"parameter stability {parameter_stability:.3f} below required 0.5")
        if adjusted_p_value >= 0.05:
            warnings.append(f"multiple-testing adjusted p-value {adjusted_p_value:.3f} >= 0.05")
        return warnings

    def _run_single_backtest(
        self,
        config: Dict[str, Any],
        strategy: Any,
        data: pd.DataFrame,
        initial_cash: float,
    ) -> BacktestResult:
        backtester = Backtester(
            config or {},
            event_bus=self._event_bus,
            lot_sizes=self.lot_sizes,
            ipo_dates=self.ipo_dates,
            portfolio_class=self.portfolio_class,
            risk_engine_class=self.risk_engine_class,
            sub_portfolio_class=self.sub_portfolio_class,
        )

        symbols = data['symbol'].unique().tolist()

        result = backtester.run(
            start=data['timestamp'].min(),
            end=data['timestamp'].max(),
            strategies=[strategy],
            initial_cash=initial_cash,
            data_provider=DataFrameProvider(data),
            symbols=symbols
        )

        return result


class WalkForwardExporter:
    """Export walk-forward results to CSV."""

    @staticmethod
    def to_csv(result: WFResult, output_path: str) -> None:
        """Export walk-forward results to CSV."""
        if not result.windows:
            return
        
        windows_df = pd.DataFrame([
            {
                "train_start": w.train_start,
                "train_end": w.train_end,
                "test_start": w.test_start,
                "test_end": w.test_end,
                "train_sharpe": w.train_sharpe,
                "test_sharpe": w.test_sharpe,
                "test_return": w.test_return,
                "test_max_dd": w.test_max_dd,
                "train_trades": w.train_trades,
                "test_trades": w.test_trades,
                "param_trials": w.param_trials,
                **{f"param_{k}": v for k, v in w.params.items()}
            }
            for w in result.windows
        ])
        windows_df.to_csv(f"{output_path}_walkforward.csv", index=False)
        
        summary_df = pd.DataFrame([{
            "aggregate_sharpe": result.aggregate_sharpe,
            "aggregate_max_dd": result.aggregate_max_dd,
            "consistency": result.consistency,
            "sharpe_degradation": result.sharpe_degradation,
            "avg_train_sharpe": result.avg_train_sharpe,
            "avg_test_sharpe": result.avg_test_sharpe,
            "test_sharpe_std": result.test_sharpe_std,
            "pct_profitable": result.pct_profitable,
            "is_viable": result.is_viable,
            "param_trials": result.param_trials,
            "parameter_stability": result.parameter_stability,
            "min_train_trades": result.min_train_trades,
            "min_test_trades": result.min_test_trades,
            "multiple_testing_adjusted_alpha": result.multiple_testing_adjusted_alpha,
            "oos_p_value": result.oos_p_value,
            "oos_p_value_adjusted": result.oos_p_value_adjusted,
            "viability_warnings": "; ".join(result.viability_warnings),
            **{f"best_param_{k}": v for k, v in result.best_params.items()}
        }])
        summary_df.to_csv(f"{output_path}_summary.csv", index=False)
