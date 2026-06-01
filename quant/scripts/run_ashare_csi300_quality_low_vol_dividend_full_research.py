"""Run full research report for CSI300 quality low-vol dividend enhancement."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
from typing import Any, Dict, List, Tuple

_project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_project_root))

import quant.scripts.run_ashare_dividend_low_vol_quality_full_research as base
from quant.domain.models.market import is_cn_symbol
from quant.features.strategies.reject.ashare_csi300_quality_low_vol_dividend_enhanced.strategy import (
    AShareCsi300QualityLowVolDividendEnhancedStrategy,
    STRATEGY_NAME,
)
from quant.features.strategies.xueqiu_small_cap_financial_filter.strategy import (
    DEFAULT_EXCLUDED_BOARD_PREFIXES as SATELLITE_EXCLUDED_BOARD_PREFIXES,
    XueqiuSmallCapFinancialFilterStrategy,
)


STRATEGY_ID = "ashare_csi300_quality_low_vol_dividend_enhanced"
TITLE = "沪深300质量低波红利增强"
INITIAL_CASH = 1_000_000.0
CORE_ALLOCATION = 0.97
SATELLITE_ALLOCATION = 0.03
SATELLITE_RISK_INDEX_SYMBOL = "399001"
TARGET_EXCESS_CAGR = 0.02
SOURCE_URLS = [
    "https://www.spglobal.com/spdji/en/indices/dividends-factors/sp-china-a-share-low-volatility-high-dividend-index/",
    "https://www.spglobal.com/spdji/en/research/article/blending-low-volatility-with-dividend-yield-in-the-china-a-share-market",
    "https://www.fxbaogao.com/detail/3884054",
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
    "score_profile": "csi300_quality_low_vol_dividend_index_enhanced_v2",
    "max_replacements_per_rebalance": 10,
    "excluded_board_prefixes": ["300", "301", "688", "689"],
    "enable_risk_exit": True,
    "risk_exit": {
        "enabled": True,
        "stop_loss_pct": 0.20,
        "take_profit_pct": 0.55,
        "trailing_stop_pct": 0.16,
    },
}
SATELLITE_STRATEGY_PARAMS: Dict[str, Any] = {
    "max_positions": 3,
    "min_positions": 3,
    "target_exposure": 1.0,
    "empty_months": [1, 4],
    "risk_index_symbol": SATELLITE_RISK_INDEX_SYMBOL,
    "index_drawdown_lookback": 5,
    "index_drawdown_threshold": -0.05,
    "excluded_board_prefixes": list(SATELLITE_EXCLUDED_BOARD_PREFIXES),
    "enable_risk_exit": True,
    "risk_exit": {
        "enabled": True,
        "stop_loss_pct": 0.12,
        "min_stop_loss_pct": 0.08,
        "max_stop_loss_pct": 0.18,
        "stop_volatility_multiplier": 3.0,
        "take_profit_pct": 0.25,
        "trailing_stop_pct": 0.10,
        "trailing_volatility_multiplier": 2.5,
        "max_trailing_stop_pct": 0.22,
        "hard_take_profit_pct": 0.0,
        "exit_volatility_lookback": 20,
        "max_holding_days": 45,
        "min_time_stop_return": 0.02,
    },
}
HYBRID_EXECUTION_COST_MODEL: Dict[str, Any] = {
    **base.EXECUTION_COST_MODEL,
    "name": "cn_daily_liquidity_impact_core_satellite",
    "max_participation_rate": 0.01,
    "impact_coefficient": 0.50,
}
_LAST_CORE_SYMBOLS: List[str] = []
_LAST_SATELLITE_SYMBOLS: List[str] = []

DETAIL_SECTION = """
<h3>策略执行流程</h3>
<div class="table-wrap"><table><thead><tr><th>步骤</th><th>执行逻辑</th><th>信息边界</th></tr></thead><tbody>
<tr><td>1. Universe</td><td>使用历史每日 total_mv 前 800 名并集作为沪深300代理大盘池，并排除普通账户受限的 300/301/688/689 股票。</td><td>不用当前成分股回溯；未来新上市股票只有在有当日 bar、状态和 PIT 字段后才可能入选。</td></tr>
<tr><td>2. Core</td><td>97% 初始资金分配给沪深300代理增强核心，默认不做沪深300择时，组合保持 95% 目标权益暴露；每月最多替换 10 只股票。</td><td>核心目标是相对 000300 的低漂移选股增强。</td></tr>
<tr><td>3. Satellite</td><td>3% 初始资金分配给小盘财务过滤卫星：剔除创业板、科创板、ST、低流动性和亏损/收入代理异常标的后，持有市值最小的 3 只。</td><td>卫星仓位只使用普通账户可买股票；这是显式的小盘 alpha 暴露，不是沪深300成分内增强。</td></tr>
<tr><td>4. 多因子排序</td><td>核心按一年动量、近 60 日动量、ROE、低波动、低 PB、低 PE、低换手和股息率综合打分；卫星按财务过滤后的 point-in-time 市值升序。</td><td>价格信号只使用当日及以前后复权价格；财务字段按 ann_date point-in-time asof join。</td></tr>
<tr><td>5. 止盈止损</td><td>核心启用 20% 固定止损和 55%/16% 跟踪止盈；卫星启用波动率自适应止损、移动止盈和 45 日时间止损。</td><td>退出信号收盘生成，订单 T+1 开盘执行；仍受涨跌停、停牌、手数和流动性约束。</td></tr>
</tbody></table></div>
"""


def _configure_base_runner() -> None:
    base.STRATEGY_ID = STRATEGY_ID
    base.TITLE = TITLE
    base.INITIAL_CASH = INITIAL_CASH
    base.SOURCE_URLS = list(SOURCE_URLS)
    base.STRATEGY_PARAMS = _report_parameters()
    base.DETAIL_SECTION = DETAIL_SECTION
    base.AShareDividendLowVolQualityEnhancedStrategy = AShareCsi300QualityLowVolDividendEnhancedStrategy
    base.EXECUTION_COST_MODEL = dict(HYBRID_EXECUTION_COST_MODEL)
    base._load_inputs = _load_hybrid_inputs
    base._load_historical_large_cap_symbols = _load_historical_normal_account_large_cap_symbols
    base._run_backtest = _run_hybrid_backtest
    base._hypothesis_row = _hypothesis_row
    base._stage_conclusions = _stage_conclusions
    base._strategy_logic = _strategy_logic
    base._parameter_explanations = _parameter_explanations
    base._parameter_sensitivity = _parameter_sensitivity


class _HybridGuardDiagnostics:
    def __init__(
        self,
        core_strategy: AShareCsi300QualityLowVolDividendEnhancedStrategy,
        satellite_strategy: XueqiuSmallCapFinancialFilterStrategy,
    ):
        self.name = STRATEGY_ID
        self.max_position_pct = 1.0
        self.max_positions = int(getattr(core_strategy, "max_positions", 0) or 0) + int(
            getattr(satellite_strategy, "max_positions", 0) or 0
        )
        self.delisting_risk_guard = False
        self.min_trade_price = 0.0
        self.min_avg_turnover = 0.0
        self.liquidity_lookback = 0
        self.max_recent_suspended_days = 0
        self._core_strategy = core_strategy
        self._satellite_strategy = satellite_strategy

    def get_guard_diagnostics(self) -> Dict[str, Any]:
        core_guard = self._child_guard(self._core_strategy)
        satellite_guard = self._child_guard(self._satellite_strategy)
        return {
            "enabled": True,
            "strategy_id": STRATEGY_ID,
            "parameters": _report_parameters(),
            "entry_rejections": self._merge_counts(core_guard.get("entry_rejections"), satellite_guard.get("entry_rejections")),
            "exit_triggers": self._merge_counts(core_guard.get("exit_triggers"), satellite_guard.get("exit_triggers")),
            "core": core_guard,
            "satellite": satellite_guard,
        }

    @staticmethod
    def _child_guard(strategy: Any) -> Dict[str, Any]:
        getter = getattr(strategy, "get_guard_diagnostics", None)
        if callable(getter):
            try:
                value = getter()
            except Exception:
                value = {}
            return dict(value or {}) if isinstance(value, dict) else {}
        return {}

    @staticmethod
    def _merge_counts(*items: Any) -> Dict[str, int]:
        merged: Dict[str, int] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            for key, value in item.items():
                try:
                    merged[str(key)] = merged.get(str(key), 0) + int(value or 0)
                except (TypeError, ValueError):
                    continue
        return merged


def _report_parameters() -> Dict[str, Any]:
    return {
        "portfolio_construction": "csi300_core_plus_small_cap_quality_satellite",
        "target_excess_cagr": TARGET_EXCESS_CAGR,
        "allocations": {
            STRATEGY_NAME: CORE_ALLOCATION,
            "xueqiu_small_cap_financial_filter": SATELLITE_ALLOCATION,
        },
        "core_strategy": dict(STRATEGY_PARAMS),
        "satellite_strategy": dict(SATELLITE_STRATEGY_PARAMS),
        "execution_cost_model": dict(HYBRID_EXECUTION_COST_MODEL),
    }


def _load_hybrid_inputs(
    start: datetime,
    end: datetime,
    historical_rank_limit: int,
    top_market_cap_limit: int,
) -> Tuple[List[str], Dict[str, int], Any, Dict[str, Any], Dict[str, Any]]:
    del top_market_cap_limit
    global _LAST_CORE_SYMBOLS, _LAST_SATELLITE_SYMBOLS
    db_provider = base.DuckDBProvider()
    db_provider.connect()
    try:
        core_symbols = _load_historical_normal_account_large_cap_symbols(
            db_provider,
            start,
            end,
            int(historical_rank_limit or 800),
        )
        satellite_symbols = _load_normal_account_stock_symbols(db_provider, start, end)
        required_symbols = list(
            dict.fromkeys([*core_symbols, *satellite_symbols, SATELLITE_RISK_INDEX_SYMBOL])
        )
        lot_sizes = base._load_lot_sizes(
            db_provider,
            list(dict.fromkeys([*required_symbols, base.TIMING_SYMBOL])),
            is_cn_symbol,
        )
        benchmark_provider, benchmark_meta = base._load_cn_benchmark_provider(
            db_provider,
            start,
            end,
            base.BenchmarkProvider,
        )
        survivorship_audit = base._cn_survivorship_audit(db_provider, start, end, formula_key=STRATEGY_ID)
    finally:
        db_provider.disconnect()
    _LAST_CORE_SYMBOLS = list(core_symbols)
    _LAST_SATELLITE_SYMBOLS = list(satellite_symbols)
    return required_symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit


def _load_normal_account_stock_symbols(db_provider: Any, start: datetime, end: datetime) -> List[str]:
    rows = db_provider.storage.conn.execute(
        """
        SELECT DISTINCT symbol
        FROM daily_cn_ochl
        WHERE CAST(timestamp AS DATE) BETWEEN ? AND ?
          AND regexp_matches(symbol, '^[0236][0-9]{5}$')
          AND NOT starts_with(symbol, '200')
          AND NOT starts_with(symbol, '300')
          AND NOT starts_with(symbol, '301')
          AND NOT starts_with(symbol, '688')
          AND NOT starts_with(symbol, '689')
          AND symbol != ?
        ORDER BY symbol
        """,
        [start, end, base.TIMING_SYMBOL],
    ).fetchall()
    return [str(row[0]) for row in rows]


def _run_hybrid_backtest(
    symbols: List[str],
    lot_sizes: Dict[str, int],
    benchmark_provider: Any,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
    start: datetime,
    end: datetime,
) -> Dict[str, Any]:
    core_symbols = list(_LAST_CORE_SYMBOLS) or [symbol for symbol in symbols if symbol != SATELLITE_RISK_INDEX_SYMBOL]
    satellite_symbols = list(_LAST_SATELLITE_SYMBOLS) or [
        symbol for symbol in symbols if symbol not in {base.TIMING_SYMBOL, SATELLITE_RISK_INDEX_SYMBOL}
    ]
    all_symbols = list(dict.fromkeys([*core_symbols, *satellite_symbols, base.TIMING_SYMBOL, SATELLITE_RISK_INDEX_SYMBOL]))
    data_provider = base._DuckDBDailyDateProvider(
        all_symbols,
        start,
        end,
        include_daily_basic=True,
        include_financial_indicators=True,
        include_execution_liquidity_features=True,
        cache_enabled=True,
    )
    data_provider._chunk_size = max(252, int(getattr(data_provider, "_chunk_size", 63) or 63))
    core_strategy = AShareCsi300QualityLowVolDividendEnhancedStrategy(
        symbols=[*core_symbols, base.TIMING_SYMBOL],
        **STRATEGY_PARAMS,
    )
    satellite_strategy = XueqiuSmallCapFinancialFilterStrategy(
        symbols=[*satellite_symbols, SATELLITE_RISK_INDEX_SYMBOL],
        **SATELLITE_STRATEGY_PARAMS,
    )
    backtest_config = {"slippage_bps": 5, "execution_cost_model": dict(HYBRID_EXECUTION_COST_MODEL)}
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
            strategies=[core_strategy, satellite_strategy],
            initial_cash=INITIAL_CASH,
            data_provider=data_provider,
            symbols=all_symbols,
            strategy_allocations={
                core_strategy.name: CORE_ALLOCATION,
                satellite_strategy.name: SATELLITE_ALLOCATION,
            },
        )
    finally:
        data_provider.close()
    benchmark_equity_curve = benchmark_provider.get_benchmark_equity(start, end, INITIAL_CASH) if benchmark_provider else None
    return base._strict_backtest_report(
        bt_result,
        start,
        end,
        INITIAL_CASH,
        all_symbols,
        benchmark_meta,
        lot_sizes,
        _HybridGuardDiagnostics(core_strategy, satellite_strategy),
        benchmark_equity_curve,
        survivorship_audit,
        {**backtest_config, "commission": base.COMMISSION_CFG},
    )


def _load_historical_normal_account_large_cap_symbols(
    db_provider: Any,
    start: datetime,
    end: datetime,
    rank_limit: int,
) -> List[str]:
    storage = db_provider.storage
    if not getattr(storage, "_daily_basic_available")():
        raise RuntimeError("daily_basic sidecar unavailable")
    rows = storage.conn.execute(
        """
        WITH ranked AS (
            SELECT
                db.trade_date,
                db.symbol,
                row_number() OVER (
                    PARTITION BY db.trade_date
                    ORDER BY db.total_mv DESC NULLS LAST
                ) AS rank
            FROM daily_basic.cn_daily_basic db
            WHERE db.trade_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
              AND db.total_mv IS NOT NULL
              AND regexp_matches(db.symbol, '^[0236][0-9]{5}$')
              AND NOT starts_with(db.symbol, '200')
              AND NOT starts_with(db.symbol, '300')
              AND NOT starts_with(db.symbol, '301')
              AND NOT starts_with(db.symbol, '688')
              AND NOT starts_with(db.symbol, '689')
              AND db.symbol != ?
        )
        SELECT DISTINCT symbol
        FROM ranked
        WHERE rank <= ?
          AND EXISTS (
              SELECT 1
              FROM daily_cn_ochl bars
              WHERE bars.symbol = ranked.symbol
                AND CAST(bars.timestamp AS DATE) BETWEEN ? AND ?
          )
        ORDER BY symbol
        """,
        [start, end, base.TIMING_SYMBOL, int(rank_limit), start, end],
    ).fetchall()
    return [str(row[0]) for row in rows]


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
        "thesis": "用 97% 沪深300代理增强核心控制基准漂移，再用 3% 普通账户小盘财务过滤卫星补充独立 alpha，目标是把相对 000300 的年化超额提高到 2 个百分点以上。",
        "status": status,
        "stage": "full_research",
        "source": "broker_and_index_methodology_review",
        "source_url": SOURCE_URLS[0],
        "decision_reason": _decision_reason(strict_report, walkforward),
        "metrics": metrics,
        "evidence": {
            "source": "S&P low-volatility high-dividend methodology, A-share dividend low-vol research summaries, and local small-cap financial-filter alpha sleeve",
            "source_urls": SOURCE_URLS,
            "local_strategy": True,
            "discovery_quality": {
                "score": 0.78,
                "source_type": "index_methodology_broker_report_and_local_strategy_audit",
                "matched_terms": ["沪深300增强", "红利低波", "低波动", "质量", "动量", "小盘财务过滤"],
                "risk_flags": [
                    "proxy_universe_not_official_constituents",
                    "industry_neutral_optimizer_not_implemented",
                    "restricted_board_exclusion_changes_benchmark_exposure",
                    "small_cap_satellite_uses_strategy_selection_after_prior_research",
                ],
            },
            "strategy_spec": {
                "strategy_id": STRATEGY_ID,
                "strategy_type": "a_share_csi300_proxy_core_small_cap_quality_satellite_enhancement",
                "signal_formula_key": STRATEGY_ID,
                "prediction_direction": "higher_is_better",
                "symbols_count": len(symbols),
                "universe": symbols,
                "universe_source": "97% historical daily top total_mv union core plus 3% ordinary-account small-cap financial-filter satellite, excluding 300/301/688/689 restricted boards",
                "lookback_days": 252,
                "horizon_days": int(STRATEGY_PARAMS["holding_days"]),
                "execution_lag_days": 1,
                "rebalance_frequency": f"core every {STRATEGY_PARAMS['holding_days']} trading days; satellite weekly signal with Jan/Apr empty months",
                "required_fields": _required_fields(),
                "parameters": _report_parameters(),
                "parameter_explanations": _parameter_explanations(),
                "strategy_logic": _strategy_logic(symbols, start, end),
                "source_report_urls": SOURCE_URLS,
                "universe_start": start.date().isoformat(),
                "universe_end": end.date().isoformat(),
                "goal": {
                    "excess_cagr_gt": TARGET_EXCESS_CAGR,
                    "satellite_allocation_lte": SATELLITE_ALLOCATION,
                    "max_adv_participation_lte": 0.05,
                },
            },
        },
    }


def _stage_conclusions(strict_report: Dict[str, Any], walkforward: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    metrics = strict_report.get("metrics") or {}
    cagr = float(metrics.get("cagr") or 0.0)
    sharpe = float(metrics.get("sharpe") or 0.0)
    max_dd = float(metrics.get("max_drawdown_pct") or 0.0)
    excess_cagr = _excess_cagr(strict_report)
    return {
        "fast_research": {
            "label": "快研究",
            "verdict": "not_run",
            "conclusion": "本轮没有再做全量 Rank IC；重点验证低卫星仓位能否把严格回测年化超额推到 2 个百分点以上。",
            "method": "先约束卫星仓位，再用严格 Backtester 验证组合层收益、执行和容量。",
        },
        "strict_backtest": {
            "label": "严格回测",
            "verdict": "pass" if _target_excess_met(strict_report) else ("warn" if cagr > 0 else "fail"),
            "conclusion": (
                f"严格回测：Sharpe={sharpe:.2f}，CAGR={cagr:.2%}，"
                f"相对 000300 年化超额={excess_cagr:.2%}，MaxDD={max_dd:.2%}。"
            ),
            "method": "项目 Backtester；双子组合分账，T+1、涨跌停、停牌、100 股手数、真实佣金税费、5bps 最小滑点和 1% ADV 冲击约束。",
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
        "core_idea": "保持沪深300增强核心为主体，用 3% 小盘财务过滤卫星补足原核心在 A 股上 alpha 不足的问题；组合收益来自大盘多因子和小盘质量 alpha 的低比例叠加。",
        "universe": f"回测窗口 {start.date()} 到 {end.date()}，组合取数 {len(symbols)} 个 symbol；核心使用历史 total_mv 前 800 名普通账户可交易股票并集，卫星使用剔除创业板和科创板后的普通账户 A 股池。",
        "core_entry_filters": [
            "daily total_mv 位于候选池 60%-100% 分位",
            "exclude board prefixes 300/301/688/689",
            "price >= 5 and average turnover >= 200000",
            "PB/PE_TTM/PS_TTM 为正且不超过极端异常上限",
            "120d volatility <= 120% and max drawdown >= -60%",
            "ST/suspended/non-listed/tradable=false rejected",
        ],
        "satellite_entry_filters": [
            "exclude board prefixes 300/301/688/689",
            "exclude ST, suspended, non-listed, non-tradable and low-price stocks",
            "20d average turnover >= 20000 local amount unit",
            "total_mv/circ_mv >= 100000, positive PE/EPS/profit proxy and inferred revenue >= 10000",
        ],
        "core_ranking_rule": "score = 22% 一年动量 + 12% 近 60 日动量 + 16% ROE + 14% 低波 + 12% 低 PB + 10% 低 PE + 8% 低换手 + 6% 股息率。",
        "satellite_ranking_rule": "财务过滤后按 point-in-time total_mv 升序，市值越小优先。",
        "portfolio_construction": f"{CORE_ALLOCATION:.0%} 初始资金给核心宽篮子，{SATELLITE_ALLOCATION:.0%} 初始资金给卫星 3 股票小篮子；Backtester 使用子组合分账。",
        "rebalance_rule": "核心每 20 个交易日调仓；卫星按周信号调仓，并在 1 月/4 月空仓。",
        "exit_rule": "核心跌出目标篮子或触发状态/权限/价格/止盈止损退出；卫星叠加深成指 5 日跌幅风险开关、波动率自适应止损、移动止盈和 45 日时间止损。",
        "risk_exit": "core stop_loss=20%, trailing_take_profit starts after 55% gain and exits after 16% peak drawdown; satellite uses 8%-18% adaptive stop and 25%/10% trailing package.",
        "risk_budget": "A 股 long-only，沪深300为基准，T+1，1% ADV 最大参与率，真实佣金税费与冲击成本；小盘卫星带来明确风格漂移，需要单独容量和过拟合审计。",
        "parameter_explanations": _parameter_explanations(),
    }


def _parameter_explanations() -> Dict[str, str]:
    return {
        "portfolio_construction": "组合结构类型；本版从单一大盘增强改为核心-卫星结构。",
        "target_excess_cagr": "本轮优化目标：相对 000300 的年化超额至少 2 个百分点。",
        "allocations": "核心/卫星初始资金分配；本版选择 97%/3%，因为最低卫星比例已经把年化超额推到 2 个百分点以上。",
        "core_strategy": "沪深300代理增强核心参数，控制基准相关性和组合宽度。",
        "satellite_strategy": "普通账户小盘财务过滤卫星参数，作为独立 alpha 来源并限制在 3% 资金。",
        "holding_days": "20 个交易日近似月度调仓，匹配指数增强常见低到中等换手节奏。",
        "max_positions": "宽篮子股票数量，降低个股噪声并更接近指数增强而非集中选股。",
        "target_weight_slots": "用固定 40 个槽位控制单票目标权重，避免信号稀疏时仓位过度集中。",
        "cap_percentile_low": "在历史大盘代理池内保留当日偏大市值区间，降低中盘漂移。",
        "excluded_board_prefixes": "普通账户权限过滤，剔除创业板和科创板股票；ETF 策略不适用。",
        "min_dividend_yield": "优化版不做硬股息率门槛，股息率作为软打分因子，避免过度防御化。",
        "min_roe": "优化版不做硬 ROE 门槛，ROE 作为核心软打分因子。",
        "max_debt_to_assets": "优化版不做硬杠杆门槛，避免金融地产等行业被机械剔除；债务质量不再强行入场过滤。",
        "max_volatility": "极端波动过滤上限，低波动在排序中体现，不再把波动压得过低。",
        "max_replacements_per_rebalance": "单次调仓替换上限，降低换手和交易成本。",
        "risk_exit": "默认启用的策略级宽止盈止损包；关闭版本只作为专项消融研究。",
        "execution_cost_model": "组合层使用 1% ADV 参与率和更高冲击系数，覆盖卫星小盘仓位的成交约束。",
    }


def _parameter_sensitivity(strict_report: Dict[str, Any]) -> Dict[str, Any]:
    metrics = strict_report.get("metrics") or {}
    report_params = _report_parameters()
    return {
        "status": "single_frozen_parameter_set",
        "method": "Constrained core-satellite configuration selected after low-satellite allocation audit; report run freezes 97%/3% allocation and does not refit inside the backtest.",
        "base_params": report_params,
        "selected_params": report_params,
        "best_params": report_params,
        "tested_count": 1,
        "pass_count": 1 if _target_excess_met(strict_report) else 0,
        "max_degradation_pct": 0.0,
        "rows": [
            {
                "name": "core_97_satellite_3",
                "parameters": report_params,
                "cagr": metrics.get("cagr"),
                "excess_cagr": _excess_cagr(strict_report),
                "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                "sharpe": metrics.get("sharpe"),
                "verdict": "pass" if _target_excess_met(strict_report) else "warn",
            }
        ],
    }


def _required_fields() -> List[str]:
    fields = list(AShareCsi300QualityLowVolDividendEnhancedStrategy(symbols=[]).required_fields)
    fields.extend(
        [
            "open",
            "high",
            "low",
            "close",
            "volume",
            "turnover",
            "circ_mv",
            "pe",
            "ps",
            "is_st",
            "tradable",
            "has_daily_bar",
            "is_listed",
            "list_status",
        ]
    )
    return sorted(set(fields))


def _benchmark_cagr(strict_report: Dict[str, Any]) -> float:
    benchmark = strict_report.get("benchmark") or {}
    return float(benchmark.get("benchmark_cagr", benchmark.get("cagr") or 0.0) or 0.0)


def _excess_cagr(strict_report: Dict[str, Any]) -> float:
    metrics = strict_report.get("metrics") or {}
    return float(metrics.get("cagr") or 0.0) - _benchmark_cagr(strict_report)


def _target_excess_met(strict_report: Dict[str, Any]) -> bool:
    metrics = strict_report.get("metrics") or {}
    return (
        _excess_cagr(strict_report) >= TARGET_EXCESS_CAGR
        and int(metrics.get("total_trades") or 0) > 50
        and base._strict_capacity_ok(strict_report)
    )


def _row_status(strict_report: Dict[str, Any], walkforward: Dict[str, Any]) -> str:
    if _target_excess_met(strict_report) and bool(walkforward.get("is_viable")):
        return "needs_fast_validation"
    if _target_excess_met(strict_report):
        return "needs_walkforward_validation"
    metrics = strict_report.get("metrics") or {}
    if float(metrics.get("cagr") or 0.0) <= 0 or float(metrics.get("sharpe") or 0.0) <= 0:
        return "rejected"
    return "needs_more_research"


def _decision_reason(strict_report: Dict[str, Any], walkforward: Dict[str, Any]) -> str:
    metrics = strict_report.get("metrics") or {}
    return (
        f"strict Sharpe={float(metrics.get('sharpe') or 0.0):.2f}, "
        f"CAGR={float(metrics.get('cagr') or 0.0):.2%}, "
        f"000300 CAGR={_benchmark_cagr(strict_report):.2%}, "
        f"excess CAGR={_excess_cagr(strict_report):.2%}; "
        f"target excess met={_target_excess_met(strict_report)}; "
        f"walkforward viable={bool(walkforward.get('is_viable'))}; "
        "small-cap satellite introduces explicit style-selection risk."
    )


if __name__ == "__main__":
    _configure_base_runner()
    base.main()
