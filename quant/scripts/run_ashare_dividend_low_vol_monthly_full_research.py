"""Run full research report for A-share monthly dividend low-vol enhancement."""

from __future__ import annotations

from datetime import datetime
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

_project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_project_root))

import quant.scripts.run_ashare_dividend_low_vol_quality_full_research as base
from quant.features.backtest.benchmark import BenchmarkProvider
from quant.features.strategies.reject.ashare_dividend_low_vol_monthly_enhanced.strategy import (
    AShareDividendLowVolMonthlyEnhancedStrategy,
)


STRATEGY_ID = "ashare_dividend_low_vol_monthly_enhanced"
TITLE = "A股红利低波月调增强"
INITIAL_CASH = 1_000_000.0
HISTORICAL_RANK_LIMIT = 3000
SOURCE_URLS = [
    "https://bigquant.com/square/paper/61e49855-4fa1-4d7f-bc01-88ecd2e483b7",
    "https://bigdata-s3.wmcloud.com/researchreport/2023-08/2311bac4c7502b6d65b5376030b7290e.pdf",
    "https://bigdata-s3.wmcloud.com/researchreport/2023-05/1a2fc57c567b7824d38eb62c5b755cf4.pdf",
]
STRATEGY_PARAMS: Dict[str, Any] = {
    "holding_days": 20,
    "max_positions": 30,
    "target_weight_slots": 30,
    "max_position_pct": 0.95,
    "cap_percentile_low": 0.20,
    "cap_percentile_high": 1.00,
    "min_price": 5.0,
    "min_turnover": 80_000.0,
    "use_market_timing": False,
    "min_long_momentum": -1.0,
    "min_recent_momentum": -1.0,
    "max_volatility": 0.80,
    "min_drawdown": -0.60,
    "max_pb": 8.0,
    "max_ps_ttm": 20.0,
    "min_roe": 0.0,
    "max_debt_to_assets": 0.0,
    "min_dividend_yield": 2.0,
    "score_profile": STRATEGY_ID,
    "enable_risk_exit": True,
    "risk_exit": {
        "enabled": True,
        "stop_loss_pct": 0.18,
        "take_profit_pct": 0.45,
        "trailing_stop_pct": 0.16,
    },
}
DETAIL_SECTION = """
<h3>策略执行流程</h3>
<div class="table-wrap"><table><thead><tr><th>步骤</th><th>执行逻辑</th><th>信息边界</th></tr></thead><tbody>
<tr><td>1. 公开策略来源</td><td>BigQuant/国盛提出红利低波月调、高频波动率替代传统低波、估值差择时；华泰整理中证红利低波指数的连续分红、流动性、股息率和低波筛选逻辑。</td><td>本地只复现日线可得部分；分钟高频波动率和精确指数成分 BP spread 留作残余研究缺口。</td></tr>
<tr><td>2. Universe</td><td>使用历史每日 total_mv 前 3000 名并集，调仓日再做状态、价格、流动性和当前市值分位过滤。</td><td>避免用当前成分/当前赢家回溯；新上市股票只有在有当日 bar 和 PIT 字段后才可入选。</td></tr>
<tr><td>3. 红利与估值</td><td>要求当日 point-in-time dv_ttm 至少 2%，PB 和 PS_TTM 为正且不过度极端，排序偏好高股息率和低 PB。</td><td>估值、股息率、市值来自 daily_basic 当日侧表。</td></tr>
<tr><td>4. 低波与质量</td><td>用 20 日后复权收益波动率替代不可得的分钟高频波动率，配合 60 日最大回撤、ROE 和资产负债率排序。</td><td>所有价格信号只使用当日及以前后复权价格。</td></tr>
<tr><td>5. 组合与执行</td><td>每 20 个交易日最多持有 30 只，目标权益仓位 95%，1,000,000 初始资金用于匹配 30 只组合和 A 股 100 股手数。</td><td>严格回测包含 T+1、100 股手数、涨跌停、停牌、佣金税费、分红处理和 2% ADV 冲击约束。</td></tr>
<tr><td>6. 风险退出</td><td>默认启用 18% 止损；盈利 45% 后若从峰值回撤 16% 触发移动止盈；ST/停牌/非上市/低价每日退出。</td><td>止盈止损为异常个股风险控制，不作为收益优化器。</td></tr>
</tbody></table></div>
"""

