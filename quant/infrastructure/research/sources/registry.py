from __future__ import annotations

from typing import Dict

from quant.domain.models.research_source_catalog import (
    ResearchSourceCatalogEntry,
    available_research_source_names,
    dashboard_research_source_names,
    default_research_query_plan,
    default_research_source_names,
    default_research_source_quality,
    research_source_catalog,
    research_source_display_name,
)
from quant.domain.ports.research_source import ResearchSource
from quant.infrastructure.research.sources.arxiv_source import ArxivSource
from quant.infrastructure.research.sources.ashare_public_forum_source import ASharePublicForumSource
from quant.infrastructure.research.sources.ashare_structural_source import AShareStructuralSource
from quant.infrastructure.research.sources.blog_source import BlogSource
from quant.infrastructure.research.sources.nber_source import NBERSource
from quant.infrastructure.research.sources.ssrn_source import SSRNSource


def build_research_sources() -> Dict[str, ResearchSource]:
    return {entry.name: _build_source(entry) for entry in research_source_catalog()}


def _build_source(entry: ResearchSourceCatalogEntry) -> ResearchSource:
    if entry.kind == "arxiv":
        return ArxivSource()
    if entry.kind == "ssrn":
        return SSRNSource()
    if entry.kind == "nber":
        return NBERSource()
    if entry.kind == "blog":
        return BlogSource()
    if entry.kind == "blog_rss":
        return BlogSource(feeds=entry.feed_urls, source_name=entry.name)
    if entry.kind == "ashare_public_forum":
        return ASharePublicForumSource(source_name=entry.name, source_filter=entry.source_filter)
    if entry.kind == "ashare_structural":
        return AShareStructuralSource()
    raise ValueError(f"Unsupported research source kind: {entry.kind}")


__all__ = [
    "available_research_source_names",
    "build_research_sources",
    "dashboard_research_source_names",
    "default_research_query_plan",
    "default_research_source_names",
    "default_research_source_quality",
    "research_source_display_name",
]
