"""Run full research report for the A-share sector ETF momentum rotation strategy."""

from __future__ import annotations

import json
import shutil
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
from quant.features.strategies.reject.ashare_sector_etf_momentum_rotation.strategy import (
    AShareSectorEtfMomentumRotationStrategy,
    DEFAULT_SECTOR_CATEGORY_SYMBOLS,
)
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.trading.sub_portfolio import SubPortfolio
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
from quant.infrastructure.research.reporting import build_research_full_report_html, build_research_stage_report_html


START = datetime(2026, 1, 5)
END = datetime(2026, 6, 5)
INITIAL_CASH = 10_000.0
STRATEGY_ID = "ashare_sector_etf_momentum_rotation"
TITLE = "A-share Sector ETF Momentum Rotation"
REPORT_ROOT = Path("quant/infrastructure/var/research/reports")
STRATEGY_DIR = Path("quant/features/strategies/reject") / STRATEGY_ID
COMMISSION_CFG = {
    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
    "HK": {"type": "hk_realistic"},
    "CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0},
}


SCENARIOS: List[Dict[str, Any]] = [
    {
        "name": "monthly_60d_trend60_vol20_top3_95pct",
        "category_symbols": DEFAULT_SECTOR_CATEGORY_SYMBOLS,
        "momentum_lookback": 60,
        "momentum_skip": 1,
        "trend_window": 60,
        "volatility_window": 20,
        "liquidity_window": 20,
        "min_avg_turnover": 20_000_000.0,
        "min_momentum": 0.02,
        "max_positions": 3,
        "max_category_weight": 0.35,
        "target_exposure": 0.95,
        "holding_days": 20,
        "volatility_floor": 0.01,
    },
    {
        "name": "monthly_40d_trend40_vol20_top3_95pct",
        "category_symbols": DEFAULT_SECTOR_CATEGORY_SYMBOLS,
        "momentum_lookback": 40,
        "momentum_skip": 1,
        "trend_window": 40,
        "volatility_window": 20,
        "liquidity_window": 20,
        "min_avg_turnover": 20_000_000.0,
        "min_momentum": 0.02,
        "max_positions": 3,
        "max_category_weight": 0.35,
        "target_exposure": 0.95,
        "holding_days": 20,
        "volatility_floor": 0.01,
    },
    {
        "name": "biweekly_40d_trend40_vol20_top3_90pct",
        "category_symbols": DEFAULT_SECTOR_CATEGORY_SYMBOLS,
        "momentum_lookback": 40,
        "momentum_skip": 1,
        "trend_window": 40,
        "volatility_window": 20,
        "liquidity_window": 20,
        "min_avg_turnover": 20_000_000.0,
        "min_momentum": 0.02,
        "max_positions": 3,
        "max_category_weight": 0.35,
        "target_exposure": 0.90,
        "holding_days": 10,
        "volatility_floor": 0.01,
    },
]


