"""Strategy operation ledger, recovery, and audit helpers."""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_AUDIT_FILE = Path(__file__).resolve().parents[1] / "var" / "strategy_audit.jsonl"
DEFAULT_PLAN_DIR = Path(__file__).resolve().parents[1] / "var" / "strategy_liquidation_plans"


def append_strategy_audit(
    path: Optional[Any],
    *,
    strategy_name: str,
    mode: str,
    action: str,
    source: str = "system",
    note: str = "",
    payload: Optional[Dict[str, Any]] = None,
    timestamp: Optional[datetime] = None,
) -> Dict[str, Any]:
    audit_path = Path(path) if path is not None else DEFAULT_AUDIT_FILE
    ts = timestamp or datetime.now()
    row = {
        "timestamp": ts.isoformat(),
        "strategy_name": strategy_name or "default",
        "mode": _mode(mode),
        "action": action,
        "source": source,
        "note": note,
        "payload": payload or {},
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_jsonable(row), ensure_ascii=False, sort_keys=True) + "\n")
    return row


def read_strategy_audit(path: Optional[Any], max_records: int = 500) -> List[Dict[str, Any]]:
    audit_path = Path(path) if path is not None else DEFAULT_AUDIT_FILE
    if not audit_path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with audit_path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows[-max_records:]


def create_liquidation_plan(
    *,
    root: Path,
    strategy_name: str,
    mode: str,
    positions_data: Dict[str, Any],
    note: str = "",
    plan_dir: Optional[Any] = None,
    audit_path: Optional[Any] = None,
) -> Dict[str, Any]:
    control_mode = _mode(mode)
    orders = []
    positions = positions_data.get("positions", {}).get(strategy_name, {})
    if isinstance(positions, dict):
        for symbol, data in sorted(positions.items()):
            qty = _float(data.get("qty", data.get("quantity")))
            if qty <= 0:
                continue
            orders.append({
                "symbol": str(symbol),
                "side": "SELL",
                "quantity": qty,
                "avg_cost": _float(data.get("avg_cost")),
                "market_value": _float(data.get("market_value")),
            })
    timestamp = datetime.now().isoformat()
    plan = {
        "strategy_name": strategy_name,
        "mode": control_mode,
        "status": "planned",
        "created_at": timestamp,
        "updated_at": timestamp,
        "note": note,
        "orders": orders,
    }
    path = liquidation_plan_path(root, strategy_name, control_mode, plan_dir=plan_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)
    append_strategy_audit(
        audit_path or _default_audit_path(root),
        strategy_name=strategy_name,
        mode=control_mode,
        action="liquidation_plan_created",
        source="dashboard",
        note=note,
        payload={"order_count": len(orders), "plan_path": str(path)},
    )
    return plan


def read_liquidation_plan(
    *,
    root: Path,
    strategy_name: str,
    mode: str,
    plan_dir: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    path = liquidation_plan_path(root, strategy_name, mode, plan_dir=plan_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def liquidation_plan_path(
    root: Path,
    strategy_name: str,
    mode: str,
    *,
    plan_dir: Optional[Any] = None,
) -> Path:
    base = Path(plan_dir) if plan_dir is not None else root / "quant" / "infrastructure" / "var" / "strategy_liquidation_plans"
    safe_name = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in strategy_name)
    return base / _mode(mode) / f"{safe_name}.json"


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
    audit_records: Optional[Sequence[Dict[str, Any]]] = None,
    liquidation_plan: Optional[Dict[str, Any]] = None,
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
    positions = positions_data.get("positions", {}).get(strategy_name, {})
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
    audit = [
        row for row in (audit_records or [])
        if row.get("strategy_name") == strategy_name and row.get("mode") == control_mode
    ]
    last_audit_at = max((str(row.get("timestamp") or "") for row in audit), default="")
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
    audit_path: Optional[Any] = None,
) -> Dict[str, Any]:
    history_getter = getattr(broker, "get_trade_history", None)
    if not callable(history_getter):
        return {
            "broker_history_supported": False,
            "imported_count": 0,
            "skipped_count": 0,
            "unresolved_count": 0,
        }
    try:
        trades = history_getter(start_date=start_date, end_date=end_date)
    except TypeError:
        trades = history_getter()
    if trades is None:
        trades = []

    existing = _existing_fill_keys(getattr(recorder, "base_dir", None))
    imported = 0
    skipped = 0
    unresolved = 0
    for raw in trades:
        trade = _normalize_trade(raw)
        if not trade:
            continue
        fill_key = _fill_key(trade)
        if fill_key in existing:
            skipped += 1
            continue
        order_id = trade["order_id"]
        strategy_name = trade.get("strategy_name") or ""
        if not strategy_name:
            strategy_getter = getattr(tracker, "get_strategy_for_order", None)
            if callable(strategy_getter):
                strategy_name = strategy_getter(order_id)
        if not strategy_name or strategy_name == "default":
            strategy_name = "default"
            unresolved += 1
            append_strategy_audit(
                audit_path,
                strategy_name=strategy_name,
                mode=mode,
                action="broker_history_unresolved",
                source="recovery",
                note="Broker fill has no known strategy attribution",
                payload={"order_id": order_id, "trade_id": trade.get("trade_id"), "symbol": trade.get("symbol")},
                timestamp=trade["timestamp"],
            )
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
        imported += 1
    return {
        "broker_history_supported": True,
        "imported_count": imported,
        "skipped_count": skipped,
        "unresolved_count": unresolved,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
    }


def _existing_fill_keys(base_dir: Optional[Any]) -> set[Tuple[str, str, str, str, str, str]]:
    if base_dir is None:
        return set()
    base = Path(base_dir)
    if not base.exists():
        return set()
    keys = set()
    for day_dir in sorted(path for path in base.iterdir() if path.is_dir()):
        path = day_dir / "fills.jsonl"
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                keys.add(_fill_key(json.loads(line)))
    return keys


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
    timestamp = record.get("timestamp") or record.get("date")
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


def _default_audit_path(root: Path) -> Path:
    return root / "quant" / "infrastructure" / "var" / "strategy_audit.jsonl"


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


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value
