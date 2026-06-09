from __future__ import annotations

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
    if formula_key == "worldquant_alpha_003":
        if isinstance(data, pd.DataFrame) and {"symbol", "date"}.issubset(data.columns):
            return _worldquant_alpha_003_panel(data, lookback)
        return _worldquant_alpha_003_raw(data, lookback)
    if formula_key == "worldquant_alpha_004":
        if isinstance(data, pd.DataFrame) and {"symbol", "date"}.issubset(data.columns):
            return _worldquant_alpha_004_panel(data, lookback)
        return _worldquant_alpha_004_raw(data, lookback)
    if formula_key == "worldquant_alpha_006":
        if isinstance(data, pd.DataFrame) and {"symbol", "date"}.issubset(data.columns):
            return _worldquant_alpha_006_panel(data, lookback)
        return _worldquant_alpha_006_raw(data, lookback)
    if formula_key == "worldquant_alpha_010":
        if isinstance(data, pd.DataFrame) and {"symbol", "date"}.issubset(data.columns):
            return _worldquant_alpha_010_panel(data, lookback)
        return _worldquant_alpha_010_raw(data, lookback)

    calculators = {
        "ashare_short_reversal_5d": _ashare_short_reversal,
        "ashare_volume_exhaustion_reversal": _ashare_volume_exhaustion_reversal,
        "ashare_volume_dryup_pullback": _ashare_volume_dryup_pullback,
        "ashare_lottery_demand_avoidance": _ashare_lottery_demand_avoidance,
        "ashare_low_volatility_defensive": _ashare_low_volatility_defensive,
        "ashare_gap_down_reversal": _ashare_gap_down_reversal,
        "ashare_volatility_scaled_reversal": _ashare_volatility_scaled_reversal,
        "ashare_liquidity_weighted_low_volatility": _ashare_liquidity_weighted_low_volatility,
        "ashare_low_volatility_momentum": _ashare_low_volatility_momentum,
        "ashare_range_contraction_breakout": _ashare_range_contraction_breakout,
        "ashare_gap_down_liquid_reversal": _ashare_gap_down_liquid_reversal,
        "ashare_turnover_stability_factor": _ashare_turnover_stability_factor,
        "ashare_small_cap_guarded_size_factor": _ashare_small_cap_guarded_size_factor,
        "ashare_price_volume_multifactor": _ashare_price_volume_multifactor,
        "ashare_industry_prosperity_trend_crowding_rotation": _ashare_industry_prosperity_trend_crowding_rotation,
        "joinquant_small_cap_size_factor": _joinquant_small_cap_size_factor,
        "joinquant_small_cap_low_price_factor": _joinquant_small_cap_low_price_factor,
        "momentum_close_return": _momentum_close_return,
        "mean_reversion_close_to_ma": _mean_reversion_close_to_ma,
        "volatility_breakout_atr": _volatility_breakout_atr,
    }
    calculator = calculators.get(formula_key)
    if calculator is None:
        return None
    if isinstance(data, pd.DataFrame) and {"symbol", "date"}.issubset(data.columns):
        panel_calculators = {
            "ashare_short_reversal_5d": _ashare_short_reversal_panel,
            "ashare_volume_exhaustion_reversal": _ashare_volume_exhaustion_reversal_panel,
            "ashare_volume_dryup_pullback": _ashare_volume_dryup_pullback_panel,
            "ashare_lottery_demand_avoidance": _ashare_lottery_demand_avoidance_panel,
            "ashare_low_volatility_defensive": _ashare_low_volatility_defensive_panel,
            "ashare_gap_down_reversal": _ashare_gap_down_reversal_panel,
            "ashare_volatility_scaled_reversal": _ashare_volatility_scaled_reversal_panel,
            "ashare_liquidity_weighted_low_volatility": _ashare_liquidity_weighted_low_volatility_panel,
            "ashare_low_volatility_momentum": _ashare_low_volatility_momentum_panel,
            "ashare_range_contraction_breakout": _ashare_range_contraction_breakout_panel,
            "ashare_gap_down_liquid_reversal": _ashare_gap_down_liquid_reversal_panel,
            "ashare_turnover_stability_factor": _ashare_turnover_stability_factor_panel,
            "ashare_small_cap_guarded_size_factor": _ashare_small_cap_guarded_size_factor_panel,
            "ashare_price_volume_multifactor": _ashare_price_volume_multifactor_panel,
            "ashare_industry_prosperity_trend_crowding_rotation": _ashare_industry_prosperity_trend_crowding_rotation_panel,
            "joinquant_small_cap_size_factor": _joinquant_small_cap_size_factor_panel,
            "joinquant_small_cap_low_price_factor": _joinquant_small_cap_low_price_factor_panel,
            "momentum_close_return": _momentum_close_return_panel,
            "mean_reversion_close_to_ma": _mean_reversion_close_to_ma_panel,
            "volatility_breakout_atr": _volatility_breakout_atr_panel,
        }
        panel_calculator = panel_calculators.get(formula_key)
        if panel_calculator is not None:
            return panel_calculator(data, lookback)
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
    return close.pct_change(lookback, fill_method=None)


