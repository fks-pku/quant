from typing import Any, Callable

import pandas as pd


def compute_signal(formula_key: str, data: Any, lookback: int = 20) -> Any:
    calculators = {
        "momentum_close_return": _momentum_close_return,
        "mean_reversion_close_to_ma": _mean_reversion_close_to_ma,
        "volatility_breakout_atr": _volatility_breakout_atr,
    }
    calculator = calculators.get(formula_key)
    if calculator is None:
        return None
    if isinstance(data, pd.DataFrame) and {"symbol", "date"}.issubset(data.columns):
        return _compute_panel_signal(data, lookback, calculator)
    return calculator(data, lookback)


def _compute_panel_signal(data: pd.DataFrame, lookback: int, calculator: Callable[[Any, int], Any]) -> pd.DataFrame:
    frame = data.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values(["symbol", "date"])
    values = frame.groupby("symbol", group_keys=False).apply(lambda group: calculator(group, lookback))
    frame = frame.assign(signal=values.to_numpy())
    return frame.pivot_table(index="date", columns="symbol", values="signal", aggfunc="last").sort_index()


def _momentum_close_return(data: Any, lookback: int) -> Any:
    close = data["close"] if isinstance(data, dict) else data.close
    return close.pct_change(lookback)


def _mean_reversion_close_to_ma(data: Any, lookback: int) -> Any:
    close = data["close"] if isinstance(data, dict) else data.close
    ma = close.rolling(lookback).mean()
    return (close - ma) / ma


def _volatility_breakout_atr(data: Any, lookback: int) -> Any:
    high = data["high"] if isinstance(data, dict) else data.high
    low = data["low"] if isinstance(data, dict) else data.low
    close = data["close"] if isinstance(data, dict) else data.close
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(lookback).mean()
    return atr


SUPPORTED_FORMULAS = {
    "momentum_close_return",
    "mean_reversion_close_to_ma",
    "volatility_breakout_atr",
}
