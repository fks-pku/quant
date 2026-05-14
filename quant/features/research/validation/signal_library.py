from typing import Any, Callable

import numpy as np
import pandas as pd


def adjusted_price_series(data: Any, field: str) -> Any:
    adjusted_field = f"adj_{field}"
    if isinstance(data, dict):
        raw = data.get(field)
        adjusted = data.get(adjusted_field)
        if adjusted is None and raw is not None and data.get("adj_factor") is not None:
            adjusted = raw * data.get("adj_factor")
        return _coalesce_adjusted(adjusted, raw)
    if isinstance(data, pd.DataFrame):
        if adjusted_field in data.columns:
            raw = data[field] if field in data.columns else None
            return _coalesce_adjusted(data[adjusted_field], raw)
        if "adj_factor" in data.columns and field in data.columns:
            raw = data[field]
            return _coalesce_adjusted(raw * data["adj_factor"], raw)
        return data[field]
    adjusted = getattr(data, adjusted_field, None)
    raw = getattr(data, field, None)
    factor = getattr(data, "adj_factor", None)
    if adjusted is None and factor is not None:
        adjusted = raw * factor
    return _coalesce_adjusted(adjusted, raw)


def adjusted_price_matrix(data: pd.DataFrame, field: str = "close") -> pd.DataFrame:
    frame = data.copy()
    frame["_research_adjusted_price"] = adjusted_price_series(frame, field)
    return frame.pivot_table(
        index="date",
        columns="symbol",
        values="_research_adjusted_price",
        aggfunc="last",
    ).sort_index()


def field_matrix(data: pd.DataFrame, field: str) -> pd.DataFrame:
    frame = data.copy()
    return frame.pivot_table(
        index="date",
        columns="symbol",
        values=field,
        aggfunc="last",
    ).sort_index()


def compute_signal(formula_key: str, data: Any, lookback: int = 20) -> Any:
    if formula_key == "worldquant_alpha_001":
        if isinstance(data, pd.DataFrame) and {"symbol", "date"}.issubset(data.columns):
            return _worldquant_alpha_001_panel(data, lookback)
        return _worldquant_alpha_001_raw(data, lookback)
    if formula_key == "worldquant_alpha_002":
        if isinstance(data, pd.DataFrame) and {"symbol", "date"}.issubset(data.columns):
            return _worldquant_alpha_002_panel(data, lookback)
        return _worldquant_alpha_002_raw(data, lookback)

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
    close = adjusted_price_series(data, "close")
    return close.pct_change(lookback)


def _mean_reversion_close_to_ma(data: Any, lookback: int) -> Any:
    close = adjusted_price_series(data, "close")
    ma = close.rolling(lookback).mean()
    return (ma - close) / ma


def _volatility_breakout_atr(data: Any, lookback: int) -> Any:
    high = adjusted_price_series(data, "high")
    low = adjusted_price_series(data, "low")
    close = adjusted_price_series(data, "close")
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(lookback).mean()
    previous_high = high.shift(1).rolling(lookback).max()
    return (close - previous_high) / atr


def _worldquant_alpha_001_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    close = adjusted_price_matrix(data, "close")
    returns = close.pct_change(fill_method=None)
    stddev = returns.rolling(lookback, min_periods=lookback).std()
    base = close.mask(returns < 0, stddev)
    signed_power = base.where(base >= 0, -base.abs()).abs().pow(2).where(base >= 0, -base.abs().pow(2))
    ts_argmax = signed_power.rolling(5, min_periods=5).apply(lambda values: float(values.argmax()), raw=True)
    return ts_argmax.rank(axis=1, pct=True) - 0.5


def _worldquant_alpha_001_raw(data: Any, lookback: int) -> Any:
    close = adjusted_price_series(data, "close")
    returns = close.pct_change(fill_method=None)
    stddev = returns.rolling(lookback, min_periods=lookback).std()
    base = close.mask(returns < 0, stddev)
    signed_power = base.where(base >= 0, -base.abs()).abs().pow(2).where(base >= 0, -base.abs().pow(2))
    return signed_power.rolling(5, min_periods=5).apply(lambda values: float(values.argmax()), raw=True)


def _worldquant_alpha_002_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    corr_window = max(2, int(lookback or 6))
    open_ = adjusted_price_matrix(data, "open")
    close = adjusted_price_matrix(data, "close")
    volume = field_matrix(data, "volume").astype(float)
    log_volume = np.log(volume.where(volume > 0))
    delta_log_volume = log_volume.diff(2)
    intraday_return = (close - open_) / open_.where(open_ != 0)
    ranked_delta_volume = delta_log_volume.rank(axis=1, pct=True)
    ranked_intraday_return = intraday_return.rank(axis=1, pct=True)
    return -ranked_delta_volume.rolling(corr_window, min_periods=corr_window).corr(ranked_intraday_return)


def _worldquant_alpha_002_raw(data: Any, lookback: int) -> Any:
    corr_window = max(2, int(lookback or 6))
    open_ = adjusted_price_series(data, "open")
    close = adjusted_price_series(data, "close")
    if not hasattr(open_, "where") or not hasattr(close, "where"):
        return None
    volume = data["volume"] if isinstance(data, pd.DataFrame) else getattr(data, "volume", None)
    if volume is None:
        return None
    volume = pd.Series(volume, index=getattr(open_, "index", None), dtype=float)
    delta_log_volume = np.log(volume.where(volume > 0)).diff(2)
    intraday_return = (close - open_) / open_.where(open_ != 0)
    return -delta_log_volume.rolling(corr_window, min_periods=corr_window).corr(intraday_return)


def _coalesce_adjusted(adjusted: Any, raw: Any) -> Any:
    if adjusted is None:
        return raw
    if raw is None:
        return adjusted
    if hasattr(adjusted, "where"):
        return adjusted.where(pd.notna(adjusted), raw)
    try:
        return raw if pd.isna(adjusted) else adjusted
    except Exception:
        return adjusted


SUPPORTED_FORMULAS = {
    "momentum_close_return",
    "mean_reversion_close_to_ma",
    "volatility_breakout_atr",
    "worldquant_alpha_001",
    "worldquant_alpha_002",
}