def _momentum_close_return_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    close = adjusted_price_matrix(data, "close")
    return close.pct_change(lookback, fill_method=None)


def _ashare_short_reversal(data: Any, lookback: int) -> Any:
    close = adjusted_price_series(data, "close")
    return -close.pct_change(max(1, int(lookback or 5)), fill_method=None)


def _ashare_short_reversal_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    close = adjusted_price_matrix(data, "close")
    return -close.pct_change(max(1, int(lookback or 5)), fill_method=None)


def _ashare_volume_exhaustion_reversal(data: Any, lookback: int) -> Any:
    close = adjusted_price_series(data, "close")
    if not hasattr(close, "rolling"):
        return None
    volume = data["volume"] if isinstance(data, pd.DataFrame) else getattr(data, "volume", None)
    if volume is None:
        return None
    volume = pd.Series(volume, index=getattr(close, "index", None), dtype=float)
    close_series = pd.Series(close, index=getattr(close, "index", None), dtype=float)
    ret = close_series.pct_change(5, fill_method=None)
    volume_ratio = volume / volume.rolling(max(10, int(lookback or 20)), min_periods=max(10, int(lookback or 20))).mean()
    return (-ret.where(ret < 0.0, 0.0)) * np.log1p(volume_ratio.clip(lower=0.0))


def _ashare_volume_exhaustion_reversal_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    window = max(10, int(lookback or 20))
    close = adjusted_price_matrix(data, "close")
    volume = field_matrix(data, "volume").astype(float)
    ret = close.pct_change(5, fill_method=None)
    volume_ratio = volume / volume.rolling(window, min_periods=window).mean()
    return (-ret.where(ret < 0.0, 0.0)) * np.log1p(volume_ratio.clip(lower=0.0))


def _ashare_volume_dryup_pullback(data: Any, lookback: int) -> Any:
    close = adjusted_price_series(data, "close")
    if not hasattr(close, "rolling"):
        return None
    volume = data["volume"] if isinstance(data, pd.DataFrame) else getattr(data, "volume", None)
    if volume is None:
        return None
    volume = pd.Series(volume, index=getattr(close, "index", None), dtype=float)
    close_series = pd.Series(close, index=getattr(close, "index", None), dtype=float)
    ret = close_series.pct_change(5, fill_method=None)
    volume_ratio = volume / volume.rolling(max(10, int(lookback or 20)), min_periods=max(10, int(lookback or 20))).mean()
    dryup = (1.0 - volume_ratio).clip(lower=0.0)
    return (-ret.where(ret < 0.0, 0.0)) * dryup


def _ashare_volume_dryup_pullback_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    window = max(10, int(lookback or 20))
    close = adjusted_price_matrix(data, "close")
    volume = field_matrix(data, "volume").astype(float)
    ret = close.pct_change(5, fill_method=None)
    volume_ratio = volume / volume.rolling(window, min_periods=window).mean()
    dryup = (1.0 - volume_ratio).clip(lower=0.0)
    return (-ret.where(ret < 0.0, 0.0)) * dryup


def _ashare_lottery_demand_avoidance(data: Any, lookback: int) -> Any:
    close = adjusted_price_series(data, "close")
    if not hasattr(close, "rolling"):
        return None
    returns = close.pct_change(fill_method=None)
    window = max(5, int(lookback or 20))
    max_return = returns.rolling(window, min_periods=window).max().clip(lower=0.0)
    volatility = returns.rolling(window, min_periods=window).std().clip(lower=0.0)
    return 1.0 / (1.0 + max_return + volatility * np.sqrt(252.0))


def _ashare_lottery_demand_avoidance_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    close = adjusted_price_matrix(data, "close")
    returns = close.pct_change(fill_method=None)
    window = max(5, int(lookback or 20))
    max_return = returns.rolling(window, min_periods=window).max().clip(lower=0.0)
    volatility = returns.rolling(window, min_periods=window).std().clip(lower=0.0)
    return 1.0 / (1.0 + max_return + volatility * np.sqrt(252.0))


def _ashare_low_volatility_defensive(data: Any, lookback: int) -> Any:
    close = adjusted_price_series(data, "close")
    if not hasattr(close, "rolling"):
        return None
    returns = close.pct_change(fill_method=None)
    volatility = returns.rolling(max(5, int(lookback or 20)), min_periods=max(5, int(lookback or 20))).std()
    return 1.0 / (1.0 + volatility.clip(lower=0.0) * np.sqrt(252.0))


