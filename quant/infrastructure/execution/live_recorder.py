"""File-backed live trading recorder for strategy signals, fills, and performance."""

import json
import math
import threading
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from statistics import mean, stdev
from typing import Any, Dict, Iterable, List, Optional

from quant.domain.models.order import Order
from quant.shared.utils.logger import setup_logger


_DEFAULT_BASE_DIR = Path(__file__).resolve().parents[1] / "var" / "live_trading"
_VALID_KINDS = {"signals", "orders", "fills", "snapshots"}
_recorder_instance: Optional["LiveTradingRecorder"] = None
_recorder_lock = threading.Lock()


def get_live_recorder() -> "LiveTradingRecorder":
    global _recorder_instance
    if _recorder_instance is None:
        with _recorder_lock:
            if _recorder_instance is None:
                _recorder_instance = LiveTradingRecorder()
    return _recorder_instance


class LiveTradingRecorder:
    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = Path(base_dir) if base_dir is not None else _DEFAULT_BASE_DIR
        self._lock = threading.RLock()
        self.logger = setup_logger("LiveTradingRecorder")

    def record_signal(
        self,
        timestamp: datetime,
        strategy_name: Optional[str],
        symbol: str,
        side: str,
        quantity: float,
        order_type: str,
        price: Optional[float],
        status: str,
        order_id: Optional[str] = None,
        reason: str = "",
    ) -> None:
        self._append("signals", timestamp, {
            "timestamp": timestamp,
            "strategy_name": strategy_name or "default",
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "order_type": order_type,
            "price": price,
            "status": status,
            "order_id": order_id,
            "reason": reason,
        })

    def record_order(
        self,
        order: Order,
        broker_order_id: Optional[str],
        status: str,
        reason: str = "",
        timestamp: Optional[datetime] = None,
    ) -> None:
        ts = timestamp or order.timestamp or datetime.now()
        side = order.side.value if hasattr(order.side, "value") else str(order.side)
        order_type = order.order_type.value if hasattr(order.order_type, "value") else str(order.order_type)
        self._append("orders", ts, {
            "timestamp": ts,
            "order_id": order.order_id,
            "broker_order_id": broker_order_id,
            "strategy_name": order.strategy_name or "default",
            "symbol": order.symbol,
            "side": side,
            "quantity": order.quantity,
            "order_type": order_type,
            "price": order.price,
            "status": status,
            "reason": reason,
        })

    def record_fill(
        self,
        order_id: str,
        timestamp: datetime,
        strategy_name: Optional[str],
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        commission: float = 0.0,
        fill_id: Optional[str] = None,
    ) -> None:
        self._append("fills", timestamp, {
            "timestamp": timestamp,
            "fill_id": fill_id,
            "order_id": order_id,
            "strategy_name": strategy_name or "default",
            "symbol": symbol,
            "side": side.upper(),
            "quantity": quantity,
            "price": price,
            "commission": commission,
            "value": quantity * price,
        })

    def record_strategy_snapshot(
        self,
        timestamp: datetime,
        strategy_name: str,
        nav: float,
        market_value: float,
        cash: float,
        realized_pnl: float,
        unrealized_pnl: float,
    ) -> None:
        self._append("snapshots", timestamp, {
            "timestamp": timestamp,
            "date": timestamp.date().isoformat(),
            "strategy_name": strategy_name,
            "nav": nav,
            "market_value": market_value,
            "cash": cash,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "total_pnl": realized_pnl + unrealized_pnl,
        })

    def record_strategy_breakdown(
        self,
        breakdown: Dict[str, Dict[str, Any]],
        total_nav: float,
        timestamp: Optional[datetime] = None,
    ) -> None:
        ts = timestamp or datetime.now()
        total_market_value = sum(float(v.get("total_market_value", 0.0)) for v in breakdown.values())
        cash_pool = max(0.0, float(total_nav) - total_market_value)
        for strategy_name, data in breakdown.items():
            market_value = float(data.get("total_market_value", 0.0))
            cash = cash_pool * (market_value / total_market_value) if total_market_value > 0 else 0.0
            realized = float(data.get("total_realized_pnl", 0.0))
            unrealized = float(data.get("total_unrealized_pnl", 0.0))
            self.record_strategy_snapshot(
                timestamp=ts,
                strategy_name=strategy_name,
                nav=market_value + cash,
                market_value=market_value,
                cash=cash,
                realized_pnl=realized,
                unrealized_pnl=unrealized,
            )

    def read_day(
        self,
        kind: str,
        trading_date: str,
        strategy_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if kind not in _VALID_KINDS:
            raise ValueError(f"Unsupported live record kind: {kind}")
        path = self._path(kind, trading_date)
        if not path.exists():
            return []
        records = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                item = json.loads(line)
                if strategy_name is None or item.get("strategy_name") == strategy_name:
                    records.append(item)
        return records

    def get_strategy_performance(self, strategy_name: str, days: int = 365) -> Dict[str, Any]:
        fills = self._read_all("fills", strategy_name, days)
        snapshots = self._read_all("snapshots", strategy_name, days)
        closed_trades = self._closed_trades(fills)
        realized_pnl = sum(t["pnl"] for t in closed_trades)
        gross_profit = sum(t["pnl"] for t in closed_trades if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in closed_trades if t["pnl"] < 0))
        wins = len([t for t in closed_trades if t["pnl"] > 0])
        win_rate = wins / len(closed_trades) if closed_trades else 0.0
        latest_snapshot = self._latest_daily_snapshots(snapshots)[-1] if snapshots else {}
        unrealized = float(latest_snapshot.get("unrealized_pnl", 0.0) or 0.0)
        pnl_curve = self._latest_daily_snapshots(snapshots)
        return {
            "strategy_name": strategy_name,
            "total_pnl": round(realized_pnl + unrealized, 4),
            "realized_pnl": round(realized_pnl, 4),
            "unrealized_pnl": round(unrealized, 4),
            "total_trades": len(closed_trades),
            "win_rate": round(win_rate, 6),
            "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss > 0 else 0.0,
            "max_drawdown": round(self._max_drawdown(pnl_curve), 6),
            "sharpe_ratio": round(self._sharpe(pnl_curve), 6),
            "pnl_curve": pnl_curve,
            "recent_trades": closed_trades[-20:],
            "latest_snapshot": latest_snapshot,
        }

    def _append(self, kind: str, timestamp: datetime, record: Dict[str, Any]) -> None:
        path = self._path(kind, timestamp.date().isoformat())
        payload = self._jsonable(record)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def _path(self, kind: str, trading_date: str) -> Path:
        return self.base_dir / trading_date / f"{kind}.jsonl"

    def _read_all(self, kind: str, strategy_name: str, days: int) -> List[Dict[str, Any]]:
        if not self.base_dir.exists():
            return []
        records: List[Dict[str, Any]] = []
        day_dirs = sorted([p for p in self.base_dir.iterdir() if p.is_dir()])[-days:]
        for day_dir in day_dirs:
            records.extend(self.read_day(kind, day_dir.name, strategy_name=strategy_name))
        return records

    def _closed_trades(self, fills: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        lots: Dict[str, List[List[float]]] = {}
        trades: List[Dict[str, Any]] = []
        for fill in sorted(fills, key=lambda item: item.get("timestamp", "")):
            symbol = fill.get("symbol", "")
            side = str(fill.get("side", "")).upper()
            qty = float(fill.get("quantity", 0.0) or 0.0)
            price = float(fill.get("price", 0.0) or 0.0)
            if side == "BUY":
                lots.setdefault(symbol, []).append([qty, price])
                continue
            if side != "SELL":
                continue
            remaining = qty
            pnl = 0.0
            closed_qty = 0.0
            symbol_lots = lots.setdefault(symbol, [])
            while remaining > 1e-9 and symbol_lots:
                lot_qty, lot_price = symbol_lots[0]
                take = min(lot_qty, remaining)
                pnl += (price - lot_price) * take
                closed_qty += take
                lot_qty -= take
                remaining -= take
                if lot_qty <= 1e-9:
                    symbol_lots.pop(0)
                else:
                    symbol_lots[0][0] = lot_qty
            if closed_qty > 0:
                trades.append({
                    "timestamp": fill.get("timestamp"),
                    "symbol": symbol,
                    "quantity": closed_qty,
                    "exit_price": price,
                    "pnl": round(pnl, 4),
                    "order_id": fill.get("order_id"),
                    "strategy_name": fill.get("strategy_name"),
                })
        return trades

    def _latest_daily_snapshots(self, snapshots: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        by_date: Dict[str, Dict[str, Any]] = {}
        for snap in snapshots:
            key = snap.get("date") or str(snap.get("timestamp", ""))[:10]
            by_date[key] = snap
        return [by_date[key] for key in sorted(by_date.keys())]

    def _max_drawdown(self, curve: List[Dict[str, Any]]) -> float:
        peak = -math.inf
        max_dd = 0.0
        for point in curve:
            nav = float(point.get("nav", 0.0) or 0.0)
            peak = max(peak, nav)
            if peak > 0:
                max_dd = max(max_dd, (peak - nav) / peak)
        return max_dd

    def _sharpe(self, curve: List[Dict[str, Any]]) -> float:
        navs = [float(point.get("nav", 0.0) or 0.0) for point in curve]
        returns = [
            navs[i] / navs[i - 1] - 1.0
            for i in range(1, len(navs))
            if navs[i - 1] > 0
        ]
        if len(returns) < 2:
            return 0.0
        vol = stdev(returns)
        if vol <= 0:
            return 0.0
        return mean(returns) / vol * math.sqrt(252)

    def _jsonable(self, value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Enum):
            return value.value
        if is_dataclass(value):
            return self._jsonable(asdict(value))
        if isinstance(value, dict):
            return {str(k): self._jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._jsonable(v) for v in value]
        return value
