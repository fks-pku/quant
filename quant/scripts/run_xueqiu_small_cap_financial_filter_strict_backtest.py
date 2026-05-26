"""Run strict backtests for the Xueqiu small-cap financial-filter candidate."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant.api.research_bp import (
    _DuckDBDailyDateProvider,
    _cn_survivorship_audit,
    _load_cn_benchmark_provider,
    _load_lot_sizes,
    _load_research_config,
    _strict_backtest_report,
    _strict_execution_cost_model,
)
from quant.domain.models.market import is_cn_symbol
from quant.features.backtest.benchmark import BenchmarkProvider
from quant.features.backtest.engine import Backtester
from quant.features.strategies.xueqiu_small_cap_financial_filter.strategy import (
    XueqiuSmallCapFinancialFilterStrategy,
)
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.trading.sub_portfolio import SubPortfolio
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
from quant.infrastructure.research.reporting import build_research_stage_report_html


START = datetime(2016, 1, 1)
END = datetime(2025, 12, 31)
INITIAL_CASH = 20000.0
STRATEGY_ID = "xueqiu_small_cap_financial_filter"
TITLE = "Xueqiu small-cap financial-filter rotation"
SOURCE_URL = "https://xueqiu.com/7708198303/333999968"
REPORT_ROOT = Path("quant/infrastructure/var/research/reports")
DEFAULT_TARGET_CAGR = 0.10
DEFAULT_TARGET_MAX_DRAWDOWN = -0.30
COMMISSION_CFG = {
    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
    "HK": {"type": "hk_realistic"},
    "CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0},
}


SCENARIOS: List[Dict[str, Any]] = [
    {
        "name": "top5_source_month_filter",
        "max_positions": 5,
        "min_positions": 3,
        "target_exposure": 1.0,
        "empty_months": [1, 4],
        "risk_index_symbol": "",
    },
    {
        "name": "top5_no_empty_month_filter",
        "max_positions": 5,
        "min_positions": 3,
        "target_exposure": 1.0,
        "empty_months": [],
        "risk_index_symbol": "",
    },
    {
        "name": "top5_source_month_plus_shenzhen_stop",
        "max_positions": 5,
        "min_positions": 3,
        "target_exposure": 1.0,
        "empty_months": [1, 4],
        "risk_index_symbol": "399001",
        "index_drawdown_lookback": 5,
        "index_drawdown_threshold": -0.05,
    },
    {
        "name": "top3_source_month_plus_shenzhen_stop",
        "max_positions": 3,
        "min_positions": 3,
        "target_exposure": 1.0,
        "empty_months": [1, 4],
        "risk_index_symbol": "399001",
        "index_drawdown_lookback": 5,
        "index_drawdown_threshold": -0.05,
    },
]


def main() -> None:
    report_dir = REPORT_ROOT / STRATEGY_ID
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "runs").mkdir(parents=True, exist_ok=True)
    target_cagr, target_max_drawdown = _target_thresholds()
    stock_symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit = _load_shared_inputs()
    rows = []
    strict_reports = {}
    for scenario in SCENARIOS:
        scenario_symbols = _scenario_symbols(stock_symbols, scenario)
        print(f"Running {scenario['name']} on {len(stock_symbols)} stocks", flush=True)
        strict_report = _run_one(
            scenario,
            stock_symbols,
            scenario_symbols,
            lot_sizes,
            benchmark_provider,
            benchmark_meta,
            survivorship_audit,
        )
        strict_reports[scenario["name"]] = strict_report
        metrics = strict_report.get("metrics") or {}
        diagnostics = strict_report.get("diagnostics") or {}
        capacity = strict_report.get("capacity") or {}
        row = {
            "scenario": scenario["name"],
            "parameters": dict(scenario),
            "symbols": scenario_symbols,
            "stock_universe_size": len(stock_symbols),
            "sharpe": metrics.get("sharpe"),
            "sortino": metrics.get("sortino"),
            "cagr": metrics.get("cagr"),
            "total_return": metrics.get("total_return"),
            "max_drawdown_pct": metrics.get("max_drawdown_pct"),
            "calmar_ratio": metrics.get("calmar_ratio"),
            "total_trades": metrics.get("total_trades"),
            "cost_drag_pct": diagnostics.get("cost_drag_pct"),
            "p95_adv_participation": capacity.get("p95_adv_participation"),
            "max_adv_participation": capacity.get("max_adv_participation"),
            "meets_goal": _meets_goal(metrics, target_cagr, target_max_drawdown),
        }
        rows.append(row)
        print(json.dumps(_compact_row(row), ensure_ascii=False), flush=True)

    best = _select_best(rows, target_cagr, target_max_drawdown)
    report_path, result_path = _write_outputs(rows, strict_reports, best, target_cagr, target_max_drawdown)
    print(
        json.dumps(
            {
                "strategy_id": STRATEGY_ID,
                "best": _compact_row(best),
                "report_path": str(report_path),
                "result_path": str(result_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _load_shared_inputs() -> Tuple[List[str], Dict[str, int], BenchmarkProvider, Dict[str, Any], Dict[str, Any]]:
    db_provider = DuckDBProvider()
    db_provider.connect()
    try:
        rows = db_provider.storage.conn.execute(
            """
            SELECT DISTINCT symbol
            FROM daily_cn_ochl
            WHERE CAST(timestamp AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
            ORDER BY symbol
            """,
            [START, END],
        ).fetchall()
        stock_symbols = [str(row[0]) for row in rows if is_cn_symbol(str(row[0]))]
        all_required_symbols = list(stock_symbols)
        for scenario in SCENARIOS:
            all_required_symbols.extend(_scenario_extras(scenario))
        lot_sizes = _load_lot_sizes(db_provider, list(dict.fromkeys(all_required_symbols)), is_cn_symbol)
        benchmark_provider, benchmark_meta = _load_cn_benchmark_provider(db_provider, START, END, BenchmarkProvider)
        survivorship_audit = _cn_survivorship_audit(db_provider, START, END, formula_key=STRATEGY_ID)
    finally:
        db_provider.disconnect()
    return stock_symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit


def _scenario_symbols(stock_symbols: List[str], scenario: Dict[str, Any]) -> List[str]:
    return list(dict.fromkeys([*stock_symbols, *_scenario_extras(scenario)]))


def _scenario_extras(scenario: Dict[str, Any]) -> List[str]:
    symbol = str(scenario.get("risk_index_symbol") or "")
    return [symbol] if symbol else []


def _run_one(
    scenario: Dict[str, Any],
    stock_symbols: List[str],
    all_symbols: List[str],
    lot_sizes: Dict[str, int],
    benchmark_provider: BenchmarkProvider,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
) -> Dict[str, Any]:
    strategy_info = {
        "name": TITLE,
        "description": "xueqiu small_cap market_cap total_mv financial filter",
        "research_meta": {"strategy_spec": _strategy_spec(scenario, len(stock_symbols))},
    }
    execution_cost_model = _strict_execution_cost_model(STRATEGY_ID, strategy_info, True)
    data_provider = _DuckDBDailyDateProvider(
        all_symbols,
        START,
        END,
        include_daily_basic=True,
        include_financial_indicators=False,
        include_execution_liquidity_features=True,
        cache_enabled=True,
    )
    strategy = XueqiuSmallCapFinancialFilterStrategy(symbols=all_symbols, **_strategy_kwargs(scenario))
    backtest_config = {"slippage_bps": 5, "execution_cost_model": execution_cost_model}
    bt_config = {
        "backtest": backtest_config,
        "execution": {"commission": COMMISSION_CFG},
        "data": {"default_timeframe": "1d"},
        "risk": {"max_position_pct": 1.0, "max_sector_pct": 1.0, "max_daily_loss_pct": 0.10, "max_leverage": 1.0},
    }
    backtester = Backtester(
        bt_config,
        portfolio_class=Portfolio,
        risk_engine_class=RiskEngine,
        sub_portfolio_class=SubPortfolio,
        lot_sizes=lot_sizes,
        benchmark_provider=benchmark_provider,
    )
    try:
        result = backtester.run(
            start=START,
            end=END,
            strategies=[strategy],
            initial_cash=INITIAL_CASH,
            data_provider=data_provider,
            symbols=all_symbols,
        )
    finally:
        data_provider.close()

    benchmark_equity_curve = benchmark_provider.get_benchmark_equity(START, END, INITIAL_CASH) if benchmark_provider else None
    return _strict_backtest_report(
        result,
        START,
        END,
        INITIAL_CASH,
        all_symbols,
        benchmark_meta,
        lot_sizes,
        strategy,
        benchmark_equity_curve,
        survivorship_audit,
        {**backtest_config, "commission": COMMISSION_CFG},
    )


def _strategy_kwargs(scenario: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in scenario.items() if key != "name"}


def _strategy_spec(scenario: Dict[str, Any], stock_count: int) -> Dict[str, Any]:
    index_stop = str(scenario.get("risk_index_symbol") or "")
    empty_months = scenario.get("empty_months") or []
    return {
        "strategy_id": STRATEGY_ID,
        "signal_formula_key": "xueqiu_small_cap_financial_filter",
        "strategy_type": "small_cap_size_rotation",
        "prediction_direction": "lower_market_cap_is_better_after_financial_filters",
        "parameters": {
            key: value
            for key, value in scenario.items()
            if key != "name"
        },
        "parameter_explanations": {
            "max_positions": "每次调仓最多持有的股票数量；越小越集中，收益弹性和个股风险都更高。",
            "min_positions": "候选数量低于该值时不强行满仓，避免候选池太窄时硬买。",
            "target_exposure": "目标总仓位比例；1.0 表示满足条件时满仓等权持有。",
            "empty_months": "按月份主动空仓的风控规则；当前用于表达雪球原始策略里的 1 月和 4 月防御逻辑。",
            "risk_index_symbol": "指数级风险过滤参考标的；为空表示不启用，配置深成指等代码时用于大跌止损代理。",
        },
        "lookback_days": 1,
        "horizon_days": 5,
        "execution_lag_days": 1,
        "rebalance_frequency": "weekly_tuesday_open_approximated_by_monday_close_signal",
        "source_url": SOURCE_URL,
        "universe": f"Full A-share stock universe from daily_cn_ochl ({stock_count} symbols)",
        "required_fields": [
            "open",
            "high",
            "low",
            "close",
            "adj_close",
            "volume",
            "turnover",
            "total_mv",
            "circ_mv",
            "pe_ttm",
            "pe",
            "ps_ttm",
            "ps",
            "is_st",
            "tradable",
            "has_daily_bar",
            "is_listed",
            "list_status",
        ],
        "strategy_logic": {
            "core_idea": "A 股小市值溢价叠加基础财务正向过滤：排除亏损和收入规模过小的壳化/高退市风险股票后，集中持有市值最小的 3-5 只。",
            "universe": f"daily_cn_ochl 全 A 股个股池，回测期内共 {stock_count} 个代码；不使用当前成分股列表。",
            "entry_filters": [
                "排除 ST、停牌、不可交易、非上市、list_status != L",
                "收盘价 >= 2，20 日平均成交额 >= 20000 本地 amount 单位",
                "point-in-time total_mv/circ_mv >= 100000，约 RMB 10 亿",
                "正利润代理：pe_ttm/pe 至少一项为正",
                "收入代理：total_mv / ps_ttm 或 ps >= 10000，约 RMB 1 亿",
            ],
            "ranking_rule": "对过滤后的候选股票按 point-in-time 市值升序排列，市值越小优先级越高。",
            "portfolio_construction": f"选择过滤后市值最小的 {scenario.get('max_positions')} 只，目标总敞口 {float(scenario.get('target_exposure') or 0.0):.2%}，单只目标权重为总敞口 / max_positions。",
            "rebalance_rule": "雪球帖写每周二 10:00 调仓；本地日线严格回测用周一收盘后生成信号、下一交易日开盘 T+1 尝试成交近似。",
            "exit_rule": "每个交易日先检查持仓 ST、停牌、退市状态、不可交易、价格或流动性风险；到调仓日时卖出不再入选的持仓。",
            "risk_budget": f"1 月/4 月空仓={bool(empty_months)}；指数大跌止损代理={index_stop or '未启用'}；执行使用 CN 佣金、5bps 基础滑点和 small_cap_realistic 冲击成本。",
        },
    }


def _target_thresholds() -> Tuple[float, float]:
    try:
        cfg = _load_research_config()
        gate = dict(getattr(cfg, "production_gate_config", {}) or {})
    except Exception:
        gate = {}
    target_cagr = DEFAULT_TARGET_CAGR
    try:
        target_cagr = max(target_cagr, float(gate.get("min_strict_cagr", target_cagr)))
    except (TypeError, ValueError):
        target_cagr = DEFAULT_TARGET_CAGR
    try:
        max_drawdown = -abs(float(gate.get("max_strict_drawdown", abs(DEFAULT_TARGET_MAX_DRAWDOWN))))
    except (TypeError, ValueError):
        max_drawdown = DEFAULT_TARGET_MAX_DRAWDOWN
    return target_cagr, max_drawdown


def _meets_goal(
    metrics: Dict[str, Any],
    target_cagr: float = DEFAULT_TARGET_CAGR,
    target_max_drawdown: float = DEFAULT_TARGET_MAX_DRAWDOWN,
) -> bool:
    return float(metrics.get("cagr") or 0.0) > target_cagr and float(metrics.get("max_drawdown_pct") or 0.0) >= target_max_drawdown


def _select_best(
    rows: List[Dict[str, Any]],
    target_cagr: float = DEFAULT_TARGET_CAGR,
    target_max_drawdown: float = DEFAULT_TARGET_MAX_DRAWDOWN,
) -> Dict[str, Any]:
    drawdown_controlled = [row for row in rows if float(row.get("max_drawdown_pct") or 0.0) >= target_max_drawdown]
    candidates = [row for row in drawdown_controlled if float(row.get("cagr") or 0.0) > target_cagr] or drawdown_controlled or rows
    return max(candidates, key=lambda row: _score_row(row, target_cagr, target_max_drawdown))


def _write_outputs(
    rows: List[Dict[str, Any]],
    strict_reports: Dict[str, Dict[str, Any]],
    best: Dict[str, Any],
    target_cagr: float,
    target_max_drawdown: float,
) -> Tuple[Path, Path]:
    strategy_dir = REPORT_ROOT / STRATEGY_ID
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "strategy_id": STRATEGY_ID,
        "source_url": SOURCE_URL,
        "run_ts": run_ts,
        "period": f"{START.date()}-{END.date()}",
        "initial_cash": INITIAL_CASH,
        "thresholds": {"target_cagr": target_cagr, "target_max_drawdown": target_max_drawdown},
        "rows": rows,
        "best": best,
        "strict_reports": strict_reports,
    }
    result_path = strategy_dir / "grid_result.json"
    last_result_path = strategy_dir / "last_result.json"
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    last_result_path.write_text(json.dumps(strict_reports[str(best["scenario"])], ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (strategy_dir / "runs" / f"{run_ts}_grid_result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (strategy_dir / "runs" / f"{run_ts}_result.json").write_text(
        json.dumps(strict_reports[str(best["scenario"])], ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    row = _hypothesis_row(best, strict_reports[str(best["scenario"])], target_cagr, target_max_drawdown, rows)
    result = {"run_id": f"{STRATEGY_ID}_strict_grid", "backtested": len(rows), "rejected": 0, "errors": []}
    html = build_research_stage_report_html("strict_backtest", result, [row], generated_at=datetime.now(timezone.utc).isoformat())
    html = _insert_scenario_grid(html, rows, target_cagr, target_max_drawdown)
    report_path = strategy_dir / "strict_backtest_report.html"
    report_path.write_text(html, encoding="utf-8")
    (strategy_dir / "runs" / f"{run_ts}_strict_backtest_report.html").write_text(html, encoding="utf-8")
    return report_path, result_path


def _hypothesis_row(
    best: Dict[str, Any],
    strict_report: Dict[str, Any],
    target_cagr: float,
    target_max_drawdown: float,
    sensitivity_rows: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    metrics = strict_report.get("metrics") or {}
    cagr = float(metrics.get("cagr") or 0.0)
    max_dd = float(metrics.get("max_drawdown_pct") or 0.0)
    sharpe = float(metrics.get("sharpe") or 0.0)
    verdict = "pass" if _meets_goal(metrics, target_cagr, target_max_drawdown) else "fail"
    return {
        "strategy_id": STRATEGY_ID,
        "title": f"{TITLE} - {best['scenario']}",
        "status": "needs_walkforward_validation" if verdict == "pass" else "needs_more_research",
        "metrics": {
            "strict_backtest": strict_report,
            "parameter_sensitivity": _parameter_sensitivity_payload(best, sensitivity_rows or [], target_cagr, target_max_drawdown),
            "research_stage_conclusions": {
                "strict_backtest": {
                    "label": "Strict backtest",
                    "verdict": verdict,
                    "conclusion": f"Strict backtest: Sharpe={sharpe:.2f}, CAGR={cagr:.2%}, MaxDD={max_dd:.2%}; threshold CAGR>{target_cagr:.2%}, MaxDD>={target_max_drawdown:.2%}.",
                    "method": "Project Backtester with T+1 open execution, CN commission, lot size, status/limit checks, small-cap liquidity impact, and PIT daily_basic PE/PS proxies.",
                }
            },
        },
        "evidence": {"strategy_spec": _strategy_spec(best.get("parameters") or {}, int(best.get("stock_universe_size") or 0))},
    }


def _parameter_sensitivity_payload(
    best: Dict[str, Any],
    rows: List[Dict[str, Any]],
    target_cagr: float,
    target_max_drawdown: float,
) -> Dict[str, Any]:
    if not rows:
        return {}
    sorted_rows = sorted(rows, key=lambda item: _score_row(item, target_cagr, target_max_drawdown), reverse=True)
    best_score = max((abs(float(row.get("sharpe") or 0.0)) for row in rows), default=0.0)
    degradations = [
        max(0.0, (best_score - abs(float(row.get("sharpe") or 0.0))) / best_score * 100.0)
        for row in rows
        if best_score > 0
    ]
    pass_count = sum(1 for row in rows if row.get("meets_goal") is True)
    max_degradation = max(degradations) if degradations else None
    status = "pass" if pass_count >= 2 and (max_degradation is not None and max_degradation <= 30.0) else ("warn" if pass_count else "fail")
    variants = []
    for row in sorted_rows:
        metrics_verdict = "pass" if row.get("meets_goal") is True else "fail"
        variants.append(
            {
                "name": row.get("scenario"),
                "parameters": row.get("parameters") or {},
                "cagr": row.get("cagr"),
                "max_drawdown_pct": row.get("max_drawdown_pct"),
                "sharpe": row.get("sharpe"),
                "max_adv_participation": row.get("max_adv_participation"),
                "verdict": metrics_verdict,
            }
        )
    return {
        "status": status,
        "method": "Coarse scenario sensitivity from the strict grid: position count, empty-month rule, and Shenzhen index stop variants.",
        "base_params": best.get("parameters") or {},
        "selected_params": best.get("parameters") or {},
        "best_params": (sorted_rows[0].get("parameters") or {}) if sorted_rows else {},
        "tested_count": len(rows),
        "pass_count": pass_count,
        "max_degradation_pct": max_degradation,
        "stability_note": "This is a first-pass scenario sensitivity audit from existing strict-grid variants; it is not a fresh train-window parameter search.",
        "rows": variants,
    }


def _insert_scenario_grid(
    html: str,
    rows: List[Dict[str, Any]],
    target_cagr: float,
    target_max_drawdown: float,
) -> str:
    body = "\n".join(
        "<tr>"
        f"<td>{row['scenario']}</td>"
        f"<td>{float(row.get('cagr') or 0.0):.2%}</td>"
        f"<td>{float(row.get('max_drawdown_pct') or 0.0):.2%}</td>"
        f"<td>{float(row.get('sharpe') or 0.0):.2f}</td>"
        f"<td>{float(row.get('calmar_ratio') or 0.0):.2f}</td>"
        f"<td>{int(row.get('total_trades') or 0)}</td>"
        f"<td>{float(row.get('cost_drag_pct') or 0.0):.2f}%</td>"
        f"<td>{float(row.get('max_adv_participation') or 0.0):.2%}</td>"
        f"<td>{json.dumps(row.get('parameters') or {}, ensure_ascii=False)}</td>"
        "</tr>"
        for row in sorted(rows, key=lambda item: _score_row(item, target_cagr, target_max_drawdown), reverse=True)
    )
    grid = (
        '<h3>雪球策略场景比较</h3><div class="table-wrap"><table>'
        "<thead><tr><th>场景</th><th>CAGR</th><th>MaxDD</th><th>Sharpe</th><th>Calmar</th><th>Trades</th><th>Cost Drag</th><th>Max ADV</th><th>参数</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )
    marker = "<h3>回测配置</h3>"
    return html.replace(marker, f"{grid}<h3>回测配置</h3>", 1)


def _score_row(
    row: Dict[str, Any],
    target_cagr: float = DEFAULT_TARGET_CAGR,
    target_max_drawdown: float = DEFAULT_TARGET_MAX_DRAWDOWN,
) -> Tuple[int, float, float, float, float]:
    cagr = float(row.get("cagr") or 0.0)
    max_dd = float(row.get("max_drawdown_pct") or 0.0)
    return (
        1 if max_dd >= target_max_drawdown and cagr > target_cagr else 0,
        float(row.get("sharpe") or 0.0),
        max_dd,
        cagr / max(abs(max_dd), 1e-9),
        cagr,
    )


def _compact_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "scenario": row.get("scenario"),
        "cagr": row.get("cagr"),
        "max_drawdown_pct": row.get("max_drawdown_pct"),
        "sharpe": row.get("sharpe"),
        "calmar_ratio": row.get("calmar_ratio"),
        "total_trades": row.get("total_trades"),
        "cost_drag_pct": row.get("cost_drag_pct"),
        "max_adv_participation": row.get("max_adv_participation"),
        "meets_goal": row.get("meets_goal"),
    }


if __name__ == "__main__":
    main()
