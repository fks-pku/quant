"""Run strict backtests for the guarded A-share small-cap baseline."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

_project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_project_root))

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
from quant.features.strategies.ashare_small_cap_pure_baseline.strategy import (
    AShareSmallCapPureBaselineStrategy,
)
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.trading.sub_portfolio import SubPortfolio
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
from quant.infrastructure.research.reporting import build_research_stage_report_html


START = datetime(2016, 1, 1)
END = datetime(2025, 12, 31)
INITIAL_CASH = 500000.0
STRATEGY_ID = "ashare_small_cap_pure_baseline"
TITLE = "A-share small-cap guarded baseline"
FORMULA_KEY = "ashare_small_cap_guarded_size_factor"
REPORT_ROOT = Path("quant/infrastructure/var/research/reports")
TIMING_SYMBOL = "000300"
DEFAULT_TARGET_CAGR = 0.10
DEFAULT_TARGET_MAX_DRAWDOWN = -0.30
COMMISSION_CFG = {
    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
    "HK": {"type": "hk_realistic"},
    "CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0},
}


SCENARIOS: List[Dict[str, Any]] = [
    {
        "name": "baseline_legacy_full_exposure",
        "max_positions": 20,
        "rebalance_interval": 10,
        "min_price": 5.0,
        "min_adv_value": 20000.0,
        "target_exposure": 1.0,
        "market_timing_symbol": "",
        "market_trend_window": 0,
        "market_momentum_lookback": 0,
        "market_momentum_threshold": 0.0,
        "market_risk_off_exposure": 0.0,
        "stock_trend_window": 0,
    },
    {
        "name": "pure_exposure_60pct_20pos_liq20k",
        "max_positions": 20,
        "rebalance_interval": 10,
        "min_price": 5.0,
        "min_adv_value": 20000.0,
        "target_exposure": 0.6,
        "market_timing_symbol": "",
        "market_trend_window": 0,
        "market_momentum_lookback": 0,
        "market_momentum_threshold": 0.0,
        "market_risk_off_exposure": 0.0,
        "stock_trend_window": 0,
    },
    {
        "name": "pure_exposure_55pct_20pos_liq20k",
        "max_positions": 20,
        "rebalance_interval": 10,
        "min_price": 5.0,
        "min_adv_value": 20000.0,
        "target_exposure": 0.55,
        "market_timing_symbol": "",
        "market_trend_window": 0,
        "market_momentum_lookback": 0,
        "market_momentum_threshold": 0.0,
        "market_risk_off_exposure": 0.0,
        "stock_trend_window": 0,
    },
    {
        "name": "pure_exposure_52pct_20pos_liq20k",
        "max_positions": 20,
        "rebalance_interval": 10,
        "min_price": 5.0,
        "min_adv_value": 20000.0,
        "target_exposure": 0.52,
        "market_timing_symbol": "",
        "market_trend_window": 0,
        "market_momentum_lookback": 0,
        "market_momentum_threshold": 0.0,
        "market_risk_off_exposure": 0.0,
        "stock_trend_window": 0,
    },
    {
        "name": "pure_exposure_50_75pct_20pos_liq20k",
        "max_positions": 20,
        "rebalance_interval": 10,
        "min_price": 5.0,
        "min_adv_value": 20000.0,
        "target_exposure": 0.5075,
        "market_timing_symbol": "",
        "market_trend_window": 0,
        "market_momentum_lookback": 0,
        "market_momentum_threshold": 0.0,
        "market_risk_off_exposure": 0.0,
        "stock_trend_window": 0,
    },
    {
        "name": "pure_exposure_50_5pct_20pos_liq20k",
        "max_positions": 20,
        "rebalance_interval": 10,
        "min_price": 5.0,
        "min_adv_value": 20000.0,
        "target_exposure": 0.505,
        "market_timing_symbol": "",
        "market_trend_window": 0,
        "market_momentum_lookback": 0,
        "market_momentum_threshold": 0.0,
        "market_risk_off_exposure": 0.0,
        "stock_trend_window": 0,
    },
    {
        "name": "pure_exposure_50pct_20pos_liq20k",
        "max_positions": 20,
        "rebalance_interval": 10,
        "min_price": 5.0,
        "min_adv_value": 20000.0,
        "target_exposure": 0.5,
        "market_timing_symbol": "",
        "market_trend_window": 0,
        "market_momentum_lookback": 0,
        "market_momentum_threshold": 0.0,
        "market_risk_off_exposure": 0.0,
        "stock_trend_window": 0,
    },
    {
        "name": "quality_soft_pb12_ps20_turnover35_volume5_55pct",
        "max_positions": 20,
        "rebalance_interval": 10,
        "min_price": 5.0,
        "min_adv_value": 20000.0,
        "target_exposure": 0.55,
        "market_timing_symbol": "",
        "market_trend_window": 0,
        "market_momentum_lookback": 0,
        "market_momentum_threshold": 0.0,
        "market_risk_off_exposure": 0.0,
        "stock_trend_window": 0,
        "max_pb": 12.0,
        "max_ps_ttm": 20.0,
        "max_pe_ttm": 0.0,
        "max_turnover_rate_f": 35.0,
        "max_volume_ratio": 5.0,
        "require_positive_pe": False,
        "require_quality_fields": True,
    },
    {
        "name": "quality_soft_pb12_ps20_turnover35_volume5_60pct",
        "max_positions": 20,
        "rebalance_interval": 10,
        "min_price": 5.0,
        "min_adv_value": 20000.0,
        "target_exposure": 0.6,
        "market_timing_symbol": "",
        "market_trend_window": 0,
        "market_momentum_lookback": 0,
        "market_momentum_threshold": 0.0,
        "market_risk_off_exposure": 0.0,
        "stock_trend_window": 0,
        "max_pb": 12.0,
        "max_ps_ttm": 20.0,
        "max_pe_ttm": 0.0,
        "max_turnover_rate_f": 35.0,
        "max_volume_ratio": 5.0,
        "require_positive_pe": False,
        "require_quality_fields": True,
    },
    {
        "name": "quality_core_pb8_ps10_pe120_turnover30_volume4_60pct",
        "max_positions": 20,
        "rebalance_interval": 10,
        "min_price": 5.0,
        "min_adv_value": 20000.0,
        "target_exposure": 0.6,
        "market_timing_symbol": "",
        "market_trend_window": 0,
        "market_momentum_lookback": 0,
        "market_momentum_threshold": 0.0,
        "market_risk_off_exposure": 0.0,
        "stock_trend_window": 0,
        "max_pb": 8.0,
        "max_ps_ttm": 10.0,
        "max_pe_ttm": 120.0,
        "max_turnover_rate_f": 30.0,
        "max_volume_ratio": 4.0,
        "require_positive_pe": True,
        "require_quality_fields": True,
    },
    {
        "name": "quality_core_pb8_ps10_pe120_turnover30_volume4_65pct",
        "max_positions": 20,
        "rebalance_interval": 10,
        "min_price": 5.0,
        "min_adv_value": 20000.0,
        "target_exposure": 0.65,
        "market_timing_symbol": "",
        "market_trend_window": 0,
        "market_momentum_lookback": 0,
        "market_momentum_threshold": 0.0,
        "market_risk_off_exposure": 0.0,
        "stock_trend_window": 0,
        "max_pb": 8.0,
        "max_ps_ttm": 10.0,
        "max_pe_ttm": 120.0,
        "max_turnover_rate_f": 30.0,
        "max_volume_ratio": 4.0,
        "require_positive_pe": True,
        "require_quality_fields": True,
    },
    {
        "name": "quality_tight_pb6_ps8_pe80_turnover25_volume3_65pct",
        "max_positions": 20,
        "rebalance_interval": 10,
        "min_price": 5.0,
        "min_adv_value": 20000.0,
        "target_exposure": 0.65,
        "market_timing_symbol": "",
        "market_trend_window": 0,
        "market_momentum_lookback": 0,
        "market_momentum_threshold": 0.0,
        "market_risk_off_exposure": 0.0,
        "stock_trend_window": 0,
        "max_pb": 6.0,
        "max_ps_ttm": 8.0,
        "max_pe_ttm": 80.0,
        "max_turnover_rate_f": 25.0,
        "max_volume_ratio": 3.0,
        "require_positive_pe": True,
        "require_quality_fields": True,
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
            "meets_goal": _meets_goal(metrics, target_cagr, target_max_drawdown),
        }
        rows.append(row)
        print(json.dumps(_compact_row(row), ensure_ascii=False), flush=True)

    best = _select_best(rows, target_cagr, target_max_drawdown)
    report_path, result_path = _write_outputs(rows, strict_reports, best, target_cagr, target_max_drawdown)
    print(json.dumps({"strategy_id": STRATEGY_ID, "best": _compact_row(best), "report_path": str(report_path), "result_path": str(result_path)}, ensure_ascii=False, indent=2))


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
        survivorship_audit = _cn_survivorship_audit(db_provider, START, END, formula_key=FORMULA_KEY)
    finally:
        db_provider.disconnect()
    return stock_symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit


def _scenario_symbols(stock_symbols: List[str], scenario: Dict[str, Any]) -> List[str]:
    return list(dict.fromkeys([*stock_symbols, *_scenario_extras(scenario)]))


def _scenario_extras(scenario: Dict[str, Any]) -> List[str]:
    extras = []
    timing_symbol = str(scenario.get("market_timing_symbol") or "")
    if timing_symbol:
        extras.append(timing_symbol)
    extras.extend(str(symbol) for symbol in scenario.get("broad_index_symbols") or [])
    return extras


def _run_one(
    scenario: Dict[str, Any],
    stock_symbols: List[str],
    all_symbols: List[str],
    lot_sizes: Dict[str, int],
    benchmark_provider: BenchmarkProvider,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
) -> Dict[str, Any]:
    execution_cost_model = _strict_execution_cost_model(
        STRATEGY_ID,
        {"name": TITLE, "description": "small_cap market_cap guarded size rotation", "research_meta": {"strategy_spec": _strategy_spec(scenario, len(stock_symbols))}},
        True,
    )
    data_provider = _DuckDBDailyDateProvider(
        all_symbols,
        START,
        END,
        include_daily_basic=True,
        include_execution_liquidity_features=True,
        cache_enabled=True,
    )
    strategy = AShareSmallCapPureBaselineStrategy(symbols=all_symbols, **_strategy_kwargs(scenario))
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
            symbols=all_symbols,
        )
    finally:
        data_provider.close()

    benchmark_equity_curve = benchmark_provider.get_benchmark_equity(START, END, INITIAL_CASH) if benchmark_provider else None
    return _strict_backtest_report(
        bt_result,
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


def _quality_controls_enabled(scenario: Dict[str, Any]) -> bool:
    return any(
        float(scenario.get(key) or 0.0) > 0
        for key in ("max_pb", "max_ps_ttm", "max_pe_ttm", "max_turnover_rate_f", "max_volume_ratio")
    ) or bool(scenario.get("require_positive_pe"))


def _quality_required_fields(scenario: Dict[str, Any]) -> List[str]:
    fields = []
    if float(scenario.get("max_pb") or 0.0) > 0:
        fields.append("pb")
    if float(scenario.get("max_ps_ttm") or 0.0) > 0:
        fields.extend(["ps_ttm", "ps"])
    if float(scenario.get("max_pe_ttm") or 0.0) > 0 or bool(scenario.get("require_positive_pe")):
        fields.extend(["pe_ttm", "pe"])
    if float(scenario.get("max_turnover_rate_f") or 0.0) > 0:
        fields.extend(["turnover_rate_f", "turnover_rate"])
    if float(scenario.get("max_volume_ratio") or 0.0) > 0:
        fields.append("volume_ratio")
    return list(dict.fromkeys(fields))


def _quality_entry_filters(scenario: Dict[str, Any]) -> List[str]:
    filters = []
    if float(scenario.get("max_pb") or 0.0) > 0:
        filters.append(f"PB <= {scenario.get('max_pb')}")
    if float(scenario.get("max_ps_ttm") or 0.0) > 0:
        filters.append(f"PS_TTM/PS <= {scenario.get('max_ps_ttm')}")
    if bool(scenario.get("require_positive_pe")):
        filters.append("PE_TTM/PE 必须为正")
    if float(scenario.get("max_pe_ttm") or 0.0) > 0:
        filters.append(f"PE_TTM/PE <= {scenario.get('max_pe_ttm')}")
    if float(scenario.get("max_turnover_rate_f") or 0.0) > 0:
        filters.append(f"自由流通换手率 <= {scenario.get('max_turnover_rate_f')}%")
    if float(scenario.get("max_volume_ratio") or 0.0) > 0:
        filters.append(f"量比 <= {scenario.get('max_volume_ratio')}")
    if bool(scenario.get("require_quality_fields")) and filters:
        filters.append("启用的质量字段缺失时不得入选")
    return filters


def _strategy_spec(scenario: Dict[str, Any], stock_count: int) -> Dict[str, Any]:
    construction_steps = [
        "Exclude ST, suspended, non-tradable, non-listed, non-L status, low-price, low-liquidity and missing-market-cap stocks.",
        "Rank eligible stocks by point-in-time market cap ascending.",
        "Hold the smallest names in fixed target-weight slots and send signals after close for T+1 execution.",
        "Keep unused risk budget in cash; this strict grid does not blend broad-index ETF sleeves.",
    ]
    if str(scenario.get("market_timing_symbol") or ""):
        construction_steps.insert(1, "Optionally use the configured market timing symbol as a broad market risk switch.")
    if int(scenario.get("stock_trend_window") or 0) > 0:
        construction_steps.insert(-2, "Optionally require each candidate to trade above its own moving average.")
    if _quality_controls_enabled(scenario):
        construction_steps.insert(1, "Apply valuation and crowding quality filters before the final small-cap rank.")
    required_fields = [
        "open",
        "high",
        "low",
        "close",
        "adj_close",
        "volume",
        "turnover",
        "total_mv",
        "circ_mv",
        "is_st",
        "tradable",
        "has_daily_bar",
        "is_listed",
        "list_status",
    ]
    required_fields.extend(field for field in _quality_required_fields(scenario) if field not in required_fields)
    entry_filters = [
        "仅保留主板/创业板/科创板 A 股代码",
        "排除 ST、停牌、tradable=false、has_daily_bar=false、is_listed=false、list_status != L",
        f"收盘价 >= {scenario.get('min_price')}",
        f"20 日平均成交额 >= {scenario.get('min_adv_value')}",
        "必须有有效 total_mv 或 circ_mv",
    ]
    entry_filters.extend(_quality_entry_filters(scenario))
    risk_budget_text = "回撤控制主要来自降低总股票敞口、20 只分散持仓、价格/流动性/状态过滤和小市值冲击成本模型；本轮不使用宽基指数融合。"
    core_idea_text = "A 股小市值溢价：在可交易、非风险状态、具备基本流动性的全 A 股池内，优先持有当前可见市值最小的一组股票。"
    ranking_rule_text = "对过滤后的候选股票按 point-in-time 市值升序排列；市值越小，优先级越高。"
    exit_rule_text = "每日先检查已持仓股票是否触发 ST、停牌、退市状态、不可交易、价格低于下限、流动性不足或市值缺失；触发则提交风险退出订单。"
    if _quality_controls_enabled(scenario):
        core_idea_text = "A 股小市值溢价：在可交易、非风险状态、具备基本流动性的全 A 股池内，优先持有当前可见市值较小、且没有明显估值/交易拥挤风险的一组股票。"
        ranking_rule_text = "先做状态、价格、流动性和质量过滤，再按 point-in-time 市值升序排列；市值越小，优先级越高。"
        exit_rule_text = "每日先检查已持仓股票是否触发 ST、停牌、退市状态、不可交易、价格低于下限、流动性不足、市值缺失或启用的质量护栏；触发则提交风险退出订单。"
        risk_budget_text = (
            "回撤控制来自目标总敞口、20 只分散持仓、状态/价格/流动性护栏、"
            "PB/PS/PE/换手/量比质量过滤和小市值冲击成本模型；本轮不使用宽基指数融合。"
        )
    return {
        "strategy_id": STRATEGY_ID,
        "signal_formula_key": FORMULA_KEY,
        "strategy_type": "small_cap_size_rotation",
        "prediction_direction": "lower_market_cap_is_better_after_guards",
        "lookback_days": max(int(scenario.get("market_trend_window") or 0), int(scenario.get("stock_trend_window") or 0)),
        "horizon_days": int(scenario.get("rebalance_interval") or 10),
        "execution_lag_days": 1,
        "rebalance_frequency": f"every_{int(scenario.get('rebalance_interval') or 10)}_trading_days",
        "universe": f"Full A-share stock universe from daily_cn_ochl ({stock_count} symbols)",
        "required_fields": required_fields,
        "construction_steps": construction_steps,
        "strategy_logic": {
            "core_idea": "A 股小市值溢价：在可交易、非风险状态、具备基本流动性的全 A 股池内，优先持有当前可见市值最小的一组股票。",
            "universe": f"daily_cn_ochl 全 A 股个股池，回测期内共 {stock_count} 个代码；不混入宽基 ETF 或指数资产。",
            "entry_filters": [
                "仅保留主板/创业板/科创板 A 股代码",
                "排除 ST、停牌、tradable=false、has_daily_bar=false、is_listed=false、list_status != L",
                f"收盘价 >= {scenario.get('min_price')}",
                f"20 日平均成交额 >= {scenario.get('min_adv_value')}",
                "必须有有效 total_mv 或 circ_mv",
            ],
            "entry_filters": entry_filters,
            "ranking_rule": "对过滤后的候选股票按 point-in-time 市值升序排列；市值越小，优先级越高。",
            "portfolio_construction": f"选择市值最小的 {scenario.get('max_positions')} 只，目标总敞口 {float(scenario.get('target_exposure') or 0.0):.2%}，单只目标权重为总敞口 / 持仓数，未使用资金保留现金。",
            "rebalance_rule": f"每 {scenario.get('rebalance_interval')} 个交易日收盘后重算目标组合；订单进入 deferred queue，下一交易日开盘按 T+1 执行约束尝试成交。",
            "exit_rule": "每日先检查已持仓股票是否触发 ST、停牌、退市状态、不可交易、价格低于下限、流动性不足或市值缺失；触发则提交风险退出订单。",
            "risk_budget": "回撤控制主要来自降低总股票敞口、20 只分散持仓、价格/流动性/状态过滤和小市值冲击成本模型；本轮不使用宽基指数融合。",
            "core_idea": core_idea_text,
            "universe": f"daily_cn_ochl 全 A 股个股池，回测期内共 {stock_count} 个代码；不混入宽基 ETF 或指数资产。",
            "entry_filters": entry_filters,
            "ranking_rule": ranking_rule_text,
            "portfolio_construction": f"选择过滤后市值最小的 {scenario.get('max_positions')} 只，目标总敞口 {float(scenario.get('target_exposure') or 0.0):.2%}，单只目标权重为总敞口 / 持仓数，未使用资金保留现金。",
            "rebalance_rule": f"每 {scenario.get('rebalance_interval')} 个交易日收盘后重算目标组合；订单进入 deferred queue，下一交易日开盘按 T+1 执行约束尝试成交。",
            "exit_rule": exit_rule_text,
            "risk_budget": risk_budget_text,
        },
        "risk_controls": {
            "target_exposure": scenario.get("target_exposure"),
            "market_timing_symbol": scenario.get("market_timing_symbol"),
            "market_trend_window": scenario.get("market_trend_window"),
            "market_momentum_lookback": scenario.get("market_momentum_lookback"),
            "market_risk_off_exposure": scenario.get("market_risk_off_exposure"),
            "stock_trend_window": scenario.get("stock_trend_window"),
            "min_price": scenario.get("min_price"),
            "min_adv_value": scenario.get("min_adv_value"),
            "max_positions": scenario.get("max_positions"),
            "max_pb": scenario.get("max_pb"),
            "max_ps_ttm": scenario.get("max_ps_ttm"),
            "max_pe_ttm": scenario.get("max_pe_ttm"),
            "max_turnover_rate_f": scenario.get("max_turnover_rate_f"),
            "max_volume_ratio": scenario.get("max_volume_ratio"),
            "require_positive_pe": scenario.get("require_positive_pe"),
            "require_quality_fields": scenario.get("require_quality_fields"),
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
    return max(
        candidates,
        key=lambda row: (
            float(row.get("sharpe") or 0.0),
            float(row.get("max_drawdown_pct") or 0.0),
            float(row.get("cagr") or 0.0) / max(abs(float(row.get("max_drawdown_pct") or 0.0)), 1e-9),
            float(row.get("cagr") or 0.0),
        ),
    )


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
        "run_ts": run_ts,
        "period": f"{START.date()}-{END.date()}",
        "initial_cash": INITIAL_CASH,
        "objective": f"Pure small-cap strategy: keep CAGR above {target_cagr:.2%} while limiting maximum drawdown to {abs(target_max_drawdown):.2%}, without broad-index ETF blending.",
        "thresholds": {
            "target_cagr": target_cagr,
            "target_max_drawdown": target_max_drawdown,
        },
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
    row = _hypothesis_row(best, strict_reports[str(best["scenario"])], target_cagr, target_max_drawdown)
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
            "research_stage_conclusions": {
                "strict_backtest": {
                    "label": "Strict backtest",
                    "verdict": verdict,
                    "conclusion": f"Strict backtest: Sharpe={sharpe:.2f}, CAGR={cagr:.2%}, MaxDD={max_dd:.2%}; threshold CAGR>{target_cagr:.2%}, MaxDD>={target_max_drawdown:.2%}.",
                    "method": "Project Backtester with T+1 open execution, CN commission, lot size, status/limit checks, small-cap liquidity impact, and full A-share universe from 2016.",
                }
            },
        },
        "evidence": {"strategy_spec": _strategy_spec(best.get("parameters") or {}, int(best.get("stock_universe_size") or 0))},
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
        f"<td>{json.dumps(row.get('parameters') or {}, ensure_ascii=False)}</td>"
        "</tr>"
        for row in sorted(rows, key=lambda item: _score_row(item, target_cagr, target_max_drawdown), reverse=True)
    )
    grid = (
        '<h3>风控场景比较</h3><div class="table-wrap"><table>'
        "<thead><tr><th>场景</th><th>CAGR</th><th>MaxDD</th><th>Sharpe</th><th>Calmar</th><th>Trades</th><th>参数</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )
    marker = "<h3>回测配置</h3>"
    return html.replace(marker, f"{grid}<h3>回测配置</h3>", 1)


def _score_row(
    row: Dict[str, Any],
    target_cagr: float = DEFAULT_TARGET_CAGR,
    target_max_drawdown: float = DEFAULT_TARGET_MAX_DRAWDOWN,
) -> Tuple[int, float, float, float]:
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
        "meets_goal": row.get("meets_goal"),
    }


if __name__ == "__main__":
    main()
