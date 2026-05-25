"""Run strict backtests for large-cap A-share stock timing candidates."""

from __future__ import annotations

import json
import math
import sys
from argparse import ArgumentParser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_project_root))

from quant.api.research_bp import (
    _DuckDBDailyDateProvider,
    _cn_survivorship_audit,
    _load_cn_benchmark_provider,
    _load_lot_sizes,
    _strict_backtest_report,
)
from quant.domain.models.market import is_cn_symbol
from quant.features.backtest.benchmark import BenchmarkProvider
from quant.features.backtest.engine import Backtester
from quant.features.strategies.reject.ashare_large_cap_low_vol_momentum_timing.strategy import (
    AShareLargeCapLowVolMomentumTimingStrategy,
)
from quant.features.strategies._mid_cap_common import AShareMidCapCompositeBase, ScoreSpec
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.trading.sub_portfolio import SubPortfolio
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
from quant.infrastructure.research.reporting import build_research_stage_report_html


START = datetime(2016, 1, 1)
END = datetime(2025, 12, 31)
INITIAL_CASH = 500000.0
STRATEGY_ID = "ashare_large_cap_low_vol_momentum_timing"
TITLE = "A股大市值低波动量趋势风控"
TIMING_SYMBOL = "000300"
REPORT_ROOT = Path("quant/infrastructure/var/research/reports")
COMMISSION_CFG = {
    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
    "HK": {"type": "hk_realistic"},
    "CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0},
}
LARGE_CAP_EXECUTION_COST_MODEL = {
    "enabled": True,
    "name": "cn_daily_liquidity_impact",
    "markets": ["CN"],
    "tick_size": 0.01,
    "half_spread_ticks": 0.5,
    "min_slippage_bps": 5,
    "max_participation_rate": 0.02,
    "impact_coefficient": 0.35,
    "volatility_fallback": 0.03,
    "adv_value_field": "adv20_value",
    "volatility_field": "volatility20",
}


