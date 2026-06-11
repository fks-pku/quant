#!/usr/bin/env python3
"""Local strategy operations dashboard server."""

import argparse
import math
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml
from flask import Flask, jsonify, request, send_file

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
DISPLAY_NAMES = {
    "ashare_gold_equity_barbell_timing": "A股黄金权益杠铃择时",
    "xueqiu_small_cap_financial_filter": "雪球小市值财务过滤",
    "default": "未归属持仓",
}
RUN_STATUS_STEPS = [
    {"key": "DATA_READY", "label": "数据OK", "expected": "Market data covers the trading date."},
    {"key": "SIGNAL_READY", "label": "策略信号", "expected": "Strategy emits zero or more signal decisions."},
    {"key": "ORDER_SUBMITTED", "label": "订单提交", "expected": "For due signals, submitted and filled quantities are reconciled."},
]
DASHBOARD_HTML = ROOT / ".codex" / "strategy_dashboard.html"

from quant.infrastructure.execution.live_recorder import LiveTradingRecorder
from quant.infrastructure.execution.strategy_controls import (
    apply_strategy_control_action,
    get_strategy_control,
)
from quant.infrastructure.execution.cn_trading_calendar import (
    expected_market_data_date as resolve_expected_market_data_date,
    latest_data_date as resolve_latest_data_date,
    next_trading_date_after,
    previous_trading_date_before,
)
from quant.infrastructure.execution.strategy_state_store import StrategyStateStore
from quant.infrastructure.execution.strategy_ledger import (
    build_operations_health,
    build_strategy_mode_ledger,
    create_liquidation_plan,
    read_liquidation_plan,
)
from quant.runtime.execution_commission import total_commission


def create_app(root: Path = ROOT) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    @app.get("/live")
    @app.get("/paper")
    def index():
        response = send_file(str(root / ".codex" / "strategy_dashboard.html"))
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "generated_at": datetime.now().isoformat()})

    @app.get("/api/dashboard")
    def dashboard():
        return jsonify(_json_safe(build_dashboard_payload(root)))

    @app.post("/api/strategies/<strategy_name>/control")
    def control(strategy_name: str):
        payload = request.get_json(silent=True) or {}
        action = str(payload.get("action", ""))
        mode = str(payload.get("mode") or "live").lower()
        note = str(payload.get("note", ""))
        if mode not in {"live", "paper"}:
            return jsonify({"ok": False, "error": "mode must be live or paper"}), 400
        config_dir = _mode_config_dir(root, mode)
        configured = strategy_name in _configured_strategies(config_dir)
        if action == "start" and not configured:
            initial_cash = _float(payload.get("initial_cash"))
            if initial_cash <= 0:
                return jsonify({"ok": False, "error": "initial_cash is required for first start"}), 400
            _configure_strategy_mode(root, mode, strategy_name, initial_cash)
            configured = True
        if action in {"start", "resume", "pause", "liquidate_stop"} and not configured:
            return jsonify({
                "ok": False,
                "error": f"Strategy is not present in {config_dir.name}/config.yaml; {mode} control cannot enable it.",
            }), 409
        db_path = root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb"
        state_store = StrategyStateStore(db_path)
        liquidation_plan = None
        try:
            current_strategy_config = _configured_strategies(config_dir).get(strategy_name)
            action_initial_cash = _configured_strategy_initial_cash(
                current_strategy_config,
                default_cash=_default_strategy_initial_cash(root),
            )
            control_state = apply_strategy_control_action(
                strategy_name,
                action,
                db_path,
                note=note,
                default_live_enabled=configured,
                mode=mode,
                initial_cash=action_initial_cash,
            )
            if action == "liquidate_stop":
                liquidation_plan = create_liquidation_plan(
                    strategy_name=strategy_name,
                    mode=mode,
                    store=state_store,
                    note=note,
                )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        body = {"ok": True, "control": control_state.to_dict()}
        if liquidation_plan is not None:
            body["liquidation_plan"] = liquidation_plan
        return jsonify(body)

    @app.post("/api/strategies/<strategy_name>/allocation")
    def allocation(strategy_name: str):
        payload = request.get_json(silent=True) or {}
        mode = str(payload.get("mode") or "live").lower()
        if mode not in {"live", "paper", "both"}:
            return jsonify({"ok": False, "error": "mode must be live, paper, or both"}), 400
        modes = ["live", "paper"] if mode == "both" else [mode]
        current: Dict[str, Any] = {}
        warnings: Dict[str, str] = {}
        default_cash = _default_strategy_initial_cash(root)
        for target_mode in modes:
            config_path = _mode_config_dir(root, target_mode) / "config.yaml"
            strategies = _configured_strategies(_mode_config_dir(root, target_mode))
            strategy_config = strategies.get(strategy_name)
            if strategy_config is None:
                warnings[target_mode] = f"{strategy_name} is not present in {target_mode} config"
                continue
            current[target_mode] = {
                "mode": target_mode,
                "strategy_name": strategy_name,
                "initial_cash": _configured_strategy_initial_cash(strategy_config, default_cash=default_cash),
                "allocation_cash": _configured_strategy_initial_cash(strategy_config, default_cash=default_cash),
                "config_path": str(config_path),
                "immutable": True,
            }
        if not current:
            return jsonify({
                "ok": False,
                "error": "Strategy is not present in the requested config.",
                "warnings": warnings,
            }), 409
        return jsonify({
            "ok": False,
            "error": "initial allocation cash is immutable after strategy configuration",
            "allocation_locked": True,
            "current_allocation": current,
            "warnings": warnings,
        }), 409

    @app.get("/reports/<strategy_name>")
    def report(strategy_name: str):
        report_path = _report_path(root, strategy_name)
        if report_path is None:
            return jsonify({"ok": False, "error": "report not found"}), 404
        return send_file(str(report_path))

    return app


