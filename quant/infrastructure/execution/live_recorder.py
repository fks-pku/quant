"""File-backed live trading recorder for strategy signals, fills, and performance."""

import json
import hashlib
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
from quant.infrastructure.execution.strategy_state_store import StrategyStateStore, _nullable_float
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
    def __init__(self, base_dir: Optional[Path] = None, db_path: Optional[Path] = None):
        self.base_dir = Path(base_dir) if base_dir is not None else _DEFAULT_BASE_DIR
        self._mode = "paper" if self.base_dir.name == "paper_trading" else "live"
        if db_path is not None:
            self._state_store = StrategyStateStore(db_path)
        else:
            self._state_store = StrategyStateStore(self.base_dir.parent / "strategy_dashboard.duckdb")
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

    def record_capital_event(
        self,
        *,
        strategy_name: str,
        event_type: str,
        symbol: str = "",
        amount: float = 0.0,
        quantity: float = 0.0,
        price: Optional[float] = None,
        effective_date: Optional[str] = None,
        note: str = "",
        timestamp: Optional[datetime] = None,
    ) -> None:
        ts = timestamp or datetime.now()
        eff_date = effective_date or ts.date().isoformat()
        self._state_store.record_capital_event(
            strategy_name=strategy_name,
            mode=self._mode,
            event_type=event_type,
            symbol=symbol,
            amount=amount,
            quantity=quantity,
            price=price,
            effective_date=eff_date,
            note=note,
            recorded_at=ts.isoformat(),
            apply_to_positions=True,
        )

    def record_dividend(
        self,
        *,
        strategy_name: str,
        symbol: str,
        dividend_type: str,
        cash_per_share: float = 0.0,
        stock_ratio: float = 0.0,
        effective_date: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> None:
        ts = timestamp or datetime.now()
        eff_date = effective_date or ts.date().isoformat()
        positions = self._state_store.get_positions(
            strategy_name=strategy_name, mode=self._mode,
        )
        pos = next((p for p in positions if p.get("symbol") == symbol), None)
        qty = float((pos or {}).get("quantity", 0.0))
        if dividend_type == "cash" and cash_per_share > 0 and qty > 0:
            self.record_capital_event(
                strategy_name=strategy_name,
                event_type="DIVIDEND_CASH",
                symbol=symbol,
                amount=cash_per_share * qty,
                effective_date=eff_date,
                note=f"Cash dividend: {cash_per_share}/share × {qty}sh",
                timestamp=ts,
            )
        elif dividend_type == "stock" and stock_ratio > 0 and qty > 0:
            new_shares = qty * stock_ratio
            self.record_capital_event(
                strategy_name=strategy_name,
                event_type="DIVIDEND_STOCK",
                symbol=symbol,
                quantity=new_shares,
                effective_date=eff_date,
                note=f"Stock dividend: {stock_ratio*100:.1f}% → +{new_shares}sh",
                timestamp=ts,
            )

    def read_day(
        self,
        kind: str,
        trading_date: str,
        strategy_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        name = strategy_name or "default"
        date_str = str(trading_date)[:10]
        if kind == "snapshots":
            if strategy_name is None:
                snapshots = self._state_store.get_all_snapshots_for_mode(mode=self._mode, limit=365)
            else:
                snapshots = self._state_store.get_snapshots(strategy_name=name, mode=self._mode)
            return [s for s in snapshots if str(s.get("snapshot_date", ""))[:10] == date_str]
        all_signals = self._state_store.get_signals(strategy_name=name, mode=self._mode, limit=10000)
        if strategy_name is None:
            all_signals = self._state_store.get_recent_signals(mode=self._mode, days=365)
        if kind == "signals":
            result = [s for s in all_signals if str(s.get("signal_date", ""))[:10] == date_str]
            if strategy_name is not None:
                result = [s for s in result if s.get("strategy_name") == strategy_name]
            return _sort_records(_public_signal_record(record) for record in result)
        if kind == "orders":
            return _sort_records(
                _public_order_record(record)
                for record in all_signals
                if record.get("order_id") and str(record.get("signal_date", ""))[:10] == date_str
            )
        if kind == "fills":
            return _sort_records(
                _public_fill_record(record)
                for record in all_signals
                if record.get("fill_quantity", 0) > 0 and str(record.get("signal_date", ""))[:10] == date_str
            )
        return []

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
        payload = self._jsonable(record)
        with self._lock:
            strategy_name = str(payload.get("strategy_name") or "default")
            signal_date = timestamp.date().isoformat()

            if kind == "signals":
                signal_id = _make_signal_id(payload, self._mode, signal_date)
                submit_date = str(payload.get("submit_date") or payload.get("execution_date") or "")[:10]
                self._state_store.upsert_signal(signal={
                    "signal_id": signal_id,
                    "strategy_name": strategy_name,
                    "mode": self._mode,
                    "timestamp": _ts(payload.get("timestamp")),
                    "signal_date": signal_date,
                    "symbol": str(payload.get("symbol") or ""),
                    "side": str(payload.get("side") or ""),
                    "quantity": float(payload.get("quantity", 0.0)),
                    "order_type": str(payload.get("order_type") or ""),
                        "reference_price": payload.get("reference_price") if payload.get("reference_price") is not None else payload.get("price"),
                    "status": str(payload.get("status") or "generated"),
                    "order_id": str(payload.get("order_id") or ""),
                    "failure_reason": str(payload.get("reason") or ""),
                    "submit_date": submit_date,
                    "record_date": signal_date,
                    "cost_bps": _nullable_float(payload.get("cost_bps") or payload.get("execution_cost_bps")),
                })
            elif kind == "orders":
                order_id = str(payload.get("order_id") or "")
                broker_order_id = str(payload.get("broker_order_id") or "")
                signal = self._state_store.get_signal_by_order(
                    mode=self._mode, order_id=order_id, signal_date=signal_date,
                )
                if not signal and broker_order_id:
                    signal = self._state_store.get_signal_by_order(
                        mode=self._mode, order_id=broker_order_id, signal_date=signal_date,
                    )
                if not signal and not order_id and not broker_order_id:
                    signal = self._state_store.get_signal_by_signature(
                        mode=self._mode,
                        strategy_name=strategy_name,
                        symbol=str(payload.get("symbol") or ""),
                        side=str(payload.get("side") or ""),
                        quantity=float(payload.get("quantity", 0.0)),
                        signal_date=signal_date,
                        order_type=str(payload.get("order_type") or ""),
                    )
                if not signal:
                    signal_id = _make_signal_id_from_order(payload, self._mode)
                    submit_date = str(payload.get("submit_date") or payload.get("execution_date") or "")[:10]
                    self._state_store.upsert_signal(signal={
                        "signal_id": signal_id,
                        "strategy_name": strategy_name,
                        "mode": self._mode,
                        "timestamp": _ts(payload.get("timestamp")),
                        "signal_date": signal_date,
                        "symbol": str(payload.get("symbol") or ""),
                        "side": str(payload.get("side") or ""),
                        "quantity": float(payload.get("quantity", 0.0)),
                        "order_type": str(payload.get("order_type") or ""),
                        "reference_price": payload.get("reference_price") if payload.get("reference_price") is not None else payload.get("price"),
                        "status": str(payload.get("status") or "submitted"),
                        "order_id": order_id,
                        "broker_order_id": broker_order_id,
                        "failure_reason": str(payload.get("reason") or ""),
                        "submit_date": submit_date,
                        "record_date": signal_date,
                        "cost_bps": _nullable_float(payload.get("cost_bps") or payload.get("execution_cost_bps")),
                    })
                else:
                    self._state_store.update_signal_order(
                        signal_id=str(signal.get("signal_id") or ""),
                        order_id=order_id,
                        broker_order_id=broker_order_id,
                        status=str(payload.get("status") or "submitted"),
                        failure_reason=str(payload.get("reason") or ""),
                    )
            elif kind == "fills":
                order_id = str(payload.get("order_id") or "")
                signal = self._state_store.get_signal_by_order(
                    mode=self._mode, order_id=order_id, signal_date=signal_date,
                )
                if not signal:
                    signal = self._state_store.get_signal_by_signature(
                        mode=self._mode,
                        strategy_name=strategy_name,
                        symbol=str(payload.get("symbol") or ""),
                        side=str(payload.get("side") or ""),
                        quantity=float(payload.get("quantity", 0.0)),
                        signal_date=signal_date,
                    )
                fill_qty = float(payload.get("quantity", 0.0))
                fill_price = float(payload.get("price", 0.0))
                commission = float(payload.get("commission", 0.0))
                fill_time = _ts(payload.get("timestamp"))
                fill_date = str(payload.get("timestamp"))[:10] if isinstance(payload.get("timestamp"), datetime) else signal_date
                if signal:
                    self._state_store.update_signal_fill(
                        signal_id=str(signal.get("signal_id") or ""),
                        fill_quantity=fill_qty,
                        fill_price=fill_price,
                        commission=commission,
                        fill_time=fill_time,
                        status="filled",
                    )
                else:
                    signal_id = _make_signal_id_from_order(payload, self._mode)
                    self._state_store.upsert_signal(signal={
                        "signal_id": signal_id,
                        "strategy_name": strategy_name,
                        "mode": self._mode,
                        "timestamp": _ts(payload.get("timestamp")),
                        "signal_date": signal_date,
                        "symbol": str(payload.get("symbol") or ""),
                        "side": str(payload.get("side") or "").upper(),
                        "quantity": fill_qty,
                        "order_type": "",
                        "reference_price": fill_price,
                        "status": "filled",
                        "order_id": order_id,
                        "broker_order_id": "",
                        "fill_quantity": fill_qty,
                        "fill_price": fill_price,
                        "commission": commission,
                        "fill_time": fill_time,
                        "failure_reason": "",
                        "submit_date": fill_date[:10],
                        "record_date": signal_date,
                        "cost_bps": None,
                    })
                symbol = str(payload.get("symbol") or "")
                side = str(payload.get("side") or "").upper()
                self._update_position(
                    strategy_name=strategy_name,
                    symbol=symbol,
                    side=side,
                    quantity=fill_qty,
                    price=fill_price,
                    commission=commission,
                    fill_time=fill_time,
                )
            elif kind == "snapshots":
                snap_date = str(payload.get("date") or signal_date)[:10]
                self._state_store.upsert_snapshot(
                    strategy_name=strategy_name,
                    mode=self._mode,
                    snapshot_date=snap_date,
                    nav=float(payload.get("nav", 0.0)),
                    cash=float(payload.get("cash", 0.0)),
                    market_value=float(payload.get("market_value", 0.0)),
                    realized_pnl=float(payload.get("realized_pnl", 0.0)),
                    unrealized_pnl=float(payload.get("unrealized_pnl", 0.0)),
                    total_pnl=float(payload.get("total_pnl", 0.0)),
                    source=str(payload.get("source") or self._mode),
                    recorded_at=_ts(payload.get("timestamp")),
                )

    def _read_all(self, kind: str, strategy_name: Optional[str], days: int) -> List[Dict[str, Any]]:
        name = strategy_name or "default"
        if kind == "snapshots":
            if strategy_name is None:
                return self._state_store.get_all_snapshots_for_mode(mode=self._mode, limit=days)
            return self._state_store.get_snapshots(strategy_name=name, mode=self._mode, limit=days)
        if strategy_name is None:
            base_signals = self._state_store.get_recent_signals(mode=self._mode, days=days)
        else:
            base_signals = self._state_store.get_signals(strategy_name=name, mode=self._mode, limit=max(days * 10, 100))
        if kind == "signals":
            return base_signals
        if kind == "orders":
            return [s for s in base_signals if s.get("order_id")]
        if kind == "fills":
            return [s for s in base_signals if s.get("fill_quantity", 0) > 0]
        return []

    def _closed_trades(self, fills: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        lots: Dict[str, List[List[Any]]] = {}
        trades: List[Dict[str, Any]] = []
        for fill in sorted(fills, key=lambda item: item.get("timestamp", "")):
            symbol = fill.get("symbol", "")
            side = str(fill.get("side", "")).upper()
            qty = float(fill.get("fill_quantity") or fill.get("quantity", 0.0) or 0.0)
            price = float(fill.get("fill_price") or fill.get("price", 0.0) or 0.0)
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
            qty = float(fill.get("fill_quantity") or fill.get("quantity", 0.0) or 0.0)
            price = float(fill.get("fill_price") or fill.get("price", 0.0) or 0.0)
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
            fill_price = self._positive_float(fill.get("fill_price") or fill.get("price"))
            quantity = self._positive_float(fill.get("fill_quantity") or fill.get("quantity"))
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
            price = self._positive_float(record.get("reference_price") or record.get("price"))
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
            index.append(self._parse_datetime(snapshot.get("timestamp") or snapshot.get("date") or snapshot.get("snapshot_date")))
            values.append(nav)
        return pd.Series(values, index=pd.DatetimeIndex(index), dtype=float).sort_index()

    def _latest_daily_snapshots(self, snapshots: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        by_date: Dict[str, Dict[str, Any]] = {}
        for snap in snapshots:
            key = snap.get("date") or snap.get("snapshot_date") or str(snap.get("timestamp", ""))[:10]
            normalized = dict(snap)
            normalized.setdefault("date", key)
            normalized.setdefault("timestamp", normalized.get("recorded_at", normalized.get("timestamp", "")))
            by_date[key] = normalized
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

    def _update_position(
        self,
        strategy_name: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        commission: float,
        fill_time: str,
    ) -> None:
        current = self._state_store.get_position(
            strategy_name=strategy_name, mode=self._mode, symbol=symbol,
        )
        current_qty = float((current or {}).get("quantity", 0.0))
        current_avg = float((current or {}).get("avg_cost", 0.0))
        current_rpnl = float((current or {}).get("realized_pnl", 0.0))
        if side == "BUY":
            new_qty = current_qty + quantity
            total_cost = (current_avg * current_qty) + (price * quantity) + commission
            new_avg = total_cost / new_qty if new_qty > 0 else 0.0
            new_rpnl = current_rpnl
        else:
            new_qty = max(0.0, current_qty - quantity)
            if current_qty > 0:
                realized = (price - current_avg) * min(quantity, current_qty) - commission
            else:
                realized = 0.0
            new_rpnl = current_rpnl + realized
            new_avg = current_avg if new_qty > 0 else 0.0
        if new_qty <= 0:
            self._state_store.delete_position(
                strategy_name=strategy_name, mode=self._mode, symbol=symbol,
            )
        else:
            self._state_store.upsert_position(
                strategy_name=strategy_name,
                mode=self._mode,
                symbol=symbol,
                quantity=new_qty,
                avg_cost=new_avg,
                realized_pnl=new_rpnl,
                updated_at=fill_time,
            )


def _ts(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value or datetime.now().isoformat())


def _sort_records(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(records, key=lambda item: str(item.get("timestamp") or item.get("fill_time") or item.get("recorded_at") or ""))


def _public_signal_record(record: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(record)
    row.setdefault("price", row.get("reference_price"))
    row.setdefault("date", row.get("signal_date"))
    return row


def _public_order_record(record: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(record)
    row.setdefault("price", row.get("reference_price"))
    row.setdefault("record_date", row.get("signal_date"))
    return row


def _public_fill_record(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **record,
        "timestamp": record.get("fill_time") or record.get("timestamp", ""),
        "fill_id": record.get("fill_id", ""),
        "order_id": record.get("order_id", ""),
        "strategy_name": record.get("strategy_name", ""),
        "symbol": record.get("symbol", ""),
        "side": record.get("side", ""),
        "quantity": record.get("fill_quantity", 0.0),
        "price": record.get("fill_price", 0.0),
        "commission": record.get("commission", 0.0),
        "value": float(record.get("fill_quantity", 0.0) or 0.0) * float(record.get("fill_price", 0.0) or 0.0),
        "record_date": record.get("signal_date", ""),
    }


def _make_signal_id(payload: Dict[str, Any], mode: str, signal_date: str) -> str:
    parts = [
        str(payload.get("strategy_name") or ""),
        mode,
        signal_date,
        str(payload.get("symbol") or ""),
        str(payload.get("side") or ""),
        str(payload.get("quantity")),
        str(payload.get("order_type") or ""),
        str(payload.get("order_id") or ""),
        _ts(payload.get("timestamp")),
    ]
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True)
    return f"sig:{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}"


def _make_signal_id_from_order(payload: Dict[str, Any], mode: str) -> str:
    parts = [
        str(payload.get("strategy_name") or ""),
        mode,
        str(payload.get("symbol") or ""),
        str(payload.get("side") or ""),
        str(payload.get("quantity")),
        str(payload.get("order_type") or ""),
        str(payload.get("order_id") or ""),
        _ts(payload.get("timestamp")),
    ]
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True)
    return f"sig:{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}"
