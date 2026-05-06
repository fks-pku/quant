from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import duckdb

from quant.domain.ports.research_store import ResearchStore
from quant.infrastructure.research.repository import FileResearchStore


class DuckDBResearchStore(ResearchStore):
    def __init__(self, root_dir: Path | str, db_path: Path | str | None = None):
        root = Path(root_dir)
        if db_path is None and root.suffix.lower() == ".duckdb":
            self.db_path = root
            self.root_dir = root.parent
        else:
            self.root_dir = root
            self.db_path = Path(db_path) if db_path is not None else self.root_dir / "research_state.duckdb"
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._artifact_store = FileResearchStore(self.root_dir)
        self._conn: duckdb.DuckDBPyConnection | None = duckdb.connect(str(self.db_path))
        self._ensure_schema()

    def upsert_candidate(self, info: Dict[str, Any]) -> None:
        sid = str(info["id"])
        with self._lock:
            existing = self.get_candidate(sid) or {}
            merged = {**existing, **info}
            if existing and existing.get("status") not in (None, "candidate") and info.get("status") == "candidate":
                merged["status"] = existing["status"]
            research_meta = merged.get("research_meta") or {}
            now = self._now()
            self._conn.execute(
                """
                INSERT INTO candidates (
                    id, name, status, rejection_reason, research_meta_json, data_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    status = excluded.status,
                    rejection_reason = excluded.rejection_reason,
                    research_meta_json = excluded.research_meta_json,
                    data_json = excluded.data_json,
                    updated_at = excluded.updated_at
                """,
                [
                    sid,
                    merged.get("name", ""),
                    merged.get("status"),
                    research_meta.get("rejection_reason", ""),
                    self._dumps(research_meta),
                    self._dumps(merged),
                    now,
                    now,
                ],
            )

    def get_candidate(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data_json FROM candidates WHERE id = ?",
                [strategy_id],
            ).fetchone()
        if row is None:
            return None
        return self._loads(row[0])

    def list_by_status(self, status: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data_json FROM candidates WHERE status = ? ORDER BY id",
                [status],
            ).fetchall()
        return [self._loads(row[0]) for row in rows]

    def update_status(self, strategy_id: str, status: str, reason: str = "") -> bool:
        with self._lock:
            info = self.get_candidate(strategy_id)
            if info is None:
                return False
            info["status"] = status
            if reason:
                info.setdefault("research_meta", {})["rejection_reason"] = reason
            research_meta = info.get("research_meta") or {}
            self._conn.execute(
                """
                UPDATE candidates
                SET status = ?, rejection_reason = ?, research_meta_json = ?, data_json = ?, updated_at = ?
                WHERE id = ?
                """,
                [
                    status,
                    research_meta.get("rejection_reason", ""),
                    self._dumps(research_meta),
                    self._dumps(info),
                    self._now(),
                    strategy_id,
                ],
            )
            return True

    def has_seen(self, strategy_hash: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM seen_hashes WHERE strategy_hash = ?",
                [strategy_hash],
            ).fetchone()
        return row is not None

    def mark_seen(self, strategy_hash: str, raw: Any) -> None:
        self.upsert_seen(
            strategy_hash=strategy_hash,
            title=getattr(raw, "title", ""),
            source=getattr(raw, "source", ""),
            source_url=getattr(raw, "source_url", ""),
            seen_at=self._now(),
        )

    def upsert_seen(
        self,
        strategy_hash: str,
        title: str = "",
        source: str = "",
        source_url: str = "",
        seen_at: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO seen_hashes (strategy_hash, title, source, source_url, seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(strategy_hash) DO UPDATE SET
                    title = excluded.title,
                    source = excluded.source,
                    source_url = excluded.source_url,
                    seen_at = excluded.seen_at
                """,
                [strategy_hash, title, source, source_url, seen_at or self._now()],
            )

    def write_discoveries(self, raw_strategies: Iterable[Any]) -> None:
        self._artifact_store.write_discoveries(raw_strategies)

    def write_evaluations(self, evaluations: Iterable[Tuple[Any, Any, str, str]]) -> None:
        self._artifact_store.write_evaluations(evaluations)

    def save_run_result(self, result: Any) -> None:
        self._artifact_store.save_run_result(result)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self) -> "DuckDBResearchStore":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def _ensure_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS candidates (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    status TEXT,
                    rejection_reason TEXT,
                    research_meta_json TEXT,
                    data_json TEXT NOT NULL,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_hashes (
                    strategy_hash TEXT PRIMARY KEY,
                    title TEXT,
                    source TEXT,
                    source_url TEXT,
                    seen_at TEXT
                )
                """
            )

    @staticmethod
    def _dumps(data: Dict[str, Any]) -> str:
        return json.dumps(data, ensure_ascii=False, default=str)

    @staticmethod
    def _loads(data: str) -> Dict[str, Any]:
        value = json.loads(data)
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
