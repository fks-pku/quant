import re
from typing import Any, Dict, List, Optional

from quant.features.research.discovery.quality import attach_discovery_quality, discovery_quality, discovery_score
from quant.features.research.models import EvaluationReport, RawStrategy


_TYPE_PATTERNS = [
    (r"mean reversion|reversal|contrarian", "mean_reversion"),
    (r"momentum|trend following", "momentum"),
    (r"breakout|channel", "breakout"),
    (r"factor|value|quality|low volatility|fama", "factor"),
    (r"pairs|cointegration|statistical arbitrage|stat.?arb", "stat_arb"),
    (r"regime|volatility", "regime"),
]

_CORE_TESTS = [
    "rank_ic",
    "ic_decay",
    "fdr_control",
    "factor_decomposition",
    "purged_walk_forward",
    "transaction_cost_capacity",
]


def heuristic_evaluation(raw: RawStrategy, config: Optional[Dict[str, Any]] = None) -> EvaluationReport:
    raw = attach_discovery_quality(raw, config=config)
    text = _text(raw)
    strategy_type = _strategy_type(text)
    hf = _has_any(text, ("high frequency", "high-frequency", "tick", "order book", "limit order book"))
    daily_terms = _has_any(text, ("daily", "eod", "ohlcv", "close", "end of day"))
    data_requirement = "high-frequency" if hf else "low" if daily_terms else "medium"
    daily_adaptable = not hf or _has_any(text, ("daily adaptable", "daily proxy", "daily bars"))
    economic = _score_terms(text, ("behavioral", "risk premium", "investor", "underreaction", "overreaction", "limits to arbitrage"), 2.0, 0.7)
    if strategy_type in {"momentum", "mean_reversion", "breakout", "factor"}:
        economic = max(economic, 1.2)
    factor_unique = _score_terms(text, ("alpha", "anomaly", "residual", "orthogonal", "fama", "factor"), 2.0, 0.5)
    data_availability = 2.0 if data_requirement == "low" else 1.1 if data_requirement == "medium" else 0.2
    implementation = 1.7 if strategy_type in {"momentum", "mean_reversion", "breakout"} else 1.1
    if hf:
        implementation = min(implementation, 0.4)
    overfit = 1.5
    if _has_any(text, ("deep learning", "reinforcement learning", "neural network", "machine learning")):
        overfit = 0.4
    cost = 0.9 if _has_any(text, ("liquid", "etf", "large cap", "turnover", "transaction cost")) else 0.55
    regime = 0.8 if _has_any(text, ("regime", "crisis", "bear", "bull", "out-of-sample", "out of sample")) else 0.55
    risk_flags = list(discovery_quality(raw).get("risk_flags", []))
    if data_requirement == "high-frequency" and not daily_adaptable and "high_frequency_not_daily" not in risk_flags:
        risk_flags.append("high_frequency_not_daily")
    summary = f"{strategy_type} idea triaged by deterministic professional rubric"
    report = EvaluationReport(
        suitability_score=0.0,
        complexity_score=3.0 if implementation >= 1.0 else 8.0,
        data_requirement=data_requirement,
        daily_adaptable=daily_adaptable,
        estimated_edge=0.06 if not hf else 0.01,
        recommended_symbols=_symbols(text, config),
        strategy_type=strategy_type,
        summary=summary,
        economic_rationale_score=round(economic, 2),
        factor_uniqueness_score=round(factor_unique, 2),
        data_availability_score=round(data_availability, 2),
        implementation_score=round(implementation, 2),
        overfit_risk_score=round(overfit, 2),
        cost_capacity_score=round(cost, 2),
        regime_robustness_score=round(regime, 2),
        risk_flags=sorted(set(risk_flags)),
    )
    return apply_professional_rubric(raw, report, config=config)


