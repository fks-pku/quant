"""Pure execution-cost helpers shared by backtest, paper, and live paths."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class CostProtectionEstimate:
    reference_price: float
    limit_price: float
    cost_bps: float
    slippage_bps: float
    impact_bps: float


def bar_value(item: Any, *names: str) -> Any:
    for name in names:
        if isinstance(item, dict) and name in item:
            return item[name]
        getter = getattr(item, "get", None)
        if callable(getter):
            try:
                value = getter(name, None)
            except TypeError:
                value = None
            if value is not None:
                return value
        if hasattr(item, name):
            return getattr(item, name)
    return None


def bar_symbol(item: Any) -> Optional[str]:
    value = bar_value(item, "symbol", "ts_code", "code", "ticker")
    if value is None:
        return None
    text = str(value)
    return text if text else None


def bar_close_price(item: Any) -> Optional[float]:
    return safe_positive_number(bar_value(item, "close", "close_price", "last_price", "price"))


def safe_positive_number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def infer_market(symbol: str) -> str:
    text = str(symbol or "").upper()
    base = text.split(".", 1)[0]
    suffix = text.rsplit(".", 1)[-1] if "." in text else ""
    if suffix in {"SH", "SZ", "BJ"} or (base.isdigit() and len(base) == 6):
        return "CN"
    if base.isdigit() and len(base) == 5:
        return "HK"
    return "US"


def cost_model_enabled(model: Optional[Dict[str, Any]], market: str) -> bool:
    if not isinstance(model, dict) or not model.get("enabled"):
        return False
    markets = model.get("markets")
    if not markets:
        return True
    return market in {str(item) for item in markets}


def execution_adv_value(bar: Any, price: float) -> float:
    for key in ("adv20_value", "adv_value", "avg_turnover_20", "turnover20", "avg_turnover", "turnover"):
        value = safe_positive_number(bar_value(bar, key))
        if value is not None:
            return _normalize_turnover_value(value, bar, price)
    for key in ("adv20_volume", "adv_volume", "avg_volume_20", "volume20", "avg_daily_volume", "avg_volume"):
        value = safe_positive_number(bar_value(bar, key))
        if value is not None and price > 0:
            return value * price
    volume = safe_positive_number(bar_value(bar, "volume"))
    return float(volume * price) if volume is not None and price > 0 else 0.0


def _normalize_turnover_value(value: float, bar: Any, price: float) -> float:
    volume = safe_positive_number(bar_value(bar, "volume"))
    if value <= 0 or volume is None or volume <= 0 or price <= 0:
        return value
    implied = price * volume
    ratio = implied / value if value > 0 else 0.0
    if 5.0 <= ratio <= 20.0 or 500.0 <= ratio <= 2000.0:
        return value * 1000.0
    return value


def execution_adv_quantity(bar: Any, price: float) -> float:
    for key in ("adv20_volume", "adv_volume", "avg_volume_20", "volume20", "avg_daily_volume", "avg_volume"):
        value = safe_positive_number(bar_value(bar, key))
        if value is not None:
            return value
    adv_value = execution_adv_value(bar, price)
    if adv_value > 0 and price > 0:
        return adv_value / price
    return 0.0


def execution_bar_volume(bar: Any, price: float, market: str) -> float:
    volume = safe_positive_number(bar_value(bar, "volume"))
    volume = float(volume or 0.0)
    if market != "CN" or volume <= 0 or price <= 0:
        return volume
    turnover = safe_positive_number(bar_value(bar, "turnover"))
    if turnover is None or turnover <= 0:
        return volume
    ratio = price * volume / turnover
    if 5.0 <= ratio <= 20.0:
        return volume * 100.0
    return volume


def model_slippage_bps(price: float, base_bps: float, market: str, model: Optional[Dict[str, Any]]) -> float:
    if not cost_model_enabled(model, market):
        return float(base_bps or 0.0)
    effective_bps = max(float(base_bps or 0.0), float(model.get("min_slippage_bps", 0) or 0))
    tick_size = float(model.get("tick_size", 0) or 0)
    half_spread_ticks = float(model.get("half_spread_ticks", 0.5) or 0.0)
    if price > 0 and tick_size > 0 and half_spread_ticks > 0:
        effective_bps = max(effective_bps, half_spread_ticks * tick_size / price * 10000)
    return effective_bps


def model_participation_limit(default_limit: float, market: str, model: Optional[Dict[str, Any]]) -> float:
    if not cost_model_enabled(model, market):
        return default_limit
    limit = safe_positive_number(model.get("max_participation_rate"))
    return min(default_limit, limit) if limit is not None else default_limit


def execution_impact_bps(
    quantity: float,
    fill_price: float,
    bar: Any,
    market: str,
    model: Optional[Dict[str, Any]],
    fallback_daily_volume: float,
    fallback_impact_factor: float,
) -> float:
    if not cost_model_enabled(model, market):
        if fallback_impact_factor <= 0 or fallback_daily_volume <= 0 or quantity <= 0:
            return 0.0
        participation = quantity / fallback_daily_volume
        if participation <= 0:
            return 0.0
        return fallback_impact_factor * (participation ** 0.5) * 10000
    adv_value = execution_adv_value(bar, fill_price)
    if adv_value <= 0 or fill_price <= 0 or quantity <= 0:
        return 0.0
    volatility = (
        safe_positive_number(bar_value(bar, "volatility20"))
        or safe_positive_number(bar_value(bar, "volatility_20d"))
        or safe_positive_number(bar_value(bar, "daily_volatility"))
        or safe_positive_number(model.get("volatility_fallback"))
        or 0.0
    )
    coefficient = float(model.get("impact_coefficient", 0.0) or 0.0)
    if volatility <= 0 or coefficient <= 0:
        return 0.0
    participation = quantity * fill_price / adv_value
    if participation <= 0:
        return 0.0
    return coefficient * volatility * (participation ** 0.5) * 10000


def estimate_cost_protection_limit(
    *,
    symbol: str,
    side: str,
    quantity: float,
    reference_price: float,
    market: Optional[str],
    signal_bar: Any,
    base_slippage_bps: float,
    execution_cost_model: Optional[Dict[str, Any]],
    fallback_max_cost_bps: Optional[float] = None,
    fallback_daily_volume: float = 0.0,
    fallback_impact_factor: float = 0.0,
) -> CostProtectionEstimate:
    market_name = market or infer_market(symbol)
    model_enabled = cost_model_enabled(execution_cost_model, market_name)
    if model_enabled:
        slippage = model_slippage_bps(reference_price, base_slippage_bps, market_name, execution_cost_model)
        impact = execution_impact_bps(
            quantity,
            reference_price,
            signal_bar or {},
            market_name,
            execution_cost_model,
            fallback_daily_volume,
            fallback_impact_factor,
        )
        cost_bps = slippage + impact
    else:
        cost_bps = float(fallback_max_cost_bps if fallback_max_cost_bps is not None else base_slippage_bps or 0.0)
        slippage = cost_bps
        impact = 0.0
    side_text = side.upper()
    if side_text == "BUY":
        limit_price = reference_price * (1 + cost_bps / 10000.0)
    elif side_text == "SELL":
        limit_price = reference_price * (1 - cost_bps / 10000.0)
    else:
        raise ValueError(f"Unsupported order side: {side}")
    return CostProtectionEstimate(
        reference_price=float(reference_price),
        limit_price=float(limit_price),
        cost_bps=float(cost_bps),
        slippage_bps=float(slippage),
        impact_bps=float(impact),
    )


def has_historical_cost_model(model: Optional[Dict[str, Any]], symbol: str, market: Optional[str] = None) -> bool:
    return cost_model_enabled(model, market or infer_market(symbol))
