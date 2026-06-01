"""Run full research report for strict CSI300 internal index enhancement."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import duckdb

_project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_project_root))

import quant.scripts.run_ashare_dividend_low_vol_quality_full_research as base
from quant.domain.models.market import is_cn_symbol
from quant.features.strategies.reject.ashare_csi300_strict_index_enhanced.strategy import (
    AShareCsi300StrictIndexEnhancedStrategy,
)


STRATEGY_ID = "ashare_csi300_strict_index_enhanced"
TITLE = "沪深300严格指数增强"
INITIAL_CASH = 10_000_000.0
INDEX_CODE = "000300.SH"
BENCHMARK_SYMBOL = "000300"
WEIGHT_DB = Path("quant/infrastructure/var/duckdb/live/cn_index_weight.duckdb")
SOURCE_URLS = [
    "https://tushare.pro/document/2?doc_id=96",
    "https://www.csindex.com.cn/#/indices/family/detail?indexCode=000300",
]
STRATEGY_PARAMS: Dict[str, Any] = {
    "benchmark_symbol": BENCHMARK_SYMBOL,
    "holding_days": 20,
    "max_positions": 90,
    "target_exposure": 0.98,
    "active_tilt": 3.10,
    "min_weight_multiplier": 0.0,
    "max_weight_multiplier": 7.20,
    "max_single_weight": 0.11,
    "min_price": 2.0,
    "min_turnover": 50_000.0,
    "max_volatility": 0.0,
    "min_drawdown": -1.0,
    "min_recent_momentum": -1.0,
    "min_long_momentum": -1.0,
    "enable_risk_exit": True,
    "risk_exit": {
        "enabled": True,
        "stop_loss_pct": 0.40,
        "take_profit_pct": 1.00,
        "trailing_stop_pct": 0.30,
    },
}
EXECUTION_COST_MODEL: Dict[str, Any] = {
    **base.EXECUTION_COST_MODEL,
    "name": "cn_daily_liquidity_impact_csi300_strict_index_enhanced",
    "max_participation_rate": 0.02,
    "impact_coefficient": 0.35,
}
_LAST_INDEX_WEIGHT_ROWS: List[Dict[str, Any]] = []
_LAST_WEIGHT_COVERAGE: Dict[str, Any] = {}

DETAIL_SECTION = """
<h3>策略执行流程</h3>
<div class="table-wrap"><table><thead><tr><th>步骤</th><th>执行逻辑</th><th>信息边界</th></tr></thead><tbody>
<tr><td>1. 成分与权重</td><td>只读取 Tushare index_weight 的 000300.SH 历史成分和权重；每个信号日使用不晚于当日的最近一期权重。</td><td>禁止使用市值前 N 名代理池，禁止买入非沪深300历史成分股。</td></tr>
<tr><td>2. 成分内打分</td><td>在当期沪深300成分内部，用动量、近端动量、ROE、低波、低 PB、股息率和原始指数权重打分。</td><td>价格使用后复权历史，估值和财务字段使用日线 sidecar/PIT asof join。</td></tr>
<tr><td>3. 权重调整</td><td>目标权重从指数权重出发，按 multifactor score 做 bounded active tilt；因子 warmup 不足时给中性分数，不让组合空仓等待信号。</td><td>这是指数内部权重调整，不使用小盘卫星、ETF 替代或指数外 alpha。</td></tr>
<tr><td>4. 调仓执行</td><td>每 20 个交易日收盘后重算目标权重，下一交易日开盘执行；A 股 T+1、100 股手数、涨跌停、停牌、佣金和冲击成本生效。</td><td>跟踪误差来自成分内主动权重、lot rounding、现金和成交约束。</td></tr>
<tr><td>5. 风险退出</td><td>成分剔除、ST/停牌/不可交易/价格过低每日退出；默认启用 40% 宽止损和 100% 触发的移动止盈，只做异常风险控制。</td><td>止盈止损会增加跟踪偏离，报告中必须作为 residual risk 解释。</td></tr>
</tbody></table></div>
"""


def _configure_base_runner() -> None:
    base.STRATEGY_ID = STRATEGY_ID
    base.TITLE = TITLE
    base.INITIAL_CASH = INITIAL_CASH
    base.SOURCE_URLS = list(SOURCE_URLS)
    base.STRATEGY_PARAMS = dict(STRATEGY_PARAMS)
    base.DETAIL_SECTION = DETAIL_SECTION
    base.EXECUTION_COST_MODEL = dict(EXECUTION_COST_MODEL)
    base.AShareDividendLowVolQualityEnhancedStrategy = AShareCsi300StrictIndexEnhancedStrategy
    base._load_inputs = _load_inputs
    base._run_backtest = _run_backtest
    base._hypothesis_row = _hypothesis_row
    base._stage_conclusions = _stage_conclusions
    base._strategy_logic = _strategy_logic
    base._parameter_explanations = _parameter_explanations
    base._parameter_sensitivity = _parameter_sensitivity
    base._row_status = _row_status
    base._decision_reason = _decision_reason
    base._strict_pass = _strict_pass


def _load_inputs(
    start: datetime,
    end: datetime,
    historical_rank_limit: int,
    top_market_cap_limit: int,
) -> Tuple[List[str], Dict[str, int], Any, Dict[str, Any], Dict[str, Any]]:
    del historical_rank_limit, top_market_cap_limit
    global _LAST_INDEX_WEIGHT_ROWS, _LAST_WEIGHT_COVERAGE
    weight_rows, weight_coverage = _load_index_weight_rows(start, end)
    if not weight_rows:
        raise RuntimeError(
            "CSI300 index_weight data missing. Run quant/scripts/ingest_tushare_index_weight.py first."
        )
    weight_symbols = sorted({str(row["symbol"]) for row in weight_rows})
    symbols = list(dict.fromkeys([*weight_symbols, BENCHMARK_SYMBOL]))
    db_provider = base.DuckDBProvider()
    db_provider.connect()
    try:
        lot_sizes = base._load_lot_sizes(db_provider, symbols, is_cn_symbol)
        benchmark_provider, benchmark_meta = base._load_cn_benchmark_provider(db_provider, start, end, base.BenchmarkProvider)
        survivorship_audit = base._cn_survivorship_audit(db_provider, start, end, formula_key=STRATEGY_ID)
    finally:
        db_provider.disconnect()
    _LAST_INDEX_WEIGHT_ROWS = list(weight_rows)
    _LAST_WEIGHT_COVERAGE = dict(weight_coverage)
    return symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit


def _load_index_weight_rows(start: datetime, end: datetime) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not WEIGHT_DB.exists():
        return [], {"rows": 0, "dates": 0, "coverage_start": "", "coverage_end": ""}
    conn = duckdb.connect(str(WEIGHT_DB), read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT trade_date, symbol, ts_code, weight
            FROM cn_index_weight
            WHERE index_code = ?
              AND trade_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
            ORDER BY trade_date, symbol
            """,
            [INDEX_CODE, start, end],
        ).fetchall()
        coverage_row = conn.execute(
            """
            SELECT count(*), count(DISTINCT trade_date), min(trade_date), max(trade_date), count(DISTINCT symbol)
            FROM cn_index_weight
            WHERE index_code = ?
              AND trade_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
            """,
            [INDEX_CODE, start, end],
        ).fetchone()
    finally:
        conn.close()
    weight_rows = [
        {"trade_date": row[0], "symbol": str(row[1]), "ts_code": str(row[2]), "weight": float(row[3])}
        for row in rows
    ]
    coverage = {
        "rows": int(coverage_row[0] or 0),
        "dates": int(coverage_row[1] or 0),
        "coverage_start": str(coverage_row[2]) if coverage_row[2] else "",
        "coverage_end": str(coverage_row[3]) if coverage_row[3] else "",
        "distinct_symbols": int(coverage_row[4] or 0),
        "source": str(WEIGHT_DB),
        "index_code": INDEX_CODE,
    }
    return weight_rows, coverage


