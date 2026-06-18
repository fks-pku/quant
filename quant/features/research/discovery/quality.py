import re
from datetime import date, datetime
from typing import Any, Dict, Iterable, Optional

from quant.features.research.models import DiscoveryQualityReport, RawStrategy


_SOURCE_PROFILE = {
    "arxiv": ("academic", 1.75),
    "ssrn": ("academic", 1.65),
    "nber": ("institutional", 1.55),
    "aqr": ("practitioner_research", 1.45),
    "worldquant101": ("public_factor_library", 1.35),
    "ashare_structural": ("local_structural_research", 1.55),
    "ashare_public_forum": ("practitioner_community", 1.20),
    "bigquant": ("practitioner_community", 1.20),
    "joinquant": ("practitioner_community", 1.15),
    "jointquant": ("practitioner_community", 1.15),
    "joinquant_community": ("practitioner_community", 1.15),
    "alpha_architect": ("practitioner_research", 1.35),
    "quantocracy": ("curated_blog", 1.15),
    "hudson_thames": ("practitioner_research", 1.30),
    "portfolio_optimizer": ("practitioner_research", 1.25),
    "quantpedia": ("strategy_database", 1.40),
    "blog": ("blog", 0.95),
}

_DAILY_TERMS = {
    "daily": "daily_ohlcv",
    "eod": "daily_ohlcv",
    "ohlcv": "daily_ohlcv",
    "close": "daily_ohlcv",
    "cross-sectional": "cross_sectional",
    "cross sectional": "cross_sectional",
    "rank ic": "rank_ic",
    "information coefficient": "rank_ic",
    "turnover": "cost_aware",
    "transaction cost": "cost_aware",
    "slippage": "cost_aware",
    "liquid": "liquidity_aware",
    "liquidity": "liquidity_aware",
    "equity": "liquid_equity",
    "stock": "liquid_equity",
    "etf": "liquid_equity",
    "a-share": "liquid_equity",
    "a share": "liquid_equity",
    "trading friction": "cost_aware",
    "price limit": "cost_aware",
    "daily limit": "cost_aware",
    "日线": "daily_ohlcv",
    "收盘价": "daily_ohlcv",
    "开盘价": "daily_ohlcv",
    "成交量": "daily_ohlcv",
    "换手率": "cost_aware",
    "交易成本": "cost_aware",
    "容量": "liquidity_aware",
    "流动性": "liquidity_aware",
    "股票": "liquid_equity",
    "行业": "liquid_equity",
    "申万": "liquid_equity",
    "a股": "liquid_equity",
}

_STRATEGY_TERMS = {
    "momentum": "momentum",
    "mean reversion": "mean_reversion",
    "reversal": "mean_reversion",
    "breakout": "breakout",
    "trend following": "momentum",
    "factor": "factor",
    "value": "factor",
    "quality": "factor",
    "low volatility": "factor",
    "market cap": "factor",
    "small cap": "factor",
    "size factor": "factor",
    "lottery": "factor",
    "volume exhaustion": "mean_reversion",
    "gap-down": "mean_reversion",
    "gap down": "mean_reversion",
    "statistical arbitrage": "stat_arb",
    "pairs trading": "stat_arb",
    "regime": "regime",
    "动量": "momentum",
    "趋势": "momentum",
    "轮动": "momentum",
    "反转": "mean_reversion",
    "均值回归": "mean_reversion",
    "低波": "factor",
    "红利": "factor",
    "价值": "factor",
    "质量": "factor",
    "小市值": "factor",
    "指数增强": "factor",
    "拥挤度": "regime",
}

_DAILY_PATTERNS = (
    re.compile(r"\b\d{1,4}[-\s]?day\b"),
    re.compile(r"\btrailing\s+\d{1,4}[-\s]?day"),
    re.compile(r"\bpositive days\b"),
    re.compile(r"\bdaily returns?\b"),
)

