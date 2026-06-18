"""Pure single-order execution simulator shared by backtest and paper trading."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Dict, Optional

from quant.domain.exceptions import OrderRejectedError, OrderRejectionReason
from quant.runtime.execution_commission import VOLUME_PARTICIPATION_LIMIT, calculate_commission
from quant.runtime.execution_cost import (
    bar_value,
    execution_adv_quantity,
    execution_adv_value,
    execution_bar_volume,
    execution_impact_bps,
    model_participation_limit,
    model_slippage_bps,
    safe_positive_number,
)
from quant.runtime.execution_market_rules import (
    fifo_lot_slices,
    get_lot_size,
    get_market,
    get_price_limit_direction,
    get_settled_quantity,
)


EXECUTION_TIMING_NEXT_OPEN = "NEXT_OPEN"
EXECUTION_TIMING_SAME_CLOSE = "SAME_CLOSE"
DEFAULT_RISK_PRICE_DEVIATION_LIMIT = 0.15


@dataclass(frozen=True)
class RuntimeOrder:
    symbol: str
    quantity: float
    side: str
    order_type: str = "MARKET"
    price: Optional[float] = None
    strategy: Optional[str] = None
    signal_date: Optional[datetime] = None
    risk_check_price: float = 0.0
    execution_timing: str = EXECUTION_TIMING_NEXT_OPEN
    execution_cost_reference_price: Optional[float] = None
    execution_cost_bps: Optional[float] = None
    execution_slippage_bps: Optional[float] = None
    execution_impact_bps: Optional[float] = None

    def __post_init__(self) -> None:
        side = str(self.side or "").upper()
        if side not in {"BUY", "SELL"}:
            raise OrderRejectedError(OrderRejectionReason.UNKNOWN_SIDE, self.symbol, f"side={self.side!r}")
        if not isinstance(self.quantity, (int, float)) or self.quantity <= 0:
            raise OrderRejectedError(
                OrderRejectionReason.INVALID_QUANTITY,
                self.symbol,
                f"quantity must be > 0, got {self.quantity!r}",
            )
        timing = str(self.execution_timing or EXECUTION_TIMING_NEXT_OPEN).upper()
        if timing not in {EXECUTION_TIMING_NEXT_OPEN, EXECUTION_TIMING_SAME_CLOSE}:
            raise OrderRejectedError(
                OrderRejectionReason.PRICE_INVALID,
                self.symbol,
                f"unsupported execution_timing={self.execution_timing!r}",
            )
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "order_type", str(self.order_type or "MARKET").upper())
        object.__setattr__(self, "execution_timing", timing)


@dataclass(frozen=True)
class ExecutionSimulation:
    symbol: str
    side: str
    quantity: float
    fill_price: float
    fill_time: datetime
    market: str
    commission: float
    cost_breakdown: Dict[str, float]
    observation: Dict[str, Any]
    lot_adjusted: bool = False
    volume_limited_count: int = 0
    truncated_sell: bool = False
    raw_execution_price: float = 0.0
    cost_protection_limit: Optional[float] = None


def simulate_order_execution(
    order: RuntimeOrder,
    portfolio: Any,
    symbol: str,
    bar: Any,
    *,
    lot_sizes: Optional[Dict[str, int]] = None,
    ipo_dates: Optional[Dict[str, date]] = None,
    slippage_bps: float = 0.0,
    commission_config: Any = None,
    prev_bar: Optional[Any] = None,
    risk_price_deviation_limit: float = DEFAULT_RISK_PRICE_DEVIATION_LIMIT,
    market_impact_factor: float = 0.0,
    execution_cost_model: Optional[Dict[str, Any]] = None,
    ignore_settlement: bool = False,
) -> ExecutionSimulation:
    if bar is None:
        raise OrderRejectedError(OrderRejectionReason.BAR_UNAVAILABLE, symbol)
    price_field = execution_price_field(order)
    raw_execution_price = _positive_finite_float(bar_value(bar, price_field), symbol, price_field)

    fill_ts = _as_datetime(bar_value(bar, "timestamp"), datetime.now())
    market = get_market(symbol)

    if market == "CN":
        prev_close = _float_or_zero(bar_value(prev_bar, "close")) if prev_bar is not None else 0.0
        fill_date_val = fill_ts.date()
        limit_direction = get_price_limit_direction(
            symbol,
            raw_execution_price,
            prev_close,
            fill_date_val,
            ipo_dates,
            bar_value(bar, "up_limit"),
            bar_value(bar, "down_limit"),
            bar_value(bar, "is_st"),
        )
        if (limit_direction == "UP" and order.side == "BUY") or (
            limit_direction == "DOWN" and order.side == "SELL"
        ):
            raise OrderRejectedError(OrderRejectionReason.PRICE_AT_LIMIT, symbol)

    cost_protection_limit = _execution_day_cost_limit_price(order, raw_execution_price)
    execution_order = replace(order, price=cost_protection_limit) if cost_protection_limit is not None else order
    protected_slippage_bps = _optional_non_negative_float(
        getattr(order, "execution_slippage_bps", None),
        symbol,
        "execution_slippage_bps",
    )
    effective_slippage_bps = (
        protected_slippage_bps
        if protected_slippage_bps is not None
        else model_slippage_bps(raw_execution_price, slippage_bps, market, execution_cost_model)
    )
    fill_price = resolve_base_fill_price(
        execution_order,
        raw_execution_price,
        execution_order.order_type,
        effective_slippage_bps,
        price_field,
    )
    fill_price = _positive_finite_float(fill_price, symbol, "fill_price")
    is_limit_order = execution_order.order_type == "LIMIT"

    risk_price = float(getattr(order, "risk_check_price", 0.0) or 0.0)
    if (
        execution_order.order_type != "LIMIT"
        and risk_price > 0
        and abs(fill_price - risk_price) / risk_price > risk_price_deviation_limit
    ):
        raise OrderRejectedError(OrderRejectionReason.PRICE_DEVIATION, symbol)

    lot_size = get_lot_size(symbol, lot_sizes or {})
    quantity, lot_adjusted = apply_lot_rounding(order.quantity, lot_size, order.side, market)
    if quantity is None:
        raise OrderRejectedError(OrderRejectionReason.LOT_IMPOSSIBLE, symbol)

    volume_limited_count = 0
    bar_volume = execution_bar_volume(bar, fill_price, market)
    participation_limit = model_participation_limit(VOLUME_PARTICIPATION_LIMIT, market, execution_cost_model)
    max_liquidity_qty = _liquidity_quantity_cap(bar, fill_price, bar_volume, participation_limit)
    if max_liquidity_qty is not None and quantity > max_liquidity_qty:
        max_qty = max(1, int(max_liquidity_qty))
        if market == "HK" or (market == "CN" and order.side == "BUY"):
            max_qty = (max_qty // lot_size) * lot_size
        if max_qty <= 0:
            raise OrderRejectedError(OrderRejectionReason.VOLUME_ZERO, symbol)
        quantity = float(max_qty)
        volume_limited_count += 1

    base_fill_price = fill_price
    protected_impact_bps = _optional_non_negative_float(
        getattr(order, "execution_impact_bps", None),
        symbol,
        "execution_impact_bps",
    )
    impact_bps = protected_impact_bps if is_limit_order and protected_impact_bps is not None else 0.0
    if not is_limit_order:
        impact_bps = (
            protected_impact_bps
            if protected_impact_bps is not None
            else execution_impact_bps(
                quantity,
                base_fill_price,
                bar,
                market,
                execution_cost_model,
                bar_volume,
                market_impact_factor,
            )
        )
        fill_price = apply_market_impact(base_fill_price, order.side, impact_bps)
        fill_price = _positive_finite_float(fill_price, symbol, "fill_price")
        enforce_limit_after_impact(execution_order, fill_price)

    adv_value = execution_adv_value(bar, fill_price)
    if order.side == "BUY":
        final_adv_qty = _final_buy_adv_quantity_cap(
            quantity,
            fill_price,
            adv_value,
            participation_limit,
            market,
            lot_size,
        )
        if final_adv_qty is None:
            raise OrderRejectedError(OrderRejectionReason.VOLUME_ZERO, symbol)
        if final_adv_qty < quantity:
            quantity = final_adv_qty
            volume_limited_count += 1
            if not is_limit_order:
                impact_bps = (
                    protected_impact_bps
                    if protected_impact_bps is not None
                    else execution_impact_bps(
                        quantity,
                        base_fill_price,
                        bar,
                        market,
                        execution_cost_model,
                        bar_volume,
                        market_impact_factor,
                    )
                )
                fill_price = apply_market_impact(base_fill_price, order.side, impact_bps)
                fill_price = _positive_finite_float(fill_price, symbol, "fill_price")
                enforce_limit_after_impact(execution_order, fill_price)
            adv_value = execution_adv_value(bar, fill_price)

    quantity, truncated_sell = _portfolio_quantity_gate(
        execution_order,
        portfolio,
        symbol,
        quantity,
        fill_ts.date(),
        market,
        ignore_settlement,
    )
    cost_breakdown = calculate_commission(
        symbol,
        fill_price,
        quantity,
        order.side,
        commission_config,
        fill_ts.date(),
    )
    commission = sum(cost_breakdown.values())
    _portfolio_cash_gate(execution_order, portfolio, symbol, fill_price, quantity, commission)

    adv_quantity = execution_adv_quantity(bar, fill_price)
    observation = {
        "symbol": symbol,
        "side": order.side,
        "date": fill_ts.date().isoformat(),
        "quantity": float(quantity),
        "fill_price": float(fill_price),
        "notional": float(abs(quantity * fill_price)),
        "bar_volume": float(bar_volume or 0.0),
        "adv_value": float(adv_value or 0.0),
        "adv_volume": float(adv_quantity or 0.0),
        "volume_participation": float(abs(quantity) / bar_volume) if bar_volume and bar_volume > 0 else 0.0,
        "adv_participation": float(abs(quantity * fill_price) / adv_value) if adv_value and adv_value > 0 else 0.0,
        "adv_volume_participation": float(abs(quantity) / adv_quantity) if adv_quantity and adv_quantity > 0 else 0.0,
        "participation_limit": float(participation_limit or 0.0),
        "impact_bps": float(impact_bps or 0.0),
        "slippage_bps": float(effective_slippage_bps or 0.0),
        "execution_timing": order.execution_timing,
        "execution_price_field": price_field,
    }
    if cost_protection_limit is not None:
        observation.update({
            "cost_protection_bps": float(order.execution_cost_bps or 0.0),
            "cost_protection_limit": float(cost_protection_limit),
            "cost_protection_reference_price": float(raw_execution_price),
            "cost_reference_price": float(order.execution_cost_reference_price or 0.0),
        })

    return ExecutionSimulation(
        symbol=symbol,
        side=order.side,
        quantity=float(quantity),
        fill_price=float(fill_price),
        fill_time=fill_ts,
        market=market,
        commission=float(commission),
        cost_breakdown=cost_breakdown,
        observation=observation,
        lot_adjusted=lot_adjusted,
        volume_limited_count=volume_limited_count,
        truncated_sell=truncated_sell,
        raw_execution_price=float(raw_execution_price),
        cost_protection_limit=cost_protection_limit,
    )


def execution_price_field(order: RuntimeOrder) -> str:
    return "close" if order.execution_timing == EXECUTION_TIMING_SAME_CLOSE else "open"


def apply_slippage(price: float, side: str, bps: float) -> float:
    if price <= 0:
        return price
    slippage = price * (bps / 10000)
    if side == "BUY":
        return price + slippage
    return price - slippage


def resolve_base_fill_price(
    order: RuntimeOrder,
    raw_price: float,
    order_type: str,
    slippage_bps: float,
    price_field: str = "open",
) -> float:
    if order_type != "LIMIT":
        return apply_slippage(raw_price, order.side, slippage_bps)
    limit_price = _positive_finite_float(order.price, order.symbol, "limit price")
    if order.side == "BUY" and raw_price > limit_price:
        raise OrderRejectedError(
            OrderRejectionReason.LIMIT_NOT_MARKETABLE,
            order.symbol,
            f"{price_field}={raw_price} > limit={limit_price}",
        )
    if order.side == "SELL" and raw_price < limit_price:
        raise OrderRejectedError(
            OrderRejectionReason.LIMIT_NOT_MARKETABLE,
            order.symbol,
            f"{price_field}={raw_price} < limit={limit_price}",
        )
    return float(limit_price)


def enforce_limit_after_impact(order: RuntimeOrder, fill_price: float) -> None:
    if order.order_type != "LIMIT":
        return
    limit_price = order.price
    if order.side == "BUY" and fill_price > limit_price:
        raise OrderRejectedError(
            OrderRejectionReason.LIMIT_NOT_MARKETABLE,
            order.symbol,
            f"impacted fill={fill_price} > limit={limit_price}",
        )
    if order.side == "SELL" and fill_price < limit_price:
        raise OrderRejectedError(
            OrderRejectionReason.LIMIT_NOT_MARKETABLE,
            order.symbol,
            f"impacted fill={fill_price} < limit={limit_price}",
        )


def apply_lot_rounding(quantity: float, lot_size: int, side: str, market: str) -> tuple[Optional[float], bool]:
    if market not in ("HK", "CN"):
        return float(quantity), False
    if side == "BUY":
        lot_qty = (int(quantity) // lot_size) * lot_size
        if lot_qty < lot_size:
            return None, False
        if lot_qty != int(quantity):
            return float(lot_qty), True
        return float(lot_qty), False
    if market == "HK":
        lot_qty = (int(quantity) // lot_size) * lot_size
        if lot_qty < lot_size:
            return None, False
        if lot_qty != int(quantity):
            return float(lot_qty), True
        return float(lot_qty), False
    if market == "CN":
        if int(quantity) < 1:
            return None, False
        return float(quantity), False
    return float(quantity), False


def apply_market_impact(fill_price: float, side: str, impact_bps: float) -> float:
    if impact_bps <= 0:
        return fill_price
    adjustment = fill_price * (impact_bps / 10000)
    if side == "BUY":
        return fill_price + adjustment
    return fill_price - adjustment


def _positive_finite_float(value: Any, symbol: str, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise OrderRejectedError(OrderRejectionReason.PRICE_INVALID, symbol, f"{label}={value!r}")
    if not math.isfinite(number) or number <= 0:
        raise OrderRejectedError(OrderRejectionReason.PRICE_INVALID, symbol, f"{label}={value!r}")
    return number


def _optional_non_negative_float(value: Any, symbol: str, label: str) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise OrderRejectedError(OrderRejectionReason.PRICE_INVALID, symbol, f"{label}={value!r}")
    if not math.isfinite(number) or number < 0:
        raise OrderRejectedError(OrderRejectionReason.PRICE_INVALID, symbol, f"{label}={value!r}")
    return number


def _execution_day_cost_limit_price(order: RuntimeOrder, reference_price: float) -> Optional[float]:
    if order.order_type != "LIMIT" or order.price is not None:
        return None
    cost_bps = _optional_non_negative_float(
        getattr(order, "execution_cost_bps", None),
        order.symbol,
        "execution_cost_bps",
    )
    if cost_bps is None:
        return None
    if order.side == "BUY":
        return float(reference_price) * (1 + cost_bps / 10000.0)
    if order.side == "SELL":
        return float(reference_price) * (1 - cost_bps / 10000.0)
    raise OrderRejectedError(OrderRejectionReason.UNKNOWN_SIDE, order.symbol, f"side={order.side!r}")


def _liquidity_quantity_cap(
    bar: Any,
    fill_price: float,
    bar_volume: float,
    participation_limit: float,
) -> Optional[float]:
    candidates = []
    if bar_volume > 0:
        candidates.append(bar_volume * participation_limit)
    adv_quantity = execution_adv_quantity(bar, fill_price)
    if adv_quantity > 0:
        candidates.append(adv_quantity * participation_limit)
    adv_value = execution_adv_value(bar, fill_price)
    if adv_value > 0 and fill_price > 0:
        candidates.append(adv_value * participation_limit / fill_price)
    if not candidates:
        return None
    return min(candidates)


def _final_buy_adv_quantity_cap(
    quantity: float,
    fill_price: float,
    adv_value: float,
    participation_limit: float,
    market: str,
    lot_size: int,
) -> Optional[float]:
    if quantity <= 0 or fill_price <= 0 or adv_value <= 0 or participation_limit <= 0:
        return quantity
    if quantity * fill_price <= adv_value * participation_limit:
        return quantity
    max_qty = int((adv_value * participation_limit) / fill_price)
    if market == "HK" or market == "CN":
        max_qty = (max_qty // lot_size) * lot_size
    if max_qty <= 0:
        return None
    return float(max_qty)


def _portfolio_quantity_gate(
    order: RuntimeOrder,
    portfolio: Any,
    symbol: str,
    quantity: float,
    fill_date: date,
    market: str,
    ignore_settlement: bool,
) -> tuple[float, bool]:
    if order.side != "SELL" or portfolio is None:
        return quantity, False
    pos = _portfolio_position(portfolio, symbol)
    if not pos or getattr(pos, "quantity", 0) <= 0:
        raise OrderRejectedError(OrderRejectionReason.NO_POSITION, symbol, "no position to sell (short not supported)")
    settled_qty = getattr(pos, "quantity", 0) if ignore_settlement else get_settled_quantity(symbol, pos, fill_date, market)
    if settled_qty <= 0:
        raise OrderRejectedError(OrderRejectionReason.T1_SETTLEMENT, symbol)
    sell_qty = min(quantity, settled_qty)
    return float(sell_qty), sell_qty < quantity


def _portfolio_cash_gate(
    order: RuntimeOrder,
    portfolio: Any,
    symbol: str,
    fill_price: float,
    quantity: float,
    commission: float,
) -> None:
    if order.side != "BUY" or portfolio is None:
        return
    total_cost = fill_price * quantity + commission
    if hasattr(portfolio, "can_afford"):
        if not portfolio.can_afford(total_cost):
            raise OrderRejectedError(OrderRejectionReason.INSUFFICIENT_CASH, symbol)
    elif hasattr(portfolio, "cash") and float(getattr(portfolio, "cash") or 0.0) < total_cost:
        raise OrderRejectedError(OrderRejectionReason.INSUFFICIENT_CASH, symbol)


def _portfolio_position(portfolio: Any, symbol: str) -> Any:
    if hasattr(portfolio, "get_position"):
        return portfolio.get_position(symbol)
    positions = getattr(portfolio, "positions", None)
    if isinstance(positions, dict):
        return positions.get(symbol)
    return None


def _as_datetime(value: Any, default: datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    if hasattr(value, "to_pydatetime"):
        try:
            return value.to_pydatetime()
        except Exception:
            pass
    if value is not None:
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            pass
    return default


def _float_or_zero(value: Any) -> float:
    number = safe_positive_number(value)
    return float(number) if number is not None else 0.0


__all__ = [
    "DEFAULT_RISK_PRICE_DEVIATION_LIMIT",
    "EXECUTION_TIMING_NEXT_OPEN",
    "EXECUTION_TIMING_SAME_CLOSE",
    "ExecutionSimulation",
    "RuntimeOrder",
    "apply_lot_rounding",
    "apply_market_impact",
    "apply_slippage",
    "enforce_limit_after_impact",
    "execution_price_field",
    "fifo_lot_slices",
    "resolve_base_fill_price",
    "simulate_order_execution",
]