SCENARIOS: List[Dict[str, Any]] = [
    {
        "name": "top20_momentum_lowvol_ma200",
        "cap_percentile_low": 0.80,
        "cap_percentile_high": 1.00,
        "max_positions": 30,
        "holding_days": 20,
        "min_turnover": 80_000.0,
        "timing_ma": 200,
        "timing_exit_buffer": 0.98,
        "timing_momentum_lookback": 60,
        "min_timing_momentum": -0.08,
        "stop_loss_pct": 0.10,
        "take_profit_pct": 0.25,
        "trailing_stop_pct": 0.10,
        "score_profile": "momentum_lowvol",
    },
    {
        "name": "top10_momentum_lowvol_ma200",
        "cap_percentile_low": 0.90,
        "cap_percentile_high": 1.00,
        "max_positions": 20,
        "holding_days": 20,
        "min_turnover": 100_000.0,
        "timing_ma": 200,
        "timing_exit_buffer": 0.98,
        "timing_momentum_lookback": 60,
        "min_timing_momentum": -0.08,
        "stop_loss_pct": 0.10,
        "take_profit_pct": 0.25,
        "trailing_stop_pct": 0.10,
        "score_profile": "momentum_lowvol",
    },
    {
        "name": "top_band_trend_elastic_ma60",
        "cap_percentile_low": 0.90,
        "cap_percentile_high": 1.00,
        "max_positions": 5,
        "max_position_pct": 0.90,
        "holding_days": 5,
        "min_turnover": 120_000.0,
        "timing_ma": 120,
        "timing_exit_buffer": 0.96,
        "timing_momentum_lookback": 40,
        "min_timing_momentum": -0.10,
        "symbol_trend_ma": 60,
        "symbol_entry_buffer": 1.00,
        "symbol_exit_buffer": 0.98,
        "stop_loss_pct": 0.06,
        "take_profit_pct": 0.0,
        "trailing_stop_pct": 0.0,
        "score_profile": "trend_elastic",
    },
    {
        "name": "large_pool_trend_elastic_ma60",
        "cap_percentile_low": 0.00,
        "cap_percentile_high": 1.00,
        "max_positions": 6,
        "max_position_pct": 0.90,
        "holding_days": 5,
        "min_turnover": 120_000.0,
        "timing_ma": 120,
        "timing_exit_buffer": 0.96,
        "timing_momentum_lookback": 40,
        "min_timing_momentum": -0.10,
        "symbol_trend_ma": 60,
        "symbol_entry_buffer": 1.00,
        "symbol_exit_buffer": 0.98,
        "stop_loss_pct": 0.06,
        "take_profit_pct": 0.0,
        "trailing_stop_pct": 0.0,
        "score_profile": "trend_elastic",
    },
    {
        "name": "large_pool_trend_elastic_ma120",
        "cap_percentile_low": 0.00,
        "cap_percentile_high": 1.00,
        "max_positions": 5,
        "max_position_pct": 0.90,
        "holding_days": 10,
        "min_turnover": 120_000.0,
        "timing_ma": 200,
        "timing_exit_buffer": 0.97,
        "timing_momentum_lookback": 60,
        "min_timing_momentum": -0.10,
        "symbol_trend_ma": 120,
        "symbol_entry_buffer": 1.00,
        "symbol_exit_buffer": 0.97,
        "stop_loss_pct": 0.08,
        "take_profit_pct": 0.0,
        "trailing_stop_pct": 0.0,
        "score_profile": "trend_elastic",
    },
    {
        "name": "rule_top20_quality_trend_ma60",
        "cap_percentile_low": 0.80,
        "cap_percentile_high": 1.00,
        "max_positions": 12,
        "target_weight_slots": 12,
        "max_position_pct": 0.90,
        "holding_days": 5,
        "min_turnover": 120_000.0,
        "use_market_timing": False,
        "timing_ma": 120,
        "timing_exit_buffer": 0.96,
        "timing_momentum_lookback": 40,
        "min_timing_momentum": -0.10,
        "symbol_trend_ma": 60,
        "symbol_entry_buffer": 1.00,
        "symbol_exit_buffer": 1.00,
        "min_long_momentum": 0.00,
        "min_recent_momentum": 0.00,
        "max_volatility": 0.70,
        "min_drawdown": -0.35,
        "max_pb": 15.0,
        "max_ps_ttm": 20.0,
        "stop_loss_pct": 0.06,
        "take_profit_pct": 0.0,
        "trailing_stop_pct": 0.0,
        "score_profile": "rule_based_quality_trend",
    },
    {
        "name": "rule_top20_quality_trend_ma120",
        "cap_percentile_low": 0.80,
        "cap_percentile_high": 1.00,
        "max_positions": 12,
        "target_weight_slots": 12,
        "max_position_pct": 0.90,
        "holding_days": 10,
        "min_turnover": 120_000.0,
        "use_market_timing": False,
        "timing_ma": 120,
        "timing_exit_buffer": 0.96,
        "timing_momentum_lookback": 40,
        "min_timing_momentum": -0.10,
        "symbol_trend_ma": 120,
        "symbol_entry_buffer": 1.00,
        "symbol_exit_buffer": 1.00,
        "min_long_momentum": 0.00,
        "min_recent_momentum": 0.00,
        "max_volatility": 0.65,
        "min_drawdown": -0.35,
        "max_pb": 15.0,
        "max_ps_ttm": 20.0,
        "stop_loss_pct": 0.08,
        "take_profit_pct": 0.0,
        "trailing_stop_pct": 0.0,
        "score_profile": "rule_based_quality_trend",
    },
    {
        "name": "rule_top30_quality_trend_ma60",
        "cap_percentile_low": 0.70,
        "cap_percentile_high": 1.00,
        "max_positions": 15,
        "target_weight_slots": 15,
        "max_position_pct": 0.90,
        "holding_days": 5,
        "min_turnover": 100_000.0,
        "use_market_timing": False,
        "timing_ma": 120,
        "timing_exit_buffer": 0.96,
        "timing_momentum_lookback": 40,
        "min_timing_momentum": -0.10,
        "symbol_trend_ma": 60,
        "symbol_entry_buffer": 1.00,
        "symbol_exit_buffer": 1.00,
        "min_long_momentum": 0.00,
        "min_recent_momentum": 0.00,
        "max_volatility": 0.75,
        "min_drawdown": -0.40,
        "max_pb": 18.0,
        "max_ps_ttm": 25.0,
        "stop_loss_pct": 0.06,
        "take_profit_pct": 0.0,
        "trailing_stop_pct": 0.0,
        "score_profile": "rule_based_quality_trend",
    },
    {
        "name": "rule_top20_quality_trend_monthly_ma60",
        "cap_percentile_low": 0.80,
        "cap_percentile_high": 1.00,
        "max_positions": 12,
        "target_weight_slots": 12,
        "max_position_pct": 0.90,
        "holding_days": 20,
        "min_turnover": 120_000.0,
        "use_market_timing": False,
        "timing_ma": 120,
        "timing_exit_buffer": 0.96,
        "timing_momentum_lookback": 40,
        "min_timing_momentum": -0.10,
        "symbol_trend_ma": 60,
        "symbol_entry_buffer": 1.00,
        "symbol_exit_buffer": 0.95,
        "min_long_momentum": 0.00,
        "min_recent_momentum": 0.00,
        "max_volatility": 0.70,
        "min_drawdown": -0.40,
        "max_pb": 15.0,
        "max_ps_ttm": 20.0,
        "stop_loss_pct": 0.10,
        "take_profit_pct": 0.0,
        "trailing_stop_pct": 0.0,
        "score_profile": "rule_based_quality_trend",
    },
    {
        "name": "rule_top20_quality_trend_monthly_ma120",
        "cap_percentile_low": 0.80,
        "cap_percentile_high": 1.00,
        "max_positions": 12,
        "target_weight_slots": 12,
        "max_position_pct": 0.90,
        "holding_days": 20,
        "min_turnover": 120_000.0,
        "use_market_timing": False,
        "timing_ma": 120,
        "timing_exit_buffer": 0.96,
        "timing_momentum_lookback": 40,
        "min_timing_momentum": -0.10,
        "symbol_trend_ma": 120,
        "symbol_entry_buffer": 1.00,
        "symbol_exit_buffer": 0.95,
        "min_long_momentum": 0.00,
        "min_recent_momentum": 0.00,
        "max_volatility": 0.65,
        "min_drawdown": -0.40,
        "max_pb": 15.0,
        "max_ps_ttm": 20.0,
        "stop_loss_pct": 0.10,
        "take_profit_pct": 0.0,
        "trailing_stop_pct": 0.0,
        "score_profile": "rule_based_quality_trend",
    },
    {
        "name": "top20_quality_defensive_ma120",
        "cap_percentile_low": 0.80,
        "cap_percentile_high": 1.00,
        "max_positions": 30,
        "holding_days": 15,
        "min_turnover": 80_000.0,
        "timing_ma": 120,
        "timing_exit_buffer": 0.97,
        "timing_momentum_lookback": 40,
        "min_timing_momentum": -0.05,
        "stop_loss_pct": 0.08,
        "take_profit_pct": 0.18,
        "trailing_stop_pct": 0.08,
        "score_profile": "quality_defensive",
    },
    {
        "name": "top15_pure_momentum_ma120",
        "cap_percentile_low": 0.85,
        "cap_percentile_high": 1.00,
        "max_positions": 15,
        "holding_days": 10,
        "min_turnover": 100_000.0,
        "timing_ma": 120,
        "timing_exit_buffer": 0.97,
        "timing_momentum_lookback": 40,
        "min_timing_momentum": -0.05,
        "stop_loss_pct": 0.08,
        "take_profit_pct": 0.18,
        "trailing_stop_pct": 0.08,
        "score_profile": "pure_momentum",
    },
    {
        "name": "top20_reversal_ma200",
        "cap_percentile_low": 0.80,
        "cap_percentile_high": 1.00,
        "max_positions": 30,
        "holding_days": 5,
        "min_turnover": 80_000.0,
        "timing_ma": 200,
        "timing_exit_buffer": 0.98,
        "timing_momentum_lookback": 60,
        "min_timing_momentum": -0.08,
        "stop_loss_pct": 0.06,
        "take_profit_pct": 0.12,
        "trailing_stop_pct": 0.05,
        "score_profile": "reversal",
    },
    {
        "name": "top10_reversal_ma120",
        "cap_percentile_low": 0.90,
        "cap_percentile_high": 1.00,
        "max_positions": 20,
        "holding_days": 5,
        "min_turnover": 100_000.0,
        "timing_ma": 120,
        "timing_exit_buffer": 0.97,
        "timing_momentum_lookback": 40,
        "min_timing_momentum": -0.05,
        "stop_loss_pct": 0.06,
        "take_profit_pct": 0.12,
        "trailing_stop_pct": 0.05,
        "score_profile": "reversal",
    },
    {
        "name": "top20_volume_reversal_ma120",
        "cap_percentile_low": 0.80,
        "cap_percentile_high": 1.00,
        "max_positions": 30,
        "holding_days": 5,
        "min_turnover": 80_000.0,
        "timing_ma": 120,
        "timing_exit_buffer": 0.97,
        "timing_momentum_lookback": 40,
        "min_timing_momentum": -0.05,
        "stop_loss_pct": 0.06,
        "take_profit_pct": 0.12,
        "trailing_stop_pct": 0.05,
        "score_profile": "volume_reversal",
    },
    {
        "name": "top20_quality_growth_ma200",
        "cap_percentile_low": 0.80,
        "cap_percentile_high": 1.00,
        "max_positions": 30,
        "holding_days": 20,
        "min_turnover": 80_000.0,
        "timing_ma": 200,
        "timing_exit_buffer": 0.98,
        "timing_momentum_lookback": 60,
        "min_timing_momentum": -0.08,
        "stop_loss_pct": 0.10,
        "take_profit_pct": 0.25,
        "trailing_stop_pct": 0.10,
        "score_profile": "quality_growth",
        "include_financial_indicators": True,
    },
    {
        "name": "top10_quality_growth_ma120",
        "cap_percentile_low": 0.90,
        "cap_percentile_high": 1.00,
        "max_positions": 20,
        "holding_days": 15,
        "min_turnover": 100_000.0,
        "timing_ma": 120,
        "timing_exit_buffer": 0.97,
        "timing_momentum_lookback": 40,
        "min_timing_momentum": -0.05,
        "stop_loss_pct": 0.08,
        "take_profit_pct": 0.18,
        "trailing_stop_pct": 0.08,
        "score_profile": "quality_growth",
        "include_financial_indicators": True,
    },
]