def build_dashboard_payload(root: Path = ROOT) -> Dict[str, Any]:
    live_config = _configured_strategies(root / "quant" / "infrastructure" / "var" / "qmt_live_config")
    paper_config = _configured_strategies(root / "quant" / "infrastructure" / "var" / "paper_config")
    live_records_dir = root / "quant" / "infrastructure" / "var" / "live_trading"
    paper_records_dir = root / "quant" / "infrastructure" / "var" / "paper_trading"
    state_store = StrategyStateStore(root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb")
    db_path = str(state_store.db_path)

    all_positions_grouped = state_store.get_all_positions_grouped()
    symbol_set = state_store.get_all_position_symbols()
    db_strategy_names = set(state_store.get_all_known_strategy_names())
    strategy_names = _discover_strategy_names(
        live_config=live_config,
        paper_config=paper_config,
        db_strategy_names=db_strategy_names,
    )
    benchmark = _benchmark_curve(root)
    all_position_symbols = _position_symbols(all_positions_grouped)
    all_position_symbols.update(symbol_set)
    latest_prices = _latest_close_prices(root, all_position_symbols)
    latest_market_data_date = _latest_market_data_date(root) or _latest_price_date(latest_prices)
    if latest_market_data_date and "__market__" not in latest_prices:
        latest_prices["__market__"] = {
            "date": latest_market_data_date,
            "price": 1.0,
            "source": "market_calendar",
        }
    default_strategy_cash = _default_strategy_initial_cash(root)

    strategies = []
    for strategy_name in strategy_names:
        live_configured = bool(live_config.get(strategy_name, {}).get("enabled", False))
        paper_configured = bool(paper_config.get(strategy_name, {}).get("enabled", False))
        live_initial_cash = _configured_strategy_initial_cash(
            live_config.get(strategy_name),
            default_cash=default_strategy_cash,
        )
        paper_initial_cash = _configured_strategy_initial_cash(
            paper_config.get(strategy_name),
            default_cash=default_strategy_cash,
        )
        live_control_file = get_strategy_control(
            strategy_name,
            db_path,
            default_live_enabled=live_configured,
            mode="live",
        )
        paper_control_file = get_strategy_control(
            strategy_name,
            db_path,
            default_live_enabled=paper_configured,
            mode="paper",
        )
        live_records = _read_mode_records(
            root,
            live_records_dir,
            strategy_name,
            mode="live",
            configured=live_configured,
            latest_market_data_date=latest_market_data_date,
            commission_config=_mode_commission_config(root, "live"),
        )
        paper_records = _read_mode_records(
            root,
            paper_records_dir,
            strategy_name,
            mode="paper",
            configured=paper_configured,
            latest_market_data_date=latest_market_data_date,
            commission_config=_mode_commission_config(root, "paper"),
        )
        live_db_control = state_store.get_current_state(strategy_name=strategy_name, mode="live")
        live_control = _control_from_db(live_db_control, live_control_file.to_dict(), configured=live_configured, mode="live")
        paper_db_control = state_store.get_current_state(strategy_name=strategy_name, mode="paper")
        paper_control = _control_from_db(paper_db_control, paper_control_file.to_dict(), configured=paper_configured, mode="paper")
        live_liquidation_plan = read_liquidation_plan(
            strategy_name=strategy_name,
            mode="live",
            store=state_store,
        )
        paper_liquidation_plan = read_liquidation_plan(
            strategy_name=strategy_name,
            mode="paper",
            store=state_store,
        )
        live_holdings = _holdings_for_strategy(
            all_positions_grouped,
            strategy_name,
            "live",
            live_records["fills"],
            latest_prices,
            live_records["orders"],
            live_initial_cash,
            state_store.get_capital_events(strategy_name=strategy_name, mode="live"),
        )
        paper_holdings = _holdings_for_strategy(
            all_positions_grouped,
            strategy_name,
            "paper",
            paper_records["fills"],
            latest_prices,
            paper_records["orders"],
            paper_initial_cash,
            state_store.get_capital_events(strategy_name=strategy_name, mode="paper"),
        )
        live_performance = _performance(root, live_records_dir, strategy_name, live_holdings, live_records)
        paper_performance = _performance(root, paper_records_dir, strategy_name, paper_holdings, paper_records)
        _apply_execution_summary(live_performance, live_records["execution_summary"])
        _apply_execution_summary(paper_performance, paper_records["execution_summary"])
        report_path = _report_path(root, strategy_name)
        live_positions_data = all_positions_grouped.get(strategy_name, {}).get("live", {})
        paper_positions_data = all_positions_grouped.get(strategy_name, {}).get("paper", {})
        live_ledger = build_strategy_mode_ledger(
            strategy_name=strategy_name,
            mode="live",
            configured=live_configured,
            initial_cash=live_initial_cash,
            control=live_control,
            records=live_records,
            positions_data=live_positions_data,
            latest_market_data_date=latest_market_data_date,
            latest_record_date=_records_latest_date(live_records),
            liquidation_plan=live_liquidation_plan,
            state_store=state_store,
        )
        paper_ledger = build_strategy_mode_ledger(
            strategy_name=strategy_name,
            mode="paper",
            configured=paper_configured,
            initial_cash=paper_initial_cash,
            control=paper_control,
            records=paper_records,
            positions_data=paper_positions_data,
            latest_market_data_date=latest_market_data_date,
            latest_record_date=_records_latest_date(paper_records),
            liquidation_plan=paper_liquidation_plan,
            state_store=state_store,
        )
        live_run_status_bar = _run_status_bar(
            root=root,
            configured=live_configured,
            control=live_control,
            records=live_records,
            positions_data=live_positions_data,
            latest_market_data_date=latest_market_data_date,
        )
        paper_run_status_bar = _run_status_bar(
            root=root,
            configured=paper_configured,
            control=paper_control,
            records=paper_records,
            positions_data=paper_positions_data,
            latest_market_data_date=latest_market_data_date,
        )

        strategies.append({
            "name": strategy_name,
            "display_name": DISPLAY_NAMES.get(strategy_name, _humanize_strategy_name(strategy_name)),
            "report_url": f"/reports/{strategy_name}" if report_path else None,
            "report_path": str(report_path) if report_path else None,
            "initial_cash": {
                "live": live_initial_cash,
                "paper": paper_initial_cash,
                "default": default_strategy_cash,
            },
            "live": {
                "configured": live_configured,
                "initial_cash": live_initial_cash,
                "control": live_control,
                "accepts_signals": _control_accepts_signals(live_control),
                "ledger": live_ledger,
                "state": _mode_state_summary(live_records),
                "recovery": _recovery_status(live_ledger),
                "run_status_bar": live_run_status_bar,
                "liquidation_plan": live_liquidation_plan,
                "performance": live_performance,
                "holdings": live_holdings,
                "records": live_records,
            },
            "paper": {
                "configured": paper_configured,
                "initial_cash": paper_initial_cash,
                "control": paper_control,
                "accepts_signals": _control_accepts_signals(paper_control),
                "ledger": paper_ledger,
                "state": _mode_state_summary(paper_records),
                "recovery": _recovery_status(paper_ledger),
                "run_status_bar": paper_run_status_bar,
                "liquidation_plan": paper_liquidation_plan,
                "performance": paper_performance,
                "holdings": paper_holdings,
                "records": paper_records,
            },
        })

    payload = {
        "generated_at": datetime.now().isoformat(),
        "dashboard_asset_version": _dashboard_asset_version(root),
        "today": date.today().isoformat(),
        "latest_market_data_date": latest_market_data_date,
        "freshness": _freshness_status(
            root=root,
            latest_market_data_date=latest_market_data_date,
        ),
        "scheduled_jobs": _latest_scheduled_jobs(root),
        "strategies": strategies,
        "benchmark": benchmark,
    }
    payload["operations_health"] = build_operations_health(strategies)
    return payload


def _dashboard_asset_version(root: Path) -> str:
    html_path = root / ".codex" / "strategy_dashboard.html"
    if not html_path.exists():
        return "missing"
    stat = html_path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _freshness_status(
    *,
    root: Path,
    latest_market_data_date: Optional[str],
) -> Dict[str, Any]:
    expected = _expected_market_data_date(root=root)
    return {
        "expected_market_data_date": expected,
        "latest_market_data_date": latest_market_data_date,
        "market_data_stale": _date_before(latest_market_data_date, expected),
    }


def _expected_market_data_date(now: Optional[datetime] = None, root: Path = ROOT) -> str:
    return resolve_expected_market_data_date(
        now,
        cache_path=root / "quant" / "infrastructure" / "var" / "calendar" / "cn_trade_calendar_sse.json",
        duckdb_dir=root / "quant" / "infrastructure" / "var" / "duckdb" / "live",
        allow_refresh=False,
    ).isoformat()


def _date_before(value: Optional[str], expected: Optional[str]) -> bool:
    if not value or not expected:
        return False
    return str(value)[:10] < str(expected)[:10]


def _latest_scheduled_jobs(root: Path) -> Dict[str, Dict[str, Any]]:
    log_dir = root / "logs" / "scheduled"
    return {
        "data_update": _latest_scheduled_job(log_dir, "update_cn_data_oss_*.log", "data update", "publish exit_code="),
        "live_recovery": _latest_scheduled_job(log_dir, "qmt_live_recovery_*.log", "live recovery", "qmt live recovery exit_code="),
        "live_pending": _latest_scheduled_job(log_dir, "qmt_live_daily_*.log", "live pending", "qmt live daily exit_code="),
        "paper_replay": _latest_scheduled_job(log_dir, "paper_daily_*.log", "paper replay", "paper daily exit_code="),
    }


def _latest_scheduled_job(log_dir: Path, pattern: str, name: str, exit_marker: str) -> Dict[str, Any]:
    if not log_dir.exists():
        return {"name": name, "status": "missing", "exit_code": None, "finished_at": "", "error": ""}
    paths = sorted(log_dir.glob(pattern), key=lambda path: path.stat().st_mtime)
    if not paths:
        return {"name": name, "status": "missing", "exit_code": None, "finished_at": "", "error": ""}
    path = paths[-1]
    lines = _read_tail_lines(path, max_lines=240)
    exit_code = _extract_exit_code(lines, exit_marker)
    if exit_code is None and any("already complete" in line for line in lines):
        exit_code = 0
    status = "ok" if exit_code == 0 else "failed" if exit_code is not None else "unknown"
    error = _latest_log_error(lines) if status != "ok" else ""
    return {
        "name": name,
        "status": status,
        "exit_code": exit_code,
        "finished_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
        "path": str(path),
        "error": error,
    }


def _read_tail_lines(path: Path, max_lines: int = 200) -> List[str]:
    try:
        data = path.read_bytes()
    except OSError:
        return []
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le"):
        try:
            return data.decode(encoding).splitlines()[-max_lines:]
        except UnicodeError:
            continue
    return data.decode("utf-8", errors="replace").splitlines()[-max_lines:]


def _extract_exit_code(lines: List[str], marker: str) -> Optional[int]:
    for line in reversed(lines):
        if marker not in line:
            continue
        tail = line.split(marker, 1)[1]
        match = re.search(r"-?\d+", tail)
        return int(match.group(0)) if match else None
    return None


def _latest_log_error(lines: List[str]) -> str:
    for line in reversed(lines):
        text = line.strip()
        if any(token in text for token in ("RuntimeError:", "SystemExit", "Traceback", " ERROR ", "| ERROR")):
            return text[-240:]
    return ""


def _recovery_status(ledger: Dict[str, Any]) -> Dict[str, Any]:
    missing = ledger.get("missing_signal_dates", [])
    issues = ledger.get("health_issues", [])
    return {
        "status": ledger.get("health_status", "ok"),
        "last_signal_date": ledger.get("latest_signal_date"),
        "last_fill_date": ledger.get("latest_fill_date"),
        "last_snapshot_date": ledger.get("latest_snapshot_date"),
        "latest_market_data_date": ledger.get("latest_market_data_date"),
        "missing_signal_dates": missing,
        "needs_catchup": bool(missing),
        "issues": issues,
    }


def _configured_strategies(config_dir: Path) -> Dict[str, Dict[str, Any]]:
    config = _read_yaml(config_dir / "config.yaml")
    result: Dict[str, Dict[str, Any]] = {}
    for item in config.get("strategies", []) or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", ""))
        if name:
            result[name] = item
    return result


def _configure_strategy_mode(root: Path, mode: str, strategy_name: str, initial_cash: float) -> None:
    config_path = _mode_config_dir(root, mode) / "config.yaml"
    config = _read_yaml(config_path)
    strategies = config.setdefault("strategies", [])
    if not isinstance(strategies, list):
        strategies = []
        config["strategies"] = strategies
    for item in strategies:
        if isinstance(item, dict) and str(item.get("name", "")) == strategy_name:
            return
    strategies.append({"name": strategy_name, "enabled": True, "initial_cash": float(initial_cash)})
    _write_yaml(config_path, config)


def _mode_config_dir(root: Path, mode: str) -> Path:
    if mode == "live":
        return root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    if mode == "paper":
        return root / "quant" / "infrastructure" / "var" / "paper_config"
    raise ValueError("mode must be live or paper")


def _mode_commission_config(root: Path, mode: str) -> Dict[str, Any]:
    config = _read_yaml(_mode_config_dir(root, mode) / "config.yaml")
    execution = config.get("execution", {}) if isinstance(config, dict) else {}
    commission = execution.get("commission", {}) if isinstance(execution, dict) else {}
    return commission if isinstance(commission, dict) else {}


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_yaml(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    tmp_path.replace(path)


def _default_strategy_initial_cash(root: Path) -> float:
    config = _read_yaml(root / "quant" / "shared" / "config" / "config.yaml")
    candidates = [
        (config.get("live_trading", {}) or {}).get("strategy_initial_cash"),
        (config.get("trading", {}) or {}).get("strategy_initial_cash"),
        (config.get("system", {}) or {}).get("strategy_initial_cash"),
    ]
    for value in candidates:
        cash = _float(value)
        if cash > 0:
            return cash
    return 20000.0


def _configured_strategy_initial_cash(
    *configs: Optional[Dict[str, Any]],
    default_cash: float,
) -> float:
    for config in configs:
        if not isinstance(config, dict):
            continue
        for key in ("allocation_cash", "strategy_initial_cash", "initial_cash", "capital"):
            if key not in config:
                continue
            cash = _optional_float(config.get(key))
            if cash is not None and cash >= 0:
                return cash
    return default_cash


def _discover_strategy_names(
    *,
    live_config: Dict[str, Dict[str, Any]],
    paper_config: Dict[str, Dict[str, Any]],
    db_strategy_names: set[str],
) -> List[str]:
    names = set(live_config) | set(paper_config)
    names.update(db_strategy_names)
    ordered = [name for name in sorted(names) if name and name != "default"]
    if "default" in names:
        ordered.append("default")
    return ordered


def _strategy_dirs(root: Path) -> List[str]:
    base = root / "quant" / "features" / "strategies"
    if not base.exists():
        return []
    return [
        item.name for item in base.iterdir()
        if item.is_dir() and ((item / "strategy.py").exists() or (item / "full_research_report.html").exists())
    ]


def _read_mode_records(
    root: Path,
    base_dir: Path,
    strategy_name: str,
    *,
    mode: str,
    configured: bool,
    latest_market_data_date: Optional[str],
    commission_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state_store = StrategyStateStore(root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb")
    all_signals = state_store.get_signals(strategy_name=strategy_name, mode=mode, limit=5000)
    signals = all_signals
    orders = [s for s in all_signals if s.get("order_id") and str(s.get("status", "")).lower() not in _pending_signal_statuses()]
    fills_raw = [s for s in all_signals if s.get("fill_quantity", 0) > 0]

    order_records = [_signal_to_order_record(s) for s in orders]
    fill_records = [_signal_to_fill_record(s) for s in fills_raw]

    display_fills = _dashboard_fill_rows(mode, order_records, fill_records, commission_config=commission_config)
    order_rows = _dashboard_order_rows(
        mode,
        order_records,
        display_fills,
        _open_prices_for_orders(root, order_records),
        commission_config=commission_config,
    )
    signal_rows = _dashboard_signal_rows(root, signals, order_records, display_fills)
    snapshots = state_store.get_snapshots(strategy_name=strategy_name, mode=mode, limit=365)
    snapshot_rows = _dashboard_snapshot_rows(snapshots, latest_market_data_date)
    return {
        "signal_ledger": all_signals,
        "signals": signal_rows,
        "orders": order_rows,
        "fills": sorted(display_fills, key=lambda item: item.get("timestamp", "")),
        "snapshots": sorted(snapshot_rows, key=lambda item: item.get("timestamp", "")),
        "operations": [],
        "runs": [],
        "control_state": [],
        "capital_events": [],
        "submit_attempts": [],
        "positions": [],
        "watermarks": [],
        "reconciliations": [],
        "pending_orders": _pending_submit_orders(root, signals, order_records, display_fills, as_of_date=date.today().isoformat()),
        "execution_summary": _dashboard_execution_summary(order_rows, display_fills),
        "latest_watermark": {},
    }


def _signal_to_order_record(signal: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "timestamp": signal.get("timestamp", ""),
        "order_id": signal.get("order_id", ""),
        "broker_order_id": signal.get("broker_order_id", ""),
        "strategy_name": signal.get("strategy_name", ""),
        "symbol": signal.get("symbol", ""),
        "side": signal.get("side", ""),
        "quantity": signal.get("quantity", 0.0),
        "order_type": signal.get("order_type", ""),
        "price": signal.get("reference_price", 0.0),
        "status": signal.get("status", "submitted"),
        "reason": signal.get("failure_reason", ""),
        "record_date": signal.get("signal_date", ""),
    }


def _signal_to_fill_record(signal: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "timestamp": signal.get("fill_time", signal.get("timestamp", "")),
        "fill_id": "",
        "order_id": signal.get("order_id", ""),
        "strategy_name": signal.get("strategy_name", ""),
        "symbol": signal.get("symbol", ""),
        "side": signal.get("side", ""),
        "quantity": signal.get("fill_quantity", 0.0),
        "price": signal.get("fill_price", 0.0),
        "commission": signal.get("commission", 0.0),
        "value": float(signal.get("fill_quantity", 0.0)) * float(signal.get("fill_price", 0.0)),
        "record_date": signal.get("signal_date", ""),
    }


def _control_from_db(
    db_row: Optional[Dict[str, Any]],
    fallback: Dict[str, Any],
    *,
    configured: bool,
    mode: str,
) -> Dict[str, Any]:
    if db_row is None:
        return {
            "live_enabled": bool(fallback.get("live_enabled", configured)),
            "live_state": str(fallback.get("live_state") or ("running" if configured else "stopped")),
            "liquidation_requested": bool(fallback.get("liquidation_requested", False)),
            "updated_at": str(fallback.get("updated_at", "")),
            "mode": mode,
        }
    lifecycle = str(db_row.get("to_state") or db_row.get("lifecycle_state") or "stopped")
    return {
        "live_enabled": bool(db_row.get("signal_enabled", False)),
        "live_state": lifecycle,
        "liquidation_requested": bool(db_row.get("liquidation_requested", False)),
        "updated_at": str(db_row.get("recorded_at") or db_row.get("updated_at") or ""),
        "initial_cash": float(db_row.get("initial_cash", 0.0)),
        "mode": mode,
    }


def _dashboard_snapshot_rows(
    snapshots: List[Dict[str, Any]],
    latest_market_data_date: Optional[str],
) -> List[Dict[str, Any]]:
    max_date = str(latest_market_data_date or "")[:10]
    by_date: Dict[str, Dict[str, Any]] = {}
    for row in snapshots:
        row_date = str(row.get("date") or row.get("snapshot_date") or row.get("record_date") or row.get("timestamp") or "")[:10]
        if not row_date:
            continue
        if max_date and row_date > max_date:
            continue
        existing = by_date.get(row_date)
        if existing is None or _snapshot_rank(row) >= _snapshot_rank(existing):
            norm = dict(row)
            norm["date"] = row_date
            norm.setdefault("timestamp", norm.get("recorded_at", ""))
            by_date[row_date] = norm
    return sorted(by_date.values(), key=lambda item: str(item.get("timestamp") or item.get("date") or ""))


def _snapshot_rank(row: Dict[str, Any]) -> tuple[int, str]:
    source = str(row.get("source") or "")
    if source == "canonical_fill_ledger":
        priority = 3
    elif source == "cash_only_no_activity":
        priority = 2
    else:
        priority = 1
    return (priority, str(row.get("timestamp") or ""))


def _latest_record_day(records: Iterable[Dict[str, Any]]) -> Optional[str]:
    latest: Optional[str] = None
    for record in records:
        value = _record_date(record)
        if value and (latest is None or value > latest):
            latest = value
    return latest


def _mode_state_summary(records: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "run": _latest_by_timestamp(records.get("runs", [])),
        "control_state": _latest_by_timestamp(records.get("control_state", [])),
        "watermark": _latest_by_timestamp(records.get("watermarks", [])),
        "capital_events": list(records.get("capital_events", []))[-20:],
        "reconciliations": list(records.get("reconciliations", []))[-20:],
    }


def _latest_by_timestamp(records: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    rows = list(records)
    if not rows:
        return None
    return sorted(rows, key=lambda item: str(item.get("updated_at") or item.get("timestamp") or item.get("created_at") or ""))[-1]


def _control_accepts_signals(control: Dict[str, Any]) -> bool:
    return (
        bool(control.get("live_enabled"))
        and str(control.get("live_state")) == "running"
        and not bool(control.get("liquidation_requested"))
    )


def _records_latest_date(records: Dict[str, Any]) -> Optional[str]:
    latest: Optional[str] = None
    for kind in ("signals", "orders", "fills", "snapshots"):
        for record in records.get(kind, []):
            value = record.get("record_date") or record.get("date") or record.get("timestamp") or record.get("snapshot_date")
            text = value.date().isoformat() if isinstance(value, datetime) else str(value or "")[:10]
            if len(text) == 10 and (latest is None or text > latest):
                latest = text
    return latest


def _run_status_bar(
    *,
    root: Path,
    configured: bool,
    control: Dict[str, Any],
    records: Dict[str, Any],
    positions_data: Dict[str, Any],
    latest_market_data_date: Optional[str],
) -> Dict[str, Any]:
    anchor = latest_market_data_date or _records_latest_date(records) or date.today().isoformat()
    dates = _recent_run_dates(root, anchor, count=3)
    timeline = _run_status_timeline(
        root=root,
        dates=dates,
        configured=configured,
        control=control,
        records=records,
        positions_data=positions_data,
        latest_market_data_date=latest_market_data_date,
    )
    return {
        "steps": RUN_STATUS_STEPS,
        "dates": dates,
        "status": _aggregate_run_status(timeline),
        "timeline": timeline,
        "days": [
            _run_status_day(
                root=root,
                trading_date=trading_date,
                configured=configured,
                control=control,
                records=records,
                positions_data=positions_data,
                latest_market_data_date=latest_market_data_date,
            )
            for trading_date in dates
        ],
    }


def _recent_run_dates(root: Path, anchor: str, *, count: int) -> List[str]:
    anchor_day = _parse_run_date(anchor) or date.today()
    days = [anchor_day]
    current = anchor_day
    while len(days) < count:
        try:
            current = previous_trading_date_before(
                current,
                cache_path=root / "quant" / "infrastructure" / "var" / "calendar" / "cn_trade_calendar_sse.json",
                duckdb_dir=root / "quant" / "infrastructure" / "var" / "duckdb" / "live",
                allow_refresh=False,
            )
        except Exception:
            current = _previous_weekday_before(current)
        if current >= days[-1]:
            current = _previous_weekday_before(days[-1])
        days.append(current)
    return [day.isoformat() for day in reversed(days)]


def _run_status_timeline(
    *,
    root: Path,
    dates: List[str],
    configured: bool,
    control: Dict[str, Any],
    records: Dict[str, Any],
    positions_data: Dict[str, Any],
    latest_market_data_date: Optional[str],
) -> List[Dict[str, Any]]:
    if not dates:
        return []
    timeline: List[Dict[str, Any]] = []
    first_date = dates[0]
    timeline.append(_timeline_checkpoint(
        _evaluate_run_checkpoint(
            key="DATA_READY",
            root=root,
            trading_date=first_date,
            configured=configured,
            control=control,
            grouped=_grouped_run_records(records, first_date),
            positions_data=positions_data,
            latest_market_data_date=latest_market_data_date,
            context={},
        ),
        date=first_date,
    ))
    timeline.append(_timeline_checkpoint(
        _evaluate_run_checkpoint(
            key="SIGNAL_READY",
            root=root,
            trading_date=first_date,
            configured=configured,
            control=control,
            grouped=_grouped_run_records(records, first_date),
            positions_data=positions_data,
            latest_market_data_date=latest_market_data_date,
            context={},
        ),
        date=first_date,
    ))
    for index in range(1, len(dates)):
        signal_date = dates[index - 1]
        submit_date = dates[index]
        timeline.append(_timeline_checkpoint(
            _evaluate_run_checkpoint(
                key="ORDER_SUBMITTED",
                root=root,
                trading_date=submit_date,
                configured=configured,
                control=control,
                grouped=_grouped_submit_records(records, signal_date=signal_date, submit_date=submit_date),
                positions_data=positions_data,
                latest_market_data_date=latest_market_data_date,
                context={},
            ),
            date=submit_date,
            signal_date=signal_date,
            submit_date=submit_date,
        ))
        timeline.append(_timeline_checkpoint(
            _evaluate_run_checkpoint(
                key="DATA_READY",
                root=root,
                trading_date=submit_date,
                configured=configured,
                control=control,
                grouped=_grouped_run_records(records, submit_date),
                positions_data=positions_data,
                latest_market_data_date=latest_market_data_date,
                context={},
            ),
            date=submit_date,
        ))
        timeline.append(_timeline_checkpoint(
            _evaluate_run_checkpoint(
                key="SIGNAL_READY",
                root=root,
                trading_date=submit_date,
                configured=configured,
                control=control,
                grouped=_grouped_run_records(records, submit_date),
                positions_data=positions_data,
                latest_market_data_date=latest_market_data_date,
                context={},
            ),
            date=submit_date,
        ))
    return timeline


def _timeline_checkpoint(
    checkpoint: Dict[str, Any],
    *,
    date: str,
    signal_date: Optional[str] = None,
    submit_date: Optional[str] = None,
) -> Dict[str, Any]:
    item = dict(checkpoint)
    item["date"] = date
    item["label"] = f"{date[5:]} {_timeline_step_label(str(item.get('key') or ''))}"
    item["id"] = ":".join(part for part in [date, str(item.get("key") or ""), signal_date or ""] if part)
    if signal_date:
        item["signal_date"] = signal_date
    if submit_date:
        item["submit_date"] = submit_date
    return item


def _timeline_step_label(key: str) -> str:
    if key == "DATA_READY":
        return "数据OK"
    if key == "SIGNAL_READY":
        return "策略信号"
    if key == "ORDER_SUBMITTED":
        return "提交订单"
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


def _run_status_day(
    *,
    root: Path,
    trading_date: str,
    configured: bool,
    control: Dict[str, Any],
    records: Dict[str, Any],
    positions_data: Dict[str, Any],
    latest_market_data_date: Optional[str],
) -> Dict[str, Any]:
    grouped = {
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
    checkpoints = []
    blocked = False
    waiting = False
    context: Dict[str, Any] = {}
    for step in RUN_STATUS_STEPS:
        key = step["key"]
        if (blocked or waiting) and key != "SNAPSHOT_WRITTEN":
            checkpoints.append(_checkpoint(
                key,
                "pending",
                "waiting for prior checkpoint",
                observed="prior checkpoint blocked" if blocked else "prior checkpoint pending",
                decision="pending: waiting for prior checkpoint",
            ))
            continue
        status = _evaluate_run_checkpoint(
            key=key,
            root=root,
            trading_date=trading_date,
            configured=configured,
            control=control,
            grouped=grouped,
            positions_data=positions_data,
            latest_market_data_date=latest_market_data_date,
            context=context,
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
    root: Path,
    trading_date: str,
    configured: bool,
    control: Dict[str, Any],
    grouped: Dict[str, List[Dict[str, Any]]],
    positions_data: Dict[str, Any],
    latest_market_data_date: Optional[str],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    if key == "DATA_READY":
        observed = f"latest_market_data_date={str(latest_market_data_date or '-')[:10]} target={trading_date}"
        if latest_market_data_date and trading_date <= str(latest_market_data_date)[:10]:
            return _checkpoint(key, "ok", "market data ready", observed=observed, decision="ok: market data ready")
        return _checkpoint(key, "blocked", "market data missing", observed=observed, decision="blocked: market data missing")
    if not configured:
        return _checkpoint(key, "pending", "mode not configured", observed="mode not configured", decision="pending: mode not configured")
    if key == "SIGNAL_READY":
        signals = grouped["signals"]
        context["signals"] = signals
        signal_details = _run_signal_details(signals, root=root)
        pending_signals = [
            signal for signal in signals
            if str(signal.get("status") or "").lower() in _pending_signal_statuses()
        ]
        context["pending_signals"] = pending_signals
        if not signals:
            context["no_signal"] = True
            return _checkpoint(key, "ok", "no signal", observed="0 signal row(s)", decision="ok no-op: no signal emitted", details=[])
        if any(str(signal.get("status") or "").lower() in _failed_signal_statuses() for signal in signals):
            return _checkpoint(
                key,
                "blocked",
                "signal failed",
                observed=f"{len(signals)} signal row(s): {_format_status_counts(signals)}",
                decision="blocked: failed signal status present",
                details=signal_details,
            )
        return _checkpoint(
            key,
            "ok",
            f"{len(signals)} signal(s)",
            observed=f"{len(signals)} signal row(s): {_format_status_counts(signals)}",
            decision="ok: signal decision recorded",
            details=signal_details,
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
            return _checkpoint(key, "ok", "no order needed", observed="0 pending signal(s), 0 order row(s)", decision="ok no-op: no order required", details=[])
        submit_dates = [
            _signal_submit_date(signal, root=root)
            for signal in pending_signals
        ]
        due_signals = [
            signal for signal, submit_date in zip(pending_signals, submit_dates)
            if not submit_date or submit_date <= trading_date
        ]
        future_submit_dates = sorted({submit_date for submit_date in submit_dates if submit_date and submit_date > trading_date})
        if pending_signals and not due_signals and not orders:
            next_submit = future_submit_dates[0] if future_submit_dates else "-"
            details = _run_order_details(pending_signals, orders, root=root, trading_date=trading_date)
            return _checkpoint(
                key,
                "pending",
                "waiting submit date",
                observed=f"{len(pending_signals)} pending signal(s), next submit_date={next_submit}",
                decision="pending: submit date not reached",
                details=details,
            )
        required_signals = due_signals or pending_signals
        detail_signals = due_signals
        order_details = _run_order_details(detail_signals, orders, root=root, trading_date=trading_date)
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
            )
        if any(str(order.get("status") or "").lower() in _failed_signal_statuses() for order in orders):
            return _checkpoint(
                key,
                "blocked",
                "order failed",
                observed=f"{len(orders)} order row(s): {_format_status_counts(orders)}",
                decision="blocked: failed order status present",
                details=order_details,
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
            return _checkpoint(
                key,
                "blocked",
                "no fill",
                observed=observed,
                decision="blocked: no fills for due signals",
                details=order_details,
            )
        if signal_count and signal_qty > 0 and filled_qty + 1e-9 < signal_qty:
            return _checkpoint(
                key,
                "warning",
                "partial fill",
                observed=observed,
                decision="warning: partially filled due signals",
                details=order_details,
            )
        return _checkpoint(
            key,
            "ok",
            "all filled" if signal_count else f"{len(orders)} order(s)",
            observed=observed if signal_count else f"{len(orders)} order row(s): {_format_status_counts(orders)}",
            decision="ok: all due signals filled" if signal_count else "ok: order records present",
            details=order_details,
        )
    if key == "EXECUTION_CONFIRMED":
        fills = grouped["fills"]
        context["fills"] = fills
        if not context.get("orders") and not fills:
            return _checkpoint(key, "ok", "no execution needed", observed="0 order row(s), 0 fill row(s)", decision="ok no-op: no execution required")
        if not fills:
            return _checkpoint(
                key,
                "blocked",
                "fill missing",
                observed=f"{len(context.get('orders', []))} order row(s), 0 fill row(s)",
                decision="blocked: fill missing",
            )
        statuses = {str(order.get("display_status") or order.get("status") or "").lower() for order in context.get("orders", [])}
        if any(status in {"partial", "no_fill"} for status in statuses):
            return _checkpoint(
                key,
                "blocked",
                "execution incomplete",
                observed=f"{len(fills)} fill row(s), order statuses={', '.join(sorted(statuses))}",
                decision="blocked: partial/no_fill execution status",
            )
        missing = [
            str(fill.get("symbol") or "")
            for fill in fills
            if str(fill.get("side") or "").upper() == "BUY"
            and _float((positions_data.get(str(fill.get("symbol") or "")) or {}).get("qty")) <= 0
        ]
        if missing:
            symbols = ", ".join(sorted({item for item in missing if item})[:3])
            return _checkpoint(
                key,
                "blocked",
                f"position missing: {symbols}",
                observed=f"missing tracked position(s): {symbols}",
                decision="blocked: position not synced",
            )
        return _checkpoint(
            key,
            "ok",
            f"{len(fills)} fill(s)",
            observed=f"{len(fills)} fill row(s)",
            decision="ok: execution confirmed",
        )
    if key == "SNAPSHOT_WRITTEN":
        snapshots = grouped["snapshots"]
        if not snapshots:
            return _checkpoint(key, "blocked", "snapshot missing", observed="0 snapshot row(s)", decision="blocked: snapshot missing")
        return _checkpoint(
            key,
            "ok",
            f"{len(snapshots)} snapshot(s)",
            observed=f"{len(snapshots)} snapshot row(s)",
            decision="ok: snapshot written",
        )
    return _checkpoint(key, "pending", "unknown checkpoint", observed="unknown checkpoint", decision="pending: unknown checkpoint")


def _run_signal_details(signals: List[Dict[str, Any]], *, root: Path) -> List[Dict[str, Any]]:
    details: List[Dict[str, Any]] = []
    for signal in signals:
        details.append({
            "timestamp": str(signal.get("timestamp") or ""),
            "signal_date": _checkpoint_record_date(signal),
            "submit_date": _signal_submit_date(signal, root=root),
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
    root: Path,
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
                root=root,
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
        fallback_signals.setdefault(_run_signal_submit_match_key(signals[signal_index], root=root), []).append(signal_index)
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
                root=root,
                trading_date=trading_date,
                submitted_qty=submitted_qty,
                filled_qty=filled_qty,
            )
    return [detail for detail in details if detail is not None]


def _run_order_detail_for_signal(
    signal: Dict[str, Any],
    *,
    root: Path,
    trading_date: str,
    submitted_qty: float,
    filled_qty: float,
) -> Dict[str, Any]:
    signal_qty = _float(signal.get("quantity"))
    submit_date = _signal_submit_date(signal, root=root)
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


def _run_signal_submit_match_key(signal: Dict[str, Any], *, root: Path) -> tuple[str, str, str]:
    return (
        str(signal.get("symbol") or "").split(".")[0],
        str(signal.get("side") or "").upper(),
        _signal_submit_date(signal, root=root),
    )


def _effective_submitted_quantity(order: Dict[str, Any]) -> float:
    return max(_float(order.get("quantity")), _float(order.get("filled_qty")))


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


def _orders_for_signal(signal: Dict[str, Any], orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    signal_ids = set(_record_identifiers(signal))
    if signal_ids:
        matches = [
            order for order in orders
            if signal_ids.intersection(_record_identifiers(order))
        ]
        if matches:
            return matches
    signal_symbol = str(signal.get("symbol") or "").split(".")[0]
    signal_side = str(signal.get("side") or "").upper()
    signal_qty = _float(signal.get("quantity"))
    return [
        order for order in orders
        if str(order.get("symbol") or "").split(".")[0] == signal_symbol
        and str(order.get("side") or "").upper() == signal_side
        and abs(_float(order.get("quantity")) - signal_qty) <= 1e-9
    ]


def _format_quantity(value: float) -> str:
    if abs(value - round(value)) <= 1e-9:
        return str(int(round(value)))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _checkpoint(
    key: str,
    status: str,
    message: str,
    *,
    observed: Optional[str] = None,
    decision: Optional[str] = None,
    details: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    payload = {
        "key": key,
        "status": status,
        "message": message,
        "expected": _run_status_expected(key),
        "observed": observed or message,
        "decision": decision or message,
    }
    if details is not None:
        payload["details"] = details
    return payload


def _run_status_expected(key: str) -> str:
    for step in RUN_STATUS_STEPS:
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


def _parse_run_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


def _previous_weekday_before(value: date) -> date:
    current = value - timedelta(days=1)
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current


def _dashboard_order_rows(
    mode: str,
    orders: List[Dict[str, Any]],
    fills: List[Dict[str, Any]],
    open_prices: Optional[Dict[tuple[str, str], float]] = None,
    *,
    commission_config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    open_prices = open_prices or {}
    fill_totals: Dict[tuple[str, str], Dict[str, float]] = {}
    for fill in fills:
        identifiers = _record_identifiers(fill)
        fill_date = _record_date(fill)
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
    for order in sorted(orders, key=lambda item: item.get("timestamp", "")):
        row = dict(order)
        totals = _fill_totals_for_order(fill_totals, order)
        filled_qty = totals["quantity"]
        limit_price = _float(order.get("price"))
        raw_fill_price = totals["value"] / filled_qty if filled_qty > 0 else None
        display_fill_price = _display_fill_price(mode, limit_price, raw_fill_price, filled_qty)
        commission = _display_commission(
            mode,
            order,
            filled_qty,
            raw_fill_price,
            totals["commission"],
            commission_config,
        )
        open_price = open_prices.get(_order_open_key(order))
        row["limit_price"] = limit_price
        row["open_price"] = open_price
        row["filled_qty"] = filled_qty
        row["raw_fill_price"] = raw_fill_price
        row["fill_price"] = display_fill_price
        row["commission"] = commission
        row["slippage_bps"] = _order_slippage_bps(open_price, display_fill_price, filled_qty)
        row["display_contract"] = "paper_limit_fill" if mode == "paper" else "live_actual_fill"
        order_qty = _float(order.get("quantity"))
        if filled_qty <= 0:
            row["display_status"] = "no_fill"
        elif filled_qty + 1e-9 >= order_qty:
            row["display_status"] = "filled"
        else:
            row["display_status"] = "partial"
        rows.append(row)
    return rows


def _dashboard_fill_rows(
    mode: str,
    orders: List[Dict[str, Any]],
    fills: List[Dict[str, Any]],
    *,
    commission_config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    if mode != "paper":
        return [dict(fill) for fill in fills]
    order_dates = {
        (identifier, _record_date(order))
        for order in orders
        for identifier in _record_identifiers(order)
        if _record_date(order)
    }
    grouped: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
    passthrough: List[Dict[str, Any]] = []
    for fill in fills:
        row = dict(fill)
        keys = [
            (identifier, _record_date(row))
            for identifier in _record_identifiers(row)
            if _record_date(row)
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


def _open_prices_for_orders(root: Path, orders: List[Dict[str, Any]]) -> Dict[tuple[str, str], float]:
    keys = {_order_open_key(order) for order in orders}
    keys = {(symbol, trading_date) for symbol, trading_date in keys if symbol and trading_date}
    if not keys:
        return {}
    symbols = sorted({symbol for symbol, _ in keys})
    dates = sorted({trading_date for _, trading_date in keys})
    sources = [
        (root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_ohlcv.duckdb", "daily_cn_ochl"),
        (root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_etf_ohlcv.duckdb", "daily_cn_ochl"),
        (root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_index_ohlcv.duckdb", "daily_cn_ochl"),
    ]
    prices: Dict[tuple[str, str], float] = {}
    symbol_placeholders = ",".join("?" for _ in symbols)
    date_placeholders = ",".join("?" for _ in dates)
    query = f"""
        select symbol, cast(timestamp as date) as trading_date, open
        from {{table}}
        where symbol in ({symbol_placeholders})
          and cast(timestamp as date) in ({date_placeholders})
    """
    for db_path, table in sources:
        if not db_path.exists():
            continue
        try:
            import duckdb

            with duckdb.connect(str(db_path), read_only=True) as con:
                rows = con.execute(query.format(table=table), [*symbols, *dates]).fetchall()
        except Exception:
            continue
        for symbol, trading_date, open_price in rows:
            key = (str(symbol), str(trading_date)[:10])
            price = _float(open_price)
            if key in keys and key not in prices and price > 0:
                prices[key] = price
    return prices


def _order_open_key(order: Dict[str, Any]) -> tuple[str, str]:
    symbol = str(order.get("symbol") or "").split(".")[0]
    return (symbol, _record_date(order))


def _fill_totals_for_order(
    fill_totals: Dict[tuple[str, str], Dict[str, float]],
    order: Dict[str, Any],
) -> Dict[str, float]:
    order_date = _record_date(order)
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


def _record_identifiers(record: Dict[str, Any]) -> List[str]:
    identifiers: List[str] = []
    for key in ("order_id", "broker_order_id"):
        value = str(record.get(key) or "")
        if value and value not in identifiers:
            identifiers.append(value)
    return identifiers


def _display_fill_price(
    mode: str,
    limit_price: float,
    raw_fill_price: Optional[float],
    filled_qty: float,
) -> Optional[float]:
    if filled_qty <= 0:
        return None
    if mode == "paper" and limit_price > 0:
        return limit_price
    return raw_fill_price


def _order_slippage_bps(
    open_price: Optional[float],
    fill_price: Optional[float],
    filled_qty: float,
) -> Optional[float]:
    if filled_qty <= 0 or open_price is None or open_price <= 0 or fill_price is None or fill_price <= 0:
        return None
    return round((fill_price / open_price - 1.0) * 10000.0, 6)


def _dashboard_execution_summary(order_rows: List[Dict[str, Any]], fills: List[Dict[str, Any]]) -> Dict[str, Any]:
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


def _dashboard_signal_rows(
    root: Path,
    signals: List[Dict[str, Any]],
    orders: List[Dict[str, Any]],
    fills: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    submitted_ids = _submitted_order_ids(orders, fills)
    submitted_signatures = [
        (_order_signature(record), _order_action_signature(record), _record_date(record), _timestamp_text(record))
        for record in orders
        if _is_submitted_order(record)
    ]
    rows = []
    filled_order_ids = {str(f.get("order_id") or "") for f in fills if f.get("order_id")}
    for signal in sorted(signals, key=lambda item: item.get("timestamp", "")):
        status = str(signal.get("status") or "").lower()
        if status not in _pending_signal_statuses() and status not in _failed_signal_statuses():
            continue
        if _is_dashboard_submit_attempt_signal(signal, root, submitted_ids, submitted_signatures):
            continue
        if float(signal.get("fill_quantity", 0.0)) > 0:
            continue
        if str(signal.get("order_id") or "") in filled_order_ids:
            continue
        rows.append(signal)
    return rows


def _is_dashboard_submit_attempt_signal(
    signal: Dict[str, Any],
    root: Path,
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
    submit_date = _signal_submit_date(signal, root=root, failed=False)
    return _signal_is_submitted(signal, submitted_ids, submitted_signatures, submit_date)


def _apply_execution_summary(performance: Dict[str, Any], summary: Dict[str, Any]) -> None:
    performance["total_commission"] = summary.get("total_commission", 0.0)
    performance["slippage_sample_count"] = summary.get("slippage_sample_count", 0)
    performance["median_slippage_bps"] = summary.get("median_slippage_bps")
    performance["weighted_avg_slippage_bps"] = summary.get("weighted_avg_slippage_bps")


def _pending_submit_orders(
    root: Path,
    signals: List[Dict[str, Any]],
    orders: List[Dict[str, Any]],
    fills: List[Dict[str, Any]],
    *,
    as_of_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    submitted_ids = _submitted_order_ids(orders, fills)
    submitted_signatures = [
        (_order_signature(record), _order_action_signature(record), _record_date(record), _timestamp_text(record))
        for record in orders
        if _is_submitted_order(record)
    ]
    close_prices = _signal_close_prices(root, signals)
    pending = []
    for signal in sorted(signals, key=lambda item: item.get("timestamp", "")):
        status = str(signal.get("status", "")).lower()
        if status not in _pending_signal_statuses() and status not in _failed_signal_statuses():
            continue
        failed = status in _failed_signal_statuses()
        submit_date = _signal_submit_date(signal, root=root, failed=failed)
        if not failed and _signal_is_submitted(signal, submitted_ids, submitted_signatures, submit_date):
            continue
        signal_date = _record_date(signal)
        row = dict(signal)
        row["signal_date"] = signal_date
        row["submit_date"] = submit_date
        if _date_before(row["submit_date"], as_of_date):
            continue
        cost_bps = _pending_submit_cost_bps(
            row,
            close_prices.get(_signal_close_key(row)),
        )
        if cost_bps is not None:
            row["cost_bps"] = cost_bps
            row["cost_bps_display"] = f"+{cost_bps:.1f} bps"
        row["display_status"] = "failed" if failed else "pending_submit"
        pending.append(row)
    return pending


def _pending_signal_statuses() -> set[str]:
    return {"accepted", "pending", "queued", "pending_submit"}


def _failed_signal_statuses() -> set[str]:
    return {"rejected", "failed", "error", "dropped", "cancelled", "canceled", "expired"}


def _is_submitted_order(record: Dict[str, Any]) -> bool:
    return str(record.get("status") or "").lower() not in _failed_signal_statuses()


def _signal_submit_date(signal: Dict[str, Any], *, root: Path, failed: bool = False) -> str:
    explicit = str(signal.get("submit_date") or signal.get("execution_date") or "")[:10]
    if explicit:
        return explicit
    signal_date = _record_date(signal)
    if failed or _is_intraday_signal(signal):
        return signal_date
    return str(_next_trading_date(signal_date, root=root) or "")


def _is_intraday_signal(signal: Dict[str, Any]) -> bool:
    timestamp = str(signal.get("timestamp") or "")
    if "T" not in timestamp:
        return False
    time_text = timestamp.split("T", 1)[1][:8]
    return bool(time_text) and time_text < "15:00:00"


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


def _signal_close_prices(root: Path, signals: List[Dict[str, Any]]) -> Dict[tuple[str, str], float]:
    keys = {_signal_close_key(signal) for signal in signals}
    keys = {(symbol, trading_date) for symbol, trading_date in keys if symbol and trading_date}
    if not keys:
        return {}
    symbols = sorted({symbol for symbol, _ in keys})
    dates = sorted({trading_date for _, trading_date in keys})
    sources = [
        (root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_ohlcv.duckdb", "daily_cn_ochl"),
        (root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_etf_ohlcv.duckdb", "daily_cn_ochl"),
        (root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_index_ohlcv.duckdb", "daily_cn_ochl"),
    ]
    prices: Dict[tuple[str, str], float] = {}
    symbol_placeholders = ",".join("?" for _ in symbols)
    date_placeholders = ",".join("?" for _ in dates)
    query = f"""
        select symbol, cast(timestamp as date) as trading_date, close
        from {{table}}
        where symbol in ({symbol_placeholders})
          and cast(timestamp as date) in ({date_placeholders})
    """
    for db_path, table in sources:
        if not db_path.exists():
            continue
        try:
            import duckdb

            with duckdb.connect(str(db_path), read_only=True) as con:
                rows = con.execute(query.format(table=table), [*symbols, *dates]).fetchall()
        except Exception:
            continue
        for symbol, trading_date, close_price in rows:
            key = (str(symbol), str(trading_date)[:10])
            price = _float(close_price)
            if key in keys and key not in prices and price > 0:
                prices[key] = price
    return prices


def _signal_close_key(signal: Dict[str, Any]) -> tuple[str, str]:
    symbol = str(signal.get("symbol") or "").split(".")[0]
    return (symbol, _record_date(signal))


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


def _record_date(record: Dict[str, Any]) -> str:
    timestamp = str(record.get("timestamp") or "")
    if len(timestamp) >= 10:
        return timestamp[:10]
    return str(record.get("record_date") or "")


def _next_trading_date(value: str, *, root: Path) -> Optional[str]:
    if not value:
        return None
    try:
        day = datetime.fromisoformat(value[:10]).date()
    except ValueError:
        return None
    return next_trading_date_after(
        day,
        cache_path=root / "quant" / "infrastructure" / "var" / "calendar" / "cn_trade_calendar_sse.json",
        duckdb_dir=root / "quant" / "infrastructure" / "var" / "duckdb" / "live",
        allow_refresh=False,
    ).isoformat()


def _date_before(left: str, right: Optional[str]) -> bool:
    if not left or not right:
        return False
    try:
        return datetime.fromisoformat(left[:10]).date() < datetime.fromisoformat(right[:10]).date()
    except ValueError:
        return False


def _position_symbols(positions_grouped: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]]) -> set[str]:
    symbols: set[str] = set()
    for strategy_modes in positions_grouped.values():
        for mode_positions in strategy_modes.values():
            symbols.update(str(symbol) for symbol in mode_positions.keys() if symbol)
    return symbols


def _latest_close_prices(root: Path, symbols: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    requested = sorted({str(symbol).split(".")[0] for symbol in symbols if symbol})
    if not requested:
        return {}
    sources = [
        ("stock", root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_ohlcv.duckdb", "daily_cn_ochl"),
        ("etf", root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_etf_ohlcv.duckdb", "daily_cn_ochl"),
        ("index", root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_index_ohlcv.duckdb", "daily_cn_ochl"),
    ]
    prices: Dict[str, Dict[str, Any]] = {}
    for source_name, db_path, table in sources:
        if not db_path.exists():
            continue
        remaining = [symbol for symbol in requested if symbol not in prices]
        if not remaining:
            break
        placeholders = ",".join("?" for _ in remaining)
        query = f"""
            select symbol, cast(timestamp as date) as price_date, close
            from {table}
            where symbol in ({placeholders})
            qualify row_number() over (partition by symbol order by timestamp desc) = 1
        """
        try:
            import duckdb

            with duckdb.connect(str(db_path), read_only=True) as con:
                rows = con.execute(query, remaining).fetchall()
        except Exception:
            continue
        for symbol, price_date, close in rows:
            price = _float(close)
            if price <= 0:
                continue
            prices[str(symbol)] = {
                "price": price,
                "date": str(price_date)[:10],
                "source": source_name,
            }
    return prices


def _latest_price_date(prices: Dict[str, Dict[str, Any]]) -> Optional[str]:
    dates = [
        str(item.get("date", ""))
        for item in prices.values()
        if item.get("date")
    ]
    return sorted(dates)[-1] if dates else None


def _latest_market_data_date(root: Path) -> Optional[str]:
    try:
        return resolve_latest_data_date(
            duckdb_dir=root / "quant" / "infrastructure" / "var" / "duckdb" / "live",
        ).isoformat()
    except Exception:
        return None


def _holdings_for_strategy(
    all_positions_grouped: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]],
    strategy_name: str,
    mode: str,
    fills: List[Dict[str, Any]],
    latest_prices: Optional[Dict[str, Dict[str, Any]]] = None,
    order_rows: Optional[List[Dict[str, Any]]] = None,
    initial_cash: float = 0.0,
    capital_events: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    stored_positions = all_positions_grouped.get(strategy_name, {}).get(mode, {}) or {}
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
    for symbol in sorted(set(stored_positions) | set(contract_positions)):
        raw = stored_positions.get(symbol, {}) or {}
        contract_position = contract_positions.get(symbol)
        if contract_position:
            qty = _float(contract_position.get("qty"))
            avg_cost = _float(contract_position.get("avg_cost"))
            cost_value = _float(contract_position.get("cost_value"))
        else:
            qty = _float(raw.get("qty"))
            avg_cost = _float(raw.get("avg_cost"))
            cost_value = avg_cost * qty
        if qty <= 0:
            continue
        stored_market_value = _float(raw.get("market_value"))
        close_price = latest_prices.get(str(symbol).split(".")[0], {})
        stale_after_activity = False
        symbol_activity_date = str(contract_activity_dates.get(symbol) or "")
        if close_price and contract_position:
            close_date = str(close_price.get("date", ""))[:10]
            stale_after_activity = bool(symbol_activity_date and close_date and close_date < symbol_activity_date)
        if close_price and not stale_after_activity:
            current_price = _float(close_price.get("price"))
            price_date = str(close_price.get("date", ""))
            price_source = str(close_price.get("source", "duckdb"))
            valuation_status = "marked"
        elif contract_position and stale_after_activity:
            current_price = _float(contract_position.get("unmarked_price")) or avg_cost
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
    if has_contract_activity and initial_cash > 0:
        cash = initial_cash + _float(contract_state["cash_delta"]) + capital_delta
        cash_source = "order_contract"
        nav = cash + total_market_value
    elif holdings and initial_cash > 0:
        cash = initial_cash - total_market_value + capital_delta
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
    total_pnl = nav - initial_cash if cash_source in ("order_contract", "initial_cash", "position_baseline") else realized + total_unrealized
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


def _performance(
    root: Path,
    base_dir: Path,
    strategy_name: str,
    holdings: Dict[str, Any],
    records: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        performance = LiveTradingRecorder(base_dir).get_strategy_performance_from_records(strategy_name, records or {})
    except Exception:
        performance = {}
    curve = performance.get("pnl_curve") or []
    initial_cash = _float(holdings.get("initial_cash"))
    latest_snapshot = _snapshot_from_holdings(strategy_name, holdings)
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
    denominator = initial_cash if holdings.get("cash_source") in {"order_contract", "initial_cash", "position_baseline"} and initial_cash > 0 else total_cost
    performance["total_return"] = (
        holdings.get("total_pnl", 0.0) / denominator
        if denominator and denominator > 0
        else performance.get("total_return", 0.0)
    )
    if holdings.get("cash_source") in {"order_contract", "initial_cash", "position_baseline"}:
        performance["total_nav"] = holdings.get("nav", 0.0)
        performance["cash"] = holdings.get("cash", 0.0)
        performance["total_nav_source"] = (
            "current_execution_state"
            if holdings.get("cash_source") == "order_contract"
            else holdings.get("cash_source")
        )
    if latest_snapshot is not None:
        performance["latest_snapshot"] = latest_snapshot
        if holdings.get("cash_source") not in {"order_contract", "initial_cash", "position_baseline"} and _float(performance.get("total_nav")) <= 0:
            performance["total_nav"] = latest_snapshot.get("nav", 0.0)
            performance["total_nav_source"] = "latest_snapshot"
    performance.setdefault("total_nav", 0.0)
    performance.setdefault("total_nav_source", "latest_snapshot")
    performance["pnl_curve"] = curve
    return performance


def _curve_from_order_rows(
    *,
    root: Path,
    strategy_name: str,
    order_rows: List[Dict[str, Any]],
    initial_cash: float,
    latest_market_data_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    max_mark_date = str(latest_market_data_date or date.today().isoformat())[:10]
    filled_rows = [
        row for row in order_rows
        if _float(row.get("filled_qty")) > 0 and _float(row.get("fill_price")) > 0
        and _record_date(row) <= max_mark_date
    ]
    if initial_cash <= 0 or not filled_rows:
        return []
    symbols = sorted({str(row.get("symbol") or "").split(".")[0] for row in filled_rows if row.get("symbol")})
    dates = [_record_date(row) for row in filled_rows if _record_date(row)]
    if not symbols or not dates:
        return []
    start = min(dates)
    end = max_mark_date
    price_history = _close_price_history(root, symbols, start, end)
    trading_dates = sorted({
        price_date
        for symbol_prices in price_history.values()
        for price_date in symbol_prices
        if start <= price_date <= end
    })
    if not trading_dates:
        trading_dates = sorted(set(dates))
    lots: Dict[str, List[Dict[str, float]]] = {}
    cash = float(initial_cash)
    realized = 0.0
    baseline_day = previous_trading_date_before(
        datetime.fromisoformat(start[:10]).date(),
        cache_path=root / "quant" / "infrastructure" / "var" / "calendar" / "cn_trade_calendar_sse.json",
        duckdb_dir=root / "quant" / "infrastructure" / "var" / "duckdb" / "live",
        allow_refresh=False,
    )
    baseline_date = baseline_day.isoformat()
    curve = [{
        "date": baseline_date,
        "timestamp": f"{baseline_date}T23:59:59",
        "strategy_name": strategy_name,
        "nav": initial_cash,
        "market_value": 0.0,
        "cash": initial_cash,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "total_pnl": 0.0,
    }]
    sorted_rows = sorted(filled_rows, key=lambda item: item.get("timestamp", ""))
    row_index = 0
    for trading_date in trading_dates:
        while row_index < len(sorted_rows) and _record_date(sorted_rows[row_index]) <= trading_date:
            row = sorted_rows[row_index]
            row_index += 1
            symbol = str(row.get("symbol") or "").split(".")[0]
            side = str(row.get("side") or "").upper()
            qty = _float(row.get("filled_qty"))
            fill_price = _float(row.get("fill_price"))
            commission = _float(row.get("commission"))
            if not symbol or qty <= 0 or fill_price <= 0:
                continue
            notional = qty * fill_price
            if side == "BUY":
                unit_cost = (notional + commission) / qty
                lots.setdefault(symbol, []).append({"qty": qty, "unit_cost": unit_cost})
                cash -= notional + commission
            elif side == "SELL":
                remaining = qty
                removed_cost = 0.0
                for lot in lots.get(symbol, []):
                    if remaining <= 0:
                        break
                    take = min(_float(lot.get("qty")), remaining)
                    if take <= 0:
                        continue
                    removed_cost += take * _float(lot.get("unit_cost"))
                    lot["qty"] = _float(lot.get("qty")) - take
                    remaining -= take
                lots[symbol] = [lot for lot in lots.get(symbol, []) if _float(lot.get("qty")) > 1e-9]
                if remaining > 1e-9:
                    removed_cost += remaining * fill_price
                cash += notional - commission
                realized += notional - commission - removed_cost
        market_value = 0.0
        cost_value = 0.0
        for symbol, symbol_lots in lots.items():
            qty = sum(_float(lot.get("qty")) for lot in symbol_lots)
            if qty <= 0:
                continue
            cost_value += sum(_float(lot.get("qty")) * _float(lot.get("unit_cost")) for lot in symbol_lots)
            price = _latest_price_on_or_before(price_history.get(symbol, {}), trading_date)
            if price is None:
                price = _weighted_avg_lot_cost(symbol_lots)
            market_value += qty * price
        nav = cash + market_value
        unrealized = market_value - cost_value
        curve.append({
            "date": trading_date,
            "timestamp": f"{trading_date}T15:00:00",
            "strategy_name": strategy_name,
            "nav": nav,
            "market_value": market_value,
            "cash": cash,
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "total_pnl": nav - initial_cash,
        })
    return curve


def _close_price_history(root: Path, symbols: List[str], start: str, end: str) -> Dict[str, Dict[str, float]]:
    if not symbols:
        return {}
    sources = [
        (root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_ohlcv.duckdb", "daily_cn_ochl"),
        (root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_etf_ohlcv.duckdb", "daily_cn_ochl"),
        (root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_index_ohlcv.duckdb", "daily_cn_ochl"),
    ]
    history: Dict[str, Dict[str, float]] = {symbol: {} for symbol in symbols}
    for db_path, table in sources:
        if not db_path.exists():
            continue
        remaining = [symbol for symbol in symbols if not history.get(symbol)]
        if not remaining:
            break
        placeholders = ",".join("?" for _ in remaining)
        query = f"""
            select symbol, cast(timestamp as date) as price_date, close
            from {table}
            where symbol in ({placeholders})
              and cast(timestamp as date) between ? and ?
            order by symbol, timestamp
        """
        try:
            import duckdb

            with duckdb.connect(str(db_path), read_only=True) as con:
                rows = con.execute(query, [*remaining, start, end]).fetchall()
        except Exception:
            continue
        for symbol, price_date, close in rows:
            price = _float(close)
            if price > 0:
                history.setdefault(str(symbol), {})[str(price_date)[:10]] = price
    return history


def _latest_price_on_or_before(prices: Dict[str, float], trading_date: str) -> Optional[float]:
    eligible = [date_text for date_text in prices if date_text <= trading_date]
    if not eligible:
        return None
    return prices[max(eligible)]


def _weighted_avg_lot_cost(lots: List[Dict[str, float]]) -> float:
    qty = sum(_float(lot.get("qty")) for lot in lots)
    if qty <= 0:
        return 0.0
    return sum(_float(lot.get("qty")) * _float(lot.get("unit_cost")) for lot in lots) / qty


def _snapshot_from_holdings(strategy_name: str, holdings: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    market_value = _float(holdings.get("total_market_value"))
    total_cost = _float(holdings.get("total_cost"))
    if market_value <= 0 and total_cost <= 0 and holdings.get("cash_source") != "initial_cash":
        return None
    cash = _float(holdings.get("cash")) if holdings.get("cash_source") in {"order_contract", "initial_cash", "position_baseline"} else 0.0
    nav = _float(holdings.get("nav")) if holdings.get("cash_source") in {"order_contract", "initial_cash", "position_baseline"} else market_value
    snapshot_date = str(holdings.get("price_date") or "")
    if holdings.get("cash_source") == "order_contract":
        latest_activity_date = str(holdings.get("latest_activity_date") or "")[:10]
        if not snapshot_date or (latest_activity_date and snapshot_date < latest_activity_date):
            return None
    if not snapshot_date:
        snapshot_date = date.today().isoformat()
    return {
        "date": snapshot_date,
        "timestamp": datetime.now().isoformat(),
        "strategy_name": strategy_name,
        "nav": nav,
        "market_value": market_value,
        "cash": cash,
        "realized_pnl": holdings.get("realized_pnl", 0.0),
        "unrealized_pnl": holdings.get("unrealized_pnl", 0.0),
        "total_pnl": holdings.get("total_pnl", 0.0),
    }


def _benchmark_curve(root: Path, max_points: int = 180) -> Dict[str, Any]:
    db_path = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_index_ohlcv.duckdb"
    if not db_path.exists():
        return {"name": "沪深300", "symbol": "000300", "points": []}
    try:
        import duckdb

        with duckdb.connect(str(db_path), read_only=True) as con:
            rows = con.execute(
                """
                select cast(timestamp as date) as d, close
                from daily_cn_ochl
                where symbol = '000300'
                order by d desc
                limit ?
                """,
                [max_points],
            ).fetchall()
    except Exception:
        return {"name": "沪深300", "symbol": "000300", "points": []}
    points = [
        {"date": str(row[0])[:10], "value": _float(row[1])}
        for row in reversed(rows)
        if _float(row[1]) > 0
    ]
    return {"name": "沪深300", "symbol": "000300", "points": points}


def _report_path(root: Path, strategy_name: str) -> Optional[Path]:
    path = root / "quant" / "features" / "strategies" / strategy_name / "full_research_report.html"
    return path if path.exists() else None


def _humanize_strategy_name(strategy_name: str) -> str:
    return strategy_name.replace("_", " ").title()


def _float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def _optional_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _median(values: List[float]) -> float:
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    if count % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    DASHBOARD_HTML.parent.mkdir(parents=True, exist_ok=True)
    app = create_app(ROOT)
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