def apply_professional_rubric(
    raw: RawStrategy,
    report: EvaluationReport,
    config: Optional[Dict[str, Any]] = None,
) -> EvaluationReport:
    raw = attach_discovery_quality(raw, config=config)
    quality = discovery_quality(raw)
    risk_flags = sorted(set(list(report.risk_flags or []) + list(quality.get("risk_flags", []) or [])))
    if report.data_requirement == "high-frequency" and not report.daily_adaptable:
        risk_flags.append("high_frequency_not_daily")
    if report.estimated_edge > 0.25:
        risk_flags.append("unrealistic_edge")
    risk_flags = sorted(set(risk_flags))
    required_data_fields = _required_data_fields(raw, report)
    validation_tests = _validation_tests(raw, report, risk_flags)
    breakdown = _score_breakdown(raw, report, risk_flags)
    signal_quality = _clamp(sum(breakdown.values()), 0.0, 10.0)
    data_risk = _data_risk_score(report, risk_flags)
    bias_risk = _bias_risk_score(risk_flags)
    research_confidence = _clamp(
        0.45 * signal_quality + 0.35 * discovery_score(raw) + 0.20 * bias_risk,
        0.0,
        10.0,
    )
    base_suitability = report.suitability_score if report.suitability_score > 0 else signal_quality
    admission = _clamp(
        0.35 * base_suitability + 0.45 * signal_quality + 0.20 * research_confidence,
        0.0,
        10.0,
    )
    if _critical_risk(risk_flags):
        admission = min(admission, signal_quality + 1.0)
    threshold = float((config or {}).get("min_admission_score", 6.0) or 6.0)
    report.risk_flags = risk_flags
    report.required_data_fields = required_data_fields
    report.validation_tests = validation_tests
    report.score_breakdown = {k: round(v, 3) for k, v in breakdown.items()}
    report.signal_quality_score = round(signal_quality, 2)
    report.research_confidence_score = round(research_confidence, 2)
    report.data_risk_score = round(data_risk, 2)
    report.bias_risk_score = round(bias_risk, 2)
    report.admission_score = round(admission, 2)
    report.evidence_notes = _evidence_notes(raw, report, quality)
    if report.admission_score < threshold and not report.rejection_reason:
        report.rejection_reason = f"admission_score={report.admission_score:.1f} < {threshold:.1f}"
    elif report.admission_score >= threshold and report.rejection_reason.startswith("admission_score="):
        report.rejection_reason = ""
    if report.suitability_score <= 0:
        report.suitability_score = round(report.admission_score, 2)
    return report


def _score_breakdown(raw: RawStrategy, report: EvaluationReport, risk_flags: List[str]) -> Dict[str, float]:
    scores = {
        "economic_rationale": _norm(report.economic_rationale_score, 2.0) * 1.25,
        "factor_uniqueness": _norm(report.factor_uniqueness_score, 2.0) * 1.15,
        "data_availability": _norm(report.data_availability_score, 2.0) * 1.20,
        "implementation": _norm(report.implementation_score, 2.0) * 1.10,
        "overfit_control": _norm(report.overfit_risk_score, 2.0) * 1.20,
        "cost_capacity": _norm(report.cost_capacity_score, 1.0) * 0.90,
        "regime_robustness": _norm(report.regime_robustness_score, 1.0) * 0.75,
        "discovery_quality": _norm(discovery_score(raw), 10.0) * 1.45,
    }
    if report.data_requirement == "low":
        scores["daily_bar_fit"] = 0.55
    elif report.data_requirement == "medium":
        scores["daily_bar_fit"] = 0.25
    else:
        scores["daily_bar_fit"] = -0.55
    scores["risk_haircut"] = -_risk_penalty(report, risk_flags)
    return scores


def _risk_penalty(report: EvaluationReport, risk_flags: List[str]) -> float:
    weights = {
        "high_frequency_not_daily": 1.8,
        "intraday_dependency": 0.7,
        "alternative_data_required": 0.75,
        "ml_overfit_risk": 0.7,
        "unrealistic_edge": 0.9,
        "missing_source_url": 0.35,
        "thin_description": 0.25,
        "stale_source": 0.25,
        "crypto_capacity_risk": 0.35,
        "fundamental_lag": 0.25,
    }
    penalty = sum(weights.get(flag, 0.0) for flag in set(risk_flags))
    if report.complexity_score > 7.0:
        penalty += 0.45
    return penalty


