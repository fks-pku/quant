"""Strategy operation ledger, recovery, and audit helpers."""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from quant.infrastructure.execution.strategy_state_store import StrategyStateStore



def create_liquidation_plan(
    *,
    strategy_name: str,
    mode: str,
    store: StrategyStateStore,
    note: str = "",
    timestamp: Optional[datetime] = None,
) -> Dict[str, Any]:
    control_mode = _mode(mode)
    ts = (timestamp or datetime.now()).isoformat()
    positions = store.get_positions(strategy_name=strategy_name, mode=control_mode)
    orders = []
    for pos in positions:
        qty = _float(pos.get("quantity"))
        if qty <= 0:
            continue
        orders.append({
            "symbol": str(pos.get("symbol", "")),
            "side": "SELL",
            "quantity": qty,
            "avg_cost": _float(pos.get("avg_cost")),
        })
    plan = {
        "strategy_name": strategy_name,
        "mode": control_mode,
        "status": "planned",
        "created_at": ts,
        "updated_at": ts,
        "note": note,
        "orders": orders,
    }
    store.record_state(
        strategy_name=strategy_name,
        mode=control_mode,
        from_state="running",
        to_state="liquidating",
        signal_enabled=False,
        submit_enabled=False,
        liquidation_requested=True,
        note=f"liquidation_plan_created: {note}",
        recorded_at=ts,
    )
    return plan


def read_liquidation_plan(
    *,
    strategy_name: str,
    mode: str,
    store: StrategyStateStore,
) -> Optional[Dict[str, Any]]:
    control_mode = _mode(mode)
    current = store.get_current_state(strategy_name=strategy_name, mode=control_mode)
    if current is None:
        return None
    to_state = str(current.get("to_state", ""))
    if to_state != "liquidating":
        return None
    positions = store.get_positions(strategy_name=strategy_name, mode=control_mode)
    orders = []
    for pos in positions:
        qty = _float(pos.get("quantity"))
        if qty <= 0:
            continue
        orders.append({
            "symbol": str(pos.get("symbol", "")),
            "side": "SELL",
            "quantity": qty,
            "avg_cost": _float(pos.get("avg_cost")),
        })
    return {
        "strategy_name": strategy_name,
        "mode": control_mode,
        "status": str(current.get("to_state", "planned")),
        "created_at": str(current.get("recorded_at", "")),
        "updated_at": str(current.get("recorded_at", "")),
        "note": str(current.get("note", "")),
        "orders": orders,
    }


def _next_business_date(value: Optional[str]) -> Optional[str]:
    day = _parse_date(value)
    if day is None:
        return None
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.isoformat()


