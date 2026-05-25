from typing import Any, Dict, Optional


DEFAULT_PRODUCTION_GATE_THRESHOLDS: Dict[str, float] = {
    "min_rank_ic": 0.02,
    "min_rank_ic_ir": 0.30,
    "min_rank_ic_tstat": 2.0,
    "min_hit_rate": 0.55,
    "min_strict_sharpe": 0.80,
    "min_strict_cagr": 0.05,
    "max_strict_drawdown": 0.25,
    "min_calmar": 0.70,
    "min_profit_factor": 1.20,
    "min_total_trades": 50,
    "min_aggregate_oos_sharpe": 0.80,
    "min_worst_oos_sharpe": 0.30,
    "min_profitable_splits_pct": 0.55,
    "min_deflated_sharpe_ratio": 0.95,
    "max_adv_participation": 0.05,
    "preferred_mean_correlation": 0.30,
    "max_mean_correlation": 0.50,
}


def evaluate_production_readiness(
    metrics: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    thresholds = _thresholds(config)
    observed = _observed_metrics(metrics)
    failures = _failure_reasons(observed, thresholds)
    warnings = _warning_reasons(observed, thresholds)

    if failures:
        verdict = "fail"
        status = "rejected"
    elif warnings:
        verdict = "warn"
        status = "needs_more_validation"
    else:
        verdict = "pass"
        status = "paper_trading_candidate"

    return {
        "verdict": verdict,
        "status": status,
        "reason": _reason(verdict, failures, warnings),
        "metrics": observed,
        "thresholds": thresholds,
        "failures": failures,
        "warnings": warnings,
    }


def _thresholds(config: Optional[Dict[str, Any]]) -> Dict[str, float]:
    thresholds = dict(DEFAULT_PRODUCTION_GATE_THRESHOLDS)
    if not config:
        return thresholds
    source = config.get("production_gate", config)
    if isinstance(source, dict):
        for key in thresholds:
            if key in source:
                value = _optional_float(source.get(key))
                if value is not None:
                    thresholds[key] = value
    return thresholds


def _observed_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    portfolio = _dict(metrics.get("portfolio_diagnostics"))
    strict = _dict(metrics.get("strict_backtest"))
    strict_metrics = _dict(strict.get("metrics"))
    diagnostics = _dict(strict.get("diagnostics"))
    capacity = _dict(strict.get("capacity"))
    walkforward = _dict(metrics.get("walkforward") or metrics.get("purged_walkforward"))
    ensemble = _dict(metrics.get("ensemble") or metrics.get("ensemble_result"))

    return {
        "rank_ic": _optional_float(metrics.get("rank_ic")),
        "rank_ic_ir": _optional_float(metrics.get("rank_ic_ir")),
        "rank_ic_tstat": _optional_float(metrics.get("rank_ic_tstat")),
        "hit_rate": _optional_float(metrics.get("hit_rate")),
        "top_bucket_after_cost_sharpe": _optional_float(portfolio.get("top_bucket_after_cost_sharpe")),
        "top_bucket_after_cost_annualized_return": _optional_float(portfolio.get("top_bucket_after_cost_annualized_return")),
        "top_bucket_after_cost_max_drawdown": _optional_float(portfolio.get("top_bucket_after_cost_max_drawdown")),
        "top_bucket_after_cost_calmar_ratio": _optional_float(portfolio.get("top_bucket_after_cost_calmar_ratio")),
        "strict_sharpe": _optional_float(strict_metrics.get("sharpe")),
        "strict_cagr": _optional_float(strict_metrics.get("cagr")),
        "strict_max_drawdown": _optional_float(strict_metrics.get("max_drawdown_pct")),
        "strict_calmar": _optional_float(strict_metrics.get("calmar_ratio")),
        "profit_factor": _optional_float(strict_metrics.get("profit_factor")),
        "total_trades": _optional_int(strict_metrics.get("total_trades")),
        "cost_drag_pct": _optional_float(diagnostics.get("cost_drag_pct")),
        "max_adv_participation": _optional_float(capacity.get("max_adv_participation")),
        "p95_adv_participation": _optional_float(capacity.get("p95_adv_participation")),
        "aggregate_oos_sharpe": _optional_float(walkforward.get("aggregate_oos_sharpe")),
        "worst_oos_sharpe": _optional_float(walkforward.get("worst_oos_sharpe")),
        "pct_profitable_splits": _optional_float(walkforward.get("pct_profitable_splits")),
        "deflated_sharpe_ratio": _optional_float(walkforward.get("deflated_sharpe_ratio")),
        "capacity_ok": walkforward.get("capacity_ok") if "capacity_ok" in walkforward else None,
        "bull_only_warning": walkforward.get("bull_only_warning") if "bull_only_warning" in walkforward else None,
        "mean_correlation": _optional_float(ensemble.get("mean_correlation")),
    }


def _failure_reasons(metrics: Dict[str, Any], thresholds: Dict[str, float]) -> list[str]:
    checks = [
        ("rank_ic", "min_rank_ic", "min", _fmt_float),
        ("rank_ic_ir", "min_rank_ic_ir", "min", _fmt_float),
        ("rank_ic_tstat", "min_rank_ic_tstat", "min", _fmt_float),
        ("hit_rate", "min_hit_rate", "min", _fmt_pct),
        ("strict_sharpe", "min_strict_sharpe", "min", _fmt_float),
        ("strict_cagr", "min_strict_cagr", "min", _fmt_pct),
        ("strict_calmar", "min_calmar", "min", _fmt_float),
        ("profit_factor", "min_profit_factor", "min", _fmt_float),
        ("aggregate_oos_sharpe", "min_aggregate_oos_sharpe", "min", _fmt_float),
        ("worst_oos_sharpe", "min_worst_oos_sharpe", "min", _fmt_float),
        ("pct_profitable_splits", "min_profitable_splits_pct", "min", _fmt_pct),
    ]
    failures: list[str] = []
    for metric_key, threshold_key, direction, formatter in checks:
        value = metrics.get(metric_key)
        threshold = thresholds[threshold_key]
        if value is None:
            failures.append(f"{metric_key}=missing")
        elif direction == "min" and value < threshold:
            failures.append(f"{metric_key}={formatter(value)} < {formatter(threshold)}")

    drawdown = metrics.get("strict_max_drawdown")
    if drawdown is None:
        failures.append("strict_max_drawdown=missing")
    elif abs(drawdown) > thresholds["max_strict_drawdown"]:
        failures.append(
            f"strict_max_drawdown={_fmt_pct(abs(drawdown))} > {_fmt_pct(thresholds['max_strict_drawdown'])}"
        )

    total_trades = metrics.get("total_trades")
    if total_trades is None:
        failures.append("total_trades=missing")
    elif total_trades < int(thresholds["min_total_trades"]):
        failures.append(f"total_trades={total_trades} < {int(thresholds['min_total_trades'])}")

    capacity_ok = metrics.get("capacity_ok")
    if capacity_ok is False:
        failures.append("capacity_ok=False")

    max_adv = metrics.get("max_adv_participation")
    if max_adv is not None and max_adv > thresholds["max_adv_participation"]:
        failures.append(f"max_adv_participation={_fmt_pct(max_adv)} > {_fmt_pct(thresholds['max_adv_participation'])}")

    if metrics.get("bull_only_warning") is True:
        failures.append("bull_only_warning=True")

    mean_corr = metrics.get("mean_correlation")
    if mean_corr is not None and mean_corr > thresholds["max_mean_correlation"]:
        failures.append(f"mean_correlation={_fmt_float(mean_corr)} > {_fmt_float(thresholds['max_mean_correlation'])}")

    return failures


def _warning_reasons(metrics: Dict[str, Any], thresholds: Dict[str, float]) -> list[str]:
    warnings: list[str] = []
    mean_corr = metrics.get("mean_correlation")
    if (
        mean_corr is not None
        and mean_corr > thresholds["preferred_mean_correlation"]
        and mean_corr <= thresholds["max_mean_correlation"]
    ):
        warnings.append(
            f"mean_correlation={_fmt_float(mean_corr)} > preferred {_fmt_float(thresholds['preferred_mean_correlation'])}"
        )
    dsr = metrics.get("deflated_sharpe_ratio")
    if dsr is not None and dsr < thresholds["min_deflated_sharpe_ratio"]:
        warnings.append(
            f"deflated_sharpe_ratio={_fmt_float(dsr)} < {_fmt_float(thresholds['min_deflated_sharpe_ratio'])}"
        )
    return warnings


def _reason(verdict: str, failures: list[str], warnings: list[str]) -> str:
    if verdict == "pass":
        return "Production gate passed"
    if verdict == "warn":
        return "Production gate needs more validation: " + "; ".join(warnings)
    return "Production gate failed: " + "; ".join(failures)


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _optional_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fmt_float(value: float) -> str:
    return f"{value:.2f}"


def _fmt_pct(value: float) -> str:
    return f"{value:.2%}"
