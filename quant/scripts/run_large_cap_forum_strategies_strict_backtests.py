"""Run strict backtests for large-cap forum strategy candidates."""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

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
from quant.features.strategies.reject.ashare_alpha158_factor_composite.strategy import (
    AShareAlpha158FactorCompositeStrategy,
)
from quant.features.strategies.reject.ashare_csi300_low_turnover_multifactor.strategy import (
    AShareCsi300LowTurnoverMultifactorStrategy,
)
from quant.features.strategies.reject.ashare_dividend_low_vol_smart_beta.strategy import (
    AShareDividendLowVolSmartBetaStrategy,
)
from quant.features.strategies.reject.ashare_etf_rsrs_momentum_rotation.strategy import (
    AShareEtfRsrsMomentumRotationStrategy,
    DEFAULT_ETF_RSRS_SYMBOLS,
)
from quant.features.strategies.reject.ashare_low_vol_value_momentum.strategy import (
    AShareLowVolValueMomentumStrategy,
)
from quant.features.strategies.reject.ashare_white_horse_market_temperature.strategy import (
    AShareWhiteHorseMarketTemperatureStrategy,
)
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.trading.sub_portfolio import SubPortfolio
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
from quant.infrastructure.research.reporting import build_research_stage_report_html


START = datetime(2016, 1, 1)
END = datetime(2025, 12, 31)
INITIAL_CASH = 20000.0
TIMING_SYMBOL = "000300"
REPORT_ROOT = Path("quant/infrastructure/var/research/reports")
COMMISSION_CFG = {
    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
    "HK": {"type": "hk_realistic"},
    "CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0},
}
CN_DAILY_COST_MODEL = {
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
CN_ETF_COST_MODEL = {
    "enabled": True,
    "name": "cn_etf_liquidity_impact",
    "markets": ["CN"],
    "tick_size": 0.01,
    "half_spread_ticks": 0.5,
    "min_slippage_bps": 5,
    "max_participation_rate": 0.05,
    "impact_coefficient": 0.15,
    "volatility_fallback": 0.015,
    "adv_value_field": "adv20_value",
    "volatility_field": "volatility20",
}


@dataclass(frozen=True)
class CandidateRun:
    strategy_id: str
    title: str
    strategy_class: Type
    asset_class: str
    source_url: str
    thesis: str
    include_financial_indicators: bool = True
    symbols_factory: Optional[Callable[[List[str]], List[str]]] = None


RUNS: List[CandidateRun] = [
    CandidateRun(
        "ashare_csi300_low_turnover_multifactor",
        "CSI300 proxy low-turnover multi-factor",
        AShareCsi300LowTurnoverMultifactorStrategy,
        "stock",
        "https://www.joinquant.com/community/post/detailMobile?postId=63726",
        "Large-cap stocks with stable multi-factor scores can be held with low replacement turnover.",
    ),
    CandidateRun(
        "ashare_alpha158_factor_composite",
        "Alpha158-style transparent large-cap composite",
        AShareAlpha158FactorCompositeStrategy,
        "stock",
        "https://www.joinquant.com/community/post/detailMobile?postId=63726",
        "A transparent subset of alpha158-style price, volatility, liquidity, and value features can proxy the forum Qlib idea without private ML state.",
    ),
    CandidateRun(
        "ashare_etf_rsrs_momentum_rotation",
        "Broad ETF momentum rotation with RSRS timing",
        AShareEtfRsrsMomentumRotationStrategy,
        "etf",
        "https://www.joinquant.com/community/post/detailMobile?postId=41718",
        "ETF rotation should only take risk when index RSRS timing is constructive, then allocate to the strongest risk-adjusted momentum ETFs.",
        include_financial_indicators=False,
        symbols_factory=lambda _: [*DEFAULT_ETF_RSRS_SYMBOLS, TIMING_SYMBOL],
    ),
    CandidateRun(
        "ashare_white_horse_market_temperature",
        "White-horse quality with market temperature",
        AShareWhiteHorseMarketTemperatureStrategy,
        "stock",
        "https://www.joinquant.com/community/post/detailMobile?postId=63052",
        "Quality white-horse companies may preserve large-cap exposure better when market temperature is favorable.",
    ),
    CandidateRun(
        "ashare_low_vol_value_momentum",
        "Large-cap low-vol value momentum",
        AShareLowVolValueMomentumStrategy,
        "stock",
        "https://bigquant.com/square/ai/817ea23e-aa24-8c94-d4f4-5b0b917a234a",
        "Low volatility, value, and medium-term momentum are complementary large-cap premia after turnover and impact costs.",
    ),
    CandidateRun(
        "ashare_dividend_low_vol_smart_beta",
        "Dividend low-volatility smart beta",
        AShareDividendLowVolSmartBetaStrategy,
        "stock",
        "https://bigquant.com/wiki/doc/E0tgJl9RgB",
        "Dividend yield and low volatility can define a slower smart-beta portfolio for non-small-cap A-shares.",
    ),
]


DETAILS = {
    "stock": """
<h3>Strategy execution logic</h3>
<div class="table-wrap"><table><thead><tr><th>Daily step</th><th>Rule</th><th>Signal meaning</th></tr></thead><tbody>
<tr><td>1. Universe</td><td>Start from the historical daily TOP total_mv large-cap union, then filter ST, suspended, non-listed, low-price, and low-liquidity names.</td><td>The test is not limited to current CSI300 constituents; it uses daily PIT market cap to form a broad large-cap proxy.</td></tr>
<tr><td>2. Market state</td><td>Most variants use CSI300 close versus moving average and medium momentum as a risk gate; the low-turnover CSI300 proxy disables this gate to isolate the factor.</td><td>Signals are generated after close and orders execute T+1 at next open.</td></tr>
<tr><td>3. Cross-section score</td><td>Rank eligible stocks by the strategy-specific factor mix: value, low volatility, momentum, dividend, quality, liquidity, or turnover.</td><td>All ranks are same-day PIT fields or historical adjusted-price indicators.</td></tr>
<tr><td>4. Portfolio</td><td>Equal-weight the selected large-cap names with fixed target slots; low-turnover variant rebalances every 20 trading days and replaces at most one holding per rebalance.</td><td>Target slots avoid concentrating exposure when the valid signal set is sparse.</td></tr>
<tr><td>5. Execution</td><td>Backtester enforces CN lot size, T+1, price limits, suspension, stock commission/taxes, dividends/bonus shares, and cn_daily_liquidity_impact.</td><td>The report is a strict execution test, not a vectorized factor chart.</td></tr>
</tbody></table></div>
""",
    "etf": """
<h3>Strategy execution logic</h3>
<div class="table-wrap"><table><thead><tr><th>Daily step</th><th>Rule</th><th>Signal meaning</th></tr></thead><tbody>
<tr><td>1. Universe</td><td>Use CN-listed broad/style/cross-market ETFs and CSI300 index high-low bars for RSRS timing.</td><td>ETF bars come from cn_etf_ohlcv; index bars come from cn_index_ohlcv.</td></tr>
<tr><td>2. RSRS timing</td><td>Regress recent index high on low, standardize beta, multiply by R-squared, and switch risk on/off with entry and exit thresholds.</td><td>Risk is taken only when index high-low structure is constructive.</td></tr>
<tr><td>3. ETF score</td><td>Eligible ETFs need positive trend, sufficient liquidity, and positive risk-adjusted momentum.</td><td>Score favors ETFs with stronger momentum per unit volatility.</td></tr>
<tr><td>4. Portfolio</td><td>Hold the top one or two ETFs at target exposure; keep residual capital as real cash.</td><td>No synthetic cash ETF is added to make returns look smoother.</td></tr>
<tr><td>5. Execution</td><td>Backtester enforces T+1, CN fund commission, lot size, and cn_etf_liquidity_impact.</td><td>Signal close and execution price are separated.</td></tr>
</tbody></table></div>
""",
}


def main() -> None:
    args = _parse_args()
    selected_runs = _selected_runs(args.names)
    stock_symbols, benchmark_provider, benchmark_meta, stock_lot_sizes, stock_survivorship = _load_stock_inputs(
        args.top_market_cap_limit,
        args.historical_large_cap_rank_limit,
    )
    rows = []
    report_paths = {}
    for run in selected_runs:
        symbols = run.symbols_factory(stock_symbols) if run.symbols_factory else [*stock_symbols, TIMING_SYMBOL]
        lot_sizes = stock_lot_sizes if run.asset_class == "stock" else _load_lot_sizes_for_symbols(symbols)
        survivorship = stock_survivorship if run.asset_class == "stock" else {}
        print(f"Running {run.strategy_id} on {len(symbols)} symbols", flush=True)
        strict_report = _run_one(run, symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship)
        report_path, result_path = _write_strategy_report(run, strict_report)
        metrics = strict_report.get("metrics") or {}
        row = {
            "strategy_id": run.strategy_id,
            "title": run.title,
            "report_path": str(report_path),
            "result_path": str(result_path),
            "sharpe": metrics.get("sharpe"),
            "cagr": metrics.get("cagr"),
            "total_return": metrics.get("total_return"),
            "max_drawdown_pct": metrics.get("max_drawdown_pct"),
            "total_trades": metrics.get("total_trades"),
            "meets_goal": _meets_goal(metrics),
        }
        rows.append(row)
        report_paths[run.strategy_id] = str(report_path)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    batch_path = _write_batch_report(rows)
    print(json.dumps({"batch_report": str(batch_path), "rows": rows, "report_paths": report_paths}, ensure_ascii=False, indent=2))


def _load_stock_inputs(
    top_market_cap_limit: Optional[int],
    historical_large_cap_rank_limit: Optional[int],
) -> Tuple[List[str], BenchmarkProvider, Dict[str, Any], Dict[str, int], Dict[str, Any]]:
    db_provider = DuckDBProvider()
    db_provider.connect()
    try:
        if top_market_cap_limit:
            symbols = _load_latest_top_market_cap_symbols(db_provider, top_market_cap_limit)
        elif historical_large_cap_rank_limit:
            symbols = _load_historical_large_cap_symbols(db_provider, historical_large_cap_rank_limit)
        else:
            symbols = _load_ashare_symbols(db_provider)
        all_symbols = [*symbols, TIMING_SYMBOL]
        benchmark_provider, benchmark_meta = _load_cn_benchmark_provider(db_provider, START, END, BenchmarkProvider)
        lot_sizes = _load_lot_sizes(db_provider, all_symbols, is_cn_symbol)
        survivorship = _cn_survivorship_audit(db_provider, START, END, formula_key="large_cap_forum_batch")
    finally:
        db_provider.disconnect()
    return symbols, benchmark_provider, benchmark_meta, lot_sizes, survivorship


def _load_lot_sizes_for_symbols(symbols: List[str]) -> Dict[str, int]:
    db_provider = DuckDBProvider()
    db_provider.connect()
    try:
        return _load_lot_sizes(db_provider, symbols, is_cn_symbol)
    finally:
        db_provider.disconnect()


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


def _load_historical_large_cap_symbols(db_provider: DuckDBProvider, rank_limit: int) -> List[str]:
    if rank_limit <= 0:
        raise ValueError("historical_large_cap_rank_limit must be positive")
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
        [START, END, TIMING_SYMBOL, int(rank_limit), START, END],
    ).fetchall()
    return [str(row[0]) for row in rows]