def build_strategy_mode_ledger(
    *,
    strategy_name: str,
    mode: str,
    configured: bool,
    initial_cash: float,
    control: Dict[str, Any],
    records: Dict[str, List[Dict[str, Any]]],
    positions_data: Dict[str, Any],
    latest_market_data_date: Optional[str],
    latest_record_date: Optional[str],
    liquidation_plan: Optional[Dict[str, Any]] = None,
    state_store: Optional[StrategyStateStore] = None,
) -> Dict[str, Any]:
    control_mode = _mode(mode)
    latest_signal_date = _latest_record_day(records.get("signals", []))
    latest_order_date = _latest_record_day(records.get("orders", []))
    latest_fill_date = _latest_record_day(records.get("fills", []))
    latest_snapshot_date = _latest_record_day(records.get("snapshots", []))
    latest_run_date = _max_date_text(latest_signal_date, latest_snapshot_date)
    pending_action_count = len(records.get("pending_orders", []))
    open_order_count = len([
        order for order in records.get("orders", [])
        if str(order.get("display_status") or order.get("status") or "").lower() in {"no_fill", "partial", "pending", "submitted"}
    ])
    positions = positions_data.get(strategy_name, {})
    holding_count = len([
        item for item in positions.values()
        if isinstance(item, dict) and _float(item.get("qty", item.get("quantity"))) > 0
    ]) if isinstance(positions, dict) else 0
    accepts = bool(control.get("live_enabled")) and str(control.get("live_state")) == "running" and not bool(control.get("liquidation_requested"))
    missing_signal_dates = _missing_signal_dates(
        latest_signal_date=latest_run_date,
        latest_market_data_date=latest_market_data_date,
        enabled=configured and accepts,
    )
    last_audit_at = ""
    if state_store is not None:
        last_audit_at = state_store.get_latest_recorded_at(strategy_name=strategy_name, mode=control_mode) or ""
    issues = []
    if configured and accepts and missing_signal_dates:
        issues.append({
            "code": "missing_signal_dates",
            "message": "market data is newer than the latest strategy signal",
            "dates": missing_signal_dates,
        })
    if control.get("liquidation_requested") and not liquidation_plan:
        issues.append({
            "code": "liquidation_plan_missing",
            "message": "strategy is liquidating but no liquidation plan exists",
        })
    if latest_record_date and latest_market_data_date and latest_record_date < latest_market_data_date and configured:
        issues.append({
            "code": "record_date_lags_market_data",
            "message": "latest record date is older than latest market data date",
            "record_date": latest_record_date,
            "market_data_date": latest_market_data_date,
        })
    status = "ok"
    if issues:
        status = "warning"
    if control.get("liquidation_requested") and not liquidation_plan:
        status = "blocked"
    return {
        "id": f"{strategy_name}:{control_mode}",
        "strategy_name": strategy_name,
        "mode": control_mode,
        "configured": configured,
        "initial_cash": float(initial_cash or 0.0),
        "control_state": control.get("live_state", "stopped"),
        "accepts_signals": accepts,
        "latest_signal_date": latest_signal_date,
        "latest_order_date": latest_order_date,
        "latest_fill_date": latest_fill_date,
        "latest_snapshot_date": latest_snapshot_date,
        "latest_run_date": latest_run_date,
        "latest_record_date": latest_record_date,
        "latest_market_data_date": latest_market_data_date,
        "next_execution_date": _next_business_date(latest_signal_date) if latest_signal_date else None,
        "missing_signal_dates": missing_signal_dates,
        "pending_action_count": pending_action_count,
        "open_order_count": open_order_count,
        "holding_count": holding_count,
        "liquidation_order_count": len((liquidation_plan or {}).get("orders", [])),
        "last_audit_at": last_audit_at,
        "health_status": status,
        "health_issues": issues,
    }


