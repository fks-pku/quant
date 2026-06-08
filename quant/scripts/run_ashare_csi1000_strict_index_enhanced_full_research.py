"""Run full research report for strict CSI1000 internal index enhancement."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

_project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_project_root))

import quant.scripts.run_ashare_csi300_strict_index_enhanced_full_research as template
from quant.domain.models.market import is_cn_symbol
from quant.features.strategies.reject.ashare_csi1000_strict_index_enhanced.strategy import (
    AShareCsi1000StrictIndexEnhancedStrategy,
)


START = datetime(2016, 1, 1)
END = datetime(2026, 5, 31)
STRATEGY_ID = "ashare_csi1000_strict_index_enhanced"
TITLE = "中证1000严格指数增强"
INITIAL_CASH = 10_000.0
INDEX_CODE = "000852.SH"
BENCHMARK_SYMBOL = "000852"
WEIGHT_DB = Path("quant/infrastructure/var/duckdb/live/cn_index_weight.duckdb")
SOURCE_URLS = [
    "https://tushare.pro/document/2?doc_id=96",
    "https://www.csindex.com.cn/#/indices/family/detail?indexCode=000852",
]
STRATEGY_PARAMS: Dict[str, Any] = {
    "benchmark_symbol": BENCHMARK_SYMBOL,
    "holding_days": 20,
    "max_positions": 120,
    "target_exposure": 0.98,
    "active_tilt": 3.40,
    "min_weight_multiplier": 0.0,
    "max_weight_multiplier": 8.00,
    "max_single_weight": 0.055,
    "min_price": 2.0,
    "min_turnover": 30_000.0,
    "max_volatility": 1.50,
    "min_drawdown": -0.70,
    "min_recent_momentum": -0.45,
    "min_long_momentum": -0.55,
    "enable_risk_exit": True,
    "risk_exit": {
        "enabled": True,
        "stop_loss_pct": 0.45,
        "take_profit_pct": 1.20,
        "trailing_stop_pct": 0.35,
    },
}
EXECUTION_COST_MODEL: Dict[str, Any] = {
    **template.base.EXECUTION_COST_MODEL,
    "name": "cn_daily_liquidity_impact_csi1000_strict_index_enhanced",
    "max_participation_rate": 0.02,
    "impact_coefficient": 0.40,
}

DETAIL_SECTION = """
<h3>策略执行流程</h3>
<div class="table-wrap"><table><thead><tr><th>步骤</th><th>执行逻辑</th><th>信息边界</th></tr></thead><tbody>
<tr><td>1. 成分与权重</td><td>只读取 Tushare index_weight 的 000852.SH 历史成分和权重；每个信号日使用不晚于当日的最近一期权重。</td><td>禁止使用市值前 N 名代理池，禁止买入非中证1000历史成分股。</td></tr>
<tr><td>2. 成分内打分</td><td>在当期中证1000成分内部，用 12-1 动量、近端动量、ROE、毛利率、低波动、低 PB、低负债和原始指数权重打分。</td><td>价格信号使用后复权历史，估值和财务字段使用日线 sidecar/PIT asof join。</td></tr>
<tr><td>3. 权重调整</td><td>目标权重从指数权重出发，按 multifactor score 做 bounded active tilt；因子 warmup 不足时给中性分数。</td><td>这是指数内部权重调整，不使用指数外卫星仓、ETF 替代或小盘代理池。</td></tr>
<tr><td>4. 调仓执行</td><td>每 20 个交易日收盘后重算目标权重，下一交易日开盘执行；A 股 T+1、100 股手数、涨跌停、停牌、佣金和冲击成本生效。</td><td>跟踪误差来自成分内主动权重、lot rounding、现金和成交约束。</td></tr>
<tr><td>5. 风险退出</td><td>成分剔除、ST/停牌/不可交易/价格过低每日退出；默认启用 45% 宽止损和 120% 触发的移动止盈。</td><td>止盈止损会增加跟踪偏离，报告中必须作为 residual risk 解释。</td></tr>
</tbody></table></div>
"""


def main() -> None:
    _configure_template()
    template.base.main()


def _configure_template() -> None:
    template.START = START
    template.END = END
    template.STRATEGY_ID = STRATEGY_ID
    template.TITLE = TITLE
    template.INITIAL_CASH = INITIAL_CASH
    template.INDEX_CODE = INDEX_CODE
    template.BENCHMARK_SYMBOL = BENCHMARK_SYMBOL
    template.WEIGHT_DB = WEIGHT_DB
    template.SOURCE_URLS = list(SOURCE_URLS)
    template.STRATEGY_PARAMS = dict(STRATEGY_PARAMS)
    template.EXECUTION_COST_MODEL = dict(EXECUTION_COST_MODEL)
    template.DETAIL_SECTION = DETAIL_SECTION
    template.AShareCsi300StrictIndexEnhancedStrategy = AShareCsi1000StrictIndexEnhancedStrategy
    template._load_inputs = _load_inputs
    template._hypothesis_row = _hypothesis_row
    template._stage_conclusions = _stage_conclusions
    template._strategy_logic = _strategy_logic
    template._parameter_explanations = _parameter_explanations
    template._parameter_sensitivity = _parameter_sensitivity
    template._row_status = _row_status
    template._decision_reason = _decision_reason
    template._strict_pass = _strict_pass
    template.base.START = START
    template.base.END = END
    template._configure_base_runner()


def _load_inputs(
    start: datetime,
    end: datetime,
    historical_rank_limit: int,
    top_market_cap_limit: int,
) -> Tuple[List[str], Dict[str, int], Any, Dict[str, Any], Dict[str, Any]]:
    del historical_rank_limit, top_market_cap_limit
    weight_rows, weight_coverage = template._load_index_weight_rows(start, end)
    if not weight_rows:
        raise RuntimeError(
            "CSI1000 index_weight data missing. Run "
            "python quant/scripts/ingest_tushare_index_weight.py --index-code 000852.SH "
            f"--start {start.date()} --end {end.date()} first."
        )
    weight_symbols = sorted({str(row["symbol"]) for row in weight_rows})
    symbols = list(dict.fromkeys([*weight_symbols, BENCHMARK_SYMBOL]))
    db_provider = template.base.DuckDBProvider()
    db_provider.connect()
    try:
        lot_sizes = template.base._load_lot_sizes(db_provider, symbols, is_cn_symbol)
        benchmark_provider, benchmark_meta = _load_benchmark_provider(db_provider, start, end)
        survivorship_audit = template.base._cn_survivorship_audit(db_provider, start, end, formula_key=STRATEGY_ID)
    finally:
        db_provider.disconnect()
    template._LAST_INDEX_WEIGHT_ROWS = list(weight_rows)
    template._LAST_WEIGHT_COVERAGE = dict(weight_coverage)
    return symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit


def _load_benchmark_provider(db_provider: Any, start: datetime, end: datetime) -> Tuple[Any, Dict[str, Any]]:
    bars = db_provider.get_bars(BENCHMARK_SYMBOL, start, end, "1d")
    if bars.empty:
        return None, {"symbol": "", "coverage_start": "", "coverage_end": "", "rows": 0, "fallback_used": False}
    price_column = "adj_close" if "adj_close" in bars.columns and not bars["adj_close"].isna().all() else "close"
    provider = template.base.BenchmarkProvider(bars, price_column=price_column)
    timestamps = pd.to_datetime(bars["timestamp"], errors="coerce").dropna() if "timestamp" in bars.columns else None
    meta = {
        "symbol": BENCHMARK_SYMBOL,
        "coverage_start": str(timestamps.min().date()) if timestamps is not None and not timestamps.empty else "",
        "coverage_end": str(timestamps.max().date()) if timestamps is not None and not timestamps.empty else "",
        "rows": int(len(bars)),
        "fallback_used": False,
        "price_column": price_column,
    }
    return provider, meta


def _hypothesis_row(
    symbols: List[str],
    strict_report: Dict[str, Any],
    walkforward: Dict[str, Any],
    start: datetime,
    end: datetime,
) -> Dict[str, Any]:
    metrics = {
        "strict_backtest": strict_report,
        "walkforward": walkforward,
        "parameter_sensitivity": _parameter_sensitivity(strict_report),
    }
    metrics["research_stage_conclusions"] = _stage_conclusions(strict_report, walkforward)
    status = _row_status(strict_report, walkforward)
    return {
        "strategy_id": STRATEGY_ID,
        "title": TITLE,
        "thesis": "严格中证1000指数增强必须只在历史成分股内部做权重调整；本策略以 point-in-time index_weight 为基准权重，用多因子分数做有限主动偏离。",
        "status": status,
        "stage": "full_research",
        "source": "tushare_index_weight_and_csi_methodology",
        "source_url": SOURCE_URLS[0],
        "decision_reason": _decision_reason(strict_report, walkforward),
        "metrics": metrics,
        "evidence": {
            "source": "Tushare index_weight 000852.SH historical constituent weights",
            "source_urls": SOURCE_URLS,
            "local_strategy": True,
            "discovery_quality": {
                "score": 0.84,
                "source_type": "official_index_methodology_plus_pit_index_weight_data",
                "matched_terms": ["中证1000", "指数增强", "成分股", "指数权重", "active weight"],
                "risk_flags": [
                    "industry_neutral_optimizer_not_implemented",
                    "chinext_star_permission_needed_for_some_constituents",
                    "small_mid_cap_liquidity_and_capacity_risk",
                    "monthly_index_weight_update_frequency_requires_forward_fill",
                ],
            },
            "strategy_spec": {
                "strategy_id": STRATEGY_ID,
                "strategy_type": "strict_csi1000_internal_index_enhancement",
                "signal_formula_key": STRATEGY_ID,
                "prediction_direction": "higher_is_overweight",
                "symbols_count": len(symbols),
                "universe": symbols,
                "universe_source": "Tushare index_weight 000852.SH point-in-time constituent weights only",
                "index_weight_audit": dict(template._LAST_WEIGHT_COVERAGE),
                "lookback_days": 252,
                "horizon_days": int(STRATEGY_PARAMS["holding_days"]),
                "execution_lag_days": 1,
                "rebalance_frequency": f"every {STRATEGY_PARAMS['holding_days']} trading days",
                "required_fields": AShareCsi1000StrictIndexEnhancedStrategy(symbols=[]).required_fields,
                "parameters": STRATEGY_PARAMS,
                "parameter_explanations": _parameter_explanations(),
                "strategy_logic": _strategy_logic(symbols, start, end),
                "source_report_urls": SOURCE_URLS,
                "universe_start": start.date().isoformat(),
                "universe_end": end.date().isoformat(),
                "goal": {"excess_cagr_gt": 0.02, "tracking_error_lte": 0.14, "max_adv_participation_lte": 0.05},
            },
        },
    }


def _stage_conclusions(strict_report: Dict[str, Any], walkforward: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    metrics = strict_report.get("metrics") or {}
    benchmark = strict_report.get("benchmark") or {}
    cagr = float(metrics.get("cagr") or 0.0)
    sharpe = float(metrics.get("sharpe") or 0.0)
    max_dd = float(metrics.get("max_drawdown_pct") or 0.0)
    excess = cagr - float(benchmark.get("benchmark_cagr") or 0.0)
    tracking_error = float(benchmark.get("tracking_error") or 0.0)
    return {
        "fast_research": {
            "label": "快研究",
            "verdict": "not_run",
            "conclusion": "本轮重点校正 universe 定义，不使用中证1000代理池或小盘卫星仓。",
            "method": "使用 Tushare index_weight 成分权重定义可交易 universe，并在策略报告中审计成分权重覆盖。",
        },
        "strict_backtest": {
            "label": "严格回测",
            "verdict": "pass" if _strict_pass(strict_report) else ("warn" if cagr > 0 else "fail"),
            "conclusion": f"严格回测：Sharpe={sharpe:.2f}，CAGR={cagr:.2%}，年化超额={excess:.2%}，TE={tracking_error:.2%}，MaxDD={max_dd:.2%}。",
            "method": "项目 Backtester；只交易中证1000历史成分，T+1、涨跌停、停牌、100股手数、真实佣金税费、5bps 最小滑点和 2% ADV 冲击约束。",
        },
        "walkforward_strict_audit": {
            "label": "Walk-forward strict audit",
            "verdict": str(walkforward.get("verdict") or "fail"),
            "conclusion": (
                f"冻结参数日历 OOS：aggregate={float(walkforward.get('aggregate_oos_sharpe') or 0.0):.2f}，"
                f"worst={float(walkforward.get('worst_oos_sharpe') or 0.0):.2f}，"
                f"盈利 split={float(walkforward.get('pct_profitable_splits') or 0.0):.0%}。"
            ),
            "method": "从严格回测 equity curve 切分 2018-2019、2020-2021、2022-2023、2024-2025 四个冻结参数 OOS 窗口。",
        },
    }


def _strategy_logic(symbols: List[str], start: datetime, end: datetime) -> Dict[str, Any]:
    return {
        "core_idea": "只在中证1000历史成分内部做权重调整：基准权重来自 index_weight，多因子分数只决定成分内相对超配/低配。",
        "universe": f"Tushare index_weight 000852.SH 覆盖 {template._LAST_WEIGHT_COVERAGE.get('dates', 0)} 个权重日期、{template._LAST_WEIGHT_COVERAGE.get('distinct_symbols', len(symbols))} 个历史成分；回测 {start.date()} 到 {end.date()}。",
        "entry_filters": [
            "symbol must be in latest known CSI1000 index_weight at or before signal date",
            "ST/suspended/non-listed/non-tradable/list_status != L rejected",
            f"price >= 2 and 20d average turnover >= {int(STRATEGY_PARAMS['min_turnover'])}",
            "PB/PE_TTM/PS_TTM/total_mv/circ_mv must be positive",
            "因子缺失时给中性分数，但不允许非成分股进入",
        ],
        "ranking_rule": "score = 28% 12-1 动量 + 18% 近 60 日动量 + 14% ROE + 10% 毛利率 + 12% 低波动 + 8% 低 PB + 6% 低负债 + 4% 原指数权重。",
        "portfolio_construction": "以当期指数权重为 base weight，按 score 做 bounded active tilt，乘数范围 0-8.00，最多 120 只，单票上限 5.5%，目标权益暴露 98%。",
        "rebalance_rule": "每 20 个交易日收盘后重算目标权重，下一交易日开盘执行。",
        "exit_rule": "成分剔除、ST、停牌、非上市、不可交易、低价每日退出；调仓时卖出不在目标权重集合内的成分。",
        "risk_exit": "enabled=true; stop_loss=45%; trailing_take_profit starts after 120% gain and exits after 35% drawdown from peak.",
        "risk_budget": "A 股 long-only stock-level CSI1000 index enhancement; no non-constituent satellite, no ETF proxy, no industry-neutral optimizer in this version.",
        "parameter_explanations": _parameter_explanations(),
    }


def _parameter_explanations() -> Dict[str, str]:
    return {
        "benchmark_symbol": "基准指数；严格指增使用 000852 作为收益和跟踪误差基准。",
        "index_weight_source": "Tushare index_weight 000852.SH，作为 point-in-time 成分和权重来源。",
        "holding_days": "20 个交易日近似月度调仓，降低换手同时保留因子更新。",
        "max_positions": "最多持有中证1000成分股数量；在可交易性和分散度之间折中。",
        "target_exposure": "目标股票权益暴露；保留少量现金应对手数和拒单。",
        "active_tilt": "围绕指数权重的主动偏离强度；越高 alpha/TE 都会升高。",
        "min_weight_multiplier": "低分成分最低保留的指数权重倍数。",
        "max_weight_multiplier": "高分成分最高允许的指数权重倍数。",
        "max_single_weight": "单票目标权重上限，限制集中度和跟踪误差。",
        "min_turnover": "成分内流动性过滤，避免调权落到短期不可成交股票。",
        "risk_exit": "宽止盈止损包，仅作为异常个股风险控制；会带来额外 tracking error。",
    }


def _parameter_sensitivity(strict_report: Dict[str, Any]) -> Dict[str, Any]:
    metrics = strict_report.get("metrics") or {}
    benchmark = strict_report.get("benchmark") or {}
    return {
        "status": "single_frozen_parameter_set",
        "method": "Strict CSI1000 internal constituent-weight strategy; no proxy universe, no satellite sleeve, no parameter grid in final report.",
        "base_params": STRATEGY_PARAMS,
        "selected_params": STRATEGY_PARAMS,
        "best_params": STRATEGY_PARAMS,
        "tested_count": 1,
        "pass_count": 1 if _strict_pass(strict_report) else 0,
        "max_degradation_pct": 0.0,
        "rows": [
            {
                "name": "strict_csi1000_internal_weight_tilt",
                "parameters": STRATEGY_PARAMS,
                "cagr": metrics.get("cagr"),
                "excess_cagr": float(metrics.get("cagr") or 0.0) - float(benchmark.get("benchmark_cagr") or 0.0),
                "tracking_error": benchmark.get("tracking_error"),
                "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                "sharpe": metrics.get("sharpe"),
                "verdict": "pass" if _strict_pass(strict_report) else "warn",
            }
        ],
    }


def _strict_pass(strict_report: Dict[str, Any]) -> bool:
    metrics = strict_report.get("metrics") or {}
    benchmark = strict_report.get("benchmark") or {}
    capacity = strict_report.get("capacity") or {}
    cagr = float(metrics.get("cagr") or 0.0)
    max_dd = float(metrics.get("max_drawdown_pct") or 0.0)
    excess = cagr - float(benchmark.get("benchmark_cagr") or 0.0)
    tracking_error = float(benchmark.get("tracking_error") or 999.0)
    try:
        capacity_ok = float(capacity.get("max_adv_participation")) <= 0.05
    except (TypeError, ValueError):
        capacity_ok = False
    if cagr < 0.05:
        risk_gate = False
    elif cagr < 0.10:
        risk_gate = max_dd >= -0.15
    elif cagr < 0.15:
        risk_gate = max_dd >= -0.25
    elif cagr < 0.20:
        risk_gate = max_dd >= -0.30
    else:
        risk_gate = max_dd >= -0.50
    return (
        risk_gate
        and excess >= 0.02
        and tracking_error <= 0.14
        and int(metrics.get("total_trades") or 0) > 50
        and capacity_ok
    )


def _row_status(strict_report: Dict[str, Any], walkforward: Dict[str, Any]) -> str:
    if _strict_pass(strict_report) and bool(walkforward.get("is_viable")):
        return "needs_fast_validation"
    if _strict_pass(strict_report):
        return "needs_walkforward_validation"
    metrics = strict_report.get("metrics") or {}
    if float(metrics.get("cagr") or 0.0) <= 0 or float(metrics.get("sharpe") or 0.0) <= 0:
        return "rejected"
    return "needs_more_research"


def _decision_reason(strict_report: Dict[str, Any], walkforward: Dict[str, Any]) -> str:
    metrics = strict_report.get("metrics") or {}
    benchmark = strict_report.get("benchmark") or {}
    cagr = float(metrics.get("cagr") or 0.0)
    benchmark_cagr = float(benchmark.get("benchmark_cagr") or 0.0)
    return (
        f"strict CSI1000-internal only; CAGR={cagr:.2%}, "
        f"000852 CAGR={benchmark_cagr:.2%}, excess={cagr - benchmark_cagr:.2%}, "
        f"TE={float(benchmark.get('tracking_error') or 0.0):.2%}, "
        f"beta={float(benchmark.get('beta') or 0.0):.2f}; "
        f"strict pass={_strict_pass(strict_report)}; walkforward viable={bool(walkforward.get('is_viable'))}."
    )


if __name__ == "__main__":
    main()
