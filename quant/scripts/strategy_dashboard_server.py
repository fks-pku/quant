#!/usr/bin/env python3
"""Local strategy operations dashboard server."""

import argparse
import json
import math
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml
from flask import Flask, jsonify, request, send_file

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.infrastructure.execution.live_recorder import LiveTradingRecorder
from quant.infrastructure.execution.strategy_controls import (
    DEFAULT_CONTROL_FILE,
    apply_strategy_control_action,
    get_strategy_control,
)
from quant.infrastructure.execution.cn_trading_calendar import (
    expected_market_data_date as resolve_expected_market_data_date,
    latest_data_date as resolve_latest_data_date,
    next_trading_date_after,
    previous_trading_date_before,
)
from quant.infrastructure.execution.strategy_mode_records import (
    StrategyModeRecordStore,
    materialize_daily_records,
)
from quant.infrastructure.execution.strategy_ledger import (
    build_operations_health,
    build_strategy_mode_ledger,
    create_liquidation_plan,
    read_liquidation_plan,
    read_strategy_audit,
)


LIVE_RECORD_DIR = ROOT / "quant" / "infrastructure" / "var" / "live_trading"
PAPER_RECORD_DIR = ROOT / "quant" / "infrastructure" / "var" / "paper_trading"
LIVE_POSITION_FILE = ROOT / "quant" / "features" / "data" / "strategy_positions.json"
PAPER_POSITION_FILE = PAPER_RECORD_DIR / "strategy_positions.json"
LIVE_CONFIG_DIR = ROOT / "quant" / "infrastructure" / "var" / "qmt_live_config"
PAPER_CONFIG_DIR = ROOT / "quant" / "infrastructure" / "var" / "paper_config"
STRATEGY_DIR = ROOT / "quant" / "features" / "strategies"
DASHBOARD_HTML = ROOT / ".codex" / "strategy_dashboard.html"
CONTROL_FILE = DEFAULT_CONTROL_FILE
DISPLAY_NAMES = {
    "ashare_gold_equity_barbell_timing": "A股黄金权益杠铃择时",
    "xueqiu_small_cap_financial_filter": "雪球小市值财务过滤",
    "default": "未归属持仓",
}


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
        liquidation_plan = None
        try:
            control_state = apply_strategy_control_action(
                strategy_name,
                action,
                root / "quant" / "infrastructure" / "var" / "strategy_controls.json",
                note=note,
                default_live_enabled=configured,
                mode=mode,
            )
            if action == "liquidate_stop":
                liquidation_plan = create_liquidation_plan(
                    root=root,
                    strategy_name=strategy_name,
                    mode=mode,
                    positions_data=_read_position_file(_mode_position_file(root, mode)),
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
    live_positions = _read_position_file(root / "quant" / "features" / "data" / "strategy_positions.json")
    paper_positions = _read_position_file(root / "quant" / "infrastructure" / "var" / "paper_trading" / "strategy_positions.json")
    live_records_dir = root / "quant" / "infrastructure" / "var" / "live_trading"
    paper_records_dir = root / "quant" / "infrastructure" / "var" / "paper_trading"
    control_file = root / "quant" / "infrastructure" / "var" / "strategy_controls.json"
    mode_record_store = StrategyModeRecordStore(root / "quant" / "infrastructure" / "var" / "strategy_modes")
    audit_file = root / "quant" / "infrastructure" / "var" / "strategy_audit.jsonl"
    audit_records = read_strategy_audit(audit_file)

    strategy_names = _discover_strategy_names(
        root=root,
        live_config=live_config,
        paper_config=paper_config,
        live_positions=live_positions,
        paper_positions=paper_positions,
        live_records_dir=live_records_dir,
        paper_records_dir=paper_records_dir,
        mode_record_store=mode_record_store,
    )
    benchmark = _benchmark_curve(root)
    all_position_symbols = _position_symbols(live_positions) | _position_symbols(paper_positions)
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
            control_file,
            default_live_enabled=live_configured,
            mode="live",
        )
        paper_control_file = get_strategy_control(
            strategy_name,
            control_file,
            default_live_enabled=paper_configured,
            mode="paper",
        )
        live_records = _read_mode_records(
            root,
            live_records_dir,
            strategy_name,
            mode="live",
            mode_record_store=mode_record_store,
            control=live_control_file.to_dict(),
            configured=live_configured,
            initial_cash=live_initial_cash,
            latest_market_data_date=latest_market_data_date,
        )
        paper_records = _read_mode_records(
            root,
            paper_records_dir,
            strategy_name,
            mode="paper",
            mode_record_store=mode_record_store,
            control=paper_control_file.to_dict(),
            configured=paper_configured,
            initial_cash=paper_initial_cash,
            latest_market_data_date=latest_market_data_date,
        )
        live_control = _control_from_mode_operations(
            live_records.get("operations", []),
            live_control_file.to_dict(),
            configured=live_configured,
            mode="live",
        )
        paper_control = _control_from_mode_operations(
            paper_records.get("operations", []),
            paper_control_file.to_dict(),
            configured=paper_configured,
            mode="paper",
        )
        live_liquidation_plan = read_liquidation_plan(
            root=root,
            strategy_name=strategy_name,
            mode="live",
        )
        paper_liquidation_plan = read_liquidation_plan(
            root=root,
            strategy_name=strategy_name,
            mode="paper",
        )
        live_holdings = _holdings_for_strategy(
            live_positions,
            strategy_name,
            live_records["fills"],
            latest_prices,
            live_records["orders"],
            live_initial_cash,
        )
        paper_holdings = _holdings_for_strategy(
            paper_positions,
            strategy_name,
            paper_records["fills"],
            latest_prices,
            paper_records["orders"],
            paper_initial_cash,
        )
        live_performance = _performance(root, live_records_dir, strategy_name, live_holdings, live_records)
        paper_performance = _performance(root, paper_records_dir, strategy_name, paper_holdings, paper_records)
        _apply_execution_summary(live_performance, live_records["execution_summary"])
        _apply_execution_summary(paper_performance, paper_records["execution_summary"])
        report_path = _report_path(root, strategy_name)
        live_ledger = build_strategy_mode_ledger(
            strategy_name=strategy_name,
            mode="live",
            configured=live_configured,
            initial_cash=live_initial_cash,
            control=live_control,
            records=live_records,
            positions_data=live_positions,
            latest_market_data_date=latest_market_data_date,
            latest_record_date=_records_latest_date(live_records),
            audit_records=audit_records,
            liquidation_plan=live_liquidation_plan,
        )
        paper_ledger = build_strategy_mode_ledger(
            strategy_name=strategy_name,
            mode="paper",
            configured=paper_configured,
            initial_cash=paper_initial_cash,
            control=paper_control,
            records=paper_records,
            positions_data=paper_positions,
            latest_market_data_date=latest_market_data_date,
            latest_record_date=_records_latest_date(paper_records),
            audit_records=audit_records,
            liquidation_plan=paper_liquidation_plan,
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
                "recovery": _recovery_status(live_ledger),
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
                "recovery": _recovery_status(paper_ledger),
                "liquidation_plan": paper_liquidation_plan,
                "performance": paper_performance,
                "holdings": paper_holdings,
                "records": paper_records,
            },
        })

    latest_record_dates = {
        "live": mode_record_store.latest_record_date("live"),
        "paper": mode_record_store.latest_record_date("paper"),
    }
    payload = {
        "generated_at": datetime.now().isoformat(),
        "dashboard_asset_version": _dashboard_asset_version(root),
        "today": date.today().isoformat(),
        "record_dirs": {
            "live": str(live_records_dir),
            "paper": str(paper_records_dir),
            "strategy_modes": str(mode_record_store.base_dir),
        },
        "latest_record_date": latest_record_dates,
        "latest_record_mtime": {
            "live": _latest_record_mtime(mode_record_store.base_dir / "live"),
            "paper": _latest_record_mtime(mode_record_store.base_dir / "paper"),
        },
        "benchmark": benchmark,
        "latest_market_data_date": latest_market_data_date,
        "freshness": _freshness_status(
            root=root,
            latest_market_data_date=latest_market_data_date,
            latest_record_dates=latest_record_dates,
        ),
        "scheduled_jobs": _latest_scheduled_jobs(root),
        "strategies": strategies,
    }
    payload["operations_health"] = build_operations_health(strategies)
    return payload


