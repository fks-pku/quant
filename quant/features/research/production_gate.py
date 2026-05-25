from typing import Any, Dict, Optional


DEFAULT_PRODUCTION_GATE_THRESHOLDS: Dict[str, float] = {
    "min_strict_cagr": 0.05,
    "max_drawdown_cagr_5_10": 0.15,
    "max_drawdown_cagr_10_15": 0.25,
    "max_drawdown_cagr_15_20": 0.30,
    "max_drawdown_cagr_20_plus": 0.50,
    "min_total_trades": 50,
    "max_adv_participation": 0.05,
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
    failures: list[str] = []

    cagr = metrics.get("strict_cagr")
    drawdown = metrics.get("strict_max_drawdown")
    if cagr is None:
        failures.append("strict_cagr=missing")
    elif cagr < thresholds["min_strict_cagr"]:
        failures.append(f"strict_cagr={_fmt_pct(cagr)} < {_fmt_pct(thresholds['min_strict_cagr'])}")
    if drawdown is None:
        failures.append("strict_max_drawdown=missing")
    elif cagr is not None and cagr >= thresholds["min_strict_cagr"]:
        max_drawdown = _drawdown_limit_for_cagr(cagr, thresholds)
        if abs(drawdown) > max_drawdown:
            failures.append(
                f"strict_max_drawdown={_fmt_pct(abs(drawdown))} > {_fmt_pct(max_drawdown)} for strict_cagr={_fmt_pct(cagr)}"
            )

    total_trades = metrics.get("total_trades")
    if total_trades is None:
        failures.append("total_trades=missing")
    elif total_trades <= int(thresholds["min_total_trades"]):
        failures.append(f"total_trades={total_trades} <= {int(thresholds['min_total_trades'])}")

    max_adv = metrics.get("max_adv_participation")
    if max_adv is None:
        failures.append("max_adv_participation=missing")
    elif max_adv > thresholds["max_adv_participation"]:
        failures.append(f"max_adv_participation={_fmt_pct(max_adv)} > {_fmt_pct(thresholds['max_adv_participation'])}")

    return failures


def _warning_reasons(metrics: Dict[str, Any], thresholds: Dict[str, float]) -> list[str]:
    return []


def _drawdown_limit_for_cagr(cagr: float, thresholds: Dict[str, float]) -> float:
    if cagr < 0.10:
        return thresholds["max_drawdown_cagr_5_10"]
    if cagr < 0.15:
        return thresholds["max_drawdown_cagr_10_15"]
    if cagr < 0.20:
        return thresholds["max_drawdown_cagr_15_20"]
    return thresholds["max_drawdown_cagr_20_plus"]


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
