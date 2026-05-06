from typing import Callable, Dict

import pandas as pd

from quant.features.research.models import StrategySpec


SignalFn = Callable[[pd.DataFrame, StrategySpec], pd.Series]


def build_validation_frame(bars, spec: StrategySpec) -> pd.DataFrame:
    df = _normalize_bars(bars)
    if df.empty or spec.signal_formula_key not in SIGNAL_FORMULAS:
        return pd.DataFrame()

    missing = [field for field in spec.required_fields if field not in df.columns]
    if missing:
        return pd.DataFrame()

    df["signal"] = SIGNAL_FORMULAS[spec.signal_formula_key](df, spec)
    grouped = df.groupby("symbol", group_keys=False)
    shift_start = max(1, int(spec.execution_lag_days))
    shift_end = shift_start + max(1, int(spec.horizon_days))
    df["return_start_date"] = grouped["date"].shift(-shift_start)
    df["return_end_date"] = grouped["date"].shift(-shift_end)
    df["return_start_close"] = grouped["close"].shift(-shift_start)
    df["return_end_close"] = grouped["close"].shift(-shift_end)
    df["forward_return"] = df["return_end_close"] / df["return_start_close"] - 1.0
    df["signal_date"] = df["date"]

    keep = [
        "signal_date",
        "symbol",
        "signal",
        "forward_return",
        "return_start_date",
        "return_end_date",
        "return_start_close",
        "return_end_close",
    ]
    frame = df[keep].replace([float("inf"), float("-inf")], pd.NA).dropna()
    return frame.sort_values(["signal_date", "symbol"]).reset_index(drop=True)


def momentum_close_return(df: pd.DataFrame, spec: StrategySpec) -> pd.Series:
    return df.groupby("symbol")["close"].pct_change(max(1, int(spec.lookback_days)))


def mean_reversion_close_to_ma(df: pd.DataFrame, spec: StrategySpec) -> pd.Series:
    lookback = max(2, int(spec.lookback_days))
    ma = df.groupby("symbol")["close"].transform(lambda series: series.rolling(lookback, min_periods=lookback).mean())
    return ma / df["close"] - 1.0


def volatility_breakout_atr(df: pd.DataFrame, spec: StrategySpec) -> pd.Series:
    lookback = max(2, int(spec.lookback_days))
    high = df.groupby("symbol")["high"].transform(lambda series: series.rolling(lookback, min_periods=lookback).max())
    low = df.groupby("symbol")["low"].transform(lambda series: series.rolling(lookback, min_periods=lookback).min())
    atr = (df["high"] - df["low"]).groupby(df["symbol"]).transform(lambda series: series.rolling(lookback, min_periods=lookback).mean())
    range_mid = (high + low) / 2.0
    return (df["close"] - range_mid) / atr.replace(0, pd.NA)


def _normalize_bars(bars) -> pd.DataFrame:
    if bars is None:
        return pd.DataFrame()
    df = pd.DataFrame(bars).copy()
    if df.empty:
        return df
    if "date" not in df.columns and "timestamp" in df.columns:
        df["date"] = df["timestamp"]
    required = {"date", "symbol", "close"}
    if not required.issubset(df.columns):
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    for column in ("open", "high", "low", "close", "volume"):
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df.sort_values(["symbol", "date"]).reset_index(drop=True)


SIGNAL_FORMULAS: Dict[str, SignalFn] = {
    "momentum_close_return": momentum_close_return,
    "mean_reversion_close_to_ma": mean_reversion_close_to_ma,
    "volatility_breakout_atr": volatility_breakout_atr,
}
