from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
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


RESEARCH_SOURCE_CATALOG_PATH = Path(__file__).with_name("research_source_catalog.json")


def _string_tuple(value) -> Tuple[str, ...]:
    return tuple(str(item) for item in value or ())


def _load_research_source_catalog(path: Path = RESEARCH_SOURCE_CATALOG_PATH) -> Tuple[ResearchSourceCatalogEntry, ...]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("sources")
    if not isinstance(rows, list):
        raise ValueError(f"Research source catalog must define a sources list: {path}")

    entries = []
    seen = set()
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Research source catalog entry #{idx} must be an object")
        name = str(row.get("name", "")).strip()
        kind = str(row.get("kind", "")).strip()
        source_type = str(row.get("source_type", "")).strip()
        if not name or not kind or not source_type:
            raise ValueError(f"Research source catalog entry #{idx} must include name, kind, and source_type")
        if name in seen:
            raise ValueError(f"Duplicate research source name: {name}")
        seen.add(name)
        entries.append(
            ResearchSourceCatalogEntry(
                name=name,
                kind=kind,
                source_type=source_type,
                quality_score=float(row.get("quality_score", 1.0)),
                display_name=str(row.get("display_name", "")),
                default_enabled=bool(row.get("default_enabled", False)),
                dashboard_enabled=bool(row.get("dashboard_enabled", False)),
                query_terms=_string_tuple(row.get("query_terms")),
                feed_urls=_string_tuple(row.get("feed_urls")),
                source_filter=_string_tuple(row.get("source_filter")),
            )
        )
    return tuple(entries)


RESEARCH_SOURCE_CATALOG: Tuple[ResearchSourceCatalogEntry, ...] = _load_research_source_catalog()


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
