from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from quant.infrastructure.research.duckdb_research_store import DuckDBResearchStore


def migrate_file_research_store(json_path: Path | str, duckdb_store: DuckDBResearchStore) -> Dict[str, int]:
    path = Path(json_path)
    with path.open("r", encoding="utf-8") as f:
        state = json.load(f)

    candidates = state.get("candidates", {})
    for candidate in candidates.values():
        duckdb_store.upsert_candidate(candidate)

    seen_hashes = state.get("seen_hashes", {})
    for strategy_hash, info in seen_hashes.items():
        duckdb_store.upsert_seen(
            strategy_hash=strategy_hash,
            title=info.get("title", ""),
            source=info.get("source", ""),
            source_url=info.get("source_url", ""),
            seen_at=info.get("seen_at"),
        )

    return {"candidates": len(candidates), "seen_hashes": len(seen_hashes)}