def main() -> None:
    scenarios = [_prepare_scenario(scenario) for scenario in SCENARIOS]
    all_symbols = sorted({symbol for scenario in scenarios for symbol in scenario["symbols"]})
    lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit = _load_shared_inputs(all_symbols)
    rows: List[Dict[str, Any]] = []
    strict_reports: Dict[str, Dict[str, Any]] = {}
    for scenario in scenarios:
        print(f"Running {scenario['name']} on {len(scenario['symbols'])} sector ETFs", flush=True)
        strict_report = _run_one(scenario, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit)
        strict_reports[scenario["name"]] = strict_report
        row = _scenario_row(scenario, strict_report)
        rows.append(row)
        print(json.dumps(_compact_row(row), ensure_ascii=False), flush=True)
    best = _select_best(rows)
    report_path, result_path = _write_outputs(rows, strict_reports, best)
    print(
        json.dumps(
            {
                "strategy_id": STRATEGY_ID,
                "best": best["scenario"],
                "report_path": str(report_path),
                "result_path": str(result_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


def _prepare_scenario(scenario: Dict[str, Any]) -> Dict[str, Any]:
    prepared = dict(scenario)
    prepared["category_symbols"] = {
        str(category): list(symbols)
        for category, symbols in (scenario.get("category_symbols") or {}).items()
    }
    prepared["symbols"] = sorted({symbol for symbols in prepared["category_symbols"].values() for symbol in symbols})
    return prepared


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
            "description": "CN-listed sector ETF risk-adjusted momentum rotation",
            "parameters": {"symbols": symbols, **_scenario_parameters(scenario)},
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
    strategy = AShareSectorEtfMomentumRotationStrategy(
        category_symbols=scenario["category_symbols"],
        momentum_lookback=int(scenario["momentum_lookback"]),
        momentum_skip=int(scenario["momentum_skip"]),
        trend_window=int(scenario["trend_window"]),
        volatility_window=int(scenario["volatility_window"]),
        liquidity_window=int(scenario["liquidity_window"]),
        min_avg_turnover=float(scenario["min_avg_turnover"]),
        min_momentum=float(scenario["min_momentum"]),
        max_positions=int(scenario["max_positions"]),
        max_category_weight=float(scenario["max_category_weight"]),
        target_exposure=float(scenario["target_exposure"]),
        holding_days=int(scenario["holding_days"]),
        volatility_floor=float(scenario["volatility_floor"]),
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


def _scenario_parameters(scenario: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: scenario[key]
        for key in (
            "momentum_lookback",
            "momentum_skip",
            "trend_window",
            "volatility_window",
            "liquidity_window",
            "min_avg_turnover",
            "min_momentum",
            "max_positions",
            "max_category_weight",
            "target_exposure",
            "holding_days",
            "volatility_floor",
        )
    }


def _scenario_row(scenario: Dict[str, Any], strict_report: Dict[str, Any]) -> Dict[str, Any]:
    metrics = strict_report.get("metrics") or {}
    capacity = strict_report.get("capacity") or {}
    diagnostics = strict_report.get("diagnostics") or {}
    execution_cost_bps = strict_report.get("execution_cost_bps") or {}
    return {
        "scenario": scenario["name"],
        "symbols": list(scenario["symbols"]),
        "category_symbols": scenario["category_symbols"],
        "parameters": _scenario_parameters(scenario),
        "sharpe": metrics.get("sharpe"),
        "cagr": metrics.get("cagr"),
        "total_return": metrics.get("total_return"),
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "calmar_ratio": metrics.get("calmar_ratio"),
        "total_trades": metrics.get("total_trades"),
        "cost_drag_pct": diagnostics.get("cost_drag_pct"),
        "max_adv_participation": capacity.get("max_adv_participation"),
        "weighted_effective_bps": execution_cost_bps.get("weighted_effective_bps"),
        "median_effective_bps": execution_cost_bps.get("median_effective_bps"),
        "meets_goal": _meets_goal(strict_report),
    }


def _meets_goal(strict_report: Dict[str, Any]) -> bool:
    metrics = strict_report.get("metrics") or {}
    capacity = strict_report.get("capacity") or {}
    return (
        float(metrics.get("cagr") or 0.0) > 0.10
        and float(metrics.get("max_drawdown_pct") or 0.0) >= -0.30
        and int(metrics.get("total_trades") or 0) >= 20
        and float(capacity.get("max_adv_participation") or 0.0) <= 0.05
    )


def _select_best(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    return max(
        rows,
        key=lambda row: (
            float(row.get("cagr") or 0.0) / max(abs(float(row.get("max_drawdown_pct") or 0.0)), 1e-9),
            float(row.get("sharpe") or 0.0),
            -abs(float(row.get("cost_drag_pct") or 0.0)),
        ),
    )


def _write_outputs(
    rows: List[Dict[str, Any]],
    strict_reports: Dict[str, Dict[str, Any]],
    best: Dict[str, Any],
) -> Tuple[Path, Path]:
    strategy_dir = REPORT_ROOT / STRATEGY_ID
    runs_dir = strategy_dir / "runs"
    latest_dir = REPORT_ROOT / "latest"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    walkforward = _blocked_walkforward_payload()
    stability = _blocked_stability_payload(rows)
    payload = {
        "strategy_id": STRATEGY_ID,
        "run_ts": run_ts,
        "start": START.date().isoformat(),
        "end": END.date().isoformat(),
        "initial_cash": INITIAL_CASH,
        "rows": rows,
        "best": best,
        "strict_reports": strict_reports,
        "walkforward": walkforward,
        "parameter_sensitivity": stability["parameter_sensitivity"],
    }
    result_path = strategy_dir / "grid_result.json"
    last_result_path = strategy_dir / "last_result.json"
    walkforward_path = strategy_dir / "walkforward_result.json"
    stability_path = strategy_dir / "stability_result.json"
    result_text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    last_text = json.dumps(strict_reports[str(best["scenario"])], ensure_ascii=False, indent=2, default=str)
    result_path.write_text(result_text, encoding="utf-8")
    last_result_path.write_text(last_text, encoding="utf-8")
    walkforward_path.write_text(json.dumps({"walkforward": walkforward}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    stability_path.write_text(json.dumps(stability, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (runs_dir / f"{run_ts}_grid_result.json").write_text(result_text, encoding="utf-8")
    (runs_dir / f"{run_ts}_result.json").write_text(last_text, encoding="utf-8")

    row = _hypothesis_row(best, strict_reports[str(best["scenario"])], walkforward, stability)
    result = {
        "run_id": f"{STRATEGY_ID}_full_report",
        "backtested": len(rows),
        "walkforward_passed": 0,
        "rejected": 0,
        "errors": ["sector ETF local history is too short for promotion-grade walk-forward"],
    }
    generated = datetime.now(timezone.utc).isoformat()
    strict_html = build_research_stage_report_html("strict_backtest", result, [row], generated_at=generated)
    walk_html = build_research_stage_report_html("walkforward_strict_audit", result, [row], generated_at=generated)
    fast_html = build_research_stage_report_html("fast_research", result, [row], generated_at=generated)
    full_html = build_research_full_report_html(result, [row], generated_at=generated)
    strict_report_path = strategy_dir / "strict_backtest_report.html"
    full_report_path = strategy_dir / "full_research_report.html"
    strict_report_path.write_text(strict_html, encoding="utf-8")
    full_report_path.write_text(full_html, encoding="utf-8")
    (strategy_dir / "walkforward_audit_report.html").write_text(walk_html, encoding="utf-8")
    (strategy_dir / "fast_research_report.html").write_text(fast_html, encoding="utf-8")
    (runs_dir / f"{run_ts}_strict_backtest_report.html").write_text(strict_html, encoding="utf-8")
    (runs_dir / f"{run_ts}_full_research_report.html").write_text(full_html, encoding="utf-8")
    (runs_dir / f"{run_ts}_walkforward_audit_report.html").write_text(walk_html, encoding="utf-8")
    (runs_dir / f"{run_ts}_fast_research_report.html").write_text(fast_html, encoding="utf-8")
    (latest_dir / "strict_backtest_report.html").write_text(strict_html, encoding="utf-8")
    (latest_dir / "full_research_report.html").write_text(full_html, encoding="utf-8")
    (latest_dir / "walkforward_audit_report.html").write_text(walk_html, encoding="utf-8")
    (latest_dir / "fast_research_report.html").write_text(fast_html, encoding="utf-8")
    STRATEGY_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(full_report_path, STRATEGY_DIR / "full_research_report.html")
    shutil.copyfile(strict_report_path, STRATEGY_DIR / "strict_backtest_report.html")
    return full_report_path, result_path


def _blocked_walkforward_payload() -> Dict[str, Any]:
    return {
        "verdict": "fail",
        "is_viable": False,
        "reason": "blocked_insufficient_sector_etf_history",
        "total_splits": 0,
        "evaluated_splits": 0,
        "no_trade_splits": 0,
        "aggregate_oos_sharpe": 0.0,
        "worst_oos_sharpe": 0.0,
        "pct_profitable_splits": 0.0,
        "splits": [],
        "method": "Walk-forward intentionally blocked because local sector ETF bars only cover 2026-01-05 to 2026-06-05 for most symbols.",
    }


def _blocked_stability_payload(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    variants = []
    for row in rows:
        variants.append(
            {
                "name": row["scenario"],
                "parameters": row.get("parameters") or {},
                "cagr": row.get("cagr"),
                "max_drawdown_pct": row.get("max_drawdown_pct"),
                "sharpe": row.get("sharpe"),
                "max_adv_participation": row.get("max_adv_participation"),
                "verdict": "warning",
            }
        )
    return {
        "parameter_sensitivity": {
            "verdict": "warning",
            "stability_note": "Only short-window parameter scenarios were run; this is not promotion-grade stability.",
            "rows": variants,
            "max_scenario_count": len(variants),
            "reason": "blocked_insufficient_sector_etf_history",
        }
    }


def _hypothesis_row(
    best: Dict[str, Any],
    strict_report: Dict[str, Any],
    walkforward: Dict[str, Any],
    stability: Dict[str, Any],
) -> Dict[str, Any]:
    metrics = strict_report.get("metrics") or {}
    cagr = float(metrics.get("cagr") or 0.0)
    max_dd = float(metrics.get("max_drawdown_pct") or 0.0)
    sharpe = float(metrics.get("sharpe") or 0.0)
    status = "needs_more_validation"
    return {
        "strategy_id": STRATEGY_ID,
        "title": f"{TITLE} - {best['scenario']}",
        "source": "local_strategy",
        "status": status,
        "stage": "backtest",
        "decision_reason": "No-Go: strict sanity backtest ran, but local sector ETF history is too short for promotion-grade walk-forward and stability evidence.",
        "thesis": "A-share sector leadership tends to persist over intermediate horizons; ranking liquid CN-listed sector ETFs by risk-adjusted momentum may capture industry rotation while avoiding single-stock execution complexity.",
        "metrics": {
            "strict_backtest": strict_report,
            "walkforward": walkforward,
            "parameter_sensitivity": stability["parameter_sensitivity"],
            "research_stage_conclusions": {
                "fast_research": {
                    "label": "Fast research",
                    "verdict": "n/a",
                    "conclusion": "ETF timing/rotation does not use cross-sectional Rank IC as the production gate.",
                    "method": "Public reference review plus local strategy implementation.",
                },
                "strict_backtest": {
                    "label": "Strict backtest",
                    "verdict": "warn" if cagr > 0 else "fail",
                    "conclusion": f"Strict sanity backtest: Sharpe={sharpe:.2f}, CAGR={cagr:.2%}, MaxDD={max_dd:.2%}.",
                    "method": "Project Backtester with close signal, T+1 next-open execution, CN ETF commission, lot size, suspension/limit checks, and cn_etf_liquidity_impact.",
                },
                "walkforward_strict_audit": {
                    "label": "Walk-forward strict audit",
                    "verdict": "fail",
                    "conclusion": "Blocked by insufficient local sector ETF history; no promotion-grade OOS splits were run.",
                    "method": "Persisted blocked walkforward_result.json for audit traceability.",
                },
                "final_decision": {
                    "label": "Final Decision",
                    "verdict": status,
                    "conclusion": "Keep as candidate/reject strategy. Do not enable paper or live until longer PIT ETF history supports walk-forward and stability.",
                    "method": "Decision uses strict report plus explicit data coverage and bias audit warnings.",
                },
            },
        },
        "evidence": {
            "local_strategy": True,
            "metadata": {"source": "local_strategy"},
            "strategy_spec": {
                "strategy_id": STRATEGY_ID,
                "strategy_type": "cn_sector_etf_rotation",
                "signal_formula_key": STRATEGY_ID,
                "prediction_direction": "higher_is_better",
                "scenario": best["scenario"],
                "symbols": best["symbols"],
                "universe": best["symbols"],
                "category_symbols": best.get("category_symbols") or {},
                "required_fields": ["open", "high", "low", "close", "adj_close", "volume", "turnover"],
                "parameters": best.get("parameters") or {},
                "parameter_explanations": _parameter_explanations(),
                "strategy_logic": _strategy_logic(best),
                "lookback_days": (best.get("parameters") or {}).get("momentum_lookback"),
                "horizon_days": (best.get("parameters") or {}).get("holding_days"),
                "execution_lag_days": 1,
                "rebalance_frequency": f"Every {(best.get('parameters') or {}).get('holding_days', 20)} trading days",
                "fallback_symbol": "actual_cash",
                "risk_controls": {
                    "target_exposure": (best.get("parameters") or {}).get("target_exposure"),
                    "max_category_weight": (best.get("parameters") or {}).get("max_category_weight"),
                    "min_avg_turnover": (best.get("parameters") or {}).get("min_avg_turnover"),
                    "trend_window": (best.get("parameters") or {}).get("trend_window"),
                    "min_momentum": (best.get("parameters") or {}).get("min_momentum"),
                },
                "universe_construction": "CN-listed sector/theme ETF categories from a fixed, predeclared candidate pool; cross-border ETFs are excluded.",
                "references": [
                    "https://bigquant.com/wiki/doc/DlXVSO3ZVu",
                    "https://bigquant.com/square/paper/bb877968-2f1a-438a-bae8-6a89bdc04d08",
                    "https://bigquant.com/square/paper/4ad7a69b-c6b0-4658-8c7e-5f395659576c",
                    "https://onlinelibrary.wiley.com/doi/pdf/10.1111/0022-1082.00146",
                ],
                "goal": {
                    "checklist": [
                        "CAGR > 10%",
                        "MaxDD >= -30%",
                        "total_trades >= 20",
                        "max_adv_participation <= 5%",
                        "walk-forward not blocked",
                    ]
                },
            },
        },
    }


def _parameter_explanations() -> Dict[str, str]:
    return {
        "category_symbols": "Predeclared CN-listed sector ETF category map; one representative per category can be selected.",
        "momentum_lookback": "Historical adjusted-close return window used for intermediate sector momentum.",
        "momentum_skip": "Recent bars skipped in momentum to reduce same-day close noise.",
        "trend_window": "Adjusted-close moving average filter; candidates below trend are excluded.",
        "volatility_window": "Annualized volatility denominator for risk-adjusted momentum.",
        "liquidity_window": "Average turnover lookback.",
        "min_avg_turnover": "Minimum cash turnover filter.",
        "min_momentum": "Minimum raw momentum required before ranking.",
        "max_positions": "Maximum number of sector categories held.",
        "max_category_weight": "Per-sector ETF weight cap.",
        "target_exposure": "Total target ETF exposure before lot rounding.",
        "holding_days": "Rebalance gate in trading days.",
    }


def _strategy_logic(best: Dict[str, Any]) -> Dict[str, Any]:
    params = best.get("parameters") or {}
    return {
        "core_idea": "Sector leadership often persists over intermediate horizons; hold the strongest liquid sector ETFs while avoiding stale, weak, or below-trend candidates.",
        "universe": f"{len(best.get('symbols') or [])} CN-listed sector ETFs from predeclared categories; cross-border ETFs excluded.",
        "entry_filters": [
            "same-day bar exists",
            f"{params.get('liquidity_window')} day average turnover >= {params.get('min_avg_turnover')}",
            f"close above {params.get('trend_window')} day moving average",
            f"{params.get('momentum_lookback')} day momentum >= {params.get('min_momentum')}",
        ],
        "ranking_rule": "For each category choose the highest momentum/volatility representative, then hold the top scored categories.",
        "portfolio_construction": f"Top {params.get('max_positions')} categories, capped at {float(params.get('max_category_weight') or 0.0):.0%} each, total target exposure {float(params.get('target_exposure') or 0.0):.0%}.",
        "rebalance_rule": "Signals are generated after the close; project execution submits orders for next-step execution in strict backtest.",
        "exit_rule": "Held ETFs are checked daily and sold when stale, below trend, or non-positive risk-adjusted momentum.",
        "risk_budget": "Long-only ETF rotation with no leverage, lot-size rounding, cash fallback, commission, and liquidity impact.",
    }


def _compact_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "scenario": row.get("scenario"),
        "cagr": row.get("cagr"),
        "max_drawdown_pct": row.get("max_drawdown_pct"),
        "sharpe": row.get("sharpe"),
        "total_trades": row.get("total_trades"),
        "cost_drag_pct": row.get("cost_drag_pct"),
        "max_adv_participation": row.get("max_adv_participation"),
        "meets_goal": row.get("meets_goal"),
    }


if __name__ == "__main__":
    main()