DETAIL_SECTION = """
<h3>策略执行流程</h3>
<div class="table-wrap"><table><thead><tr><th>步骤</th><th>执行逻辑</th><th>本次约束</th></tr></thead><tbody>
<tr><td>1. 大市值池</td><td>每个调仓日先做 ST、停牌、非上市、低价和流动性过滤，再按当日 point-in-time total_mv 取全 A 市值前 10%-20%。</td><td>使用 daily_basic 当日字段；不使用未来市值。</td></tr>
<tr><td>2. 大盘趋势风控</td><td>用 000300 的历史后复权收盘判断风险开关；风险关闭时清仓并保持现金，重新风险开启时重置调仓 gate。</td><td>网格测试 120/200 日均线和 40/60 日动量护栏。</td></tr>
<tr><td>3. 横截面信号</td><td>候选池内按 percentile rank 打分，组合 12-1 动量、60 日动量、120 日低波、120 日小回撤、低 PB/PS 和高流通市值。</td><td>只用当日及历史后复权价格、当日财务估值字段。</td></tr>
<tr><td>4. 持仓风控</td><td>持仓每日先跑状态/价格/流动性退出，并跟踪真实成交均价触发止损/移动止盈。</td><td>止损 8%-10%；盈利后回撤 8%-10% 出场。</td></tr>
<tr><td>5. 严格执行</td><td>信号收盘后生成，订单 T+1 开盘执行；启用 A 股日线流动性冲击成本、真实佣金税费、涨跌停/停牌/手数约束。</td><td>目标：CAGR &gt; 10%，MaxDD 不超过 40%。</td></tr>
</tbody></table></div>
"""


