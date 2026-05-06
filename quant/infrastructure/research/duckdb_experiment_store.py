from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import duckdb

from quant.domain.ports import ExperimentStore


class DuckDBExperimentStore(ExperimentStore):
    def __init__(self, root_dir_or_db_path: Path | str, db_path: Path | str | None = None):
        root = Path(root_dir_or_db_path)
        if db_path is None and root.suffix.lower() == ".duckdb":
            self.db_path = root
            self.root_dir = root.parent
        else:
            self.root_dir = root
            self.db_path = Path(db_path) if db_path is not None else self.root_dir / "research_state.duckdb"
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn: duckdb.DuckDBPyConnection | None = duckdb.connect(str(self.db_path))
        self._ensure_schema()

    def start_run(self, strategy_id: str, metadata: Dict[str, Any]) -> str:
        run_id = uuid.uuid4().hex
        now = self._now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO runs (run_id, strategy_id, status, started_at, completed_at, metadata_json, error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [run_id, strategy_id, "running", now, None, self._dumps(metadata), ""],
            )
        return run_id

    def record_metrics(self, run_id: str, metrics: Iterable[Dict[str, Any]]) -> None:
        rows = []
        now = self._now()
        for metric in metrics:
            rows.append(
                [
                    uuid.uuid4().hex,
                    run_id,
                    str(metric.get("strategy_id", "")),
                    str(metric.get("metric_name", "")),
                    float(metric.get("metric_value", 0.0)),
                    str(metric.get("window_type", "")),
                    str(metric.get("window_label", "")),
                    now,
                ]
            )
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                """
                INSERT INTO metrics (
                    metric_id, run_id, strategy_id, metric_name, metric_value, window_type, window_label, recorded_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def complete_run(self, run_id: str, status: str, error: str = "") -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE runs
                SET status = ?, completed_at = ?, error = ?
                WHERE run_id = ?
                """,
                [status, self._now(), error, run_id],
            )

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT run_id, strategy_id, status, started_at, completed_at, metadata_json, error
                FROM runs
                WHERE run_id = ?
                """,
                [run_id],
            ).fetchone()
        return self._run_from_row(row) if row is not None else None

    def list_runs(self, strategy_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        if strategy_id is None:
            sql = """
                SELECT run_id, strategy_id, status, started_at, completed_at, metadata_json, error
                FROM runs
                ORDER BY started_at DESC, run_id DESC
                LIMIT ?
            """
            params: List[Any] = [limit]
        else:
            sql = """
                SELECT run_id, strategy_id, status, started_at, completed_at, metadata_json, error
                FROM runs
                WHERE strategy_id = ?
                ORDER BY started_at DESC, run_id DESC
                LIMIT ?
            """
            params = [strategy_id, limit]
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._run_from_row(row) for row in rows]

    def list_metrics(self, run_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT metric_id, run_id, strategy_id, metric_name, metric_value, window_type, window_label, recorded_at
                FROM metrics
                WHERE run_id = ?
                ORDER BY recorded_at, metric_id
                """,
                [run_id],
            ).fetchall()
        return [
            {
                "metric_id": row[0],
                "run_id": row[1],
                "strategy_id": row[2],
                "metric_name": row[3],
                "metric_value": row[4],
                "window_type": row[5],
                "window_label": row[6],
                "recorded_at": row[7],
            }
            for row in rows
        ]

    def get_artifacts(self, run_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT artifact_id, run_id, artifact_type, name, path, metadata_json, created_at
                FROM artifacts
                WHERE run_id = ?
                ORDER BY created_at, artifact_id
                """,
                [run_id],
            ).fetchall()
        return [
            {
                "artifact_id": row[0],
                "run_id": row[1],
                "artifact_type": row[2],
                "name": row[3],
                "path": row[4],
                "metadata": self._loads(row[5]),
                "created_at": row[6],
            }
            for row in rows
        ]

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self) -> "DuckDBExperimentStore":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def _ensure_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    metadata_json TEXT NOT NULL,
                    error TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metrics (
                    metric_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    metric_value DOUBLE NOT NULL,
                    window_type TEXT NOT NULL,
                    window_label TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _run_from_row(row: Any) -> Dict[str, Any]:
        return {
            "run_id": row[0],
            "strategy_id": row[1],
            "status": row[2],
            "started_at": row[3],
            "completed_at": row[4],
            "metadata": DuckDBExperimentStore._loads(row[5]),
            "error": row[6],
        }

    @staticmethod
    def _dumps(data: Dict[str, Any]) -> str:
        return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _loads(data: str) -> Dict[str, Any]:
        value = json.loads(data or "{}")
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