def _ashare_low_volatility_defensive_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    close = adjusted_price_matrix(data, "close")
    returns = close.pct_change(fill_method=None)
    window = max(5, int(lookback or 20))
    volatility = returns.rolling(window, min_periods=window).std()
    return 1.0 / (1.0 + volatility.clip(lower=0.0) * np.sqrt(252.0))


def _ashare_gap_down_reversal(data: Any, lookback: int) -> Any:
    open_ = adjusted_price_series(data, "open")
    close = adjusted_price_series(data, "close")
    if not hasattr(open_, "shift") or not hasattr(close, "shift"):
        return None
    previous_close = close.shift(1)
    gap = open_ / previous_close.where(previous_close != 0.0) - 1.0
    return -gap


def _ashare_gap_down_reversal_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    open_ = adjusted_price_matrix(data, "open")
    close = adjusted_price_matrix(data, "close")
    previous_close = close.shift(1)
    gap = open_ / previous_close.where(previous_close != 0.0) - 1.0
    return -gap


def _ashare_volatility_scaled_reversal(data: Any, lookback: int) -> Any:
    close = adjusted_price_series(data, "close")
    if not hasattr(close, "rolling"):
        return None
    close_series = pd.Series(close, index=getattr(close, "index", None), dtype=float)
    window = max(10, int(lookback or 20))
    ret = close_series.pct_change(5, fill_method=None)
    vol = close_series.pct_change(fill_method=None).rolling(window, min_periods=window).std()
    return (-ret.where(ret < 0.0, 0.0)) / vol.where(vol > 0.0)


def _ashare_volatility_scaled_reversal_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    close = adjusted_price_matrix(data, "close")
    window = max(10, int(lookback or 20))
    ret = close.pct_change(5, fill_method=None)
    vol = close.pct_change(fill_method=None).rolling(window, min_periods=window).std()
    return (-ret.where(ret < 0.0, 0.0)) / vol.where(vol > 0.0)


def _ashare_liquidity_weighted_low_volatility(data: Any, lookback: int) -> Any:
    close = adjusted_price_series(data, "close")
    if not hasattr(close, "rolling"):
        return None
    returns = close.pct_change(fill_method=None)
    window = max(10, int(lookback or 20))
    vol = returns.rolling(window, min_periods=window).std().clip(lower=0.0) * np.sqrt(252.0)
    turnover = _turnover_series(data, close).rolling(window, min_periods=window).mean()
    return np.log1p(turnover.clip(lower=0.0)) / (1.0 + vol)


def _ashare_liquidity_weighted_low_volatility_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    close = adjusted_price_matrix(data, "close")
    returns = close.pct_change(fill_method=None)
    window = max(10, int(lookback or 20))
    vol = returns.rolling(window, min_periods=window).std().clip(lower=0.0) * np.sqrt(252.0)
    turnover = _turnover_matrix(data, close).rolling(window, min_periods=window).mean()
    return np.log1p(turnover.clip(lower=0.0)) / (1.0 + vol)


def _ashare_low_volatility_momentum(data: Any, lookback: int) -> Any:
    close = adjusted_price_series(data, "close")
    if not hasattr(close, "rolling"):
        return None
    window = max(10, int(lookback or 20))
    momentum = close.pct_change(window, fill_method=None)
    vol = close.pct_change(fill_method=None).rolling(window, min_periods=window).std().clip(lower=0.0) * np.sqrt(252.0)
    return momentum.where(momentum > 0.0, 0.0) / (1.0 + vol)


def _ashare_low_volatility_momentum_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    close = adjusted_price_matrix(data, "close")
    window = max(10, int(lookback or 20))
    momentum = close.pct_change(window, fill_method=None)
    vol = close.pct_change(fill_method=None).rolling(window, min_periods=window).std().clip(lower=0.0) * np.sqrt(252.0)
    return momentum.where(momentum > 0.0, 0.0) / (1.0 + vol)


def _ashare_industry_prosperity_trend_crowding_rotation(data: Any, lookback: int) -> Any:
    if not isinstance(data, pd.DataFrame):
        return None
    return _ashare_industry_prosperity_trend_crowding_rotation_panel(data, lookback)