class LargeCapLowVolMomentumTimingStrategy(AShareMidCapCompositeBase):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        timing_symbol: str = TIMING_SYMBOL,
        holding_days: int = 20,
        max_positions: int = 30,
        max_position_pct: float = 1.0,
        cap_percentile_low: float = 0.80,
        cap_percentile_high: float = 1.00,
        min_price: float = 5.0,
        min_turnover: float = 80_000.0,
        timing_ma: int = 200,
        timing_exit_buffer: float = 0.98,
        timing_momentum_lookback: int = 60,
        min_timing_momentum: float = -0.08,
        stop_loss_pct: float = 0.10,
        take_profit_pct: float = 0.25,
        trailing_stop_pct: float = 0.10,
        score_profile: str = "momentum_lowvol",
    ):
        self.timing_symbol = str(timing_symbol)
        base_symbols = [str(symbol) for symbol in (symbols or [])]
        if self.timing_symbol not in base_symbols:
            base_symbols.append(self.timing_symbol)
        self.trade_symbols = [symbol for symbol in base_symbols if symbol != self.timing_symbol]
        self.timing_ma = max(20, int(timing_ma))
        self.timing_exit_buffer = min(max(float(timing_exit_buffer), 0.80), 1.00)
        self.timing_momentum_lookback = max(5, int(timing_momentum_lookback))
        self.min_timing_momentum = float(min_timing_momentum)
        self.stop_loss_pct = max(0.0, float(stop_loss_pct))
        self.take_profit_pct = max(0.0, float(take_profit_pct))
        self.trailing_stop_pct = max(0.0, float(trailing_stop_pct))
        self.score_profile = str(score_profile)
        self.momentum_lookback = 252
        self.momentum_skip = 21
        self.recent_momentum_lookback = 60
        self.volatility_lookback = 120
        self.drawdown_lookback = 120
        self._risk_on = False
        self._last_timing_state: Dict[str, Any] = {}
        self._entry_prices: Dict[str, float] = {}
        self._peak_prices: Dict[str, float] = {}
        super().__init__(
            STRATEGY_ID,
            symbols=base_symbols,
            holding_days=holding_days,
            max_positions=max_positions,
            max_position_pct=max_position_pct,
            cap_percentile_low=cap_percentile_low,
            cap_percentile_high=cap_percentile_high,
            min_price=min_price,
            min_turnover=min_turnover,
            lot_size=100,
            max_lookback=max(self.momentum_lookback, self.timing_ma, self.volatility_lookback) + 5,
        )

    @property
    def formula_key(self) -> str:
        return STRATEGY_ID

    @property
    def required_fields(self) -> List[str]:
        return ["total_mv", "circ_mv", "pb", "ps_ttm", "adj_close"]

    @property
    def score_specs(self) -> List[ScoreSpec]:
        if self.score_profile == "quality_defensive":
            return [
                ("volatility", 0.25, False),
                ("drawdown", 0.20, True),
                ("momentum", 0.20, True),
                ("recent_momentum", 0.10, True),
                ("pb", 0.15, False),
                ("circ_mv", 0.10, True),
            ]
        if self.score_profile == "pure_momentum":
            return [
                ("momentum", 0.55, True),
                ("recent_momentum", 0.20, True),
                ("volatility", 0.10, False),
                ("drawdown", 0.10, True),
                ("circ_mv", 0.05, True),
            ]
        if self.score_profile == "quality_growth":
            return [
                ("roe", 0.25, True),
                ("netprofit_yoy", 0.15, True),
                ("momentum", 0.25, True),
                ("volatility", 0.15, False),
                ("pb", 0.10, False),
                ("circ_mv", 0.10, True),
            ]
        if self.score_profile == "reversal":
            return [
                ("reversal", 0.45, True),
                ("volatility", 0.20, False),
                ("drawdown", 0.15, True),
                ("pb", 0.10, False),
                ("circ_mv", 0.10, True),
            ]
        if self.score_profile == "volume_reversal":
            return [
                ("volume_reversal", 0.45, True),
                ("volatility", 0.20, False),
                ("drawdown", 0.15, True),
                ("pb", 0.10, False),
                ("circ_mv", 0.10, True),
            ]
        return [
            ("momentum", 0.35, True),
            ("recent_momentum", 0.15, True),
            ("volatility", 0.20, False),
            ("drawdown", 0.15, True),
            ("pb", 0.10, False),
            ("circ_mv", 0.05, True),
        ]

    def on_after_trading(self, context: Any, trading_date) -> None:
        self._risk_exited_today = self._exit_risk_positions()
        risk_on = self._update_timing_state()
        if not risk_on:
            self._liquidate_trade_positions(self._risk_exited_today)
            self._last_rebalance_date = None
            self._days_since_rebalance = 0
            return
        if not self._check_rebalance_gate(trading_date):
            return
        self._execute_rebalance(context, trading_date)
        self._last_rebalance_date = trading_date
        self._days_since_rebalance = 0

    def _candidate_rejection(self, symbol: str, bar: Any) -> str:
        if symbol == self.timing_symbol:
            return "timing_symbol"
        return super()._candidate_rejection(symbol, bar)

    def _position_exit_reason(self, symbol: str, bar: Any) -> str:
        if symbol == self.timing_symbol:
            return "timing_symbol"
        reason = super()._position_exit_reason(symbol, bar)
        if reason:
            return reason
        profit_reason = self._profit_exit_reason(symbol, self._price(bar))
        if profit_reason:
            return profit_reason
        return ""

    def _strategy_snapshot(self, symbol: str, bar: Any, base: Dict[str, float | str]) -> Dict[str, float | str]:
        pb = self._positive_float(self._value(bar, "pb"))
        ps_ttm = self._positive_float(self._value(bar, "ps_ttm"))
        if pb <= 0:
            return {"symbol": symbol, "missing_field": "pb"}
        if ps_ttm <= 0:
            return {"symbol": symbol, "missing_field": "ps_ttm"}
        recent_momentum = self._return(symbol, self.recent_momentum_lookback)
        if recent_momentum is None:
            return {"symbol": symbol, "missing_field": "recent_momentum"}
        volatility = self._volatility(symbol, self.volatility_lookback)
        if volatility is None:
            return {"symbol": symbol, "missing_field": "volatility"}
        drawdown = self._max_drawdown(symbol, self.drawdown_lookback)
        if drawdown is None:
            return {"symbol": symbol, "missing_field": "drawdown"}
        if self.score_profile == "quality_growth":
            roe = self._positive_float(self._value(bar, "roe"))
            if roe <= 0:
                roe = self._positive_float(self._value(bar, "q_roe"))
            if roe <= 0:
                return {"symbol": symbol, "missing_field": "roe"}
            netprofit_yoy = self._finite_float(self._value(bar, "netprofit_yoy"))
            if netprofit_yoy is None:
                netprofit_yoy = self._finite_float(self._value(bar, "q_netprofit_yoy"))
            if netprofit_yoy is None:
                return {"symbol": symbol, "missing_field": "netprofit_yoy"}
            momentum = self._skip_recent_return(symbol, self.momentum_lookback, self.momentum_skip)
            if momentum is None:
                return {"symbol": symbol, "missing_field": "momentum"}
            if momentum <= -0.10 or recent_momentum <= -0.08 or netprofit_yoy <= -30.0:
                return {"symbol": symbol, "rejection_reason": "quality_growth_guard"}
            return {
                **base,
                "pb": pb,
                "ps_ttm": ps_ttm,
                "roe": roe,
                "netprofit_yoy": netprofit_yoy,
                "momentum": momentum,
                "recent_momentum": recent_momentum,
                "volatility": volatility,
                "drawdown": drawdown,
                "missing_field": "",
            }
        if self.score_profile in {"reversal", "volume_reversal"}:
            five_day_return = self._return(symbol, 5)
            if five_day_return is None:
                return {"symbol": symbol, "missing_field": "five_day_return"}
            if five_day_return > -0.015:
                return {"symbol": symbol, "rejection_reason": "no_short_term_pullback"}
            if five_day_return < -0.12 or recent_momentum < -0.20:
                return {"symbol": symbol, "rejection_reason": "falling_knife_guard"}
            volume_ratio = self._volume_ratio(symbol, 20)
            if volume_ratio is None:
                return {"symbol": symbol, "missing_field": "volume_ratio"}
            reversal = abs(five_day_return) / max(volatility, 1e-9)
            return {
                **base,
                "pb": pb,
                "ps_ttm": ps_ttm,
                "reversal": reversal,
                "volume_reversal": reversal * math.log1p(max(volume_ratio, 0.0)),
                "recent_momentum": recent_momentum,
                "volatility": volatility,
                "drawdown": drawdown,
                "missing_field": "",
            }
        momentum = self._skip_recent_return(symbol, self.momentum_lookback, self.momentum_skip)
        if momentum is None:
            return {"symbol": symbol, "missing_field": "momentum"}
        if momentum <= -0.10 or recent_momentum <= -0.08:
            return {"symbol": symbol, "rejection_reason": "negative_momentum_guard"}
        return {
            **base,
            "pb": pb,
            "ps_ttm": ps_ttm,
            "momentum": momentum,
            "recent_momentum": recent_momentum,
            "volatility": volatility,
            "drawdown": drawdown,
            "missing_field": "",
        }

    def _update_timing_state(self) -> bool:
        closes = [price for price in self._get_closes(self.timing_symbol) if price > 0 and math.isfinite(price)]
        needed = max(self.timing_ma, self.timing_momentum_lookback) + 1
        if len(closes) < needed:
            self._risk_on = False
            return False
        last = closes[-1]
        ma = sum(closes[-self.timing_ma :]) / float(self.timing_ma)
        momentum = last / closes[-self.timing_momentum_lookback - 1] - 1.0
        if last >= ma and momentum >= 0.0:
            self._risk_on = True
        elif last < ma * self.timing_exit_buffer or momentum < self.min_timing_momentum:
            self._risk_on = False
        self._last_timing_state = {
            "risk_on": self._risk_on,
            "close": last,
            "ma": ma,
            "momentum": momentum,
            "timing_ma": self.timing_ma,
            "timing_momentum_lookback": self.timing_momentum_lookback,
        }
        return self._risk_on

    def _liquidate_trade_positions(self, exclude: Optional[set[str]] = None) -> None:
        excluded = exclude or set()
        for symbol, quantity in list(self._positions.items()):
            if quantity <= 0 or symbol == self.timing_symbol or symbol in excluded:
                continue
            price = self._get_last_price(symbol)
            self.sell(symbol, int(quantity), "MARKET", price if price > 0 else None)

    def on_fill(self, context: Any, fill: Any) -> None:
        symbol = str(getattr(fill, "symbol", "") or "")
        previous_quantity = float(self._positions.get(symbol, 0) or 0)
        super().on_fill(context, fill)
        if not symbol or symbol == self.timing_symbol:
            return
        current_quantity = float(self._positions.get(symbol, 0) or 0)
        side = str(getattr(fill, "side", "") or "").upper()
        fill_quantity = float(getattr(fill, "quantity", 0) or 0)
        fill_price = self._fill_price(fill)
        if side == "BUY" and fill_quantity > 0:
            if previous_quantity > 0 and symbol in self._entry_prices:
                total_quantity = previous_quantity + fill_quantity
                if fill_price > 0:
                    self._entry_prices[symbol] = (
                        self._entry_prices[symbol] * previous_quantity + fill_price * fill_quantity
                    ) / total_quantity
            elif fill_price > 0:
                self._entry_prices[symbol] = fill_price
            if fill_price > 0:
                self._peak_prices[symbol] = max(self._peak_prices.get(symbol, fill_price), fill_price)
        elif side == "SELL" and current_quantity <= 0:
            self._entry_prices.pop(symbol, None)
            self._peak_prices.pop(symbol, None)

    def _profit_exit_reason(self, symbol: str, price: float) -> str:
        entry_price = self._effective_entry_price(symbol)
        if price <= 0 or entry_price <= 0 or self._positions.get(symbol, 0) <= 0:
            return ""
        self._peak_prices[symbol] = max(self._peak_prices.get(symbol, price), price)
        if self.stop_loss_pct > 0 and price <= entry_price * (1.0 - self.stop_loss_pct):
            return "stop_loss"
        peak = self._peak_prices.get(symbol, price)
        if (
            self.take_profit_pct > 0
            and self.trailing_stop_pct > 0
            and peak >= entry_price * (1.0 + self.take_profit_pct)
            and price <= peak * (1.0 - self.trailing_stop_pct)
        ):
            return "trailing_take_profit"
        return ""

    def _effective_entry_price(self, symbol: str) -> float:
        portfolio = getattr(getattr(self, "context", None), "portfolio", None)
        get_position = getattr(portfolio, "get_position", None)
        if callable(get_position):
            try:
                position = get_position(symbol)
            except Exception:
                position = None
            for field in ("avg_cost", "average_cost", "entry_price"):
                value = getattr(position, field, None)
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                if number > 0 and math.isfinite(number):
                    return number
        return self._entry_prices.get(symbol, 0.0)

    def _volume_ratio(self, symbol: str, lookback: int) -> Optional[float]:
        bars = self._day_data.get(symbol, [])
        if len(bars) <= lookback:
            return None
        volumes = []
        for bar in bars[-lookback - 1 : -1]:
            try:
                volume = float(self._value(bar, "volume", 0.0) or 0.0)
            except (TypeError, ValueError):
                volume = 0.0
            if volume > 0 and math.isfinite(volume):
                volumes.append(volume)
        if len(volumes) < lookback:
            return None
        current = self._positive_float(self._value(bars[-1], "volume", 0.0))
        if current <= 0:
            return None
        return current / max(sum(volumes) / float(len(volumes)), 1e-9)

    @staticmethod
    def _fill_price(fill: Any) -> float:
        for field in ("fill_price", "price", "entry_price"):
            value = getattr(fill, field, 0.0)
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number > 0 and math.isfinite(number):
                return number
        return 0.0

    @staticmethod
    def _finite_float(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def get_guard_diagnostics(self) -> Dict[str, Any]:
        diagnostics = super().get_guard_diagnostics()
        diagnostics["timing"] = dict(self._last_timing_state)
        diagnostics["parameters"].update(
            {
                "timing_symbol": self.timing_symbol,
                "timing_ma": self.timing_ma,
                "timing_exit_buffer": self.timing_exit_buffer,
                "timing_momentum_lookback": self.timing_momentum_lookback,
                "min_timing_momentum": self.min_timing_momentum,
                "stop_loss_pct": self.stop_loss_pct,
                "take_profit_pct": self.take_profit_pct,
                "trailing_stop_pct": self.trailing_stop_pct,
                "score_profile": self.score_profile,
            }
        )
        return diagnostics


def main() -> None:
    args = _parse_args()
    scenarios = _selected_scenarios(args.names)
    symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit = _load_shared_inputs(
        args.top_market_cap_limit
    )
    rows = []
    strict_reports = {}
    for scenario in scenarios:
        print(f"Running {scenario['name']} on {len(symbols)} stock symbols plus {TIMING_SYMBOL}")
        strict_report = _run_one(scenario, symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit)
        strict_reports[scenario["name"]] = strict_report
        metrics = strict_report.get("metrics") or {}
        row = {
            "scenario": scenario["name"],
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
    report_path, result_path = _write_outputs(rows, strict_reports, best)
    print(
        json.dumps(
            {
                "strategy_id": STRATEGY_ID,
                "best": best,
                "report_path": str(report_path),
                "result_path": str(result_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _load_shared_inputs(
    top_market_cap_limit: Optional[int] = None,
) -> Tuple[List[str], Dict[str, int], BenchmarkProvider, Dict[str, Any], Dict[str, Any]]:
    db_provider = DuckDBProvider()
    db_provider.connect()
    try:
        if top_market_cap_limit:
            stock_symbols = _load_latest_top_market_cap_symbols(db_provider, top_market_cap_limit)
        else:
            stock_symbols = _load_ashare_symbols(db_provider)
        symbols = [*stock_symbols, TIMING_SYMBOL]
        lot_sizes = _load_lot_sizes(db_provider, symbols, is_cn_symbol)
        benchmark_provider, benchmark_meta = _load_cn_benchmark_provider(db_provider, START, END, BenchmarkProvider)
        survivorship_audit = _cn_survivorship_audit(db_provider, START, END, formula_key=STRATEGY_ID)
    finally:
        db_provider.disconnect()
    return stock_symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit


def _load_latest_top_market_cap_symbols(db_provider: DuckDBProvider, limit: int) -> List[str]:
    if limit <= 0:
        raise ValueError("top_market_cap_limit must be positive")
    storage = db_provider.storage
    if not getattr(storage, "_daily_basic_available")():
        raise RuntimeError("daily_basic sidecar unavailable")
    rows = storage.conn.execute(
        """
        WITH latest AS (
            SELECT max(trade_date) AS trade_date
            FROM daily_basic.cn_daily_basic
        ),
        ranked AS (
            SELECT
                db.symbol,
                row_number() OVER (ORDER BY db.total_mv DESC NULLS LAST) AS rank
            FROM daily_basic.cn_daily_basic db
            JOIN latest ON db.trade_date = latest.trade_date
            WHERE db.total_mv IS NOT NULL
              AND regexp_matches(db.symbol, '^[0236][0-9]{5}$')
              AND NOT starts_with(db.symbol, '200')
              AND db.symbol != ?
              AND EXISTS (
                  SELECT 1
                  FROM daily_cn_ochl bars
                  WHERE bars.symbol = db.symbol
                    AND CAST(bars.timestamp AS DATE) BETWEEN ? AND ?
              )
        )
        SELECT symbol
        FROM ranked
        WHERE rank <= ?
        ORDER BY rank
        """,
        [TIMING_SYMBOL, START, END, int(limit)],
    ).fetchall()
    return [str(row[0]) for row in rows]


def _load_ashare_symbols(db_provider: DuckDBProvider) -> List[str]:
    rows = db_provider.storage.conn.execute(
        """
        SELECT DISTINCT symbol
        FROM daily_cn_ochl
        WHERE CAST(timestamp AS DATE) BETWEEN ? AND ?
          AND regexp_matches(symbol, '^[0236][0-9]{5}$')
          AND NOT starts_with(symbol, '200')
          AND symbol != ?
        ORDER BY symbol
        """,
        [START, END, TIMING_SYMBOL],
    ).fetchall()
    return [str(row[0]) for row in rows]


def _run_one(
    scenario: Dict[str, Any],
    stock_symbols: List[str],
    lot_sizes: Dict[str, int],
    benchmark_provider: BenchmarkProvider,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
) -> Dict[str, Any]:
    symbols = [*stock_symbols, TIMING_SYMBOL]
    execution_cost_model = dict(LARGE_CAP_EXECUTION_COST_MODEL)
    data_provider = _DuckDBDailyDateProvider(
        symbols,
        START,
        END,
        include_daily_basic=True,
        include_financial_indicators=bool(scenario.get("include_financial_indicators")),
        include_execution_liquidity_features=True,
    )
    strategy_kwargs = {key: value for key, value in scenario.items() if key not in {"name", "include_financial_indicators"}}
    strategy = AShareLargeCapLowVolMomentumTimingStrategy(symbols=symbols, timing_symbol=TIMING_SYMBOL, **strategy_kwargs)
    backtest_config = {"slippage_bps": 5, "execution_cost_model": execution_cost_model}
    bt_config = {
        "backtest": backtest_config,
        "execution": {"commission": COMMISSION_CFG},
        "data": {"default_timeframe": "1d"},
        "risk": {"max_position_pct": 1.0, "max_sector_pct": 1.0, "max_daily_loss_pct": 0.10, "max_leverage": 2.0},
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
    return float(metrics.get("cagr") or 0.0) > 0.10 and float(metrics.get("max_drawdown_pct") or 0.0) >= -0.40


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


def _write_outputs(
    rows: List[Dict[str, Any]],
    strict_reports: Dict[str, Dict[str, Any]],
    best: Dict[str, Any],
) -> Tuple[Path, Path]:
    strategy_dir = REPORT_ROOT / STRATEGY_ID
    strategy_dir.mkdir(parents=True, exist_ok=True)
    result_path = strategy_dir / "grid_result.json"
    result_path.write_text(
        json.dumps(
            {
                "strategy_id": STRATEGY_ID,
                "start": START.date().isoformat(),
                "end": END.date().isoformat(),
                "initial_cash": INITIAL_CASH,
                "rows": rows,
                "best": best,
                "strict_reports": strict_reports,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    row = _hypothesis_row(best, strict_reports[str(best["scenario"])])
    result = {"run_id": f"{STRATEGY_ID}_strict_grid", "backtested": len(rows), "rejected": 0, "errors": []}
    html = build_research_stage_report_html("strict_backtest", result, [row], generated_at=datetime.now(timezone.utc).isoformat())
    html = _insert_detail_section(html, rows)
    report_path = strategy_dir / "strict_backtest_report.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path, result_path


def _hypothesis_row(best: Dict[str, Any], strict_report: Dict[str, Any]) -> Dict[str, Any]:
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
            "research_stage_conclusions": {
                "strict_backtest": {
                    "label": "严格回测",
                    "verdict": verdict,
                    "conclusion": f"严格回测：Sharpe={sharpe:.2f}，CAGR={cagr:.2%}，MaxDD={max_dd:.2%}。",
                    "method": "项目 Backtester；T+1、真实佣金税费、100 股手数、5bps 基础滑点、cn_daily_liquidity_impact 冲击成本。",
                }
            },
        },
        "evidence": {
            "strategy_spec": {
                "strategy_id": STRATEGY_ID,
                "scenario": best["scenario"],
                "goal": {"cagr_gt": 0.10, "max_drawdown_gte": -0.40},
            }
        },
    }


def _insert_detail_section(html: str, rows: List[Dict[str, Any]]) -> str:
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
        "<h3>目标网格结果</h3><div class=\"table-wrap\"><table>"
        "<thead><tr><th>场景</th><th>CAGR</th><th>MaxDD</th><th>Sharpe</th><th>Trades</th><th>目标</th></tr></thead>"
        f"<tbody>{grid_rows}</tbody></table></div>"
    )
    marker = "<h2>2. 严格回测证据</h2>\n<h3>回测 Equity Curve</h3>"
    replacement = f"<h2>2. 严格回测证据</h2>\n{DETAIL_SECTION}{grid}<h3>回测 Equity Curve</h3>"
    return html.replace(marker, replacement, 1)


def _parse_args():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--names", nargs="*", default=None, help="Scenario names to run. Defaults to all scenarios.")
    parser.add_argument(
        "--top-market-cap-limit",
        type=int,
        default=None,
        help="Limit the stock universe to the latest total_mv top N symbols for faster strict iterations.",
    )
    return parser.parse_args()


def _selected_scenarios(names: Optional[List[str]]) -> List[Dict[str, Any]]:
    if not names:
        return list(SCENARIOS)
    wanted = set(names)
    selected = [scenario for scenario in SCENARIOS if scenario["name"] in wanted]
    missing = sorted(wanted - {scenario["name"] for scenario in selected})
    if missing:
        raise SystemExit(f"Unknown scenario names: {', '.join(missing)}")
    return selected


if __name__ == "__main__":
    main()
