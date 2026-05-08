from typing import Any

import pandas as pd


def compute_signal(formula_key: str, data: Any, lookback: int = 20) -> Any:
    if formula_key == "momentum_close_return":
        close = data["close"] if isinstance(data, dict) else data.close
        return close.pct_change(lookback)
    elif formula_key == "mean_reversion_close_to_ma":
        close = data["close"] if isinstance(data, dict) else data.close
        ma = close.rolling(lookback).mean()
        return (close - ma) / ma
    elif formula_key == "volatility_breakout_atr":
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
    return None


SUPPORTED_FORMULAS = {
    "momentum_close_return",
    "mean_reversion_close_to_ma",
    "volatility_breakout_atr",
}