_BASE_LOAD_INPUTS = base._load_inputs
_BASE_HYPOTHESIS_ROW = base._hypothesis_row


def _configure_base_runner() -> None:
    base.STRATEGY_ID = STRATEGY_ID
    base.TITLE = TITLE
    base.INITIAL_CASH = INITIAL_CASH
    base.SOURCE_URLS = list(SOURCE_URLS)
    base.STRATEGY_PARAMS = dict(STRATEGY_PARAMS)
    base.DETAIL_SECTION = DETAIL_SECTION
    base.AShareDividendLowVolQualityEnhancedStrategy = AShareDividendLowVolMonthlyEnhancedStrategy
    base._load_inputs = _load_inputs
    base._hypothesis_row = _hypothesis_row
    base._strategy_logic = _strategy_logic
    base._parameter_explanations = _parameter_explanations
    base._decision_reason = _decision_reason


def _load_inputs(
    start: datetime,
    end: datetime,
    historical_rank_limit: int,
    top_market_cap_limit: int,
) -> Tuple[List[str], Dict[str, int], BenchmarkProvider, Dict[str, Any], Dict[str, Any]]:
    del historical_rank_limit
    return _BASE_LOAD_INPUTS(start, end, HISTORICAL_RANK_LIMIT, top_market_cap_limit)


def _hypothesis_row(
    symbols: List[str],
    strict_report: Dict[str, Any],
    walkforward: Dict[str, Any],
    start: datetime,
    end: datetime,
) -> Dict[str, Any]:
    row = _BASE_HYPOTHESIS_ROW(symbols, strict_report, walkforward, start, end)
    row["strategy_id"] = STRATEGY_ID
    row["title"] = TITLE
    row["thesis"] = "红利低波公开研究的可复现部分是：宽股票池中先找稳定高股息，再用低波和估值质量过滤提升防御性；本地用日线严格回测验证能否复现公开高收益。"
    row["source"] = "bigquant_guosheng_huatai_dividend_low_vol_research"
    row["source_url"] = SOURCE_URLS[0]
    evidence = row.setdefault("evidence", {})
    evidence["source"] = "BigQuant/Guosheng dividend-low-vol enhancement plus Huatai dividend-low-vol index methodology"
    evidence["source_urls"] = SOURCE_URLS
    discovery = evidence.setdefault("discovery_quality", {})
    discovery.update(
        {
            "score": 0.84,
            "source_type": "broker_financial_engineering_report_and_bigquant_summary",
            "matched_terms": ["红利低波", "月调", "低波动", "股息率", "估值差择时"],
            "risk_flags": [
                "intraday_high_frequency_volatility_unavailable_locally",
                "exact_dividend_low_vol_index_constituents_unavailable_locally",
                "public_return_claims_require_local_strict_replay",
            ],
        }
    )
    spec = evidence.setdefault("strategy_spec", {})
    spec["strategy_id"] = STRATEGY_ID
    spec["strategy_type"] = "ashare_monthly_dividend_low_vol_enhanced"
    spec["universe_source"] = "historical daily top total_mv 3000 union; live bars and PIT fields required at each rebalance"
    spec["parameters"] = STRATEGY_PARAMS
    spec["parameter_explanations"] = _parameter_explanations()
    spec["strategy_logic"] = _strategy_logic(symbols, start, end)
    spec["goal"] = {"cagr_gt": 0.10, "max_drawdown_gte": -0.30, "max_adv_participation_lte": 0.05}
    return row