_RISK_TERMS = {
    "high-frequency": "high_frequency_not_daily",
    "high frequency": "high_frequency_not_daily",
    "tick": "high_frequency_not_daily",
    "limit order book": "high_frequency_not_daily",
    "order book": "high_frequency_not_daily",
    "intraday": "intraday_dependency",
    "alternative data": "alternative_data_required",
    "satellite": "alternative_data_required",
    "news sentiment": "alternative_data_required",
    "earnings": "fundamental_lag",
    "financial statement": "fundamental_lag",
    "deep learning": "ml_overfit_risk",
    "reinforcement learning": "ml_overfit_risk",
    "neural network": "ml_overfit_risk",
    "llm": "non_price_signal",
    "large language model": "non_price_signal",
    "multi-agent": "non_price_signal",
    "agentic ai": "non_price_signal",
    "autonomously searches": "non_price_signal",
    "web interfaces": "alternative_data_required",
    "prediction market": "non_equity_market",
    "polymarket": "non_equity_market",
    "perpetual": "non_equity_market",
    "futures": "non_equity_market",
    "market clearing": "non_equity_market",
    "trade credit": "non_equity_market",
    "crypto": "crypto_capacity_risk",
    "高频": "high_frequency_not_daily",
    "盘口": "high_frequency_not_daily",
    "分钟": "intraday_dependency",
    "舆情": "alternative_data_required",
    "新闻情绪": "alternative_data_required",
}

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def score_discovery(
    raw: RawStrategy,
    as_of: Optional[date] = None,
    config: Optional[Dict[str, Any]] = None,
) -> DiscoveryQualityReport:
    cfg = config or {}
    current_date = as_of or date.today()
    text = _text(raw)
    source_type, source_quality = _source_profile(raw.source, cfg)
    recency_score, recency_flag = _recency_score(raw.published_date, current_date)
    provenance_score, provenance_flags = _provenance_score(raw)
    detail_score, detail_flags = _detail_score(raw.description)
    matched_terms = _matched_terms(text)
    risk_flags = _risk_flags(text)
    implementability_score = _implementability_score(matched_terms, risk_flags)
    daily_data_score = _daily_data_score(matched_terms, risk_flags)
    novelty_score = _novelty_score(matched_terms, text)
    warnings = []
    if recency_flag:
        warnings.append(recency_flag)
    warnings.extend(provenance_flags)
    warnings.extend(detail_flags)
    score = _clamp(
        source_quality
        + recency_score
        + provenance_score
        + detail_score
        + implementability_score
        + daily_data_score
        + novelty_score
        - _risk_penalty(risk_flags),
        0.0,
        10.0,
    )
    return DiscoveryQualityReport(
        score=round(score, 2),
        source_quality_score=round(source_quality, 2),
        recency_score=round(recency_score, 2),
        provenance_score=round(provenance_score, 2),
        detail_score=round(detail_score, 2),
        implementability_score=round(implementability_score, 2),
        daily_data_score=round(daily_data_score, 2),
        novelty_score=round(novelty_score, 2),
        source_type=source_type,
        matched_terms=sorted(matched_terms),
        risk_flags=sorted(set(risk_flags + warnings)),
        warnings=sorted(set(warnings)),
    )


def attach_discovery_quality(
    raw: RawStrategy,
    as_of: Optional[date] = None,
    config: Optional[Dict[str, Any]] = None,
) -> RawStrategy:
    report = score_discovery(raw, as_of=as_of, config=config)
    raw.metadata = dict(raw.metadata or {})
    raw.metadata["discovery_quality"] = report.to_dict()
    return raw


