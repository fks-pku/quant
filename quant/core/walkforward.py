"""Walk-forward analysis framework with 6m train / 1m test / monthly step."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Any, Callable, Optional
import pandas as pd
import numpy as np

from quant.core.backtester import Backtester, BacktestResult
from quant.core.analytics import calculate_sharpe, calculate_max_drawdown


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


class WalkForwardEngine:
    """Walk-forward analysis engine with configurable train/test windows."""

    def __init__(
        self,
        train_window_days: int = 126,
        test_window_days: int = 21,
        step_days: int = 21,
        rebalance_freq: str = "monthly"
    ):
        self.train_window_days = train_window_days
        self.test_window_days = test_window_days
        self.step_days = step_days
        self.rebalance_freq = rebalance_freq

    def run(
        self,
        strategy_factory: Callable[[Dict], Any],
        data: pd.DataFrame,
        param_grid: Dict[str, List[Any]],
        initial_cash: float = 100000,
        config: Optional[Dict[str, Any]] = None
    ) -> WFResult:
        """
        Run walk-forward analysis.
        
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
        
        data = data.sort_values('timestamp')
        min_date = data['timestamp'].min()
        max_date = data['timestamp'].max()
        
        train_start = min_date
        while True:
            train_end = train_start + timedelta(days=self.train_window_days)
            test_start = train_end
            test_end = test_start + timedelta(days=self.test_window_days)
            
            if test_end > max_date:
                break
            
            train_data = data[(data['timestamp'] >= train_start) & (data['timestamp'] < train_end)]
            test_data = data[(data['timestamp'] >= test_start) & (data['timestamp'] < test_end)]
            
            if len(train_data) < 50 or len(test_data) < 10:
                train_start += timedelta(days=self.step_days)
                continue
            
            best_params, best_train_sharpe = self._find_best_params(
                strategy_factory, train_data, param_grid, initial_cash, config
            )
            
            strategy = strategy_factory(best_params)
            
            test_result = self._run_single_backtest(
                Backtester(config), strategy, test_data, initial_cash, config
            )
            
            test_max_dd = test_result.max_drawdown_pct if hasattr(test_result, 'max_drawdown_pct') else 0.0
            
            window_results.append(WFWindowResult(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                train_sharpe=best_train_sharpe,
                test_sharpe=test_result.sharpe_ratio,
                test_return=test_result.total_return,
                test_max_dd=test_max_dd,
                params=best_params
            ))
            
            train_start += timedelta(days=self.step_days)
        
        if not window_results:
            return WFResult(
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
                is_viable=False
            )
        
        aggregate_sharpe = np.mean([w.test_sharpe for w in window_results])
        aggregate_max_dd = max(w.test_max_dd for w in window_results) if window_results else 0.0
        consistency = len([w for w in window_results if w.test_return > 0]) / len(window_results)
        
        train_sharpes = [w.train_sharpe for w in window_results]
        test_sharpes = [w.test_sharpe for w in window_results]
        
        avg_train = float(np.mean(train_sharpes)) if train_sharpes else 0.0
        avg_test = float(np.mean(test_sharpes)) if test_sharpes else 0.0
        test_std = float(np.std(test_sharpes)) if test_sharpes else 0.0
        
        sharpe_degradation = 1.0 - (avg_test / avg_train) if avg_train != 0 else 1.0
        pct_profitable = float(len([w for w in window_results if w.test_return > 0]) / len(window_results)) if window_results else 0.0
        
        is_viable = avg_test > 0.5 and sharpe_degradation < 0.5 and pct_profitable > 0.5
        
        best_params = window_results[np.argmax([w.test_sharpe for w in window_results])].params
        
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
            is_viable=is_viable
        )

    def _find_best_params(
        self,
        strategy_factory: Callable[[Dict], Any],
        train_data: pd.DataFrame,
        param_grid: Dict[str, List[Any]],
        initial_cash: float,
        config: Dict[str, Any]
    ) -> tuple[Dict[str, Any], float]:
        """Find best params using grid search on training data."""
        best_params = {}
        best_sharpe = float('-inf')
        
        import itertools
        param_names = list(param_grid.keys())
        param_values = [param_grid[name] for name in param_names]
        
        for values in itertools.product(*param_values):
            params = dict(zip(param_names, values))
            
            try:
                strategy = strategy_factory(params)
                backtester = Backtester(config)
                result = self._run_single_backtest(backtester, strategy, train_data, initial_cash, config)
                
                if result.sharpe_ratio > best_sharpe:
                    best_sharpe = result.sharpe_ratio
                    best_params = params
            except Exception:
                continue
        
        return best_params, best_sharpe

    def _run_single_backtest(
        self,
        backtester_or_config,
        strategy: Any,
        data: pd.DataFrame,
        initial_cash: float,
        config: Optional[Dict[str, Any]] = None
    ) -> BacktestResult:
        """Run a single backtest on given data."""
        from quant.core.events import EventBus

        if isinstance(backtester_or_config, Backtester):
            backtester = backtester_or_config
        else:
            backtester = Backtester(config or {})

        event_bus = EventBus()
        
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