def _mode_position_file(root: Path, mode: str) -> Path:
    if mode == "paper":
        return root / "quant" / "infrastructure" / "var" / "paper_trading" / "strategy_positions.json"
    return root / "quant" / "features" / "data" / "strategy_positions.json"


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
    latest_record_dates: Dict[str, Optional[str]],
) -> Dict[str, Any]:
    expected = _expected_market_data_date(root=root)
    return {
        "expected_market_data_date": expected,
        "latest_market_data_date": latest_market_data_date,
        "market_data_stale": _date_before(latest_market_data_date, expected),
        "live_record_stale": _date_before(latest_record_dates.get("live"), expected),
        "paper_record_stale": _date_before(latest_record_dates.get("paper"), expected),
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
            cash = _float(config.get(key))
            if cash > 0:
                return cash
    return default_cash


def _read_position_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"positions": {}, "realized_pnl": {}, "order_map": {}}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f) or {}
    data.setdefault("positions", {})
    data.setdefault("realized_pnl", {})
    data.setdefault("order_map", {})
    return data


def _discover_strategy_names(
    *,
    root: Path,
    live_config: Dict[str, Dict[str, Any]],
    paper_config: Dict[str, Dict[str, Any]],
    live_positions: Dict[str, Any],
    paper_positions: Dict[str, Any],
    live_records_dir: Path,
    paper_records_dir: Path,
    mode_record_store: StrategyModeRecordStore,
) -> List[str]:
    names = set(live_config) | set(paper_config)
    names.update(_strategy_dirs(root))
    names.update(live_positions.get("positions", {}).keys())
    names.update(paper_positions.get("positions", {}).keys())
    names.update(_record_strategy_names(live_records_dir))
    names.update(_record_strategy_names(paper_records_dir))
    names.update(mode_record_store.strategy_names("live"))
    names.update(mode_record_store.strategy_names("paper"))
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


