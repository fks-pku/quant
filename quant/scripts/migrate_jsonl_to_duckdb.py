"""Migrate historical JSONL/JSON records into DuckDB strategy_dashboard tables.

Migrations:
  signals JSONL                    -> strategy_signals
  orders JSONL                     -> strategy_orders
  fills JSONL                      -> strategy_fills
  snapshots JSONL                  -> strategy_snapshots
  strategy_controls.json              -> strategy_states
  strategy_positions.json (live/paper)-> strategy_positions
"""

import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.infrastructure.execution.strategy_state_store import StrategyStateStore


VAR_DIR = ROOT / "quant" / "infrastructure" / "var"
LIVE_DIR = VAR_DIR / "live_trading"
PAPER_DIR = VAR_DIR / "paper_trading"
LIVE_POSITIONS_PATH = ROOT / "quant" / "features" / "data" / "strategy_positions.json"
PAPER_POSITIONS_PATH = VAR_DIR / "paper_trading" / "strategy_positions.json"
CONTROL_PATH = VAR_DIR / "strategy_controls.json"
VALID_LIFECYCLE_STATES = {"running", "paused", "stopped", "liquidating"}


def migrate_jsonl_to_duckdb(base_dir: Path, mode: str, store: StrategyStateStore) -> Dict[str, int]:
    counts = {"signals": 0, "orders": 0, "fills": 0, "snapshots": 0, "signals_created": 0}
    if not base_dir.exists():
        return counts

    day_dirs = sorted([p for p in base_dir.iterdir() if p.is_dir()])
    print(f"Migrating {len(day_dirs)} days from {base_dir} (mode={mode})")

    for day_dir in day_dirs:
        day_str = day_dir.name
        if len(day_str) < 10:
            continue

        signals = _read_jsonl(day_dir / "signals.jsonl")
        orders = _read_jsonl(day_dir / "orders.jsonl")
        fills = _read_jsonl(day_dir / "fills.jsonl")
        snapshots = _read_jsonl(day_dir / "snapshots.jsonl")

        counts["signals"] += len(signals)
        counts["orders"] += len(orders)
        counts["fills"] += len(fills)
        counts["snapshots"] += len(snapshots)

        for signal_data in signals:
            strategy_name = str(signal_data.get("strategy_name") or "default")
            signal_date = str(signal_data.get("signal_date") or signal_data.get("timestamp") or day_str)[:10]
            signal_id = _make_signal_id(signal_data, mode, signal_date)
            store.upsert_signal(signal={
                "signal_id": signal_id,
                "strategy_name": strategy_name,
                "mode": mode,
                "timestamp": str(signal_data.get("timestamp") or ""),
                "signal_date": signal_date,
                "symbol": str(signal_data.get("symbol") or ""),
                "side": str(signal_data.get("side") or ""),
                "quantity": float(signal_data.get("quantity", 0.0)),
                "order_type": str(signal_data.get("order_type") or ""),
                "reference_price": _nullable_float(signal_data.get("price")),
                "status": str(signal_data.get("status") or "generated"),
                "order_id": str(signal_data.get("order_id") or ""),
                "failure_reason": str(signal_data.get("reason") or ""),
                "submit_date": str(signal_data.get("submit_date") or signal_data.get("execution_date") or "")[:10],
                "record_date": signal_date,
            })
            counts["signals_created"] += 1

        for order_data in orders:
            order_id = str(order_data.get("order_id") or "")
            broker_order_id = str(order_data.get("broker_order_id") or "")
            strategy_name = str(order_data.get("strategy_name") or "default")
            order_date = str(order_data.get("timestamp") or day_str)[:10]

            signal = store.get_signal_by_order(mode=mode, order_id=order_id, signal_date=order_date)
            if not signal and broker_order_id:
                signal = store.get_signal_by_order(mode=mode, order_id=broker_order_id, signal_date=order_date)
            if not signal and not order_id and not broker_order_id:
                signal = store.get_signal_by_signature(
                    mode=mode, strategy_name=strategy_name,
                    symbol=str(order_data.get("symbol") or ""),
                    side=str(order_data.get("side") or ""),
                    quantity=float(order_data.get("quantity", 0.0)),
                    signal_date=order_date,
                    order_type=str(order_data.get("order_type") or ""),
                )
            if not signal:
                signal = store.get_signal_for_submission(
                    mode=mode,
                    strategy_name=strategy_name,
                    symbol=str(order_data.get("symbol") or ""),
                    side=str(order_data.get("side") or ""),
                    quantity=float(order_data.get("quantity", 0.0)),
                    submit_date=order_date,
                )
            store.upsert_order(order={
                "signal_id": str((signal or {}).get("signal_id") or ""),
                "strategy_name": strategy_name,
                "mode": mode,
                "timestamp": str(order_data.get("timestamp") or ""),
                "signal_date": str((signal or {}).get("signal_date") or ""),
                "submit_date": str(order_data.get("submit_date") or order_data.get("execution_date") or order_date)[:10],
                "record_date": order_date,
                "symbol": str(order_data.get("symbol") or ""),
                "side": str(order_data.get("side") or ""),
                "quantity": float(order_data.get("quantity", 0.0)),
                "order_type": str(order_data.get("order_type") or ""),
                "price": order_data.get("price"),
                "status": str(order_data.get("status") or "submitted"),
                "order_id": order_id,
                "broker_order_id": broker_order_id,
                "failure_reason": str(order_data.get("reason") or ""),
                "cost_bps": _nullable_float(order_data.get("cost_bps") or order_data.get("execution_cost_bps")),
                "execution_reference_price": _nullable_float(order_data.get("execution_reference_price")),
            })

        for fill_data in fills:
            order_id = str(fill_data.get("order_id") or "")
            strategy_name = str(fill_data.get("strategy_name") or "default")
            fill_date = str(fill_data.get("timestamp") or day_str)[:10]
            signal = store.get_signal_by_order(mode=mode, order_id=order_id, signal_date=fill_date)
            if not signal and not order_id:
                signal = store.get_signal_for_submission(
                    mode=mode, strategy_name=strategy_name,
                    symbol=str(fill_data.get("symbol") or ""),
                    side=str(fill_data.get("side") or ""),
                    quantity=float(fill_data.get("quantity", 0.0)),
                    submit_date=fill_date,
                )
            order = store.get_order_by_order_id(
                mode=mode,
                order_id=order_id,
                record_date=fill_date,
                strategy_name=strategy_name,
                symbol=str(fill_data.get("symbol") or ""),
                side=str(fill_data.get("side") or ""),
            )
            store.upsert_fill(fill={
                "fill_id": str(fill_data.get("fill_id") or fill_data.get("trade_id") or ""),
                "order_row_id": str((order or {}).get("order_row_id") or ""),
                "signal_id": str((signal or {}).get("signal_id") or (order or {}).get("signal_id") or ""),
                "strategy_name": strategy_name,
                "mode": mode,
                "timestamp": str(fill_data.get("timestamp") or ""),
                "signal_date": str((signal or {}).get("signal_date") or (order or {}).get("signal_date") or ""),
                "record_date": fill_date,
                "symbol": str(fill_data.get("symbol") or ""),
                "side": str(fill_data.get("side") or ""),
                "quantity": float(fill_data.get("quantity", 0.0)),
                "price": float(fill_data.get("price", 0.0)),
                "commission": float(fill_data.get("commission", 0.0)),
                "order_id": order_id,
                "broker_order_id": str((order or {}).get("broker_order_id") or ""),
                "source": "jsonl_migration",
            })

        for snap_data in snapshots:
            strategy_name = str(snap_data.get("strategy_name") or "default")
            store.upsert_snapshot(
                strategy_name=strategy_name,
                mode=mode,
                snapshot_date=str(snap_data.get("date") or snap_data.get("timestamp") or day_str)[:10],
                nav=float(snap_data.get("nav", 0.0)),
                cash=float(snap_data.get("cash", 0.0)),
                market_value=float(snap_data.get("market_value", 0.0)),
                realized_pnl=float(snap_data.get("realized_pnl", 0.0)),
                unrealized_pnl=float(snap_data.get("unrealized_pnl", 0.0)),
                total_pnl=float(snap_data.get("total_pnl", 0.0)),
                source=str(snap_data.get("source") or mode),
                recorded_at=str(snap_data.get("timestamp") or ""),
            )

    return counts


