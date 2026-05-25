"""Run strict backtests for A-share gold-equity ETF barbell timing."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from html import escape
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
try:
    from quant.features.strategies.ashare_gold_equity_barbell_timing.strategy import (
        AShareGoldEquityBarbellTimingStrategy,
        DEFAULT_PIT_SIZE_FIELDS,
    )
except ModuleNotFoundError:
    from quant.features.rejected_strategy.ashare_gold_equity_barbell_timing.strategy import (
        AShareGoldEquityBarbellTimingStrategy,
        DEFAULT_PIT_SIZE_FIELDS,
    )
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.trading.sub_portfolio import SubPortfolio
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
from quant.infrastructure.research.cn_etf_universe import (
    build_gold_equity_barbell_pit_universe,
    flatten_category_symbols,
)
from quant.infrastructure.research.reporting import build_research_stage_report_html


START = datetime(2016, 1, 1)
END = datetime(2025, 12, 31)
UNIVERSE_AS_OF = None
UNIVERSE_MIN_HISTORY_DAYS_AS_OF = 0
UNIVERSE_MAX_SYMBOLS_PER_CATEGORY = 0
INITIAL_CASH = 500000.0
STRATEGY_ID = "ashare_gold_equity_barbell_timing"
TITLE = "黄金-大盘 ETF 杠铃择时"
REPORT_ROOT = Path("quant/infrastructure/var/research/reports")
COMMISSION_CFG = {
    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
    "HK": {"type": "hk_realistic"},
    "CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0},
}


SCENARIOS: List[Dict[str, Any]] = [
    {
        "name": "monthly_63d_120ma_half_equity_half_gold",
        "timing_symbol": "000300",
        "momentum_lookback": 63,
        "momentum_skip": 1,
        "trend_window": 120,
        "volatility_window": 20,
        "liquidity_window": 20,
        "min_avg_turnover": 20_000_000.0,
        "target_exposure": 0.98,
        "risk_leg_weight": 0.50,
        "holding_days": 20,
        "require_pit_size": True,
        "pit_size_fields": list(DEFAULT_PIT_SIZE_FIELDS),
    },
    {
        "name": "monthly_63d_120ma_40pct_equity_60pct_gold",
        "timing_symbol": "000300",
        "momentum_lookback": 63,
        "momentum_skip": 1,
        "trend_window": 120,
        "volatility_window": 20,
        "liquidity_window": 20,
        "min_avg_turnover": 20_000_000.0,
        "target_exposure": 0.98,
        "risk_leg_weight": 0.40,
        "holding_days": 20,
        "require_pit_size": True,
        "pit_size_fields": list(DEFAULT_PIT_SIZE_FIELDS),
    },
    {
        "name": "weekly_63d_120ma_half_equity_half_gold",
        "timing_symbol": "000300",
        "momentum_lookback": 63,
        "momentum_skip": 1,
        "trend_window": 120,
        "volatility_window": 20,
        "liquidity_window": 20,
        "min_avg_turnover": 20_000_000.0,
        "target_exposure": 0.98,
        "risk_leg_weight": 0.50,
        "holding_days": 5,
        "require_pit_size": True,
        "pit_size_fields": list(DEFAULT_PIT_SIZE_FIELDS),
    },
    {
        "name": "monthly_126d_200ma_half_equity_half_gold",
        "timing_symbol": "000300",
        "momentum_lookback": 126,
        "momentum_skip": 1,
        "trend_window": 200,
        "volatility_window": 20,
        "liquidity_window": 20,
        "min_avg_turnover": 20_000_000.0,
        "target_exposure": 0.98,
        "risk_leg_weight": 0.50,
        "holding_days": 20,
        "require_pit_size": True,
        "pit_size_fields": list(DEFAULT_PIT_SIZE_FIELDS),
    },
]


DETAIL_SECTION = """
<h3>策略执行逻辑</h3>
<div class="table-wrap"><table><thead><tr><th>每日步骤</th><th>运行规则</th><th>信号解释</th></tr></thead><tbody>
<tr><td>1. 更新数据</td><td>读取大盘/宽基/红利/创业板 ETF 与黄金 ETF 日线，ETF/LOF 使用 fund NAV 复权后的 total-return bar。</td><td>避免 ETF 拆分或分红被误记为价格暴跌。</td></tr>
<tr><td>2. 市场温度</td><td>以沪深300 ETF 为温度计：收盘价高于均线且中期动量为正时为 risk-on，否则 risk-off。</td><td>权益风险只在大盘趋势向上时打开。</td></tr>
<tr><td>3. 权益腿选择</td><td>risk-on 时在上证50、沪深300、创业板、创业板50、红利 ETF 中按动量/波动打分选 1 只。</td><td>不固定某只股票，也不使用中证500/中证1000等小盘 proxy。</td></tr>
<tr><td>4. 防守腿</td><td>黄金 ETF 是防守腿；risk-on 时与权益腿做杠铃，risk-off 时单独承担目标敞口。</td><td>用与 A 股低相关的资产降低熊市权益暴露。</td></tr>
<tr><td>5. 调仓与执行</td><td>每 5 或 20 个交易日调仓，信号收盘生成，订单 T+1 开盘执行。</td><td>严格回测包含 ETF 基金佣金、手数、停牌/涨跌停约束和流动性冲击成本。</td></tr>
</tbody></table></div>
"""


def main() -> None:
    universe = build_gold_equity_barbell_pit_universe(
        universe_as_of=UNIVERSE_AS_OF,
        min_history_days_as_of=UNIVERSE_MIN_HISTORY_DAYS_AS_OF,
        max_symbols_per_category=UNIVERSE_MAX_SYMBOLS_PER_CATEGORY,
        universe_start=START,
        universe_end=END,
    )
    _validate_pit_universe(universe)
    scenarios = [_with_pit_universe(scenario, universe) for scenario in SCENARIOS]
    all_symbols = sorted({symbol for scenario in scenarios for symbol in scenario["symbols"]})
    lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit = _load_shared_inputs(all_symbols)
    rows = []
    strict_reports = {}
    for scenario in scenarios:
        print(f"Running {scenario['name']} on {len(all_symbols)} ETFs", flush=True)
        strict_report = _run_one(scenario, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit)
        strict_reports[scenario["name"]] = strict_report
        metrics = strict_report.get("metrics") or {}
        row = {
            "scenario": scenario["name"],
            "symbols": all_symbols,
            "risk_category_symbols": scenario["risk_category_symbols"],
            "defensive_category_symbols": scenario["defensive_category_symbols"],
            "timing_symbol": scenario["timing_symbol"],
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
    report_path, result_path = _write_outputs(rows, strict_reports, best, universe)
    print(json.dumps({"strategy_id": STRATEGY_ID, "best": best, "report_path": str(report_path), "result_path": str(result_path)}, ensure_ascii=False, indent=2))


def _with_pit_universe(scenario: Dict[str, Any], universe: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(scenario)
    result["risk_category_symbols"] = {
        key: list(value)
        for key, value in (universe.get("risk_category_symbols") or {}).items()
    }
    result["defensive_category_symbols"] = {
        key: list(value)
        for key, value in (universe.get("defensive_category_symbols") or {}).items()
    }
    result["symbols"] = list(
        dict.fromkeys(
            [
                *flatten_category_symbols(result["risk_category_symbols"], result["defensive_category_symbols"]),
                str(result["timing_symbol"]),
            ]
        )
    )
    return result


def _validate_pit_universe(universe: Dict[str, Any]) -> None:
    risk = universe.get("risk_category_symbols") or {}
    defensive = universe.get("defensive_category_symbols") or {}
    missing = [key for key, values in {**risk, **defensive}.items() if not values]
    if missing:
        raise RuntimeError(f"PIT ETF universe missing required categories: {', '.join(missing)}")


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
        {"name": TITLE, "description": "gold-equity ETF barbell timing", "parameters": dict(scenario)},
        True,
    )
    data_provider = _DuckDBDailyDateProvider(
        symbols,
        START,
        END,
        include_daily_basic=False,
        include_execution_liquidity_features=True,
    )
    strategy = AShareGoldEquityBarbellTimingStrategy(
        risk_category_symbols={key: list(value) for key, value in scenario["risk_category_symbols"].items()},
        defensive_category_symbols={key: list(value) for key, value in scenario["defensive_category_symbols"].items()},
        timing_symbol=str(scenario["timing_symbol"]),
        momentum_lookback=int(scenario["momentum_lookback"]),
        momentum_skip=int(scenario["momentum_skip"]),
        trend_window=int(scenario["trend_window"]),
        volatility_window=int(scenario["volatility_window"]),
        liquidity_window=int(scenario["liquidity_window"]),
        min_avg_turnover=float(scenario["min_avg_turnover"]),
        target_exposure=float(scenario["target_exposure"]),
        risk_leg_weight=float(scenario["risk_leg_weight"]),
        holding_days=int(scenario["holding_days"]),
        pit_size_fields=list(scenario["pit_size_fields"]),
        require_pit_size=bool(scenario["require_pit_size"]),
    )
    backtest_config = {"slippage_bps": 5, "execution_cost_model": execution_cost_model}
    bt_config = {
        "backtest": backtest_config,
        "execution": {"commission": COMMISSION_CFG},
        "data": {"default_timeframe": "1d"},
        "risk": {"max_position_pct": 1.0, "max_sector_pct": 1.0, "max_daily_loss_pct": 0.10, "max_leverage": 1.0},
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
    universe: Dict[str, Any],
) -> Tuple[Path, Path]:
    strategy_dir = REPORT_ROOT / STRATEGY_ID
    strategy_dir.mkdir(parents=True, exist_ok=True)
    result_path = strategy_dir / "grid_result.json"
    last_result_path = strategy_dir / "last_result.json"
    payload = {
        "strategy_id": STRATEGY_ID,
        "start": START.date().isoformat(),
        "end": END.date().isoformat(),
        "initial_cash": INITIAL_CASH,
        "rows": rows,
        "best": best,
        "pit_universe": universe,
        "strict_reports": strict_reports,
    }
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    last_result_path.write_text(json.dumps(strict_reports[str(best["scenario"])], ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    row = _hypothesis_row(best, strict_reports[str(best["scenario"])])
    result = {"run_id": f"{STRATEGY_ID}_strict_grid", "backtested": len(rows), "rejected": 0, "errors": []}
    html = build_research_stage_report_html("strict_backtest", result, [row], generated_at=datetime.now(timezone.utc).isoformat())
    html = _insert_detail_section(html, rows, universe)
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
                    "method": "项目 Backtester；信号收盘生成、订单 T+1 开盘执行；ETF 基金佣金、手数约束、涨跌停/停牌约束、流动性冲击成本；ETF/LOF 价格用 fund NAV 复权。",
                }
            },
        },
        "evidence": {
            "strategy_spec": {
                "strategy_id": STRATEGY_ID,
                "scenario": best["scenario"],
                "symbols": best["symbols"],
                "universe": best["symbols"],
                "risk_category_symbols": best.get("risk_category_symbols", {}),
                "defensive_category_symbols": best.get("defensive_category_symbols", {}),
                "timing_symbol": best.get("timing_symbol", "000300"),
                "universe_construction": "point-in-time ETF category universe; each category representative is the largest ETF by as-of total_netasset/net_asset before momentum scoring",
                "goal": {"cagr_gt": 0.10, "max_drawdown_gte": -0.30},
            }
        },
    }


def _insert_detail_section(html: str, rows: List[Dict[str, Any]], universe: Dict[str, Any]) -> str:
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
        "<h3>场景结果</h3><div class=\"table-wrap\"><table>"
        "<thead><tr><th>场景</th><th>CAGR</th><th>MaxDD</th><th>Sharpe</th><th>Trades</th><th>目标</th></tr></thead>"
        f"<tbody>{grid_rows}</tbody></table></div>"
    )
    grid = f"{_pit_universe_table(universe)}{grid}"
    marker = "<h2>2. 严格回测证据</h2>\n<h3>回测 Equity Curve</h3>"
    replacement = f"<h2>2. 严格回测证据</h2>\n{DETAIL_SECTION}{grid}<h3>回测 Equity Curve</h3>"
    return html.replace(marker, replacement, 1)


def _pit_universe_table(universe: Dict[str, Any]) -> str:
    category_maps = {
        **(universe.get("risk_category_symbols") or {}),
        **(universe.get("defensive_category_symbols") or {}),
    }
    rows = []
    for category, symbols in category_maps.items():
        sample = ", ".join(str(symbol) for symbol in list(symbols)[:8])
        rows.append(
            "<tr>"
            f"<td>{escape(str(category))}</td>"
            f"<td>{len(symbols)}</td>"
            f"<td>{escape(sample)}</td>"
            "<td>按每个调仓日 as-of total_netasset/net_asset 选当时规模最大的 ETF；缺规模数据不入选。</td>"
            "</tr>"
        )
    return (
        "<h3>PIT ETF Universe</h3><div class=\"table-wrap\"><table>"
        "<thead><tr><th>类别</th><th>候选数</th><th>样例</th><th>选择规则</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


if __name__ == "__main__":
    main()
