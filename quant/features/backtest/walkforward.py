"""Walk-forward analysis framework with 6m train / 1m test / monthly step."""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Any, Callable, Optional
import pandas as pd
import numpy as np

from quant.features.backtest.engine import Backtester, BacktestResult
from quant.features.backtest.analytics import calculate_sharpe, calculate_max_drawdown

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
        rebalance_freq: str = "monthly",
        min_trades: int = 3,
        lot_sizes: Optional[Dict[str, int]] = None,
        ipo_dates: Optional[Dict[str, date]] = None,
        portfolio_class=None,
        risk_engine_class=None,
        sub_portfolio_class=None,
        event_bus=None,
    ):
        self.train_window_days = train_window_days
        self.test_window_days = test_window_days
        self.step_days = step_days
        self.rebalance_freq = rebalance_freq
        self.min_trades = min_trades
        self.lot_sizes = lot_sizes or {}
        self.ipo_dates = ipo_dates or {}
        self.portfolio_class = portfolio_class
        self.risk_engine_class = risk_engine_class
        self.sub_portfolio_class = sub_portfolio_class
        self._event_bus = event_bus

        if self.portfolio_class is None:
            from quant.features.trading.portfolio import Portfolio
            self.portfolio_class = Portfolio
        if self.risk_engine_class is None:
            from quant.features.trading.risk import RiskEngine
            self.risk_engine_class = RiskEngine
        if self.sub_portfolio_class is None:
            from quant.features.trading.sub_portfolio import SubPortfolio
            self.sub_portfolio_class = SubPortfolio

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
        
        if not pd.api.types.is_datetime64_any_dtype(data['timestamp']):
            data = data.copy()
            data['timestamp'] = pd.to_datetime(data['timestamp'])
        
        data = data.sort_values('timestamp')
        unique_dates = sorted(data['timestamp'].dt.normalize().unique())
        if hasattr(unique_dates[0], 'to_pydatetime'):
            unique_dates = [d.to_pydatetime() for d in unique_dates]
        n_dates = len(unique_dates)
        
        if n_dates < self.train_window_days + self.test_window_days:
            return WFResult(
                windows=[], aggregate_sharpe=0.0, aggregate_max_dd=0.0,
                consistency=0.0, best_params={}, sharpe_degradation=0.0,
                avg_train_sharpe=0.0, avg_test_sharpe=0.0, test_sharpe_std=0.0,
                pct_profitable=0.0, is_viable=False
            )
        
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
            
            best_params, best_train_sharpe = self._find_best_params(
                strategy_factory, train_data, param_grid, initial_cash, config
            )
            
            if best_train_sharpe == float('-inf'):
                step_idx += self.step_days
                continue
            
            strategy = strategy_factory(best_params)

            test_result = self._run_single_backtest(
                config or {}, strategy, test_data, initial_cash
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
            
            step_idx += self.step_days
        
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
        test_std = float(np.std(test_sharpes, ddof=1)) if len(test_sharpes) > 1 else 0.0
        
        if avg_train > 0:
            sharpe_degradation = max(0.0, 1.0 - (avg_test / avg_train))
        elif avg_test > 0:
            sharpe_degradation = 0.0
        else:
            sharpe_degradation = 1.0
        pct_profitable = float(len([w for w in window_results if w.test_return > 0]) / len(window_results)) if window_results else 0.0
        
        is_viable = avg_test > 0.5 and sharpe_degradation < 0.5 and pct_profitable > 0.5

        from collections import Counter
        param_tuples = [tuple(sorted(w.params.items())) for w in window_results if w.params]
        if param_tuples:
            most_common_tuple, _ = Counter(param_tuples).most_common(1)[0]
            best_params = dict(most_common_tuple)
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
        import itertools
        param_names = list(param_grid.keys())
        param_values = [param_grid[name] for name in param_names]
        best_params: Dict[str, Any] = {}
        best_sharpe = float('-inf')
        
        for values in itertools.product(*param_values):
            params = dict(zip(param_names, values))
            
            try:
                strategy = strategy_factory(params)
                result = self._run_single_backtest(config, strategy, train_data, initial_cash)
                
                if len(result.trades) < self.min_trades:
                    continue
                
                if result.sharpe_ratio > best_sharpe:
                    best_sharpe = result.sharpe_ratio
                    best_params = params
            except Exception as e:
                logger.warning("WalkForward grid search failed for params %s: %s", params, e)
                continue
        
        return best_params, best_sharpe

    def _run_single_backtest(
        self,
        config: Dict[str, Any],
        strategy: Any,
        data: pd.DataFrame,
        initial_cash: float,
    ) -> BacktestResult:
        event_bus = self._event_bus
        if event_bus is None:
            from quant.infrastructure.events import EventBus
            event_bus = EventBus()
        backtester = Backtester(
            config or {},
            event_bus=event_bus,
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


class DataFrameProvider:
    """In-memory data provider for backtesting, with pre-indexed lookup."""
    
    def __init__(self, data: pd.DataFrame, dividends: Optional[pd.DataFrame] = None):
        self.data = data
        self.dividends = dividends if dividends is not None else pd.DataFrame()
        self._bar_map: Dict[tuple, Dict] = {}
        self._trading_dates: set = set()
        self._dividend_map: Dict[tuple, Dict] = {}
        self._build_index()
        self._build_dividend_index()

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
        buf: Dict[tuple, Dict] = {}
        for rec, sym, ts in zip(records, symbols, timestamps):
            key = ts.date() if hasattr(ts, 'date') else ts
            dict_key = (sym, key)
            existing = buf.get(dict_key)
            if existing is None:
                buf[dict_key] = rec
            else:
                existing_vol = existing.get('volume', 0) or 0
                new_vol = rec.get('volume', 0) or 0
                if new_vol > existing_vol:
                    buf[dict_key] = rec
        dup_count = len(records) - len(buf)
        self._bar_map = buf
        for ts in timestamps:
            dt = ts.date() if hasattr(ts, 'date') else ts
            self._trading_dates.add(dt)
        if dup_count > 0:
            import logging
            logging.getLogger(__name__).warning(
                "DataFrameProvider._build_index: resolved %d duplicate (symbol, date) rows (kept highest volume)",
                dup_count,
            )

    def _build_dividend_index(self) -> None:
        if self.dividends.empty:
            return
        df = self.dividends.copy()
        if 'ex_date' in df.columns and not pd.api.types.is_datetime64_any_dtype(df['ex_date']):
            df['ex_date'] = pd.to_datetime(df['ex_date'])
        for _, row in df.iterrows():
            if 'ex_date' not in row or pd.isna(row['ex_date']):
                continue
            sym = row.get('symbol', '')
            ex_dt = row['ex_date']
            key = ex_dt.date() if hasattr(ex_dt, 'date') else ex_dt
            self._dividend_map[(sym, key)] = row.to_dict()

    @property
    def trading_dates(self) -> set:
        return self._trading_dates

    def get_bars(self, symbol: str, start: datetime, end: datetime, timeframe: str) -> pd.DataFrame:
        start_key = start.date() if hasattr(start, 'date') else start
        end_key = end.date() if hasattr(end, 'date') else end
        rows = []
        for d in sorted(set(k[1] for k in self._bar_map if k[0] == symbol)):
            if start_key <= d < end_key:
                rec = self._bar_map.get((symbol, d))
                if rec is not None:
                    rows.append(rec)
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)

    def get_bar_for_date(self, symbol: str, date) -> Optional[Dict]:
        """O(1) lookup for a single bar by symbol + date."""
        key = date.date() if hasattr(date, 'date') else date
        return self._bar_map.get((symbol, key))

    def get_dividend_for_date(self, symbol: str, date) -> Optional[Dict]:
        """O(1) lookup for dividend by symbol + ex_date."""
        key = date.date() if hasattr(date, 'date') else date
        return self._dividend_map.get((symbol, key))

    def validate(self) -> List[str]:
        """Check data quality. Delegates to DataValidator, returns all messages for backward compat."""
        from quant.features.backtest.data_validator import DataValidator
        report = DataValidator.validate(self.data)
        return report.errors + report.warnings


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
