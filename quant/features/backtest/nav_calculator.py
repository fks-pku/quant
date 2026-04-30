"""NAV computation and open position extraction."""

from datetime import datetime
from typing import Dict, List, Any

from quant.features.backtest.market_rules import get_earliest_lot_time


def calculate_daily_nav(portfolio_map: Dict[str, Any], primary_portfolio: Any, use_subs: bool) -> float:
    if use_subs:
        return primary_portfolio.cash + sum(
            sum(p.market_value for p in pf.positions.values())
            for pf in portfolio_map.values()
        )
    return primary_portfolio.nav


def extract_open_positions(
    portfolio_map: Dict[str, Any],
    primary_portfolio: Any,
    last_prices: Dict[str, float],
    entry_times: Dict[str, datetime],
    use_subs: bool,
) -> List[Dict]:
    open_positions = []
    if use_subs:
        for pf in portfolio_map.values():
            for sym, pos in pf.positions.items():
                if pos.quantity > 0:
                    last_price = last_prices.get(sym, pos.avg_cost)
                    open_positions.append({
                        "symbol": sym,
                        "quantity": pos.quantity,
                        "entry_price": pos.avg_cost,
                        "entry_time": get_earliest_lot_time(pos) or entry_times.get(sym),
                        "current_price": last_price,
                        "unrealized_pnl": (last_price - pos.avg_cost) * pos.quantity,
                        "market_value": pos.quantity * last_price,
                        "strategy": pf.strategy_name,
                    })
    else:
        for sym, pos in primary_portfolio.positions.items():
            if pos.quantity > 0:
                last_price = last_prices.get(sym, pos.avg_cost)
                open_positions.append({
                    "symbol": sym,
                    "quantity": pos.quantity,
                    "entry_price": pos.avg_cost,
                    "entry_time": get_earliest_lot_time(pos) or entry_times.get(sym),
                    "current_price": last_price,
                    "unrealized_pnl": (last_price - pos.avg_cost) * pos.quantity,
                    "market_value": pos.quantity * last_price,
                })
    return open_positions