def build_operations_health(strategies: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    status_rank = {"ok": 0, "warning": 1, "blocked": 2}
    status = "ok"
    issues: List[Dict[str, Any]] = []
    for strategy in strategies:
        for mode_key in ("live", "paper"):
            mode_data = strategy.get(mode_key, {})
            ledger = mode_data.get("ledger", {})
            ledger_status = ledger.get("health_status", "ok")
            if status_rank.get(ledger_status, 0) > status_rank.get(status, 0):
                status = ledger_status
            for issue in ledger.get("health_issues", []):
                item = dict(issue)
                item["strategy_name"] = strategy.get("name")
                item["mode"] = mode_key
                issues.append(item)
    return {
        "status": status,
        "issue_count": len(issues),
        "issues": issues[:100],
    }


def sync_broker_trade_history(
    *,
    broker: Any,
    recorder: Any,
    tracker: Any,
    mode: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    logger: Optional[Any] = None,
) -> Dict[str, Any]:
    history_getter = getattr(broker, "get_trade_history", None)
    order_history_getter = getattr(broker, "get_order_history", None)
    if not callable(history_getter) and not callable(order_history_getter):
        return {
            "broker_history_supported": False,
            "imported_count": 0,
            "skipped_count": 0,
            "unresolved_count": 0,
        }
    trades = []
    if callable(history_getter):
        try:
            trades = history_getter(start_date=start_date, end_date=end_date)
        except TypeError:
            trades = history_getter()
        if trades is None:
            trades = []

    existing = _existing_fill_keys(recorder)
    filled_order_ids = _existing_fill_order_ids(recorder)
    local_orders = _existing_order_rows(recorder)
    imported = 0
    skipped = 0
    unresolved = 0
    touched_strategies = set()
    for raw in trades:
        trade = _normalize_trade(raw)
        if not trade:
            continue
        fill_key = _fill_key(trade)
        if fill_key in existing:
            skipped += 1
            continue
        order_id = trade["order_id"]
        local_order = local_orders.get(order_id, {})
        strategy_name = _strategy_for_recovered_order(
            tracker,
            order_id,
            fallback=trade.get("strategy_name") or local_order.get("strategy_name"),
        )
        if not strategy_name or strategy_name == "default":
            strategy_name = "default"
            unresolved += 1
            if logger:
                try:
                    logger.warning("Broker fill has no known strategy attribution: order_id=%s symbol=%s", order_id, trade.get("symbol"))
                except Exception:
                    pass
        recorder.record_fill(
            order_id=order_id,
            timestamp=trade["timestamp"],
            strategy_name=strategy_name,
            symbol=trade["symbol"],
            side=trade["side"],
            quantity=trade["quantity"],
            price=trade["price"],
            commission=trade["commission"],
            fill_id=trade.get("trade_id"),
        )
        touched_strategies.add(strategy_name)
        update = getattr(tracker, "update_from_fill", None)
        if callable(update):
            update(
                strategy_name=strategy_name,
                symbol=trade["symbol"],
                side=trade["side"],
                qty=trade["quantity"],
                price=trade["price"],
                commission=trade["commission"],
            )
        existing.add(fill_key)
        filled_order_ids.add(order_id)
        imported += 1
    order_history_imported = 0
    order_history_skipped = 0
    if callable(order_history_getter):
        try:
            orders = order_history_getter(start_date=start_date, end_date=end_date)
        except TypeError:
            orders = order_history_getter()
        for raw in orders or []:
            order = _normalize_filled_order(raw, local_orders=local_orders, broker=broker)
            if not order:
                continue
            order_id = order["order_id"]
            if order_id in filled_order_ids:
                skipped += 1
                order_history_skipped += 1
                continue
            fill_key = _fill_key(order)
            if fill_key in existing:
                skipped += 1
                order_history_skipped += 1
                continue
            strategy_name = _strategy_for_recovered_order(
                tracker,
                order_id,
                fallback=order.get("strategy_name"),
            )
            if not strategy_name or strategy_name == "default":
                strategy_name = "default"
                unresolved += 1
                if logger:
                    try:
                        logger.warning("Broker filled order has no known strategy attribution: order_id=%s symbol=%s", order_id, order.get("symbol"))
                    except Exception:
                        pass
            recorder.record_fill(
                order_id=order_id,
                timestamp=order["timestamp"],
                strategy_name=strategy_name,
                symbol=order["symbol"],
                side=order["side"],
                quantity=order["quantity"],
                price=order["price"],
                commission=order["commission"],
                fill_id=order.get("trade_id"),
            )
            touched_strategies.add(strategy_name)
            update = getattr(tracker, "update_from_fill", None)
            if callable(update):
                update(
                    strategy_name=strategy_name,
                    symbol=order["symbol"],
                    side=order["side"],
                    qty=order["quantity"],
                    price=order["price"],
                    commission=order["commission"],
                )
            existing.add(fill_key)
            filled_order_ids.add(order_id)
            imported += 1
            order_history_imported += 1
    result = {
        "broker_history_supported": True,
        "imported_count": imported,
        "skipped_count": skipped,
        "unresolved_count": unresolved,
        "order_history_imported_count": order_history_imported,
        "order_history_skipped_count": order_history_skipped,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
    }
    return result


def _existing_fill_keys(recorder: Any) -> set[Tuple[str, str, str, str, str, str]]:
    store = getattr(recorder, "_state_store", None)
    mode = getattr(recorder, "_mode", "live")
    if store is None:
        return set()
    keys = set()
    for fill in store.get_recent_fills(mode=mode, days=365):
        keys.add(_fill_key(fill))
    return keys


def _existing_fill_order_ids(recorder: Any) -> set[str]:
    store = getattr(recorder, "_state_store", None)
    mode = getattr(recorder, "_mode", "live")
    if store is None:
        return set()
    ids = set()
    for fill in store.get_recent_fills(mode=mode, days=365):
        for key in ("order_id", "broker_order_id"):
            order_id = str(fill.get(key) or "")
            if order_id:
                ids.add(order_id)
    return ids


def _existing_order_rows(recorder: Any) -> Dict[str, Dict[str, Any]]:
    store = getattr(recorder, "_state_store", None)
    mode = getattr(recorder, "_mode", "live")
    if store is None:
        return {}
    rows: Dict[str, Dict[str, Any]] = {}
    for order in store.get_recent_orders(mode=mode, days=365):
        for key in ("order_id", "broker_order_id"):
            order_id = str(order.get(key) or "")
            if order_id:
                rows[order_id] = _signal_to_order_jsonl(order)
    return rows


def _signal_to_order_jsonl(signal: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "timestamp": signal.get("timestamp", ""),
        "order_id": signal.get("order_id", ""),
        "broker_order_id": signal.get("broker_order_id", ""),
        "strategy_name": signal.get("strategy_name", ""),
        "symbol": signal.get("symbol", ""),
        "side": signal.get("side", ""),
        "quantity": signal.get("quantity", 0.0),
        "order_type": signal.get("order_type", ""),
        "price": signal.get("limit_price", signal.get("reference_price", 0.0)),
        "status": signal.get("status", ""),
        "reason": signal.get("failure_reason", ""),
    }


def _normalize_trade(raw: Any) -> Optional[Dict[str, Any]]:
    item = raw if isinstance(raw, dict) else raw.__dict__ if hasattr(raw, "__dict__") else {}
    order_id = str(_pick(item, "order_id", "broker_order_id", "m_nOrderID", "order_sysid") or "")
    symbol = str(_pick(item, "symbol", "stock_code", "m_strStockCode", "code") or "")
    side = str(_pick(item, "side", "order_side", "direction", "order_type", "entrust_bs") or "").upper()
    side = _side_text(side)
    quantity = _float(_pick(item, "quantity", "traded_volume", "volume", "m_nVolume", "qty"))
    price = _float(_pick(item, "price", "traded_price", "m_dPrice", "fill_price"))
    if not order_id or not symbol or not side or quantity <= 0 or price <= 0:
        return None
    timestamp = _parse_datetime(_pick(item, "timestamp", "traded_time", "trade_time", "m_strTradeTime"))
    return {
        "order_id": order_id,
        "trade_id": str(_pick(item, "trade_id", "fill_id", "m_strTradeID", "deal_id") or ""),
        "timestamp": timestamp,
        "strategy_name": str(_pick(item, "strategy_name", "strategy", "remark", "order_remark") or ""),
        "symbol": _cn_symbol(symbol),
        "side": side,
        "quantity": quantity,
        "price": price,
        "commission": _float(_pick(item, "commission", "m_dCommission", "fee", "entrust_fee")),
    }


def _normalize_filled_order(
    raw: Any,
    *,
    local_orders: Dict[str, Dict[str, Any]],
    broker: Any,
) -> Optional[Dict[str, Any]]:
    item = raw if isinstance(raw, dict) else raw.__dict__ if hasattr(raw, "__dict__") else {}
    status = str(_pick(item, "status", "order_status", "m_nOrderStatus") or "").lower()
    if status not in _filled_order_statuses():
        return None
    order_id = str(_pick(item, "order_id", "broker_order_id", "m_nOrderID", "order_sysid") or "")
    if not order_id:
        return None
    local = local_orders.get(order_id, {})
    symbol = str(_pick(item, "symbol", "stock_code", "m_strStockCode", "code") or local.get("symbol") or "")
    side = _side_text(_pick(item, "side", "order_side", "direction", "order_type", "entrust_bs"))
    if not side:
        side = _side_text(local.get("side"))
    quantity = _float(_pick(item, "quantity", "traded_volume", "order_volume", "volume", "m_nVolume", "qty"))
    if quantity <= 0:
        quantity = _float(local.get("quantity"))
    price = _float(_pick(item, "avg_price", "traded_price", "price", "order_price", "m_dPrice", "fill_price"))
    if price <= 0:
        price = _float(local.get("price"))
    if not symbol or not side or quantity <= 0 or price <= 0:
        return None
    commission = _float(_pick(item, "commission", "m_dCommission", "fee", "entrust_fee"))
    estimator = getattr(broker, "estimate_commission", None)
    if commission <= 0 and callable(estimator):
        try:
            commission = _float(estimator(_cn_symbol(symbol), side, quantity, price))
        except Exception:
            commission = 0.0
    strategy_name = str(local.get("strategy_name") or _pick(item, "strategy_name", "strategy", "remark", "order_remark") or "")
    return {
        "order_id": order_id,
        "trade_id": f"order_history:{order_id}",
        "timestamp": _parse_datetime(_pick(item, "timestamp", "traded_time", "trade_time", "order_time", "m_strTradeTime")),
        "strategy_name": strategy_name,
        "symbol": _cn_symbol(symbol),
        "side": side,
        "quantity": quantity,
        "price": price,
        "commission": commission,
    }


def _filled_order_statuses() -> set[str]:
    return {"56", "filled", "all_traded", "alltraded", "fully_filled", "done"}


def _strategy_for_recovered_order(tracker: Any, order_id: str, *, fallback: Any = "") -> str:
    strategy_getter = getattr(tracker, "get_strategy_for_order", None)
    if callable(strategy_getter):
        strategy_name = strategy_getter(order_id)
        if strategy_name and strategy_name != "default":
            return str(strategy_name)
    return str(fallback or "")


def _fill_key(item: Dict[str, Any]) -> Tuple[str, str, str, str, str, str]:
    timestamp = item.get("timestamp", "")
    if isinstance(timestamp, datetime):
        timestamp = timestamp.isoformat()
    return (
        str(item.get("order_id", "")),
        str(item.get("fill_id") or item.get("trade_id") or ""),
        str(timestamp),
        str(item.get("symbol", "")),
        str(_float(item.get("quantity"))),
        str(_float(item.get("price"))),
    )


def _latest_record_day(records: Iterable[Dict[str, Any]]) -> Optional[str]:
    dates = [_record_day(record) for record in records]
    dates = [value for value in dates if value]
    return max(dates) if dates else None


def _max_date_text(*values: Optional[str]) -> Optional[str]:
    dates = [str(value)[:10] for value in values if value]
    return max(dates) if dates else None


def _record_day(record: Dict[str, Any]) -> Optional[str]:
    if record.get("record_date"):
        return str(record.get("record_date"))[:10]
    timestamp = record.get("timestamp") or record.get("date") or record.get("snapshot_date")
    if isinstance(timestamp, datetime):
        return timestamp.date().isoformat()
    text = str(timestamp or "")
    return text[:10] if len(text) >= 10 else None


def _missing_signal_dates(
    *,
    latest_signal_date: Optional[str],
    latest_market_data_date: Optional[str],
    enabled: bool,
) -> List[str]:
    if not enabled or not latest_market_data_date:
        return []
    latest_market = _parse_date(latest_market_data_date)
    if latest_market is None:
        return []
    latest_signal = _parse_date(latest_signal_date) if latest_signal_date else None
    start = latest_market if latest_signal is None else latest_signal + timedelta(days=1)
    if start > latest_market:
        return []
    return [day.isoformat() for day in _business_days(start, latest_market)]


def _business_days(start: date, end: date) -> Iterable[date]:
    value = start
    while value <= end:
        if value.weekday() < 5:
            yield value
        value += timedelta(days=1)


def _next_business_date(value: Optional[str]) -> Optional[str]:
    day = _parse_date(value)
    if day is None:
        return None
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.isoformat()


def _mode(mode: str) -> str:
    value = str(mode or "live").lower()
    if value not in {"live", "paper"}:
        raise ValueError("mode must be live or paper")
    return value


def _pick(item: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item:
            value = item.get(key)
            if value is not None and value != "":
                return value
    return None


def _side_text(value: Any) -> str:
    text = str(value or "").upper()
    if text in {"BUY", "B", "23", "STOCK_BUY"}:
        return "BUY"
    if text in {"SELL", "S", "24", "STOCK_SELL"}:
        return "SELL"
    return text if text in {"BUY", "SELL"} else ""


def _cn_symbol(symbol: str) -> str:
    text = str(symbol or "")
    if "." in text and text[:6].isdigit():
        return text[:6]
    if "." in text and text[-6:].isdigit():
        return text[-6:]
    return text


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return datetime.now()
    text = text.replace(" ", "T")
    if text.isdigit():
        try:
            epoch_seconds = int(text)
            if 946684800 <= epoch_seconds <= 4102444800:
                return datetime.fromtimestamp(epoch_seconds)
        except (OverflowError, ValueError):
            pass
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return datetime.now()


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