def _ashare_industry_prosperity_trend_crowding_rotation_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    if not {"date", "symbol", "close"}.issubset(data.columns):
        return None
    industry_field = _industry_group_field(data)
    if not industry_field:
        return None
    frame = data[["date", "symbol", "volume", industry_field]].copy() if "volume" in data.columns else data[["date", "symbol", industry_field]].copy()
    close = adjusted_price_matrix(data, "close")
    frame["date"] = pd.to_datetime(frame["date"])
    frame["symbol"] = frame["symbol"].astype(str)
    frame["_industry"] = frame[industry_field].astype(str).replace({"": np.nan, "nan": np.nan, "None": np.nan})
    returns = close.pct_change(fill_method=None)
    stock_momentum = close.pct_change(max(20, int(lookback or 60)), fill_method=None)
    ret_long = _stack_matrix(returns, "_return")
    ret_long["symbol"] = ret_long["symbol"].astype(str)
    merged = frame.merge(ret_long, on=["date", "symbol"], how="left").dropna(subset=["_industry"])
    if merged.empty:
        return None
    industry_returns = merged.groupby(["date", "_industry"])["_return"].mean().unstack().sort_index()
    breadth_long = _stack_matrix(stock_momentum.gt(0.0), "_positive_trend")
    breadth_long["symbol"] = breadth_long["symbol"].astype(str)
    breadth_frame = frame.merge(breadth_long, on=["date", "symbol"], how="left").dropna(subset=["_industry"])
    industry_breadth = breadth_frame.groupby(["date", "_industry"])["_positive_trend"].mean().unstack().reindex(industry_returns.index)
    window = max(20, int(lookback or 60))
    short_window = max(10, min(20, window))
    prosperity = industry_returns.rolling(short_window, min_periods=short_window).mean().rank(axis=1, pct=True)
    breadth_score = industry_breadth.rolling(short_window, min_periods=short_window).mean().rank(axis=1, pct=True)
    trend_return = industry_returns.rolling(window, min_periods=window).sum()
    trend_vol = industry_returns.rolling(short_window, min_periods=short_window).std().replace(0.0, np.nan)
    trend = (trend_return / trend_vol).rank(axis=1, pct=True)
    if "volume" in frame.columns:
        volume_frame = frame.merge(
            _stack_matrix(field_matrix(data, "volume"), "_volume"),
            on=["date", "symbol"],
            how="left",
        ).dropna(subset=["_industry"])
        industry_activity = np.log1p(volume_frame.groupby(["date", "_industry"])["_volume"].sum().unstack().sort_index().clip(lower=0.0))
    else:
        industry_activity = industry_returns.abs()
    crowding_activity = _rolling_zscore(industry_activity, window).clip(lower=0.0)
    crowding_vol = _rolling_zscore(industry_returns.rolling(short_window, min_periods=short_window).std(), window).clip(lower=0.0)
    crowding = (0.5 * crowding_activity + 0.5 * crowding_vol).rank(axis=1, pct=True)
    composite = (
        0.30 * prosperity.fillna(0.0)
        + 0.25 * breadth_score.fillna(0.0)
        + 0.35 * trend.fillna(0.0)
        - 0.20 * crowding.fillna(0.0)
    )
    industry_score_long = _stack_matrix(composite, "_industry_score")
    scored = frame.merge(industry_score_long, on=["date", "_industry"], how="left")
    stock_tiebreaker = _stack_matrix(stock_momentum.rank(axis=1, pct=True), "_stock_tiebreaker")
    stock_tiebreaker["symbol"] = stock_tiebreaker["symbol"].astype(str)
    scored = scored.merge(stock_tiebreaker, on=["date", "symbol"], how="left")
    scored["_signal"] = scored["_industry_score"] + 0.01 * scored["_stock_tiebreaker"].fillna(0.0)
    return scored.pivot_table(index="date", columns="symbol", values="_signal", aggfunc="last").sort_index()


def _ashare_range_contraction_breakout(data: Any, lookback: int) -> Any:
    high = adjusted_price_series(data, "high")
    low = adjusted_price_series(data, "low")
    close = adjusted_price_series(data, "close")
    if not hasattr(close, "rolling"):
        return None
    window = max(10, int(lookback or 20))
    high_roll = high.rolling(window, min_periods=window).max()
    low_roll = low.rolling(window, min_periods=window).min()
    range_position = (close - low_roll) / (high_roll - low_roll).where((high_roll - low_roll) > 0.0)
    daily_range = (high - low) / close.where(close > 0.0)
    range_vol = daily_range.rolling(window, min_periods=window).mean().clip(lower=0.0)
    return range_position.clip(lower=0.0, upper=1.0) / (1.0 + range_vol * 100.0)


def _ashare_range_contraction_breakout_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    high = adjusted_price_matrix(data, "high")
    low = adjusted_price_matrix(data, "low")
    close = adjusted_price_matrix(data, "close")
    window = max(10, int(lookback or 20))
    high_roll = high.rolling(window, min_periods=window).max()
    low_roll = low.rolling(window, min_periods=window).min()
    width = high_roll - low_roll
    range_position = (close - low_roll) / width.where(width > 0.0)
    daily_range = (high - low) / close.where(close > 0.0)
    range_vol = daily_range.rolling(window, min_periods=window).mean().clip(lower=0.0)
    return range_position.clip(lower=0.0, upper=1.0) / (1.0 + range_vol * 100.0)


