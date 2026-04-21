"""Per-strategy position tracking via order-level attribution."""

import json
import threading
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from quant.shared.utils.logger import setup_logger

_STRATEGY_POSITIONS_FILE = Path(__file__).parent.parent / "data" / "strategy_positions.json"
DEFAULT_STRATEGY = "default"

_tracker_instance: Optional["StrategyPositionTracker"] = None
_tracker_lock = threading.Lock()


def get_tracker() -> "StrategyPositionTracker":
    global _tracker_instance
    if _tracker_instance is None:
        with _tracker_lock:
            if _tracker_instance is None:
                _tracker_instance = StrategyPositionTracker()
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
    def __init__(self, persist_path: Optional[Path] = None):
        self._path = persist_path or _STRATEGY_POSITIONS_FILE
        self._positions: Dict[str, Dict[str, StrategyPosition]] = {}
        self._realized_pnl: Dict[str, float] = {}
        self._order_strategy_map: Dict[str, str] = {}
        self._lock = threading.RLock()
        self.logger = setup_logger("StrategyPositionTracker")
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for strat, positions in data.get("positions", {}).items():
                    self._positions[strat] = {}
                    for sym, pos_data in positions.items():
                        self._positions[strat][sym] = StrategyPosition(**pos_data)
                self._realized_pnl = data.get("realized_pnl", {})
                self._order_strategy_map = data.get("order_map", {})
                self.logger.info(f"Loaded {sum(len(v) for v in self._positions.values())} strategy positions from {self._path}")
            except Exception as e:
                self.logger.warning(f"Failed to load strategy positions: {e}")

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "positions": {
                    strat: {sym: pos.to_dict() for sym, pos in positions.items()}
                    for strat, positions in self._positions.items()
                },
                "realized_pnl": self._realized_pnl,
                "order_map": self._order_strategy_map,
            }
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to save strategy positions: {e}")

    def record_order(self, order_id: str, strategy_name: Optional[str]) -> None:
        with self._lock:
            self._order_strategy_map[order_id] = strategy_name or DEFAULT_STRATEGY
            self._save()

    def get_strategy_for_order(self, order_id: str) -> str:
        with self._lock:
            return self._order_strategy_map.get(order_id, DEFAULT_STRATEGY)

    def update_from_fill(
        self,
        strategy_name: Optional[str],
        symbol: str,
        side: str,
        qty: float,
        price: float,
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
                total_cost = pos.avg_cost * pos.qty + price * qty
                pos.qty += qty
                pos.avg_cost = total_cost / pos.qty if pos.qty > 0 else 0.0
            elif side.upper() == "SELL":
                sell_qty = min(qty, pos.qty)
                realized = (price - pos.avg_cost) * sell_qty
                self._realized_pnl[strategy] = self._realized_pnl.get(strategy, 0.0) + realized
                pos.qty -= qty
                if pos.qty <= 1e-9:
                    del positions[symbol]

            self._save()

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

            self._save()
            return self.get_breakdown()

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
                    unrealized_pnl = pos.unrealized_pnl or (market_value - pos.avg_cost * pos.qty)
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
            self._save()
