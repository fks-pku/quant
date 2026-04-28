"""Shared fixtures for all tests."""
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import pytest

from quant.features.backtest.engine import Backtester, CommissionConfig
from quant.features.backtest.walkforward import DataFrameProvider


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
        ret = daily_return + np.random.normal(0, noise)
        price = price * (1 + ret)
        price = max(price, 1.0)
        high = price * (1 + abs(np.random.normal(0, 0.005)))
        low = price * (1 - abs(np.random.normal(0, 0.005)))
        open_price = round(price * (1 + np.random.normal(0, 0.003)), 4)
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
    return Backtester(config, lot_sizes=lot_sizes, ipo_dates=ipo_dates)


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
