from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant.api.research_bp import (
    _DuckDBDailyDateProvider,
    _archived_candidate_info,
    _candidate_backtest_risk_config,
    _candidate_formula_key,
    _candidate_requires_market_cap,
    _candidate_symbols,
    _cn_survivorship_audit,
    _load_archived_strategy_class,
    _load_cn_benchmark_provider,
    _load_lot_sizes,
    _make_walkforward_runner,
    _strict_backtest_report,
    _strict_execution_cost_model,
    _strategy_init_kwargs,
)
from quant.domain.models.market import is_cn_symbol
from quant.features.backtest.benchmark import BenchmarkProvider
from quant.features.backtest.engine import Backtester
from quant.features.research.rigor.backtest_hub import RigorHub
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.trading.sub_portfolio import SubPortfolio
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
from quant.infrastructure.research.reporting import build_research_stage_report_html


SID = "joinquant_small_cap_low_price"
START = datetime(2012, 1, 1)
END = datetime(2025, 12, 31)
INITIAL_CASH = 500000
REPORT_DIR = Path("quant/infrastructure/var/research/reports/joinquant_small_cap_low_price")


SCENARIOS = [
    {"name": "baseline_current", "parameters": {}},
    {"name": "gross_80", "parameters": {"max_position_pct": 0.8}},
    {"name": "gross_60", "parameters": {"max_position_pct": 0.6}},
    {"name": "gross_50", "parameters": {"max_position_pct": 0.5}},
    {"name": "gross_40", "parameters": {"max_position_pct": 0.4}},
    {"name": "diversified_30_gross80", "parameters": {"max_position_pct": 0.8, "max_positions": 30}},
    {"name": "diversified_40_gross80", "parameters": {"max_position_pct": 0.8, "max_positions": 40}},
    {"name": "diversified_40_gross60", "parameters": {"max_position_pct": 0.6, "max_positions": 40}},
    {"name": "liquidity_100k_gross80", "parameters": {"max_position_pct": 0.8, "min_avg_turnover": 100000.0}},
    {
        "name": "price3_liquidity_100k_gross80",
        "parameters": {"max_position_pct": 0.8, "min_trade_price": 3.0, "min_avg_turnover": 100000.0},
    },
    {
        "name": "price3_liquidity_100k_gross60",
        "parameters": {"max_position_pct": 0.6, "min_trade_price": 3.0, "min_avg_turnover": 100000.0},
    },
    {
        "name": "price5_liquidity_500k_gross60",
        "parameters": {"max_position_pct": 0.6, "min_trade_price": 5.0, "min_avg_turnover": 500000.0},
    },
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--walkforward-best", action="store_true")
    parser.add_argument("--walkforward-existing", action="store_true")
    parser.add_argument("--max-scenarios", type=int, default=0)
    args = parser.parse_args()

    report_dir = REPORT_DIR
    report_dir.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base_info = _archived_candidate_info(SID)
    if not base_info:
        raise RuntimeError(f"Archived candidate info not found: {SID}")
    strategy_class = _load_archived_strategy_class(SID, (base_info.get("research_meta") or {}).get("rejected_strategy_dir"))
    if strategy_class is None:
        raise RuntimeError(f"Archived strategy class not found: {SID}")

    symbols = _candidate_symbols(base_info, [])
    if args.walkforward_existing:
        payload = json.loads((report_dir / "risk_control_grid.json").read_text(encoding="utf-8"))
        best = dict(payload["best"])
        best_info = _scenario_info(base_info, best.get("parameters") or {})
        best_report = json.loads((report_dir / "last_result_risk_control_best.json").read_text(encoding="utf-8"))
        walkforward = _run_best_walkforward(best_info, symbols)
        _write_outputs(report_dir, run_ts, list(payload["results"]), best, best_report, best_info, walkforward)
        print(f"Updated walk-forward for existing best: {best['scenario']}", flush=True)
        print(f"HTML: {report_dir / 'risk_control_grid.html'}", flush=True)
        return 0

    shared = _load_shared_context(symbols)
    scenarios = SCENARIOS[: args.max_scenarios] if args.max_scenarios else SCENARIOS
    rows = []
    best_report = None
    best_info = None
    for idx, scenario in enumerate(scenarios, start=1):
        info = _scenario_info(base_info, scenario["parameters"])
        print(f"[{idx}/{len(scenarios)}] {scenario['name']} {scenario['parameters']}", flush=True)
        strict_report = _run_strict(strategy_class, info, symbols, shared)
        row = _summary_row(scenario["name"], scenario["parameters"], strict_report)
        rows.append(row)
        if _is_better(row, _summary_row("", {}, best_report) if best_report else None):
            best_report = strict_report
            best_info = info
        print(
            "  "
            + f"Sharpe={row['sharpe']:.4f} CAGR={row['cagr']:.2%} "
            + f"MaxDD={row['max_drawdown_pct']:.2%} Calmar={row['calmar_ratio']:.4f} "
            + f"Trades={row['total_trades']}",
            flush=True,
        )

    if best_report is None or best_info is None:
        raise RuntimeError("No scenario result produced")
    best = max(rows, key=_objective_key)
    walkforward = _run_best_walkforward(best_info, symbols) if args.walkforward_best else None
    _write_outputs(report_dir, run_ts, rows, best, best_report, best_info, walkforward)
    print(f"Best risk-control scenario: {best['scenario']}", flush=True)
    print(f"HTML: {report_dir / 'risk_control_grid.html'}", flush=True)
    print(f"Best strict report: {report_dir / 'strict_backtest_report_risk_control_best.html'}", flush=True)
    return 0


def _load_shared_context(symbols: List[str]) -> Dict[str, Any]:
    db_provider = DuckDBProvider()
    db_provider.connect()
    try:
        lot_sizes = _load_lot_sizes(db_provider, symbols, is_cn_symbol)
        benchmark_provider, benchmark_meta = _load_cn_benchmark_provider(db_provider, START, END, BenchmarkProvider)
        survivorship_audit = _cn_survivorship_audit(db_provider, START, END, formula_key="joinquant_small_cap_low_price_factor")
    finally:
        db_provider.disconnect()
    return {
        "lot_sizes": lot_sizes,
        "benchmark_provider": benchmark_provider,
        "benchmark_meta": benchmark_meta,
        "survivorship_audit": survivorship_audit,
    }


def _scenario_info(base_info: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    info = copy.deepcopy(base_info)
    params = dict(info.get("parameters") or {})
    params.update(parameters)
    info["parameters"] = params
    meta = dict(info.get("research_meta") or {})
    spec = dict(meta.get("strategy_spec") or {})
    if params.get("holding_days") is not None:
        spec["horizon_days"] = int(params.get("holding_days") or spec.get("horizon_days") or 5)
    meta["strategy_spec"] = spec
    info["research_meta"] = meta
    return info


def _run_strict(strategy_class: Any, info: Dict[str, Any], symbols: List[str], shared: Dict[str, Any]) -> Dict[str, Any]:
    execution_cost_model = _strict_execution_cost_model(SID, info, True)
    data_provider = _DuckDBDailyDateProvider(
        symbols,
        START,
        END,
        include_daily_basic=_candidate_requires_market_cap(info),
        include_execution_liquidity_features=bool(execution_cost_model and execution_cost_model.get("enabled")),
        cache_enabled=True,
    )
    strategy = strategy_class(**_strategy_init_kwargs(strategy_class, info, symbols))
    commission_cfg = {
        "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
        "HK": {"type": "hk_realistic"},
        "CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0},
    }
    backtest_config = {"slippage_bps": 5}
    if execution_cost_model:
        backtest_config["execution_cost_model"] = execution_cost_model
    bt_config = {
        "backtest": backtest_config,
        "execution": {"commission": commission_cfg},
        "data": {"default_timeframe": "1d"},
        "risk": _candidate_backtest_risk_config(info),
    }
    backtester = Backtester(
        bt_config,
        portfolio_class=Portfolio,
        risk_engine_class=RiskEngine,
        sub_portfolio_class=SubPortfolio,
        lot_sizes=shared["lot_sizes"],
        benchmark_provider=shared["benchmark_provider"],
    )
    try:
        result = backtester.run(
            start=START,
            end=END,
            strategies=[strategy],
            initial_cash=INITIAL_CASH,
            data_provider=data_provider,
            symbols=symbols,
        )
    finally:
        data_provider.close()
    benchmark_equity = None
    if shared["benchmark_provider"] is not None:
        benchmark_equity = shared["benchmark_provider"].get_benchmark_equity(START, END, INITIAL_CASH)
    return _strict_backtest_report(
        result,
        START,
        END,
        INITIAL_CASH,
        symbols,
        shared["benchmark_meta"],
        shared["lot_sizes"],
        strategy,
        benchmark_equity,
        shared["survivorship_audit"],
        {**backtest_config, "commission": commission_cfg},
    )


def _summary_row(scenario: str, parameters: Dict[str, Any], strict_report: Dict[str, Any] | None) -> Dict[str, Any]:
    metrics = (strict_report or {}).get("metrics") or {}
    capacity = (strict_report or {}).get("capacity") or {}
    turnover = (strict_report or {}).get("turnover") or {}
    diagnostics = (strict_report or {}).get("diagnostics") or {}
    return {
        "scenario": scenario,
        "parameters": dict(parameters or {}),
        "sharpe": _float(metrics.get("sharpe")),
        "sortino": _float(metrics.get("sortino")),
        "cagr": _float(metrics.get("cagr")),
        "total_return": _float(metrics.get("total_return")),
        "max_drawdown_pct": _float(metrics.get("max_drawdown_pct")),
        "calmar_ratio": _float(metrics.get("calmar_ratio")),
        "win_rate": _float(metrics.get("win_rate")),
        "profit_factor": _float(metrics.get("profit_factor")),
        "total_trades": int(metrics.get("total_trades") or 0),
        "cost_drag_pct": _float(diagnostics.get("cost_drag_pct")),
        "annual_gross_turnover": _float(turnover.get("annual_gross_turnover")),
        "p95_adv_participation": _float(capacity.get("p95_adv_participation")),
        "max_adv_participation": _float(capacity.get("max_adv_participation")),
        "estimated_capacity_at_1pct_adv_p95": _float(capacity.get("estimated_capacity_at_1pct_adv_p95")),
        "volume_limited_trades": int(diagnostics.get("volume_limited_trades") or 0),
        "limit_rejected_orders": int(diagnostics.get("limit_rejected_orders") or 0),
        "submission_rejected": int(diagnostics.get("submission_rejected") or 0),
    }


def _objective_key(row: Dict[str, Any]) -> tuple:
    cagr = row.get("cagr", 0.0)
    max_dd = row.get("max_drawdown_pct", -1.0)
    calmar = row.get("calmar_ratio", 0.0)
    sharpe = row.get("sharpe", 0.0)
    return (
        1 if cagr >= 0.08 else 0,
        1 if max_dd >= -0.30 else 0,
        calmar,
        max_dd,
        sharpe,
    )


def _is_better(row: Dict[str, Any], other: Dict[str, Any] | None) -> bool:
    return other is None or _objective_key(row) > _objective_key(other)


def _run_best_walkforward(info: Dict[str, Any], symbols: List[str]) -> Dict[str, Any]:
    print("[walkforward] best scenario", flush=True)
    with tempfile.TemporaryDirectory(prefix="small_cap_risk_control_") as temp_dir:
        temp_path = Path(temp_dir)
        source_dir = Path((info.get("research_meta") or {}).get("rejected_strategy_dir") or "")
        if not source_dir.exists():
            source_dir = Path("quant/features/rejected_strategy/joinquant_small_cap_low_price")
        archive_dir = temp_path / SID
        archive_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_dir / "strategy.py", archive_dir / "strategy.py")
        config = {}
        config_path = source_dir / "config.yaml"
        if config_path.exists():
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        config["parameters"] = dict(info.get("parameters") or {})
        (archive_dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
        hub = RigorHub(
            backtest_runner=_make_walkforward_runner(),
            config={"purged_walkforward": {"parallel_workers": 4}},
        )
        result = hub.run_walkforward(
            strategy_id=SID,
            symbols=symbols,
            start=START.strftime("%Y-%m-%d"),
            end=END.strftime("%Y-%m-%d"),
            initial_cash=INITIAL_CASH,
            strategy_archive_dir=str(archive_dir),
        )
    return {
        "is_viable": bool(result.is_viable),
        "capacity_ok": bool(getattr(result, "capacity_ok", False)),
        "aggregate_oos_sharpe": _float(result.aggregate_oos_sharpe),
        "worst_oos_sharpe": _float(result.worst_oos_sharpe),
        "pct_profitable_splits": _float(result.pct_profitable_splits),
        "deflated_sharpe_ratio": None if result.deflated_sharpe_ratio is None else _float(result.deflated_sharpe_ratio),
        "n_splits": len(result.splits or []),
    }


def _write_outputs(
    report_dir: Path,
    run_ts: str,
    rows: List[Dict[str, Any]],
    best: Dict[str, Any],
    best_report: Dict[str, Any],
    best_info: Dict[str, Any],
    walkforward: Dict[str, Any] | None,
) -> None:
    sorted_rows = sorted(rows, key=_objective_key, reverse=True)
    payload = {
        "strategy_id": SID,
        "run_ts": run_ts,
        "period": f"{START.date()}-{END.date()}",
        "initial_cash": INITIAL_CASH,
        "objective": "Prefer MaxDD control with at least 8% CAGR, then Calmar, then Sharpe.",
        "best": best,
        "best_walkforward": walkforward,
        "results": sorted_rows,
    }
    json_path = report_dir / "risk_control_grid.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (report_dir / "runs" / f"{run_ts}_risk_control_grid.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    best_result_path = report_dir / "last_result_risk_control_best.json"
    best_result_path.write_text(json.dumps(best_report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (report_dir / "runs" / f"{run_ts}_risk_control_best_result.json").write_text(
        json.dumps(best_report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    grid_html = _grid_html(payload)
    (report_dir / "risk_control_grid.html").write_text(grid_html, encoding="utf-8")
    (report_dir / "runs" / f"{run_ts}_risk_control_grid.html").write_text(grid_html, encoding="utf-8")
    best_html = build_research_stage_report_html(
        "strict_backtest",
        {"run_id": f"{SID}_risk_control_best", "backtested": 1, "rejected": 0, "errors": []},
        [_best_hypothesis(best, best_info, best_report, walkforward)],
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    (report_dir / "strict_backtest_report_risk_control_best.html").write_text(best_html, encoding="utf-8")
    (report_dir / "runs" / f"{run_ts}_risk_control_best_strict_backtest_report.html").write_text(best_html, encoding="utf-8")


def _best_hypothesis(
    best: Dict[str, Any],
    info: Dict[str, Any],
    strict_report: Dict[str, Any],
    walkforward: Dict[str, Any] | None,
) -> Dict[str, Any]:
    stage_conclusions = {
        "strict_backtest": {
            "label": "严格回测（风险控制最佳参数）",
            "verdict": "pass" if best.get("sharpe", 0.0) >= 0.5 else "warn",
            "conclusion": (
                f"风险控制 strict 最佳：{best['scenario']}，Sharpe={best['sharpe']:.2f}，"
                f"CAGR={best['cagr']:.2%}，MaxDD={best['max_drawdown_pct']:.2%}。"
            ),
            "method": "项目 Backtester + small_cap_realistic 成本冲击模型；独立风险控制参数网格。",
        }
    }
    if walkforward:
        stage_conclusions["walkforward_strict_audit"] = {
            "label": "Walk-forward strict audit（最佳参数复核）",
            "verdict": "pass" if walkforward.get("is_viable") else "fail",
            "conclusion": (
                f"最佳参数 walk-forward：aggregate={walkforward['aggregate_oos_sharpe']:.2f}，"
                f"worst={walkforward['worst_oos_sharpe']:.2f}，盈利 split={walkforward['pct_profitable_splits']:.0%}。"
            ),
            "method": "滚动 OOS strict replay；仅对 strict 网格最优行复核。",
        }
    return {
        "title": "JoinQuant Small Cap Low Price Risk Control",
        "strategy_id": SID,
        "status": "needs_more_validation",
        "decision_reason": "risk-control parameter grid",
        "metrics": {
            "strict_backtest": strict_report,
            "walkforward": walkforward or {},
            "research_stage_conclusions": stage_conclusions,
        },
        "evidence": {
            "strategy_spec": {
                **dict(((info.get("research_meta") or {}).get("strategy_spec") or {})),
                "strategy_id": SID,
                "signal_formula_key": _candidate_formula_key(info),
            }
        },
    }


def _grid_html(payload: Dict[str, Any]) -> str:
    rows = payload["results"]
    best = payload["best"]
    wf = payload.get("best_walkforward")
    wf_text = "未运行"
    if wf:
        wf_text = (
            f"aggregate={wf['aggregate_oos_sharpe']:.2f}, worst={wf['worst_oos_sharpe']:.2f}, "
            f"盈利 split={wf['pct_profitable_splits']:.0%}, capacity_ok={wf['capacity_ok']}"
        )
    body = "\n".join(
        "<tr>"
        + f"<td>{row['scenario']}</td>"
        + f"<td>{json.dumps(row['parameters'], ensure_ascii=False)}</td>"
        + f"<td>{row['sharpe']:.4f}</td>"
        + f"<td>{row['cagr']:.2%}</td>"
        + f"<td>{row['max_drawdown_pct']:.2%}</td>"
        + f"<td>{row['calmar_ratio']:.4f}</td>"
        + f"<td>{row['total_return']:.2%}</td>"
        + f"<td>{row['annual_gross_turnover']:.2%}</td>"
        + f"<td>{row['p95_adv_participation']:.2%}</td>"
        + f"<td>{row['total_trades']}</td>"
        + "</tr>"
        for row in rows
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{SID} risk control grid</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 28px; color: #172033; background: #f8fafc; }}
h1 {{ margin: 0 0 8px; }}
p {{ color: #526072; }}
table {{ width: 100%; border-collapse: collapse; background: #fff; }}
th, td {{ border: 1px solid #d8dee8; padding: 8px 10px; text-align: right; vertical-align: top; }}
th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) {{ text-align: left; }}
th {{ background: #eef2f7; }}
.summary {{ background: #fff; border: 1px solid #d8dee8; padding: 14px 16px; margin: 14px 0 18px; }}
</style>
</head>
<body>
<h1>{SID} 风险控制参数网格</h1>
<p>区间 {payload['period']}，初始资金 {payload['initial_cash']:,}，成本口径 small_cap_realistic。</p>
<div class="summary">
<b>Best:</b> {best['scenario']} |
Sharpe={best['sharpe']:.4f} |
CAGR={best['cagr']:.2%} |
MaxDD={best['max_drawdown_pct']:.2%} |
Calmar={best['calmar_ratio']:.4f}<br>
<b>Walk-forward best check:</b> {wf_text}
</div>
<table>
<thead><tr><th>scenario</th><th>parameters</th><th>Sharpe</th><th>CAGR</th><th>MaxDD</th><th>Calmar</th><th>Total Return</th><th>Annual Gross Turnover</th><th>p95 ADV</th><th>Trades</th></tr></thead>
<tbody>
{body}
</tbody>
</table>
</body>
</html>
"""


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    raise SystemExit(main())