def _strategy_logic(symbols: List[str], start: datetime, end: datetime) -> Dict[str, Any]:
    return {
        "core_idea": "复现红利低波增强公开研究的日线可得部分：高股息率提供现金收益和价值锚，20 日低波替代高频低波因子，低 PB 和质量过滤减少股息陷阱。",
        "universe": f"历史每日 total_mv 前 {HISTORICAL_RANK_LIMIT} 名并集，回测窗口 {start.date()} 到 {end.date()}，实际取数 {len(symbols)} 个 symbol。",
        "entry_filters": [
            "dv_ttm >= 2.0",
            "PB > 0 and PB <= 8",
            "PS_TTM > 0 and PS_TTM <= 20",
            "price >= 5 and 20d average turnover >= 80000",
            "ST/suspended/non-listed/tradable=false/list_status!=L rejected",
            "20d realized volatility <= 80% annualized and 60d max drawdown >= -60%",
        ],
        "ranking_rule": "score = 32% 股息率 + 28% 低 20 日波动 + 14% 低 PB + 10% 浅回撤 + 8% ROE + 4% 低负债 + 4% 近端动量。",
        "portfolio_construction": "每次调仓最多 30 只，目标总仓位 95%，按 30 个目标槽位等权，100 股取整。",
        "rebalance_rule": "每 20 个交易日收盘后重算候选，下一交易日开盘执行。",
        "exit_rule": "持仓触发 ST、停牌、非上市、不可交易、低价或 PnL 风控护栏时每日尝试退出；否则调仓跌出目标篮子时卖出。",
        "risk_exit": "enabled=true; stop_loss=18%; trailing_take_profit starts after 45% gain and exits after 16% drawdown from peak.",
        "risk_budget": "A 股 long-only，2% ADV 最大参与率，真实佣金税费、分红事件、T+1、涨跌停和停牌约束。",
        "parameter_explanations": _parameter_explanations(),
    }


def _parameter_explanations() -> Dict[str, str]:
    return {
        "holding_days": "20 个交易日近似月调，对应国盛红利低波增强方案里缩短调样周期的思路。",
        "historical_rank_limit": "历史每日 total_mv 前 3000 名并集，近似中证全指剔除尾部不可交易股票后的宽股票池。",
        "max_positions": "最多 30 只；本地使用 1,000,000 初始资金以避免 100 股手数导致组合无法分散。",
        "min_dividend_yield": "当前股息率门槛，要求 point-in-time dv_ttm 至少 2%。",
        "volatility_lookback": "20 日波动率，作为不可得分钟高频波动率的日线替代变量。",
        "max_pb": "估值过滤，避免低波红利组合集中到明显高估股票。",
        "max_position_pct": "目标总仓位 95%，保留现金缓冲。",
        "risk_exit": "宽止盈止损包，仅作为个股异常风险退出；默认 full report 不展示关闭对照。",
    }


def _decision_reason(strict_report: Dict[str, Any], walkforward: Dict[str, Any]) -> str:
    metrics = strict_report.get("metrics") or {}
    benchmark = strict_report.get("benchmark") or {}
    cagr = float(metrics.get("cagr") or 0.0)
    benchmark_cagr = float(benchmark.get("benchmark_cagr") or 0.0)
    return (
        f"strict monthly dividend-low-vol replay; CAGR={cagr:.2%}, "
        f"000300 CAGR={benchmark_cagr:.2%}, excess={cagr - benchmark_cagr:.2%}, "
        f"MaxDD={float(metrics.get('max_drawdown_pct') or 0.0):.2%}, "
        f"Sharpe={float(metrics.get('sharpe') or 0.0):.2f}; "
        f"walkforward viable={bool(walkforward.get('is_viable'))}."
    )


if __name__ == "__main__":
    _configure_base_runner()
    base.main()
