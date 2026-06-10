"""File-backed live trading recorder for strategy signals, fills, and performance."""

import json
import threading
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from quant.analytics.performance import calculate_performance_metrics, calculate_round_trip_pnls
from quant.domain.models.trade import Trade
from quant.domain.models.order import Order
from quant.infrastructure.execution.strategy_mode_records import StrategyModeRecordStore
from quant.infrastructure.execution.strategy_state_store import StrategyStateStore
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
        self._mode = "paper" if self.base_dir.name == "paper_trading" else "live"
        self._mode_store = StrategyModeRecordStore(self.base_dir.parent / "strategy_modes")
        self._state_store = StrategyStateStore(self.base_dir.parent / "strategy_state.duckdb")
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
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload = {
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
        }
        if metadata:
            payload.update(metadata)
        self._append("signals", timestamp, payload)

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
        return self.get_strategy_performance_from_records(
            strategy_name,
            {
                "fills": self._read_all("fills", strategy_name, days),
                "orders": self._read_all("orders", strategy_name, days),
                "signals": self._read_all("signals", strategy_name, days),
                "snapshots": self._read_all("snapshots", strategy_name, days),
            },
        )

    def get_strategy_performance_from_records(
        self,
        strategy_name: str,
        records: Dict[str, Iterable[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        fills = list(records.get("fills", []))
        orders = list(records.get("orders", []))
        signals = list(records.get("signals", []))
        snapshots = list(records.get("snapshots", []))
        pnl_curve = self._latest_daily_snapshots(snapshots)
        latest_snapshot = pnl_curve[-1] if pnl_curve else {}
        trades = self._trades_from_fills(fills)
        metrics = calculate_performance_metrics(
            self._equity_curve_from_snapshots(pnl_curve),
            trades,
        )
        round_trip_pnls = calculate_round_trip_pnls(trades)
        realized_pnl = sum(round_trip_pnls)
        unrealized = float(latest_snapshot.get("unrealized_pnl", 0.0) or 0.0)
        closed_trades = self._closed_trades(fills)
        slippage = self._slippage_stats(fills, orders, signals)
        return {
            "strategy_name": strategy_name,
            "total_nav": round(float(latest_snapshot.get("nav", 0.0) or 0.0), 4),
            "cash": round(float(latest_snapshot.get("cash", 0.0) or 0.0), 4),
            "total_pnl": round(realized_pnl + unrealized, 4),
            "realized_pnl": round(realized_pnl, 4),
            "unrealized_pnl": round(unrealized, 4),
            "total_trades": metrics.total_trades,
            "win_rate": round(metrics.win_rate, 6),
            "profit_factor": round(metrics.profit_factor, 6),
            "max_drawdown": round(abs(metrics.max_drawdown_pct), 6),
            "max_drawdown_pct": round(metrics.max_drawdown_pct, 6),
            "sharpe_ratio": round(metrics.sharpe_ratio, 6),
            "sortino_ratio": round(metrics.sortino_ratio, 6),
            "calmar_ratio": round(metrics.calmar_ratio, 6),
            "total_return": round(metrics.total_return, 6),
            "pnl_curve": pnl_curve,
            "recent_trades": closed_trades[-20:],
            "latest_snapshot": latest_snapshot,
            **slippage,
        }

    def get_live_summary(self, days: int = 365) -> Dict[str, Any]:
        fills = self._read_all("fills", None, days)
        orders = self._read_all("orders", None, days)
        signals = self._read_all("signals", None, days)
        snapshots = self._read_all("snapshots", None, days)
        latest_by_strategy: Dict[str, Dict[str, Any]] = {}
        for snapshot in sorted(snapshots, key=lambda item: item.get("timestamp") or item.get("date") or ""):
            strategy_name = str(snapshot.get("strategy_name") or "default")
            latest_by_strategy[strategy_name] = snapshot
        slippage = self._slippage_stats(fills, orders, signals)
        return {
            "total_nav": round(sum(float(item.get("nav", 0.0) or 0.0) for item in latest_by_strategy.values()), 4),
            "cash": round(sum(float(item.get("cash", 0.0) or 0.0) for item in latest_by_strategy.values()), 4),
            "strategy_count": len(latest_by_strategy),
            **slippage,
        }

    def _append(self, kind: str, timestamp: datetime, record: Dict[str, Any]) -> None:
        path = self._path(kind, timestamp.date().isoformat())
        payload = self._jsonable(record)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
            strategy_name = str(payload.get("strategy_name") or "default")
            self._mode_store.append(
                kind,
                mode=self._mode,
                strategy_name=strategy_name,
                record=payload,
                unique=True,
            )
            state_kind = "snapshots" if kind == "snapshots" else kind
            self._state_store.upsert_record(
                state_kind,
                mode=self._mode,
                strategy_name=strategy_name,
                record=payload,
            )
            if kind == "snapshots":
                self._mode_store.append_operation(
                    mode=self._mode,
                    strategy_name=strategy_name,
                    action="daily_snapshot",
                    timestamp=payload.get("timestamp") or payload.get("date"),
                    source="recorder",
                    payload={"snapshot": payload},
                    unique=True,
                )

    def _path(self, kind: str, trading_date: str) -> Path:
        return self.base_dir / trading_date / f"{kind}.jsonl"

    def _read_all(self, kind: str, strategy_name: Optional[str], days: int) -> List[Dict[str, Any]]:
        if not self.base_dir.exists():
            return []
        records: List[Dict[str, Any]] = []
        day_dirs = sorted([p for p in self.base_dir.iterdir() if p.is_dir()])[-days:]
        for day_dir in day_dirs:
            records.extend(self.read_day(kind, day_dir.name, strategy_name=strategy_name))
        return records

    def _closed_trades(self, fills: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        lots: Dict[str, List[List[Any]]] = {}
        trades: List[Dict[str, Any]] = []
        for fill in sorted(fills, key=lambda item: item.get("timestamp", "")):
            symbol = fill.get("symbol", "")
            side = str(fill.get("side", "")).upper()
            qty = float(fill.get("quantity", 0.0) or 0.0)
            price = float(fill.get("price", 0.0) or 0.0)
            commission = float(fill.get("commission", 0.0) or 0.0)
            if side == "BUY":
                commission_per_share = commission / qty if qty > 0 else 0.0
                lots.setdefault(symbol, []).append([qty, price, commission_per_share])
                continue
            if side != "SELL":
                continue
            remaining = qty
            pnl = 0.0
            closed_qty = 0.0
            symbol_lots = lots.setdefault(symbol, [])
            while remaining > 1e-9 and symbol_lots:
                lot_qty, lot_price, commission_per_share = symbol_lots[0]
                take = min(lot_qty, remaining)
                sell_commission = commission * (take / qty) if qty > 0 else 0.0
                pnl += (price - lot_price) * take - commission_per_share * take - sell_commission
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

    def _trades_from_fills(self, fills: Iterable[Dict[str, Any]]) -> List[Trade]:
        lots: Dict[str, List[List[Any]]] = {}
        trades: List[Trade] = []
        for fill in sorted(fills, key=lambda item: item.get("timestamp", "")):
            symbol = str(fill.get("symbol", "") or "")
            side = str(fill.get("side", "") or "").upper()
            qty = float(fill.get("quantity", 0.0) or 0.0)
            price = float(fill.get("price", 0.0) or 0.0)
            commission = float(fill.get("commission", 0.0) or 0.0)
            timestamp = self._parse_datetime(fill.get("timestamp"))
            strategy_name = fill.get("strategy_name")
            if not symbol or qty <= 0 or price <= 0:
                continue
            if side == "BUY":
                lots.setdefault(symbol, []).append([qty, price, timestamp])
                trades.append(Trade(
                    symbol=symbol,
                    quantity=qty,
                    entry_price=price,
                    exit_price=price,
                    entry_time=timestamp,
                    exit_time=timestamp,
                    side="BUY",
                    pnl=-commission,
                    commission=commission,
                    realized_pnl=-commission,
                    fill_date=timestamp,
                    fill_price=price,
                    strategy_name=strategy_name,
                ))
                continue
            if side != "SELL":
                continue
            remaining = qty
            symbol_lots = lots.setdefault(symbol, [])
            while remaining > 1e-9 and symbol_lots:
                lot_qty, lot_price, lot_timestamp = symbol_lots[0]
                take = min(lot_qty, remaining)
                sell_commission = commission * (take / qty) if qty > 0 else 0.0
                realized = (price - lot_price) * take
                trades.append(Trade(
                    symbol=symbol,
                    quantity=take,
                    entry_price=lot_price,
                    exit_price=price,
                    entry_time=lot_timestamp,
                    exit_time=timestamp,
                    side="SELL",
                    pnl=realized - sell_commission,
                    commission=sell_commission,
                    realized_pnl=realized,
                    fill_date=timestamp,
                    fill_price=price,
                    strategy_name=strategy_name,
                ))
                lot_qty -= take
                remaining -= take
                if lot_qty <= 1e-9:
                    symbol_lots.pop(0)
                else:
                    symbol_lots[0][0] = lot_qty
        return trades

    def _slippage_stats(
        self,
        fills: Iterable[Dict[str, Any]],
        orders: Iterable[Dict[str, Any]],
        signals: Iterable[Dict[str, Any]],
    ) -> Dict[str, Any]:
        references: Dict[str, float] = {}
        self._add_reference_prices(references, signals)
        self._add_reference_prices(references, orders)
        samples: List[float] = []
        weighted_sum = 0.0
        weight_total = 0.0
        for fill in fills:
            order_id = str(fill.get("order_id") or "")
            reference_price = references.get(order_id)
            fill_price = self._positive_float(fill.get("price"))
            quantity = self._positive_float(fill.get("quantity"))
            side = str(fill.get("side") or "").upper()
            if not order_id or reference_price is None or fill_price is None or quantity is None:
                continue
            if side == "BUY":
                slippage_bps = (fill_price - reference_price) / reference_price * 10000.0
            elif side == "SELL":
                slippage_bps = (reference_price - fill_price) / reference_price * 10000.0
            else:
                continue
            weight = abs(quantity * fill_price)
            samples.append(slippage_bps)
            if weight > 0:
                weighted_sum += slippage_bps * weight
                weight_total += weight
        return {
            "median_slippage_bps": round(self._median(samples), 6) if samples else None,
            "weighted_avg_slippage_bps": round(weighted_sum / weight_total, 6) if weight_total > 0 else None,
            "slippage_sample_count": len(samples),
        }

    def _add_reference_prices(self, references: Dict[str, float], records: Iterable[Dict[str, Any]]) -> None:
        for record in records:
            price = self._positive_float(record.get("price"))
            if price is None:
                continue
            for key in ("order_id", "broker_order_id"):
                order_id = str(record.get(key) or "")
                if order_id:
                    references[order_id] = price

    @staticmethod
    def _positive_float(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    @staticmethod
    def _median(values: List[float]) -> float:
        ordered = sorted(values)
        count = len(ordered)
        middle = count // 2
        if count % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2.0

    def _equity_curve_from_snapshots(self, snapshots: Iterable[Dict[str, Any]]) -> pd.Series:
        values = []
        index = []
        for snapshot in snapshots:
            nav = float(snapshot.get("nav", 0.0) or 0.0)
            if nav <= 0:
                continue
            index.append(self._parse_datetime(snapshot.get("timestamp") or snapshot.get("date")))
            values.append(nav)
        return pd.Series(values, index=pd.DatetimeIndex(index), dtype=float).sort_index()

    def _latest_daily_snapshots(self, snapshots: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        by_date: Dict[str, Dict[str, Any]] = {}
        for snap in snapshots:
            key = snap.get("date") or str(snap.get("timestamp", ""))[:10]
            by_date[key] = snap
        return [by_date[key] for key in sorted(by_date.keys())]

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day)
        return datetime.fromisoformat(str(value))

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