def _run_backtest(
    symbols: List[str],
    lot_sizes: Dict[str, int],
    benchmark_provider: Any,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
    start: datetime,
    end: datetime,
) -> Dict[str, Any]:
    data_provider = base._DuckDBDailyDateProvider(
        symbols,
        start,
        end,
        include_daily_basic=True,
        include_financial_indicators=True,
        include_execution_liquidity_features=True,
        cache_enabled=True,
    )
    data_provider._chunk_size = max(252, int(getattr(data_provider, "_chunk_size", 63) or 63))
    strategy = AShareCsi300StrictIndexEnhancedStrategy(
        symbols=symbols,
        index_weights=_LAST_INDEX_WEIGHT_ROWS,
        **STRATEGY_PARAMS,
    )
    backtest_config = {"slippage_bps": 5, "execution_cost_model": dict(EXECUTION_COST_MODEL)}
    bt_config = {
        "backtest": backtest_config,
        "execution": {"commission": base.COMMISSION_CFG},
        "data": {"default_timeframe": "1d"},
        "risk": {"max_position_pct": 1.0, "max_sector_pct": 1.0, "max_daily_loss_pct": 0.10, "max_leverage": 1.0},
    }
    backtester = base.Backtester(
        bt_config,
        portfolio_class=base.Portfolio,
        risk_engine_class=base.RiskEngine,
        sub_portfolio_class=base.SubPortfolio,
        lot_sizes=lot_sizes,
        benchmark_provider=benchmark_provider,
    )
    try:
        bt_result = backtester.run(
            start=start,
            end=end,
            strategies=[strategy],
            initial_cash=INITIAL_CASH,
            data_provider=data_provider,
            symbols=symbols,
        )
    finally:
        data_provider.close()
    benchmark_equity_curve = benchmark_provider.get_benchmark_equity(start, end, INITIAL_CASH) if benchmark_provider else None
    report = base._strict_backtest_report(
        bt_result,
        start,
        end,
        INITIAL_CASH,
        symbols,
        benchmark_meta,
        lot_sizes,
        strategy,
        benchmark_equity_curve,
        survivorship_audit,
        {**backtest_config, "commission": base.COMMISSION_CFG},
    )
    report.setdefault("data_quality", {})["index_weight_audit"] = dict(_LAST_WEIGHT_COVERAGE)
    return report


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
        "thesis": "严格沪深300指数增强必须只在历史成分股内部做权重调整；本策略以 point-in-time index_weight 为基准权重，用多因子分数做有限主动偏离。",
        "status": status,
        "stage": "full_research",
        "source": "tushare_index_weight_and_csi_methodology",
        "source_url": SOURCE_URLS[0],
        "decision_reason": _decision_reason(strict_report, walkforward),
        "metrics": metrics,
        "evidence": {
            "source": "Tushare index_weight 000300.SH historical constituent weights",
            "source_urls": SOURCE_URLS,
            "local_strategy": True,
            "discovery_quality": {
                "score": 0.86,
                "source_type": "official_index_methodology_plus_pit_index_weight_data",
                "matched_terms": ["沪深300", "指数增强", "成分股", "指数权重", "active weight"],
                "risk_flags": [
                    "industry_neutral_optimizer_not_implemented",
                    "stock_account_needs_chinext_or_star_permission_for_some_constituents",
                    "high_active_share_for_an_index_enhancement_strategy",
                    "monthly_index_weight_update_frequency_requires_forward_fill",
                ],
            },
            "strategy_spec": {
                "strategy_id": STRATEGY_ID,
                "strategy_type": "strict_csi300_internal_index_enhancement",
                "signal_formula_key": STRATEGY_ID,
                "prediction_direction": "higher_is_overweight",
                "symbols_count": len(symbols),
                "universe": symbols,
                "universe_source": "Tushare index_weight 000300.SH point-in-time constituent weights only",
                "index_weight_audit": dict(_LAST_WEIGHT_COVERAGE),
                "lookback_days": 252,
                "horizon_days": int(STRATEGY_PARAMS["holding_days"]),
                "execution_lag_days": 1,
                "rebalance_frequency": f"every {STRATEGY_PARAMS['holding_days']} trading days",
                "required_fields": AShareCsi300StrictIndexEnhancedStrategy(symbols=[]).required_fields,
                "parameters": STRATEGY_PARAMS,
                "parameter_explanations": _parameter_explanations(),
                "strategy_logic": _strategy_logic(symbols, start, end),
                "source_report_urls": SOURCE_URLS,
                "universe_start": start.date().isoformat(),
                "universe_end": end.date().isoformat(),
                "goal": {"excess_cagr_gt": 0.02, "tracking_error_lte": 0.10, "max_adv_participation_lte": 0.05},
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
            "conclusion": "本轮重点纠正 universe 定义，不再使用沪深300代理池或小盘卫星。",
            "method": "使用 Tushare index_weight 成分权重定义可交易 universe，并在策略报告中审计成分权重覆盖。",
        },
        "strict_backtest": {
            "label": "严格回测",
            "verdict": "pass" if _strict_pass(strict_report) else ("warn" if cagr > 0 else "fail"),
            "conclusion": (
                f"严格回测：Sharpe={sharpe:.2f}，CAGR={cagr:.2%}，"
                f"年化超额={excess:.2%}，TE={tracking_error:.2%}，MaxDD={max_dd:.2%}。"
            ),
            "method": "项目 Backtester；只交易沪深300历史成分，T+1、涨跌停、停牌、100 股手数、真实佣金税费、5bps 最小滑点和 2% ADV 冲击约束。",
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
        "core_idea": "只在沪深300历史成分内部做权重调整：基准权重来自 index_weight，多因子分数只决定成分内相对超配/低配。",
        "universe": f"Tushare index_weight 000300.SH 覆盖 {_LAST_WEIGHT_COVERAGE.get('dates', 0)} 个权重日期、{_LAST_WEIGHT_COVERAGE.get('distinct_symbols', len(symbols))} 个历史成分；回测 {start.date()} 到 {end.date()}。",
        "entry_filters": [
            "symbol must be in latest known CSI300 index_weight at or before signal date",
            "ST/suspended/non-listed/non-tradable/list_status != L rejected",
            f"price >= 2 and 20d average turnover >= {int(STRATEGY_PARAMS['min_turnover'])}",
            "PB/PE_TTM/PS_TTM/total_mv/circ_mv must be positive",
            "因子缺失时给中性分数，但不允许非成分股进入",
        ],
        "ranking_rule": "score = 34% 12-1 动量 + 20% 近 60 日动量 + 16% ROE + 10% 低波 + 8% 低 PB + 6% 股息率 + 6% 原指数权重。",
        "portfolio_construction": "以当期指数权重为 base weight，按 score 做 bounded active tilt，乘数范围 0-7.20，最多 90 只，单票上限 11%，目标权益暴露 98%。这是高 active-share 指增，不是低偏离全复制。",
        "rebalance_rule": "每 20 个交易日收盘后重算目标权重，下一交易日开盘执行。",
        "exit_rule": "成分剔除、ST、停牌、非上市、不可交易、低价每日退出；调仓时卖出不在目标权重集合内的成分。",
        "risk_exit": "enabled=true; stop_loss=40%; trailing_take_profit starts after 100% gain and exits after 30% drawdown from peak.",
        "risk_budget": "A 股 long-only stock-level CSI300 index enhancement; no non-constituent satellite, no ETF proxy, no industry-neutral optimizer in this version. Active share is intentionally high to pursue the 2% excess target.",
        "parameter_explanations": _parameter_explanations(),
    }


def _parameter_explanations() -> Dict[str, str]:
    return {
        "benchmark_symbol": "基准指数；严格指增使用 000300 作为收益和跟踪误差基准。",
        "index_weight_source": "Tushare index_weight 000300.SH，作为 point-in-time 成分和权重来源。",
        "holding_days": "20 个交易日近似月度调仓，降低换手同时保留因子更新。",
        "max_positions": "最多持有沪深300成分股数量；受 100 股手数和初始资金约束，不强行全 300 复制。",
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
        "method": "Strict CSI300 internal constituent-weight strategy; no proxy universe, no satellite sleeve, no parameter grid in final report.",
        "base_params": STRATEGY_PARAMS,
        "selected_params": STRATEGY_PARAMS,
        "best_params": STRATEGY_PARAMS,
        "tested_count": 1,
        "pass_count": 1 if _strict_pass(strict_report) else 0,
        "max_degradation_pct": 0.0,
        "rows": [
            {
                "name": "strict_csi300_internal_weight_tilt",
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
    excess = cagr - float(benchmark.get("benchmark_cagr") or 0.0)
    tracking_error = float(benchmark.get("tracking_error") or 999.0)
    try:
        capacity_ok = float(capacity.get("max_adv_participation")) <= 0.05
    except (TypeError, ValueError):
        capacity_ok = False
    return excess >= 0.02 and tracking_error <= 0.10 and int(metrics.get("total_trades") or 0) > 50 and capacity_ok


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
        f"strict CSI300-internal only; CAGR={cagr:.2%}, "
        f"000300 CAGR={benchmark_cagr:.2%}, excess={cagr - benchmark_cagr:.2%}, "
        f"TE={float(benchmark.get('tracking_error') or 0.0):.2%}, "
        f"beta={float(benchmark.get('beta') or 0.0):.2f}; "
        f"strict pass={_strict_pass(strict_report)}; walkforward viable={bool(walkforward.get('is_viable'))}."
    )


if __name__ == "__main__":
    _configure_base_runner()
    base.main()