def _record_strategy_names(base_dir: Path) -> List[str]:
    names = set()
    for kind in ("signals", "orders", "fills", "snapshots"):
        for record in _read_jsonl_records(base_dir, kind):
            name = record.get("strategy_name")
            if name:
                names.add(str(name))
    return sorted(names)


def _read_mode_records(
    root: Path,
    base_dir: Path,
    strategy_name: str,
    *,
    mode: str,
    mode_record_store: StrategyModeRecordStore,
    control: Dict[str, Any],
    configured: bool,
    initial_cash: float,
    latest_market_data_date: Optional[str],
) -> Dict[str, Any]:
    legacy_records = {
        "signals": _filter_strategy(_read_jsonl_records(base_dir, "signals"), strategy_name),
        "orders": _filter_strategy(_read_jsonl_records(base_dir, "orders"), strategy_name),
        "fills": _filter_strategy(_read_jsonl_records(base_dir, "fills"), strategy_name),
        "snapshots": _filter_strategy(_read_jsonl_records(base_dir, "snapshots"), strategy_name),
    }
    materialize_daily_records(
        mode_record_store,
        mode=mode,
        strategy_name=strategy_name,
        records=legacy_records,
    )
    _materialize_control_operation(
        mode_record_store,
        strategy_name=strategy_name,
        mode=mode,
        control=control,
        configured=configured,
    )
    stored_records = mode_record_store.read_records(mode=mode, strategy_name=strategy_name)
    stored_orders = stored_records["orders"]
    stored_fills = stored_records["fills"]
    stored_order_rows = _dashboard_order_rows(mode, stored_orders, stored_fills, _open_prices_for_orders(root, stored_orders))
    _materialize_canonical_snapshots(
        mode_record_store,
        mode=mode,
        strategy_name=strategy_name,
        curve=_curve_from_order_rows(
            root=root,
            strategy_name=strategy_name,
            order_rows=stored_order_rows,
            initial_cash=initial_cash,
        ),
        initial_cash=initial_cash,
    )
    _materialize_cash_only_snapshot(
        mode_record_store,
        mode=mode,
        strategy_name=strategy_name,
        configured=configured,
        control=control,
        initial_cash=initial_cash,
        latest_market_data_date=latest_market_data_date,
        existing_snapshots=stored_records["snapshots"],
        has_filled_activity=bool(stored_fills) or any(_float(row.get("filled_qty")) > 0 for row in stored_order_rows),
    )
    stored_records = mode_record_store.read_records(mode=mode, strategy_name=strategy_name)
    signals = stored_records["signals"]
    orders = stored_records["orders"]
    fills = stored_records["fills"]
    snapshots = stored_records["snapshots"]
    order_rows = _dashboard_order_rows(mode, orders, fills, _open_prices_for_orders(root, orders))
    return {
        "operations": sorted(stored_records["operations"], key=lambda item: item.get("timestamp", "")),
        "signals": sorted(signals, key=lambda item: item.get("timestamp", "")),
        "orders": order_rows,
        "fills": sorted(fills, key=lambda item: item.get("timestamp", "")),
        "snapshots": sorted(snapshots, key=lambda item: item.get("timestamp", "")),
        "pending_orders": _pending_submit_orders(root, signals, orders, fills, as_of_date=date.today().isoformat()),
        "execution_summary": _dashboard_execution_summary(order_rows, fills),
    }


