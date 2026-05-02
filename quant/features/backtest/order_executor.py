"""Order execution pipeline — slippage, lot rounding, volume limit, commission, trade generation."""

from __future__ import annotations
import logging
from datetime import date, datetime
from typing import TYPE_CHECKING, Dict, List, Any, Optional

import pandas as pd

if TYPE_CHECKING:
    from quant.features.backtest.schemas import BacktestBar, DeferredOrder

from quant.domain.models.trade import Trade
from quant.features.backtest.entities import BacktestDiagnostics
from quant.features.backtest.commission import calculate_commission, VOLUME_PARTICIPATION_LIMIT
from quant.features.backtest.market_rules import (
    get_market,
    get_lot_size,
    is_price_at_limit,
    get_settled_quantity,
    fifo_lot_slices,
)

logger = logging.getLogger(__name__)

DEFAULT_RISK_PRICE_DEVIATION_LIMIT = 0.15


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
) -> List[Trade]:
    raw_open = bar.get('open')
    if not raw_open or raw_open <= 0:
        return []

    signal_date = order.get('_signal_date')
    fill_ts = bar.get('timestamp', datetime.now())
    if not isinstance(fill_ts, datetime):
        fill_ts = pd.Timestamp(fill_ts).to_pydatetime()

    market = get_market(symbol)

    if market == "CN" and prev_bar:
        prev_close = prev_bar.get('close', 0)
        fill_date_val = fill_ts.date() if hasattr(fill_ts, 'date') else date.today()
        if is_price_at_limit(symbol, raw_open, prev_close, fill_date_val, ipo_dates):
            diag.limit_rejected_orders += 1
            return []

    fill_price = apply_slippage(raw_open, order['side'], slippage_bps)

    risk_price = order.get('_risk_check_price', 0)
    if risk_price > 0 and abs(fill_price - risk_price) / risk_price > risk_price_deviation_limit:
        # discarded_orders incremented by caller (engine.py) uniformly
        return []
    quantity = order['quantity']
    lot_size = get_lot_size(symbol, lot_sizes)

    qty, lot_adjusted = apply_lot_rounding(quantity, lot_size, order['side'], market)
    if qty is None:
        return []
    if lot_adjusted:
        diag.lot_adjusted_trades += 1
    quantity = qty

    bar_volume = bar.get('volume', 0)
    if bar_volume > 0 and quantity > bar_volume * VOLUME_PARTICIPATION_LIMIT:
        max_qty = max(1, int(bar_volume * VOLUME_PARTICIPATION_LIMIT))
        if market in ("HK", "CN"):
            max_qty = (max_qty // lot_size) * lot_size
        if max_qty <= 0:
            return []
        quantity = float(max_qty)
        diag.volume_limited_trades += 1

    if order['side'] == 'BUY':
        return _execute_buy(
            order, portfolio, symbol, fill_ts, fill_price, quantity,
            signal_date, market, entry_times, entry_prices, diag, commission_config,
        )
    elif order['side'] == 'SELL':
        return _execute_sell(
            order, portfolio, symbol, fill_ts, fill_price, quantity,
            signal_date, market, entry_times, entry_prices, diag, commission_config,
        )

    return []


def apply_slippage(price: float, side: str, bps: float) -> float:
    slippage = price * (bps / 10000)
    if side == 'BUY':
        return price + slippage
    return price - slippage


def apply_lot_rounding(quantity: float, lot_size: int, side: str, market: str) -> tuple:
    if market not in ("HK", "CN"):
        return float(quantity), False
    if side == 'BUY':
        lot_qty = (int(quantity) // lot_size) * lot_size
        if lot_qty < lot_size:
            return None, False
        if lot_qty != int(quantity):
            return float(lot_qty), True
        return float(lot_qty), False
    else:
        if market == "HK":
            lot_qty = (int(quantity) // lot_size) * lot_size
            if lot_qty < lot_size:
                return None, False
            if lot_qty != int(quantity):
                return float(lot_qty), True
            return float(lot_qty), False
        if int(quantity) < 1:
            return None, False
        return float(quantity), False


def _execute_buy(
    order, portfolio, symbol, fill_ts, fill_price, quantity,
    signal_date, market, entry_times, entry_prices, diag, commission_config,
) -> List[Trade]:
    cost_breakdown = calculate_commission(symbol, fill_price, quantity, order['side'], commission_config)
    commission = sum(cost_breakdown.values())

    total_cost = fill_price * quantity + commission
    if hasattr(portfolio, 'can_afford'):
        if not portfolio.can_afford(total_cost):
            return []
    elif portfolio.cash < total_cost:
        return []

    diag.total_commission += commission

    strategy_key = order.get('strategy')
    pos_before = portfolio.get_position(symbol)
    if pos_before is None or pos_before.quantity == 0:
        entry_times[(strategy_key, symbol)] = fill_ts
        entry_prices[(strategy_key, symbol)] = fill_price
    else:
        entry_times.setdefault((strategy_key, symbol), fill_ts)
        entry_prices.setdefault((strategy_key, symbol), fill_price)

    fill_date_val = fill_ts.date() if hasattr(fill_ts, 'date') else date.today()
    portfolio.update_position(symbol, quantity=quantity, price=fill_price, cost=total_cost, trade_date=fill_date_val)
    portfolio.cash -= total_cost

    diag.fill_count += 1

    return [Trade(
        entry_time=fill_ts,
        exit_time=fill_ts,
        symbol=symbol,
        side=order['side'],
        entry_price=fill_price,
        exit_price=fill_price,
        quantity=quantity,
        pnl=-commission,
        commission=commission,
        realized_pnl=-commission,
        signal_date=signal_date,
        fill_date=fill_ts,
        fill_price=fill_price,
        intended_qty=order['quantity'],
        cost_breakdown=cost_breakdown,
        strategy_name=order.get('strategy'),
    )]


def _execute_sell(
    order, portfolio, symbol, fill_ts, fill_price, quantity,
    signal_date, market, entry_times, entry_prices, diag, commission_config,
) -> List[Trade]:
    pos = portfolio.get_position(symbol)
    if not pos or pos.quantity <= 0:
        logger.warning(
            "SELL rejected for %s: no position to sell (short not supported)", symbol
        )
        return []

    fill_date_val = fill_ts.date() if hasattr(fill_ts, 'date') else date.today()
    settled_qty = get_settled_quantity(symbol, pos, fill_date_val, market)
    if settled_qty <= 0:
        if market == "CN":
            diag.t1_rejected_sells += 1
        return []

    sell_qty = min(quantity, settled_qty)
    if sell_qty < quantity:
        logger.warning("SELL %s truncated: requested %d, settled %d", symbol, quantity, sell_qty)

    cost_breakdown = calculate_commission(symbol, fill_price, sell_qty, order['side'], commission_config)
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
            side=order['side'],
            entry_price=lot_price,
            exit_price=fill_price,
            quantity=sub_qty,
            pnl=sub_realized - sub_commission,
            commission=sub_commission,
            realized_pnl=sub_realized,
            signal_date=signal_date,
            fill_date=fill_ts,
            fill_price=fill_price,
            intended_qty=order['quantity'],
            cost_breakdown=sub_cost_breakdown,
            strategy_name=order.get('strategy'),
        ))

    portfolio.cash += fill_price * sell_qty - commission
    portfolio.update_position(symbol, quantity=-sell_qty, price=fill_price, cost=0, realized_pnl=total_realized)

    updated_pos = portfolio.get_position(symbol)
    if updated_pos is None or updated_pos.quantity <= 0:
        strategy_key = order.get('strategy')
        entry_times.pop((strategy_key, symbol), None)
        entry_prices.pop((strategy_key, symbol), None)

    diag.fill_count += 1

    return trades
