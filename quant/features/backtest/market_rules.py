"""Market-specific rules registry — symbol classification, lot sizes, price limits, settlement."""

import math
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from quant.domain.models.market import detect_market, is_cn_symbol, cn_price_limit_pct


MARKET_CURRENCY = {"CN": "CNY", "HK": "HKD", "US": "USD"}
DEFAULT_LOT_SIZE = 100
IPO_NO_LIMIT_CALENDAR_DAYS = 9


def get_market(symbol: str) -> str:
    return detect_market(symbol)


def get_lot_size(symbol: str, lot_sizes: Dict[str, int]) -> int:
    if lot_sizes is None:
        lot_sizes = {}
    market = get_market(symbol)
    if market == "US":
        return 1
    ls = lot_sizes.get(symbol, DEFAULT_LOT_SIZE)
    return ls if ls is not None and ls > 0 else DEFAULT_LOT_SIZE


def _round_half_up(value: float, decimals: int = 2) -> float:
    multiplier = 10 ** decimals
    return math.floor(value * multiplier + 0.5) / multiplier


def is_price_at_limit(
    symbol: str,
    open_price: float,
    prev_close: float,
    current_date: Optional[date] = None,
    ipo_dates: Optional[Dict[str, date]] = None,
) -> bool:
    return get_price_limit_direction(
        symbol, open_price, prev_close, current_date, ipo_dates
    ) is not None


def get_price_limit_direction(
    symbol: str,
    open_price: float,
    prev_close: float,
    current_date: Optional[date] = None,
    ipo_dates: Optional[Dict[str, date]] = None,
) -> Optional[str]:
    market = get_market(symbol)
    if market != "CN":
        return None
    if prev_close <= 0:
        return None
    if current_date and ipo_dates and symbol in ipo_dates:
        ipo_d = ipo_dates[symbol]
        calendar_days_since_ipo = (current_date - ipo_d).days
        if calendar_days_since_ipo <= IPO_NO_LIMIT_CALENDAR_DAYS:
            return None
    limit_pct = cn_price_limit_pct(symbol)
    upper = _round_half_up(prev_close * (1 + limit_pct))
    lower = _round_half_up(prev_close * (1 - limit_pct))
    open_rounded = _round_half_up(open_price)
    if open_rounded >= upper:
        return "UP"
    if open_rounded <= lower:
        return "DOWN"
    return None


def get_settled_quantity(symbol: str, pos: Any, trade_date: date, market: Optional[str] = None) -> float:
    if market is None:
        market = get_market(symbol)
    if market == "CN":
        return pos.settled_quantity(trade_date)
    return pos.quantity


def select_currency(symbols: List[str]) -> str:
    if not symbols:
        return "USD"
    currencies = {MARKET_CURRENCY.get(get_market(s), "USD") for s in symbols}
    if len(currencies) == 1:
        return currencies.pop()
    raise ValueError(
        f"Mixed currencies are not supported in one backtest: {sorted(currencies)}"
    )


def is_suspended(bar: Dict) -> bool:
    if bar.get("_suspended", False) is True:
        return True
    if bar.get("volume", 0) == 0:
        return True
    if bar.get("close", 0) == 0 and bar.get("open", 0) == 0:
        return True
    return False


def get_earliest_lot_time(pos) -> Optional[datetime]:
    if pos is None or not pos.has_lots:
        return None
    d = pos.earliest_lot_date
    return datetime(d.year, d.month, d.day) if d else None


def fifo_lot_slices(pos, sell_qty: float) -> List[tuple]:
    slices = []
    remaining = sell_qty
    for lot_date, lot in pos.iter_lots_fifo():
        if remaining <= 0:
            break
        take = min(lot.qty, remaining)
        slices.append((lot_date, take, lot.price))
        remaining -= take
    return slices
