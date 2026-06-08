"""Run parallel stability audit for the A-share broad asset ETF rotation strategy."""

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

_project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_project_root))

from quant.scripts import run_ashare_broad_asset_etf_rotation_strict_backtest as strict_runner


DEGRADATION_THRESHOLD_PCT = 40.0
MIN_PASS_RATIO = 0.60
PARAMETER_KEYS = [
    "momentum_lookback",
    "trend_window",
    "volatility_window",
    "tilt_strength",
    "temperature",
    "min_branch_weight",
    "max_branch_weight",
    "rebalance_threshold",
    "trend_penalty",
    "holding_days",
    "min_avg_turnover",
    "target_exposure",
]


def main(argv: List[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    args = parser.parse_args(argv)

    payload, full_report_path = run_stability(max_workers=max(1, args.workers))
    print(
        json.dumps(
            {
                "strategy_id": strict_runner.STRATEGY_ID,
                "tested_count": payload["parameter_sensitivity"]["tested_count"],
                "status": payload["parameter_sensitivity"]["status"],
                "max_degradation_pct": payload["parameter_sensitivity"]["max_degradation_pct"],
                "full_report_path": str(full_report_path),
                "stability_result_path": str(payload["stability_result_path"]),
                "stability_report_path": str(payload["stability_report_path"]),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


def run_stability(max_workers: int = 4) -> Tuple[Dict[str, Any], Path]:
    universe = strict_runner.build_broad_asset_etf_pit_universe(
        universe_as_of=strict_runner.UNIVERSE_AS_OF,
        min_history_days_as_of=strict_runner.UNIVERSE_MIN_HISTORY_DAYS_AS_OF,
        max_symbols_per_category=strict_runner.UNIVERSE_MAX_SYMBOLS_PER_CATEGORY,
        universe_start=strict_runner.START,
        universe_end=strict_runner.END,
    )
    strict_runner._validate_pit_universe(universe)
    base_scenario = strict_runner._with_pit_universe(strict_runner.SCENARIOS[0], universe)
    variants = _stability_variants(base_scenario)
    all_symbols = sorted({symbol for scenario in variants for symbol in scenario["symbols"]})
    lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit = strict_runner._load_shared_inputs(all_symbols)
    rows, strict_reports = _run_variants_parallel(
        variants,
        lot_sizes,
        benchmark_provider,
        benchmark_meta,
        survivorship_audit,
        max_workers=max_workers,
    )
    parameter_sensitivity = _build_parameter_sensitivity_payload(base_scenario, rows)
    payload, full_report_path = _write_stability_outputs(rows, strict_reports, parameter_sensitivity)
    return payload, full_report_path


def _stability_variants(base_scenario: Dict[str, Any]) -> List[Dict[str, Any]]:
    variants = [_variant(base_scenario, "base_locked")]
    variants.extend(
        [
            _variant(base_scenario, "lookback_84", momentum_lookback=84),
            _variant(base_scenario, "lookback_168", momentum_lookback=168),
            _variant(base_scenario, "trend_90", trend_window=90),
            _variant(base_scenario, "trend_160", trend_window=160),
            _variant(base_scenario, "vol_40", volatility_window=40),
            _variant(base_scenario, "vol_90", volatility_window=90),
            _variant(base_scenario, "tilt_50", tilt_strength=0.50),
            _variant(base_scenario, "tilt_85", tilt_strength=0.85),
            _variant(base_scenario, "temperature_50", temperature=0.50),
            _variant(base_scenario, "max_branch_25", max_branch_weight=0.25),
            _variant(base_scenario, "rebalance_threshold_03", rebalance_threshold=0.03),
            _variant(base_scenario, "rebalance_10", holding_days=10),
            _variant(base_scenario, "rebalance_40", holding_days=40),
        ]
    )
    return variants


def _variant(base_scenario: Dict[str, Any], suffix: str, **updates: Any) -> Dict[str, Any]:
    scenario = dict(base_scenario)
    scenario["category_symbols"] = {
        category: list(symbols)
        for category, symbols in (base_scenario.get("category_symbols") or {}).items()
    }
    scenario["symbols"] = list(base_scenario.get("symbols") or [])
    scenario["pit_size_fields"] = list(base_scenario.get("pit_size_fields") or [])
    scenario.update(updates)
    scenario["name"] = f"{base_scenario['name']}__{suffix}"
    scenario["stability_variant"] = suffix
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
        results = [
            _run_variant(scenario, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit)
            for scenario in variants
        ]
    else:
        print(f"Running {len(variants)} stability variants with {workers} workers", flush=True)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(
                executor.map(
                    lambda scenario: _run_variant(
                        scenario,
                        lot_sizes,
                        benchmark_provider,
                        benchmark_meta,
                        survivorship_audit,
                    ),
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
        strict_report = strict_runner._run_one(
            scenario,
            lot_sizes,
            benchmark_provider,
            benchmark_meta,
            survivorship_audit,
        )
        row = _row_from_report(scenario, strict_report)
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


def _row_from_report(scenario: Dict[str, Any], strict_report: Dict[str, Any]) -> Dict[str, Any]:
    metrics = strict_report.get("metrics") or {}
    capacity = strict_report.get("capacity") or {}
    diagnostics = strict_report.get("diagnostics") or {}
    execution_cost_bps = strict_report.get("execution_cost_bps") or {}
    row = {
        "name": scenario["stability_variant"],
        "scenario": scenario["name"],
        "variant": scenario["stability_variant"],
        "parameters": _stability_parameters(scenario),
        "cagr": metrics.get("cagr"),
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "sharpe": metrics.get("sharpe"),
        "calmar_ratio": metrics.get("calmar_ratio"),
        "total_trades": metrics.get("total_trades"),
        "cost_drag_pct": diagnostics.get("cost_drag_pct"),
        "max_adv_participation": capacity.get("max_adv_participation"),
        "weighted_effective_bps": execution_cost_bps.get("weighted_effective_bps"),
        "median_effective_bps": execution_cost_bps.get("median_effective_bps"),
        "meets_goal": strict_runner._meets_goal(strict_report),
    }
    return row


def _stability_parameters(scenario: Dict[str, Any]) -> Dict[str, Any]:
    return {key: scenario.get(key) for key in PARAMETER_KEYS if key in scenario}


def _build_parameter_sensitivity_payload(
    base_scenario: Dict[str, Any],
    rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
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
        "method": (
            "Parallel one-factor stability sweep around the locked strict scenario; "
            "it perturbs lookback, trend window, volatility window, continuous weighting parameters, and rebalance interval without selecting a new production parameter set."
        ),
        "base_params": _stability_parameters(base_scenario),
        "selected_params": _stability_parameters(base_scenario),
        "best_params": best_variant.get("parameters") or {},
        "tested_count": tested_count,
        "pass_count": pass_count,
        "max_degradation_pct": max_degradation,
        "max_degradation_threshold_pct": DEGRADATION_THRESHOLD_PCT,
        "stability_note": (
            f"{pass_count}/{tested_count} variants stayed inside the stability band. "
            "Degradation is measured versus the locked base scenario's positive-CAGR Calmar-style score; "
            "better variants are retained as audit evidence only, not as parameter selection."
        ),
        "rows": variants,
    }


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


def _write_stability_outputs(
    rows: List[Dict[str, Any]],
    strict_reports: Dict[str, Dict[str, Any]],
    parameter_sensitivity: Dict[str, Any],
) -> Tuple[Dict[str, Any], Path]:
    strategy_dir = strict_runner.REPORT_ROOT / strict_runner.STRATEGY_ID
    runs_dir = strategy_dir / "runs"
    latest_dir = strict_runner.REPORT_ROOT / "latest"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result_path = strategy_dir / "stability_result.json"
    report_path = strategy_dir / "stability_report.html"
    payload = {
        "strategy_id": strict_runner.STRATEGY_ID,
        "run_ts": run_ts,
        "start": strict_runner.START.date().isoformat(),
        "end": strict_runner.END.date().isoformat(),
        "workers_note": "Variants were run in parallel; result ordering is restored by variant definition order.",
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
    full_report_path = _refresh_full_report_from_strict_grid(strategy_dir)
    return payload, full_report_path


def _refresh_full_report_from_strict_grid(strategy_dir: Path) -> Path:
    grid_path = strategy_dir / "grid_result.json"
    if not grid_path.exists():
        return strategy_dir / "full_research_report.html"
    payload = json.loads(grid_path.read_text(encoding="utf-8"))
    full_report_path, _ = strict_runner._write_outputs(
        list(payload.get("rows") or []),
        dict(payload.get("strict_reports") or {}),
        dict(payload.get("best") or {}),
        dict(payload.get("pit_universe") or {}),
    )
    return full_report_path


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
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(strict_runner.STRATEGY_ID)} stability report</title>
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
<section>
<h1>{escape(strict_runner.STRATEGY_ID)} Stability Report</h1>
<p>Parallel one-factor perturbation audit. This report does not select new production parameters.</p>
</section>
<section>
<h2>Summary</h2>
<div class="table-wrap"><table><tbody>{summary_rows}</tbody></table></div>
</section>
<section>
<h2>Variants</h2>
<div class="table-wrap"><table><thead><tr><th>Variant</th><th>Parameters</th><th>CAGR</th><th>MaxDD</th><th>Sharpe</th><th>Max ADV</th><th>Degradation</th><th>Verdict</th></tr></thead><tbody>{variant_rows}</tbody></table></div>
</section>
</main>
</body>
</html>
"""


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
