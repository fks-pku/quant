from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from quant.domain.ports.research_artifact_store import ResearchArtifactStore


class FileArtifactStore(ResearchArtifactStore):
    def __init__(self, root_dir: str):
        self._root = Path(root_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        self._index: Dict[str, Dict[str, Any]] = {}

    def save_json(self, run_id: str, name: str, data: Dict[str, Any]) -> Dict[str, Any]:
        run_dir = self._root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / f"{name}.json"
        path.write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
        artifact_id = uuid.uuid4().hex[:12]
        meta = {
            "artifact_id": artifact_id,
            "run_id": run_id,
            "artifact_type": "json",
            "name": name,
            "path": str(path),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._index[artifact_id] = {**meta, "_path": path}
        return meta

    def save_table(self, run_id: str, name: str, table: Any) -> Dict[str, Any]:
        run_dir = self._root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / f"{name}.json"
        data = table if isinstance(table, (list, dict)) else {"rows": list(table)}
        path.write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
        artifact_id = uuid.uuid4().hex[:12]
        meta = {
            "artifact_id": artifact_id,
            "run_id": run_id,
            "artifact_type": "table",
            "name": name,
            "path": str(path),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._index[artifact_id] = {**meta, "_path": path}
        return meta

    def load_artifact(self, artifact_id: str) -> Any:
        entry = self._index.get(artifact_id)
        if entry is None:
            return None
        path = Path(entry["_path"])
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
