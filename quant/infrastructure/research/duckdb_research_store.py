from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import duckdb

from quant.domain.ports.research_store import ResearchStore


class DuckDBResearchStore(ResearchStore):
    def __init__(self, db_path: str, artifact_root: str):
        self._db_path = db_path
        self._artifact_root = Path(artifact_root)
        self._artifact_root.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with duckdb.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candidates (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    description TEXT,
                    status TEXT DEFAULT 'candidate',
                    priority INTEGER DEFAULT 999,
                    source TEXT,
                    source_url TEXT,
                    research_meta JSON,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS seen_hashes (
                    hash TEXT PRIMARY KEY,
                    title TEXT,
                    source TEXT,
                    source_url TEXT,
                    seen_at TEXT
                )
            """)

    def upsert_candidate(self, info: Dict[str, Any]) -> None:
        sid = str(info["id"])
        existing = self._get_candidate_row(sid)

        if existing:
            merged = {**existing, **info}
            if existing.get("status") not in (None, "candidate") and info.get("status") == "candidate":
                merged["status"] = existing["status"]
        else:
            merged = dict(info)

        self._upsert_row(sid, merged)

    def get_candidate(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        return self._get_candidate_row(strategy_id)

    def list_by_status(self, status: str) -> List[Dict[str, Any]]:
        with duckdb.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT id, name, description, status, priority, source, source_url, research_meta, created_at, updated_at FROM candidates WHERE status = ?",
                [status],
            ).fetchall()
        result = []
        for row in rows:
            d = self._row_to_dict(row)
            if d.get("status") == status:
                result.append(d)
        return result

    def update_status(self, strategy_id: str, status: str, reason: str = "") -> bool:
        existing = self._get_candidate_row(strategy_id)
        if existing is None:
            return False
        existing["status"] = status
        if reason:
            meta = existing.get("research_meta") or {}
            meta["rejection_reason"] = reason
            existing["research_meta"] = meta
        self._upsert_row(strategy_id, existing)
        return True

    def has_seen(self, strategy_hash: str) -> bool:
        with duckdb.connect(self._db_path) as conn:
            row = conn.execute("SELECT 1 FROM seen_hashes WHERE hash = ?", [strategy_hash]).fetchone()
        return row is not None

    def mark_seen(self, strategy_hash: str, raw: Any) -> None:
        with duckdb.connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO seen_hashes (hash, title, source, source_url, seen_at) VALUES (?, ?, ?, ?, ?)",
                [
                    strategy_hash,
                    getattr(raw, "title", ""),
                    getattr(raw, "source", ""),
                    getattr(raw, "source_url", ""),
                    self._now(),
                ],
            )

    def write_discoveries(self, raw_strategies: Iterable[Any]) -> None:
        lines = ["# Discovered Strategies", ""]
        for raw in raw_strategies:
            title = getattr(raw, "title", "")
            description = getattr(raw, "description", "")
            source = getattr(raw, "source", "")
            source_url = getattr(raw, "source_url", "")
            published_date = getattr(raw, "published_date", None)
            authors = getattr(raw, "authors", None)
            lines.extend(
                [
                    f"## {title}",
                    f"- **Source**: [{source}]({source_url})",
                    f"- **Published**: {published_date or 'Unknown'}",
                    f"- **Authors**: {authors or 'Unknown'}",
                    f"- **Core Idea**: {description[:300]}",
                    "",
                ]
            )
        self._write_text("discovered_strategies.md", "\n".join(lines))

    def write_evaluations(self, evaluations: Iterable[Tuple[Any, Any, str, str]]) -> None:
        lines = ["# Strategy Evaluation", ""]
        for raw, report, verdict, reason in evaluations:
            lines.extend(
                [
                    f"## {getattr(raw, 'title', '')}",
                    f"- **Verdict**: {verdict}",
                    f"- **Reason**: {reason}",
                    f"- **Suitability**: {self._fmt(report, 'suitability_score')}",
                    f"- **Complexity**: {self._fmt(report, 'complexity_score')}",
                    f"- **economic_rationale**: {self._fmt(report, 'economic_rationale_score')}",
                    f"- **factor_uniqueness**: {self._fmt(report, 'factor_uniqueness_score')}",
                    f"- **data_availability**: {self._fmt(report, 'data_availability_score')}",
                    f"- **implementation**: {self._fmt(report, 'implementation_score')}",
                    f"- **overfit_risk**: {self._fmt(report, 'overfit_risk_score')}",
                    f"- **cost_capacity**: {self._fmt(report, 'cost_capacity_score')}",
                    f"- **regime_robustness**: {self._fmt(report, 'regime_robustness_score')}",
                    f"- **Risk Flags**: {self._risk_flags(report)}",
                    f"- **Summary**: {getattr(report, 'summary', '')}",
                    "",
                ]
            )
        self._write_text("strategy_evaluation.md", "\n".join(lines))

    def save_run_result(self, result: Any) -> None:
        data = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        data["saved_at"] = self._now()
        self._write_json("last_result.json", data)
        run_name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._write_json(Path("runs") / f"{run_name}_result.json", data)

    def _get_candidate_row(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        with duckdb.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT id, name, description, status, priority, source, source_url, research_meta, created_at, updated_at FROM candidates WHERE id = ?",
                [strategy_id],
            ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def _row_to_dict(self, row: tuple) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": row[0],
            "name": row[1],
            "description": row[2],
            "status": row[3],
            "priority": row[4],
            "source": row[5],
            "source_url": row[6],
        }
        raw_meta = row[7]
        if raw_meta is not None:
            if isinstance(raw_meta, str):
                d["research_meta"] = json.loads(raw_meta)
            elif isinstance(raw_meta, dict):
                d["research_meta"] = raw_meta
            else:
                d["research_meta"] = {}
        else:
            d["research_meta"] = {}
        if row[9] is not None:
            d["updated_at"] = row[9]
        if row[8] is not None:
            d["created_at"] = row[8]
        return d

    def _upsert_row(self, sid: str, merged: Dict[str, Any]) -> None:
        now = self._now()
        created = merged.get("created_at") or now
        meta = merged.get("research_meta", {})
        if isinstance(meta, str):
            meta = json.loads(meta)
        meta_json = json.dumps(meta, default=str, ensure_ascii=False)

        with duckdb.connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO candidates
                   (id, name, description, status, priority, source, source_url, research_meta, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?::JSON, ?, ?)""",
                [
                    sid,
                    merged.get("name", ""),
                    merged.get("description", ""),
                    merged.get("status", "candidate"),
                    merged.get("priority", 999),
                    merged.get("source", ""),
                    merged.get("source_url", ""),
                    meta_json,
                    created,
                    now,
                ],
            )

    def _write_json(self, relative_path: Path | str, data: Dict[str, Any]) -> None:
        path = self._artifact_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        tmp_path.replace(path)

    def _write_text(self, relative_path: Path | str, text: str) -> None:
        path = self._artifact_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _fmt(report: Any, field: str) -> str:
        return f"{float(getattr(report, field, 0.0)):.2f}"

    @staticmethod
    def _risk_flags(report: Any) -> str:
        flags = getattr(report, "risk_flags", [])
        return ", ".join(flags) if flags else "None"