def migrate_control_states(path: Path, store: StrategyStateStore) -> Dict[str, int]:
    counts = {"live": 0, "paper": 0}
    if not path.exists():
        return counts
    data = json.loads(path.read_text("utf-8"))

    for bucket_key, mode in [("strategies", "live"), ("paper_strategies", "paper")]:
        bucket = data.get(bucket_key, {})
        if not isinstance(bucket, dict):
            continue
        for strategy_name, raw in bucket.items():
            if not isinstance(raw, dict):
                continue
            live_state = str(raw.get("live_state", "running"))
            if live_state not in VALID_LIFECYCLE_STATES:
                live_state = "stopped"
            live_enabled = bool(raw.get("live_enabled", False))
            liquidation = bool(raw.get("liquidation_requested", False))
            updated_at = str(raw.get("updated_at", ""))
            store.record_state(
                strategy_name=strategy_name,
                mode=mode,
                from_state="stopped",
                to_state=live_state,
                signal_enabled=live_enabled,
                submit_enabled=live_enabled and live_state == "running" and not liquidation,
                liquidation_requested=liquidation,
                initial_cash=0.0,
                note=f"migrated from strategy_controls.json (previous: {str(raw.get('note', ''))})",
                recorded_at=updated_at,
            )
            counts[mode] += 1
    return counts


