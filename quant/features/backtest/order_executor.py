"""Order execution pipeline — slippage, lot rounding, volume limit, commission, trade generation."""

from __future__ import annotations
import logging
import math
from dataclasses import replace
from datetime import date, datetime
from typing import TYPE_CHECKING, Dict, List, Any, Optional, Tuple

import pandas as pd

if TYPE_CHECKING:
    from quant.features.backtest.schemas import BacktestBar, DeferredOrder

from quant.domain.models.trade import Trade
from quant.features.backtest.entities import BacktestDiagnostics
from quant.features.backtest.exceptions import OrderRejectedError, OrderRejectionReason
from quant.features.backtest.commission import calculate_commission, VOLUME_PARTICIPATION_LIMIT
from quant.features.backtest.market_rules import (
    get_market,
    get_lot_size,
    get_price_limit_direction,
    get_settled_quantity,
    fifo_lot_slices,
)
from quant.features.backtest.schemas import EXECUTION_TIMING_SAME_CLOSE
from quant.runtime.execution_cost import (
    cost_model_enabled as _shared_cost_model_enabled,
    execution_adv_quantity as _shared_execution_adv_quantity,
    execution_adv_value as _shared_execution_adv_value,
    execution_bar_volume as _shared_execution_bar_volume,
    execution_impact_bps,
    model_participation_limit as _shared_model_participation_limit,
    model_slippage_bps as _shared_model_slippage_bps,
    safe_positive_number,
)
from quant.runtime.execution_simulator import RuntimeOrder, simulate_order_execution

logger = logging.getLogger(__name__)

DEFAULT_RISK_PRICE_DEVIATION_LIMIT = 0.15


