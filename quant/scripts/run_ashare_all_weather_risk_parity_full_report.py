"""Run the full research report for the A-share all-weather risk-parity candidate."""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import escape
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
    _strict_execution_cost_model,
)
from quant.domain.models.market import is_cn_symbol
from quant.features.backtest.benchmark import BenchmarkProvider
from quant.features.backtest.engine import Backtester
from quant.features.research.models import PurgedWalkForwardResult
from quant.features.research.rigor.backtest_hub import RigorHub
from quant.features.strategies.reject.ashare_all_weather_risk_parity.strategy import (
    AShareAllWeatherRiskParityStrategy,
    DEFAULT_CATEGORY_SYMBOLS,
    DEFAULT_PIT_SIZE_FIELDS,
    DEFAULT_RISK_BUDGETS,
)
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.trading.sub_portfolio import SubPortfolio
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
from quant.infrastructure.research.cn_etf_universe import build_broad_asset_etf_pit_universe, flatten_category_symbols
from quant.infrastructure.research.reporting import build_research_full_report_html, build_research_stage_report_html


START = datetime(2016, 1, 1)
END = datetime(2026, 5, 31)
INITIAL_CASH = 10_000.0
STRATEGY_ID = "ashare_all_weather_risk_parity"
TITLE = "A-share All-Weather Risk Parity"
REPORT_ROOT = Path("quant/infrastructure/var/research/reports")
UNIVERSE_AS_OF = None
UNIVERSE_MIN_HISTORY_DAYS_AS_OF = 0
UNIVERSE_MAX_SYMBOLS_PER_CATEGORY = 0
COMMISSION_CFG = {
    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
    "HK": {"type": "hk_realistic"},
    "CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0},
}
WALKFORWARD_CONFIG = {
    "purged_walkforward": {
        "train_window_days": 252,
        "test_window_days": 63,
        "step_days": 63,
        "purge_days": 5,
        "embargo_days": 21,
        "min_train_observations": 126,
        "parallel_workers": 4,
        "prefetch_data": False,
    },
    "thresholds": {
        "min_worst_oos_sharpe": 0.3,
        "min_profitable_splits_pct": 0.5,
    },
    "cost_model": {
        "max_adv_pct": 0.05,
    },
}
DEGRADATION_THRESHOLD_PCT = 40.0
MIN_PASS_RATIO = 0.60
PARAMETER_KEYS = [
    "risk_budgets",
    "momentum_lookback",
    "trend_window",
    "volatility_window",
    "max_asset_weight",
    "holding_days",
    "min_avg_turnover",
    "target_exposure",
    "trend_guard_enabled",
]


SCENARIOS: List[Dict[str, Any]] = [
    {
        "name": "monthly_63d_vol60_base",
        "category_symbols": {key: list(value) for key, value in DEFAULT_CATEGORY_SYMBOLS.items()},
        "risk_budgets": dict(DEFAULT_RISK_BUDGETS),
        "momentum_lookback": 63,
        "momentum_skip": 1,
        "trend_window": 120,
        "volatility_window": 60,
        "volatility_floor": 0.02,
        "liquidity_window": 20,
        "min_avg_turnover": 20_000_000.0,
        "target_exposure": 0.98,
        "max_asset_weight": 0.45,
        "holding_days": 20,
        "require_pit_size": True,
        "pit_size_fields": list(DEFAULT_PIT_SIZE_FIELDS),
        "trend_guard_enabled": False,
        "risk_exit": {
            "enabled": True,
            "stop_loss_pct": 0.10,
            "take_profit_pct": 0.18,
            "trailing_stop_pct": 0.07,
            "max_holding_days": 90,
            "min_time_stop_return": -0.02,
        },
    },
    {
        "name": "monthly_63d_vol60_trend_guard",
        "category_symbols": {key: list(value) for key, value in DEFAULT_CATEGORY_SYMBOLS.items()},
        "risk_budgets": dict(DEFAULT_RISK_BUDGETS),
        "momentum_lookback": 63,
        "momentum_skip": 1,
        "trend_window": 120,
        "volatility_window": 60,
        "volatility_floor": 0.02,
        "liquidity_window": 20,
        "min_avg_turnover": 20_000_000.0,
        "target_exposure": 0.98,
        "max_asset_weight": 0.45,
        "holding_days": 20,
        "require_pit_size": True,
        "pit_size_fields": list(DEFAULT_PIT_SIZE_FIELDS),
        "trend_guard_enabled": True,
        "risk_exit": {
            "enabled": True,
            "stop_loss_pct": 0.10,
            "take_profit_pct": 0.18,
            "trailing_stop_pct": 0.07,
            "max_holding_days": 90,
            "min_time_stop_return": -0.02,
        },
    },
    {
        "name": "weekly_42d_vol40_base",
        "category_symbols": {key: list(value) for key, value in DEFAULT_CATEGORY_SYMBOLS.items()},
        "risk_budgets": dict(DEFAULT_RISK_BUDGETS),
        "momentum_lookback": 42,
        "momentum_skip": 1,
        "trend_window": 90,
        "volatility_window": 40,
        "volatility_floor": 0.02,
        "liquidity_window": 20,
        "min_avg_turnover": 20_000_000.0,
        "target_exposure": 0.98,
        "max_asset_weight": 0.45,
        "holding_days": 10,
        "require_pit_size": True,
        "pit_size_fields": list(DEFAULT_PIT_SIZE_FIELDS),
        "trend_guard_enabled": False,
        "risk_exit": {
            "enabled": True,
            "stop_loss_pct": 0.10,
            "take_profit_pct": 0.18,
            "trailing_stop_pct": 0.07,
            "max_holding_days": 90,
            "min_time_stop_return": -0.02,
        },
    },
]


