from __future__ import annotations

import json
from hashlib import sha256
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import duckdb

from quant.domain.ports.research_store import ResearchStore
from quant.infrastructure.research.asset_paths import (
    IDEA_BANK_JSON,
    IDEA_BANK_MD,
    DISCOVERED_STRATEGIES_MD,
    LAST_RESULT_JSON,
    LATEST_REPORT_DIR,
    LATEST_REPORT_METADATA,
    STAGE_REPORT_HTML,
    STRATEGY_EVALUATION_MD,
    latest_stage_report_html_path,
    report_dir,
    report_id_for_result,
)
from quant.infrastructure.research.reporting import build_research_stage_report_html


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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS hypotheses (
                    hypothesis_id TEXT PRIMARY KEY,
                    strategy_id TEXT,
                    title TEXT,
                    status TEXT,
                    stage TEXT,
                    source TEXT,
                    source_url TEXT,
                    thesis TEXT,
                    decision_reason TEXT,
                    metrics JSON,
                    evidence JSON,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS idea_bank (
                    idea_id TEXT PRIMARY KEY,
                    title TEXT,
                    description TEXT,
                    source TEXT,
                    source_url TEXT,
                    authors TEXT,
                    published_date TEXT,
                    status TEXT,
                    reason TEXT,
                    run_id TEXT,
                    metadata JSON,
                    discovered_at TEXT,
                    updated_at TEXT
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

    def upsert_hypothesis(self, info: Dict[str, Any]) -> None:
        hid = str(info["hypothesis_id"])
        existing = self.get_hypothesis(hid) or {}
        now = self._now()
        merged = {**existing, **info}
        merged["hypothesis_id"] = hid
        merged["strategy_id"] = merged.get("strategy_id", "")
        merged["metrics"] = {**(existing.get("metrics") or {}), **(info.get("metrics") or {})}
        merged["evidence"] = {**(existing.get("evidence") or {}), **(info.get("evidence") or {})}
        merged["created_at"] = existing.get("created_at") or info.get("created_at") or now
        merged["updated_at"] = now
        self._upsert_hypothesis_row(hid, merged)

    def get_hypothesis(self, hypothesis_id: str) -> Optional[Dict[str, Any]]:
        with duckdb.connect(self._db_path) as conn:
            row = conn.execute(
                """SELECT hypothesis_id, strategy_id, title, status, stage, source, source_url,
                          thesis, decision_reason, metrics, evidence, created_at, updated_at
                   FROM hypotheses WHERE hypothesis_id = ?""",
                [hypothesis_id],
            ).fetchone()
        if row is None:
            return None
        return self._hypothesis_row_to_dict(row)

    def list_hypotheses(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with duckdb.connect(self._db_path) as conn:
            if status is None:
                rows = conn.execute(
                    """SELECT hypothesis_id, strategy_id, title, status, stage, source, source_url,
                              thesis, decision_reason, metrics, evidence, created_at, updated_at
                       FROM hypotheses ORDER BY updated_at, hypothesis_id"""
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT hypothesis_id, strategy_id, title, status, stage, source, source_url,
                              thesis, decision_reason, metrics, evidence, created_at, updated_at
                       FROM hypotheses WHERE status = ? ORDER BY updated_at, hypothesis_id""",
                    [status],
                ).fetchall()
        return [self._hypothesis_row_to_dict(row) for row in rows]

    def upsert_idea(self, raw: Any, status: str = "discovered", run_id: str = "", reason: str = "") -> None:
        idea_id = self._idea_id(raw)
        existing = self._get_idea_row(idea_id) or {}
        now = self._now()
        discovered_at = existing.get("discovered_at") or now
        metadata = dict(self._raw_field(raw, "metadata") or {})
        metadata_json = json.dumps(metadata, default=str, ensure_ascii=False)
        final_status = self._merged_idea_status(existing.get("status", ""), status)
        with duckdb.connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO idea_bank
                   (idea_id, title, description, source, source_url, authors, published_date,
                    status, reason, run_id, metadata, discovered_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::JSON, ?, ?)""",
                [
                    idea_id,
                    self._raw_field(raw, "title"),
                    self._raw_field(raw, "description"),
                    self._raw_field(raw, "source"),
                    self._raw_field(raw, "source_url"),
                    self._raw_field(raw, "authors"),
                    self._raw_field(raw, "published_date"),
                    final_status,
                    reason if final_status == status else existing.get("reason", ""),
                    run_id or existing.get("run_id", ""),
                    metadata_json,
                    discovered_at,
                    now,
                ],
            )
        self._write_idea_bank_artifacts(self.list_ideas())

    def list_ideas(self, status: Optional[Any] = None) -> List[Dict[str, Any]]:
        with duckdb.connect(self._db_path) as conn:
            if status is None:
                rows = conn.execute(
                    """SELECT idea_id, title, description, source, source_url, authors, published_date,
                              status, reason, run_id, metadata, discovered_at, updated_at
                       FROM idea_bank ORDER BY updated_at, idea_id"""
                ).fetchall()
            else:
                statuses = [status] if isinstance(status, str) else list(status or [])
                if not statuses:
                    return []
                placeholders = ", ".join("?" for _ in statuses)
                rows = conn.execute(
                    f"""SELECT idea_id, title, description, source, source_url, authors, published_date,
                               status, reason, run_id, metadata, discovered_at, updated_at
                        FROM idea_bank WHERE status IN ({placeholders}) ORDER BY updated_at, idea_id""",
                    statuses,
                ).fetchall()
        return [self._idea_row_to_dict(row) for row in rows]

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
            quality = (getattr(raw, "metadata", {}) or {}).get("discovery_quality", {})
            lines.extend(
                [
                    f"## {title}",
                    f"- **Source**: [{source}]({source_url})",
                    f"- **Published**: {published_date or 'Unknown'}",
                    f"- **Authors**: {authors or 'Unknown'}",
                    f"- **Discovery Quality**: {float(quality.get('score', 0.0) or 0.0):.2f}",
                    f"- **Discovery Flags**: {', '.join(quality.get('risk_flags', []) or []) or 'None'}",
                    f"- **Core Idea**: {description[:300]}",
                    "",
                ]
            )
        self._write_text(DISCOVERED_STRATEGIES_MD, "\n".join(lines))

    def write_evaluations(self, evaluations: Iterable[Tuple[Any, Any, str, str]]) -> None:
        lines = ["# Strategy Evaluation", ""]
        for raw, report, verdict, reason in evaluations:
            lines.extend(
                [
                    f"## {getattr(raw, 'title', '')}",
                    f"- **Verdict**: {verdict}",
                    f"- **Reason**: {reason}",
                    f"- **Suitability**: {self._fmt(report, 'suitability_score')}",
                    f"- **Admission**: {self._fmt(report, 'admission_score')}",
                    f"- **Signal Quality**: {self._fmt(report, 'signal_quality_score')}",
                    f"- **Research Confidence**: {self._fmt(report, 'research_confidence_score')}",
                    f"- **Complexity**: {self._fmt(report, 'complexity_score')}",
                    f"- **economic_rationale**: {self._fmt(report, 'economic_rationale_score')}",
                    f"- **factor_uniqueness**: {self._fmt(report, 'factor_uniqueness_score')}",
                    f"- **data_availability**: {self._fmt(report, 'data_availability_score')}",
                    f"- **implementation**: {self._fmt(report, 'implementation_score')}",
                    f"- **overfit_risk**: {self._fmt(report, 'overfit_risk_score')}",
                    f"- **cost_capacity**: {self._fmt(report, 'cost_capacity_score')}",
                    f"- **regime_robustness**: {self._fmt(report, 'regime_robustness_score')}",
                    f"- **Validation Tests**: {self._list_field(report, 'validation_tests')}",
                    f"- **Required Data Fields**: {self._list_field(report, 'required_data_fields')}",
                    f"- **Risk Flags**: {self._risk_flags(report)}",
                    f"- **Summary**: {getattr(report, 'summary', '')}",
                    "",
                ]
            )
        self._write_text(LATEST_REPORT_DIR / STRATEGY_EVALUATION_MD, "\n".join(lines))

    def save_run_result(self, result: Any) -> None:
        data = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        data["saved_at"] = self._now()
        hypotheses = self._hypotheses_for_result(result)
        report_id = report_id_for_result(data, hypotheses)
        report_root = report_dir(report_id)
        self._write_json(report_root / LAST_RESULT_JSON, data)
        self._write_json(LATEST_REPORT_DIR / LAST_RESULT_JSON, data)
        run_name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._write_json(report_root / "runs" / f"{run_name}_result.json", data)
        for legacy_name in ("full_research_report.html", "full_research_report.md"):
            self._delete_file(report_root / legacy_name)
            self._delete_file(LATEST_REPORT_DIR / legacy_name)
        self._delete_matching(report_root / "runs", "*_full_research_report.html")
        stage_metadata = {}
        for stage_key, filename in STAGE_REPORT_HTML.items():
            stage_report = build_research_stage_report_html(stage_key, data, hypotheses, generated_at=data["saved_at"])
            self._write_text(report_root / filename, stage_report)
            self._write_text(latest_stage_report_html_path(stage_key), stage_report)
            self._write_text(report_root / "runs" / f"{run_name}_{filename.name}", stage_report)
            stage_metadata[stage_key] = {
                "path": str(report_root / filename),
                "latest_path": str(latest_stage_report_html_path(stage_key)),
                "filename": filename.as_posix(),
            }
        self._write_json(
            LATEST_REPORT_METADATA,
            {
                "report_id": report_id,
                "run_name": run_name,
                "updated_at": data["saved_at"],
                "stage_reports": stage_metadata,
            },
        )

    def _get_candidate_row(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        with duckdb.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT id, name, description, status, priority, source, source_url, research_meta, created_at, updated_at FROM candidates WHERE id = ?",
                [strategy_id],
            ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def _get_idea_row(self, idea_id: str) -> Optional[Dict[str, Any]]:
        with duckdb.connect(self._db_path) as conn:
            row = conn.execute(
                """SELECT idea_id, title, description, source, source_url, authors, published_date,
                          status, reason, run_id, metadata, discovered_at, updated_at
                   FROM idea_bank WHERE idea_id = ?""",
                [idea_id],
            ).fetchone()
        if row is None:
            return None
        return self._idea_row_to_dict(row)

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

    def _hypothesis_row_to_dict(self, row: tuple) -> Dict[str, Any]:
        return {
            "hypothesis_id": row[0],
            "strategy_id": row[1] or "",
            "title": row[2] or "",
            "status": row[3] or "",
            "stage": row[4] or "",
            "source": row[5] or "",
            "source_url": row[6] or "",
            "thesis": row[7] or "",
            "decision_reason": row[8] or "",
            "metrics": self._json_value(row[9]),
            "evidence": self._json_value(row[10]),
            "created_at": row[11],
            "updated_at": row[12],
        }

    def _idea_row_to_dict(self, row: tuple) -> Dict[str, Any]:
        return {
            "idea_id": row[0],
            "title": row[1] or "",
            "description": row[2] or "",
            "source": row[3] or "",
            "source_url": row[4] or "",
            "authors": row[5] or "",
            "published_date": row[6] or "",
            "status": row[7] or "",
            "reason": row[8] or "",
            "run_id": row[9] or "",
            "metadata": self._json_value(row[10]),
            "discovered_at": row[11],
            "updated_at": row[12],
        }

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

    def _upsert_hypothesis_row(self, hid: str, merged: Dict[str, Any]) -> None:
        metrics_json = json.dumps(merged.get("metrics", {}), default=str, ensure_ascii=False)
        evidence_json = json.dumps(merged.get("evidence", {}), default=str, ensure_ascii=False)
        with duckdb.connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO hypotheses
                   (hypothesis_id, strategy_id, title, status, stage, source, source_url, thesis,
                    decision_reason, metrics, evidence, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?::JSON, ?::JSON, ?, ?)""",
                [
                    hid,
                    merged.get("strategy_id", ""),
                    merged.get("title", ""),
                    merged.get("status", ""),
                    merged.get("stage", ""),
                    merged.get("source", ""),
                    merged.get("source_url", ""),
                    merged.get("thesis", ""),
                    merged.get("decision_reason", ""),
                    metrics_json,
                    evidence_json,
                    merged.get("created_at"),
                    merged.get("updated_at"),
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

    def _delete_file(self, relative_path: Path | str) -> None:
        path = self._artifact_root / relative_path
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass

    def _delete_matching(self, relative_dir: Path | str, pattern: str) -> None:
        path = self._artifact_root / relative_dir
        if not path.exists():
            return
        for item in path.glob(pattern):
            try:
                if item.is_file():
                    item.unlink()
            except OSError:
                pass

    def _hypotheses_for_result(self, result: Any) -> List[Dict[str, Any]]:
        rows = self.list_hypotheses()
        titles = {
            getattr(entry, "title", "")
            for entry in getattr(result, "log", []) or []
            if getattr(entry, "title", "") and getattr(entry, "phase", "") not in {"stage1_queue", "local_idea_bank"}
        }
        if not titles:
            return rows
        selected = [row for row in rows if row.get("title") in titles]
        return selected or rows

    def _write_idea_bank_artifacts(self, ideas: Iterable[Dict[str, Any]]) -> None:
        rows = sorted((dict(item) for item in ideas), key=lambda item: (item.get("updated_at", ""), item.get("title", "")))
        payload = {"ideas": rows, "updated_at": self._now()}
        self._write_json(IDEA_BANK_JSON, payload)
        lines = ["# Research Idea Bank", ""]
        for item in rows:
            quality = (item.get("metadata") or {}).get("discovery_quality") or {}
            lines.extend(
                [
                    f"## {item.get('title', '')}",
                    f"- **Status**: {item.get('status', '')}",
                    f"- **Source**: [{item.get('source', '')}]({item.get('source_url', '')})",
                    f"- **Published**: {item.get('published_date') or 'Unknown'}",
                    f"- **Discovery Quality**: {float(quality.get('score', 0.0) or 0.0):.2f}",
                    f"- **Reason**: {item.get('reason', '') or 'n/a'}",
                    f"- **Core Idea**: {str(item.get('description', ''))[:500]}",
                    "",
                ]
            )
        body = "\n".join(lines)
        self._write_text(IDEA_BANK_MD, body)

    @classmethod
    def _idea_id(cls, raw: Any) -> str:
        text = "|".join(
            [
                cls._raw_field(raw, "title"),
                cls._raw_field(raw, "source"),
                cls._raw_field(raw, "source_url"),
            ]
        )
        return sha256(text.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _raw_field(raw: Any, field: str) -> Any:
        if isinstance(raw, dict):
            return raw.get(field, "")
        return getattr(raw, field, "")

    @staticmethod
    def _merged_idea_status(existing_status: str, new_status: str) -> str:
        terminal = {"candidate", "rejected", "stage1_rejected", "needs_manual_spec", "error"}
        non_terminal = {"discovered", "skipped", "research_queue"}
        if existing_status in terminal and new_status in non_terminal:
            return existing_status
        return new_status

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

    @staticmethod
    def _list_field(report: Any, field: str) -> str:
        values = getattr(report, field, [])
        return ", ".join(values) if values else "None"

    @staticmethod
    def _json_value(value: Any) -> Dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            return json.loads(value) if value else {}
        return {}