def _read_jsonl_records(base_dir: Path, kind: str, max_days: int = 365) -> List[Dict[str, Any]]:
    if not base_dir.exists():
        return []
    records: List[Dict[str, Any]] = []
    day_dirs = [path for path in base_dir.iterdir() if path.is_dir()]
    for day_dir in sorted(day_dirs)[-max_days:]:
        path = day_dir / f"{kind}.jsonl"
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                item = json.loads(line)
                item.setdefault("record_date", day_dir.name)
                records.append(item)
    return records


def _filter_strategy(records: Iterable[Dict[str, Any]], strategy_name: str) -> List[Dict[str, Any]]:
    return [record for record in records if record.get("strategy_name") == strategy_name]


def _materialize_control_operation(
    mode_record_store: StrategyModeRecordStore,
    *,
    strategy_name: str,
    mode: str,
    control: Dict[str, Any],
    configured: bool,
) -> None:
    timestamp = str(control.get("updated_at") or "")
    if not timestamp:
        return
    mode_record_store.append_operation(
        mode=mode,
        strategy_name=strategy_name,
        action="control_state",
        timestamp=timestamp,
        source="strategy_controls",
        payload={"control": dict(control), "configured": configured},
        unique=True,
    )


def _materialize_canonical_snapshots(
    mode_record_store: StrategyModeRecordStore,
    *,
    mode: str,
    strategy_name: str,
    curve: List[Dict[str, Any]],
    initial_cash: float,
) -> None:
    if initial_cash <= 0:
        return
    for point in curve:
        point_date = str(point.get("date") or point.get("record_date") or "")[:10]
        if not point_date:
            continue
        snapshot = dict(point)
        snapshot["timestamp"] = f"{point_date}T23:59:59"
        snapshot["record_date"] = point_date
        snapshot["date"] = point_date
        snapshot["source"] = "canonical_fill_ledger"
        snapshot["initial_cash"] = initial_cash
        mode_record_store.append(
            "snapshots",
            mode=mode,
            strategy_name=strategy_name,
            record=snapshot,
            unique=True,
        )
        mode_record_store.append_operation(
            mode=mode,
            strategy_name=strategy_name,
            action="canonical_snapshot",
            timestamp=snapshot["timestamp"],
            source="strategy_mode_ledger",
            payload={
                "date": point_date,
                "initial_cash": initial_cash,
                "nav": snapshot.get("nav"),
                "total_pnl": snapshot.get("total_pnl"),
            },
            unique=True,
        )