def main(argv: List[str] | None = None) -> None:
    args = _parse_args(argv)
    universe = _build_universe()
    scenarios = [_with_pit_universe(scenario, universe) for scenario in SCENARIOS]
    all_symbols = sorted({symbol for scenario in scenarios for symbol in scenario["symbols"]})
    lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit = _load_shared_inputs(all_symbols)
    rows: List[Dict[str, Any]] = []
    strict_reports: Dict[str, Dict[str, Any]] = {}
    for scenario in scenarios:
        print(f"Running {scenario['name']} on {len(scenario['symbols'])} ETFs", flush=True)
        strict_report = _run_one(scenario, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit)
        strict_reports[scenario["name"]] = strict_report
        row = _row_from_strict_report(scenario, strict_report)
        rows.append(row)
        print(json.dumps(_compact_row(row), ensure_ascii=False), flush=True)
    best = _select_best(rows)
    report_path, result_path = _write_outputs(rows, strict_reports, best, universe)
    followups = {}
    if args.run_followups:
        walkforward_payload, walkforward_report = run_walkforward(max_workers=max(1, args.walkforward_workers))
        stability_payload, stability_report = run_stability(max_workers=max(1, args.stability_workers))
        followups = {
            "walkforward": {"payload": walkforward_payload, "report_path": walkforward_report},
            "stability": {"payload": stability_payload, "report_path": stability_report},
        }
        report_path, result_path = _write_outputs(rows, strict_reports, best, universe)
    print(
        json.dumps(
            {
                "strategy_id": STRATEGY_ID,
                "best": _compact_row(best),
                "report_path": str(report_path),
                "result_path": str(result_path),
                "followups": _compact_followup_summary(followups),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


def _parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-followups", dest="run_followups", action="store_false")
    parser.add_argument("--walkforward-workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--stability-workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.set_defaults(run_followups=True)
    return parser.parse_args(argv)


def _build_universe() -> Dict[str, Any]:
    broad = build_broad_asset_etf_pit_universe(
        universe_as_of=UNIVERSE_AS_OF,
        min_history_days_as_of=UNIVERSE_MIN_HISTORY_DAYS_AS_OF,
        max_symbols_per_category=UNIVERSE_MAX_SYMBOLS_PER_CATEGORY,
        universe_start=START,
        universe_end=END,
    )
    source = broad.get("category_symbols") or {}
    category_symbols = {
        "equity": list(dict.fromkeys(source.get("csi300", []) + source.get("sse50", []) + source.get("dividend", []))),
        "gold": list(source.get("gold", [])),
        "bond_rate": list(source.get("bond_rate", [])),
        "cash": list(source.get("cash", [])),
    }
    missing = [category for category, symbols in category_symbols.items() if not symbols]
    if not any(category_symbols.values()):
        raise RuntimeError("Audited all-weather ETF registry universe has no active categories with local PIT data")
    blocked = {"512100", "513100", "513050", "159920", "510900", "513330", "513180", "513130"}
    overlap = sorted(set(flatten_category_symbols(category_symbols)) & blocked)
    if overlap:
        raise RuntimeError(f"Blocked ETF appeared in all-weather default pool: {', '.join(overlap)}")
    return {
        **broad,
        "category_symbols": category_symbols,
        "symbols": flatten_category_symbols(category_symbols),
        "missing_pit_categories": missing,
    }


def _with_pit_universe(scenario: Dict[str, Any], universe: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(scenario)
    active = {key: list(value) for key, value in (universe.get("category_symbols") or {}).items()}
    result["category_symbols"] = active
    result["symbols"] = flatten_category_symbols(active)
    result["missing_pit_categories"] = list(universe.get("missing_pit_categories") or [])
    result["registered_universe_counts"] = dict(universe.get("registered_universe_counts") or {})
    result["universe_registry_version"] = universe.get("universe_registry_version") or "audited_stable_etf_registry_v1"
    result["universe_selection_policy"] = universe.get("universe_selection_policy") or "audited_stable_etf_registry"
    return result


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
    start: datetime = START,
    end: datetime = END,
) -> Dict[str, Any]:
    symbols = list(scenario["symbols"])
    execution_cost_model = _strict_execution_cost_model(
        STRATEGY_ID,
        {
            "name": TITLE,
            "description": "CN-listed all-weather ETF risk-parity allocation",
            "parameters": dict(scenario),
        },
        True,
    )
    data_provider = _DuckDBDailyDateProvider(
        symbols,
        start,
        end,
        include_daily_basic=False,
        include_execution_liquidity_features=True,
    )
    strategy = AShareAllWeatherRiskParityStrategy(**_strategy_kwargs(scenario))
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
            start=start,
            end=end,
            strategies=[strategy],
            initial_cash=INITIAL_CASH,
            data_provider=data_provider,
            symbols=symbols,
        )
    finally:
        data_provider.close()
    benchmark_equity_curve = benchmark_provider.get_benchmark_equity(start, end, INITIAL_CASH) if benchmark_provider else None
    return _strict_backtest_report(
        bt_result,
        start,
        end,
        INITIAL_CASH,
        symbols,
        benchmark_meta,
        lot_sizes,
        strategy,
        benchmark_equity_curve,
        survivorship_audit,
        {**backtest_config, "commission": COMMISSION_CFG},
    )


def _strategy_kwargs(scenario: Dict[str, Any]) -> Dict[str, Any]:
    keys = [
        "category_symbols",
        "risk_budgets",
        "momentum_lookback",
        "momentum_skip",
        "trend_window",
        "volatility_window",
        "volatility_floor",
        "liquidity_window",
        "min_avg_turnover",
        "target_exposure",
        "max_asset_weight",
        "holding_days",
        "require_pit_size",
        "pit_size_fields",
        "trend_guard_enabled",
        "risk_exit",
    ]
    return {key: scenario[key] for key in keys if key in scenario}


def _row_from_strict_report(scenario: Dict[str, Any], strict_report: Dict[str, Any]) -> Dict[str, Any]:
    metrics = strict_report.get("metrics") or {}
    capacity = strict_report.get("capacity") or {}
    diagnostics = strict_report.get("diagnostics") or {}
    execution_cost_bps = strict_report.get("execution_cost_bps") or {}
    return {
        "scenario": scenario["name"],
        "symbols": scenario["symbols"],
        "parameters": _scenario_parameters(scenario),
        "category_symbols": scenario["category_symbols"],
        "risk_budgets": scenario["risk_budgets"],
        "missing_pit_categories": scenario.get("missing_pit_categories", []),
        "registered_universe_counts": scenario.get("registered_universe_counts", {}),
        "universe_registry_version": scenario.get("universe_registry_version", "audited_stable_etf_registry_v1"),
        "universe_selection_policy": scenario.get("universe_selection_policy", "audited_stable_etf_registry"),
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
        "meets_goal": _meets_goal(strict_report) and not scenario.get("missing_pit_categories"),
        "strict_meets_metric_gate": _meets_goal(strict_report),
    }


def _meets_goal(strict_report: Dict[str, Any]) -> bool:
    metrics = strict_report.get("metrics") or {}
    capacity = strict_report.get("capacity") or {}
    cagr = float(metrics.get("cagr") or 0.0)
    max_drawdown = float(metrics.get("max_drawdown_pct") or 0.0)
    total_trades = int(metrics.get("total_trades") or 0)
    max_adv = float(capacity.get("max_adv_participation") or 0.0)
    drawdown_floor = _drawdown_floor_for_cagr(cagr)
    return drawdown_floor is not None and max_drawdown >= drawdown_floor and total_trades > 50 and max_adv <= 0.05


def _drawdown_floor_for_cagr(cagr: float) -> float | None:
    if cagr >= 0.20:
        return -0.50
    if cagr >= 0.15:
        return -0.30
    if cagr >= 0.10:
        return -0.25
    if cagr >= 0.05:
        return -0.15
    return None


def _select_best(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    return max(rows, key=_score_row)


def _score_row(row: Dict[str, Any]) -> Tuple[int, float, float, float, float]:
    cagr = float(row.get("cagr") or 0.0)
    max_dd = float(row.get("max_drawdown_pct") or 0.0)
    max_adv = float(row.get("max_adv_participation") or 0.0)
    return (
        1 if row.get("meets_goal") is True else 0,
        cagr / max(abs(max_dd), 1e-9),
        float(row.get("sharpe") or 0.0),
        cagr,
        -max_adv,
    )


def _scenario_parameters(scenario: Dict[str, Any]) -> Dict[str, Any]:
    excluded = {
        "name",
        "symbols",
        "registered_universe_counts",
        "universe_registry_version",
        "universe_selection_policy",
        "pit_size_fields",
    }
    return {key: value for key, value in scenario.items() if key not in excluded}


def run_walkforward(max_workers: int = 4) -> Tuple[Dict[str, Any], Path]:
    universe = _build_universe()
    base_scenario = _with_pit_universe(SCENARIOS[0], universe)
    symbols = list(base_scenario["symbols"])
    lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit = _load_shared_inputs(symbols)

    def split_runner(_: str, request: Dict[str, Any]) -> Dict[str, Any]:
        return _run_split_replay(base_scenario, request, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit)

    config = _walkforward_config(max_workers=max_workers)
    hub = RigorHub(backtest_runner=split_runner, config=config)
    result = hub.run_walkforward(
        STRATEGY_ID,
        symbols,
        START.date().isoformat(),
        END.date().isoformat(),
        initial_cash=INITIAL_CASH,
    )
    payload = _build_walkforward_payload(result, max_workers=max_workers)
    full_report_path = _write_walkforward_outputs(payload)
    return payload, full_report_path


def _run_split_replay(
    base_scenario: Dict[str, Any],
    request: Dict[str, Any],
    lot_sizes: Dict[str, int],
    benchmark_provider: Any,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
) -> Dict[str, Any]:
    test_start = datetime.strptime(str(request["start"]), "%Y-%m-%d")
    end = datetime.strptime(str(request["end"]), "%Y-%m-%d")
    run_start = datetime.strptime(str(request.get("train_start_date") or request["start"]), "%Y-%m-%d")
    scenario = _clone_scenario(base_scenario)
    scenario["name"] = f"{base_scenario['name']}__wf_{request['start']}_{request['end']}"
    strict_report = _run_one(scenario, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit, start=run_start, end=end)
    all_returns = _returns_from_curve((strict_report.get("equity_curve") or {}).get("strategy") or [])
    oos_returns = _slice_returns(all_returns, test_start, end)
    metrics = _oos_metrics_from_returns(oos_returns)
    max_adv = _safe_float((strict_report.get("capacity") or {}).get("max_adv_participation"))
    total_trades = int(metrics.get("total_trades") or 0)
    return {
        "metrics": metrics,
        "returns": oos_returns,
        "trades": _capacity_proxy_trades(total_trades, max_adv),
        "strict_capacity": strict_report.get("capacity") or {},
        "strict_period": strict_report.get("period"),
    }


def _build_walkforward_payload(result: PurgedWalkForwardResult, max_workers: int = 4) -> Dict[str, Any]:
    thresholds = _walkforward_thresholds()
    splits = [_split_payload(idx, split) for idx, split in enumerate(result.splits, start=1)]
    total_splits = int(result.total_splits or len(splits))
    evaluated_splits = int(result.evaluated_splits or sum(1 for split in splits if split.get("has_trades") is not False))
    no_trade_splits = int(result.no_trade_splits or sum(1 for split in splits if split.get("has_trades") is False))
    verdict = _walkforward_verdict(result)
    return {
        "strategy_id": STRATEGY_ID,
        "run_ts": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "start": START.date().isoformat(),
        "end": END.date().isoformat(),
        "walkforward": {
            "verdict": verdict,
            "is_viable": bool(result.is_viable),
            "reason": "Frozen-parameter purged walk-forward strict replay; zero-trade windows are retained and excluded from aggregate OOS statistics.",
            "method": "Purged walk-forward over the locked base scenario with the same Backtester constraints, CN ETF cost model, PIT universe, and initial cash.",
            "thresholds": thresholds,
            "aggregate_oos_sharpe": float(result.aggregate_oos_sharpe),
            "worst_oos_sharpe": float(result.worst_oos_sharpe),
            "deflated_sharpe_ratio": result.deflated_sharpe_ratio,
            "sharpe_degradation": float(result.sharpe_degradation),
            "pct_profitable_splits": float(result.pct_profitable_splits),
            "capacity_ok": bool(result.capacity_ok),
            "regime_breakdown": dict(result.regime_breakdown or {}),
            "bull_only_warning": bool(result.bull_only_warning),
            "total_splits": total_splits,
            "evaluated_splits": evaluated_splits,
            "no_trade_splits": no_trade_splits,
            "workers": max(1, int(max_workers)),
            "splits": splits,
        },
    }


def run_stability(max_workers: int = 4) -> Tuple[Dict[str, Any], Path]:
    universe = _build_universe()
    base_scenario = _with_pit_universe(SCENARIOS[0], universe)
    variants = _stability_variants(base_scenario)
    all_symbols = sorted({symbol for scenario in variants for symbol in scenario["symbols"]})
    lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit = _load_shared_inputs(all_symbols)
    rows, strict_reports = _run_variants_parallel(variants, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit, max_workers=max_workers)
    parameter_sensitivity = _build_parameter_sensitivity_payload(base_scenario, rows)
    payload, full_report_path = _write_stability_outputs(rows, strict_reports, parameter_sensitivity)
    return payload, full_report_path


def _stability_variants(base_scenario: Dict[str, Any]) -> List[Dict[str, Any]]:
    variants = [_variant(base_scenario, "base_locked")]
    variants.extend(
        [
            _variant(base_scenario, "lookback_42", momentum_lookback=42),
            _variant(base_scenario, "lookback_84", momentum_lookback=84),
            _variant(base_scenario, "vol_40", volatility_window=40),
            _variant(base_scenario, "vol_90", volatility_window=90),
            _variant(base_scenario, "max_weight_35", max_asset_weight=0.35),
            _variant(base_scenario, "max_weight_55", max_asset_weight=0.55),
            _variant(base_scenario, "rebalance_10", holding_days=10),
            _variant(base_scenario, "rebalance_40", holding_days=40),
            _variant(base_scenario, "trend_guard", trend_guard_enabled=True),
        ]
    )
    return variants


def _variant(base_scenario: Dict[str, Any], suffix: str, **updates: Any) -> Dict[str, Any]:
    scenario = _clone_scenario(base_scenario)
    scenario.update(updates)
    scenario["name"] = f"{base_scenario['name']}__{suffix}"
    scenario["stability_variant"] = suffix
    return scenario


def _clone_scenario(base_scenario: Dict[str, Any]) -> Dict[str, Any]:
    scenario = dict(base_scenario)
    scenario["category_symbols"] = {category: list(symbols) for category, symbols in (base_scenario.get("category_symbols") or {}).items()}
    scenario["risk_budgets"] = dict(base_scenario.get("risk_budgets") or {})
    scenario["symbols"] = list(base_scenario.get("symbols") or [])
    scenario["pit_size_fields"] = list(base_scenario.get("pit_size_fields") or [])
    scenario["risk_exit"] = dict(base_scenario.get("risk_exit") or {})
    scenario["missing_pit_categories"] = list(base_scenario.get("missing_pit_categories") or [])
    return scenario


def _run_variants_parallel(
    variants: List[Dict[str, Any]],
    lot_sizes: Dict[str, int],
    benchmark_provider: Any,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
    max_workers: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    workers = min(max(1, max_workers), len(variants))
    if workers <= 1:
        results = [_run_variant(scenario, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit) for scenario in variants]
    else:
        print(f"Running {len(variants)} stability variants with {workers} workers", flush=True)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(
                executor.map(
                    lambda scenario: _run_variant(scenario, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit),
                    variants,
                )
            )
    rows = [row for row, _ in results]
    strict_reports = {row["scenario"]: report for row, report in results if report}
    return rows, strict_reports


def _run_variant(
    scenario: Dict[str, Any],
    lot_sizes: Dict[str, int],
    benchmark_provider: Any,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    try:
        print(f"Running stability variant {scenario['stability_variant']}", flush=True)
        strict_report = _run_one(scenario, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit)
        row = _row_from_strict_report(scenario, strict_report)
        row["name"] = scenario["stability_variant"]
        row["variant"] = scenario["stability_variant"]
        row["parameters"] = _stability_parameters(scenario)
        row["verdict"] = "reference"
        return row, strict_report
    except Exception as exc:
        row = {
            "scenario": scenario["name"],
            "variant": scenario["stability_variant"],
            "parameters": _stability_parameters(scenario),
            "verdict": "fail",
            "error": str(exc),
        }
        return row, {}


def _build_parameter_sensitivity_payload(base_scenario: Dict[str, Any], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    base_variant = next((row for row in rows if row.get("variant") == "base_locked"), rows[0] if rows else {})
    base_score = _stability_score(base_variant)
    variants: List[Dict[str, Any]] = []
    pass_count = 0
    max_degradation = 0.0
    for row in rows:
        degradation = _degradation_pct(base_score, _stability_score(row))
        max_degradation = max(max_degradation, degradation)
        verdict = _variant_verdict(row, degradation)
        if verdict == "pass":
            pass_count += 1
        variant = dict(row)
        variant["degradation_pct"] = degradation
        variant["verdict"] = verdict
        variants.append(variant)
    tested_count = len(variants)
    pass_ratio = pass_count / tested_count if tested_count else 0.0
    if pass_ratio >= MIN_PASS_RATIO and max_degradation <= DEGRADATION_THRESHOLD_PCT:
        status = "pass"
    elif pass_count > 0:
        status = "warn"
    else:
        status = "fail"
    best_variant = max(variants, key=_stability_score) if variants else {}
    return {
        "status": status,
        "method": "One-factor stability sweep around the locked all-weather risk-parity scenario; it does not select new production parameters.",
        "base_params": _stability_parameters(base_scenario),
        "selected_params": _stability_parameters(base_scenario),
        "best_params": best_variant.get("parameters") or {},
        "tested_count": tested_count,
        "pass_count": pass_count,
        "max_degradation_pct": max_degradation,
        "max_degradation_threshold_pct": DEGRADATION_THRESHOLD_PCT,
        "stability_note": f"{pass_count}/{tested_count} variants stayed inside the stability band; better variants are audit evidence only.",
        "rows": variants,
    }


def _write_outputs(
    rows: List[Dict[str, Any]],
    strict_reports: Dict[str, Dict[str, Any]],
    best: Dict[str, Any],
    universe: Dict[str, Any],
) -> Tuple[Path, Path]:
    strategy_dir = REPORT_ROOT / STRATEGY_ID
    runs_dir = strategy_dir / "runs"
    latest_dir = REPORT_ROOT / "latest"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "strategy_id": STRATEGY_ID,
        "run_ts": run_ts,
        "start": START.date().isoformat(),
        "end": END.date().isoformat(),
        "initial_cash": INITIAL_CASH,
        "rows": rows,
        "best": best,
        "pit_universe": universe,
        "strict_reports": strict_reports,
    }
    result_path = strategy_dir / "grid_result.json"
    last_result_path = strategy_dir / "last_result.json"
    result_text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    best_report = strict_reports[str(best["scenario"])]
    last_text = json.dumps(best_report, ensure_ascii=False, indent=2, default=str)
    result_path.write_text(result_text, encoding="utf-8")
    last_result_path.write_text(last_text, encoding="utf-8")
    (runs_dir / f"{run_ts}_grid_result.json").write_text(result_text, encoding="utf-8")
    (runs_dir / f"{run_ts}_result.json").write_text(last_text, encoding="utf-8")
    row = _hypothesis_row(best, best_report)
    _attach_followup_metrics(row, strategy_dir)
    result = {
        "run_id": f"{STRATEGY_ID}_full_report",
        "backtested": len(rows),
        "walkforward_passed": 1 if (row.get("metrics", {}).get("walkforward") or {}).get("is_viable") else 0,
        "rejected": 0,
        "errors": [],
    }
    generated = datetime.now(timezone.utc).isoformat()
    strict_html = build_research_stage_report_html("strict_backtest", result, [row], generated_at=generated)
    full_html = build_research_full_report_html(result, [row], generated_at=generated)
    fast_html = build_research_stage_report_html("fast_research", result, [row], generated_at=generated)
    walk_html = build_research_stage_report_html("walkforward_strict_audit", result, [row], generated_at=generated)
    paths = {
        "strict_backtest_report.html": strict_html,
        "full_research_report.html": full_html,
        "fast_research_report.html": fast_html,
        "walkforward_audit_report.html": walk_html,
    }
    for name, html in paths.items():
        (strategy_dir / name).write_text(html, encoding="utf-8")
        (runs_dir / f"{run_ts}_{name}").write_text(html, encoding="utf-8")
        (latest_dir / name).write_text(html, encoding="utf-8")
    return strategy_dir / "full_research_report.html", result_path


def _attach_followup_metrics(row: Dict[str, Any], strategy_dir: Path) -> None:
    metrics = row.setdefault("metrics", {})
    stability_path = strategy_dir / "stability_result.json"
    if stability_path.exists():
        try:
            stability = json.loads(stability_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            stability = {}
        parameter_sensitivity = stability.get("parameter_sensitivity") if isinstance(stability, dict) else None
        if isinstance(parameter_sensitivity, dict):
            metrics["parameter_sensitivity"] = parameter_sensitivity
    walkforward_path = strategy_dir / "walkforward_result.json"
    if walkforward_path.exists():
        try:
            walkforward = json.loads(walkforward_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            walkforward = {}
        payload = walkforward.get("walkforward") if isinstance(walkforward, dict) else None
        if isinstance(payload, dict):
            metrics["walkforward"] = payload
            stages = metrics.setdefault("research_stage_conclusions", {})
            stages["walkforward_strict_audit"] = {
                "label": "Walk-forward strict audit",
                "verdict": str(payload.get("verdict") or ("pass" if payload.get("is_viable") else "fail")),
                "conclusion": (
                    f"WF aggregate={payload.get('aggregate_oos_sharpe', 'n/a')}; "
                    f"worst={payload.get('worst_oos_sharpe', 'n/a')}; "
                    f"profitable={payload.get('pct_profitable_splits', 'n/a')}."
                ),
                "method": "Persisted purged walk-forward strict audit loaded from walkforward_result.json.",
            }


def _hypothesis_row(best: Dict[str, Any], strict_report: Dict[str, Any]) -> Dict[str, Any]:
    metrics = strict_report.get("metrics") or {}
    cagr = float(metrics.get("cagr") or 0.0)
    max_dd = float(metrics.get("max_drawdown_pct") or 0.0)
    sharpe = float(metrics.get("sharpe") or 0.0)
    metric_gate = bool(best.get("strict_meets_metric_gate"))
    missing_categories = list(best.get("missing_pit_categories") or [])
    verdict = "pass" if metric_gate and not missing_categories else "warn" if metric_gate else "fail"
    status = "candidate" if verdict == "pass" else "needs_more_validation"
    return {
        "strategy_id": STRATEGY_ID,
        "title": f"{TITLE} - {best['scenario']}",
        "source": "local_strategy",
        "status": status,
        "stage": "backtest",
        "decision_reason": _decision_reason(best, metric_gate, missing_categories),
        "thesis": "境内 ETF 全天候组合尝试用黄金、类现金和利率债资产桶分散权益 beta，并用逆波动方法分配资金权重。",
        "metrics": {
            "strict_backtest": strict_report,
            "research_stage_conclusions": {
                "fast_research": {
                    "label": "Fast research",
                    "verdict": "n/a",
                    "conclusion": "ETF 资产配置候选；横截面 Rank IC 不适用于这类资产桶配置策略。",
                    "method": "本地用户确认的全天候策略候选，使用人工审计过的境内 ETF 注册池。",
                },
                "strict_backtest": {
                    "label": "Strict backtest",
                    "verdict": "pass" if metric_gate else "fail",
                    "conclusion": f"严格回测：Sharpe={sharpe:.2f}, CAGR={cagr:.2%}, MaxDD={max_dd:.2%}。",
                    "method": "项目 Backtester 执行 T+1 开盘成交、境内 ETF 佣金、手数、涨跌停、ADV 参与率上限，以及 ETF 净值/规模 PIT 证据检查。",
                },
                "walkforward_strict_audit": {
                    "label": "Walk-forward strict audit",
                    "verdict": "not_run",
                    "conclusion": "持久化 walk-forward payload 可用后，本报告会刷新该审计结果。",
                    "method": "等待后续验证。",
                },
                "final_decision": {
                    "label": "Final decision",
                    "verdict": status,
                    "conclusion": _decision_reason(best, metric_gate, missing_categories),
                    "method": "决策使用当前生产门槛，并显式审计全天候资产桶完整性。",
                },
            },
        },
        "evidence": {
            "local_strategy": True,
            "metadata": {"source": "local_strategy"},
            "strategy_spec": {
                "strategy_id": STRATEGY_ID,
                "strategy_type": "etf_asset_allocation_risk_parity",
                "signal_formula_key": STRATEGY_ID,
                "prediction_direction": "higher_is_better",
                "scenario": best["scenario"],
                "symbols": best["symbols"],
                "universe": best["symbols"],
                "required_fields": [
                    "open",
                    "high",
                    "low",
                    "close",
                    "adj_close",
                    "volume",
                    "turnover",
                    "unit_nav",
                    "adj_nav",
                    "total_netasset",
                    "net_asset",
                ],
                "parameters": best.get("parameters") or {},
                "parameter_explanations": _parameter_explanations(),
                "strategy_logic": _strategy_logic(best),
                "lookback_days": best.get("parameters", {}).get("momentum_lookback"),
                "horizon_days": best.get("parameters", {}).get("holding_days"),
                "execution_lag_days": 1,
                "rebalance_frequency": f"每 {best.get('parameters', {}).get('holding_days', 20)} 个交易日",
                "risk_controls": {
                    "target_exposure": best.get("parameters", {}).get("target_exposure"),
                    "min_adv_value": best.get("parameters", {}).get("min_avg_turnover"),
                    "max_asset_weight": best.get("parameters", {}).get("max_asset_weight"),
                    "trend_guard_enabled": best.get("parameters", {}).get("trend_guard_enabled"),
                },
                "category_symbols": best.get("category_symbols", {}),
                "risk_budgets": best.get("risk_budgets", {}),
                "missing_pit_categories": missing_categories,
                "pit_universe_enabled": True,
                "universe_selection_policy": best.get("universe_selection_policy", "audited_stable_etf_registry"),
                "universe_registry_version": best.get("universe_registry_version", "audited_stable_etf_registry_v1"),
                "registered_universe_counts": best.get("registered_universe_counts", {}),
                "universe_construction": "使用人工审计过的稳定境内 ETF 注册池，并按权益、黄金、利率债和现金四类全天候资产桶归并。",
                "goal": {
                    "checklist": [
                        "最大 ADV 参与率不超过 5%",
                        "总交易次数大于 50",
                        "CAGR/MaxDD 分层门槛",
                        "全天候核心资产桶具备 PIT 数据",
                    ]
                },
            },
        },
    }


def _decision_reason(best: Dict[str, Any], metric_gate: bool, missing_categories: List[str]) -> str:
    if missing_categories:
        return (
            "严格回测指标门槛已经通过，但本地 PIT 候选池缺少全天候组合所需资产桶 "
            f"({', '.join(missing_categories)})，因此候选仍保持 needs_more_validation。"
        )
    if metric_gate:
        return "严格生产指标门槛已经通过；后续仍需人工复核容量、资产桶完整性和组合相关性。"
    return "严格生产指标门槛未通过；继续留在 reject staging。"


def _parameter_explanations() -> Dict[str, str]:
    return {
        "category_symbols": "人工审计后的境内 ETF 资产桶映射，归并为权益、黄金、利率债和现金。",
        "risk_budgets": "每个资产桶的事前目标风险预算，后续会转换成逆波动资金权重。",
        "momentum_lookback": "中期动量观察窗口；会跳过最近若干交易日，用于在每个资产桶内选择代表 ETF。",
        "momentum_skip": "动量计算时跳过的最近交易日数量，用来降低短期反转和成交噪声影响。",
        "trend_window": "启用趋势保护时使用的移动均线窗口，主要约束风险资产暴露。",
        "volatility_window": "计算已实现波动率的回看窗口，用于逆波动仓位分配。",
        "volatility_floor": "波动率分母下限，防止类现金 ETF 因波动率过低拿到过高仓位。",
        "liquidity_window": "流动性过滤使用的平均成交额窗口。",
        "min_avg_turnover": "ETF 进入候选池所需的最低平均成交额门槛。",
        "target_exposure": "组合目标总风险资产暴露上限；剩余资金保留为真实现金。",
        "max_asset_weight": "单只入选 ETF 的资金权重上限。",
        "holding_days": "两次调仓之间间隔的交易日数量。",
        "require_pit_size": "若启用，则信号日必须能看到点时基金净值或规模证据。",
        "trend_guard_enabled": "是否对风险资产启用趋势过滤；默认不约束黄金、现金和债券资产桶。",
        "risk_exit": "每天先检查已持有 ETF 的止损、移动止盈和时间退出，再进入普通调仓逻辑。",
    }


def _strategy_logic(best: Dict[str, Any]) -> Dict[str, Any]:
    params = best.get("parameters") or {}
    category_text = "; ".join(
        f"{category}: {', '.join(str(symbol) for symbol in symbols)}"
        for category, symbols in (best.get("category_symbols") or {}).items()
    )
    missing = ", ".join(str(category) for category in (best.get("missing_pit_categories") or []))
    risk_budgets = params.get("risk_budgets") or best.get("risk_budgets") or {}
    return {
        "core_idea": "把境内 ETF 分成权益、黄金、利率债和现金四类全天候资产桶，先设定各资产桶的风险预算，再用逆波动方法换算成资金权重。",
        "universe": (
            f"只使用人工审计过的稳定境内 ETF 注册池：{category_text}。"
            f"本轮缺少 PIT 证据的资产桶：{missing or '无'}。"
        ),
        "entry_filters": [
            "资产桶和代表 ETF 必须已经登记在人工审计过的境内 ETF 注册池中",
            "调仓日必须有当日可交易行情，不能使用缺失或停牌数据",
            f"{params.get('liquidity_window', 20)} 日平均成交额必须不低于 {float(params.get('min_avg_turnover') or 0):.0f}",
            "启用 require_pit_size 时，信号日必须能看到点时净值或基金规模证据",
            "每个资产桶只从当时可见候选中，选择动量除以波动率后最强的代表 ETF",
        ],
        "ranking_rule": (
            f"每个资产桶内，按 {params.get('momentum_lookback', 63)} 日跳空动量除以 "
            f"max({params.get('volatility_window', 60)} 日年化波动率, {params.get('volatility_floor', 0.02)}) 排名。"
        ),
        "portfolio_construction": (
            f"先把风险预算 {risk_budgets} 除以各桶已实现波动率，再归一到目标暴露 "
            f"{float(params.get('target_exposure') or 0):.0%}，并把单只 ETF 权重限制在 {float(params.get('max_asset_weight') or 0):.0%} 以内。"
        ),
        "rebalance_rule": (
            f"每隔 {int(params.get('holding_days') or 20)} 个交易日，在收盘后重新计算 PIT 可见性、代表 ETF、波动率和目标权重；"
            "订单在下一交易日开盘执行。"
        ),
        "exit_rule": (
            "每天先于调仓门槛检查风险退出：单腿止损、盈利启动后的移动止盈，以及长期亏损持仓的时间退出。"
            "调仓时卖出不再入选的持仓；如果没有资产桶满足条件，组合保留真实现金。"
        ),
        "risk_budget": "风险控制来自资产桶预算、逆波动仓位、单资产上限、波动率下限、点时净值/规模要求、流动性过滤、T+1 执行、手数检查和 ADV 参与率上限。",
        "parameter_explanations": _parameter_explanations(),
    }


def _write_walkforward_outputs(payload: Dict[str, Any]) -> Path:
    strategy_dir = REPORT_ROOT / STRATEGY_ID
    runs_dir = strategy_dir / "runs"
    latest_dir = REPORT_ROOT / "latest"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)
    run_ts = str(payload.get("run_ts") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    result_path = strategy_dir / "walkforward_result.json"
    report_path = strategy_dir / "walkforward_result_report.html"
    payload["walkforward_result_path"] = result_path
    payload["walkforward_report_path"] = report_path
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    result_path.write_text(text, encoding="utf-8")
    (runs_dir / f"{run_ts}_walkforward_result.json").write_text(text, encoding="utf-8")
    html = _walkforward_report_html(payload)
    report_path.write_text(html, encoding="utf-8")
    (runs_dir / f"{run_ts}_walkforward_result_report.html").write_text(html, encoding="utf-8")
    (latest_dir / "walkforward_result.json").write_text(text, encoding="utf-8")
    (latest_dir / "walkforward_result_report.html").write_text(html, encoding="utf-8")
    return _refresh_full_report_from_grid(strategy_dir)


def _write_stability_outputs(
    rows: List[Dict[str, Any]],
    strict_reports: Dict[str, Dict[str, Any]],
    parameter_sensitivity: Dict[str, Any],
) -> Tuple[Dict[str, Any], Path]:
    strategy_dir = REPORT_ROOT / STRATEGY_ID
    runs_dir = strategy_dir / "runs"
    latest_dir = REPORT_ROOT / "latest"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result_path = strategy_dir / "stability_result.json"
    report_path = strategy_dir / "stability_report.html"
    payload = {
        "strategy_id": STRATEGY_ID,
        "run_ts": run_ts,
        "start": START.date().isoformat(),
        "end": END.date().isoformat(),
        "workers_note": "Variants were run in parallel; result ordering follows variant definition order.",
        "parameter_sensitivity": parameter_sensitivity,
        "rows": rows,
        "strict_reports": strict_reports,
        "stability_result_path": result_path,
        "stability_report_path": report_path,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    result_path.write_text(text, encoding="utf-8")
    (runs_dir / f"{run_ts}_stability_result.json").write_text(text, encoding="utf-8")
    html = _stability_report_html(payload)
    report_path.write_text(html, encoding="utf-8")
    (runs_dir / f"{run_ts}_stability_report.html").write_text(html, encoding="utf-8")
    (latest_dir / "stability_result.json").write_text(text, encoding="utf-8")
    (latest_dir / "stability_report.html").write_text(html, encoding="utf-8")
    full_report_path = _refresh_full_report_from_grid(strategy_dir)
    return payload, full_report_path


def _refresh_full_report_from_grid(strategy_dir: Path) -> Path:
    grid_path = strategy_dir / "grid_result.json"
    if not grid_path.exists():
        return strategy_dir / "full_research_report.html"
    payload = json.loads(grid_path.read_text(encoding="utf-8"))
    full_report_path, _ = _write_outputs(
        list(payload.get("rows") or []),
        dict(payload.get("strict_reports") or {}),
        dict(payload.get("best") or {}),
        dict(payload.get("pit_universe") or {}),
    )
    return full_report_path


def _stability_parameters(scenario: Dict[str, Any]) -> Dict[str, Any]:
    return {key: scenario.get(key) for key in PARAMETER_KEYS if key in scenario}


def _stability_score(row: Dict[str, Any]) -> float:
    cagr = _safe_float(row.get("cagr")) or 0.0
    drawdown = abs(_safe_float(row.get("max_drawdown_pct")) or 0.0)
    if cagr <= 0:
        return 0.0
    return cagr / max(drawdown, 0.01)


def _degradation_pct(base_score: float, variant_score: float) -> float:
    if base_score <= 0:
        return 0.0 if variant_score > 0 else 100.0
    return max(0.0, (base_score - variant_score) / base_score * 100.0)


def _variant_verdict(row: Dict[str, Any], degradation_pct: float) -> str:
    cagr = _safe_float(row.get("cagr"))
    total_trades = _safe_int(row.get("total_trades")) or 0
    max_adv = _safe_float(row.get("max_adv_participation"))
    if cagr is None or cagr <= 0 or total_trades <= 30:
        return "fail"
    if max_adv is not None and max_adv > 0.05:
        return "fail"
    if degradation_pct <= DEGRADATION_THRESHOLD_PCT and total_trades > 50:
        return "pass"
    return "warn"


def _walkforward_config(max_workers: int) -> Dict[str, Any]:
    config = {
        "purged_walkforward": dict(WALKFORWARD_CONFIG["purged_walkforward"]),
        "thresholds": dict(WALKFORWARD_CONFIG["thresholds"]),
        "cost_model": dict(WALKFORWARD_CONFIG["cost_model"]),
    }
    config["purged_walkforward"]["parallel_workers"] = max(1, int(max_workers))
    return config


def _walkforward_thresholds() -> Dict[str, Any]:
    config = _walkforward_config(max_workers=1)
    purged = config["purged_walkforward"]
    thresholds = config["thresholds"]
    return {
        "train_window_days": purged["train_window_days"],
        "test_window_days": purged["test_window_days"],
        "step_days": purged["step_days"],
        "purge_days": purged["purge_days"],
        "embargo_days": purged["embargo_days"],
        "min_train_observations": purged["min_train_observations"],
        "min_worst_oos_sharpe": thresholds["min_worst_oos_sharpe"],
        "min_profitable_splits_pct": thresholds["min_profitable_splits_pct"],
        "min_deflated_sharpe_ratio": 0.95,
        "max_adv_pct": config["cost_model"]["max_adv_pct"],
    }


def _walkforward_verdict(result: PurgedWalkForwardResult) -> str:
    if result.is_viable:
        return "pass"
    if result.evaluated_splits > 0 and result.aggregate_oos_sharpe > 0:
        return "warn"
    return "fail"


def _split_payload(idx: int, split: Dict[str, Any]) -> Dict[str, Any]:
    response = split.get("response") if isinstance(split.get("response"), dict) else {}
    metrics = response.get("metrics") if isinstance(response.get("metrics"), dict) else {}
    has_trades = bool(split.get("has_trades", True))
    trade_count = _safe_int(split.get("trade_count"))
    oos_sharpe = _safe_float(split.get("test_sharpe"))
    total_return_value = metrics.get("total_return")
    if total_return_value is None:
        total_return_value = metrics.get("return")
    total_return = _safe_float(total_return_value)
    if has_trades is False or trade_count == 0:
        verdict = "excluded_no_trade"
    elif oos_sharpe is not None and oos_sharpe > 0:
        verdict = "pass"
    else:
        verdict = "fail"
    return {
        "split": idx,
        "train_start": split.get("train_start_date") or split.get("train_start"),
        "train_end": split.get("train_end_date") or split.get("train_end"),
        "test_start": split.get("test_start_date") or split.get("test_start"),
        "test_end": split.get("test_end_date") or split.get("test_end"),
        "parameters": "frozen parameters",
        "oos_sharpe": oos_sharpe,
        "test_sharpe": oos_sharpe,
        "return": total_return,
        "total_return": total_return,
        "cagr": _safe_float(metrics.get("cagr")),
        "max_drawdown": _safe_float(metrics.get("max_drawdown_pct") if metrics.get("max_drawdown_pct") is not None else metrics.get("max_dd")),
        "trade_count": trade_count,
        "has_trades": has_trades,
        "capacity": response.get("strict_capacity") or {},
        "verdict": verdict,
    }


def _returns_from_curve(points: List[Dict[str, Any]]) -> pd.Series:
    rows = []
    for point in points:
        if not isinstance(point, dict):
            continue
        date_text = str(point.get("date") or "")[:10]
        value = _safe_float(point.get("value"))
        if not date_text or value is None:
            continue
        rows.append((pd.Timestamp(date_text), value))
    if len(rows) < 2:
        return pd.Series(dtype=float)
    series = pd.Series({date: value for date, value in rows}).sort_index()
    return series.pct_change(fill_method=None).dropna().astype(float)


def _slice_returns(returns: pd.Series, start: datetime, end: datetime) -> pd.Series:
    if returns.empty:
        return returns
    indexed = returns.copy()
    if not isinstance(indexed.index, pd.DatetimeIndex):
        indexed.index = pd.to_datetime(indexed.index, errors="coerce")
    indexed = indexed.dropna().sort_index()
    return indexed.loc[(indexed.index >= pd.Timestamp(start)) & (indexed.index <= pd.Timestamp(end))].astype(float)


def _oos_metrics_from_returns(returns: pd.Series) -> Dict[str, Any]:
    active_returns = returns.dropna().astype(float)
    if active_returns.empty:
        return {"sharpe": 0.0, "cagr": 0.0, "total_return": 0.0, "max_drawdown_pct": 0.0, "total_trades": 0}
    mean = float(active_returns.mean())
    std = float(active_returns.std(ddof=0))
    sharpe = mean / std * (252.0 ** 0.5) if std > 1e-12 else 0.0
    equity = (1.0 + active_returns).cumprod()
    periods = max(1, len(active_returns))
    final_value = float(equity.iloc[-1])
    cagr = final_value ** (252.0 / periods) - 1.0 if final_value > 0 else -1.0
    drawdown = equity / equity.cummax() - 1.0
    activity_count = int((active_returns.abs() > 1e-12).sum())
    return {
        "sharpe": sharpe,
        "cagr": cagr,
        "total_return": final_value - 1.0,
        "max_drawdown_pct": float(drawdown.min()) if not drawdown.empty else 0.0,
        "total_trades": activity_count,
    }


def _capacity_proxy_trades(total_trades: int, max_adv: float | None) -> List[Dict[str, Any]]:
    if total_trades <= 0:
        return []
    if max_adv is None:
        return [{"trade_value": 0.0, "avg_daily_volume": 1.0, "price": 1.0, "volatility": 0.2}]
    return [{"trade_value": max(0.0, max_adv), "avg_daily_volume": 1.0, "price": 1.0, "volatility": 0.2}]


def _walkforward_report_html(payload: Dict[str, Any]) -> str:
    walkforward = payload["walkforward"]
    summary_rows = "".join(
        f"<tr><td>{escape(label)}</td><td>{escape(str(value))}</td></tr>"
        for label, value in [
            ("verdict", walkforward.get("verdict")),
            ("total_splits", walkforward.get("total_splits")),
            ("evaluated_splits", walkforward.get("evaluated_splits")),
            ("no_trade_splits", walkforward.get("no_trade_splits")),
            ("aggregate_oos_sharpe", _fmt_cell(walkforward.get("aggregate_oos_sharpe"))),
            ("worst_oos_sharpe", _fmt_cell(walkforward.get("worst_oos_sharpe"))),
            ("pct_profitable_splits", _pct_cell(walkforward.get("pct_profitable_splits"))),
            ("capacity_ok", walkforward.get("capacity_ok")),
            ("method", walkforward.get("method")),
        ]
    )
    split_rows = "".join(
        "<tr>"
        f"<td>{escape(str(row.get('split')))}</td>"
        f"<td>{escape(str(row.get('train_start')))} - {escape(str(row.get('train_end')))}</td>"
        f"<td>{escape(str(row.get('test_start')))} - {escape(str(row.get('test_end')))}</td>"
        f"<td>{_fmt_cell(row.get('oos_sharpe'))}</td>"
        f"<td>{_pct_cell(row.get('return'))}</td>"
        f"<td>{escape(str(row.get('trade_count')))}</td>"
        f"<td>{escape(str(row.get('verdict')))}</td>"
        "</tr>"
        for row in walkforward.get("splits", [])
    )
    return _simple_report_html(f"{STRATEGY_ID} Walk-forward Report", "Parallel frozen-parameter purged OOS strict replay.", summary_rows, split_rows)


def _stability_report_html(payload: Dict[str, Any]) -> str:
    sensitivity = payload["parameter_sensitivity"]
    summary_rows = "".join(
        f"<tr><td>{escape(label)}</td><td>{escape(str(value))}</td></tr>"
        for label, value in [
            ("status", sensitivity.get("status")),
            ("tested_count", sensitivity.get("tested_count")),
            ("pass_count", sensitivity.get("pass_count")),
            ("max_degradation_pct", f"{float(sensitivity.get('max_degradation_pct') or 0):.2f}%"),
            ("method", sensitivity.get("method")),
            ("stability_note", sensitivity.get("stability_note")),
        ]
    )
    variant_rows = "".join(
        "<tr>"
        f"<td>{escape(str(row.get('variant') or row.get('name')))}</td>"
        f"<td>{escape(json.dumps(row.get('parameters') or {}, ensure_ascii=False))}</td>"
        f"<td>{_pct_cell(row.get('cagr'))}</td>"
        f"<td>{_pct_cell(row.get('max_drawdown_pct'))}</td>"
        f"<td>{_fmt_cell(row.get('sharpe'))}</td>"
        f"<td>{_pct_cell(row.get('max_adv_participation'))}</td>"
        f"<td>{float(row.get('degradation_pct') or 0):.2f}%</td>"
        f"<td>{escape(str(row.get('verdict')))}</td>"
        "</tr>"
        for row in sensitivity.get("rows", [])
    )
    return _simple_report_html(f"{STRATEGY_ID} Stability Report", "One-factor perturbation audit. This report does not select new production parameters.", summary_rows, variant_rows)


def _simple_report_html(title: str, description: str, summary_rows: str, detail_rows: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
body{{margin:0;background:#f6f3ec;color:#18222b;font-family:"Microsoft YaHei","PingFang SC","Segoe UI",system-ui,sans-serif;line-height:1.6}}
main{{width:min(1180px,calc(100% - 40px));margin:0 auto;padding:36px 0 64px}}
section{{margin:18px 0;padding:22px;background:#fffdfa;border:1px solid #d8dee3}}
h1{{margin:0 0 12px;font-size:34px}}h2{{margin:0 0 12px;font-size:22px}}p{{color:#66727e}}
.table-wrap{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{padding:9px 11px;border-bottom:1px solid #d8dee3;text-align:left;vertical-align:top}}th{{background:#f0ece3}}
</style>
</head>
<body>
<main>
<section><h1>{escape(title)}</h1><p>{escape(description)}</p></section>
<section><h2>Summary</h2><div class="table-wrap"><table><tbody>{summary_rows}</tbody></table></div></section>
<section><h2>Details</h2><div class="table-wrap"><table><tbody>{detail_rows}</tbody></table></div></section>
</main>
</body>
</html>
"""


def _compact_followup_summary(followups: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    summary: Dict[str, Dict[str, Any]] = {}
    for key, item in followups.items():
        payload = item.get("payload") or {}
        if key == "walkforward":
            walkforward = payload.get("walkforward") or {}
            summary[key] = {
                "verdict": walkforward.get("verdict"),
                "total_splits": walkforward.get("total_splits"),
                "evaluated_splits": walkforward.get("evaluated_splits"),
                "report_path": str(item.get("report_path")),
            }
        elif key == "stability":
            sensitivity = payload.get("parameter_sensitivity") or {}
            summary[key] = {
                "status": sensitivity.get("status"),
                "tested_count": sensitivity.get("tested_count"),
                "report_path": str(item.get("report_path")),
            }
    return summary


def _compact_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "scenario": row.get("scenario"),
        "symbols": row.get("symbols"),
        "missing_pit_categories": row.get("missing_pit_categories"),
        "cagr": row.get("cagr"),
        "max_drawdown_pct": row.get("max_drawdown_pct"),
        "sharpe": row.get("sharpe"),
        "calmar_ratio": row.get("calmar_ratio"),
        "total_trades": row.get("total_trades"),
        "cost_drag_pct": row.get("cost_drag_pct"),
        "max_adv_participation": row.get("max_adv_participation"),
        "weighted_effective_bps": row.get("weighted_effective_bps"),
        "median_effective_bps": row.get("median_effective_bps"),
        "meets_goal": row.get("meets_goal"),
        "strict_meets_metric_gate": row.get("strict_meets_metric_gate"),
    }


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pct_cell(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    return f"{number:.2%}"


def _fmt_cell(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    return f"{number:.4f}"


if __name__ == "__main__":
    main()
