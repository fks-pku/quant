"""Run strict backtests for large-cap A-share ETF defensive rotation."""

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
    _strict_backtest_report,
    _strict_execution_cost_model,
)
from quant.domain.models.market import is_cn_symbol
from quant.features.backtest.benchmark import BenchmarkProvider
from quant.features.backtest.engine import Backtester
from quant.features.strategies.reject.joinquant_qixing_daily_etf_rotation.strategy import (
    DEFAULT_RISK_SYMBOLS,
    JoinquantQixingDailyEtfRotationStrategy,
)
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.trading.sub_portfolio import SubPortfolio
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
from quant.infrastructure.research.reporting import build_research_stage_report_html


START = datetime(2016, 1, 1)
END = datetime(2025, 12, 31)
INITIAL_CASH = 20000.0
STRATEGY_ID = "large_cap_etf_defensive_rotation"
TITLE = "大市值 ETF 动量防守轮动"
REPORT_ROOT = Path("quant/infrastructure/var/research/reports")
COMMISSION_CFG = {
    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
    "HK": {"type": "hk_realistic"},
    "CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0},
}


SCENARIOS: List[Dict[str, Any]] = [
    {
        "name": "qixing_liquid_broad_etf",
        "symbols": [*DEFAULT_RISK_SYMBOLS, "511880"],
        "cash_symbol": "511880",
        "score_window": 24,
        "min_active_candidates": 5,
        "min_avg_turnover": 20_000_000.0,
        "recent_drawdown_stop": 0.05,
        "fixed_stop_loss": 0.08,
        "target_exposure": 0.98,
    },
    {
        "name": "pure_large_cap",
        "symbols": ["510050", "510300", "510880", "159915", "159949", "511880"],
        "cash_symbol": "511880",
        "score_window": 24,
        "min_active_candidates": 3,
        "min_avg_turnover": 20_000_000.0,
        "recent_drawdown_stop": 0.05,
        "fixed_stop_loss": 0.08,
        "target_exposure": 0.98,
    },
    {
        "name": "pure_large_cap_tighter_stop",
        "symbols": ["510050", "510300", "510880", "159915", "159949", "511880"],
        "cash_symbol": "511880",
        "score_window": 24,
        "min_active_candidates": 3,
        "min_avg_turnover": 20_000_000.0,
        "recent_drawdown_stop": 0.035,
        "fixed_stop_loss": 0.06,
        "target_exposure": 0.95,
    },
    {
        "name": "large_cap_plus_gold",
        "symbols": ["510050", "510300", "510880", "159915", "159949", "518880", "511880"],
        "cash_symbol": "511880",
        "score_window": 24,
        "min_active_candidates": 3,
        "min_avg_turnover": 20_000_000.0,
        "recent_drawdown_stop": 0.05,
        "fixed_stop_loss": 0.08,
        "target_exposure": 0.98,
    },
    {
        "name": "large_cap_plus_gold_tighter_stop",
        "symbols": ["510050", "510300", "510880", "159915", "159949", "518880", "511880"],
        "cash_symbol": "511880",
        "score_window": 24,
        "min_active_candidates": 3,
        "min_avg_turnover": 20_000_000.0,
        "recent_drawdown_stop": 0.035,
        "fixed_stop_loss": 0.06,
        "target_exposure": 0.95,
    },
]


DETAIL_SECTION = """
<h3>策略执行流程</h3>
<div class="table-wrap"><table><thead><tr><th>步骤</th><th>执行逻辑</th><th>本次约束</th></tr></thead><tbody>
<tr><td>1. 大市值 ETF 池</td><td>只在沪深 300、上证 50、红利、创业板/创业板 50 等高流动性大盘 ETF 中轮动；防守腿使用货币 ETF。</td><td>默认严格回测从 2016-01-01 开始。</td></tr>
<tr><td>2. 动量打分</td><td>每天用最近 N 日收盘价做加权 log-price 回归，年化斜率乘 R² 得到趋势质量分。</td><td>默认 score_window=24；只用当日及历史收盘。</td></tr>
<tr><td>3. 流动性与异常量过滤</td><td>候选必须满足 20 日平均成交额下限，且当日成交量不能显著高于过去均值。</td><td>min_avg_turnover=2000 万；max_volume_multiple=2.5。</td></tr>
<tr><td>4. 风控切现金</td><td>若现持有风险 ETF 触发近 3 日回撤或入场后固定止损，则次日切换到现金 ETF。</td><td>网格测试 5%/8% 与 3.5%/6% 两档止损。</td></tr>
<tr><td>5. 严格执行</td><td>信号收盘后生成，订单 T+1 开盘执行；使用 ETF 专用冲击成本、基金佣金、涨跌停/停牌/手数约束。</td><td>目标：CAGR &gt; 10%，MaxDD 不超过 30%。</td></tr>
</tbody></table></div>
"""


