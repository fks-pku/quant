from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict


def migrate_file_research_store(json_path: str, duckdb_store: Any) -> Dict[str, int]:
    path = Path(json_path)
    if not path.exists():
        return {"candidates": 0, "seen_hashes": 0}

    data = json.loads(path.read_text(encoding="utf-8"))
    for info in data.get("candidates", {}).values():
        duckdb_store.upsert_candidate(dict(info))
    for strategy_hash, raw in data.get("seen_hashes", {}).items():
        duckdb_store.mark_seen(strategy_hash, SimpleNamespace(**raw))
    return {
        "candidates": len(data.get("candidates", {})),
        "seen_hashes": len(data.get("seen_hashes", {})),
    }
