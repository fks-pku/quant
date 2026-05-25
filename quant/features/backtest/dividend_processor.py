"""Dividend processing — cash dividends, stock splits, and CN dividend tax."""

import logging
import math
from datetime import date, datetime
from typing import Dict, List, Any, Optional, Union

from quant.features.backtest.market_rules import get_market

logger = logging.getLogger(__name__)

CN_DIVIDEND_TAX_SHORT_DAYS = 30
CN_DIVIDEND_TAX_MEDIUM_DAYS = 365
ADJ_FACTOR_SPLIT_LOWER_BOUND = 0.75
ADJ_FACTOR_SPLIT_UPPER_BOUND = 1.25
ADJ_FACTOR_CONTINUITY_LOWER_BOUND = 0.80
ADJ_FACTOR_CONTINUITY_UPPER_BOUND = 1.20


def process_dividends(
    data_provider: Any,
    portfolio: Any,
    symbols: List[str],
    current_date: datetime,
    last_prices: Dict[str, float],
    entry_times: Dict[str, datetime],
    diag: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    stock_dividends: List[Dict[str, Any]] = []
    if not data_provider or not hasattr(data_provider, 'get_dividend_for_date'):
        return stock_dividends
    positions = getattr(portfolio, "positions", None)
    if isinstance(positions, dict):
        position_items = [(symbol, pos) for symbol, pos in positions.items() if getattr(pos, "quantity", 0) > 0]
    else:
        position_items = [(symbol, portfolio.get_position(symbol)) for symbol in symbols]
    for symbol, pos in position_items:
        if not pos or pos.quantity <= 0:
            continue
        try:
            div = data_provider.get_dividend_for_date(symbol, current_date)
        except (TypeError, ValueError, KeyError, AttributeError):
            logger.warning("Error getting dividend for %s on %s", symbol, current_date)
            continue
        if not div:
            continue
        market = get_market(symbol)
        try:
            cash_div = float(div.get('cash_dividend', 0) or 0)
        except (ValueError, TypeError):
            logger.warning("Invalid cash dividend for %s on %s: %s", symbol, current_date, div.get('cash_dividend'))
            cash_div = 0.0
        try:
            stock_div = float(div.get('stock_dividend', 0) or 0)
        except (ValueError, TypeError):
            logger.warning("Invalid stock dividend for %s on %s: %s", symbol, current_date, div.get('stock_dividend'))
            stock_div = 0.0
        if cash_div > 0:
            payment = cash_div * pos.quantity
            tax = 0.0
            if market == "CN":
                tax = calculate_cn_dividend_tax(pos, cash_div, current_date)
            if diag is not None:
                diag.total_cash_dividends += payment
                diag.total_dividend_tax += tax
                diag.total_net_dividends += payment - tax
            portfolio.cash += payment - tax
            pos.adjust_lots_for_cash_dividend(cash_div)
            logger.info("%s ex-div: cash %.4f/share x %d = %.2f, tax=%.2f", symbol, cash_div, pos.quantity, payment, tax)
        if stock_div > 0:
            additional_shares = pos.quantity * stock_div
            pos.adjust_lots_for_stock_dividend(stock_div)
            stock_dividends.append({'symbol': symbol, 'ratio': stock_div, 'additional_shares': additional_shares})
            logger.info("%s ex-div: stock %.4f/share adjusted lots, new qty=%.0f, avg_cost=%.4f", symbol, stock_div, pos.quantity, pos.avg_cost)
    return stock_dividends


def process_adjustment_factor_changes(
    data_provider: Any,
    portfolio: Any,
    prev_bars: Dict[str, Dict[str, Any]],
    today_bars: Dict[str, Dict[str, Any]],
    current_date: datetime,
) -> List[Dict[str, Any]]:
    adjustments: List[Dict[str, Any]] = []
    positions = getattr(portfolio, "positions", None)
    if not isinstance(positions, dict):
        return adjustments
    for symbol, pos in positions.items():
        if getattr(pos, "quantity", 0) <= 0:
            continue
        if _has_explicit_stock_dividend(data_provider, symbol, current_date):
            continue
        factor = _implicit_quantity_factor(prev_bars.get(symbol), today_bars.get(symbol))
        if factor is None:
            continue
        old_quantity = float(pos.quantity)
        pos.adjust_lots_for_quantity_factor(factor)
        new_quantity = float(pos.quantity)
        quantity_delta = new_quantity - old_quantity
        if abs(quantity_delta) < 1e-10:
            continue
        close_price = _positive_float(today_bars.get(symbol, {}).get("close"))
        if close_price is not None:
            pos.update_market_price(close_price)
        adjustments.append({
            "symbol": symbol,
            "factor": factor,
            "quantity_delta": quantity_delta,
            "new_quantity": new_quantity,
        })
        logger.info(
            "%s adj_factor quantity adjustment factor=%.6f, qty %.4f -> %.4f",
            symbol,
            factor,
            old_quantity,
            new_quantity,
        )
    return adjustments


def _has_explicit_stock_dividend(data_provider: Any, symbol: str, current_date: datetime) -> bool:
    if not data_provider or not hasattr(data_provider, "get_dividend_for_date"):
        return False
    try:
        div = data_provider.get_dividend_for_date(symbol, current_date)
    except (TypeError, ValueError, KeyError, AttributeError):
        return False
    if not div:
        return False
    stock_div = _positive_float(div.get("stock_dividend"))
    return stock_div is not None and stock_div > 0


def _implicit_quantity_factor(
    prev_bar: Optional[Dict[str, Any]],
    today_bar: Optional[Dict[str, Any]],
) -> Optional[float]:
    if not prev_bar or not today_bar:
        return None
    prev_factor = _positive_float(prev_bar.get("adj_factor"))
    today_factor = _positive_float(today_bar.get("adj_factor"))
    prev_close = _positive_float(prev_bar.get("close"))
    today_close = _positive_float(today_bar.get("close"))
    if prev_factor is None or today_factor is None or prev_close is None or today_close is None:
        return None
    quantity_factor = today_factor / prev_factor
    if ADJ_FACTOR_SPLIT_LOWER_BOUND <= quantity_factor <= ADJ_FACTOR_SPLIT_UPPER_BOUND:
        return None
    adjusted_price_ratio = (today_close / prev_close) * quantity_factor
    if not ADJ_FACTOR_CONTINUITY_LOWER_BOUND <= adjusted_price_ratio <= ADJ_FACTOR_CONTINUITY_UPPER_BOUND:
        return None
    return quantity_factor


def _positive_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def calculate_cn_dividend_tax(pos: Any, cash_div: float, current_date: Union[date, datetime]) -> float:
    total_tax = 0.0
    today = current_date.date() if hasattr(current_date, 'date') else current_date
    for lot_date, lot in pos.iter_lots():
        holding_days = (today - lot_date).days
        if holding_days <= CN_DIVIDEND_TAX_SHORT_DAYS:
            rate = 0.20
        elif holding_days <= CN_DIVIDEND_TAX_MEDIUM_DAYS:
            rate = 0.10
        else:
            rate = 0.0
        total_tax += cash_div * lot.qty * rate
    return total_tax