def discovery_score(raw: RawStrategy) -> float:
    quality = (raw.metadata or {}).get("discovery_quality") or {}
    try:
        return float(quality.get("score", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def discovery_quality(raw: RawStrategy) -> Dict[str, Any]:
    return dict((raw.metadata or {}).get("discovery_quality") or {})


def _source_profile(source: str, config: Dict[str, Any]) -> tuple[str, float]:
    overrides = config.get("source_quality", {}) if isinstance(config, dict) else {}
    key = str(source or "").lower().strip()
    source_type, score = _SOURCE_PROFILE.get(key, ("unknown", 0.8))
    if isinstance(overrides, dict) and key in overrides:
        try:
            score = float(overrides[key])
        except (TypeError, ValueError):
            pass
    return source_type, _clamp(score, 0.0, 2.0)


def _recency_score(value: Optional[str], as_of: date) -> tuple[float, str]:
    published = _parse_date(value)
    if published is None:
        return 0.35, "missing_published_date"
    age_days = max(0, (as_of - published).days)
    if age_days <= 180:
        return 1.35, ""
    if age_days <= 365:
        return 1.1, ""
    if age_days <= 730:
        return 0.75, ""
    return 0.25, "stale_source"


def _provenance_score(raw: RawStrategy) -> tuple[float, list[str]]:
    score = 0.0
    flags = []
    if raw.source_url:
        score += 0.55
    else:
        flags.append("missing_source_url")
    if raw.authors:
        score += 0.35
    else:
        flags.append("missing_authors")
    if raw.published_date:
        score += 0.35
    return score, flags


def _detail_score(description: str) -> tuple[float, list[str]]:
    text = str(description or "").strip()
    length = len(text)
    flags = []
    if length >= 500:
        score = 1.25
    elif length >= 180:
        score = 0.95
    elif length >= 50:
        score = 0.55
    else:
        score = 0.2
        flags.append("thin_description")
    return score, flags


def _matched_terms(text: str) -> set[str]:
    matches = set()
    for term, label in {**_DAILY_TERMS, **_STRATEGY_TERMS}.items():
        if _contains_term(text, term):
            matches.add(label)
    if any(pattern.search(text) for pattern in _DAILY_PATTERNS):
        matches.add("daily_ohlcv")
    return matches


def _risk_flags(text: str) -> list[str]:
    return [label for term, label in _RISK_TERMS.items() if _contains_term(text, term)]


def _implementability_score(matched_terms: Iterable[str], risk_flags: Iterable[str]) -> float:
    terms = set(matched_terms)
    flags = set(risk_flags)
    score = 0.4
    if terms.intersection({"momentum", "mean_reversion", "breakout", "factor", "regime"}):
        score += 0.75
    if terms.intersection({"rank_ic", "cost_aware", "liquidity_aware"}):
        score += 0.45
    if flags.intersection({"high_frequency_not_daily", "alternative_data_required", "ml_overfit_risk"}):
        score -= 0.45
    return _clamp(score, 0.0, 1.5)


def _daily_data_score(matched_terms: Iterable[str], risk_flags: Iterable[str]) -> float:
    terms = set(matched_terms)
    flags = set(risk_flags)
    score = 0.25
    if "daily_ohlcv" in terms:
        score += 1.0
    if "liquid_equity" in terms:
        score += 0.35
    if flags.intersection({"high_frequency_not_daily", "intraday_dependency"}):
        score -= 0.75
    if "alternative_data_required" in flags:
        score -= 0.35
    return _clamp(score, 0.0, 1.5)


def _novelty_score(matched_terms: Iterable[str], text: str) -> float:
    terms = set(matched_terms)
    score = min(0.85, len(terms.intersection(set(_STRATEGY_TERMS.values()))) * 0.22)
    if "out-of-sample" in text or "out of sample" in text:
        score += 0.25
    if "fama" in text or "factor decomposition" in text:
        score += 0.2
    return _clamp(score, 0.0, 1.0)


def _risk_penalty(flags: Iterable[str]) -> float:
    weights = {
        "high_frequency_not_daily": 1.2,
        "intraday_dependency": 0.55,
        "alternative_data_required": 0.65,
        "ml_overfit_risk": 0.55,
        "crypto_capacity_risk": 0.35,
        "non_equity_market": 0.75,
        "non_price_signal": 0.75,
        "fundamental_lag": 0.25,
    }
    return sum(weights.get(flag, 0.0) for flag in set(flags))


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    text = str(value).strip()
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        pass
    match = re.search(r"(\d{1,2})\s+([A-Za-z]{3,})\s+(\d{4})", text)
    if match:
        day = int(match.group(1))
        month = _MONTHS.get(match.group(2)[:3].lower())
        year = int(match.group(3))
        if month:
            return date(year, month, day)
    match = re.search(r"(\d{4})[-/](\d{1,2})(?:[-/](\d{1,2}))?", text)
    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3) or 1)
        return date(year, month, day)
    return None


def _text(raw: RawStrategy) -> str:
    return f"{raw.title} {raw.description}".lower()


def _contains_term(text: str, term: str) -> bool:
    escaped = re.escape(term.lower())
    return re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", text) is not None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))