def main() -> None:
    all_symbols = sorted({symbol for scenario in SCENARIOS for symbol in scenario["symbols"]})
    lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit = _load_shared_inputs(all_symbols)
    rows = []
    strict_reports = {}
    for scenario in SCENARIOS:
        print(f"Running {scenario['name']} on {len(scenario['symbols'])} ETFs")
        strict_report = _run_one(scenario, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit)
        strict_reports[scenario["name"]] = strict_report
        metrics = strict_report.get("metrics") or {}
        row = {
            "scenario": scenario["name"],
            "symbols": scenario["symbols"],
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


def _load_shared_inputs(symbols: List[str]) -> Tuple[Dict[str, int], BenchmarkProvider, Dict[str, Any], Dict[str, Any]]:
    db_provider = DuckDBProvider()
    db_provider.connect()
    try:
        lot_sizes = _load_lot_sizes(db_provider, symbols, is_cn_symbol)
        benchmark_provider, benchmark_meta = _load_cn_benchmark_provider(db_provider, START, END, BenchmarkProvider)
        survivorship_audit = _cn_survivorship_audit(db_provider, START, END, formula_key=STRATEGY_ID)
    finally:
        db_provider.disconnect()
    return lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit


def _run_one(
    scenario: Dict[str, Any],
    lot_sizes: Dict[str, int],
    benchmark_provider: BenchmarkProvider,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
) -> Dict[str, Any]:
    symbols = list(scenario["symbols"])
    execution_cost_model = _strict_execution_cost_model(
        STRATEGY_ID,
        {
            "name": TITLE,
            "description": "large-cap ETF defensive rotation",
            "parameters": {"symbols": symbols, "cash_symbol": scenario["cash_symbol"]},
        },
        True,
    )
    data_provider = _DuckDBDailyDateProvider(
        symbols,
        START,
        END,
        include_daily_basic=False,
        include_execution_liquidity_features=True,
    )
    strategy = JoinquantQixingDailyEtfRotationStrategy(
        symbols=symbols,
        cash_symbol=str(scenario["cash_symbol"]),
        score_window=int(scenario["score_window"]),
        min_active_candidates=int(scenario["min_active_candidates"]),
        min_avg_turnover=float(scenario["min_avg_turnover"]),
        recent_drawdown_stop=float(scenario["recent_drawdown_stop"]),
        fixed_stop_loss=float(scenario["fixed_stop_loss"]),
        target_exposure=float(scenario["target_exposure"]),
    )
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
    return float(metrics.get("cagr") or 0.0) > 0.10 and float(metrics.get("max_drawdown_pct") or 0.0) >= -0.30


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
                    "method": "项目 Backtester；T+1、ETF/基金佣金、100 股手数、5bps 基础滑点、cn_etf_liquidity_impact 冲击成本。",
                }
            },
        },
        "evidence": {
            "strategy_spec": {
                "strategy_id": STRATEGY_ID,
                "scenario": best["scenario"],
                "symbols": best["symbols"],
                "goal": {"cagr_gt": 0.10, "max_drawdown_gte": -0.30},
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
        f"<td>{'通过' if row['meets_goal'] else '未通过'}</td>"
        "</tr>"
        for row in rows
    )
    grid = (
        "<h3>目标网格结果</h3><div class=\"table-wrap\"><table>"
        "<thead><tr><th>场景</th><th>CAGR</th><th>MaxDD</th><th>Sharpe</th><th>目标</th></tr></thead>"
        f"<tbody>{grid_rows}</tbody></table></div>"
    )
    marker = "<h2>2. 严格回测证据</h2>\n<h3>回测 Equity Curve</h3>"
    replacement = f"<h2>2. 严格回测证据</h2>\n{DETAIL_SECTION}{grid}<h3>回测 Equity Curve</h3>"
    return html.replace(marker, replacement, 1)


if __name__ == "__main__":
    main()
