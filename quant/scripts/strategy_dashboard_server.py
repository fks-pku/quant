#!/usr/bin/env python3
"""Local strategy operations dashboard server."""

import argparse
import copy
import math
import re
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Iterable, List, Optional

import yaml
from flask import Flask, jsonify, request, send_file

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
DISPLAY_NAMES = {
    "ashare_gold_equity_barbell_timing": "A股黄金权益杠铃择时",
    "xueqiu_small_cap_financial_filter": "雪球小市值财务过滤",
    "default": "Default / Manual Orders",
}
DASHBOARD_HTML = ROOT / ".codex" / "strategy_dashboard.html"
BROKER_POSITION_SNAPSHOT_TTL_SECONDS = 1800
BROKER_POSITION_SNAPSHOT_FAILURE_TTL_SECONDS = 300
BROKER_POSITION_SNAPSHOT_MIN_FREE_BYTES = 1024 * 1024 * 1024
_BROKER_POSITION_SNAPSHOT_CACHE: Dict[str, Dict[str, Any]] = {}
_BROKER_POSITION_SNAPSHOT_CACHE_LOCK = RLock()

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
from quant.features.trading.dashboard_projection import (
    RUN_STATUS_STEPS,
    project_execution_summary,
    project_fill_rows,
    project_holdings,
    project_order_rows,
    project_performance,
    project_pending_orders,
    project_run_status_bar,
    project_signal_rows,
)


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
    broker_position_snapshot = _live_broker_position_snapshot(root)
    db_strategy_names = set(state_store.get_all_known_strategy_names())
    strategy_names = _discover_strategy_names(
        live_config=live_config,
        paper_config=paper_config,
        db_strategy_names=db_strategy_names,
    )
    benchmark = _benchmark_curve(root)
    all_position_symbols = _position_symbols(all_positions_grouped)
    all_position_symbols.update(symbol_set)
    all_position_symbols.update(
        str(item.get("symbol") or "")
        for item in broker_position_snapshot.get("positions", [])
        if item.get("symbol")
    )
    latest_prices = _latest_close_prices(root, all_position_symbols)
    latest_market_data_date = _latest_market_data_date(root) or _latest_price_date(latest_prices)
    if latest_market_data_date and "__market__" not in latest_prices:
        latest_prices["__market__"] = {
            "date": latest_market_data_date,
            "price": 1.0,
            "source": "market_calendar",
        }
    default_strategy_cash = _default_strategy_initial_cash(root)
    manual_default_holdings = _manual_default_holdings(
        broker_position_snapshot,
        all_positions_grouped,
        latest_prices,
    )
    if manual_default_holdings.get("items") and "default" not in strategy_names:
        strategy_names.append("default")

    strategies = []
    for strategy_name in strategy_names:
        manual_default = strategy_name == "default"
        live_configured = bool(live_config.get(strategy_name, {}).get("enabled", False)) and not manual_default
        paper_configured = bool(paper_config.get(strategy_name, {}).get("enabled", False)) and not manual_default
        live_initial_cash = 0.0 if manual_default else _configured_strategy_initial_cash(
            live_config.get(strategy_name),
            default_cash=default_strategy_cash,
        )
        paper_initial_cash = 0.0 if manual_default else _configured_strategy_initial_cash(
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
        live_holdings = (
            manual_default_holdings
            if manual_default and manual_default_holdings.get("items")
            else project_holdings(
                stored_positions=all_positions_grouped.get(strategy_name, {}).get("live", {}) or {},
                fills=live_records["fills"],
                latest_prices=latest_prices,
                order_rows=live_records["orders"],
                initial_cash=live_initial_cash,
                capital_events=state_store.get_capital_events(strategy_name=strategy_name, mode="live"),
            )
        )
        paper_holdings = project_holdings(
            stored_positions=all_positions_grouped.get(strategy_name, {}).get("paper", {}) or {},
            fills=paper_records["fills"],
            latest_prices=latest_prices,
            order_rows=paper_records["orders"],
            initial_cash=paper_initial_cash,
            capital_events=state_store.get_capital_events(strategy_name=strategy_name, mode="paper"),
        )
        live_performance = _performance(
            root,
            live_records_dir,
            strategy_name,
            live_holdings,
            live_records,
            latest_market_data_date=latest_market_data_date,
        )
        paper_performance = _performance(
            root,
            paper_records_dir,
            strategy_name,
            paper_holdings,
            paper_records,
            latest_market_data_date=latest_market_data_date,
        )
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
            latest_market_data_date=latest_market_data_date,
        )
        paper_run_status_bar = _run_status_bar(
            root=root,
            configured=paper_configured,
            control=paper_control,
            records=paper_records,
            latest_market_data_date=latest_market_data_date,
        )

        strategies.append({
            "name": strategy_name,
            "display_name": DISPLAY_NAMES.get(strategy_name, _humanize_strategy_name(strategy_name)),
            "manual": manual_default,
            "report_url": f"/reports/{strategy_name}" if report_path else None,
            "report_path": str(report_path) if report_path else None,
            "initial_cash": {
                "live": live_initial_cash,
                "paper": paper_initial_cash,
                "default": 0.0 if manual_default else default_strategy_cash,
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


def _live_broker_position_snapshot(root: Path) -> Dict[str, Any]:
    cfg_path = root / "quant" / "infrastructure" / "var" / "qmt_live_config" / "brokers.yaml"
    cfg = (_read_yaml(cfg_path).get("qmt", {}) or {})
    if not isinstance(cfg, dict) or not (cfg.get("userdata_mini_path") or cfg.get("mini_qmt_path")):
        return {
            "status": "unconfigured",
            "source": "qmt",
            "positions": [],
            "generated_at": datetime.now().isoformat(),
            "error": "qmt broker config is missing",
        }
    cache_key = _broker_position_snapshot_cache_key(root, cfg_path)
    cached = _broker_position_snapshot_from_cache(cache_key)
    if cached is not None:
        return cached
    low_disk_error = _qmt_userdata_low_disk_error(cfg)
    if low_disk_error:
        payload = {
            "status": "unavailable",
            "source": "qmt",
            "positions": [],
            "generated_at": datetime.now().isoformat(),
            "error": low_disk_error,
        }
        _store_broker_position_snapshot_cache(cache_key, payload)
        return copy.deepcopy(payload)
    payload = _live_broker_position_snapshot_uncached(root, cfg)
    _store_broker_position_snapshot_cache(cache_key, payload)
    return copy.deepcopy(payload)


def _live_broker_position_snapshot_uncached(root: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    subprocess_snapshot = _live_broker_position_snapshot_subprocess(root)
    if subprocess_snapshot is not None:
        return subprocess_snapshot
    broker = None
    try:
        from quant.infrastructure.execution.brokers.qmt import QMTBroker

        broker = QMTBroker(
            host=cfg.get("host", "127.0.0.1"),
            port=cfg.get("port", 58610),
            account=cfg.get("account", ""),
            account_type=cfg.get("account_type", "STOCK"),
            password=cfg.get("password", ""),
            trade_mode=cfg.get("trade_mode", "SIMULATE"),
            userdata_mini_path=cfg.get("userdata_mini_path", ""),
            xtquant_path=cfg.get("xtquant_path", ""),
            mini_qmt_path=cfg.get("mini_qmt_path", ""),
        )
        broker.connect()
        positions = [_broker_position_row(position) for position in broker.get_positions()]
        positions = [position for position in positions if position.get("symbol") and _float(position.get("quantity")) > 0]
        return {
            "status": "ok",
            "source": "qmt",
            "positions": positions,
            "generated_at": datetime.now().isoformat(),
            "error": "",
        }
    except Exception as exc:
        return {
            "status": "unavailable",
            "source": "qmt",
            "positions": [],
            "generated_at": datetime.now().isoformat(),
            "error": str(exc),
        }
    finally:
        if broker is not None:
            try:
                broker.disconnect()
            except Exception:
                pass


def _broker_position_snapshot_cache_key(root: Path, cfg_path: Path) -> str:
    try:
        root_key = str(root.resolve())
    except Exception:
        root_key = str(root)
    try:
        stat = cfg_path.stat()
        cfg_sig = f"{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        cfg_sig = "missing"
    return f"{root_key}|{cfg_sig}"


def _broker_position_snapshot_from_cache(cache_key: str) -> Optional[Dict[str, Any]]:
    now = time.monotonic()
    with _BROKER_POSITION_SNAPSHOT_CACHE_LOCK:
        entry = _BROKER_POSITION_SNAPSHOT_CACHE.get(cache_key)
        if not entry:
            return None
        payload = entry.get("payload") or {}
        status = str(payload.get("status") or "")
        ttl = (
            BROKER_POSITION_SNAPSHOT_TTL_SECONDS
            if status == "ok"
            else BROKER_POSITION_SNAPSHOT_FAILURE_TTL_SECONDS
        )
        if now - _float(entry.get("cached_monotonic")) > ttl:
            _BROKER_POSITION_SNAPSHOT_CACHE.pop(cache_key, None)
            return None
        return copy.deepcopy(payload)


def _store_broker_position_snapshot_cache(cache_key: str, payload: Dict[str, Any]) -> None:
    with _BROKER_POSITION_SNAPSHOT_CACHE_LOCK:
        _BROKER_POSITION_SNAPSHOT_CACHE[cache_key] = {
            "cached_monotonic": time.monotonic(),
            "payload": copy.deepcopy(payload),
        }


def _qmt_userdata_low_disk_error(cfg: Dict[str, Any]) -> str:
    anchor = _existing_disk_usage_anchor(
        str(cfg.get("userdata_mini_path") or cfg.get("mini_qmt_path") or "")
    )
    if anchor is None:
        return ""
    try:
        usage = shutil.disk_usage(str(anchor))
    except OSError:
        return ""
    if usage.free >= BROKER_POSITION_SNAPSHOT_MIN_FREE_BYTES:
        return ""
    free_mb = usage.free / (1024 * 1024)
    min_mb = BROKER_POSITION_SNAPSHOT_MIN_FREE_BYTES / (1024 * 1024)
    return f"qmt broker snapshot skipped: free disk {free_mb:.1f} MB below {min_mb:.0f} MB guard"


def _existing_disk_usage_anchor(path_text: str) -> Optional[Path]:
    if not path_text:
        return None
    path = Path(path_text)
    for candidate in [path, *path.parents]:
        if candidate.exists():
            return candidate
    return None


def _live_broker_position_snapshot_subprocess(root: Path) -> Optional[Dict[str, Any]]:
    python_exe = root / ".venv-qmt" / "Scripts" / "python.exe"
    if not python_exe.exists():
        return None
    marker = "__QMT_POSITION_SNAPSHOT__"
    script = r"""
from pathlib import Path
import json
import sys
root = Path.cwd()
sys.path.insert(0, str(root))
from quant.infrastructure.execution.brokers.qmt import QMTBroker
from quant.shared.utils.config_loader import ConfigLoader
cfg = ConfigLoader(str(root / "quant" / "infrastructure" / "var" / "qmt_live_config")).load("brokers.yaml").get("qmt", {})
broker = QMTBroker(
    host=cfg.get("host", "127.0.0.1"),
    port=cfg.get("port", 58610),
    account=cfg.get("account", ""),
    account_type=cfg.get("account_type", "STOCK"),
    password=cfg.get("password", ""),
    trade_mode=cfg.get("trade_mode", "SIMULATE"),
    userdata_mini_path=cfg.get("userdata_mini_path", ""),
    xtquant_path=cfg.get("xtquant_path", ""),
    mini_qmt_path=cfg.get("mini_qmt_path", ""),
)
try:
    broker.connect()
    rows = []
    for position in broker.get_positions():
        rows.append({
            "symbol": getattr(position, "symbol", ""),
            "quantity": float(getattr(position, "quantity", 0.0) or 0.0),
            "avg_cost": float(getattr(position, "avg_cost", 0.0) or 0.0),
            "market_value": float(getattr(position, "market_value", 0.0) or 0.0),
            "unrealized_pnl": float(getattr(position, "unrealized_pnl", 0.0) or 0.0),
        })
    payload = {"ok": True, "positions": rows}
except Exception as exc:
    payload = {"ok": False, "error": str(exc), "positions": []}
finally:
    try:
        broker.disconnect()
    except Exception:
        pass
print("__QMT_POSITION_SNAPSHOT__" + json.dumps(payload, ensure_ascii=False))
"""
    try:
        completed = subprocess.run(
            [str(python_exe), "-c", script],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except Exception as exc:
        return {
            "status": "unavailable",
            "source": "qmt",
            "positions": [],
            "generated_at": datetime.now().isoformat(),
            "error": str(exc),
        }
    output = "\n".join([completed.stdout or "", completed.stderr or ""])
    payload_line = ""
    for line in output.splitlines():
        if marker in line:
            payload_line = line.split(marker, 1)[1]
    if not payload_line:
        error = (completed.stderr or completed.stdout or "qmt snapshot subprocess produced no json").strip()
        return {
            "status": "unavailable",
            "source": "qmt",
            "positions": [],
            "generated_at": datetime.now().isoformat(),
            "error": error[-240:],
        }
    try:
        payload = yaml.safe_load(payload_line) or {}
    except Exception as exc:
        return {
            "status": "unavailable",
            "source": "qmt",
            "positions": [],
            "generated_at": datetime.now().isoformat(),
            "error": f"qmt snapshot json parse failed: {exc}",
        }
    positions = [_broker_position_row(position) for position in payload.get("positions", []) or []]
    positions = [position for position in positions if position.get("symbol") and _float(position.get("quantity")) > 0]
    return {
        "status": "ok" if payload.get("ok") else "unavailable",
        "source": "qmt",
        "positions": positions if payload.get("ok") else [],
        "generated_at": datetime.now().isoformat(),
        "error": "" if payload.get("ok") else str(payload.get("error") or "qmt snapshot failed"),
    }


def _broker_position_row(position: Any) -> Dict[str, Any]:
    if isinstance(position, dict):
        getter = position.get
    else:
        getter = lambda key, default=None: getattr(position, key, default)
    symbol = _base_symbol(str(getter("symbol", "")))
    quantity = _float(getter("quantity", getter("qty", 0.0)))
    avg_cost = _float(getter("avg_cost", 0.0))
    market_value = _float(getter("market_value", 0.0))
    return {
        "symbol": symbol,
        "quantity": quantity,
        "avg_cost": avg_cost,
        "market_value": market_value,
        "unrealized_pnl": _float(getter("unrealized_pnl", 0.0)),
    }


def _unassigned_broker_holdings(
    broker_snapshot: Dict[str, Any],
    positions_grouped: Dict[str, Dict[str, Dict[str, Any]]],
    latest_prices: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    assigned = _live_strategy_position_totals(positions_grouped)
    rows: List[Dict[str, Any]] = []
    over_assigned = 0
    for position in broker_snapshot.get("positions", []) or []:
        symbol = _base_symbol(str(position.get("symbol") or ""))
        broker_qty = _float(position.get("quantity"))
        if not symbol or broker_qty <= 0:
            continue
        assigned_row = assigned.get(symbol, {})
        assigned_qty = _float(assigned_row.get("quantity"))
        gap_qty = broker_qty - assigned_qty
        if gap_qty < -1e-9:
            over_assigned += 1
            continue
        if gap_qty <= 1e-9:
            continue
        broker_avg_cost = _float(position.get("avg_cost"))
        broker_market_value = _float(position.get("market_value"))
        latest_price_row = latest_prices.get(symbol, {})
        current_price = broker_market_value / broker_qty if broker_market_value > 0 else _float(latest_price_row.get("price"))
        market_value = gap_qty * current_price if current_price > 0 else 0.0
        rows.append({
            "symbol": symbol,
            "unassigned_qty": gap_qty,
            "broker_qty": broker_qty,
            "assigned_qty": assigned_qty,
            "assigned_avg_cost": _safe_avg_cost(assigned_qty, _float(assigned_row.get("cost"))),
            "broker_avg_cost": broker_avg_cost,
            "current_price": current_price,
            "market_value": market_value,
            "cost_basis": gap_qty * broker_avg_cost,
            "price_source": "broker_market_value" if broker_market_value > 0 else str(latest_price_row.get("source") or ""),
            "price_date": str(latest_price_row.get("date") or ""),
            "assigned_strategies": assigned_row.get("strategies", []),
        })
    rows.sort(key=lambda item: (-_float(item.get("market_value")), str(item.get("symbol") or "")))
    return {
        "status": broker_snapshot.get("status", "unavailable"),
        "source": broker_snapshot.get("source", "qmt"),
        "generated_at": broker_snapshot.get("generated_at", ""),
        "error": broker_snapshot.get("error", ""),
        "items": rows,
        "total_market_value": sum(_float(row.get("market_value")) for row in rows),
        "total_cost_basis": sum(_float(row.get("cost_basis")) for row in rows),
        "unassigned_count": len(rows),
        "broker_position_count": len(broker_snapshot.get("positions", []) or []),
        "assigned_symbol_count": len(assigned),
        "over_assigned_count": over_assigned,
    }


def _manual_default_holdings(
    broker_snapshot: Dict[str, Any],
    positions_grouped: Dict[str, Dict[str, Dict[str, Any]]],
    latest_prices: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    gap = _unassigned_broker_holdings(broker_snapshot, positions_grouped, latest_prices)
    items = []
    price_dates = []
    total_market_value = 0.0
    total_cost = 0.0
    total_unrealized = 0.0
    for row in gap.get("items", []):
        qty = _float(row.get("unassigned_qty"))
        avg_cost = _float(row.get("broker_avg_cost"))
        current_price = _float(row.get("current_price"))
        market_value = _float(row.get("market_value"))
        cost_basis = _float(row.get("cost_basis"))
        unrealized = market_value - cost_basis
        if row.get("price_date"):
            price_dates.append(str(row.get("price_date")))
        total_market_value += market_value
        total_cost += cost_basis
        total_unrealized += unrealized
        items.append({
            "symbol": str(row.get("symbol") or ""),
            "qty": qty,
            "current_price": current_price,
            "price_date": str(row.get("price_date") or ""),
            "price_source": str(row.get("price_source") or "broker_market_value"),
            "valuation_status": "manual_broker_holding",
            "avg_cost": avg_cost,
            "market_value": market_value,
            "unrealized_pnl": unrealized,
            "return_pct": (unrealized / cost_basis) if cost_basis > 0 else 0.0,
            "broker_qty": _float(row.get("broker_qty")),
            "assigned_qty": _float(row.get("assigned_qty")),
        })
    return {
        "items": items,
        "total_market_value": total_market_value,
        "total_cost": total_cost,
        "unrealized_pnl": total_unrealized,
        "realized_pnl": 0.0,
        "total_pnl": total_unrealized,
        "initial_cash": 0.0,
        "cash": 0.0,
        "cash_source": None,
        "nav": total_market_value,
        "price_date": sorted(price_dates)[-1] if price_dates else None,
        "latest_activity_date": "",
        "capital_delta": 0.0,
        "broker_snapshot_status": gap.get("status", "unavailable"),
        "broker_snapshot_error": gap.get("error", ""),
        "broker_snapshot_at": gap.get("generated_at", ""),
        "over_assigned_count": gap.get("over_assigned_count", 0),
    }


def _live_strategy_position_totals(
    positions_grouped: Dict[str, Dict[str, Dict[str, Any]]],
) -> Dict[str, Dict[str, Any]]:
    totals: Dict[str, Dict[str, Any]] = {}
    for strategy_name, modes in positions_grouped.items():
        if str(strategy_name) == "default":
            continue
        for symbol, position in (modes.get("live", {}) or {}).items():
            base_symbol = _base_symbol(symbol)
            quantity = _float(position.get("qty", position.get("quantity", 0.0)))
            if not base_symbol or quantity <= 0:
                continue
            avg_cost = _float(position.get("avg_cost"))
            row = totals.setdefault(base_symbol, {"quantity": 0.0, "cost": 0.0, "strategies": []})
            row["quantity"] = _float(row.get("quantity")) + quantity
            row["cost"] = _float(row.get("cost")) + quantity * avg_cost
            row["strategies"].append({
                "strategy_name": str(strategy_name),
                "quantity": quantity,
                "avg_cost": avg_cost,
            })
    return totals


def _safe_avg_cost(quantity: float, cost: float) -> float:
    return cost / quantity if quantity > 0 else 0.0


def _base_symbol(symbol: Any) -> str:
    text = str(symbol or "").strip()
    if "." in text and text[:6].isdigit():
        return text[:6]
    if "." in text and text[-6:].isdigit():
        return text[-6:]
    return text


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
    all_orders = state_store.get_orders(strategy_name=strategy_name, mode=mode, limit=5000)
    all_fills = state_store.get_fills(strategy_name=strategy_name, mode=mode, limit=5000)
    signals = _signals_with_projection_inputs(root, all_signals)
    orders = [
        order for order in all_orders
        if str(order.get("status", "")).lower() not in _pending_signal_statuses()
    ]

    order_records = [_order_to_order_record(order) for order in orders]
    fill_records = [_fill_to_fill_record(fill) for fill in all_fills]

    display_fills = project_fill_rows(
        mode=mode,
        orders=order_records,
        fills=fill_records,
        commission_config=commission_config,
    )
    order_rows = project_order_rows(
        mode=mode,
        orders=order_records,
        fills=display_fills,
        signals=signals,
        open_prices=_open_prices_for_orders(root, order_records),
        as_of_date=latest_market_data_date if mode == "paper" else None,
        commission_config=commission_config,
    )
    signal_rows = project_signal_rows(signals=signals, orders=order_records, fills=display_fills)
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
        "pending_orders": project_pending_orders(
            signals=signals,
            orders=order_records,
            fills=display_fills,
            as_of_date=date.today().isoformat(),
        ),
        "execution_summary": project_execution_summary(order_rows, display_fills),
        "latest_watermark": {},
    }


def _order_to_order_record(order: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "timestamp": order.get("timestamp", ""),
        "order_id": order.get("order_id", ""),
        "broker_order_id": order.get("broker_order_id", ""),
        "order_row_id": order.get("order_row_id", ""),
        "signal_id": order.get("signal_id", ""),
        "strategy_name": order.get("strategy_name", ""),
        "symbol": order.get("symbol", ""),
        "side": order.get("side", ""),
        "quantity": order.get("quantity", 0.0),
        "order_type": order.get("order_type", ""),
        "price": order.get("limit_price", order.get("price", 0.0)),
        "status": order.get("status", "submitted"),
        "reason": order.get("failure_reason", order.get("reason", "")),
        "record_date": order.get("record_date") or order.get("submit_date", ""),
        "signal_date": order.get("signal_date", ""),
        "submit_date": order.get("submit_date", ""),
        "cost_bps": order.get("cost_bps"),
        "execution_cost_bps": order.get("execution_cost_bps", order.get("cost_bps")),
        "execution_reference_price": order.get("execution_reference_price"),
    }


def _fill_to_fill_record(fill: Dict[str, Any]) -> Dict[str, Any]:
    quantity = float(fill.get("quantity", fill.get("fill_quantity", 0.0)) or 0.0)
    price = float(fill.get("price", fill.get("fill_price", 0.0)) or 0.0)
    return {
        "timestamp": fill.get("timestamp", fill.get("fill_time", "")),
        "fill_id": fill.get("fill_id", ""),
        "order_id": fill.get("order_id", ""),
        "broker_order_id": fill.get("broker_order_id", ""),
        "order_row_id": fill.get("order_row_id", ""),
        "signal_id": fill.get("signal_id", ""),
        "strategy_name": fill.get("strategy_name", ""),
        "symbol": fill.get("symbol", ""),
        "side": fill.get("side", ""),
        "quantity": quantity,
        "price": price,
        "commission": fill.get("commission", 0.0),
        "value": quantity * price,
        "record_date": fill.get("record_date") or fill.get("signal_date", ""),
        "signal_date": fill.get("signal_date", ""),
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
        "record_date": signal.get("record_date") or signal.get("signal_date", ""),
        "signal_date": signal.get("signal_date", ""),
        "submit_date": signal.get("submit_date", ""),
        "cost_bps": signal.get("cost_bps"),
        "execution_cost_bps": signal.get("execution_cost_bps"),
        "execution_reference_price": signal.get("execution_reference_price"),
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
    latest_market_data_date: Optional[str],
) -> Dict[str, Any]:
    anchor = latest_market_data_date or _records_latest_date(records) or date.today().isoformat()
    dates = _recent_run_dates(root, anchor, count=3)
    return project_run_status_bar(
        dates=dates,
        configured=configured,
        control=control,
        records=_records_with_projected_submit_dates(records, root=root),
        latest_market_data_date=latest_market_data_date,
        steps=RUN_STATUS_STEPS,
    )


def _records_with_projected_submit_dates(records: Dict[str, Any], *, root: Path) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in records.items():
        if not isinstance(value, list):
            result[key] = value
            continue
        if key in {"signals", "signal_ledger"}:
            result[key] = _signals_with_projection_inputs(root, value)
            continue
        rows = []
        for row in value:
            if not isinstance(row, dict):
                rows.append(row)
                continue
            rows.append(dict(row))
        result[key] = rows
    return result


def _signals_with_projection_inputs(root: Path, signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    close_prices = _signal_close_prices(root, signals)
    rows: List[Dict[str, Any]] = []
    for signal in signals:
        if not isinstance(signal, dict):
            rows.append(signal)
            continue
        enriched = dict(signal)
        status = str(enriched.get("status") or "").lower()
        enriched["projected_submit_date"] = _signal_submit_date(
            enriched,
            root=root,
            failed=status in _failed_signal_statuses(),
        )
        close_price = close_prices.get(_signal_close_key(enriched))
        if close_price is not None:
            enriched["signal_close_price"] = close_price
        rows.append(enriched)
    return rows


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


def _apply_execution_summary(performance: Dict[str, Any], summary: Dict[str, Any]) -> None:
    performance["total_commission"] = summary.get("total_commission", 0.0)
    performance["slippage_sample_count"] = summary.get("slippage_sample_count", 0)
    performance["median_slippage_bps"] = summary.get("median_slippage_bps")
    performance["weighted_avg_slippage_bps"] = summary.get("weighted_avg_slippage_bps")


def _pending_signal_statuses() -> set[str]:
    return {"accepted", "pending", "queued", "pending_submit"}


def _failed_signal_statuses() -> set[str]:
    return {"rejected", "failed", "error", "dropped", "cancelled", "canceled", "expired"}


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


def _performance(
    root: Path,
    base_dir: Path,
    strategy_name: str,
    holdings: Dict[str, Any],
    records: Optional[Dict[str, Any]] = None,
    latest_market_data_date: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        performance = LiveTradingRecorder(base_dir).get_strategy_performance_from_records(strategy_name, records or {})
    except Exception:
        performance = {}
    return project_performance(
        strategy_name=strategy_name,
        raw_performance=performance,
        holdings=holdings,
        latest_market_data_date=latest_market_data_date,
    )


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
