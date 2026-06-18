from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional

from quant.runtime.execution_commission import total_commission


CASH_BACKED_SOURCES = {"order_contract", "initial_cash", "position_baseline"}
RUN_STATUS_STEPS = [
    {"key": "DATA_READY", "label": "数据OK", "expected": "Market data covers the trading date."},
    {"key": "SIGNAL_READY", "label": "策略信号", "expected": "Strategy emits zero or more signal decisions."},
    {"key": "ORDER_SUBMITTED", "label": "提交订单", "expected": "For due signals, submitted and filled quantities are reconciled."},
]


def project_holdings(
    *,
    stored_positions: Mapping[str, Mapping[str, Any]],
    fills: List[Dict[str, Any]],
    latest_prices: Optional[Dict[str, Dict[str, Any]]] = None,
    order_rows: Optional[List[Dict[str, Any]]] = None,
    initial_cash: float = 0.0,
    capital_events: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    contract_state = _contract_position_state(order_rows or [])
    contract_positions = contract_state["positions"]
    contract_activity_dates = contract_state.get("symbol_activity_dates", {})
    has_contract_activity = bool(contract_state["has_activity"])
    realized = (
        contract_state["realized_pnl"]
        if has_contract_activity
        else sum(_float(p.get("realized_pnl", 0.0)) for p in stored_positions.values())
    )
    last_contract_prices = _latest_order_fill_prices(order_rows or [])
    last_fill_prices = _latest_fill_prices(fills)
    latest_prices = latest_prices or {}
    holdings = []
    total_market_value = 0.0
    total_cost = 0.0
    total_unrealized = 0.0
    price_dates = []
    stored_contract_mismatch = False
    for symbol in sorted(set(stored_positions) | set(contract_positions)):
        raw = stored_positions.get(symbol, {}) or {}
        contract_position = contract_positions.get(symbol)
        if raw:
            qty = _float(raw.get("qty", raw.get("quantity", 0.0)))
            avg_cost = _float(raw.get("avg_cost"))
            cost_value = avg_cost * qty
            if not contract_position or abs(_float(contract_position.get("qty")) - qty) > 1e-9:
                stored_contract_mismatch = True
        elif contract_position:
            qty = _float(contract_position.get("qty"))
            avg_cost = _float(contract_position.get("avg_cost"))
            cost_value = _float(contract_position.get("cost_value"))
        else:
            qty = 0.0
            avg_cost = 0.0
            cost_value = 0.0
        if qty <= 0:
            continue
        stored_market_value = _float(raw.get("market_value"))
        close_price = latest_prices.get(str(symbol).split(".")[0], {})
        stale_after_activity = False
        symbol_activity_date = str(contract_activity_dates.get(symbol) or "")
        if close_price and symbol_activity_date:
            close_date = str(close_price.get("date", ""))[:10]
            stale_after_activity = bool(symbol_activity_date and close_date and close_date < symbol_activity_date)
        if close_price and not stale_after_activity:
            current_price = _float(close_price.get("price"))
            price_date = str(close_price.get("date", ""))
            price_source = str(close_price.get("source", "duckdb"))
            valuation_status = "marked"
        elif stale_after_activity:
            current_price = last_contract_prices.get(symbol)
            if current_price is None and contract_position:
                current_price = _float(contract_position.get("unmarked_price"))
            current_price = _float(current_price) or avg_cost
            price_date = ""
            price_source = "unmarked_fill_after_activity"
            valuation_status = "unmarked_after_activity"
        elif qty > 0 and stored_market_value > 0:
            current_price = stored_market_value / qty
            price_date = ""
            price_source = "position_market_value"
            valuation_status = "stored_position_mark"
        else:
            current_price = last_contract_prices.get(symbol, last_fill_prices.get(symbol, avg_cost))
            price_date = ""
            price_source = "display_contract_fill" if symbol in last_contract_prices else "last_fill"
            valuation_status = "unmarked_after_activity" if contract_position else "fallback_mark"
        market_value = current_price * qty
        unrealized = market_value - cost_value
        if price_date:
            price_dates.append(price_date)
        total_market_value += market_value
        total_cost += cost_value
        total_unrealized += unrealized
        holdings.append({
            "symbol": symbol,
            "qty": qty,
            "current_price": current_price,
            "price_date": price_date,
            "price_source": price_source,
            "valuation_status": valuation_status,
            "avg_cost": avg_cost,
            "market_value": market_value,
            "unrealized_pnl": unrealized,
            "return_pct": (unrealized / cost_value) if cost_value > 0 else 0.0,
        })
    capital_delta = _capital_cash_delta(capital_events or [])
    cash_source = None
    cash = 0.0
    nav = total_market_value
    position_cash_basis = total_cost if total_cost > 0 else total_market_value
    if stored_contract_mismatch and holdings and initial_cash > 0:
        cash = initial_cash - position_cash_basis + capital_delta
        cash_source = "position_baseline"
        nav = cash + total_market_value
    elif has_contract_activity and initial_cash > 0:
        cash = initial_cash + _float(contract_state["cash_delta"]) + capital_delta
        cash_source = "order_contract"
        nav = cash + total_market_value
    elif holdings and initial_cash > 0:
        cash = initial_cash - position_cash_basis + capital_delta
        cash_source = "position_baseline"
        nav = cash + total_market_value
    elif not holdings and initial_cash > 0:
        cash = initial_cash + capital_delta
        cash_source = "initial_cash"
        nav = max(0.0, cash)
        if not price_dates:
            price_date = _latest_price_date(latest_prices)
            if price_date:
                price_dates.append(price_date)
    total_pnl = nav - initial_cash if cash_source in CASH_BACKED_SOURCES else realized + total_unrealized
    if cash_source == "initial_cash" and capital_delta == 0.0:
        total_pnl = 0.0
    return {
        "items": holdings,
        "total_market_value": total_market_value,
        "total_cost": total_cost,
        "unrealized_pnl": total_unrealized,
        "realized_pnl": realized,
        "total_pnl": total_pnl,
        "initial_cash": initial_cash,
        "cash": cash,
        "cash_source": cash_source,
        "nav": nav,
        "price_date": sorted(price_dates)[-1] if price_dates else None,
        "latest_activity_date": contract_state.get("latest_activity_date"),
        "capital_delta": capital_delta,
    }


def project_performance(
    *,
    strategy_name: str,
    raw_performance: Optional[Dict[str, Any]],
    holdings: Dict[str, Any],
    latest_market_data_date: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    performance = dict(raw_performance or {})
    curve = list(performance.get("pnl_curve") or [])
    initial_cash = _float(holdings.get("initial_cash"))
    latest_snapshot = _snapshot_from_holdings(
        strategy_name,
        holdings,
        latest_market_data_date=latest_market_data_date,
        now=now,
    )
    if latest_snapshot is not None:
        snapshot_date = latest_snapshot["date"]
        if not any((snapshot.get("date") or str(snapshot.get("timestamp", ""))[:10]) == snapshot_date for snapshot in curve):
            curve.append(latest_snapshot)
            curve = sorted(curve, key=lambda item: item.get("date") or str(item.get("timestamp", ""))[:10])
    performance.setdefault("strategy_name", strategy_name)
    performance.setdefault("total_trades", 0)
    performance.setdefault("win_rate", 0.0)
    performance.setdefault("profit_factor", 0.0)
    performance.setdefault("max_drawdown", 0.0)
    performance.setdefault("sharpe_ratio", 0.0)
    performance.setdefault("sortino_ratio", 0.0)
    performance.setdefault("calmar_ratio", 0.0)
    performance.setdefault("cash", 0.0)
    performance.setdefault("median_slippage_bps", None)
    performance.setdefault("weighted_avg_slippage_bps", None)
    performance.setdefault("slippage_sample_count", 0)
    performance["total_pnl"] = holdings.get("total_pnl", 0.0)
    performance["realized_pnl"] = holdings.get("realized_pnl", 0.0)
    performance["unrealized_pnl"] = holdings.get("unrealized_pnl", 0.0)
    performance["total_pnl_pct"] = (
        holdings.get("total_pnl", 0.0) / initial_cash
        if initial_cash > 0
        else 0.0
    )
    total_cost = holdings.get("total_cost", 0.0)
    denominator = initial_cash if holdings.get("cash_source") in CASH_BACKED_SOURCES and initial_cash > 0 else total_cost
    performance["total_return"] = (
        holdings.get("total_pnl", 0.0) / denominator
        if denominator and denominator > 0
        else performance.get("total_return", 0.0)
    )
    if holdings.get("cash_source") in CASH_BACKED_SOURCES:
        performance["total_nav"] = holdings.get("nav", 0.0)
        performance["cash"] = holdings.get("cash", 0.0)
        performance["total_nav_source"] = (
            "current_execution_state"
            if holdings.get("cash_source") == "order_contract"
            else holdings.get("cash_source")
        )
    if latest_snapshot is not None:
        performance["latest_snapshot"] = latest_snapshot
        if holdings.get("cash_source") not in CASH_BACKED_SOURCES and _float(performance.get("total_nav")) <= 0:
            performance["total_nav"] = latest_snapshot.get("nav", 0.0)
            performance["total_nav_source"] = "latest_snapshot"
    performance.setdefault("total_nav", 0.0)
    performance.setdefault("total_nav_source", "latest_snapshot")
    performance["pnl_curve"] = curve
    return performance


def project_run_status_bar(
    *,
    dates: List[str],
    configured: bool,
    control: Dict[str, Any],
    records: Dict[str, Any],
    latest_market_data_date: Optional[str],
    steps: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    steps = steps or RUN_STATUS_STEPS
    timeline = _run_status_timeline(
        dates=dates,
        configured=configured,
        control=control,
        records=records,
        latest_market_data_date=latest_market_data_date,
        steps=steps,
    )
    return {
        "steps": steps,
        "dates": dates,
        "status": _aggregate_run_status(timeline),
        "timeline": timeline,
        "days": [
            _run_status_day(
                trading_date=trading_date,
                configured=configured,
                control=control,
                records=records,
                latest_market_data_date=latest_market_data_date,
                steps=steps,
            )
            for trading_date in dates
        ],
    }


def project_pending_orders(
    *,
    signals: List[Dict[str, Any]],
    orders: List[Dict[str, Any]],
    fills: List[Dict[str, Any]],
    as_of_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    submitted_ids = _submitted_order_ids(orders, fills)
    submitted_signatures = [
        (_order_signature(record), _order_action_signature(record), _checkpoint_record_date(record), _timestamp_text(record))
        for record in orders
        if _is_submitted_order(record)
    ]
    pending: List[Dict[str, Any]] = []
    for signal in sorted(signals, key=lambda item: item.get("timestamp", "")):
        status = str(signal.get("status", "")).lower()
        if status not in _pending_signal_statuses() and status not in _failed_signal_statuses():
            continue
        failed = status in _failed_signal_statuses()
        submit_date = _signal_submit_date(signal)
        if not failed and _signal_is_submitted(signal, submitted_ids, submitted_signatures, submit_date):
            continue
        row = dict(signal)
        row["signal_date"] = _checkpoint_record_date(signal)
        row["submit_date"] = submit_date
        if _date_before(row["submit_date"], as_of_date):
            continue
        cost_bps = _pending_submit_cost_bps(row, _optional_float(row.get("signal_close_price")))
        if cost_bps is not None:
            row["cost_bps"] = cost_bps
            row["cost_bps_display"] = f"+{cost_bps:.1f} bps"
        row["display_status"] = "failed" if failed else "pending_submit"
        pending.append(row)
    return pending


def project_signal_rows(
    *,
    signals: List[Dict[str, Any]],
    orders: List[Dict[str, Any]],
    fills: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    submitted_ids = _submitted_order_ids(orders, fills)
    submitted_signatures = [
        (_order_signature(record), _order_action_signature(record), _checkpoint_record_date(record), _timestamp_text(record))
        for record in orders
        if _is_submitted_order(record)
    ]
    filled_order_ids = {str(fill.get("order_id") or "") for fill in fills if fill.get("order_id")}
    rows: List[Dict[str, Any]] = []
    for signal in sorted(signals, key=lambda item: item.get("timestamp", "")):
        status = str(signal.get("status") or "").lower()
        if status not in _pending_signal_statuses() and status not in _failed_signal_statuses():
            continue
        if _is_dashboard_submit_attempt_signal(signal, submitted_ids, submitted_signatures):
            continue
        if _float(signal.get("fill_quantity")) > 0:
            continue
        if str(signal.get("order_id") or "") in filled_order_ids:
            continue
        rows.append(signal)
    return rows


def project_fill_rows(
    *,
    mode: str,
    orders: List[Dict[str, Any]],
    fills: List[Dict[str, Any]],
    commission_config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    if mode != "paper":
        return [dict(fill) for fill in fills]
    order_dates = {
        (identifier, _checkpoint_record_date(order))
        for order in orders
        for identifier in _record_identifiers(order)
        if _checkpoint_record_date(order)
    }
    grouped: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
    passthrough: List[Dict[str, Any]] = []
    for fill in fills:
        row = dict(fill)
        keys = [
            (identifier, _checkpoint_record_date(row))
            for identifier in _record_identifiers(row)
            if _checkpoint_record_date(row)
        ]
        matching_keys = [key for key in keys if key in order_dates] or keys
        if not matching_keys:
            passthrough.append(row)
            continue
        grouped.setdefault(matching_keys[0], []).append(row)
    result = passthrough
    for group_rows in grouped.values():
        recorded_total = sum(_float(row.get("commission")) for row in group_rows)
        if recorded_total > 0:
            result.extend(group_rows)
            continue
        qty = sum(_float(row.get("quantity")) for row in group_rows)
        value = sum(_float(row.get("quantity")) * _float(row.get("price")) for row in group_rows)
        symbol = str((group_rows[0].get("symbol") if group_rows else "") or "").split(".")[0]
        side = str((group_rows[0].get("side") if group_rows else "") or "").upper()
        estimated = 0.0
        if qty > 0 and value > 0 and symbol and side in {"BUY", "SELL"}:
            try:
                estimated = round(float(total_commission(symbol, value / qty, qty, side, commission_config or {})), 6)
            except Exception:
                estimated = 0.0
        for row in group_rows:
            item = dict(row)
            if estimated > 0:
                row_value = _float(row.get("quantity")) * _float(row.get("price"))
                item["commission"] = round(estimated * row_value / value, 6) if value > 0 else estimated / len(group_rows)
                item["commission_source"] = "estimated_paper_shared_model"
            result.append(item)
    return sorted(result, key=lambda item: item.get("timestamp", ""))


def project_order_rows(
    *,
    mode: str,
    orders: List[Dict[str, Any]],
    fills: List[Dict[str, Any]],
    signals: Optional[List[Dict[str, Any]]] = None,
    open_prices: Optional[Dict[tuple[str, str], float]] = None,
    as_of_date: Optional[str] = None,
    commission_config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    open_prices = open_prices or {}
    signal_rows = list(signals or [])
    fill_totals: Dict[tuple[str, str], Dict[str, float]] = {}
    for fill in fills:
        identifiers = _record_identifiers(fill)
        fill_date = _checkpoint_record_date(fill)
        if not identifiers or not fill_date:
            continue
        qty = _float(fill.get("quantity"))
        price = _float(fill.get("price"))
        commission = _float(fill.get("commission"))
        for identifier in identifiers:
            totals = fill_totals.setdefault(
                (identifier, fill_date),
                {"quantity": 0.0, "value": 0.0, "commission": 0.0},
            )
            totals["quantity"] += qty
            totals["value"] += qty * price
            totals["commission"] += commission

    rows = []
    display_orders = list(orders)
    if mode == "paper" and signals and as_of_date:
        display_orders.extend(_synthetic_no_fill_orders(signals, orders, fills, as_of_date))
    for order in sorted(display_orders, key=lambda item: item.get("timestamp", "")):
        row = dict(order)
        totals = _fill_totals_for_order(fill_totals, order)
        filled_qty = totals["quantity"]
        limit_price = _float(order.get("price"))
        raw_fill_price = totals["value"] / filled_qty if filled_qty > 0 else None
        display_fill_price = _display_fill_price(raw_fill_price, filled_qty)
        commission = _display_commission(
            mode,
            order,
            filled_qty,
            raw_fill_price,
            totals["commission"],
            commission_config,
        )
        open_price = _order_execution_reference_price(order)
        if open_price is None:
            open_price = open_prices.get(_order_open_key(order))
        if open_price is None:
            open_price = _infer_execution_open_price(order, signal_rows)
        row["limit_price"] = limit_price
        row["open_price"] = open_price
        row["filled_qty"] = filled_qty
        row["raw_fill_price"] = raw_fill_price
        row["fill_price"] = display_fill_price
        row["commission"] = commission
        row["slippage_bps"] = _order_slippage_bps(open_price, display_fill_price, filled_qty)
        row["display_contract"] = "actual_fill"
        order_qty = _float(order.get("quantity"))
        if filled_qty <= 0:
            row["display_status"] = "no_fill"
        elif filled_qty + 1e-9 >= order_qty:
            row["display_status"] = "filled"
        else:
            row["display_status"] = "partial"
        rows.append(row)
    return rows


def _synthetic_no_fill_orders(
    signals: List[Dict[str, Any]],
    orders: List[Dict[str, Any]],
    fills: List[Dict[str, Any]],
    as_of_date: str,
) -> List[Dict[str, Any]]:
    submitted_ids = _submitted_order_ids(orders, fills)
    submitted_signatures = [
        (_order_signature(record), _order_action_signature(record), _checkpoint_record_date(record), _timestamp_text(record))
        for record in orders
        if _is_submitted_order(record)
    ]
    rows: List[Dict[str, Any]] = []
    for signal in sorted(signals, key=lambda item: item.get("timestamp", "")):
        status = str(signal.get("status") or "").lower()
        if status not in _pending_signal_statuses() and status not in _failed_signal_statuses():
            continue
        submit_date = _signal_submit_date(signal)
        if not submit_date or _date_before(as_of_date, submit_date):
            continue
        if _signal_is_submitted(signal, submitted_ids, submitted_signatures, submit_date):
            continue
        rows.append(_signal_to_no_fill_order(signal, submit_date))
    return rows


def _signal_to_no_fill_order(signal: Dict[str, Any], submit_date: str) -> Dict[str, Any]:
    return {
        "timestamp": f"{submit_date}T09:30:00",
        "signal_date": _checkpoint_record_date(signal),
        "submit_date": submit_date,
        "record_date": submit_date,
        "order_id": str(signal.get("order_id") or ""),
        "broker_order_id": str(signal.get("broker_order_id") or ""),
        "strategy_name": str(signal.get("strategy_name") or ""),
        "symbol": str(signal.get("symbol") or ""),
        "side": str(signal.get("side") or "").upper(),
        "quantity": _float(signal.get("quantity")),
        "order_type": str(signal.get("order_type") or ""),
        "price": _float(signal.get("reference_price", signal.get("price"))),
        "status": "no_fill",
        "reason": str(signal.get("failure_reason") or signal.get("reason") or "no_fill"),
        "source": "due_signal_without_fill",
    }


def project_execution_summary(order_rows: List[Dict[str, Any]], fills: List[Dict[str, Any]]) -> Dict[str, Any]:
    samples = [
        _float(row.get("slippage_bps"))
        for row in order_rows
        if row.get("slippage_bps") is not None
    ]
    weighted_sum = 0.0
    weight_total = 0.0
    for row in order_rows:
        slippage_bps = row.get("slippage_bps")
        fill_price = row.get("fill_price")
        filled_qty = _float(row.get("filled_qty"))
        if slippage_bps is None or fill_price is None or filled_qty <= 0:
            continue
        weight = abs(filled_qty * _float(fill_price))
        if weight > 0:
            weighted_sum += _float(slippage_bps) * weight
            weight_total += weight
    return {
        "total_commission": round(sum(_float(row.get("commission")) for row in order_rows), 6),
        "median_slippage_bps": round(_median(samples), 6) if samples else None,
        "weighted_avg_slippage_bps": round(weighted_sum / weight_total, 6) if weight_total > 0 else None,
        "slippage_sample_count": len(samples),
    }


def _run_status_timeline(
    *,
    dates: List[str],
    configured: bool,
    control: Dict[str, Any],
    records: Dict[str, Any],
    latest_market_data_date: Optional[str],
    steps: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not dates:
        return []
    timeline: List[Dict[str, Any]] = []
    first_date = dates[0]
    timeline.append(_timeline_checkpoint(
        _evaluate_run_checkpoint(
            key="DATA_READY",
            trading_date=first_date,
            configured=configured,
            control=control,
            grouped=_grouped_run_records(records, first_date),
            latest_market_data_date=latest_market_data_date,
            context={},
            steps=steps,
        ),
        date=first_date,
        steps=steps,
    ))
    timeline.append(_timeline_checkpoint(
        _evaluate_run_checkpoint(
            key="SIGNAL_READY",
            trading_date=first_date,
            configured=configured,
            control=control,
            grouped=_grouped_run_records(records, first_date),
            latest_market_data_date=latest_market_data_date,
            context={},
            steps=steps,
        ),
        date=first_date,
        steps=steps,
    ))
    for index in range(1, len(dates)):
        signal_date = dates[index - 1]
        submit_date = dates[index]
        timeline.append(_timeline_checkpoint(
            _evaluate_run_checkpoint(
                key="ORDER_SUBMITTED",
                trading_date=submit_date,
                configured=configured,
                control=control,
                grouped=_grouped_submit_records(records, signal_date=signal_date, submit_date=submit_date),
                latest_market_data_date=latest_market_data_date,
                context={},
                steps=steps,
            ),
            date=submit_date,
            signal_date=signal_date,
            submit_date=submit_date,
            steps=steps,
        ))
        timeline.append(_timeline_checkpoint(
            _evaluate_run_checkpoint(
                key="DATA_READY",
                trading_date=submit_date,
                configured=configured,
                control=control,
                grouped=_grouped_run_records(records, submit_date),
                latest_market_data_date=latest_market_data_date,
                context={},
                steps=steps,
            ),
            date=submit_date,
            steps=steps,
        ))
        timeline.append(_timeline_checkpoint(
            _evaluate_run_checkpoint(
                key="SIGNAL_READY",
                trading_date=submit_date,
                configured=configured,
                control=control,
                grouped=_grouped_run_records(records, submit_date),
                latest_market_data_date=latest_market_data_date,
                context={},
                steps=steps,
            ),
            date=submit_date,
            steps=steps,
        ))
    return timeline


def _run_status_day(
    *,
    trading_date: str,
    configured: bool,
    control: Dict[str, Any],
    records: Dict[str, Any],
    latest_market_data_date: Optional[str],
    steps: List[Dict[str, Any]],
) -> Dict[str, Any]:
    grouped = _grouped_run_records(records, trading_date)
    checkpoints = []
    blocked = False
    waiting = False
    context: Dict[str, Any] = {}
    for step in steps:
        key = str(step.get("key") or "")
        if (blocked or waiting) and key != "SNAPSHOT_WRITTEN":
            checkpoints.append(_checkpoint(
                key,
                "pending",
                "waiting for prior checkpoint",
                observed="prior checkpoint blocked" if blocked else "prior checkpoint pending",
                decision="pending: waiting for prior checkpoint",
                steps=steps,
            ))
            continue
        status = _evaluate_run_checkpoint(
            key=key,
            trading_date=trading_date,
            configured=configured,
            control=control,
            grouped=grouped,
            latest_market_data_date=latest_market_data_date,
            context=context,
            steps=steps,
        )
        checkpoints.append(status)
        if status["status"] in {"blocked", "failed"}:
            blocked = True
        elif status["status"] == "pending" and key != "SNAPSHOT_WRITTEN":
            waiting = True
    day_status = "ok" if all(item["status"] == "ok" for item in checkpoints) else next(
        (item["status"] for item in checkpoints if item["status"] != "ok"),
        "pending",
    )
    return {
        "date": trading_date,
        "status": day_status,
        "checkpoints": checkpoints,
    }


def _evaluate_run_checkpoint(
    *,
    key: str,
    trading_date: str,
    configured: bool,
    control: Dict[str, Any],
    grouped: Dict[str, List[Dict[str, Any]]],
    latest_market_data_date: Optional[str],
    context: Dict[str, Any],
    steps: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if key == "DATA_READY":
        observed = f"latest_market_data_date={str(latest_market_data_date or '-')[:10]} target={trading_date}"
        if latest_market_data_date and trading_date <= str(latest_market_data_date)[:10]:
            return _checkpoint(key, "ok", "market data ready", observed=observed, decision="ok: market data ready", steps=steps)
        return _checkpoint(key, "blocked", "market data missing", observed=observed, decision="blocked: market data missing", steps=steps)
    if not configured:
        return _checkpoint(key, "pending", "mode not configured", observed="mode not configured", decision="pending: mode not configured", steps=steps)
    if key == "SIGNAL_READY":
        signals = grouped["signals"]
        context["signals"] = signals
        signal_details = _run_signal_details(signals)
        pending_signals = [
            signal for signal in signals
            if str(signal.get("status") or "").lower() in _pending_signal_statuses()
        ]
        context["pending_signals"] = pending_signals
        if not signals:
            context["no_signal"] = True
            return _checkpoint(key, "ok", "no signal", observed="0 signal row(s)", decision="ok no-op: no signal emitted", details=[], steps=steps)
        if any(str(signal.get("status") or "").lower() in _failed_signal_statuses() for signal in signals):
            return _checkpoint(
                key,
                "blocked",
                "signal failed",
                observed=f"{len(signals)} signal row(s): {_format_status_counts(signals)}",
                decision="blocked: failed signal status present",
                details=signal_details,
                steps=steps,
            )
        return _checkpoint(
            key,
            "ok",
            f"{len(signals)} signal(s)",
            observed=f"{len(signals)} signal row(s): {_format_status_counts(signals)}",
            decision="ok: signal decision recorded",
            details=signal_details,
            steps=steps,
        )
    if key == "ORDER_SUBMITTED":
        orders = grouped["orders"]
        context["orders"] = orders
        ledger_signals = grouped.get("signal_ledger", grouped.get("signals", []))
        pending_signals = [
            signal for signal in ledger_signals
            if str(signal.get("status") or "").lower() in _pending_signal_statuses()
        ]
        if not pending_signals and not orders:
            return _checkpoint(key, "ok", "no order needed", observed="0 pending signal(s), 0 order row(s)", decision="ok no-op: no order required", details=[], steps=steps)
        submit_dates = [_signal_submit_date(signal) for signal in pending_signals]
        due_signals = [
            signal for signal, submit_date in zip(pending_signals, submit_dates)
            if not submit_date or submit_date <= trading_date
        ]
        future_submit_dates = sorted({submit_date for submit_date in submit_dates if submit_date and submit_date > trading_date})
        if pending_signals and not due_signals and not orders:
            next_submit = future_submit_dates[0] if future_submit_dates else "-"
            details = _run_order_details(pending_signals, orders, trading_date=trading_date)
            return _checkpoint(
                key,
                "pending",
                "waiting submit date",
                observed=f"{len(pending_signals)} pending signal(s), next submit_date={next_submit}",
                decision="pending: submit date not reached",
                details=details,
                steps=steps,
            )
        detail_signals = due_signals
        order_details = _run_order_details(detail_signals, orders, trading_date=trading_date)
        if not _control_accepts_signals(control):
            observed = (
                f"{len(due_signals or pending_signals)} pending signal(s), "
                f"control state={control.get('live_state', 'stopped')}, "
                f"signal_enabled={bool(control.get('live_enabled'))}, "
                f"liquidation_requested={bool(control.get('liquidation_requested'))}"
            )
            return _checkpoint(
                key,
                "blocked",
                "submit disabled",
                observed=observed,
                decision="blocked: submit disabled while signals pending",
                details=order_details,
                steps=steps,
            )
        if any(str(order.get("status") or "").lower() in _failed_signal_statuses() for order in orders):
            return _checkpoint(
                key,
                "blocked",
                "order failed",
                observed=f"{len(orders)} order row(s): {_format_status_counts(orders)}",
                decision="blocked: failed order status present",
                details=order_details,
                steps=steps,
            )
        signal_count = len(order_details)
        submitted_qty = sum(_float(item.get("submitted_quantity")) for item in order_details)
        filled_qty = sum(_float(item.get("filled_quantity")) for item in order_details)
        signal_qty = sum(_float(item.get("signal_quantity")) for item in order_details)
        observed = (
            f"submitted={_format_quantity(submitted_qty)} "
            f"filled={_format_quantity(filled_qty)} "
            f"for {signal_count} signal(s)"
        )
        if signal_count and filled_qty <= 0:
            return _checkpoint(key, "blocked", "no fill", observed=observed, decision="blocked: no fills for due signals", details=order_details, steps=steps)
        if signal_count and signal_qty > 0 and filled_qty + 1e-9 < signal_qty:
            return _checkpoint(key, "warning", "partial fill", observed=observed, decision="warning: partially filled due signals", details=order_details, steps=steps)
        return _checkpoint(
            key,
            "ok",
            "all filled" if signal_count else f"{len(orders)} order(s)",
            observed=observed if signal_count else f"{len(orders)} order row(s): {_format_status_counts(orders)}",
            decision="ok: all due signals filled" if signal_count else "ok: order records present",
            details=order_details,
            steps=steps,
        )
    return _checkpoint(key, "pending", "unknown checkpoint", observed="unknown checkpoint", decision="pending: unknown checkpoint", steps=steps)


def _timeline_checkpoint(
    checkpoint: Dict[str, Any],
    *,
    date: str,
    steps: List[Dict[str, Any]],
    signal_date: Optional[str] = None,
    submit_date: Optional[str] = None,
) -> Dict[str, Any]:
    item = dict(checkpoint)
    item["date"] = date
    item["label"] = f"{date[5:]} {_timeline_step_label(str(item.get('key') or ''), steps)}"
    item["id"] = ":".join(part for part in [date, str(item.get("key") or ""), signal_date or ""] if part)
    if signal_date:
        item["signal_date"] = signal_date
    if submit_date:
        item["submit_date"] = submit_date
    return item


def _timeline_step_label(key: str, steps: List[Dict[str, Any]]) -> str:
    if key == "ORDER_SUBMITTED":
        return "提交订单"
    for step in steps:
        if step.get("key") == key:
            return str(step.get("label") or key)
    return key


def _aggregate_run_status(items: List[Dict[str, Any]]) -> str:
    statuses = [str(item.get("status") or "pending").lower() for item in items]
    if any(status in {"blocked", "failed"} for status in statuses):
        return "blocked"
    if any(status == "warning" for status in statuses):
        return "warning"
    if any(status == "pending" for status in statuses):
        return "pending"
    return "ok"


def _grouped_run_records(records: Dict[str, Any], trading_date: str) -> Dict[str, List[Dict[str, Any]]]:
    return {
        "signals": [
            record for record in records.get("signals", [])
            if _checkpoint_record_date(record) == trading_date
        ],
        "signal_ledger": [
            record for record in records.get("signal_ledger", records.get("signals", []))
            if _checkpoint_record_date(record) == trading_date
        ],
        "orders": [
            record for record in records.get("orders", [])
            if _checkpoint_record_date(record) == trading_date
        ],
        "fills": [
            record for record in records.get("fills", [])
            if _checkpoint_record_date(record) == trading_date
        ],
        "snapshots": [
            record for record in records.get("snapshots", [])
            if _checkpoint_record_date(record) == trading_date
        ],
    }


def _grouped_submit_records(
    records: Dict[str, Any],
    *,
    signal_date: str,
    submit_date: str,
) -> Dict[str, List[Dict[str, Any]]]:
    return {
        "signals": [
            record for record in records.get("signals", [])
            if _checkpoint_record_date(record) == signal_date
        ],
        "signal_ledger": [
            record for record in records.get("signals", [])
            if _checkpoint_record_date(record) == signal_date
        ],
        "orders": [
            record for record in records.get("orders", [])
            if _checkpoint_record_date(record) == submit_date
        ],
        "fills": [
            record for record in records.get("fills", [])
            if _checkpoint_record_date(record) == submit_date
        ],
        "snapshots": [],
    }


def _run_signal_details(signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    details: List[Dict[str, Any]] = []
    for signal in signals:
        details.append({
            "timestamp": str(signal.get("timestamp") or ""),
            "signal_date": _checkpoint_record_date(signal),
            "submit_date": _signal_submit_date(signal),
            "symbol": str(signal.get("symbol") or ""),
            "side": str(signal.get("side") or "").upper(),
            "quantity": _float(signal.get("quantity")),
            "order_type": str(signal.get("order_type") or ""),
            "reference_price": _float(signal.get("reference_price", signal.get("price"))),
            "status": str(signal.get("display_status") or signal.get("status") or "unknown").lower(),
            "order_id": str(signal.get("order_id") or ""),
        })
    return details


def _run_order_details(
    signals: List[Dict[str, Any]],
    orders: List[Dict[str, Any]],
    *,
    trading_date: str,
) -> List[Dict[str, Any]]:
    if not signals and orders:
        return [_run_order_detail_from_order(order) for order in orders]
    details: List[Optional[Dict[str, Any]]] = [None] * len(signals)
    assigned_orders: set[int] = set()
    fallback_signal_indexes: List[int] = []
    indexed_orders = list(enumerate(orders))
    for signal_index, signal in enumerate(signals):
        signal_ids = set(_record_identifiers(signal))
        exact_matches = [
            (order_index, order) for order_index, order in indexed_orders
            if order_index not in assigned_orders
            and signal_ids
            and signal_ids.intersection(_record_identifiers(order))
        ]
        if exact_matches:
            assigned_orders.update(order_index for order_index, _ in exact_matches)
            submitted_qty = sum(_effective_submitted_quantity(order) for _, order in exact_matches)
            filled_qty = sum(_float(order.get("filled_qty")) for _, order in exact_matches)
            details[signal_index] = _run_order_detail_for_signal(
                signal,
                trading_date=trading_date,
                submitted_qty=submitted_qty,
                filled_qty=filled_qty,
            )
        else:
            fallback_signal_indexes.append(signal_index)

    fallback_orders: Dict[tuple[str, str, str], List[Dict[str, Any]]] = {}
    for order_index, order in indexed_orders:
        if order_index in assigned_orders:
            continue
        fallback_orders.setdefault(_run_order_record_match_key(order), []).append(order)
    fallback_signals: Dict[tuple[str, str, str], List[int]] = {}
    for signal_index in fallback_signal_indexes:
        fallback_signals.setdefault(_run_signal_submit_match_key(signals[signal_index]), []).append(signal_index)
    for key, signal_indexes in fallback_signals.items():
        group_orders = fallback_orders.get(key, [])
        submitted_remaining = sum(_effective_submitted_quantity(order) for order in group_orders)
        filled_remaining = sum(_float(order.get("filled_qty")) for order in group_orders)
        for signal_index in sorted(signal_indexes, key=lambda idx: str(signals[idx].get("timestamp") or "")):
            signal_qty = _float(signals[signal_index].get("quantity"))
            submitted_qty = min(signal_qty, submitted_remaining) if submitted_remaining > 0 else 0.0
            filled_qty = min(signal_qty, filled_remaining) if filled_remaining > 0 else 0.0
            submitted_remaining = max(0.0, submitted_remaining - submitted_qty)
            filled_remaining = max(0.0, filled_remaining - filled_qty)
            details[signal_index] = _run_order_detail_for_signal(
                signals[signal_index],
                trading_date=trading_date,
                submitted_qty=submitted_qty,
                filled_qty=filled_qty,
            )
    return [detail for detail in details if detail is not None]


def _run_order_detail_for_signal(
    signal: Dict[str, Any],
    *,
    trading_date: str,
    submitted_qty: float,
    filled_qty: float,
) -> Dict[str, Any]:
    signal_qty = _float(signal.get("quantity"))
    submit_date = _signal_submit_date(signal)
    submitted_qty = min(signal_qty, submitted_qty) if signal_qty > 0 else submitted_qty
    filled_qty = min(signal_qty, filled_qty) if signal_qty > 0 else filled_qty
    if submit_date and submit_date > trading_date and submitted_qty <= 0:
        status = "pending_submit"
    elif filled_qty + 1e-9 >= signal_qty and signal_qty > 0:
        status = "filled"
    elif filled_qty > 0:
        status = "partial"
    elif submitted_qty > 0:
        status = "no_fill"
    else:
        status = "missing"
    return {
        "timestamp": str(signal.get("timestamp") or ""),
        "signal_date": _checkpoint_record_date(signal),
        "submit_date": submit_date,
        "symbol": str(signal.get("symbol") or ""),
        "side": str(signal.get("side") or "").upper(),
        "signal_quantity": signal_qty,
        "submitted_quantity": submitted_qty,
        "filled_quantity": filled_qty,
        "status": status,
        "order_id": str(signal.get("order_id") or ""),
    }


def _run_order_record_match_key(record: Dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(record.get("symbol") or "").split(".")[0],
        str(record.get("side") or "").upper(),
        _checkpoint_record_date(record),
    )


def _run_signal_submit_match_key(signal: Dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(signal.get("symbol") or "").split(".")[0],
        str(signal.get("side") or "").upper(),
        _signal_submit_date(signal),
    )


def _run_order_detail_from_order(order: Dict[str, Any]) -> Dict[str, Any]:
    submitted_qty = _float(order.get("quantity"))
    filled_qty = _float(order.get("filled_qty"))
    if filled_qty + 1e-9 >= submitted_qty and submitted_qty > 0:
        status = "filled"
    elif filled_qty > 0:
        status = "partial"
    else:
        status = "no_fill"
    return {
        "timestamp": str(order.get("timestamp") or ""),
        "signal_date": _checkpoint_record_date(order),
        "submit_date": _checkpoint_record_date(order),
        "symbol": str(order.get("symbol") or ""),
        "side": str(order.get("side") or "").upper(),
        "signal_quantity": submitted_qty,
        "submitted_quantity": submitted_qty,
        "filled_quantity": filled_qty,
        "status": status,
        "order_id": str(order.get("order_id") or ""),
    }


def _checkpoint(
    key: str,
    status: str,
    message: str,
    *,
    steps: List[Dict[str, Any]],
    observed: Optional[str] = None,
    decision: Optional[str] = None,
    details: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    payload = {
        "key": key,
        "status": status,
        "message": message,
        "expected": _run_status_expected(key, steps),
        "observed": observed or message,
        "decision": decision or message,
    }
    if details is not None:
        payload["details"] = details
    return payload


def _run_status_expected(key: str, steps: List[Dict[str, Any]]) -> str:
    for step in steps:
        if step["key"] == key:
            return str(step.get("expected") or "")
    return ""


def _format_status_counts(records: List[Dict[str, Any]]) -> str:
    counts: Dict[str, int] = {}
    for record in records:
        status = str(record.get("display_status") or record.get("status") or "unknown").lower() or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return ", ".join(f"{status}={counts[status]}" for status in sorted(counts)) or "none"


def _checkpoint_record_date(record: Dict[str, Any]) -> str:
    for key in ("record_date", "signal_date", "snapshot_date", "date", "timestamp"):
        value = record.get(key)
        if value:
            return value.date().isoformat() if isinstance(value, datetime) else str(value)[:10]
    return ""


def _signal_submit_date(signal: Dict[str, Any]) -> str:
    explicit = str(signal.get("projected_submit_date") or signal.get("submit_date") or signal.get("execution_date") or "")[:10]
    if explicit:
        return explicit
    signal_date = _checkpoint_record_date(signal)
    if str(signal.get("status") or "").lower() in _failed_signal_statuses() or _is_intraday_signal(signal):
        return signal_date
    return signal_date


def _pending_signal_statuses() -> set[str]:
    return {"accepted", "pending", "queued", "pending_submit"}


def _failed_signal_statuses() -> set[str]:
    return {"rejected", "failed", "error", "dropped", "cancelled", "canceled", "expired"}


def _is_submitted_order(record: Dict[str, Any]) -> bool:
    return str(record.get("status") or "").lower() not in _failed_signal_statuses()


def _control_accepts_signals(control: Dict[str, Any]) -> bool:
    return (
        bool(control.get("live_enabled"))
        and str(control.get("live_state")) == "running"
        and not bool(control.get("liquidation_requested"))
    )


def _is_intraday_signal(signal: Dict[str, Any]) -> bool:
    timestamp = str(signal.get("timestamp") or "")
    if "T" not in timestamp:
        return False
    time_text = timestamp.split("T", 1)[1][:8]
    return bool(time_text) and time_text < "15:00:00"


def _effective_submitted_quantity(order: Dict[str, Any]) -> float:
    return max(_float(order.get("quantity")), _float(order.get("filled_qty")))


def _record_identifiers(record: Dict[str, Any]) -> List[str]:
    values = []
    seen = set()
    for key in ("order_id", "broker_order_id", "client_order_id"):
        value = str(record.get(key) or "")
        if value and value not in seen:
            seen.add(value)
            values.append(value)
    return values


def _order_open_key(order: Dict[str, Any]) -> tuple[str, str]:
    symbol = str(order.get("symbol") or "").split(".")[0]
    return (symbol, _checkpoint_record_date(order))


def _fill_totals_for_order(
    fill_totals: Dict[tuple[str, str], Dict[str, float]],
    order: Dict[str, Any],
) -> Dict[str, float]:
    order_date = _checkpoint_record_date(order)
    keys = [(identifier, order_date) for identifier in _record_identifiers(order) if order_date]
    for key in keys:
        if key in fill_totals:
            return fill_totals[key]
    identifiers = _record_identifiers(order)
    matches = [
        totals for (identifier, _), totals in fill_totals.items()
        if identifier in identifiers
    ]
    if len(matches) == 1:
        return matches[0]
    return {"quantity": 0.0, "value": 0.0, "commission": 0.0}


def _display_commission(
    mode: str,
    order: Dict[str, Any],
    filled_qty: float,
    raw_fill_price: Optional[float],
    recorded_commission: float,
    commission_config: Optional[Dict[str, Any]],
) -> float:
    if recorded_commission > 0 or mode != "paper" or filled_qty <= 0:
        return recorded_commission
    price = _float(raw_fill_price)
    if price <= 0:
        price = _float(order.get("avg_fill_price")) or _float(order.get("price"))
    if price <= 0:
        return recorded_commission
    symbol = str(order.get("symbol") or "").split(".")[0]
    side = str(order.get("side") or "").upper()
    if not symbol or side not in {"BUY", "SELL"}:
        return recorded_commission
    try:
        return round(float(total_commission(symbol, price, filled_qty, side, commission_config or {})), 6)
    except Exception:
        return recorded_commission


def _display_fill_price(raw_fill_price: Optional[float], filled_qty: float) -> Optional[float]:
    if filled_qty <= 0:
        return None
    return raw_fill_price


def _order_slippage_bps(
    open_price: Optional[float],
    fill_price: Optional[float],
    filled_qty: float,
) -> Optional[float]:
    if filled_qty <= 0 or open_price is None or open_price <= 0 or fill_price is None or fill_price <= 0:
        return None
    return round((fill_price / open_price - 1.0) * 10000.0, 6)


def _order_execution_reference_price(order: Dict[str, Any]) -> Optional[float]:
    price = _optional_float(order.get("execution_reference_price"))
    if price is None or price <= 0:
        return None
    return price


def _infer_execution_open_price(
    order: Dict[str, Any],
    signals: List[Dict[str, Any]],
) -> Optional[float]:
    limit_price = _optional_float(order.get("limit_price"))
    if limit_price is None:
        limit_price = _optional_float(order.get("price"))
    if limit_price is None or limit_price <= 0:
        return None
    cost_bps = _execution_cost_bps_for_order(order, signals)
    if cost_bps is None:
        return None
    side = str(order.get("side") or "").upper()
    if side == "BUY":
        divisor = 1.0 + cost_bps / 10000.0
    elif side == "SELL":
        divisor = 1.0 - cost_bps / 10000.0
    else:
        return None
    if divisor <= 0 or not math.isfinite(divisor):
        return None
    inferred = limit_price / divisor
    if inferred <= 0 or not math.isfinite(inferred):
        return None
    return round(inferred, 12)


def _execution_cost_bps_for_order(
    order: Dict[str, Any],
    signals: List[Dict[str, Any]],
) -> Optional[float]:
    direct = _optional_float(order.get("execution_cost_bps"))
    if direct is None:
        direct = _optional_float(order.get("cost_bps"))
    if direct is not None:
        return direct
    order_signature = _order_action_signature(order)
    if order_signature is None:
        return None
    order_date = _checkpoint_record_date(order)
    order_timestamp = _timestamp_text(order)
    candidates: List[tuple[int, str, float]] = []
    for signal in signals:
        if _order_action_signature(signal) != order_signature:
            continue
        cost_bps = _optional_float(signal.get("execution_cost_bps"))
        if cost_bps is None:
            cost_bps = _optional_float(signal.get("cost_bps"))
        if cost_bps is None:
            continue
        signal_submit_date = _signal_submit_date(signal)
        signal_date = _checkpoint_record_date(signal)
        signal_timestamp = _timestamp_text(signal)
        if order_date and signal_submit_date == order_date:
            score = 0
        elif order_date and signal_date == order_date:
            score = 1
        elif order_timestamp and signal_timestamp and _timestamps_near(order_timestamp, signal_timestamp):
            score = 2
        else:
            continue
        candidates.append((score, signal_timestamp, cost_bps))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def _submitted_order_ids(
    orders: List[Dict[str, Any]],
    fills: List[Dict[str, Any]],
) -> set[str]:
    ids: set[str] = set()
    for record in [record for record in orders if _is_submitted_order(record)] + fills:
        for key in ("order_id", "broker_order_id"):
            value = str(record.get(key) or "")
            if value:
                ids.add(value)
    return ids


def _signal_is_submitted(
    signal: Dict[str, Any],
    submitted_ids: set[str],
    submitted_signatures: List[tuple[Optional[tuple[Any, ...]], Optional[tuple[Any, ...]], str, str]],
    submit_date: str,
) -> bool:
    order_id = str(signal.get("order_id") or "")
    if order_id and order_id in submitted_ids:
        return True
    signature = _order_signature(signal)
    action_signature = _order_action_signature(signal)
    if signature is None and action_signature is None:
        return False
    signal_time = _timestamp_text(signal)
    for submitted_signature, submitted_action_signature, submitted_date, submitted_time in submitted_signatures:
        if signature is not None and submitted_signature == signature:
            if not signal_time or not submitted_time or submitted_time >= signal_time:
                return True
            if _timestamps_near(submitted_time, signal_time):
                return True
        if action_signature is not None and submitted_action_signature == action_signature:
            if submit_date and submitted_date == submit_date:
                return True
            if submitted_time and signal_time and _timestamps_near(submitted_time, signal_time):
                return True
    return False


def _is_dashboard_submit_attempt_signal(
    signal: Dict[str, Any],
    submitted_ids: set[str],
    submitted_signatures: List[tuple[Optional[tuple[Any, ...]], Optional[tuple[Any, ...]], str, str]],
) -> bool:
    status = str(signal.get("status", "")).lower()
    if status in _failed_signal_statuses():
        return True
    if status not in _pending_signal_statuses():
        return False
    if not _is_intraday_signal(signal):
        return False
    submit_date = _signal_submit_date(signal)
    return _signal_is_submitted(signal, submitted_ids, submitted_signatures, submit_date)


def _order_signature(record: Dict[str, Any]) -> Optional[tuple[Any, ...]]:
    strategy_name = str(record.get("strategy_name") or "default")
    symbol = str(record.get("symbol") or "")
    side = str(record.get("side") or "").upper()
    order_type = str(record.get("order_type") or "").upper()
    quantity = _float(record.get("quantity"))
    price = _float(record.get("price"))
    if not symbol or not side or quantity <= 0:
        return None
    return (
        strategy_name,
        symbol,
        side,
        order_type,
        round(quantity, 6),
        round(price, 6),
    )


def _order_action_signature(record: Dict[str, Any]) -> Optional[tuple[Any, ...]]:
    strategy_name = str(record.get("strategy_name") or "default")
    symbol = str(record.get("symbol") or "").split(".")[0]
    side = str(record.get("side") or "").upper()
    quantity = _float(record.get("quantity"))
    if not symbol or not side or quantity <= 0:
        return None
    return (
        strategy_name,
        symbol,
        side,
        round(quantity, 6),
    )


def _timestamp_text(record: Dict[str, Any]) -> str:
    return str(record.get("timestamp") or record.get("record_date") or "")


def _timestamps_near(left: str, right: str, tolerance_seconds: float = 1.0) -> bool:
    try:
        left_dt = datetime.fromisoformat(left[:26])
        right_dt = datetime.fromisoformat(right[:26])
    except ValueError:
        return False
    return abs((left_dt - right_dt).total_seconds()) <= tolerance_seconds


def _pending_submit_cost_bps(row: Dict[str, Any], signal_close: Optional[float]) -> Optional[float]:
    cost_bps = _optional_float(row.get("execution_cost_bps"))
    if cost_bps is None:
        cost_bps = _optional_float(row.get("cost_bps"))
    if cost_bps is not None:
        return cost_bps

    limit_price = _optional_float(row.get("price"))
    reference_price = _optional_float(row.get("reference_price"))
    if reference_price is None:
        reference_price = signal_close
    if limit_price is None or limit_price <= 0 or reference_price is None or reference_price <= 0:
        return None

    side = str(row.get("side") or "").upper()
    if side == "BUY":
        inferred = (limit_price / reference_price - 1.0) * 10000.0
    elif side == "SELL":
        inferred = (1.0 - limit_price / reference_price) * 10000.0
    else:
        return None
    if not math.isfinite(inferred) or inferred < -1e-6:
        return None
    return max(0.0, inferred)


def _date_before(left: str, right: Optional[str]) -> bool:
    if not left or not right:
        return False
    try:
        return datetime.fromisoformat(left[:10]).date() < datetime.fromisoformat(right[:10]).date()
    except ValueError:
        return False


def _format_quantity(value: float) -> str:
    if abs(value - round(value)) <= 1e-9:
        return str(int(round(value)))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _snapshot_from_holdings(
    strategy_name: str,
    holdings: Dict[str, Any],
    *,
    latest_market_data_date: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    market_value = _float(holdings.get("total_market_value"))
    total_cost = _float(holdings.get("total_cost"))
    if market_value <= 0 and total_cost <= 0 and holdings.get("cash_source") != "initial_cash":
        return None
    cash = _float(holdings.get("cash")) if holdings.get("cash_source") in CASH_BACKED_SOURCES else 0.0
    nav = _float(holdings.get("nav")) if holdings.get("cash_source") in CASH_BACKED_SOURCES else market_value
    snapshot_date = str(holdings.get("price_date") or "")[:10]
    max_snapshot_date = str(latest_market_data_date or "")[:10]
    latest_activity_date = str(holdings.get("latest_activity_date") or "")[:10]
    if max_snapshot_date:
        if snapshot_date and snapshot_date > max_snapshot_date:
            return None
        if latest_activity_date and latest_activity_date > max_snapshot_date:
            return None
    if holdings.get("cash_source") == "order_contract":
        if not snapshot_date or (latest_activity_date and snapshot_date < latest_activity_date):
            return None
    if not snapshot_date:
        snapshot_date = max_snapshot_date or date.today().isoformat()
    timestamp = (now or datetime.now()).isoformat()
    return {
        "date": snapshot_date,
        "timestamp": timestamp,
        "strategy_name": strategy_name,
        "nav": nav,
        "market_value": market_value,
        "cash": cash,
        "realized_pnl": holdings.get("realized_pnl", 0.0),
        "unrealized_pnl": holdings.get("unrealized_pnl", 0.0),
        "total_pnl": holdings.get("total_pnl", 0.0),
    }


def _capital_cash_delta(events: List[Dict[str, Any]]) -> float:
    delta = 0.0
    for evt in events:
        etype = str(evt.get("event_type") or "").upper()
        if etype in ("DEPOSIT", "DIVIDEND_CASH"):
            delta += _float(evt.get("amount"))
        elif etype == "WITHDRAW":
            delta -= _float(evt.get("amount"))
    return delta


def _contract_position_state(order_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    lots: Dict[str, List[Dict[str, float]]] = {}
    realized_pnl = 0.0
    cash_delta = 0.0
    has_activity = False
    latest_activity_date = ""
    symbol_activity_dates: Dict[str, str] = {}
    for row in sorted(order_rows, key=lambda item: item.get("timestamp", "")):
        symbol = str(row.get("symbol") or "")
        side = str(row.get("side") or "").upper()
        qty = _float(row.get("filled_qty"))
        fill_price = _float(row.get("fill_price"))
        commission = _float(row.get("commission"))
        if not symbol or qty <= 0 or fill_price <= 0:
            continue
        has_activity = True
        row_date = _record_date(row)
        if row_date and row_date > latest_activity_date:
            latest_activity_date = row_date
        if row_date and (symbol not in symbol_activity_dates or row_date > symbol_activity_dates[symbol]):
            symbol_activity_dates[symbol] = row_date
        notional = qty * fill_price
        if side == "BUY":
            unit_cost = (notional + commission) / qty
            lots.setdefault(symbol, []).append({"qty": qty, "unit_cost": unit_cost, "unit_price": fill_price})
            cash_delta -= notional + commission
        elif side == "SELL":
            removed_cost = 0.0
            remaining = qty
            for lot in lots.get(symbol, []):
                if remaining <= 0:
                    break
                take = min(_float(lot.get("qty")), remaining)
                if take <= 0:
                    continue
                removed_cost += take * _float(lot.get("unit_cost"))
                lot["qty"] = _float(lot.get("qty")) - take
                remaining -= take
            if remaining > 1e-9:
                removed_cost += remaining * fill_price
            lots[symbol] = [lot for lot in lots.get(symbol, []) if _float(lot.get("qty")) > 1e-9]
            realized_pnl += notional - commission - removed_cost
            cash_delta += notional - commission
    positions = {}
    for symbol, symbol_lots in lots.items():
        qty = sum(_float(lot.get("qty")) for lot in symbol_lots)
        cost_value = sum(_float(lot.get("qty")) * _float(lot.get("unit_cost")) for lot in symbol_lots)
        unmarked_value = sum(_float(lot.get("qty")) * _float(lot.get("unit_price")) for lot in symbol_lots)
        if qty <= 1e-9:
            continue
        positions[symbol] = {
            "qty": qty,
            "avg_cost": cost_value / qty,
            "cost_value": cost_value,
            "unmarked_price": unmarked_value / qty if unmarked_value > 0 else cost_value / qty,
        }
    return {
        "positions": positions,
        "realized_pnl": realized_pnl,
        "cash_delta": cash_delta,
        "has_activity": has_activity,
        "latest_activity_date": latest_activity_date,
        "symbol_activity_dates": symbol_activity_dates,
    }


def _latest_order_fill_prices(order_rows: List[Dict[str, Any]]) -> Dict[str, float]:
    prices: Dict[str, float] = {}
    for row in sorted(order_rows, key=lambda item: item.get("timestamp", "")):
        symbol = str(row.get("symbol", ""))
        price = _float(row.get("fill_price"))
        qty = _float(row.get("filled_qty"))
        if symbol and qty > 0 and price > 0:
            prices[symbol] = price
    return prices


def _latest_fill_prices(fills: List[Dict[str, Any]]) -> Dict[str, float]:
    prices: Dict[str, float] = {}
    for fill in sorted(fills, key=lambda item: item.get("timestamp", "")):
        symbol = str(fill.get("symbol", ""))
        price = _float(fill.get("price"))
        if symbol and price > 0:
            prices[symbol] = price
    return prices


def _latest_price_date(prices: Dict[str, Dict[str, Any]]) -> Optional[str]:
    dates = [
        str(item.get("date"))[:10]
        for item in prices.values()
        if item.get("date")
    ]
    return sorted(dates)[-1] if dates else None


def _record_date(record: Dict[str, Any]) -> str:
    timestamp = str(record.get("timestamp") or "")
    if len(timestamp) >= 10:
        return timestamp[:10]
    return str(record.get("record_date") or "")


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
