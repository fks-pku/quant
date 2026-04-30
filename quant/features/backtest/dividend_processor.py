"""Dividend processing — cash dividends, stock splits, and CN dividend tax."""

import logging
from datetime import date, datetime
from typing import Dict, List, Any

from quant.features.backtest.market_rules import get_market

logger = logging.getLogger(__name__)

CN_DIVIDEND_TAX_SHORT_DAYS = 30
CN_DIVIDEND_TAX_MEDIUM_DAYS = 365


def process_dividends(
    data_provider: Any,
    portfolio: Any,
    symbols: List[str],
    current_date: datetime,
    last_prices: Dict[str, float],
    entry_times: Dict[str, datetime],
) -> None:
    if not data_provider or not hasattr(data_provider, 'get_dividend_for_date'):
        return
    for symbol in symbols:
        pos = portfolio.get_position(symbol)
        if not pos or pos.quantity <= 0:
            continue
        div = data_provider.get_dividend_for_date(symbol, current_date)
        if not div:
            continue
        market = get_market(symbol)
        cash_div = float(div.get('cash_dividend', 0) or 0)
        stock_div = float(div.get('stock_dividend', 0) or 0)
        if cash_div > 0:
            payment = cash_div * pos.quantity
            tax = 0.0
            if market == "CN":
                tax = calculate_cn_dividend_tax(pos, cash_div, current_date)
            portfolio.cash += payment - tax
            pos.adjust_lots_for_cash_dividend(cash_div)
            logger.info("%s ex-div: cash %.4f/share x %d = %.2f, tax=%.2f", symbol, cash_div, pos.quantity, payment, tax)
        if stock_div > 0:
            pos.adjust_lots_for_stock_dividend(stock_div)
            logger.info("%s ex-div: stock %.4f/share adjusted lots, new qty=%.0f, avg_cost=%.4f", symbol, stock_div, pos.quantity, pos.avg_cost)


def calculate_cn_dividend_tax(pos: Any, cash_div: float, current_date: Any) -> float:
    total_tax = 0.0
    today = current_date.date() if hasattr(current_date, 'date') else current_date
    for lot_date, lot in pos._lots.items():
        holding_days = (today - lot_date).days
        if holding_days <= CN_DIVIDEND_TAX_SHORT_DAYS:
            rate = 0.20
        elif holding_days <= CN_DIVIDEND_TAX_MEDIUM_DAYS:
            rate = 0.10
        else:
            rate = 0.0
        total_tax += cash_div * lot.qty * rate
    return total_tax
