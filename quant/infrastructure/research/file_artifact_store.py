from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from quant.domain.ports import ResearchArtifactStore


class FileArtifactStore(ResearchArtifactStore):
    def __init__(self, root_dir: Path | str):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def save_json(self, run_id: str, name: str, data: Dict[str, Any]) -> Dict[str, Any]:
        path = self._artifact_path(run_id, name, ".json")
        path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, default=str), encoding="utf-8")
        return self._metadata(run_id, name, "json", path, {"format": "json"})

    def save_table(self, run_id: str, name: str, table: Any) -> Dict[str, Any]:
        path = self._artifact_path(run_id, name, ".json")
        path.write_text(json.dumps(table, ensure_ascii=False, sort_keys=True, indent=2, default=str), encoding="utf-8")
        row_count = len(table) if hasattr(table, "__len__") else None
        return self._metadata(run_id, name, "table", path, {"format": "json", "row_count": row_count})

    def load_artifact(self, artifact_id: str) -> Any:
        path = Path(artifact_id)
        if not path.is_absolute():
            path = self.root_dir / path
        return json.loads(path.read_text(encoding="utf-8"))

    def _artifact_path(self, run_id: str, name: str, suffix: str) -> Path:
        safe_run_id = self._safe_part(run_id)
        safe_name = self._safe_part(name)
        if not safe_name.endswith(suffix):
            safe_name = f"{safe_name}{suffix}"
        path = self.root_dir / "experiments" / safe_run_id / safe_name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _metadata(self, run_id: str, name: str, artifact_type: str, path: Path, metadata: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "artifact_id": str(path),
            "run_id": run_id,
            "artifact_type": artifact_type,
            "name": Path(name).stem,
            "path": str(path),
            "metadata": metadata,
        }

    @staticmethod
    def _safe_part(value: str) -> str:
        return str(value).replace("\\", "_").replace("/", "_")
