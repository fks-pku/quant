"""Per-strategy position tracking via DB-backed StrategyStateStore."""

import threading
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from quant.infrastructure.execution.strategy_state_store import StrategyStateStore
from quant.shared.utils.logger import setup_logger

DEFAULT_STRATEGY = "default"

_tracker_instance: Optional["StrategyPositionTracker"] = None
_tracker_lock = threading.Lock()


def get_tracker(store: Optional[StrategyStateStore] = None, mode: str = "live") -> "StrategyPositionTracker":
    global _tracker_instance
    if _tracker_instance is None:
        with _tracker_lock:
            if _tracker_instance is None:
                _tracker_instance = StrategyPositionTracker(store=store, mode=mode)
    return _tracker_instance


@dataclass
class StrategyPosition:
    symbol: str
    strategy_name: str
    qty: float = 0.0
    avg_cost: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StrategySnapshot:
    date: str
    strategy_name: str
    nav: float = 0.0
    market_value: float = 0.0
    cash: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0


class StrategyPositionTracker:
    def __init__(self, store: Optional[StrategyStateStore] = None, mode: str = "live"):
        self._store = store
        self._mode = mode
        self._positions: Dict[str, Dict[str, StrategyPosition]] = {}
        self._realized_pnl: Dict[str, float] = {}
        self._order_strategy_map: Dict[str, str] = {}
        self._lock = threading.RLock()
        self.logger = setup_logger("StrategyPositionTracker")
        if self._store is not None:
            self._load()

    def _load(self) -> None:
        if self._store is None:
            return
        try:
            positions_list = self._store.get_all_positions_for_mode(mode=self._mode)
            for pos_data in positions_list:
                strategy = pos_data.get("strategy_name", DEFAULT_STRATEGY)
                if strategy not in self._positions:
                    self._positions[strategy] = {}
                self._positions[strategy][pos_data["symbol"]] = StrategyPosition(
                    symbol=pos_data["symbol"],
                    strategy_name=strategy,
                    qty=float(pos_data.get("quantity", 0.0)),
                    avg_cost=float(pos_data.get("avg_cost", 0.0)),
                )
                self._realized_pnl[strategy] = float(pos_data.get("realized_pnl", 0.0))
            if self._positions:
                self.logger.info("Loaded %d strategy positions from DB", sum(len(v) for v in self._positions.values()))
        except Exception as e:
            self.logger.warning("Failed to load strategy positions from DB: %s", e)

    def _save(self) -> None:
        pass

    def record_order(self, order_id: str, strategy_name: Optional[str]) -> None:
        with self._lock:
            self._order_strategy_map[order_id] = strategy_name or DEFAULT_STRATEGY

    def get_strategy_for_order(self, order_id: str) -> str:
        with self._lock:
            return self._order_strategy_map.get(order_id, DEFAULT_STRATEGY)

    def get_positions_for_strategy(self, strategy_name: str) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            positions = self._positions.get(strategy_name, {})
            return {symbol: pos.to_dict() for symbol, pos in positions.items()}

    def update_from_fill(
        self,
        strategy_name: Optional[str],
        symbol: str,
        side: str,
        qty: float,
        price: float,
        commission: float = 0.0,
    ) -> None:
        strategy = strategy_name or DEFAULT_STRATEGY
        with self._lock:
            if strategy not in self._positions:
                self._positions[strategy] = {}
            if strategy not in self._realized_pnl:
                self._realized_pnl[strategy] = 0.0
            positions = self._positions[strategy]

            if symbol not in positions:
                positions[symbol] = StrategyPosition(
                    symbol=symbol, strategy_name=strategy
                )
            pos = positions[symbol]

            if side.upper() == "BUY":
                total_cost = pos.avg_cost * pos.qty + price * qty + max(float(commission or 0.0), 0.0)
                pos.qty += qty
                pos.avg_cost = total_cost / pos.qty if pos.qty > 0 else 0.0
            elif side.upper() == "SELL":
                sell_qty = min(qty, pos.qty)
                sell_commission = max(float(commission or 0.0), 0.0) * (sell_qty / qty) if qty > 0 else 0.0
                realized = (price - pos.avg_cost) * sell_qty - sell_commission
                self._realized_pnl[strategy] = self._realized_pnl.get(strategy, 0.0) + realized
                pos.qty -= qty
                if pos.qty <= 1e-9:
                    del positions[symbol]

    def calibrate(self, broker_positions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            tracker_totals: Dict[str, float] = {}
            for strat, positions in self._positions.items():
                for sym, pos in positions.items():
                    tracker_totals[sym] = tracker_totals.get(sym, 0.0) + pos.qty

            for bp in broker_positions:
                symbol = bp.get("symbol", bp.get("code", ""))
                actual_qty = float(bp.get("qty", bp.get("quantity", 0)))
                tracked_qty = tracker_totals.get(symbol, 0.0)
                diff = actual_qty - tracked_qty
                market_value = float(bp.get("market_value", 0.0) or 0.0)
                current_price = float(bp.get("current_price", bp.get("price", 0.0)) or 0.0)
                if current_price <= 0 and actual_qty > 0 and market_value > 0:
                    current_price = market_value / actual_qty
                broker_unrealized = float(bp.get("unrealized_pnl", 0.0) or 0.0)

                if diff > 0.001:
                    if DEFAULT_STRATEGY not in self._positions:
                        self._positions[DEFAULT_STRATEGY] = {}
                    if symbol not in self._positions[DEFAULT_STRATEGY]:
                        self._positions[DEFAULT_STRATEGY][symbol] = StrategyPosition(
                            symbol=symbol,
                            strategy_name=DEFAULT_STRATEGY,
                        )
                    self._positions[DEFAULT_STRATEGY][symbol].qty += diff
                    cost = float(bp.get("cost_price", bp.get("avg_cost", 0)))
                    if cost > 0:
                        self._positions[DEFAULT_STRATEGY][symbol].avg_cost = cost
                elif diff < -0.001:
                    self._reduce_proportionally(symbol, abs(diff))

                if actual_qty > 0 and symbol:
                    self._mark_to_market(
                        symbol=symbol,
                        actual_qty=actual_qty,
                        market_value=market_value,
                        current_price=current_price,
                        broker_unrealized=broker_unrealized,
                    )

            self._save()
            return self.get_breakdown()

    def _mark_to_market(
        self,
        symbol: str,
        actual_qty: float,
        market_value: float,
        current_price: float,
        broker_unrealized: float,
    ) -> None:
        if actual_qty <= 0:
            return
        for positions in self._positions.values():
            if symbol not in positions:
                continue
            pos = positions[symbol]
            share = pos.qty / actual_qty
            pos.market_value = market_value * share if market_value > 0 else pos.qty * current_price
            if current_price > 0:
                pos.unrealized_pnl = (current_price - pos.avg_cost) * pos.qty
            else:
                pos.unrealized_pnl = broker_unrealized * share

    def _reduce_proportionally(self, symbol: str, qty_to_reduce: float) -> None:
        remaining = qty_to_reduce
        for strat, positions in self._positions.items():
            if symbol in positions and remaining > 0:
                pos = positions[symbol]
                reduce_qty = min(pos.qty, remaining)
                pos.qty -= reduce_qty
                remaining -= reduce_qty
                if pos.qty <= 1e-9:
                    del positions[symbol]

    def get_breakdown(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            breakdown: Dict[str, Dict[str, Any]] = {}
            for strat, positions in self._positions.items():
                holdings = []
                total_mv = 0.0
                total_unrealized = 0.0
                for sym, pos in positions.items():
                    market_value = pos.market_value or 0.0
                    unrealized_pnl = pos.unrealized_pnl if pos.unrealized_pnl is not None else (market_value - pos.avg_cost * pos.qty)
                    holdings.append({
                        "symbol": sym,
                        "strategy": strat,
                        "qty": pos.qty,
                        "avg_cost": round(pos.avg_cost, 4),
                        "market_value": round(market_value, 2),
                        "unrealized_pnl": round(unrealized_pnl, 2),
                    })
                    total_mv += market_value
                    total_unrealized += unrealized_pnl
                breakdown[strat] = {
                    "holdings": holdings,
                    "total_market_value": round(total_mv, 2),
                    "total_unrealized_pnl": round(total_unrealized, 2),
                    "total_realized_pnl": round(self._realized_pnl.get(strat, 0.0), 2),
                }
            return breakdown

    def get_all_strategies(self) -> List[str]:
        with self._lock:
            return list(self._positions.keys())

    def snapshot_all(self, total_nav: float) -> List[StrategySnapshot]:
        with self._lock:
            total_mv = sum(
                sum(pos.market_value or 0 for pos in positions.values())
                for positions in self._positions.values()
            )
            total_mv = max(total_mv, 1.0)
            snapshots = []
            today = date.today().isoformat()
            for strat, positions in self._positions.items():
                strat_mv = sum(pos.market_value or 0 for pos in positions.values())
                strat_unrealized = sum(pos.unrealized_pnl or 0 for pos in positions.values())
                strat_realized = self._realized_pnl.get(strat, 0.0)
                cash_share = total_nav * (strat_mv / total_mv) - strat_mv
                snapshots.append(StrategySnapshot(
                    date=today,
                    strategy_name=strat,
                    nav=round(strat_mv + cash_share, 2),
                    market_value=round(strat_mv, 2),
                    cash=round(cash_share, 2),
                    unrealized_pnl=round(strat_unrealized, 2),
                    realized_pnl=round(strat_realized, 2),
                ))
            return snapshots

    def clear(self) -> None:
        with self._lock:
            self._positions.clear()
            self._realized_pnl.clear()
            self._order_strategy_map.clear()