def _ashare_gap_down_liquid_reversal(data: Any, lookback: int) -> Any:
    gap_signal = _ashare_gap_down_reversal(data, lookback)
    if gap_signal is None:
        return None
    close = adjusted_price_series(data, "close")
    window = max(10, int(lookback or 20))
    turnover = _turnover_series(data, close).rolling(window, min_periods=window).mean()
    return gap_signal.where(gap_signal > 0.0, 0.0) * np.log1p(turnover.clip(lower=0.0))


def _ashare_gap_down_liquid_reversal_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    gap_signal = _ashare_gap_down_reversal_panel(data, lookback)
    close = adjusted_price_matrix(data, "close")
    window = max(10, int(lookback or 20))
    turnover = _turnover_matrix(data, close).rolling(window, min_periods=window).mean()
    return gap_signal.where(gap_signal > 0.0, 0.0) * np.log1p(turnover.clip(lower=0.0))


def _ashare_turnover_stability_factor(data: Any, lookback: int) -> Any:
    close = adjusted_price_series(data, "close")
    window = max(10, int(lookback or 20))
    turnover = _turnover_series(data, close)
    avg_turnover = turnover.rolling(window, min_periods=window).mean()
    turnover_vol = turnover.rolling(window, min_periods=window).std()
    stability = avg_turnover / turnover_vol.where(turnover_vol > 0.0)
    return np.log1p(avg_turnover.clip(lower=0.0)) * stability.clip(lower=0.0)


def _ashare_turnover_stability_factor_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    close = adjusted_price_matrix(data, "close")
    window = max(10, int(lookback or 20))
    turnover = _turnover_matrix(data, close)
    avg_turnover = turnover.rolling(window, min_periods=window).mean()
    turnover_vol = turnover.rolling(window, min_periods=window).std()
    stability = avg_turnover / turnover_vol.where(turnover_vol > 0.0)
    return np.log1p(avg_turnover.clip(lower=0.0)) * stability.clip(lower=0.0)


def _ashare_price_volume_multifactor(data: Any, lookback: int) -> Any:
    close = adjusted_price_series(data, "close")
    if not hasattr(close, "rolling"):
        return None
    close_series = pd.Series(close, index=getattr(close, "index", None), dtype=float)
    returns = close_series.pct_change(fill_method=None)
    window = max(10, int(lookback or 20))
    momentum = close_series.pct_change(window, fill_method=None)
    reversal = -close_series.pct_change(5, fill_method=None)
    low_vol = -returns.rolling(window, min_periods=window).std()
    volume = data["volume"] if isinstance(data, pd.DataFrame) and "volume" in data.columns else getattr(data, "volume", None)
    if volume is None:
        return None
    volume = pd.Series(volume, index=close_series.index, dtype=float)
    liquidity = np.log1p(volume.rolling(window, min_periods=window).mean().clip(lower=0.0))
    return (
        0.40 * _time_series_zscore(momentum, window)
        + 0.25 * _time_series_zscore(low_vol, window)
        + 0.20 * _time_series_zscore(reversal, window)
        + 0.15 * _time_series_zscore(liquidity, window)
    )


def _ashare_price_volume_multifactor_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    close = adjusted_price_matrix(data, "close")
    volume = field_matrix(data, "volume").astype(float)
    window = max(10, int(lookback or 20))
    returns = close.pct_change(fill_method=None)
    momentum = close.pct_change(window, fill_method=None)
    reversal = -close.pct_change(5, fill_method=None)
    low_vol = -returns.rolling(window, min_periods=window).std()
    liquidity = np.log1p(volume.rolling(window, min_periods=window).mean().clip(lower=0.0))
    return (
        0.40 * momentum.rank(axis=1, pct=True)
        + 0.25 * low_vol.rank(axis=1, pct=True)
        + 0.20 * reversal.rank(axis=1, pct=True)
        + 0.15 * liquidity.rank(axis=1, pct=True)
    )


def _joinquant_small_cap_size_factor(data: Any, lookback: int) -> Any:
    if not isinstance(data, pd.DataFrame):
        return None
    market_cap = _market_cap_series(data)
    if market_cap is None:
        return None
    return _inverse_market_cap(market_cap)


def _joinquant_small_cap_size_factor_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    market_cap = _market_cap_matrix(data)
    if market_cap is None:
        return None
    return _inverse_market_cap(market_cap)


def _joinquant_small_cap_low_price_factor(data: Any, lookback: int) -> Any:
    if not isinstance(data, pd.DataFrame):
        return None
    market_cap = _market_cap_series(data)
    if market_cap is None or "close" not in data.columns:
        return None
    price = pd.to_numeric(data["close"], errors="coerce")
    eligible = (price >= 2.0) & (price <= 20.0)
    if "turnover" in data.columns:
        turnover = pd.to_numeric(data["turnover"], errors="coerce")
        eligible &= turnover.rolling(20, min_periods=1).mean() >= 20000.0
    signal = _inverse_market_cap(market_cap)
    return signal.where(eligible)