def _run_one(
    run: CandidateRun,
    symbols: List[str],
    lot_sizes: Dict[str, int],
    benchmark_provider: BenchmarkProvider,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
) -> Dict[str, Any]:
    execution_cost_model = dict(CN_ETF_COST_MODEL if run.asset_class == "etf" else CN_DAILY_COST_MODEL)
    data_provider = _DuckDBDailyDateProvider(
        symbols,
        START,
        END,
        include_daily_basic=run.asset_class == "stock",
        include_financial_indicators=run.include_financial_indicators,
        include_execution_liquidity_features=True,
    )
    strategy = run.strategy_class(symbols=symbols)
    backtest_config = {"slippage_bps": 5, "execution_cost_model": execution_cost_model}
    bt_config = {
        "backtest": backtest_config,
        "execution": {"commission": COMMISSION_CFG},
        "data": {"default_timeframe": "1d"},
        "risk": {"max_position_pct": 1.0, "max_sector_pct": 1.0, "max_daily_loss_pct": 0.10, "max_leverage": 2.0},
    }
    if run.asset_class == "etf":
        bt_config["risk"]["max_leverage"] = 1.0
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


def _write_strategy_report(run: CandidateRun, strict_report: Dict[str, Any]) -> Tuple[Path, Path]:
    strategy_dir = REPORT_ROOT / run.strategy_id
    strategy_dir.mkdir(parents=True, exist_ok=True)
    result_path = strategy_dir / "last_result.json"
    result_path.write_text(json.dumps(strict_report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    row = _hypothesis_row(run, strict_report)
    result = {"run_id": f"{run.strategy_id}_strict", "backtested": 1, "rejected": 0, "errors": []}
    html = build_research_stage_report_html("strict_backtest", result, [row], generated_at=datetime.now(timezone.utc).isoformat())
    html = _insert_detail_section(html, DETAILS[run.asset_class])
    report_path = strategy_dir / "strict_backtest_report.html"
    report_path.write_text(html, encoding="utf-8")
    run_dir = strategy_dir / "runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (run_dir / f"{timestamp}_strict_backtest_report.html").write_text(html, encoding="utf-8")
    return report_path, result_path


def _hypothesis_row(run: CandidateRun, strict_report: Dict[str, Any]) -> Dict[str, Any]:
    metrics = strict_report.get("metrics") or {}
    cagr = float(metrics.get("cagr") or 0.0)
    max_dd = float(metrics.get("max_drawdown_pct") or 0.0)
    sharpe = float(metrics.get("sharpe") or 0.0)
    verdict = "pass" if _meets_goal(metrics) else ("warn" if cagr > 0 and sharpe > 0 else "fail")
    method = (
        "Project Backtester; close signal, T+1 next-open execution, CN fund fees, lot size, price-limit/suspension checks, and cn_etf_liquidity_impact."
        if run.asset_class == "etf"
        else "Project Backtester; close signal, T+1 next-open execution, CN stock commission/taxes, lot size, price-limit/suspension checks, dividends, and cn_daily_liquidity_impact."
    )
    return {
        "strategy_id": run.strategy_id,
        "title": run.title,
        "status": "needs_walkforward_validation" if verdict == "pass" else "needs_more_research",
        "metrics": {
            "strict_backtest": strict_report,
            "research_stage_conclusions": {
                "strict_backtest": {
                    "label": "strict backtest",
                    "verdict": verdict,
                    "conclusion": f"Strict backtest: Sharpe={sharpe:.2f}, CAGR={cagr:.2%}, MaxDD={max_dd:.2%}.",
                    "method": method,
                }
            },
        },
        "evidence": {
            "strategy_spec": {
                "strategy_id": run.strategy_id,
                "source_url": run.source_url,
                "thesis": run.thesis,
                "start": START.date().isoformat(),
                "end": END.date().isoformat(),
                "asset_class": run.asset_class,
                "goal": {"cagr_gt": 0.10, "max_drawdown_gte": -0.40},
            }
        },
    }


def _write_batch_report(rows: List[Dict[str, Any]]) -> Path:
    report_dir = REPORT_ROOT / "ashare_large_cap_forum_batch"
    report_dir.mkdir(parents=True, exist_ok=True)
    sorted_rows = sorted(
        rows,
        key=lambda item: (
            bool(item.get("meets_goal")),
            float(item.get("cagr") or 0.0) / max(abs(float(item.get("max_drawdown_pct") or 0.0)), 1e-9),
            float(item.get("sharpe") or 0.0),
        ),
        reverse=True,
    )
    result_path = report_dir / "batch_result.json"
    result_path.write_text(json.dumps({"rows": sorted_rows}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    table_rows = "\n".join(
        "<tr>"
        f"<td>{row['strategy_id']}</td>"
        f"<td>{float(row.get('cagr') or 0.0):.2%}</td>"
        f"<td>{float(row.get('max_drawdown_pct') or 0.0):.2%}</td>"
        f"<td>{float(row.get('sharpe') or 0.0):.2f}</td>"
        f"<td>{int(row.get('total_trades') or 0)}</td>"
        f"<td>{'pass' if row.get('meets_goal') else 'watch'}</td>"
        f"<td><code>{row['report_path']}</code></td>"
        "</tr>"
        for row in sorted_rows
    )
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Large-cap forum strict backtest batch</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 32px; color: #172033; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #d7dce5; padding: 8px 10px; text-align: left; }}
th {{ background: #f4f6f8; }}
code {{ white-space: nowrap; }}
</style></head>
<body>
<h1>Large-cap forum strict backtest batch</h1>
<p>Period: {START.date().isoformat()} to {END.date().isoformat()}; initial cash: {INITIAL_CASH:,.0f}; all rows are strict Backtester outputs.</p>
<table><thead><tr><th>Strategy</th><th>CAGR</th><th>MaxDD</th><th>Sharpe</th><th>Trades</th><th>Goal</th><th>Report</th></tr></thead><tbody>
{table_rows}
</tbody></table>
</body></html>"""
    report_path = report_dir / "strict_backtest_report.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path


def _insert_detail_section(html: str, details: str) -> str:
    marker = "<h3>回测 Equity Curve</h3>"
    if marker in html:
        return html.replace(marker, f"{details}{marker}", 1)
    fallback = "<h3>Backtest Equity Curve</h3>"
    if fallback in html:
        return html.replace(fallback, f"{details}{fallback}", 1)
    return html + details


def _meets_goal(metrics: Dict[str, Any]) -> bool:
    return float(metrics.get("cagr") or 0.0) > 0.10 and float(metrics.get("max_drawdown_pct") or 0.0) >= -0.40


def _parse_args():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--names", nargs="*", default=None, help="Strategy ids to run. Defaults to all.")
    parser.add_argument(
        "--top-market-cap-limit",
        type=int,
        default=None,
        help="Optional latest market-cap top-N universe for faster exploratory strict runs.",
    )
    parser.add_argument(
        "--historical-large-cap-rank-limit",
        type=int,
        default=800,
        help="Daily total_mv rank limit used to build the historical large-cap candidate-universe union. Use 0 for full A-share universe.",
    )
    return parser.parse_args()


def _selected_runs(names: Optional[List[str]]) -> List[CandidateRun]:
    if not names:
        return list(RUNS)
    wanted = set(names)
    selected = [run for run in RUNS if run.strategy_id in wanted]
    missing = sorted(wanted - {run.strategy_id for run in selected})
    if missing:
        raise SystemExit(f"Unknown strategy ids: {', '.join(missing)}")
    return selected


if __name__ == "__main__":
    main()