def _positive_finite_float(value: Any, symbol: str, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise OrderRejectedError(OrderRejectionReason.PRICE_INVALID, symbol,
                                 f"{label}={value!r}")
    if not math.isfinite(number) or number <= 0:
        raise OrderRejectedError(OrderRejectionReason.PRICE_INVALID, symbol,
                                 f"{label}={value!r}")
    return number


def _non_negative_volume(value: Any) -> float:
    try:
        volume = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(volume) or volume <= 0:
        return 0.0
    return volume


def compute_market_impact(quantity: float, daily_volume: float, impact_factor: float) -> float:
    """Square-root market impact model (bps).

    impact_bps = impact_factor * sqrt(quantity / daily_volume) * 10000

    Args:
        quantity: order quantity in shares
        daily_volume: daily bar volume in shares
        impact_factor: configurable impact coefficient (0 = disabled, 0.001-0.01 typical)

    Returns impact in basis points.
    """
    if impact_factor <= 0 or daily_volume <= 0 or quantity <= 0:
        return 0.0
    participation = quantity / daily_volume
    if participation <= 0:
        return 0.0
    return impact_factor * (participation ** 0.5) * 10000


def _safe_number(value: Any) -> Optional[float]:
    return safe_positive_number(value)


def _cost_model_enabled(model: Optional[Dict[str, Any]], market: str) -> bool:
    return _shared_cost_model_enabled(model, market)


def _execution_adv_value(bar: "BacktestBar", price: float) -> float:
    return _shared_execution_adv_value(bar, price)


def _normalize_turnover_value(value: float, bar: "BacktestBar", price: float) -> float:
    volume = _safe_number(bar.get("volume"))
    if value <= 0 or volume is None or volume <= 0 or price <= 0:
        return value
    implied = price * volume
    ratio = implied / value if value > 0 else 0.0
    if 5.0 <= ratio <= 20.0 or 500.0 <= ratio <= 2000.0:
        return value * 1000.0
    return value


def _execution_bar_volume(bar: "BacktestBar", price: float, market: str) -> float:
    return _shared_execution_bar_volume(bar, price, market)


def _execution_adv_quantity(bar: "BacktestBar", price: float) -> float:
    return _shared_execution_adv_quantity(bar, price)


def _model_slippage_bps(price: float, base_bps: float, market: str, model: Optional[Dict[str, Any]]) -> float:
    return _shared_model_slippage_bps(price, base_bps, market, model)


def _model_participation_limit(default_limit: float, market: str, model: Optional[Dict[str, Any]]) -> float:
    return _shared_model_participation_limit(default_limit, market, model)


def _liquidity_quantity_cap(
    bar: "BacktestBar",
    fill_price: float,
    bar_volume: float,
    participation_limit: float,
) -> Optional[float]:
    candidates = []
    if bar_volume > 0:
        candidates.append(bar_volume * participation_limit)
    adv_quantity = _execution_adv_quantity(bar, fill_price)
    if adv_quantity > 0:
        candidates.append(adv_quantity * participation_limit)
    adv_value = _execution_adv_value(bar, fill_price)
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


def compute_execution_impact(
    quantity: float,
    fill_price: float,
    bar: "BacktestBar",
    market: str,
    model: Optional[Dict[str, Any]],
    fallback_daily_volume: float,
    fallback_impact_factor: float,
) -> float:
    return execution_impact_bps(
        quantity,
        fill_price,
        bar,
        market,
        model,
        fallback_daily_volume,
        fallback_impact_factor,
    )


def apply_market_impact(fill_price: float, side: str, impact_bps: float) -> float:
    """Apply market impact to fill price. BUY increases price, SELL decreases."""
    if impact_bps <= 0:
        return fill_price
    adjustment = fill_price * (impact_bps / 10000)
    if side == 'BUY':
        return fill_price + adjustment
    return fill_price - adjustment


def execution_price_field(order: "DeferredOrder") -> str:
    return "close" if order.execution_timing == EXECUTION_TIMING_SAME_CLOSE else "open"


def _optional_non_negative_float(value: Any, symbol: str, label: str) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise OrderRejectedError(OrderRejectionReason.PRICE_INVALID, symbol,
                                 f"{label}={value!r}")
    if not math.isfinite(number) or number < 0:
        raise OrderRejectedError(OrderRejectionReason.PRICE_INVALID, symbol,
                                 f"{label}={value!r}")
    return number


def _execution_day_cost_limit_price(
    order: "DeferredOrder",
    reference_price: float,
    order_type: str,
) -> Optional[float]:
    if order_type != "LIMIT" or order.price is not None:
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
    raise OrderRejectedError(OrderRejectionReason.UNKNOWN_SIDE, order.symbol,
                             f"side={order.side!r}")


def execute_order(
    order: "DeferredOrder",
    portfolio: Any,
    symbol: str,
    bar: "BacktestBar",
    entry_times: Dict[str, datetime],
    entry_prices: Dict[str, float],
    diag: BacktestDiagnostics,
    lot_sizes: Dict[str, int],
    ipo_dates: Optional[Dict[str, date]],
    slippage_bps: float,
    commission_config: Any,
    prev_bar: Optional["BacktestBar"] = None,
    risk_price_deviation_limit: float = DEFAULT_RISK_PRICE_DEVIATION_LIMIT,
    market_impact_factor: float = 0.0,
    execution_cost_model: Optional[Dict[str, Any]] = None,
    ignore_settlement: bool = False,
) -> List[Trade]:
    runtime_order = RuntimeOrder(
        symbol=order.symbol,
        quantity=order.quantity,
        side=order.side,
        order_type=order.order_type,
        price=order.price,
        strategy=order.strategy,
        signal_date=order.signal_date,
        risk_check_price=order.risk_check_price,
        execution_timing=order.execution_timing,
        execution_cost_reference_price=order.execution_cost_reference_price,
        execution_cost_bps=order.execution_cost_bps,
        execution_slippage_bps=order.execution_slippage_bps,
        execution_impact_bps=order.execution_impact_bps,
    )
    try:
        simulation = simulate_order_execution(
            runtime_order,
            portfolio,
            symbol,
            bar,
            lot_sizes=lot_sizes,
            ipo_dates=ipo_dates,
            slippage_bps=slippage_bps,
            commission_config=commission_config,
            prev_bar=prev_bar,
            risk_price_deviation_limit=risk_price_deviation_limit,
            market_impact_factor=market_impact_factor,
            execution_cost_model=execution_cost_model,
            ignore_settlement=ignore_settlement,
        )
    except OrderRejectedError as exc:
        if exc.reason == OrderRejectionReason.PRICE_AT_LIMIT:
            diag.limit_rejected_orders += 1
        elif exc.reason == OrderRejectionReason.T1_SETTLEMENT and get_market(symbol) == "CN":
            diag.t1_rejected_sells += 1
        raise

    if simulation.lot_adjusted:
        diag.lot_adjusted_trades += 1
    diag.volume_limited_trades += simulation.volume_limited_count
    if simulation.truncated_sell:
        diag.truncated_sells += 1
        logger.warning("SELL %s truncated: requested %d, settled %d", symbol, order.quantity, simulation.quantity)

    if order.side == 'BUY':
        trades = _execute_buy(
            order, portfolio, symbol, simulation.fill_time, simulation.fill_price, simulation.quantity,
            order.signal_date, simulation.market, entry_times, entry_prices, diag, commission_config,
        )
    elif order.side == 'SELL':
        trades = _execute_sell(
            order, portfolio, symbol, simulation.fill_time, simulation.fill_price, simulation.quantity,
            order.signal_date, simulation.market, entry_times, entry_prices, diag, commission_config,
            ignore_settlement,
        )
    else:
        raise OrderRejectedError(OrderRejectionReason.UNKNOWN_SIDE, symbol,
                                 f"side={order.side!r}")

    diag.record_execution_observation(simulation.observation)
    return trades


def apply_slippage(price: float, side: str, bps: float) -> float:
    if price <= 0:
        return price
    slippage = price * (bps / 10000)
    if side == 'BUY':
        return price + slippage
    return price - slippage


def resolve_base_fill_price(
    order: "DeferredOrder",
    raw_price: float,
    order_type: str,
    slippage_bps: float,
    price_field: str = "open",
) -> float:
    if order_type != "LIMIT":
        return apply_slippage(raw_price, order.side, slippage_bps)
    limit_price = order.price
    limit_price = _positive_finite_float(limit_price, order.symbol, "limit price")
    if order.side == "BUY" and raw_price > limit_price:
        raise OrderRejectedError(OrderRejectionReason.LIMIT_NOT_MARKETABLE, order.symbol,
                                 f"{price_field}={raw_price} > limit={limit_price}")
    if order.side == "SELL" and raw_price < limit_price:
        raise OrderRejectedError(OrderRejectionReason.LIMIT_NOT_MARKETABLE, order.symbol,
                                 f"{price_field}={raw_price} < limit={limit_price}")
    return float(limit_price)


def enforce_limit_after_impact(order: "DeferredOrder", fill_price: float, order_type: str) -> None:
    if order_type != "LIMIT":
        return
    limit_price = order.price
    if order.side == "BUY" and fill_price > limit_price:
        raise OrderRejectedError(OrderRejectionReason.LIMIT_NOT_MARKETABLE, order.symbol,
                                 f"impacted fill={fill_price} > limit={limit_price}")
    if order.side == "SELL" and fill_price < limit_price:
        raise OrderRejectedError(OrderRejectionReason.LIMIT_NOT_MARKETABLE, order.symbol,
                                 f"impacted fill={fill_price} < limit={limit_price}")


def apply_lot_rounding(quantity: float, lot_size: int, side: str, market: str) -> Tuple[Optional[float], bool]:
    if market not in ("HK", "CN"):
        return float(quantity), False
    if side == 'BUY':
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


def _execute_buy(
    order: "DeferredOrder",
    portfolio: Any,
    symbol: str,
    fill_ts: datetime,
    fill_price: float,
    quantity: float,
    signal_date: Optional[date],
    market: str,
    entry_times: Dict[Any, datetime],
    entry_prices: Dict[Any, float],
    diag: BacktestDiagnostics,
    commission_config: Any,
) -> List[Trade]:
    fill_date_val = fill_ts.date() if hasattr(fill_ts, 'date') else date.today()
    cost_breakdown = calculate_commission(symbol, fill_price, quantity, order.side, commission_config, fill_date_val)
    commission = sum(cost_breakdown.values())

    total_cost = fill_price * quantity + commission
    if hasattr(portfolio, 'can_afford'):
        if not portfolio.can_afford(total_cost):
            raise OrderRejectedError(OrderRejectionReason.INSUFFICIENT_CASH, symbol)
    elif portfolio.cash < total_cost:
        raise OrderRejectedError(OrderRejectionReason.INSUFFICIENT_CASH, symbol)

    diag.total_commission += commission

    strategy_key = order.strategy
    pos_before = portfolio.get_position(symbol)
    if pos_before is None or pos_before.quantity == 0:
        entry_times[(strategy_key, symbol)] = fill_ts
        entry_prices[(strategy_key, symbol)] = fill_price
    else:
        entry_times.setdefault((strategy_key, symbol), fill_ts)
        entry_prices.setdefault((strategy_key, symbol), fill_price)

    portfolio.update_position(symbol, quantity=quantity, price=fill_price, cost=total_cost, trade_date=fill_date_val)
    portfolio.cash -= total_cost

    diag.fill_count += 1

    return [Trade(
        entry_time=fill_ts,
        exit_time=fill_ts,
        symbol=symbol,
        side=order.side,
        entry_price=fill_price,
        exit_price=fill_price,
        quantity=quantity,
        pnl=-commission,
        commission=commission,
        realized_pnl=-commission,
        signal_date=signal_date,
        fill_date=fill_ts,
        fill_price=fill_price,
        intended_qty=order.quantity,
        cost_breakdown=cost_breakdown,
        strategy_name=order.strategy,
    )]


def _execute_sell(
    order: "DeferredOrder",
    portfolio: Any,
    symbol: str,
    fill_ts: datetime,
    fill_price: float,
    quantity: float,
    signal_date: Optional[date],
    market: str,
    entry_times: Dict[Any, datetime],
    entry_prices: Dict[Any, float],
    diag: BacktestDiagnostics,
    commission_config: Any,
    ignore_settlement: bool = False,
) -> List[Trade]:
    pos = portfolio.get_position(symbol)
    if not pos or pos.quantity <= 0:
        raise OrderRejectedError(OrderRejectionReason.NO_POSITION, symbol,
                                 "no position to sell (short not supported)")

    fill_date_val = fill_ts.date() if hasattr(fill_ts, 'date') else date.today()
    settled_qty = pos.quantity if ignore_settlement else get_settled_quantity(symbol, pos, fill_date_val, market)
    if settled_qty <= 0:
        if market == "CN":
            diag.t1_rejected_sells += 1
        raise OrderRejectedError(OrderRejectionReason.T1_SETTLEMENT, symbol)

    sell_qty = min(quantity, settled_qty)
    if sell_qty < quantity:
        diag.truncated_sells += 1
        logger.warning("SELL %s truncated: requested %d, settled %d", symbol, quantity, sell_qty)

    cost_breakdown = calculate_commission(symbol, fill_price, sell_qty, order.side, commission_config, fill_date_val)
    commission = sum(cost_breakdown.values())

    diag.total_commission += commission

    lot_slices = fifo_lot_slices(pos, sell_qty)

    trades = []
    total_realized = 0.0
    for lot_date, sub_qty, lot_price in lot_slices:
        sub_ratio = sub_qty / sell_qty
        sub_commission = commission * sub_ratio
        sub_realized = (fill_price - lot_price) * sub_qty
        total_realized += sub_realized
        sub_cost_breakdown = {k: v * sub_ratio for k, v in cost_breakdown.items()}
        entry_time = datetime(lot_date.year, lot_date.month, lot_date.day)
        trades.append(Trade(
            entry_time=entry_time,
            exit_time=fill_ts,
            symbol=symbol,
            side=order.side,
            entry_price=lot_price,
            exit_price=fill_price,
            quantity=sub_qty,
            pnl=sub_realized - sub_commission,
            commission=sub_commission,
            realized_pnl=sub_realized,
            signal_date=signal_date,
            fill_date=fill_ts,
            fill_price=fill_price,
            intended_qty=order.quantity,
            cost_breakdown=sub_cost_breakdown,
            strategy_name=order.strategy,
        ))

    portfolio.cash += fill_price * sell_qty - commission
    portfolio.update_position(symbol, quantity=-sell_qty, price=fill_price, cost=0, realized_pnl=total_realized)

    updated_pos = portfolio.get_position(symbol)
    if updated_pos is None or updated_pos.quantity <= 0:
        strategy_key = order.strategy
        entry_times.pop((strategy_key, symbol), None)
        entry_prices.pop((strategy_key, symbol), None)

    diag.fill_count += 1

    return trades