def _joinquant_small_cap_low_price_factor_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    market_cap = _market_cap_matrix(data)
    if market_cap is None or "close" not in data.columns:
        return None
    price = field_matrix(data, "close").astype(float)
    eligible = (price >= 2.0) & (price <= 20.0)
    if "turnover" in data.columns:
        turnover = field_matrix(data, "turnover").astype(float)
        eligible &= turnover.rolling(20, min_periods=1).mean() >= 20000.0
    signal = _inverse_market_cap(market_cap)
    return signal.where(eligible)


def _ashare_small_cap_guarded_size_factor(data: Any, lookback: int) -> Any:
    if not isinstance(data, pd.DataFrame):
        return None
    market_cap = _market_cap_series(data)
    if market_cap is None:
        return None
    signal = _inverse_market_cap(market_cap)
    return signal.where(_guarded_small_cap_eligible_series(data, market_cap))


def _ashare_small_cap_guarded_size_factor_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    market_cap = _market_cap_matrix(data)
    if market_cap is None:
        return None
    signal = _inverse_market_cap(market_cap)
    return signal.where(_guarded_small_cap_eligible_panel(data, market_cap))


def _mean_reversion_close_to_ma(data: Any, lookback: int) -> Any:
    close = adjusted_price_series(data, "close")
    ma = close.rolling(lookback).mean()
    return (ma - close) / ma


def _mean_reversion_close_to_ma_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    close = adjusted_price_matrix(data, "close")
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


def _volatility_breakout_atr_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    high = adjusted_price_matrix(data, "high")
    low = adjusted_price_matrix(data, "low")
    close = adjusted_price_matrix(data, "close")
    tr_arrays = np.stack(
        [
            (high - low).to_numpy(dtype=float),
            (high - close.shift(1)).abs().to_numpy(dtype=float),
            (low - close.shift(1)).abs().to_numpy(dtype=float),
        ],
        axis=0,
    )
    tr = pd.DataFrame(np.nanmax(tr_arrays, axis=0), index=high.index, columns=high.columns)
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


def _worldquant_alpha_003_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    corr_window = max(2, int(lookback or 10))
    open_ = adjusted_price_matrix(data, "open")
    volume = field_matrix(data, "volume").astype(float)
    ranked_open = open_.rank(axis=1, pct=True)
    ranked_volume = volume.rank(axis=1, pct=True)
    return -ranked_open.rolling(corr_window, min_periods=corr_window).corr(ranked_volume)


def _worldquant_alpha_003_raw(data: Any, lookback: int) -> Any:
    corr_window = max(2, int(lookback or 10))
    open_ = adjusted_price_series(data, "open")
    if not hasattr(open_, "rolling"):
        return None
    volume = data["volume"] if isinstance(data, pd.DataFrame) else getattr(data, "volume", None)
    if volume is None:
        return None
    volume = pd.Series(volume, index=getattr(open_, "index", None), dtype=float)
    open_series = pd.Series(open_, index=getattr(open_, "index", None), dtype=float)
    return -open_series.rolling(corr_window, min_periods=corr_window).corr(volume)


def _worldquant_alpha_004_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    rank_window = max(2, int(lookback or 9))
    low = adjusted_price_matrix(data, "low")
    ranked_low = low.rank(axis=1, pct=True)
    return -_time_series_rank_last_pct(ranked_low, rank_window)


def _worldquant_alpha_004_raw(data: Any, lookback: int) -> Any:
    rank_window = max(2, int(lookback or 9))
    low = adjusted_price_series(data, "low")
    if not hasattr(low, "rolling"):
        return None
    low_series = pd.Series(low, index=getattr(low, "index", None), dtype=float)
    return -_time_series_rank_last_pct(low_series, rank_window)


def _worldquant_alpha_006_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    corr_window = max(2, int(lookback or 10))
    open_ = adjusted_price_matrix(data, "open")
    volume = field_matrix(data, "volume").astype(float)
    return -open_.rolling(corr_window, min_periods=corr_window).corr(volume)


def _worldquant_alpha_006_raw(data: Any, lookback: int) -> Any:
    corr_window = max(2, int(lookback or 10))
    open_ = adjusted_price_series(data, "open")
    if not hasattr(open_, "rolling"):
        return None
    volume = data["volume"] if isinstance(data, pd.DataFrame) else getattr(data, "volume", None)
    if volume is None:
        return None
    volume = pd.Series(volume, index=getattr(open_, "index", None), dtype=float)
    open_series = pd.Series(open_, index=getattr(open_, "index", None), dtype=float)
    return -open_series.rolling(corr_window, min_periods=corr_window).corr(volume)


