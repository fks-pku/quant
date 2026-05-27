"""Run strict backtests for A-share gold-equity ETF barbell timing."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Tuple

_project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_project_root))

from quant.api.research_bp import (
    _DuckDBDailyDateProvider,
    _cn_survivorship_audit,
    _load_cn_benchmark_provider,
    _load_lot_sizes,
    _strict_backtest_report,
    _strict_execution_cost_model,
)
from quant.domain.models.market import is_cn_symbol
from quant.features.backtest.benchmark import BenchmarkProvider
from quant.features.backtest.engine import Backtester
try:
    from quant.features.strategies.ashare_gold_equity_barbell_timing.strategy import (
        AShareGoldEquityBarbellTimingStrategy,
        DEFAULT_PIT_SIZE_FIELDS,
    )
except ModuleNotFoundError:
    from quant.features.rejected_strategy.ashare_gold_equity_barbell_timing.strategy import (
        AShareGoldEquityBarbellTimingStrategy,
        DEFAULT_PIT_SIZE_FIELDS,
    )
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.trading.sub_portfolio import SubPortfolio
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
from quant.infrastructure.research.cn_etf_universe import (
    build_gold_equity_barbell_pit_universe,
    flatten_category_symbols,
)
from quant.infrastructure.research.reporting import build_research_full_report_html, build_research_stage_report_html


START = datetime(2016, 1, 1)
END = datetime(2025, 12, 31)
UNIVERSE_AS_OF = None
UNIVERSE_MIN_HISTORY_DAYS_AS_OF = 0
UNIVERSE_MAX_SYMBOLS_PER_CATEGORY = 0
INITIAL_CASH = 20000.0
STRATEGY_ID = "ashare_gold_equity_barbell_timing"
TITLE = "黄金-大盘 ETF 杠铃择时"
REPORT_ROOT = Path("quant/infrastructure/var/research/reports")
COMMISSION_CFG = {
    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
    "HK": {"type": "hk_realistic"},
    "CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0},
}


SCENARIOS: List[Dict[str, Any]] = [
    {
        "name": "monthly_63d_120ma_half_equity_half_gold",
        "timing_symbol": "000300",
        "momentum_lookback": 63,
        "momentum_skip": 1,
        "trend_window": 120,
        "volatility_window": 20,
        "liquidity_window": 20,
        "min_avg_turnover": 20_000_000.0,
        "target_exposure": 0.98,
        "risk_leg_weight": 0.50,
        "holding_days": 20,
        "require_pit_size": True,
        "pit_size_fields": list(DEFAULT_PIT_SIZE_FIELDS),
    },
    {
        "name": "monthly_63d_120ma_40pct_equity_60pct_gold",
        "timing_symbol": "000300",
        "momentum_lookback": 63,
        "momentum_skip": 1,
        "trend_window": 120,
        "volatility_window": 20,
        "liquidity_window": 20,
        "min_avg_turnover": 20_000_000.0,
        "target_exposure": 0.98,
        "risk_leg_weight": 0.40,
        "holding_days": 20,
        "require_pit_size": True,
        "pit_size_fields": list(DEFAULT_PIT_SIZE_FIELDS),
    },
    {
        "name": "weekly_63d_120ma_half_equity_half_gold",
        "timing_symbol": "000300",
        "momentum_lookback": 63,
        "momentum_skip": 1,
        "trend_window": 120,
        "volatility_window": 20,
        "liquidity_window": 20,
        "min_avg_turnover": 20_000_000.0,
        "target_exposure": 0.98,
        "risk_leg_weight": 0.50,
        "holding_days": 5,
        "require_pit_size": True,
        "pit_size_fields": list(DEFAULT_PIT_SIZE_FIELDS),
    },
    {
        "name": "monthly_126d_200ma_half_equity_half_gold",
        "timing_symbol": "000300",
        "momentum_lookback": 126,
        "momentum_skip": 1,
        "trend_window": 200,
        "volatility_window": 20,
        "liquidity_window": 20,
        "min_avg_turnover": 20_000_000.0,
        "target_exposure": 0.98,
        "risk_leg_weight": 0.50,
        "holding_days": 20,
        "require_pit_size": True,
        "pit_size_fields": list(DEFAULT_PIT_SIZE_FIELDS),
    },
]

RISK_EXIT_VARIANTS: List[Dict[str, Any]] = [
    {"risk_exit_label": "baseline_no_risk_exit", "enable_risk_exit": False, "risk_exit": {"enabled": False}},
    {"risk_exit_label": "risk_exit_enabled", "enable_risk_exit": True, "risk_exit": {"enabled": True}},
]


DETAIL_SECTION = """
<h3>策略执行逻辑</h3>
<div class="table-wrap"><table><thead><tr><th>每日步骤</th><th>运行规则</th><th>信号解释</th></tr></thead><tbody>
<tr><td>1. 更新数据</td><td>读取大盘/宽基/红利/创业板 ETF 与黄金 ETF 日线，ETF/LOF 使用 fund NAV 复权后的 total-return bar。</td><td>避免 ETF 拆分或分红被误记为价格暴跌。</td></tr>
<tr><td>2. 市场温度</td><td>以沪深300 ETF 为温度计：收盘价高于均线且中期动量为正时为 risk-on，否则 risk-off。</td><td>权益风险只在大盘趋势向上时打开。</td></tr>
<tr><td>3. 权益腿选择</td><td>risk-on 时在上证50、沪深300、创业板、创业板50、红利 ETF 中按动量/波动打分选 1 只。</td><td>不固定某只股票，也不使用中证500/中证1000等小盘 proxy。</td></tr>
<tr><td>4. 防守腿</td><td>黄金 ETF 是防守腿；risk-on 时与权益腿做杠铃，risk-off 时单独承担目标敞口。</td><td>用与 A 股低相关的资产降低熊市权益暴露。</td></tr>
<tr><td>5. 调仓与执行</td><td>每 5 或 20 个交易日调仓，信号收盘生成，订单 T+1 开盘执行。</td><td>严格回测包含 ETF 基金佣金、手数、停牌/涨跌停约束和流动性冲击成本。</td></tr>
</tbody></table></div>
"""


def main() -> None:
    universe = build_gold_equity_barbell_pit_universe(
        universe_as_of=UNIVERSE_AS_OF,
        min_history_days_as_of=UNIVERSE_MIN_HISTORY_DAYS_AS_OF,
        max_symbols_per_category=UNIVERSE_MAX_SYMBOLS_PER_CATEGORY,
        universe_start=START,
        universe_end=END,
    )
    _validate_pit_universe(universe)
    scenarios = [_with_pit_universe(scenario, universe) for scenario in _risk_exit_scenarios(SCENARIOS)]
    all_symbols = sorted({symbol for scenario in scenarios for symbol in scenario["symbols"]})
    lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit = _load_shared_inputs(all_symbols)
    rows = []
    strict_reports = {}
    for scenario in scenarios:
        print(f"Running {scenario['name']} on {len(all_symbols)} ETFs", flush=True)
        strict_report = _run_one(scenario, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit)
        strict_reports[scenario["name"]] = strict_report
        metrics = strict_report.get("metrics") or {}
        row = {
            "scenario": scenario["name"],
            "base_scenario": scenario.get("base_scenario", scenario["name"]),
            "risk_exit_label": scenario.get("risk_exit_label", "risk_exit_enabled"),
            "enable_risk_exit": bool(scenario.get("enable_risk_exit", True)),
            "symbols": all_symbols,
            "parameters": _scenario_parameters(scenario),
            "risk_category_symbols": scenario["risk_category_symbols"],
            "defensive_category_symbols": scenario["defensive_category_symbols"],
            "timing_symbol": scenario["timing_symbol"],
            "registered_universe_counts": scenario.get("registered_universe_counts", {}),
            "universe_registry_version": scenario.get("universe_registry_version", "audited_stable_etf_registry_v1"),
            "sharpe": metrics.get("sharpe"),
            "cagr": metrics.get("cagr"),
            "total_return": metrics.get("total_return"),
            "max_drawdown_pct": metrics.get("max_drawdown_pct"),
            "total_trades": metrics.get("total_trades"),
            "meets_goal": _meets_goal(metrics),
        }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))

    best = _select_best(rows)
    report_path, result_path = _write_outputs(rows, strict_reports, best, universe)
    print(json.dumps({"strategy_id": STRATEGY_ID, "best": best, "report_path": str(report_path), "result_path": str(result_path)}, ensure_ascii=False, indent=2))


def _with_pit_universe(scenario: Dict[str, Any], universe: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(scenario)
    result["risk_category_symbols"] = {
        key: list(value)
        for key, value in (universe.get("risk_category_symbols") or {}).items()
    }
    result["defensive_category_symbols"] = {
        key: list(value)
        for key, value in (universe.get("defensive_category_symbols") or {}).items()
    }
    result["symbols"] = list(
        dict.fromkeys(
            [
                *flatten_category_symbols(result["risk_category_symbols"], result["defensive_category_symbols"]),
                str(result["timing_symbol"]),
            ]
        )
    )
    result["registered_universe_counts"] = dict(universe.get("registered_universe_counts") or {})
    result["universe_registry_version"] = universe.get("universe_registry_version") or "audited_stable_etf_registry_v1"
    return result


def _risk_exit_scenarios(scenarios: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    expanded = []
    for scenario in scenarios:
        for variant in RISK_EXIT_VARIANTS:
            row = dict(scenario)
            row.update(variant)
            row["base_scenario"] = scenario["name"]
            row["name"] = f"{scenario['name']}__{variant['risk_exit_label']}"
            expanded.append(row)
    return expanded


def _validate_pit_universe(universe: Dict[str, Any]) -> None:
    risk = universe.get("risk_category_symbols") or {}
    defensive = universe.get("defensive_category_symbols") or {}
    missing = [key for key, values in {**risk, **defensive}.items() if not values]
    if missing:
        raise RuntimeError(f"Audited ETF registry universe missing required categories: {', '.join(missing)}")


def _load_shared_inputs(symbols: List[str]) -> Tuple[Dict[str, int], BenchmarkProvider, Dict[str, Any], Dict[str, Any]]:
    db_provider = DuckDBProvider()
    db_provider.connect()
    try:
        lot_sizes = _load_lot_sizes(db_provider, symbols, is_cn_symbol)
        benchmark_provider, benchmark_meta = _load_cn_benchmark_provider(db_provider, START, END, BenchmarkProvider)
        survivorship_audit = _cn_survivorship_audit(db_provider, START, END, formula_key=STRATEGY_ID)
    finally:
        db_provider.disconnect()
    return lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit


def _run_one(
    scenario: Dict[str, Any],
    lot_sizes: Dict[str, int],
    benchmark_provider: BenchmarkProvider,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
) -> Dict[str, Any]:
    symbols = list(scenario["symbols"])
    execution_cost_model = _strict_execution_cost_model(
        STRATEGY_ID,
        {"name": TITLE, "description": "gold-equity ETF barbell timing", "parameters": dict(scenario)},
        True,
    )
    data_provider = _DuckDBDailyDateProvider(
        symbols,
        START,
        END,
        include_daily_basic=False,
        include_execution_liquidity_features=True,
    )
    strategy = AShareGoldEquityBarbellTimingStrategy(
        risk_category_symbols={key: list(value) for key, value in scenario["risk_category_symbols"].items()},
        defensive_category_symbols={key: list(value) for key, value in scenario["defensive_category_symbols"].items()},
        timing_symbol=str(scenario["timing_symbol"]),
        momentum_lookback=int(scenario["momentum_lookback"]),
        momentum_skip=int(scenario["momentum_skip"]),
        trend_window=int(scenario["trend_window"]),
        volatility_window=int(scenario["volatility_window"]),
        liquidity_window=int(scenario["liquidity_window"]),
        min_avg_turnover=float(scenario["min_avg_turnover"]),
        target_exposure=float(scenario["target_exposure"]),
        risk_leg_weight=float(scenario["risk_leg_weight"]),
        holding_days=int(scenario["holding_days"]),
        pit_size_fields=list(scenario["pit_size_fields"]),
        require_pit_size=bool(scenario["require_pit_size"]),
        enable_risk_exit=bool(scenario.get("enable_risk_exit", True)),
        risk_exit=dict(scenario.get("risk_exit") or {}),
    )
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
        bt_result = backtester.run(
            start=START,
            end=END,
            strategies=[strategy],
            initial_cash=INITIAL_CASH,
            data_provider=data_provider,
            symbols=symbols,
        )
    finally:
        data_provider.close()

    benchmark_equity_curve = benchmark_provider.get_benchmark_equity(START, END, INITIAL_CASH) if benchmark_provider else None
    return _strict_backtest_report(
        bt_result,
        START,
        END,
        INITIAL_CASH,
        symbols,
        benchmark_meta,
        lot_sizes,
        strategy,
        benchmark_equity_curve,
        survivorship_audit,
        {**backtest_config, "commission": COMMISSION_CFG},
    )


def _meets_goal(metrics: Dict[str, Any]) -> bool:
    return float(metrics.get("cagr") or 0.0) > 0.10 and float(metrics.get("max_drawdown_pct") or 0.0) >= -0.30


def _select_best(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    viable = [row for row in rows if row["meets_goal"]]
    candidates = viable or rows
    return max(
        candidates,
        key=lambda row: (
            float(row.get("cagr") or 0.0) / max(abs(float(row.get("max_drawdown_pct") or 0.0)), 1e-9),
            float(row.get("sharpe") or 0.0),
        ),
    )


def _scenario_parameters(scenario: Dict[str, Any]) -> Dict[str, Any]:
    excluded = {
        "name",
        "symbols",
        "risk_category_symbols",
        "defensive_category_symbols",
        "registered_universe_counts",
        "universe_registry_version",
        "pit_size_fields",
        "base_scenario",
        "risk_exit_label",
    }
    return {key: value for key, value in scenario.items() if key not in excluded}


def _parameter_explanations() -> Dict[str, str]:
    return {
        "timing_symbol": "市场温度判断标的；价格在趋势线上且动量为正时打开权益风险。",
        "momentum_lookback": "ETF 动量观察窗口；用于计算风险调整动量排名。",
        "momentum_skip": "动量计算跳过最近交易日数量，用于降低短期反转噪声。",
        "trend_window": "大盘趋势均线窗口；用于判断 risk-on / risk-off。",
        "volatility_window": "波动率估计窗口；动量分数会惩罚高波动标的。",
        "liquidity_window": "平均成交额观察窗口；用于过滤流动性不足的 ETF。",
        "min_avg_turnover": "最低平均成交额门槛；低于该值不进入候选。",
        "target_exposure": "目标总仓位比例；低于 1.0 表示保留少量现金缓冲。",
        "risk_leg_weight": "risk-on 时权益腿目标权重；剩余权重分配给黄金防御腿。",
        "holding_days": "调仓/持有间隔；到期才重新排序和换仓。",
        "require_pit_size": "是否要求点时可见基金规模数据；用于降低 ETF 幸存者/规模偏差。",
        "enable_risk_exit": "是否启用 ETF 持仓级止盈止损/时间止损包；关闭时只保留原始择时调仓逻辑。",
        "risk_exit": "止盈止损开关与阈值分组，用于正式报告生成 baseline_no_risk_exit 与 risk_exit_enabled 对照。",
        "base_scenario": "不含风险退出开关后缀的原始场景名称，用于配对比较同一参数场景下的 risk_exit 关闭与开启效果。",
        "risk_exit_label": "风险退出开关分组；baseline_no_risk_exit 表示关闭 PnL 型止盈止损，risk_exit_enabled 表示启用默认止盈止损包。",
    }


def _strategy_logic(best: Dict[str, Any]) -> Dict[str, Any]:
    params = best.get("parameters") or {}
    risk_categories = best.get("risk_category_symbols") or {}
    defensive_categories = best.get("defensive_category_symbols") or {}
    risk_text = "; ".join(
        f"{category}: {', '.join(str(symbol) for symbol in symbols)}"
        for category, symbols in risk_categories.items()
    )
    defensive_text = "; ".join(
        f"{category}: {', '.join(str(symbol) for symbol in symbols)}"
        for category, symbols in defensive_categories.items()
    )
    return {
        "core_idea": (
            "Gold-equity ETF barbell timing: use CSI 300 trend and momentum as the risk-on gate, "
            "hold the strongest broad-equity ETF category together with gold ETF in risk-on regimes, "
            "and fall back to gold ETF when equity risk is off."
        ),
        "universe": (
            "Audited stable ETF registry only. Risk categories: "
            f"{risk_text or 'none'}. Defensive categories: {defensive_text or 'none'}. "
            "New ETF categories must be manually registered before research."
        ),
        "entry_filters": [
            "registered ETF category must be user-approved before the backtest window",
            "rebalance-day bar must be current and tradable",
            f"{params.get('liquidity_window', 20)}-day average turnover must be at least {float(params.get('min_avg_turnover') or 0):.0f}",
            "PIT fund size/NAV data must be visible when require_pit_size is enabled",
            "risk leg requires positive risk-adjusted momentum",
        ],
        "ranking_rule": (
            f"Risk-on is true when {params.get('timing_symbol', '000300')} closes above its "
            f"{params.get('trend_window', 120)}-day moving average and has positive "
            f"{params.get('momentum_lookback', 63)}-day skipped momentum. Among visible risk ETFs, "
            "rank by momentum divided by realized volatility and select the highest score."
        ),
        "portfolio_construction": (
            f"Target exposure is {float(params.get('target_exposure') or 0):.0%}. In risk-on regimes, "
            f"{float(params.get('risk_leg_weight') or 0):.0%} of exposure goes to the selected equity ETF "
            "and the rest to gold ETF; in risk-off regimes the target exposure is allocated to gold ETF."
        ),
        "rebalance_rule": (
            f"Every {int(params.get('holding_days') or 20)} trading days after close, recompute risk-on, "
            "select target ETF legs, submit orders, and execute them at the next trading day's open."
        ),
        "exit_rule": (
            "Positions not in the latest target basket are sold at the next execution opportunity; "
            "if equity risk turns off or no eligible risk ETF remains, equity exposure is removed and gold is kept. "
            "When risk_exit.enabled is true, ETF legs can also exit through stop_loss, trailing_take_profit, or time_stop before the rebalance gate."
        ),
        "risk_budget": (
            "Risk control comes from the risk-on trend gate, permanent gold defensive leg, PIT ETF registry, "
            "turnover and fund-size filters, risk_exit off/on audited stops, 98% maximum exposure, T+1 execution, lot-size checks, and 5% ADV participation cap."
        ),
        "parameter_explanations": _parameter_explanations(),
    }


def _parameter_sensitivity_payload(best: Dict[str, Any], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {}
    best_score = max((abs(float(row.get("sharpe") or 0.0)) for row in rows), default=0.0)
    degradations = [
        max(0.0, (best_score - abs(float(row.get("sharpe") or 0.0))) / best_score * 100.0)
        for row in rows
        if best_score > 0
    ]
    pass_count = sum(1 for row in rows if row.get("meets_goal") is True)
    max_degradation = max(degradations) if degradations else None
    variants = []
    for row in sorted(rows, key=lambda item: float(item.get("sharpe") or 0.0), reverse=True):
        variants.append(
            {
                "name": row.get("scenario"),
                "parameters": row.get("parameters") or {},
                "cagr": row.get("cagr"),
                "max_drawdown_pct": row.get("max_drawdown_pct"),
                "sharpe": row.get("sharpe"),
                "verdict": "pass" if row.get("meets_goal") is True else "warn",
            }
        )
    return {
        "status": "pass" if pass_count >= 2 and (max_degradation is not None and max_degradation <= 30.0) else ("warn" if pass_count else "fail"),
        "method": "Strict-grid scenario sensitivity over ETF timing windows, equity/gold split, and rebalance interval.",
        "base_params": best.get("parameters") or {},
        "selected_params": best.get("parameters") or {},
        "best_params": best.get("parameters") or {},
        "tested_count": len(rows),
        "pass_count": pass_count,
        "max_degradation_pct": max_degradation,
        "stability_note": "Scenario sensitivity is derived from strict-grid variants and should be treated as robustness evidence, not as a new full-sample optimization pass.",
        "rows": variants,
    }


def _write_outputs(
    rows: List[Dict[str, Any]],
    strict_reports: Dict[str, Dict[str, Any]],
    best: Dict[str, Any],
    universe: Dict[str, Any],
) -> Tuple[Path, Path]:
    strategy_dir = REPORT_ROOT / STRATEGY_ID
    strategy_dir.mkdir(parents=True, exist_ok=True)
    result_path = strategy_dir / "grid_result.json"
    last_result_path = strategy_dir / "last_result.json"
    payload = {
        "strategy_id": STRATEGY_ID,
        "start": START.date().isoformat(),
        "end": END.date().isoformat(),
        "initial_cash": INITIAL_CASH,
        "rows": rows,
        "best": best,
        "pit_universe": universe,
        "strict_reports": strict_reports,
    }
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    last_result_path.write_text(json.dumps(strict_reports[str(best["scenario"])], ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    row = _hypothesis_row(best, strict_reports[str(best["scenario"])], rows)
    result = {"run_id": f"{STRATEGY_ID}_strict_grid", "backtested": len(rows), "rejected": 0, "errors": []}
    generated = datetime.now(timezone.utc).isoformat()
    comparison = _risk_exit_comparison(rows, strict_reports, best)
    if comparison:
        comparison_text = json.dumps(comparison, ensure_ascii=False, indent=2, default=str)
        (strategy_dir / "risk_exit_comparison.json").write_text(comparison_text, encoding="utf-8")
    html = build_research_stage_report_html("strict_backtest", result, [row], generated_at=generated)
    html = _insert_detail_section(html, rows, universe)
    html = _insert_risk_exit_comparison(html, comparison, stage_only=True)
    full_html = build_research_full_report_html(result, [row], generated_at=generated)
    full_html = _insert_risk_exit_comparison(full_html, comparison)
    fast_html = build_research_stage_report_html("fast_research", result, [row], generated_at=generated)
    fast_html = _insert_risk_exit_comparison(fast_html, comparison, stage_only=True)
    walk_html = build_research_stage_report_html("walkforward_strict_audit", result, [row], generated_at=generated)
    walk_html = _insert_risk_exit_comparison(walk_html, comparison, stage_only=True)
    report_path = strategy_dir / "strict_backtest_report.html"
    report_path.write_text(html, encoding="utf-8")
    full_report_path = strategy_dir / "full_research_report.html"
    full_report_path.write_text(full_html, encoding="utf-8")
    (strategy_dir / "fast_research_report.html").write_text(fast_html, encoding="utf-8")
    (strategy_dir / "walkforward_audit_report.html").write_text(walk_html, encoding="utf-8")
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    runs_dir = strategy_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{run_ts}_strict_backtest_report.html").write_text(html, encoding="utf-8")
    (runs_dir / f"{run_ts}_full_research_report.html").write_text(full_html, encoding="utf-8")
    (runs_dir / f"{run_ts}_fast_research_report.html").write_text(fast_html, encoding="utf-8")
    (runs_dir / f"{run_ts}_walkforward_audit_report.html").write_text(walk_html, encoding="utf-8")
    if comparison:
        (runs_dir / f"{run_ts}_risk_exit_comparison.json").write_text(
            json.dumps(comparison, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    latest_dir = REPORT_ROOT / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    (latest_dir / "strict_backtest_report.html").write_text(html, encoding="utf-8")
    (latest_dir / "full_research_report.html").write_text(full_html, encoding="utf-8")
    (latest_dir / "fast_research_report.html").write_text(fast_html, encoding="utf-8")
    (latest_dir / "walkforward_audit_report.html").write_text(walk_html, encoding="utf-8")
    if comparison:
        (latest_dir / "risk_exit_comparison.json").write_text(
            json.dumps(comparison, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    return report_path, result_path


def _hypothesis_row(best: Dict[str, Any], strict_report: Dict[str, Any], rows: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    metrics = strict_report.get("metrics") or {}
    cagr = float(metrics.get("cagr") or 0.0)
    max_dd = float(metrics.get("max_drawdown_pct") or 0.0)
    sharpe = float(metrics.get("sharpe") or 0.0)
    verdict = "pass" if _meets_goal(metrics) else "warn"
    return {
        "strategy_id": STRATEGY_ID,
        "title": f"{TITLE} - {best['scenario']}",
        "status": "needs_walkforward_validation" if verdict == "pass" else "needs_more_research",
        "metrics": {
            "strict_backtest": strict_report,
            "parameter_sensitivity": _parameter_sensitivity_payload(best, rows or []),
            "research_stage_conclusions": {
                "strict_backtest": {
                    "label": "严格回测",
                    "verdict": verdict,
                    "conclusion": f"严格回测：Sharpe={sharpe:.2f}，CAGR={cagr:.2%}，MaxDD={max_dd:.2%}。",
                    "method": "项目 Backtester；信号收盘生成、订单 T+1 开盘执行；ETF 基金佣金、手数约束、涨跌停/停牌约束、流动性冲击成本；ETF/LOF 价格用 fund NAV 复权。",
                }
            },
        },
        "evidence": {
            "strategy_spec": {
                "strategy_id": STRATEGY_ID,
                "strategy_type": "etf_barbell_timing",
                "signal_formula_key": STRATEGY_ID,
                "prediction_direction": "higher_is_better",
                "scenario": best["scenario"],
                "symbols": best["symbols"],
                "universe": best["symbols"],
                "required_fields": [
                    "open",
                    "high",
                    "low",
                    "close",
                    "adj_close",
                    "volume",
                    "turnover",
                    "unit_nav",
                    "adj_nav",
                    "total_netasset",
                    "net_asset",
                ],
                "parameters": best.get("parameters") or {},
                "parameter_explanations": _parameter_explanations(),
                "strategy_logic": _strategy_logic(best),
                "lookback_days": best.get("parameters", {}).get("momentum_lookback"),
                "horizon_days": best.get("parameters", {}).get("holding_days"),
                "execution_lag_days": 1,
                "rebalance_frequency": f"every {best.get('parameters', {}).get('holding_days', 20)} trading days",
                "fallback_symbol": "518880",
                "risk_controls": {
                    "target_exposure": best.get("parameters", {}).get("target_exposure"),
                    "min_adv_value": best.get("parameters", {}).get("min_avg_turnover"),
                    "market_timing_symbol": best.get("timing_symbol", "000300"),
                },
                "risk_category_symbols": best.get("risk_category_symbols", {}),
                "defensive_category_symbols": best.get("defensive_category_symbols", {}),
                "timing_symbol": best.get("timing_symbol", "000300"),
                "pit_universe_enabled": True,
                "universe_selection_policy": "audited_stable_etf_registry",
                "universe_registry_version": best.get("universe_registry_version", "audited_stable_etf_registry_v1"),
                "registered_universe_counts": best.get("registered_universe_counts", {}),
                "universe_construction": "audited stable ETF registry; each category can only use user-approved representative ETFs and new categories require explicit registration",
                "goal": {"cagr_gt": 0.10, "max_drawdown_gte": -0.30},
            }
        },
    }


COMPARISON_METRICS = [
    ("CAGR", "pct", "cagr"),
    ("Total Return", "pct", "total_return"),
    ("Max Drawdown", "pct", "max_drawdown_pct"),
    ("Sharpe", "num", "sharpe"),
    ("Sortino", "num", "sortino"),
    ("Calmar", "num", "calmar_ratio"),
    ("Win Rate", "pct", "win_rate"),
    ("Profit Factor", "num", "profit_factor"),
    ("Total Trades", "int", "total_trades"),
    ("Round Trips", "int", "round_trip_trades"),
    ("Avg Holding Days", "num", "avg_trade_duration_days"),
    ("Commission", "money", "total_commission"),
    ("Cost Drag", "pct_points", "cost_drag_pct"),
    ("Max ADV Participation", "pct", "max_adv_participation"),
    ("P95 ADV Participation", "pct", "p95_adv_participation"),
    ("Volume Limited Trades", "int", "volume_limited_trades"),
]


def _risk_exit_comparison(
    rows: List[Dict[str, Any]],
    strict_reports: Dict[str, Dict[str, Any]],
    best: Dict[str, Any],
) -> Dict[str, Any]:
    base_scenario = str(best.get("base_scenario") or best.get("scenario") or "")
    off = _find_risk_exit_row(rows, base_scenario, "baseline_no_risk_exit")
    on = _find_risk_exit_row(rows, base_scenario, "risk_exit_enabled")
    if not off or not on:
        return {}
    before = _comparison_snapshot(off, strict_reports.get(str(off["scenario"])) or {})
    after = _comparison_snapshot(on, strict_reports.get(str(on["scenario"])) or {})
    return {
        "strategy_id": STRATEGY_ID,
        "base_scenario": base_scenario,
        "baseline_no_risk_exit": before,
        "risk_exit_enabled": after,
        "deltas": {
            key: None if before.get(key) is None or after.get(key) is None else after[key] - before[key]
            for _, _, key in COMPARISON_METRICS
        },
        "exit_triggers": after.get("exit_triggers") or {},
        "interpretation": (
            "This is a same-sample strict Backtester comparison. Improvement from risk_exit_enabled must still be confirmed by walk-forward or holdout validation."
        ),
    }


def _find_risk_exit_row(rows: List[Dict[str, Any]], base_scenario: str, label: str) -> Dict[str, Any] | None:
    for row in rows:
        if row.get("base_scenario") == base_scenario and row.get("risk_exit_label") == label:
            return row
    return None


def _comparison_snapshot(row: Dict[str, Any], strict_report: Dict[str, Any]) -> Dict[str, Any]:
    metrics = strict_report.get("metrics") or {}
    diagnostics = strict_report.get("diagnostics") or {}
    capacity = strict_report.get("capacity") or {}
    guard = strict_report.get("guard_diagnostics") or {}
    return {
        "scenario": row.get("scenario"),
        "risk_exit_label": row.get("risk_exit_label"),
        "cagr": metrics.get("cagr"),
        "total_return": metrics.get("total_return"),
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "sharpe": metrics.get("sharpe"),
        "sortino": metrics.get("sortino"),
        "calmar_ratio": metrics.get("calmar_ratio"),
        "win_rate": metrics.get("win_rate"),
        "profit_factor": metrics.get("profit_factor"),
        "total_trades": metrics.get("total_trades"),
        "round_trip_trades": metrics.get("round_trip_trades"),
        "avg_trade_duration_days": metrics.get("avg_trade_duration_days"),
        "total_commission": diagnostics.get("total_commission"),
        "cost_drag_pct": diagnostics.get("cost_drag_pct"),
        "max_adv_participation": capacity.get("max_adv_participation"),
        "p95_adv_participation": capacity.get("p95_adv_participation"),
        "volume_limited_trades": diagnostics.get("volume_limited_trades"),
        "exit_triggers": dict(guard.get("exit_triggers") or {}),
    }


def _insert_risk_exit_comparison(html: str, comparison: Dict[str, Any], stage_only: bool = False) -> str:
    if not comparison:
        return html
    if stage_only:
        panel = (
            '<section class="panel">\n'
            "<h2>2. Risk Exit Off/On Comparison</h2>\n"
            "<p>本阶段报告已同步生成止盈止损/风险退出开关对照；完整表格见 full_research_report.html，机器可读版本见 risk_exit_comparison.json。</p>\n"
            "</section>\n"
        )
    else:
        before = comparison.get("baseline_no_risk_exit") or {}
        after = comparison.get("risk_exit_enabled") or {}
        body = "".join(
            "<tr>"
            f"<td>{escape(name)}</td>"
            f"<td>{_fmt_compare(before.get(key), kind)}</td>"
            f"<td>{_fmt_compare(after.get(key), kind)}</td>"
            f"<td>{_fmt_delta(before.get(key), after.get(key), kind)}</td>"
            "</tr>"
            for name, kind, key in COMPARISON_METRICS
        )
        triggers = comparison.get("exit_triggers") or {}
        trigger_text = ", ".join(f"{escape(str(key))}: {int(value)}" for key, value in sorted(triggers.items())) or "n/a"
        panel = (
            '<section class="panel">\n'
            "<h2>2. Risk Exit Off/On Comparison</h2>\n"
            "<p>同一 base scenario、同一数据、同一 universe、同一成本模型和同一执行口径下，对比 risk_exit.enabled=false 与 risk_exit.enabled=true。</p>\n"
            '<div class="table-wrap"><table><thead><tr><th>Metric</th><th>baseline_no_risk_exit</th><th>risk_exit_enabled</th><th>变化</th></tr></thead>'
            f"<tbody>{body}</tbody></table></div>\n"
            f"<p>risk_exit_enabled 触发统计：{trigger_text}。该表是同样本 strict 回测对照，改善仍需 walk-forward 或留出样本确认。</p>\n"
            "</section>\n"
        )
    for marker in (
        '<section class="panel">\n<h2>2. Metric Checklist</h2>',
        '<section class="panel">\n<h2>3. 报告导航</h2>',
    ):
        if marker in html:
            return html.replace(marker, panel + marker, 1)
    return html.replace("</body>", panel + "</body>", 1)


def _fmt_compare(value: Any, kind: str) -> str:
    if value is None:
        return "n/a"
    number = float(value)
    if kind == "pct":
        return f"{number:.2%}"
    if kind == "pct_points":
        return f"{number:.2f}%"
    if kind == "int":
        return f"{int(round(number)):,}"
    if kind == "money":
        return f"{number:,.2f}"
    return f"{number:.2f}"


def _fmt_delta(before: Any, after: Any, kind: str) -> str:
    if before is None or after is None:
        return "n/a"
    delta = float(after) - float(before)
    if kind == "pct":
        return f"{delta:+.2%}"
    if kind == "pct_points":
        return f"{delta:+.2f} pct pts"
    if kind == "int":
        return f"{int(round(delta)):+,}"
    if kind == "money":
        return f"{delta:+,.2f}"
    return f"{delta:+.2f}"


def _insert_detail_section(html: str, rows: List[Dict[str, Any]], universe: Dict[str, Any]) -> str:
    grid_rows = "\n".join(
        "<tr>"
        f"<td>{row['scenario']}</td>"
        f"<td>{float(row.get('cagr') or 0.0):.2%}</td>"
        f"<td>{float(row.get('max_drawdown_pct') or 0.0):.2%}</td>"
        f"<td>{float(row.get('sharpe') or 0.0):.2f}</td>"
        f"<td>{int(row.get('total_trades') or 0)}</td>"
        f"<td>{'通过' if row['meets_goal'] else '未通过'}</td>"
        "</tr>"
        for row in rows
    )
    grid = (
        "<h3>场景结果</h3><div class=\"table-wrap\"><table>"
        "<thead><tr><th>场景</th><th>CAGR</th><th>MaxDD</th><th>Sharpe</th><th>Trades</th><th>目标</th></tr></thead>"
        f"<tbody>{grid_rows}</tbody></table></div>"
    )
    grid = f"{_pit_universe_table(universe)}{grid}"
    marker = "<h2>2. 严格回测证据</h2>\n<h3>回测 Equity Curve</h3>"
    replacement = f"<h2>2. 严格回测证据</h2>\n{DETAIL_SECTION}{grid}<h3>回测 Equity Curve</h3>"
    return html.replace(marker, replacement, 1)


def _pit_universe_table(universe: Dict[str, Any]) -> str:
    category_maps = {
        **(universe.get("risk_category_symbols") or {}),
        **(universe.get("defensive_category_symbols") or {}),
    }
    rows = []
    for category, symbols in category_maps.items():
        sample = ", ".join(str(symbol) for symbol in list(symbols)[:8])
        rows.append(
            "<tr>"
            f"<td>{escape(str(category))}</td>"
            f"<td>{len(symbols)}</td>"
            f"<td>{escape(sample)}</td>"
            "<td>只允许用户审计注册的稳定代表 ETF；调仓日缺 bar/NAV/规模/流动性/lookback 不入选。</td>"
            "</tr>"
        )
    counts = universe.get("registered_universe_counts") or {}
    rows.append(
        "<tr>"
        "<td>registry_quality</td>"
        f"<td>{int(counts.get('active_symbol_count') or 0)} active / {int(counts.get('registered_symbol_count') or 0)} registered</td>"
        f"<td>missing_data={int(counts.get('missing_data_count') or 0)}</td>"
        "<td>候选类别来自 audited_stable_etf_registry；新增 ETF 类别必须经人工审计后注册。</td>"
        "</tr>"
    )
    return (
        "<h3>Audited Stable ETF Universe</h3><div class=\"table-wrap\"><table>"
        "<thead><tr><th>类别</th><th>候选数</th><th>样例</th><th>选择规则</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


if __name__ == "__main__":
    main()
