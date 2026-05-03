"""Shared fixtures for all tests."""
from datetime import datetime, timedelta, date
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd
import pytest

from quant.features.backtest.engine import Backtester
from quant.features.backtest.walkforward import DataFrameProvider
from quant.features.strategies.base import Strategy
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.trading.sub_portfolio import SubPortfolio


_RNG = np.random.RandomState(42)


@pytest.fixture
def base_config():
    return {
        "backtest": {"slippage_bps": 0},
        "execution": {
            "commission": {
                "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
                "HK": {"type": "hk_realistic"},
                "CN": {"type": "cn_realistic"},
            }
        },
        "risk": {
            "max_position_pct": 0.20,
            "max_sector_pct": 0.40,
            "max_daily_loss_pct": 0.05,
        },
    }


def make_bar_dict(symbol, timestamp, open_, high, low, close, volume=1000000):
    return {
        "symbol": symbol,
        "timestamp": timestamp,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def make_bars_df(
    symbol: str,
    start: datetime,
    n_days: int,
    start_price: float = 100.0,
    daily_return: float = 0.001,
    volume: int = 1000000,
    noise: float = 0.02,
) -> pd.DataFrame:
    rows = []
    price = start_price
    for i in range(n_days):
        ts = start + timedelta(days=i)
        ret = daily_return + _RNG.normal(0, noise)
        price = price * (1 + ret)
        price = max(price, 1.0)
        high = price * (1 + abs(_RNG.normal(0, 0.005)))
        low = price * (1 - abs(_RNG.normal(0, 0.005)))
        open_price = round(price * (1 + _RNG.normal(0, 0.003)), 4)
        rows.append({
            "symbol": symbol,
            "timestamp": ts,
            "open": open_price,
            "high": round(high, 4),
            "low": round(low, 4),
            "close": round(price, 4),
            "volume": volume,
            "adj_open": open_price,
            "adj_high": round(high, 4),
            "adj_low": round(low, 4),
            "adj_close": round(price, 4),
            "adj_factor": 1.0,
        })
    return pd.DataFrame(rows)


def make_cn_bars(
    symbols: List[str],
    start: datetime,
    n_days: int,
    start_prices: Optional[Dict[str, float]] = None,
    daily_return: float = 0.001,
) -> pd.DataFrame:
    start_prices = start_prices or {s: 50.0 for s in symbols}
    dfs = []
    for sym in symbols:
        dfs.append(make_bars_df(sym, start, n_days, start_prices.get(sym, 50.0), daily_return, volume=5000000))
    return pd.concat(dfs, ignore_index=True)


def make_hk_bars(
    symbols: List[str],
    start: datetime,
    n_days: int,
    start_prices: Optional[Dict[str, float]] = None,
    daily_return: float = 0.001,
) -> pd.DataFrame:
    start_prices = start_prices or {s: 100.0 for s in symbols}
    dfs = []
    for sym in symbols:
        dfs.append(make_bars_df(sym, start, n_days, start_prices.get(sym, 100.0), daily_return, volume=2000000))
    return pd.concat(dfs, ignore_index=True)


def make_us_bars(
    symbols: List[str],
    start: datetime,
    n_days: int,
    start_prices: Optional[Dict[str, float]] = None,
    daily_return: float = 0.001,
) -> pd.DataFrame:
    start_prices = start_prices or {s: 150.0 for s in symbols}
    dfs = []
    for sym in symbols:
        dfs.append(make_bars_df(sym, start, n_days, start_prices.get(sym, 150.0), daily_return, volume=3000000))
    return pd.concat(dfs, ignore_index=True)


def make_dividends_df(symbol: str, ex_dates: List[datetime], amounts: List[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "symbol": [symbol] * len(ex_dates),
        "ex_date": ex_dates,
        "cash_dividend": amounts,
        "stock_dividend": [0.0] * len(ex_dates),
    })


def make_backtester(config=None, lot_sizes=None, ipo_dates=None):
    config = config or {
        "backtest": {"slippage_bps": 0},
        "execution": {"commission": {}},
        "risk": {},
    }
    return Backtester(
        config, lot_sizes=lot_sizes, ipo_dates=ipo_dates,
        portfolio_class=Portfolio,
        risk_engine_class=RiskEngine,
        sub_portfolio_class=SubPortfolio,
    )


class _TestStrategy(Strategy):
    """Reusable test strategy base — eliminates ~30 inline class duplications.

    Usage:
        strat = make_test_strategy("BuyAAPL", symbols=["AAPL"],
                                   on_after=lambda ctx, td: ctx.submit_order("AAPL", 10, "BUY"))
    """

    def __init__(self, name: str, symbols: List[str],
                 on_after: Optional[Callable] = None):
        super().__init__(name)
        self._symbols = symbols
        self._on_after = on_after

    @property
    def symbols(self) -> List[str]:
        return self._symbols

    def on_after_trading(self, context, trading_date):
        if self._on_after:
            self._on_after(context, trading_date)


def make_test_strategy(name: str, symbols: List[str],
                       on_after: Optional[Callable] = None) -> _TestStrategy:
    """Factory for test strategies with minimal boilerplate."""
    return _TestStrategy(name, symbols, on_after)


class MockDataProvider:
    """Configurable data provider for testing fallback paths."""

    def __init__(self, bars=None, trading_dates=None, dividends=None):
        self._bars = bars or {}
        self._trading_dates = trading_dates
        self._dividends = dividends or {}

    @property
    def trading_dates(self):
        return self._trading_dates

    def get_bar_for_date(self, symbol, dt):
        key = dt.date() if hasattr(dt, 'date') else dt
        return self._bars.get((symbol, key))

    def get_dividend_for_date(self, symbol, dt):
        key = dt.date() if hasattr(dt, 'date') else dt
        return self._dividends.get((symbol, key))

    def get_bars(self, symbol, start, end, timeframe="1d"):
        return pd.DataFrame()


def run_simple_backtest(
    bt: Backtester,
    data: pd.DataFrame,
    strategies: list,
    symbols: list,
    initial_cash: float = 1000000,
    dividends: Optional[pd.DataFrame] = None,
):
    provider = DataFrameProvider(data, dividends=dividends)
    return bt.run(
        start=data["timestamp"].min(),
        end=data["timestamp"].max(),
        strategies=strategies,
        initial_cash=initial_cash,
        data_provider=provider,
        symbols=symbols,
    )