def migrate_positions(
    pos_path: Path,
    mode: str,
    store: StrategyStateStore,
) -> Dict[str, int]:
    counts = {"strategies": 0, "positions": 0}
    if not pos_path.exists():
        return counts
    data = json.loads(pos_path.read_text("utf-8"))
    positions = data.get("positions", {})
    if not isinstance(positions, dict):
        return counts
    for strategy_name, symbols in positions.items():
        if not isinstance(symbols, dict):
            continue
        strat_count = 0
        for symbol, pos_data in symbols.items():
            if not isinstance(pos_data, dict):
                continue
            qty = float(pos_data.get("qty", pos_data.get("quantity", 0.0)))
            avg_cost = float(pos_data.get("avg_cost", 0.0))
            if qty <= 0 and avg_cost <= 0:
                continue
            realized_pnl = float(data.get("realized_pnl", {}).get(strategy_name, 0.0))
            store.upsert_position(
                strategy_name=strategy_name,
                mode=mode,
                symbol=str(symbol),
                quantity=qty,
                avg_cost=avg_cost,
                realized_pnl=realized_pnl,
                updated_at=datetime.now().isoformat(),
            )
            strat_count += 1
            counts["positions"] += 1
        if strat_count > 0:
            counts["strategies"] += 1
    return counts


def migrate_all(target_root: Optional[Path] = None) -> Dict[str, Any]:
    root = target_root or ROOT
    db_path = root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb"
    store = StrategyStateStore(db_path)
    store.ensure_schema()

    live_dir = root / "quant" / "infrastructure" / "var" / "live_trading"
    paper_dir = root / "quant" / "infrastructure" / "var" / "paper_trading"
    live_positions = root / "quant" / "features" / "data" / "strategy_positions.json"
    paper_positions = root / "quant" / "infrastructure" / "var" / "paper_trading" / "strategy_positions.json"
    control_path = root / "quant" / "infrastructure" / "var" / "strategy_controls.json"

    live_jsonl = migrate_jsonl_to_duckdb(live_dir, "live", store)
    paper_jsonl = migrate_jsonl_to_duckdb(paper_dir, "paper", store)
    control_states = migrate_control_states(control_path, store)
    live_pos = migrate_positions(live_positions, "live", store)
    paper_pos = migrate_positions(paper_positions, "paper", store)

    return {
        "live_jsonl": live_jsonl,
        "paper_jsonl": paper_jsonl,
        "control_states": control_states,
        "live_positions": live_pos,
        "paper_positions": paper_pos,
    }


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    return records


def _make_signal_id(payload, mode, signal_date):
    import hashlib
    parts = [
        str(payload.get("strategy_name") or ""), mode, signal_date,
        str(payload.get("symbol") or ""), str(payload.get("side") or ""),
        str(payload.get("quantity")), str(payload.get("order_type") or ""),
        str(payload.get("order_id") or ""), str(payload.get("timestamp") or ""),
    ]
    return f"sig:{hashlib.sha1(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:16]}"


def _make_signal_id_from_order(payload, mode):
    import hashlib
    parts = [
        str(payload.get("strategy_name") or ""), mode,
        str(payload.get("symbol") or ""), str(payload.get("side") or ""),
        str(payload.get("quantity")), str(payload.get("order_type") or ""),
        str(payload.get("order_id") or ""), str(payload.get("timestamp") or ""),
    ]
    return f"sig:{hashlib.sha1(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:16]}"


def _nullable_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    results = migrate_all()

    print("\n--- Migration Summary ---")
    for label, counts in [("live JSONL", results["live_jsonl"]), ("paper JSONL", results["paper_jsonl"])]:
        print(f"{label}: signals={counts['signals']} orders={counts['orders']} "
              f"fills={counts['fills']} snapshots={counts['snapshots']} "
              f"created={counts['signals_created']}")
    print(f"control_states: live={results['control_states']['live']} paper={results['control_states']['paper']}")
    for label, counts in [("live positions", results["live_positions"]), ("paper positions", results["paper_positions"])]:
        print(f"{label}: strategies={counts['strategies']} positions={counts['positions']}")
    print("Done.")
