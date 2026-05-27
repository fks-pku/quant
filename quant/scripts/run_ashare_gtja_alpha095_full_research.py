"""Run full research report for Guotai Junan Alpha191 factor 095."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
from typing import Any, Dict, List

_project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_project_root))

import quant.scripts.run_ashare_dividend_low_vol_quality_full_research as base
from quant.features.strategies.reject.ashare_gtja_alpha095_amount_std.strategy import (
    AShareGtjaAlpha095AmountStdStrategy,
)


STRATEGY_ID = "ashare_gtja_alpha095_amount_std"
TITLE = "国泰君安 Alpha191 因子095: 20日成交额波动"
INITIAL_CASH = 1_000_000.0
SOURCE_URLS = [
    "https://github.com/SelenaMa9812/Guotai-Junan-191-Alpha",
    "https://gist.github.com/kangchihlun/7850f07c11bdea022b2d49f1d4ed9802",
    "https://gist.githubusercontent.com/kangchihlun/7850f07c11bdea022b2d49f1d4ed9802/raw/93863977241b56cd23d5696bfbc26fd9bfd3924b/GTJA_Alpha191.py",
]
LOCAL_ALPHA191_PATH = "quant/infrastructure/var/research/external/guotai_junan_191_alpha"
STRATEGY_PARAMS: Dict[str, Any] = {
    "holding_days": 5,
    "max_positions": 40,
    "target_weight_slots": 40,
    "max_position_pct": 0.95,
    "cap_percentile_low": 0.25,
    "cap_percentile_high": 1.00,
    "min_price": 3.0,
    "min_turnover": 200_000.0,
    "lot_size": 100,
    "amount_lookback": 20,
    "alpha_high_is_better": True,
    "benchmark_symbol": base.TIMING_SYMBOL,
}


DETAIL_SECTION = """
<h3>策略执行流程</h3>
<div class="table-wrap"><table><thead><tr><th>步骤</th><th>执行逻辑</th><th>信息边界</th></tr></thead><tbody>
<tr><td>1. 因子来源</td><td>下载国泰君安 Alpha191 公共复现库；因子095公式为 STD(AMOUNT,20)。</td><td>本地文件保存在 quant/infrastructure/var/research/external/guotai_junan_191_alpha；公式不含行业中性或组合优化规则。</td></tr>
<tr><td>2. Universe</td><td>使用历史每日 total_mv 前 800 名并集，再按当日 point-in-time 市值分位取 25%-100% 区间。</td><td>不用当前成分股回溯；未来新上市股票只有在有当日 bar、状态和 PIT 字段后才可能入选。</td></tr>
<tr><td>3. 因子计算</td><td>每只股票取过去 20 个交易日 AMOUNT 的样本标准差，缺少完整窗口或成交额非正则剔除。</td><td>只使用当日及以前的 bar；本地 daily 字段优先使用 amount，其次 turnover，再回退到 close * volume。</td></tr>
<tr><td>4. 排名方向</td><td>默认按 Alpha191 原始值高者优先，等权买入前 40 只。</td><td>这是公式方向假设，不是用本次回测优化出来的方向；若报告表现差，不能反向调参后视为同一严格结论。</td></tr>
<tr><td>5. 严格执行</td><td>每 5 个交易日收盘后重算，下一交易日开盘执行；目标仓位 95%，100 股取整。</td><td>回测包含 T+1、涨跌停、停牌、ST/上市状态、佣金税费、5bps 最小滑点和 2% ADV 冲击约束。</td></tr>
</tbody></table></div>
"""


def _configure_base_runner() -> None:
    base.STRATEGY_ID = STRATEGY_ID
    base.TITLE = TITLE
    base.INITIAL_CASH = INITIAL_CASH
    base.SOURCE_URLS = list(SOURCE_URLS)
    base.STRATEGY_PARAMS = dict(STRATEGY_PARAMS)
    base.DETAIL_SECTION = DETAIL_SECTION
    base.AShareDividendLowVolQualityEnhancedStrategy = AShareGtjaAlpha095AmountStdStrategy
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
        "thesis": "短周期成交额波动可能代表资金关注度和交易活跃度变化，Alpha095 测试其在 A 股中大盘池的单因子选股能力。",
        "status": status,
        "stage": "full_research",
        "source": "gtja_alpha191_public_formula_library",
        "source_url": SOURCE_URLS[0],
        "decision_reason": base._decision_reason(strict_report, walkforward),
        "metrics": metrics,
        "evidence": {
            "source": "Guotai Junan Alpha191 public replication library and downloaded local formula file",
            "source_urls": SOURCE_URLS,
            "local_formula_path": f"{LOCAL_ALPHA191_PATH}/GTJA_Alpha191.py",
            "local_strategy": True,
            "discovery_quality": {
                "score": 0.72,
                "source_type": "public_broker_factor_replication",
                "matched_terms": ["国泰君安", "Alpha191", "alpha_95", "STD(AMOUNT,20)"],
                "risk_flags": ["formula_direction_not_independently_validated", "single_factor_short_cycle", "no_industry_neutralization"],
            },
            "strategy_spec": {
                "strategy_id": STRATEGY_ID,
                "strategy_type": "a_share_gtja_alpha191_single_factor",
                "signal_formula_key": "Alpha095 = STD(AMOUNT,20)",
                "prediction_direction": "higher_is_better_raw_alpha191_value",
                "symbols_count": len(symbols),
                "universe": symbols,
                "universe_source": "historical daily top total_mv union with daily PIT cap-band filtering",
                "lookback_days": int(STRATEGY_PARAMS["amount_lookback"]),
                "horizon_days": int(STRATEGY_PARAMS["holding_days"]),
                "execution_lag_days": 1,
                "rebalance_frequency": f"every {STRATEGY_PARAMS['holding_days']} trading days",
                "required_fields": AShareGtjaAlpha095AmountStdStrategy(symbols=[]).required_fields,
                "parameters": STRATEGY_PARAMS,
                "parameter_explanations": _parameter_explanations(),
                "strategy_logic": _strategy_logic(symbols, start, end),
                "source_report_urls": SOURCE_URLS,
                "downloaded_alpha191_path": LOCAL_ALPHA191_PATH,
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
            "conclusion": "本轮目标是对因子095给出完整可交易回测报告，未单独运行 Rank IC 分桶研究。",
            "method": "先下载 Alpha191 公式库并实现严格可交易 long-only 组合，再用项目 Backtester 直接验证净值表现。",
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
        "core_idea": "检验国泰君安 Alpha191 因子095的 A 股可交易单因子收益：过去 20 日成交额波动越高，资金关注度变化越强。",
        "universe": f"历史每日 total_mv 前 800 名并集，回测窗口 {start.date()} 到 {end.date()}，实际取数 {len(symbols)} 个 symbol。",
        "entry_filters": [
            "daily total_mv 位于候选池 25%-100% 分位",
            "price >= 3 and average turnover >= 200000",
            "完整 20 日 AMOUNT 窗口且标准差为正",
            "ST/suspended/non-listed/tradable=false rejected",
        ],
        "ranking_rule": "score = percentile_rank(STD(AMOUNT,20), higher_is_better=True)。",
        "portfolio_construction": "每次调仓最多 40 只，目标总仓位 95%，按 40 个目标槽位等权分配，100 股取整。",
        "rebalance_rule": "每 5 个交易日收盘后重算候选，下一交易日开盘执行。",
        "exit_rule": "持仓触发 ST、停牌、非上市、不可交易、低价或跌出目标篮子时卖出。",
        "risk_budget": "A 股 long-only，沪深300为基准，T+1，2% ADV 最大参与率，真实佣金税费与冲击成本。",
        "parameter_explanations": _parameter_explanations(),
    }


def _parameter_explanations() -> Dict[str, str]:
    return {
        "holding_days": "5 个交易日近似周度调仓，匹配 Alpha191 短周期价量因子的研究语境。",
        "max_positions": "宽篮子持仓数量，降低单一成交额波动因子的个股噪声。",
        "target_weight_slots": "固定 40 个目标槽位控制单票权重，避免信号稀疏时过度集中。",
        "max_position_pct": "组合目标总仓位，保留少量现金缓冲用于手数和交易成本。",
        "cap_percentile_low": "剔除候选池中当日市值最低的 25%，降低微盘和容量偏差。",
        "cap_percentile_high": "保留最高市值端，避免人为排除大盘交易活跃股票。",
        "min_price": "买入价格下限，过滤低价和潜在退市风险较高标的。",
        "min_turnover": "20 日平均成交额下限，保证短周期价量因子有基本交易容量。",
        "amount_lookback": "Alpha095 公式窗口，等于 STD(AMOUNT,20) 的 20 日。",
        "alpha_high_is_better": "按 Alpha191 原始因子值从高到低买入；这是预设方向，不是回测优化方向。",
        "benchmark_symbol": "沪深300基准代码，仅用于报告基准，不进入策略股票池。",
    }


def _parameter_sensitivity(strict_report: Dict[str, Any]) -> Dict[str, Any]:
    metrics = strict_report.get("metrics") or {}
    return {
        "status": "single_frozen_parameter_set",
        "method": "Faithful Alpha095 implementation with one frozen direction and parameter set; no optimization grid was used.",
        "base_params": STRATEGY_PARAMS,
        "selected_params": STRATEGY_PARAMS,
        "best_params": STRATEGY_PARAMS,
        "tested_count": 1,
        "pass_count": 1 if base._strict_pass(strict_report) else 0,
        "max_degradation_pct": 0.0,
        "rows": [
            {
                "name": "base_gtja_alpha095_raw_high",
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
