from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ResearchSourceCatalogEntry:
    name: str
    kind: str
    source_type: str
    quality_score: float
    display_name: str = ""
    default_enabled: bool = False
    dashboard_enabled: bool = False
    query_terms: Tuple[str, ...] = field(default_factory=tuple)
    feed_urls: Tuple[str, ...] = field(default_factory=tuple)
    source_filter: Tuple[str, ...] = field(default_factory=tuple)


RESEARCH_SOURCE_CATALOG: Tuple[ResearchSourceCatalogEntry, ...] = (
    ResearchSourceCatalogEntry(
        "arxiv",
        "arxiv",
        "academic",
        1.75,
        display_name="arXiv",
        default_enabled=True,
        dashboard_enabled=True,
    ),
    ResearchSourceCatalogEntry(
        "ssrn",
        "ssrn",
        "academic",
        1.65,
        display_name="SSRN",
        query_terms=(
            "daily trading strategy equity factor",
            "cross sectional momentum anomaly",
            "mean reversion equity strategy",
        ),
    ),
    ResearchSourceCatalogEntry("nber", "nber", "institutional", 1.55, display_name="NBER"),
    ResearchSourceCatalogEntry("blog", "blog", "blog", 0.95, display_name="Blog"),
    ResearchSourceCatalogEntry(
        "ashare_public_forum",
        "ashare_public_forum",
        "practitioner_community",
        1.20,
        display_name="A-Share Forum",
    ),
    ResearchSourceCatalogEntry(
        "bigquant",
        "ashare_public_forum",
        "practitioner_community",
        1.20,
        display_name="BigQuant",
        default_enabled=True,
        dashboard_enabled=True,
        query_terms=("行业 轮动", "Alpha101", "景气度 趋势 拥挤度"),
        source_filter=("bigquant",),
    ),
    ResearchSourceCatalogEntry(
        "joinquant",
        "ashare_public_forum",
        "practitioner_community",
        1.15,
        display_name="JoinQuant",
        source_filter=("joinquant",),
    ),
    ResearchSourceCatalogEntry(
        "jointquant",
        "ashare_public_forum",
        "practitioner_community",
        1.15,
        display_name="JointQuant",
        default_enabled=True,
        dashboard_enabled=True,
        query_terms=("小市值", "多因子"),
        source_filter=("joinquant",),
    ),
    ResearchSourceCatalogEntry(
        "quantocracy",
        "blog_rss",
        "curated_blog",
        1.15,
        display_name="Quantocracy",
        default_enabled=True,
        dashboard_enabled=True,
        query_terms=("daily factor momentum mean reversion", "quant trading strategy transaction costs"),
        feed_urls=("https://quantocracy.com/feed/",),
    ),
    ResearchSourceCatalogEntry(
        "hudson_thames",
        "blog_rss",
        "practitioner_research",
        1.30,
        display_name="Hudson & Thames",
        query_terms=("daily mean reversion momentum portfolio optimization", "backtesting pitfalls daily equity strategy"),
        feed_urls=("https://hudsonthames.org/feed/",),
    ),
    ResearchSourceCatalogEntry(
        "portfolio_optimizer",
        "blog_rss",
        "practitioner_research",
        1.25,
        display_name="Portfolio Optimizer",
        query_terms=("portfolio optimization daily allocation risk", "risk parity momentum allocation"),
        feed_urls=("https://portfoliooptimizer.io/feed.xml",),
    ),
    ResearchSourceCatalogEntry(
        "alpha_architect",
        "blog_rss",
        "practitioner_research",
        1.35,
        display_name="Alpha Architect",
        query_terms=("factor investing momentum value quality", "daily equity factor strategy"),
        feed_urls=("https://alphaarchitect.com/feed/",),
    ),
    ResearchSourceCatalogEntry(
        "quantpedia",
        "blog_rss",
        "strategy_database",
        1.40,
        display_name="Quantpedia",
        query_terms=("daily equity factor momentum seasonality", "market timing asset allocation strategy"),
        feed_urls=("https://quantpedia.com/feed/",),
    ),
    ResearchSourceCatalogEntry(
        "ashare_structural",
        "ashare_structural",
        "local_structural_research",
        1.55,
        display_name="A-Share Structural",
    ),
)


def research_source_catalog() -> Tuple[ResearchSourceCatalogEntry, ...]:
    return RESEARCH_SOURCE_CATALOG


def available_research_source_names() -> List[str]:
    return [entry.name for entry in RESEARCH_SOURCE_CATALOG]


def default_research_source_names() -> List[str]:
    return [entry.name for entry in RESEARCH_SOURCE_CATALOG if entry.default_enabled]


def dashboard_research_source_names() -> List[str]:
    return [entry.name for entry in RESEARCH_SOURCE_CATALOG if entry.dashboard_enabled]


def default_research_query_plan() -> Dict[str, List[Dict[str, str]]]:
    return {
        entry.name: [{"query": query} for query in entry.query_terms]
        for entry in RESEARCH_SOURCE_CATALOG
        if entry.query_terms
    }


def default_research_source_quality() -> Dict[str, float]:
    return {entry.name: float(entry.quality_score) for entry in RESEARCH_SOURCE_CATALOG}


def research_source_profile(source_name: str) -> Optional[Tuple[str, float]]:
    key = str(source_name or "").lower().strip()
    for entry in RESEARCH_SOURCE_CATALOG:
        if entry.name == key:
            return entry.source_type, float(entry.quality_score)
    return None


def research_source_display_name(source_name: str) -> Optional[str]:
    key = str(source_name or "").lower().strip()
    for entry in RESEARCH_SOURCE_CATALOG:
        if entry.name == key:
            return entry.display_name or entry.name
    return None
