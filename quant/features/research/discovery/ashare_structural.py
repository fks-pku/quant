from __future__ import annotations

from typing import List, Optional, Sequence

from quant.features.research.models import RawStrategy
from quant.infrastructure.research.sources.ashare_structural_source import (
    ASHARE_STRUCTURAL_AUTHORS,
    ASHARE_STRUCTURAL_PUBLISHED_DATE,
    ASHARE_STRUCTURAL_SOURCE,
    ASHARE_STRUCTURAL_SOURCE_URL,
    AShareStructuralSource,
    build_ashare_structural_strategy_dicts,
)


def build_ashare_structural_raw_strategies(
    idea_ids: Optional[Sequence[str]] = None,
) -> List[RawStrategy]:
    return [_raw_strategy(row) for row in build_ashare_structural_strategy_dicts(idea_ids=idea_ids)]


def _raw_strategy(row) -> RawStrategy:
    return RawStrategy(
        title=row.get("title", ""),
        description=row.get("description", ""),
        source=row.get("source", ASHARE_STRUCTURAL_SOURCE),
        source_url=row.get("source_url", ASHARE_STRUCTURAL_SOURCE_URL),
        authors=row.get("authors", ASHARE_STRUCTURAL_AUTHORS),
        published_date=row.get("published_date", ASHARE_STRUCTURAL_PUBLISHED_DATE),
        metadata=dict(row.get("metadata") or {}),
    )
