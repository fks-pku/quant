#!/usr/bin/env python3
"""Backfill paper records from same-day live strategy signals."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import duckdb
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.domain.models.order import Order, OrderSide, OrderType
from quant.features.portfolio.tracker import StrategyPositionTracker
from quant.infrastructure.execution.live_recorder import LiveTradingRecorder
from quant.infrastructure.execution.strategy_state_store import StrategyStateStore


LIVE_DIR = ROOT / "quant" / "infrastructure" / "var" / "live_trading"
PAPER_DIR = ROOT / "quant" / "infrastructure" / "var" / "paper_trading"
DUCKDB_DIR = ROOT / "quant" / "infrastructure" / "var" / "duckdb" / "live"
DEFAULT_PAPER_SLIPPAGE_BPS = 5.0


@dataclass(frozen=True)
class ExecutionBar:
    symbol: str
    trading_date: date
    open: float
    close: float
    source: str


def backfill_paper_from_live_records(
    trading_date: str,
    *,
    root: Path = ROOT,
    force: bool = False,
) -> Dict[str, Any]:
    live_dir = root / "quant" / "infrastructure" / "var" / "live_trading"
    paper_dir = root / "quant" / "infrastructure" / "var" / "paper_trading"
    state_store = StrategyStateStore(root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb")
    live_recorder = LiveTradingRecorder(live_dir)

    signals = [
        signal for signal in live_recorder.read_day("signals", trading_date)
        if _is_replayable_live_signal(signal)
    ]
    target_order_ids = {
        f"PAPER-{str(signal.get('order_id') or '')}" if signal.get("order_id") else _fallback_order_id(signal)
        for signal in signals
    }
    if force and target_order_ids:
        state_store.delete_signals_for_orders(mode="paper", order_ids=target_order_ids, signal_date=trading_date)
        _rebuild_positions_from_paper_fills(state_store)

    paper_recorder = LiveTradingRecorder(paper_dir)
    tracker = StrategyPositionTracker(store=state_store, mode="paper")
    slippage_bps = _load_paper_slippage_bps(root)
    existing_order_ids = {
        str(record.get("order_id"))
        for kind in ("signals", "orders", "fills")
        for record in paper_recorder.read_day(kind, trading_date)
        if record.get("order_id")
    }
    written = 0
    skipped = 0
    rejected = 0
    filled = 0
    strategy_names: set[str] = set()

    for signal in signals:
        strategy_name = str(signal.get("strategy_name") or "default")
        live_signal_id = str(signal.get("order_id") or "")
        paper_order_id = f"PAPER-{live_signal_id}" if live_signal_id else _fallback_order_id(signal)
        if paper_order_id in existing_order_ids and not force:
            skipped += 1
            strategy_names.add(strategy_name)
            continue

        symbol = str(signal.get("symbol") or "")
        side = str(signal.get("side") or "").upper()
        quantity = _float(signal.get("quantity"))
        order_type = str(signal.get("order_type") or "MARKET").upper()
        limit_price = _optional_float(signal.get("reference_price") if signal.get("reference_price") is not None else signal.get("price"))
        timestamp = _parse_timestamp(signal.get("timestamp"), trading_date)
        bar = _load_execution_bar(root, symbol, trading_date)
        fill_price, reason = _paper_fill_price(
            side=side,
            order_type=order_type,
            limit_price=limit_price,
            bar=bar,
            slippage_bps=slippage_bps,
        )

        paper_recorder.record_signal(
            timestamp=timestamp,
            strategy_name=strategy_name,
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            price=limit_price,
            status=str(signal.get("status") or "accepted"),
            order_id=paper_order_id,
            reason=str(signal.get("reason") or ""),
        )
        order = Order(
            symbol=symbol,
            quantity=quantity,
            side=OrderSide(side),
            order_type=OrderType(order_type),
            order_id=paper_order_id,
            price=limit_price,
            timestamp=timestamp,
            strategy_name=strategy_name,
        )
        paper_recorder.record_order(
            order,
            broker_order_id=paper_order_id,
            status="submitted" if fill_price is not None else "rejected",
            reason=reason,
            timestamp=timestamp,
        )
        written += 1
        strategy_names.add(strategy_name)

        if fill_price is None:
            rejected += 1
            continue
        fill_ts = timestamp + timedelta(seconds=1)
        paper_recorder.record_fill(
            order_id=paper_order_id,
            timestamp=fill_ts,
            strategy_name=strategy_name,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=fill_price,
            commission=0.0,
            fill_id=f"{paper_order_id}-FILL",
        )
        tracker.record_order(paper_order_id, strategy_name)
        tracker.update_from_fill(strategy_name, symbol, side, quantity, fill_price)
        filled += 1

    _record_snapshots(
        paper_recorder=paper_recorder,
        tracker=tracker,
        root=root,
        trading_date=trading_date,
        strategy_names=strategy_names,
    )
    return {
        "trading_date": trading_date,
        "signals": len(signals),
        "written": written,
        "filled": filled,
        "rejected": rejected,
        "skipped": skipped,
    }


def _paper_fill_price(
    *,
    side: str,
    order_type: str,
    limit_price: Optional[float],
    bar: Optional[ExecutionBar],
    slippage_bps: float = DEFAULT_PAPER_SLIPPAGE_BPS,
) -> tuple[Optional[float], str]:
    if bar is None or bar.open <= 0:
        return None, "missing_execution_open"
    if order_type == "LIMIT":
        if limit_price is None or limit_price <= 0:
            return None, "missing_limit_price"
        if side == "BUY" and bar.open > limit_price:
            return None, "limit_not_marketable"
        if side == "SELL" and bar.open < limit_price:
            return None, "limit_not_marketable"
        return bar.open, ""
    fill_price = _apply_slippage(bar.open, side, slippage_bps)
    return fill_price, ""


def _apply_slippage(price: float, side: str, slippage_bps: float) -> float:
    bps = max(float(slippage_bps or 0.0), 0.0)
    adjustment = price * (bps / 10000.0)
    if side.upper() == "BUY":
        return price + adjustment
    return price - adjustment


def _is_replayable_live_signal(signal: Dict[str, Any]) -> bool:
    order_id = str(signal.get("order_id") or "")
    broker_order_id = str(signal.get("broker_order_id") or "")
    status = str(signal.get("status") or "").lower()
    if broker_order_id and broker_order_id == order_id and status in {"submitted", "filled", "cancelled", "canceled"}:
        return False
    return True


def _load_paper_slippage_bps(root: Path) -> float:
    config_path = root / "quant" / "infrastructure" / "var" / "paper_config" / "config.yaml"
    if not config_path.exists():
        return DEFAULT_PAPER_SLIPPAGE_BPS
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return float(data.get("execution", {}).get("slippage_bps", DEFAULT_PAPER_SLIPPAGE_BPS))
    except Exception:
        return DEFAULT_PAPER_SLIPPAGE_BPS


def _load_execution_bar(root: Path, symbol: str, trading_date: str) -> Optional[ExecutionBar]:
    normalized = str(symbol).split(".")[0]
    sources = [
        ("stock", root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_ohlcv.duckdb"),
        ("etf", root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_etf_ohlcv.duckdb"),
        ("index", root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_index_ohlcv.duckdb"),
    ]
    for source, path in sources:
        if not path.exists():
            continue
        with duckdb.connect(str(path), read_only=True) as con:
            row = con.execute(
                """
                select cast(timestamp as date), symbol, open, close
                from daily_cn_ochl
                where symbol = ? and cast(timestamp as date) = ?
                limit 1
                """,
                [normalized, trading_date],
            ).fetchone()
        if row is not None:
            return ExecutionBar(
                symbol=str(row[1]),
                trading_date=row[0],
                open=_float(row[2]),
                close=_float(row[3]),
                source=source,
            )
    return None


def _record_snapshots(
    *,
    paper_recorder: LiveTradingRecorder,
    tracker: StrategyPositionTracker,
    root: Path,
    trading_date: str,
    strategy_names: Iterable[str],
) -> None:
    breakdown = tracker.get_breakdown()
    snapshot_ts = datetime.combine(datetime.fromisoformat(trading_date).date(), time(15, 0))
    for strategy_name in sorted(set(strategy_names)):
        data = breakdown.get(strategy_name, {})
        holdings = data.get("holdings", [])
        market_value = 0.0
        unrealized = 0.0
        for holding in holdings:
            symbol = str(holding.get("symbol") or "")
            bar = _load_execution_bar(root, symbol, trading_date)
            close = bar.close if bar is not None and bar.close > 0 else _float(holding.get("avg_cost"))
            qty = _float(holding.get("qty"))
            avg_cost = _float(holding.get("avg_cost"))
            market_value += qty * close
            unrealized += (close - avg_cost) * qty
        realized = _float(data.get("total_realized_pnl"))
        if market_value <= 0 and realized == 0 and unrealized == 0:
            continue
        paper_recorder.record_strategy_snapshot(
            timestamp=snapshot_ts,
            strategy_name=strategy_name,
            nav=market_value,
            market_value=market_value,
            cash=0.0,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
        )


def _rebuild_positions_from_paper_fills(store: StrategyStateStore) -> None:
    store.clear_positions_for_mode(mode="paper")
    fills = [
        signal for signal in store.get_recent_signals(mode="paper", days=3650)
        if _float(signal.get("fill_quantity")) > 0
    ]
    for fill in sorted(fills, key=lambda item: str(item.get("fill_time") or item.get("timestamp") or "")):
        _apply_fill_to_position(store, fill)


def _apply_fill_to_position(store: StrategyStateStore, fill: Dict[str, Any]) -> None:
    strategy_name = str(fill.get("strategy_name") or "default")
    symbol = str(fill.get("symbol") or "")
    side = str(fill.get("side") or "").upper()
    quantity = _float(fill.get("fill_quantity") or fill.get("quantity"))
    price = _float(fill.get("fill_price") or fill.get("price"))
    commission = _float(fill.get("commission"))
    if not symbol or quantity <= 0 or price <= 0:
        return
    current = store.get_position(strategy_name=strategy_name, mode="paper", symbol=symbol)
    current_qty = _float((current or {}).get("quantity"))
    current_avg = _float((current or {}).get("avg_cost"))
    current_rpnl = _float((current or {}).get("realized_pnl"))
    if side == "BUY":
        new_qty = current_qty + quantity
        total_cost = (current_avg * current_qty) + (price * quantity) + commission
        new_avg = total_cost / new_qty if new_qty > 0 else 0.0
        new_rpnl = current_rpnl
    elif side == "SELL":
        new_qty = max(0.0, current_qty - quantity)
        realized = (price - current_avg) * min(quantity, current_qty) - commission if current_qty > 0 else 0.0
        new_avg = current_avg if new_qty > 0 else 0.0
        new_rpnl = current_rpnl + realized
    else:
        return
    if new_qty <= 0:
        store.delete_position(strategy_name=strategy_name, mode="paper", symbol=symbol)
    else:
        store.upsert_position(
            strategy_name=strategy_name,
            mode="paper",
            symbol=symbol,
            quantity=new_qty,
            avg_cost=new_avg,
            realized_pnl=new_rpnl,
            updated_at=str(fill.get("fill_time") or fill.get("timestamp") or datetime.now().isoformat()),
        )


def _fallback_order_id(signal: Dict[str, Any]) -> str:
    timestamp = str(signal.get("timestamp") or "").replace(":", "").replace("-", "")
    symbol = str(signal.get("symbol") or "UNKNOWN")
    return f"PAPER-{timestamp}-{symbol}"


def _parse_timestamp(value: Any, trading_date: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if value:
        return datetime.fromisoformat(str(value))
    return datetime.combine(datetime.fromisoformat(trading_date).date(), time(9, 30))


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    result = backfill_paper_from_live_records(args.date, force=args.force)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