def _worldquant_alpha_010_panel(data: pd.DataFrame, lookback: int) -> pd.DataFrame:
    delta_window = max(2, int(lookback or 4))
    close = adjusted_price_matrix(data, "close")
    delta = close.diff(1)
    ts_min = delta.rolling(delta_window, min_periods=delta_window).min()
    ts_max = delta.rolling(delta_window, min_periods=delta_window).max()
    raw = delta.where((ts_min > 0) | (ts_max < 0), -delta)
    return raw.rank(axis=1, pct=True)


def _worldquant_alpha_010_raw(data: Any, lookback: int) -> Any:
    delta_window = max(2, int(lookback or 4))
    close = adjusted_price_series(data, "close")
    if not hasattr(close, "rolling"):
        return None
    close_series = pd.Series(close, index=getattr(close, "index", None), dtype=float)
    delta = close_series.diff(1)
    ts_min = delta.rolling(delta_window, min_periods=delta_window).min()
    ts_max = delta.rolling(delta_window, min_periods=delta_window).max()
    return delta.where((ts_min > 0) | (ts_max < 0), -delta)


def _time_series_rank_last_pct(values: pd.Series | pd.DataFrame, lookback: int) -> pd.Series | pd.DataFrame:
    current = values
    less = None
    equal = None
    valid = None
    for offset in range(max(1, int(lookback))):
        shifted = values.shift(offset)
        valid_mask = shifted.notna() & current.notna()
        less_part = (shifted < current).where(valid_mask, False).astype(float)
        equal_part = (shifted == current).where(valid_mask, False).astype(float)
        valid_part = valid_mask.astype(float)
        less = less_part if less is None else less + less_part
        equal = equal_part if equal is None else equal + equal_part
        valid = valid_part if valid is None else valid + valid_part
    rank = less + (equal + 1.0) / 2.0
    ranked = rank / valid.where(valid != 0)
    return ranked.where(valid >= max(1, int(lookback)))


def _time_series_zscore(values: pd.Series, lookback: int) -> pd.Series:
    window = max(2, int(lookback))
    mean = values.rolling(window, min_periods=window).mean()
    std = values.rolling(window, min_periods=window).std()
    return (values - mean) / std.where(std > 0.0)


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


def _turnover_series(data: Any, close: Any) -> pd.Series:
    if isinstance(data, pd.DataFrame) and "turnover" in data.columns:
        return pd.Series(data["turnover"], index=getattr(close, "index", None), dtype=float)
    if isinstance(data, pd.DataFrame) and "volume" in data.columns:
        volume = pd.Series(data["volume"], index=getattr(close, "index", None), dtype=float)
        return volume * pd.Series(close, index=getattr(close, "index", None), dtype=float)
    if isinstance(data, dict):
        turnover = data.get("turnover")
        if turnover is not None:
            return pd.Series([float(turnover)])
        return pd.Series([float(data.get("volume", 0.0) or 0.0) * float(data.get("close", 0.0) or 0.0)])
    turnover = getattr(data, "turnover", None)
    if turnover is not None:
        return pd.Series(turnover, index=getattr(close, "index", None), dtype=float)
    volume = getattr(data, "volume", None)
    if volume is not None:
        return pd.Series(volume, index=getattr(close, "index", None), dtype=float) * pd.Series(close, index=getattr(close, "index", None), dtype=float)
    return pd.Series(0.0, index=getattr(close, "index", None), dtype=float)