def _required_data_fields(raw: RawStrategy, report: EvaluationReport) -> List[str]:
    fields = {"date", "symbol", "close"}
    text = _text(raw)
    if report.strategy_type in {"breakout", "volatility", "regime"} or _has_any(text, ("atr", "breakout", "ohlc")):
        fields.update({"high", "low"})
    if _has_any(text, ("volume", "liquid", "turnover", "capacity")):
        fields.add("volume")
    if _has_any(text, ("fundamental", "earnings", "book value", "book-to-market", "book to market")):
        fields.add("point_in_time_fundamentals")
    if report.data_requirement == "high-frequency":
        fields.add("intraday_or_order_book")
    return sorted(fields)


def _validation_tests(raw: RawStrategy, report: EvaluationReport, risk_flags: List[str]) -> List[str]:
    tests = list(_CORE_TESTS)
    if report.strategy_type in {"regime", "momentum", "mean_reversion"}:
        tests.append("regime_breakdown")
    if "fundamental_lag" in risk_flags or "point_in_time_fundamentals" in _required_data_fields(raw, report):
        tests.append("point_in_time_lag_audit")
    if "high_frequency_not_daily" in risk_flags:
        tests.append("daily_adaptability_audit")
    if "ml_overfit_risk" in risk_flags:
        tests.append("feature_stability_walk_forward")
    return sorted(set(tests))


def _data_risk_score(report: EvaluationReport, risk_flags: List[str]) -> float:
    base = {
        "low": 8.5,
        "medium": 6.5,
        "high-frequency": 2.5,
    }.get(report.data_requirement, 4.5)
    if "alternative_data_required" in risk_flags:
        base -= 1.0
    if "fundamental_lag" in risk_flags:
        base -= 0.8
    return _clamp(base, 0.0, 10.0)


def _bias_risk_score(risk_flags: List[str]) -> float:
    score = 8.5
    for flag in set(risk_flags):
        if flag in {"high_frequency_not_daily", "ml_overfit_risk", "unrealistic_edge"}:
            score -= 1.5
        elif flag in {"alternative_data_required", "fundamental_lag", "intraday_dependency"}:
            score -= 1.0
        elif flag.startswith("missing_") or flag == "thin_description":
            score -= 0.4
    return _clamp(score, 0.0, 10.0)


def _evidence_notes(raw: RawStrategy, report: EvaluationReport, quality: Dict[str, Any]) -> List[str]:
    notes = [
        f"discovery_score={float(quality.get('score', 0.0) or 0.0):.1f}",
        f"source_type={quality.get('source_type', 'unknown')}",
        f"data_requirement={report.data_requirement}",
        f"required_fields={','.join(report.required_data_fields)}",
    ]
    if report.risk_flags:
        notes.append(f"risk_flags={','.join(report.risk_flags)}")
    return notes


def _critical_risk(risk_flags: List[str]) -> bool:
    return bool(set(risk_flags).intersection({"high_frequency_not_daily", "unrealistic_edge"}))


def _strategy_type(text: str) -> str:
    for pattern, strategy_type in _TYPE_PATTERNS:
        if re.search(pattern, text):
            return strategy_type
    return "unknown"


def _symbols(text: str, config: Optional[Dict[str, Any]] = None) -> List[str]:
    cfg = config or {}
    target_market = str(cfg.get("target_market") or cfg.get("market") or "").lower()
    configured_symbols = cfg.get("default_symbols") or cfg.get("symbols") or []
    if target_market in {"cn", "china", "a_share", "a-share", "ashare"}:
        return list(configured_symbols or ["000300", "000905", "600519", "000001", "510300"])
    if _has_any(text, ("a-share", "china", "chinese equity")):
        return list(configured_symbols or ["000300", "600519"])
    if _has_any(text, ("hong kong", "hk equity")):
        return ["HSI", "00700"]
    if _has_any(text, ("etf", "equity", "stock", "large cap", "liquid")):
        return ["SPY", "QQQ"]
    return ["SPY"]


def _score_terms(text: str, terms: tuple[str, ...], max_score: float, step: float) -> float:
    return min(max_score, sum(step for term in terms if term in text))


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _text(raw: RawStrategy) -> str:
    return f"{raw.title} {raw.description}".lower()


def _norm(value: Any, max_value: float) -> float:
    try:
        return _clamp(float(value) / max_value, 0.0, 1.0)
    except (TypeError, ValueError):
        return 0.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))
