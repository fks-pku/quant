"""Run full research report for the A-share CSI300 index-enhanced multifactor candidate."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
from typing import Any, Dict, List

_project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_project_root))

import quant.scripts.run_ashare_dividend_low_vol_quality_full_research as base
from quant.features.strategies.reject.ashare_csi300_index_enhanced_multifactor.strategy import (
    AShareCsi300IndexEnhancedMultifactorStrategy,
)


STRATEGY_ID = "ashare_csi300_index_enhanced_multifactor"
TITLE = "A股沪深300指数增强多因子"
INITIAL_CASH = 10_000.0
SOURCE_URLS = [
    "https://www.fxbaogao.com/detail/5056710",
    "https://mf.bigquant.com/square/paper/d45e2e4b-153e-4405-a527-a12f1cd7db2b",
    "https://bigquant.com/square/paper/5d96f709-e221-4f8d-90f2-7fdcd7bed8b7",
]
STRATEGY_PARAMS: Dict[str, Any] = {
    "holding_days": 20,
    "max_positions": 40,
    "target_weight_slots": 40,
    "max_position_pct": 0.95,
    "cap_percentile_low": 0.60,
    "cap_percentile_high": 1.00,
    "min_price": 5.0,
    "min_turnover": 200_000.0,
    "use_market_timing": False,
    "symbol_trend_ma": 0,
    "min_long_momentum": -0.40,
    "min_recent_momentum": -0.30,
    "max_volatility": 1.20,
    "min_drawdown": -0.60,
    "max_pb": 30.0,
    "max_ps_ttm": 50.0,
    "min_roe": 0.0,
    "max_debt_to_assets": 0.0,
    "min_dividend_yield": 0.0,
    "score_profile": "csi300_index_enhanced_multifactor",
    "max_replacements_per_rebalance": 10,
}


DETAIL_SECTION = """
<h3>策略执行流程</h3>
<div class="table-wrap"><table><thead><tr><th>步骤</th><th>执行逻辑</th><th>信息边界</th></tr></thead><tbody>
<tr><td>1. Universe</td><td>使用历史每日 total_mv 前 800 名并集作为沪深300增强代理池，再按当日 point-in-time 市值分位取偏大盘区间。</td><td>不使用当前指数成分名单回溯；未来新上市股票只有在有当日 bar、状态和 PIT 字段后才可能入选。</td></tr>
<tr><td>2. 基础过滤</td><td>过滤 ST、停牌、非上市、不可交易、低价、低成交额，以及 PB/PE/PS 极端或缺失标的。</td><td>状态和估值字段均来自当日可得数据。</td></tr>
<tr><td>3. 多因子打分</td><td>综合一年动量、近 60 日动量、ROE、低波动、低 PB、低 PE、低换手和股息率。</td><td>价格信号使用当日及以前后复权价格；财务字段按 ann_date point-in-time asof join。</td></tr>
<tr><td>4. 组合构造</td><td>每 20 个交易日最多持有 40 只，目标总仓位 95%，单次调仓最多替换 10 只。</td><td>这是宽篮子指数增强代理，不是集中选股策略；默认 100 万资金避免 100 股手数导致长期空仓。</td></tr>
<tr><td>5. 严格执行</td><td>信号收盘生成，订单 T+1 开盘执行；回测包含 100 股手数、涨跌停、停牌、佣金税费和 2% ADV 冲击约束。</td><td>行业中性和组合优化器本版未实现，作为残余风险披露。</td></tr>
</tbody></table></div>
"""


def _configure_base_runner() -> None:
    base.STRATEGY_ID = STRATEGY_ID
    base.TITLE = TITLE
    base.INITIAL_CASH = INITIAL_CASH
    base.SOURCE_URLS = list(SOURCE_URLS)
    base.STRATEGY_PARAMS = dict(STRATEGY_PARAMS)
    base.DETAIL_SECTION = DETAIL_SECTION
    base.AShareDividendLowVolQualityEnhancedStrategy = AShareCsi300IndexEnhancedMultifactorStrategy
    base._hypothesis_row = _hypothesis_row
    base._stage_conclusions = _stage_conclusions
    base._strategy_logic = _strategy_logic
    base._parameter_explanations = _parameter_explanations
    base._parameter_sensitivity = _parameter_sensitivity


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
    status = base._row_status(strict_report, walkforward)
    return {
        "strategy_id": STRATEGY_ID,
        "title": TITLE,
        "thesis": "在沪深300代理大盘池内，用成长/动量、盈利质量、估值、波动和流动性多因子做宽篮子超额收益增强。",
        "status": status,
        "stage": "full_research",
        "source": "broker_report_review",
        "source_url": SOURCE_URLS[0],
        "decision_reason": base._decision_reason(strict_report, walkforward),
        "metrics": metrics,
        "evidence": {
            "source": "broker financial engineering CSI300 index enhancement and multifactor summaries",
            "source_urls": SOURCE_URLS,
            "local_strategy": True,
            "discovery_quality": {
                "score": 0.78,
                "source_type": "broker_financial_engineering_report",
                "matched_terms": ["沪深300增强", "指数增强", "多因子", "动量", "成长", "盈利", "估值"],
                "risk_flags": ["proxy_universe_not_official_constituents", "industry_neutral_optimizer_not_implemented"],
            },
            "strategy_spec": {
                "strategy_id": STRATEGY_ID,
                "strategy_type": "a_share_csi300_proxy_index_enhancement_multifactor",
                "signal_formula_key": STRATEGY_ID,
                "prediction_direction": "higher_is_better",
                "symbols_count": len(symbols),
                "universe": symbols,
                "universe_source": "historical daily top total_mv union with daily PIT cap-band filtering",
                "lookback_days": 252,
                "horizon_days": int(STRATEGY_PARAMS["holding_days"]),
                "execution_lag_days": 1,
                "rebalance_frequency": f"every {STRATEGY_PARAMS['holding_days']} trading days",
                "required_fields": AShareCsi300IndexEnhancedMultifactorStrategy(symbols=[]).required_fields,
                "parameters": STRATEGY_PARAMS,
                "parameter_explanations": _parameter_explanations(),
                "strategy_logic": _strategy_logic(symbols, start, end),
                "source_report_urls": SOURCE_URLS,
                "universe_start": start.date().isoformat(),
                "universe_end": end.date().isoformat(),
                "goal": {"cagr_gt": 0.10, "max_drawdown_gte": -0.30, "max_adv_participation_lte": 0.05},
            },
        },
    }


def _stage_conclusions(strict_report: Dict[str, Any], walkforward: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    metrics = strict_report.get("metrics") or {}
    cagr = float(metrics.get("cagr") or 0.0)
    sharpe = float(metrics.get("sharpe") or 0.0)
    max_dd = float(metrics.get("max_drawdown_pct") or 0.0)
    return {
        "fast_research": {
            "label": "快研究",
            "verdict": "not_run",
            "conclusion": "本轮未单独运行全量 Rank IC；直接验证透明多因子组合在严格 Backtester 下的可交易结果。",
            "method": "策略源自沪深300指数增强和多因子选股金工研究方向，使用本地可得 PIT 字段实现透明版本。",
        },
        "strict_backtest": {
            "label": "严格回测",
            "verdict": "pass" if base._strict_pass(strict_report) else ("warn" if cagr > 0 else "fail"),
            "conclusion": f"严格回测：Sharpe={sharpe:.2f}，CAGR={cagr:.2%}，MaxDD={max_dd:.2%}。",
            "method": "项目 Backtester；T+1、涨跌停、停牌、100 股手数、真实佣金税费、5bps 最小滑点和 cn_daily_liquidity_impact。",
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
        "core_idea": "在沪深300代理大盘池内用透明多因子排序获取相对指数的选股增强，保持宽持仓和月度低换手。",
        "universe": f"历史每日 total_mv 前 800 名并集，回测窗口 {start.date()} 到 {end.date()}，实际取数 {len(symbols)} 个 symbol。",
        "entry_filters": [
            "daily total_mv 位于候选池 60%-100% 分位",
            "price >= 5 and average turnover >= 200000",
            "PB/PE_TTM/PS_TTM 为正且不超过宽松上限",
            "ST/suspended/non-listed/tradable=false rejected",
        ],
        "ranking_rule": "score = 22% 一年动量 + 12% 近 60 日动量 + 16% ROE + 14% 低波 + 12% 低 PB + 10% 低 PE + 8% 低换手 + 6% 股息率。",
        "portfolio_construction": "每次调仓最多 40 只，目标总仓位 95%，按 40 个目标槽位等权分配，单次最多替换 10 只。",
        "rebalance_rule": "每 20 个交易日收盘后重算候选，下一交易日开盘执行。",
        "exit_rule": "持仓触发 ST、停牌、非上市、不可交易、低价或跌出目标篮子时卖出；无指数择时，保持增强组合持续暴露。",
        "risk_budget": "A 股 long-only，沪深300为基准，T+1，2% ADV 最大参与率，真实佣金税费与冲击成本；本版未做行业中性优化。",
        "parameter_explanations": _parameter_explanations(),
    }


def _parameter_explanations() -> Dict[str, str]:
    return {
        "holding_days": "20 个交易日近似月度调仓，匹配指数增强常见低到中等换手节奏。",
        "max_positions": "宽篮子股票数量，降低个股噪声并更接近指数增强而非集中选股。",
        "target_weight_slots": "用固定 40 个槽位控制单票目标权重，避免信号稀疏时仓位过度集中。",
        "cap_percentile_low": "在历史大盘代理池内保留当日偏大市值区间，降低中小盘漂移。",
        "min_turnover": "成交额下限，避免低流动性标的进入指数增强篮子。",
        "max_replacements_per_rebalance": "单次调仓替换上限，降低换手和交易成本。",
        "max_position_pct": "组合目标总仓位，保留少量现金缓冲。",
        "cap_percentile_high": "在历史大盘代理池内保留的最高市值分位，1.0 表示不剔除最顶部大市值股票。",
        "min_price": "买入价格下限，过滤低价和潜在退市风险较高标的。",
        "use_market_timing": "指数增强默认不做指数择时，保持相对基准的持续权益暴露。",
        "symbol_trend_ma": "个股趋势过滤窗口；0 表示关闭，避免指数增强组合过度空仓。",
        "min_long_momentum": "一年动量下限，仅剔除极弱趋势标的。",
        "min_recent_momentum": "近 60 日动量下限，仅剔除短期大幅走弱标的。",
        "max_volatility": "年化波动率上限，极端高波动标的不进入候选。",
        "min_drawdown": "近 120 日最大回撤下限，极端深回撤标的不进入候选。",
        "max_pb": "PB 宽松上限，用于剔除极端估值异常。",
        "max_ps_ttm": "PS_TTM 宽松上限，用于剔除极端估值异常。",
        "min_roe": "ROE 硬门槛；0 表示不做硬过滤，只在排序中偏好高 ROE。",
        "max_debt_to_assets": "资产负债率硬门槛；0 表示不使用该字段过滤。",
        "min_dividend_yield": "股息率硬门槛；0 表示不做硬过滤，只在排序中给予高股息小权重。",
        "score_profile": "策略打分画像，用于报告和诊断追踪当前多因子权重方案。",
    }


def _parameter_sensitivity(strict_report: Dict[str, Any]) -> Dict[str, Any]:
    metrics = strict_report.get("metrics") or {}
    return {
        "status": "single_frozen_parameter_set",
        "method": "Transparent CSI300 proxy multifactor implementation with one frozen parameter set; no optimization grid was used.",
        "base_params": STRATEGY_PARAMS,
        "selected_params": STRATEGY_PARAMS,
        "best_params": STRATEGY_PARAMS,
        "tested_count": 1,
        "pass_count": 1 if base._strict_pass(strict_report) else 0,
        "max_degradation_pct": 0.0,
        "rows": [
            {
                "name": "base_csi300_index_enhanced_multifactor",
                "parameters": STRATEGY_PARAMS,
                "cagr": metrics.get("cagr"),
                "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                "sharpe": metrics.get("sharpe"),
                "verdict": "pass" if base._strict_pass(strict_report) else "warn",
            }
        ],
    }


if __name__ == "__main__":
    _configure_base_runner()
    base.main()