def _turnover_matrix(data: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    if "turnover" in data.columns:
        return field_matrix(data, "turnover").astype(float)
    if "volume" in data.columns:
        return field_matrix(data, "volume").astype(float) * close
    return pd.DataFrame(0.0, index=close.index, columns=close.columns)


_MARKET_CAP_FIELDS = (
    "total_mv",
    "circ_mv",
    "market_cap",
    "total_market_cap",
    "float_market_cap",
    "circulating_market_cap",
)


def _market_cap_series(data: pd.DataFrame) -> pd.Series:
    for field in _MARKET_CAP_FIELDS:
        if field not in data.columns:
            continue
        series = pd.to_numeric(data[field], errors="coerce")
        if series.notna().any():
            return series
    return None


def _market_cap_matrix(data: pd.DataFrame) -> pd.DataFrame:
    for field in _MARKET_CAP_FIELDS:
        if field not in data.columns:
            continue
        matrix = field_matrix(data, field).astype(float)
        if matrix.notna().any().any():
            return matrix
    return None


def _inverse_market_cap(market_cap: pd.Series | pd.DataFrame) -> pd.Series | pd.DataFrame:
    numeric = market_cap.astype(float)
    return 1.0 / numeric.where(numeric > 0.0)


def _guarded_small_cap_eligible_series(data: pd.DataFrame, market_cap: pd.Series) -> pd.Series:
    eligible = market_cap.notna()
    if "close" in data.columns:
        eligible &= pd.to_numeric(data["close"], errors="coerce") >= 5.0
    close = adjusted_price_series(data, "close") if "close" in data.columns else None
    if close is not None and ("turnover" in data.columns or "volume" in data.columns):
        turnover = _turnover_series(data, close)
        eligible &= turnover.rolling(20, min_periods=1).mean() >= 20000.0
    if "is_st" in data.columns:
        eligible &= ~_truthy_series(data["is_st"])
    for field in ("tradable", "has_daily_bar", "is_listed"):
        if field in data.columns:
            eligible &= _truthy_series(data[field], default=True)
    if "list_status" in data.columns:
        eligible &= data["list_status"].astype(str).str.upper().eq("L")
    return eligible


def _guarded_small_cap_eligible_panel(data: pd.DataFrame, market_cap: pd.DataFrame) -> pd.DataFrame:
    eligible = market_cap.notna()
    if "close" in data.columns:
        price = field_matrix(data, "close").astype(float)
        eligible &= price >= 5.0
    if "turnover" in data.columns or "volume" in data.columns:
        close = adjusted_price_matrix(data, "close") if "close" in data.columns else market_cap * 0.0 + 1.0
        turnover = _turnover_matrix(data, close)
        eligible &= turnover.rolling(20, min_periods=1).mean() >= 20000.0
    if "is_st" in data.columns:
        eligible &= ~_truthy_matrix(field_matrix(data, "is_st"))
    for field in ("tradable", "has_daily_bar", "is_listed"):
        if field in data.columns:
            eligible &= _truthy_matrix(field_matrix(data, field), default=True)
    if "list_status" in data.columns:
        eligible &= field_matrix(data, "list_status").astype(str).apply(lambda col: col.str.upper().eq("L"))
    return eligible


def _truthy_series(values: Any, default: bool = False) -> pd.Series:
    series = pd.Series(values)
    if series.empty:
        return pd.Series(default, index=series.index, dtype=bool)
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(default).astype(bool)
    numeric = pd.to_numeric(series, errors="coerce")
    result = numeric.fillna(1 if default else 0).astype(float) != 0.0
    text_mask = numeric.isna()
    if text_mask.any():
        text = series.astype(str).str.lower()
        truthy = text.isin({"true", "t", "yes", "y", "1", "l", "listed"})
        falsy = text.isin({"false", "f", "no", "n", "0", "nan", "none", ""})
        parsed = truthy.where(~falsy, False)
        parsed = parsed.where(truthy | falsy, default)
        result = result.where(~text_mask, parsed)
    return result.astype(bool)


def _truthy_matrix(values: pd.DataFrame, default: bool = False) -> pd.DataFrame:
    return values.apply(lambda col: _truthy_series(col, default=default))


def _industry_group_field(data: pd.DataFrame) -> str:
    for field in ("l1_code", "l2_code", "industry_code", "l1_name", "industry_name"):
        if field in data.columns:
            values = data[field].dropna().astype(str)
            if not values.empty and values.str.strip().ne("").any():
                return field
    return ""


def _rolling_zscore(values: pd.DataFrame, window: int) -> pd.DataFrame:
    mean = values.rolling(window, min_periods=window).mean()
    std = values.rolling(window, min_periods=window).std().replace(0.0, np.nan)
    return (values - mean) / std


def _stack_matrix(values: pd.DataFrame, name: str) -> pd.DataFrame:
    try:
        return values.stack(future_stack=True).rename(name).reset_index()
    except TypeError:
        return values.stack(dropna=False).rename(name).reset_index()


SUPPORTED_FORMULAS = {
    "ashare_short_reversal_5d",
    "ashare_volume_exhaustion_reversal",
    "ashare_volume_dryup_pullback",
    "ashare_lottery_demand_avoidance",
    "ashare_low_volatility_defensive",
    "ashare_gap_down_reversal",
    "ashare_volatility_scaled_reversal",
    "ashare_liquidity_weighted_low_volatility",
    "ashare_low_volatility_momentum",
    "ashare_range_contraction_breakout",
    "ashare_gap_down_liquid_reversal",
    "ashare_turnover_stability_factor",
    "ashare_small_cap_guarded_size_factor",
    "ashare_price_volume_multifactor",
    "ashare_industry_prosperity_trend_crowding_rotation",
    "joinquant_small_cap_size_factor",
    "joinquant_small_cap_low_price_factor",
    "momentum_close_return",
    "mean_reversion_close_to_ma",
    "volatility_breakout_atr",
    "worldquant_alpha_001",
    "worldquant_alpha_002",
    "worldquant_alpha_003",
    "worldquant_alpha_004",
    "worldquant_alpha_006",
    "worldquant_alpha_010",
}


NON_POSITIVE_SELECTION_FORMULAS = frozenset({
    "worldquant_alpha_004",
})


def uses_positive_signal_filter(formula_key: str) -> bool:
    return str(formula_key) not in NON_POSITIVE_SELECTION_FORMULAS