def _materialize_cash_only_snapshot(
    mode_record_store: StrategyModeRecordStore,
    *,
    mode: str,
    strategy_name: str,
    configured: bool,
    control: Dict[str, Any],
    initial_cash: float,
    latest_market_data_date: Optional[str],
    existing_snapshots: List[Dict[str, Any]],
    has_filled_activity: bool,
) -> None:
    if not configured or initial_cash <= 0 or not latest_market_data_date:
        return
    if has_filled_activity:
        return
    state = str(control.get("live_state") or "").lower()
    if state not in {"running", "paused"}:
        return
    snapshot_date = str(latest_market_data_date)[:10]
    if any(
        str(row.get("date") or row.get("record_date") or row.get("timestamp") or "")[:10] == snapshot_date
        for row in existing_snapshots
    ):
        return
    timestamp = f"{snapshot_date}T15:00:00"
    snapshot = {
        "timestamp": timestamp,
        "record_date": snapshot_date,
        "date": snapshot_date,
        "source": "cash_only_no_activity",
        "initial_cash": initial_cash,
        "nav": initial_cash,
        "market_value": 0.0,
        "cash": initial_cash,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "total_pnl": 0.0,
    }
    mode_record_store.append(
        "snapshots",
        mode=mode,
        strategy_name=strategy_name,
        record=snapshot,
        unique=True,
    )
    mode_record_store.append_operation(
        mode=mode,
        strategy_name=strategy_name,
        action="cash_only_snapshot",
        timestamp=timestamp,
        source="strategy_mode_ledger",
        payload={
            "date": snapshot_date,
            "initial_cash": initial_cash,
            "nav": initial_cash,
            "reason": "configured mode has no activity but still needs a NAV point",
        },
        unique=True,
    )


def _control_from_mode_operations(
    operations: Iterable[Dict[str, Any]],
    fallback: Dict[str, Any],
    *,
    configured: bool,
    mode: str,
) -> Dict[str, Any]:
    control = dict(fallback)
    control.setdefault("mode", mode)
    control.setdefault("live_enabled", bool(configured))
    control.setdefault("live_state", "running" if control.get("live_enabled") else "stopped")
    for operation in sorted(operations, key=lambda item: str(item.get("timestamp") or "")):
        payload = operation.get("payload", {})
        if isinstance(payload, dict) and isinstance(payload.get("control"), dict):
            control.update(payload["control"])
            control["mode"] = mode
            continue
        action = str(operation.get("action") or "")
        if action in {"start", "resume"}:
            control.update({"live_enabled": True, "live_state": "running", "liquidation_requested": False})
        elif action == "pause":
            control.update({"live_enabled": bool(control.get("live_enabled") or configured), "live_state": "paused", "liquidation_requested": False})
        elif action == "liquidate_stop":
            control.update({"live_enabled": False, "live_state": "liquidating", "liquidation_requested": True})
        elif action == "stop":
            control.update({"live_enabled": False, "live_state": "stopped", "liquidation_requested": False})
        if action in {"start", "resume", "pause", "liquidate_stop", "stop"}:
            control["updated_at"] = operation.get("timestamp", control.get("updated_at", ""))
    control["mode"] = mode
    return control


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
            value = record.get("record_date") or record.get("date") or record.get("timestamp")
            text = value.date().isoformat() if isinstance(value, datetime) else str(value or "")[:10]
            if len(text) == 10 and (latest is None or text > latest):
                latest = text
    return latest


