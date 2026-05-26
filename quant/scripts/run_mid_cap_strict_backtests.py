"""Run strict backtests for the three A-share mid-cap candidate strategies."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple, Type

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
from quant.features.strategies.reject.ashare_mid_cap_dividend_low_vol_capacity.strategy import (
    AShareMidCapDividendLowVolCapacityStrategy,
)
from quant.features.strategies.reject.ashare_mid_cap_low_vol_value.strategy import (
    AShareMidCapLowVolValueStrategy,
)
from quant.features.strategies.reject.ashare_mid_cap_momentum_value_guard.strategy import (
    AShareMidCapMomentumValueGuardStrategy,
)
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.trading.sub_portfolio import SubPortfolio
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
from quant.infrastructure.research.reporting import build_research_stage_report_html


START = datetime(2016, 1, 1)
END = datetime(2025, 12, 31)
INITIAL_CASH = 20000.0
REPORT_ROOT = Path("quant/infrastructure/var/research/reports")
COMMISSION_CFG = {
    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
    "HK": {"type": "hk_realistic"},
    "CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0},
}


STRATEGIES: List[Tuple[str, str, Type[Any]]] = [
    ("ashare_mid_cap_low_vol_value", "中盘低波价值", AShareMidCapLowVolValueStrategy),
    ("ashare_mid_cap_dividend_low_vol_capacity", "中盘股息低波容量", AShareMidCapDividendLowVolCapacityStrategy),
    ("ashare_mid_cap_momentum_value_guard", "中盘 12-1 动量估值护栏", AShareMidCapMomentumValueGuardStrategy),
]


DETAIL_SECTIONS = {
    "ashare_mid_cap_low_vol_value": """
<h3>策略执行流程</h3>
<div class="table-wrap"><table><thead><tr><th>步骤</th><th>执行逻辑</th><th>本次参数</th></tr></thead><tbody>
<tr><td>1. 中市值池</td><td>每个调仓日先过滤 ST、停牌、不可交易、非上市、低价和低成交额股票，再按当日 point-in-time total_mv 计算横截面分位。</td><td>total_mv 30%-80% 分位；价格 >= 5；20 日平均 turnover >= 50000。</td></tr>
<tr><td>2. 信号打分</td><td>只在中市值池内做当日横截面百分位 rank，rank=1 表示该字段最优。</td><td>score = 25% 低 PB + 20% 低 PE_TTM + 15% 低 PS_TTM + 25% 低 60 日波动 + 15% 小 60 日回撤。</td></tr>
<tr><td>3. 组合构建</td><td>取综合分最高的 50 只等权，向下取整到 100 股手数。</td><td>holding_days=20；target gross=100%。</td></tr>
<tr><td>4. 风险退出</td><td>持仓每日先跑状态/价格/流动性护栏，触发后即使未到调仓日也尝试卖出。</td><td>ST、停牌、list_status 非 L、不可交易、低价、低成交额。</td></tr>
</tbody></table></div>
""",
    "ashare_mid_cap_dividend_low_vol_capacity": """
<h3>策略执行流程</h3>
<div class="table-wrap"><table><thead><tr><th>步骤</th><th>执行逻辑</th><th>本次参数</th></tr></thead><tbody>
<tr><td>1. 中市值池</td><td>每个调仓日使用过滤后的 A 股 universe，按当日 total_mv 分位定义中盘容量池。</td><td>total_mv 30%-85% 分位；价格 >= 5；20 日平均 turnover >= 50000。</td></tr>
<tr><td>2. 信号打分</td><td>只在候选池内做当日横截面百分位 rank，不使用未来分红、不做全样本归一化。</td><td>score = 35% 高 dv_ttm + 25% 低 60 日波动 + 20% 低 PB + 10% 高 circ_mv + 10% 低 turnover_rate_f。</td></tr>
<tr><td>3. 组合构建</td><td>取综合分最高的 50 只等权，向下取整到 100 股手数。</td><td>holding_days=20；target gross=100%。</td></tr>
<tr><td>4. 风险退出</td><td>持仓每日先跑状态/价格/流动性护栏，触发后即使未到调仓日也尝试卖出。</td><td>ST、停牌、list_status 非 L、不可交易、低价、低成交额。</td></tr>
</tbody></table></div>
""",
    "ashare_mid_cap_momentum_value_guard": """
