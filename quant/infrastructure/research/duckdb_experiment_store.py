from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import duckdb

from quant.domain.ports.experiment_store import ExperimentStore


class DuckDBExperimentStore(ExperimentStore):
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with duckdb.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    strategy_id TEXT,
                    config_hash TEXT,
                    data_hash TEXT,
                    code_version TEXT,
                    status TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    error TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics (
                    run_id TEXT,
                    strategy_id TEXT,
                    metric_name TEXT,
                    metric_value DOUBLE,
                    window_type TEXT,
                    window_label TEXT,
                    metadata JSON,
                    PRIMARY KEY (run_id, strategy_id, metric_name, window_type, window_label)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    strategy_id TEXT,
                    artifact_type TEXT,
                    name TEXT,
                    path TEXT,
                    metadata JSON,
                    created_at TEXT
                )
            """)

    def start_run(self, strategy_id: str, metadata: Dict[str, Any]) -> str:
        run_id = uuid.uuid4().hex[:16]
        now = self._now()
        config_hash = metadata.get("config_hash", "")
        data_hash = metadata.get("data_hash", "")
        code_version = metadata.get("code_version", "")
        with duckdb.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO runs (run_id, strategy_id, config_hash, data_hash, code_version, status, started_at, completed_at, error) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, '')",
                [run_id, strategy_id, config_hash, data_hash, code_version, "running", now],
            )
        return run_id

    def record_metrics(self, run_id: str, metrics: Iterable[Dict[str, Any]]) -> None:
        with duckdb.connect(self._db_path) as conn:
            for m in metrics:
                conn.execute(
                    "INSERT OR REPLACE INTO metrics (run_id, strategy_id, metric_name, metric_value, window_type, window_label, metadata) VALUES (?, ?, ?, ?, ?, ?, ?::JSON)",
                    [
                        run_id,
                        m.get("strategy_id", ""),
                        m.get("metric_name", ""),
                        float(m.get("metric_value", 0.0)),
                        m.get("window_type", ""),
                        m.get("window_label", ""),
                        json.dumps(m.get("metadata", {}), default=str),
                    ],
                )

    def complete_run(self, run_id: str, status: str, error: str = "") -> None:
        now = self._now()
        with duckdb.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE runs SET status = ?, completed_at = ?, error = ? WHERE run_id = ?",
                [status, now, error, run_id],
            )

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with duckdb.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT run_id, strategy_id, config_hash, data_hash, code_version, status, started_at, completed_at, error FROM runs WHERE run_id = ?",
                [run_id],
            ).fetchone()
        if row is None:
            return None
        return self._run_to_dict(row)

    def list_runs(self, strategy_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with duckdb.connect(self._db_path) as conn:
            if strategy_id:
                rows = conn.execute(
                    "SELECT run_id, strategy_id, config_hash, data_hash, code_version, status, started_at, completed_at, error FROM runs WHERE strategy_id = ? ORDER BY started_at DESC LIMIT ?",
                    [strategy_id, limit],
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT run_id, strategy_id, config_hash, data_hash, code_version, status, started_at, completed_at, error FROM runs ORDER BY started_at DESC LIMIT ?",
                    [limit],
                ).fetchall()
        return [self._run_to_dict(r) for r in rows]

    def list_metrics(self, run_id: str) -> List[Dict[str, Any]]:
        with duckdb.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT run_id, strategy_id, metric_name, metric_value, window_type, window_label, metadata FROM metrics WHERE run_id = ?",
                [run_id],
            ).fetchall()
        return [
            {
                "run_id": r[0],
                "strategy_id": r[1],
                "metric_name": r[2],
                "metric_value": r[3],
                "window_type": r[4],
                "window_label": r[5],
                "metadata": json.loads(r[6]) if isinstance(r[6], str) else (r[6] or {}),
            }
            for r in rows
        ]

    def get_artifacts(self, run_id: str) -> List[Dict[str, Any]]:
        with duckdb.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT artifact_id, run_id, strategy_id, artifact_type, name, path, metadata, created_at FROM artifacts WHERE run_id = ?",
                [run_id],
            ).fetchall()
        return [
            {
                "artifact_id": r[0],
                "run_id": r[1],
                "strategy_id": r[2],
                "artifact_type": r[3],
                "name": r[4],
                "path": r[5],
                "metadata": json.loads(r[6]) if isinstance(r[6], str) else (r[6] or {}),
                "created_at": r[7],
            }
            for r in rows
        ]

    @staticmethod
    def _run_to_dict(row: tuple) -> Dict[str, Any]:
        return {
            "run_id": row[0],
            "strategy_id": row[1],
            "config_hash": row[2],
            "data_hash": row[3],
            "code_version": row[4],
            "status": row[5],
            "started_at": row[6],
            "completed_at": row[7],
            "error": row[8],
        }

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