def _dashboard_order_rows(
    mode: str,
    orders: List[Dict[str, Any]],
    fills: List[Dict[str, Any]],
    open_prices: Optional[Dict[tuple[str, str], float]] = None,
) -> List[Dict[str, Any]]:
    open_prices = open_prices or {}
    fill_totals: Dict[str, Dict[str, float]] = {}
    for fill in fills:
        order_id = str(fill.get("order_id", ""))
        if not order_id:
            continue
        qty = _float(fill.get("quantity"))
        price = _float(fill.get("price"))
        commission = _float(fill.get("commission"))
        totals = fill_totals.setdefault(order_id, {"quantity": 0.0, "value": 0.0, "commission": 0.0})
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
        open_price = open_prices.get(_order_open_key(order))
        row["limit_price"] = limit_price
        row["open_price"] = open_price
        row["filled_qty"] = filled_qty
        row["raw_fill_price"] = raw_fill_price
        row["fill_price"] = display_fill_price
        row["commission"] = totals["commission"]
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
    fill_totals: Dict[str, Dict[str, float]],
    order: Dict[str, Any],
) -> Dict[str, float]:
    keys = []
    for key in ("order_id", "broker_order_id"):
        value = str(order.get(key) or "")
        if value and value not in keys:
            keys.append(value)
    for key in keys:
        if key in fill_totals:
            return fill_totals[key]
    return {"quantity": 0.0, "value": 0.0, "commission": 0.0}


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
        "total_commission": round(sum(_float(fill.get("commission")) for fill in fills), 6),
        "median_slippage_bps": round(_median(samples), 6) if samples else None,
        "weighted_avg_slippage_bps": round(weighted_sum / weight_total, 6) if weight_total > 0 else None,
        "slippage_sample_count": len(samples),
    }


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
        (_order_signature(record), _timestamp_text(record))
        for record in orders
    ]
    close_prices = _signal_close_prices(root, signals)
    pending = []
    for signal in sorted(signals, key=lambda item: item.get("timestamp", "")):
        if str(signal.get("status", "")).lower() not in {"accepted", "pending", "queued", "pending_submit"}:
            continue
        if _signal_is_submitted(signal, submitted_ids, submitted_signatures):
            continue
        signal_date = _record_date(signal)
        row = dict(signal)
        row["signal_date"] = signal_date
        row["submit_date"] = str(
            row.get("submit_date")
            or row.get("execution_date")
            or _next_trading_date(signal_date, root=root)
            or ""
        )
        if _date_before(row["submit_date"], as_of_date):
            continue
        cost_bps = _pending_submit_cost_bps(
            row,
            close_prices.get(_signal_close_key(row)),
        )
        if cost_bps is not None:
            row["cost_bps"] = cost_bps
            row["cost_bps_display"] = f"+{cost_bps:.1f} bps"
        row["display_status"] = "pending_submit"
        pending.append(row)
    return pending


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
    for record in [*orders, *fills]:
        for key in ("order_id", "broker_order_id"):
            value = str(record.get(key) or "")
            if value:
                ids.add(value)
    return ids


def _signal_is_submitted(
    signal: Dict[str, Any],
    submitted_ids: set[str],
    submitted_signatures: List[tuple[Optional[tuple[Any, ...]], str]],
) -> bool:
    order_id = str(signal.get("order_id") or "")
    if order_id and order_id in submitted_ids:
        return True
    signature = _order_signature(signal)
    if signature is None:
        return False
    signal_time = _timestamp_text(signal)
    for submitted_signature, submitted_time in submitted_signatures:
        if submitted_signature != signature:
            continue
        if not signal_time or not submitted_time or submitted_time >= signal_time:
            return True
        if _timestamps_near(submitted_time, signal_time):
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


def _position_symbols(position_data: Dict[str, Any]) -> set[str]:
    symbols: set[str] = set()
    for positions in (position_data.get("positions", {}) or {}).values():
        if not isinstance(positions, dict):
            continue
        symbols.update(str(symbol) for symbol in positions.keys() if symbol)
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
    position_data: Dict[str, Any],
    strategy_name: str,
    fills: List[Dict[str, Any]],
    latest_prices: Optional[Dict[str, Dict[str, Any]]] = None,
    order_rows: Optional[List[Dict[str, Any]]] = None,
    initial_cash: float = 0.0,
) -> Dict[str, Any]:
    stored_positions = position_data.get("positions", {}).get(strategy_name, {}) or {}
    contract_state = _contract_position_state(order_rows or [])
    contract_positions = contract_state["positions"]
    has_contract_activity = bool(contract_state["has_activity"])
    realized = (
        contract_state["realized_pnl"]
        if has_contract_activity
        else _float(position_data.get("realized_pnl", {}).get(strategy_name, 0.0))
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
        if close_price:
            current_price = _float(close_price.get("price"))
            price_date = str(close_price.get("date", ""))
            price_source = str(close_price.get("source", "duckdb"))
        elif qty > 0 and stored_market_value > 0:
            current_price = stored_market_value / qty
            price_date = ""
            price_source = "position_market_value"
        else:
            current_price = last_contract_prices.get(symbol, last_fill_prices.get(symbol, avg_cost))
            price_date = ""
            price_source = "display_contract_fill" if symbol in last_contract_prices else "last_fill"
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
            "avg_cost": avg_cost,
            "market_value": market_value,
            "unrealized_pnl": unrealized,
            "return_pct": (unrealized / cost_value) if cost_value > 0 else 0.0,
        })
    cash_source = None
    cash = 0.0
    nav = total_market_value
    if has_contract_activity and initial_cash > 0:
        cash = initial_cash + _float(contract_state["cash_delta"])
        cash_source = "order_contract"
        nav = cash + total_market_value
    elif not holdings and initial_cash > 0:
        cash = initial_cash
        cash_source = "initial_cash"
        nav = initial_cash
        if not price_dates:
            price_date = _latest_price_date(latest_prices)
            if price_date:
                price_dates.append(price_date)
    total_pnl = nav - initial_cash if cash_source == "order_contract" else realized + total_unrealized
    if cash_source == "initial_cash":
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
    }