<h3>策略执行流程</h3>
<div class="table-wrap"><table><thead><tr><th>步骤</th><th>执行逻辑</th><th>本次参数</th></tr></thead><tbody>
<tr><td>1. 中市值池</td><td>每个调仓日使用过滤后的 A 股 universe，按当日 total_mv 分位定义中盘池。</td><td>total_mv 30%-80% 分位；价格 >= 5；20 日平均 turnover >= 50000。</td></tr>
<tr><td>2. 12-1 动量</td><td>动量只用后复权价格，计算 252 交易日前到 21 交易日前的收益，跳过最近 1 个月以减少短期反转影响。</td><td>momentum = adj_close[t-21] / adj_close[t-252] - 1。</td></tr>
<tr><td>3. 信号打分</td><td>只在候选池内做当日横截面百分位 rank，动量是主信号，估值和风险字段作为护栏。</td><td>score = 45% 高 12-1 动量 + 20% 低 PB + 15% 低 PS_TTM + 10% 低 120 日波动 + 10% 高 circ_mv。</td></tr>
<tr><td>4. 组合与退出</td><td>取综合分最高的 50 只等权；持仓每日跑状态/价格/流动性护栏。</td><td>holding_days=20；target gross=100%。</td></tr>
</tbody></table></div>
""",
}


def main() -> None:
    summaries = []
    symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit = _load_shared_inputs()
    for strategy_id, title, strategy_class in STRATEGIES:
        print(f"Running {strategy_id} on {len(symbols)} symbols")
        strict_report = _run_one(strategy_id, strategy_class, symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit)
        report_path, result_path = _write_report(strategy_id, title, strict_report)
        metrics = strict_report.get("metrics") or {}
        summaries.append(
            {
                "strategy_id": strategy_id,
                "report_path": str(report_path),
                "result_path": str(result_path),
                "sharpe": metrics.get("sharpe"),
                "cagr": metrics.get("cagr"),
                "total_return": metrics.get("total_return"),
                "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                "total_trades": metrics.get("total_trades"),
            }
        )
        print(json.dumps(summaries[-1], ensure_ascii=False))
    print(json.dumps({"completed": summaries}, ensure_ascii=False, indent=2))


def _load_shared_inputs():
    db_provider = DuckDBProvider()
    db_provider.connect()
    try:
        symbols = _load_ashare_symbols(db_provider)
        lot_sizes = _load_lot_sizes(db_provider, symbols, is_cn_symbol)
        benchmark_provider, benchmark_meta = _load_cn_benchmark_provider(db_provider, START, END, BenchmarkProvider)
        survivorship_audit = _cn_survivorship_audit(db_provider, START, END, formula_key="ashare_mid_cap")
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
    return [str(row[0]) for row in rows]


def _run_one(
    strategy_id: str,
    strategy_class: Type[Any],
    symbols: List[str],
    lot_sizes: Dict[str, int],
    benchmark_provider: BenchmarkProvider,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
) -> Dict[str, Any]:
    execution_cost_model = _strict_execution_cost_model(strategy_id, {"name": strategy_id}, True)
    data_provider = _DuckDBDailyDateProvider(
        symbols,
        START,
        END,
        include_daily_basic=True,
        include_execution_liquidity_features=True,
    )
    strategy = strategy_class(symbols=symbols)
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


def _write_report(strategy_id: str, title: str, strict_report: Dict[str, Any]) -> Tuple[Path, Path]:
    strategy_dir = REPORT_ROOT / strategy_id
    strategy_dir.mkdir(parents=True, exist_ok=True)
    result_path = strategy_dir / "last_result_with_execution_cost_model.json"
    result_path.write_text(json.dumps(strict_report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    row = _hypothesis_row(strategy_id, title, strict_report)
    result = {"run_id": f"{strategy_id}_strict_with_execution_cost_model", "backtested": 1, "rejected": 0, "errors": []}
    html = build_research_stage_report_html("strict_backtest", result, [row], generated_at=datetime.now(timezone.utc).isoformat())
    html = _insert_detail_section(html, strategy_id)
    report_path = strategy_dir / "strict_backtest_report_with_execution_cost_model.html"
    report_path.write_text(html, encoding="utf-8")
    (strategy_dir / "strict_backtest_report.html").write_text(html, encoding="utf-8")
    return report_path, result_path


def _hypothesis_row(strategy_id: str, title: str, strict_report: Dict[str, Any]) -> Dict[str, Any]:
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
        "strategy_id": strategy_id,
        "title": title,
        "status": "needs_more_validation",
        "metrics": {
            "strict_backtest": strict_report,
            "research_stage_conclusions": {
                "strict_backtest": {
                    "label": "严格回测",
                    "verdict": verdict,
                    "conclusion": (
                        f"禁用 execution cost model 严格回测：Sharpe={sharpe:.2f}，"
                        f"CAGR={cagr:.2%}，MaxDD={max_dd:.2%}。"
                    ),
                    "method": "项目 Backtester；T+1、涨跌停、停牌、100 股手数、真实佣金、5bps 固定滑点；execution cost model disabled。",
                }
            },
        },
        "evidence": {"strategy_spec": {"strategy_id": strategy_id}},
    }


def _insert_detail_section(html: str, strategy_id: str) -> str:
    section = DETAIL_SECTIONS.get(strategy_id, "")
    if not section:
        return html
    marker = "<h2>2. 严格回测证据</h2>\n<h3>回测 Equity Curve</h3>"
    replacement = f"<h2>2. 严格回测证据</h2>\n{section}<h3>回测 Equity Curve</h3>"
    return html.replace(marker, replacement, 1)


if __name__ == "__main__":
    main()