class DataFrameProvider:
    """In-memory data provider for backtesting, with pre-indexed lookup."""
    
    def __init__(self, data: pd.DataFrame):
        self.data = data
        self._bar_map: Dict[tuple, Dict] = {}
        self._trading_dates: set = set()
        self._build_index()

    def _build_index(self) -> None:
        if self.data.empty:
            return
        df = self.data
        if not pd.api.types.is_datetime64_any_dtype(df['timestamp']):
            df = df.copy()
            df['timestamp'] = pd.to_datetime(df['timestamp'])
        for col in ('open', 'high', 'low', 'close', 'volume'):
            if col not in df.columns:
                return
        records = df.to_dict('records')
        symbols = df['symbol'].tolist()
        timestamps = df['timestamp'].tolist()
        for rec, sym, ts in zip(records, symbols, timestamps):
            key = ts.date() if hasattr(ts, 'date') else ts
            self._bar_map[(sym, key)] = rec
            dt = datetime(ts.year, ts.month, ts.day) if hasattr(ts, 'year') else ts
            self._trading_dates.add(dt)

    @property
    def trading_dates(self) -> set:
        return self._trading_dates

    def get_bars(self, symbol: str, start: datetime, end: datetime, timeframe: str) -> pd.DataFrame:
        return self.data[
            (self.data['symbol'] == symbol) &
            (self.data['timestamp'] >= start) &
            (self.data['timestamp'] < end)
        ]

    def get_bar_for_date(self, symbol: str, date) -> Optional[Dict]:
        """O(1) lookup for a single bar by symbol + date."""
        key = date.date() if hasattr(date, 'date') else date
        return self._bar_map.get((symbol, key))

    def validate(self) -> List[str]:
        """Check data quality. Returns list of warning messages."""
        warnings = []
        if self.data.empty:
            return ["Data is empty"]

        for col in ['open', 'high', 'low', 'close']:
            mask_neg = self.data[col] < 0
            if mask_neg.any():
                warnings.append(f"Negative {col}: {mask_neg.sum()} rows")

        mask_zero = self.data['close'] == 0
        if mask_zero.any():
            warnings.append(f"Zero close price: {mask_zero.sum()} rows")

        ohlc_invalid = (
            (self.data['high'] < self.data['low']) |
            (self.data['high'] < self.data[['open', 'close']].max(axis=1)) |
            (self.data['low'] > self.data[['open', 'close']].min(axis=1))
        )
        if ohlc_invalid.any():
            warnings.append(f"OHLC logic violation: {ohlc_invalid.sum()} rows")

        for symbol in self.data['symbol'].unique():
            sym_data = self.data[self.data['symbol'] == symbol].sort_values('timestamp')
            same_close = (sym_data['close'] == sym_data['close'].shift(1)) & (sym_data['volume'] > 0)
            consecutive = same_close.rolling(20).sum()
            if (consecutive >= 20).any():
                warnings.append(f"{symbol}: 20+ consecutive same close with volume > 0")

        return warnings


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
            **{f"best_param_{k}": v for k, v in result.best_params.items()}
        }])
        summary_df.to_csv(f"{output_path}_summary.csv", index=False)