def _contract_position_state(order_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    lots: Dict[str, List[Dict[str, float]]] = {}
    realized_pnl = 0.0
    cash_delta = 0.0
    has_activity = False
    for row in sorted(order_rows, key=lambda item: item.get("timestamp", "")):
        symbol = str(row.get("symbol") or "")
        side = str(row.get("side") or "").upper()
        qty = _float(row.get("filled_qty"))
        fill_price = _float(row.get("fill_price"))
        commission = _float(row.get("commission"))
        if not symbol or qty <= 0 or fill_price <= 0:
            continue
        has_activity = True
        notional = qty * fill_price
        if side == "BUY":
            unit_cost = (notional + commission) / qty
            lots.setdefault(symbol, []).append({"qty": qty, "unit_cost": unit_cost})
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
        if qty <= 1e-9:
            continue
        positions[symbol] = {
            "qty": qty,
            "avg_cost": cost_value / qty,
            "cost_value": cost_value,
        }
    return {
        "positions": positions,
        "realized_pnl": realized_pnl,
        "cash_delta": cash_delta,
        "has_activity": has_activity,
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
    denominator = initial_cash if holdings.get("cash_source") in {"order_contract", "initial_cash"} and initial_cash > 0 else total_cost
    performance["total_return"] = (
        holdings.get("total_pnl", 0.0) / denominator
        if denominator and denominator > 0
        else performance.get("total_return", 0.0)
    )
    if latest_snapshot is not None:
        performance["latest_snapshot"] = latest_snapshot
        if holdings.get("cash_source") in {"order_contract", "initial_cash"}:
            performance["total_nav"] = holdings.get("nav", 0.0)
            performance["cash"] = holdings.get("cash", 0.0)
        elif _float(performance.get("total_nav")) <= 0:
            performance["total_nav"] = latest_snapshot.get("nav", 0.0)
    performance.setdefault("total_nav", 0.0)
    performance["pnl_curve"] = curve
    return performance


def _curve_from_order_rows(
    *,
    root: Path,
    strategy_name: str,
    order_rows: List[Dict[str, Any]],
    initial_cash: float,
) -> List[Dict[str, Any]]:
    filled_rows = [
        row for row in order_rows
        if _float(row.get("filled_qty")) > 0 and _float(row.get("fill_price")) > 0
    ]
    if initial_cash <= 0 or not filled_rows:
        return []
    symbols = sorted({str(row.get("symbol") or "").split(".")[0] for row in filled_rows if row.get("symbol")})
    dates = [_record_date(row) for row in filled_rows if _record_date(row)]
    if not symbols or not dates:
        return []
    start = min(dates)
    end = date.today().isoformat()
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
    cash = _float(holdings.get("cash")) if holdings.get("cash_source") in {"order_contract", "initial_cash"} else 0.0
    nav = _float(holdings.get("nav")) if holdings.get("cash_source") in {"order_contract", "initial_cash"} else market_value
    snapshot_date = str(holdings.get("price_date") or date.today().isoformat())
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


def _latest_record_date(base_dir: Path) -> Optional[str]:
    if not base_dir.exists():
        return None
    dates = [item.name for item in base_dir.iterdir() if item.is_dir()]
    return sorted(dates)[-1] if dates else None


def _latest_record_mtime(base_dir: Path) -> Optional[str]:
    if not base_dir.exists():
        return None
    latest = None
    for path in base_dir.rglob("*.jsonl"):
        mtime = path.stat().st_mtime
        if latest is None or mtime > latest:
            latest = mtime
    return datetime.fromtimestamp(latest).isoformat() if latest is not None else None


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
