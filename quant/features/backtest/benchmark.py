"""Benchmark provider for comparing strategy against buy-and-hold."""

from datetime import date, datetime
from typing import Optional, Union
import pandas as pd
import numpy as np


def _to_date(val) -> date:
    return val.date() if hasattr(val, "date") else val


class BenchmarkProvider:
    """Computes buy-and-hold benchmark equity and returns from OHLCV data.

    Accepts either a raw DataFrame (with columns timestamp, close) or a
    DataFrameProvider instance (common in backtests).

    The benchmark holds one unit of the asset from start date and tracks
    the close-price return series.
    """

    def __init__(self, data: Union[pd.DataFrame, "DataFrameProvider"], price_column: str = "close"):
        if data is None:
            raise ValueError("Benchmark data cannot be None")

        from quant.features.backtest.walkforward import DataFrameProvider

        if isinstance(data, DataFrameProvider):
            self._data = data.data
        elif isinstance(data, pd.DataFrame):
            self._data = data
        else:
            raise TypeError(f"Expected DataFrame or DataFrameProvider, got {type(data).__name__}")

        if self._data.empty:
            raise ValueError("Benchmark data cannot be empty")

        self._price_column = price_column
        self._daily_returns: Optional[pd.Series] = None
        self._timestamps: Optional[pd.Series] = None
        self._compute_returns()

    def _compute_returns(self) -> None:
        df = self._data.copy()
        if self._price_column not in df.columns:
            if "close" in df.columns:
                self._price_column = "close"
            else:
                raise ValueError(
                    f"Benchmark data has no '{self._price_column}' or 'close' column; "
                    f"available columns: {list(df.columns)}"
                )

        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"])

        df = df.sort_values("timestamp")
        grouped = df.groupby(df["timestamp"].dt.date)
        close_series = grouped[self._price_column].last()
        close_series.index = pd.to_datetime(close_series.index)
        self._timestamps = close_series.index
        self._daily_returns = close_series.pct_change().dropna()

    def get_benchmark_returns(self, start: Optional[datetime] = None, end: Optional[datetime] = None) -> pd.Series:
        """Return daily return series, optionally filtered by date range."""
        returns = self._daily_returns
        if returns is None or returns.empty:
            return pd.Series(dtype=float)
        if start is not None:
            start_d = _to_date(start)
            returns = returns[returns.index >= pd.Timestamp(start_d)]
        if end is not None:
            end_d = _to_date(end)
            returns = returns[returns.index <= pd.Timestamp(end_d)]
        return returns

    def get_benchmark_equity(self, start: Optional[datetime] = None, end: Optional[datetime] = None, initial_cash: float = 100000.0) -> pd.Series:
        """Return benchmark equity curve starting from initial_cash."""
        returns = self.get_benchmark_returns(start, end)
        if returns.empty:
            return pd.Series(dtype=float)
        cumulative = (1.0 + returns).cumprod()
        equity = initial_cash * cumulative
        equity.index = returns.index
        return equity
