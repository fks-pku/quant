"""Run strict backtest for JoinQuant-inspired value RSRS timing."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

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
from quant.features.strategies.reject.joinquant_value_rsrs_timing.strategy import (
    JoinquantValueRsrsTimingStrategy,
)
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.trading.sub_portfolio import SubPortfolio
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
from quant.infrastructure.research.reporting import build_research_stage_report_html


START = datetime(2016, 1, 1)
END = datetime(2025, 12, 31)
INITIAL_CASH = 500000.0
STRATEGY_ID = "joinquant_value_rsrs_timing"
TITLE = "JoinQuant 价值选股 + RSRS 择时"
TIMING_SYMBOL = "000300"
REPORT_ROOT = Path("quant/infrastructure/var/research/reports")
COMMISSION_CFG = {
    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
    "HK": {"type": "hk_realistic"},
    "CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0},
}
EXECUTION_COST_MODEL = {
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


DETAIL_SECTION = """
<h3>本策略信号解释</h3>
<div class="table-wrap"><table><thead><tr><th>每日步骤</th><th>执行逻辑</th><th>本次参数</th></tr></thead><tbody>
<tr><td>1. 更新数据</td><td>收盘后接收全 A 股日线 bar，并单独接收 000300 指数真实 high/low；信号只使用当日及历史数据，订单在下一交易日执行。</td><td>市场=CN，频率=1d，区间=2016-01-01 至 2025-12-31。</td></tr>
<tr><td>2. 持仓风控</td><td>每天先检查已有持仓，遇到 ST、停牌、不可交易、非上市、list_status 非 L、价格低于下限或成交额低于下限时尝试退出。</td><td>min_price=5，min_turnover=50000。</td></tr>
<tr><td>3. 日线止盈止损</td><td>持仓收盘价跌破买入均价 10% 时触发硬止损；盈利达到 20% 后启动移动止盈，若从持仓最高收盘价回撤 8% 则退出。信号在收盘后生成，卖单仍在下一交易日执行。</td><td>stop_loss=10%，take_profit=20%，trailing_stop=8%。</td></tr>
<tr><td>4. RSRS 择时</td><td>对 000300 最近 N 日最低价与最高价做 OLS：high = alpha + beta * low；取 beta 的滚动 z-score，再乘以回归 R² 得到 RSRS score。</td><td>N=18，zscore_window=120，score >= 0.7 风险开启，score <= -0.7 风险关闭。</td></tr>
<tr><td>5. 风险关闭</td><td>RSRS 低于退出阈值或尚未完成热身时不新开仓；已有股票持仓全部卖出并保持现金。</td><td>不交易 timing_symbol 本身。</td></tr>
<tr><td>6. 价值候选池</td><td>风险开启且到达调仓日时，过滤 ST、停牌、不可交易、低价、低成交额股票，并要求 PE/PB/PS、市值字段有效。</td><td>使用 daily_basic point-in-time 字段。</td></tr>
<tr><td>7. 横截面打分</td><td>候选池内做 percentile rank：低 PB、低 PE_TTM、低 PS_TTM、高 DV_TTM、更大流通市值得分更高。</td><td>30% PB + 25% PE_TTM + 20% PS_TTM + 15% DV_TTM + 10% circ_mv。</td></tr>
<tr><td>8. 组合构建</td><td>选择综合分最高的 20 只股票等权，按 100 股手数向下取整；未入选持仓卖出，入选持仓调整到目标权重。</td><td>holding_days=20，target_gross=98%。</td></tr>
</tbody></table></div>
"""


class RsrsDailyDateProvider:
    def __init__(self, stock_symbols: List[str], timing_symbol: str, start: datetime, end: datetime):
        self._base = _DuckDBDailyDateProvider(
            stock_symbols,
            start,
            end,
            include_daily_basic=True,
            include_execution_liquidity_features=True,
        )
        self.trading_dates = self._base.trading_dates
        self._timing_symbol = str(timing_symbol)
        self._index_bars_by_date = self._load_timing_bars(start, end)
        self._dividends_by_key = self._load_dividends(stock_symbols, start, end)

    def get_bars_for_date(self, trading_date):
        key = pd.Timestamp(trading_date).date()
        rows = list(self._base.get_bars_for_date(key) or [])
        timing_bar = self._index_bars_by_date.get(key)
        if timing_bar is not None:
            rows.append(timing_bar)
        return rows

    def get_dividend_for_date(self, symbol, trading_date):
        key = (str(symbol), pd.Timestamp(trading_date).date())
        return self._dividends_by_key.get(key)

    def close(self) -> None:
        self._base.close()

    def _load_timing_bars(self, start: datetime, end: datetime) -> Dict[Any, Dict[str, Any]]:
        provider = DuckDBProvider()
        provider.connect()
        try:
            frame = provider.get_bars(self._timing_symbol, start, end, "1d")
        finally:
            provider.disconnect()
        if frame is None or frame.empty:
            return {}
        frame = frame.copy()
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
        frame = frame.dropna(subset=["timestamp"]).sort_values("timestamp")
        records = {}
        for record in frame.to_dict("records"):
            record["symbol"] = self._timing_symbol
            record["_suspended"] = False
            record["tradable"] = True
            record["has_daily_bar"] = True
            records[record["timestamp"].date()] = record
        return records

    def _load_dividends(self, stock_symbols: List[str], start: datetime, end: datetime) -> Dict[Any, Dict[str, Any]]:
        symbols = list(dict.fromkeys(str(symbol) for symbol in stock_symbols))
        if not symbols:
            return {}
        provider = DuckDBProvider()
        provider.connect()
        try:
            storage = provider.storage
            attached = storage._ensure_sidecar_attached("corp_actions", storage._corporate_actions_db_path)
            if not attached or not storage._table_exists("corp_actions.cn_dividends"):
                return {}
            placeholders = ", ".join("?" for _ in symbols)
            frame = storage.conn.execute(
                f"""
                SELECT
                    symbol,
                    CAST(ex_date AS DATE) AS ex_date,
                    cash_dividend,
                    stock_dividend,
                    allotment_ratio,
                    allotment_price,
                    record_date,
                    pay_date,
                    ann_date
                FROM corp_actions.cn_dividends
                WHERE CAST(ex_date AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
                  AND symbol IN ({placeholders})
                  AND (
                    COALESCE(cash_dividend, 0) > 0
                    OR COALESCE(stock_dividend, 0) > 0
                    OR COALESCE(allotment_ratio, 0) > 0
                  )
                """,
                [start, end, *symbols],
            ).fetchdf()
        finally:
            provider.disconnect()
        if frame is None or frame.empty:
            return {}
        result = {}
        for record in frame.to_dict("records"):
            key = (str(record.get("symbol")), pd.Timestamp(record.get("ex_date")).date())
            result[key] = {
                "cash_dividend": _float_or_zero(record.get("cash_dividend")),
                "stock_dividend": _float_or_zero(record.get("stock_dividend")),
                "allotment_ratio": _float_or_zero(record.get("allotment_ratio")),
                "allotment_price": _float_or_zero(record.get("allotment_price")),
                "record_date": record.get("record_date") or "",
                "pay_date": record.get("pay_date") or "",
                "ann_date": record.get("ann_date") or "",
            }
        return result


def _float_or_zero(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if pd.isna(number):
        return 0.0
    return number


def main() -> None:
    symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit = _load_shared_inputs()
    print(f"Running {STRATEGY_ID} on {len(symbols)} stock symbols plus {TIMING_SYMBOL}")
    strict_report = _run_backtest(symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit)
    report_path, result_path = _write_report(strict_report)
    metrics = strict_report.get("metrics") or {}
    print(
        json.dumps(
            {
                "strategy_id": STRATEGY_ID,
                "report_path": str(report_path),
                "result_path": str(result_path),
                "sharpe": metrics.get("sharpe"),
                "cagr": metrics.get("cagr"),
                "total_return": metrics.get("total_return"),
                "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                "total_trades": metrics.get("total_trades"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _load_shared_inputs() -> Tuple[List[str], Dict[str, int], BenchmarkProvider, Dict[str, Any], Dict[str, Any]]:
    db_provider = DuckDBProvider()
    db_provider.connect()
    try:
        symbols = _load_ashare_symbols(db_provider)
        lot_sizes = _load_lot_sizes(db_provider, [*symbols, TIMING_SYMBOL], is_cn_symbol)
        benchmark_provider, benchmark_meta = _load_cn_benchmark_provider(db_provider, START, END, BenchmarkProvider)
        survivorship_audit = _cn_survivorship_audit(db_provider, START, END, formula_key=STRATEGY_ID)
    finally:
        db_provider.disconnect()
    return symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit


def _load_ashare_symbols(db_provider: DuckDBProvider) -> List[str]:
    rows = db_provider.storage.conn.execute(
        """
        SELECT DISTINCT symbol
        FROM daily_cn_ochl
        WHERE CAST(timestamp AS DATE) BETWEEN ? AND ?
          AND regexp_matches(symbol, '^[0236][0-9]{5}$')
          AND NOT starts_with(symbol, '200')
        ORDER BY symbol
        """,
        [START, END],
    ).fetchall()
    return [str(row[0]) for row in rows if str(row[0]) != TIMING_SYMBOL]


def _run_backtest(
    symbols: List[str],
    lot_sizes: Dict[str, int],
    benchmark_provider: BenchmarkProvider,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
) -> Dict[str, Any]:
    strategy_symbols = [*symbols, TIMING_SYMBOL]
    data_provider = RsrsDailyDateProvider(symbols, TIMING_SYMBOL, START, END)
    strategy = JoinquantValueRsrsTimingStrategy(symbols=strategy_symbols, timing_symbol=TIMING_SYMBOL)
    backtest_config = {"slippage_bps": 5, "execution_cost_model": EXECUTION_COST_MODEL}
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
            symbols=strategy_symbols,
        )
    finally:
        data_provider.close()
    benchmark_equity_curve = benchmark_provider.get_benchmark_equity(START, END, INITIAL_CASH) if benchmark_provider else None
    return _strict_backtest_report(
        bt_result,
        START,
        END,
        INITIAL_CASH,
        strategy_symbols,
        benchmark_meta,
        lot_sizes,
        strategy,
        benchmark_equity_curve,
        survivorship_audit,
        {**backtest_config, "commission": COMMISSION_CFG},
    )


def _write_report(strict_report: Dict[str, Any]) -> Tuple[Path, Path]:
    strategy_dir = REPORT_ROOT / STRATEGY_ID
    strategy_dir.mkdir(parents=True, exist_ok=True)
    result_path = strategy_dir / "last_result.json"
    result_path.write_text(json.dumps(strict_report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    row = _hypothesis_row(strict_report)
    result = {"run_id": f"{STRATEGY_ID}_strict", "backtested": 1, "rejected": 0, "errors": []}
    html = build_research_stage_report_html("strict_backtest", result, [row], generated_at=datetime.now(timezone.utc).isoformat())
    html = _insert_detail_section(html)
    report_path = strategy_dir / "strict_backtest_report.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path, result_path


def _hypothesis_row(strict_report: Dict[str, Any]) -> Dict[str, Any]:
    metrics = strict_report.get("metrics") or {}
    sharpe = float(metrics.get("sharpe") or 0.0)
    cagr = float(metrics.get("cagr") or 0.0)
    max_dd = float(metrics.get("max_drawdown_pct") or 0.0)
    total_return = float(metrics.get("total_return") or 0.0)
    if sharpe >= 1.0 and cagr > 0.03:
        verdict = "pass"
    elif sharpe > 0 and total_return > 0:
        verdict = "warn"
    else:
        verdict = "fail"
    return {
        "strategy_id": STRATEGY_ID,
        "title": TITLE,
        "status": "needs_more_validation",
        "metrics": {
            "strict_backtest": strict_report,
            "research_stage_conclusions": {
                "strict_backtest": {
                    "label": "严格回测",
                    "verdict": verdict,
                    "conclusion": f"严格回测：Sharpe={sharpe:.2f}，CAGR={cagr:.2%}，MaxDD={max_dd:.2%}。",
                    "method": "项目 Backtester：T+1、涨跌停、停牌、100 股手数、真实佣金税费、5bps 固定滑点。",
                }
            },
        },
        "evidence": {
            "strategy_spec": {
                "strategy_id": STRATEGY_ID,
                "source_url": "https://www.joinquant.com/view/community/detail/713a60a2a1daaac2276dab73eb322ddc",
                "daily_approximation": True,
            }
        },
    }


def _insert_detail_section(html: str) -> str:
    marker = "<h3>回测 Equity Curve</h3>"
    if marker in html:
        return html.replace(marker, f"{DETAIL_SECTION}{marker}", 1)
    return html + DETAIL_SECTION


if __name__ == "__main__":
    main()
