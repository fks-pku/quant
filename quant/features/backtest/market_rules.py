"""Market-specific rules registry — symbol classification, lot sizes, price limits, settlement."""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from quant.shared.utils.symbol_utils import detect_market, is_cn_symbol, cn_price_limit_pct


MARKET_CURRENCY = {"CN": "CNY", "HK": "HKD", "US": "USD"}
DEFAULT_LOT_SIZE = 100
IPO_NO_LIMIT_DAYS = 5


def get_market(symbol: str) -> str:
    return detect_market(symbol)


def get_lot_size(symbol: str, lot_sizes: Dict[str, int]) -> int:
    market = get_market(symbol)
    if market == "US":
        return 1
    ls = lot_sizes.get(symbol, DEFAULT_LOT_SIZE)
    return ls if ls is not None and ls > 0 else DEFAULT_LOT_SIZE


def is_price_at_limit(
    symbol: str,
    open_price: float,
    prev_close: float,
    current_date: Optional[date] = None,
    ipo_dates: Optional[Dict[str, date]] = None,
) -> bool:
    market = get_market(symbol)
    if market != "CN":
        return False
    if prev_close <= 0:
        return False
    if current_date and ipo_dates and symbol in ipo_dates:
        ipo_d = ipo_dates[symbol]
        trading_days_since_ipo = (current_date - ipo_d).days
        if trading_days_since_ipo <= IPO_NO_LIMIT_DAYS:
            return False
    limit_pct = cn_price_limit_pct(symbol)
    upper = round(prev_close * (1 + limit_pct), 2)
    lower = round(prev_close * (1 - limit_pct), 2)
    open_rounded = round(open_price, 2)
    return open_rounded >= upper or open_rounded <= lower


def get_settled_quantity(symbol: str, pos: Any, trade_date: date, market: Optional[str] = None) -> float:
    if market is None:
        market = get_market(symbol)
    if market == "CN":
        return pos.settled_quantity(trade_date)
    return pos.quantity


def select_currency(symbols: List[str]) -> str:
    if not symbols:
        return "USD"
    markets = {get_market(s) for s in symbols}
    if len(markets) == 1:
        return MARKET_CURRENCY.get(markets.pop(), "USD")
    return "USD"


def is_suspended(bar: Dict) -> bool:
    if bar.get("volume", 0) == 0:
        return True
    if bar.get("close", 0) == 0 and bar.get("open", 0) == 0:
        return True
    return False


def get_earliest_lot_time(pos) -> Optional[datetime]:
    if not pos._lots:
        return None
    earliest = min(pos._lots.keys())
    return datetime(earliest.year, earliest.month, earliest.day)


def fifo_lot_slices(pos, sell_qty: float) -> List[tuple]:
    slices = []
    remaining = sell_qty
    for lot_date in sorted(pos._lots.keys()):
        if remaining <= 0:
            break
        lot = pos._lots[lot_date]
        take = min(lot.qty, remaining)
        slices.append((lot_date, take, lot.price))
        remaining -= take
    return slices
