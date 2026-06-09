from __future__ import annotations

import json
from hashlib import sha256
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from quant.domain.ports.research_store import ResearchStore
from quant.infrastructure.research.asset_paths import (
    IDEA_BANK_JSON,
    IDEA_BANK_MD,
    DISCOVERED_STRATEGIES_MD,
    FULL_REPORT_HTML,
    INITIAL_SCREENING_TABLE_MD,
    LAST_RESULT_JSON,
    LATEST_REPORT_DIR,
    LATEST_REPORT_METADATA,
    STAGE_REPORT_HTML,
    STRATEGY_EVALUATION_MD,
    latest_full_report_html_path,
    latest_stage_report_html_path,
    report_dir,
    report_id_for_result,
)
from quant.infrastructure.research.initial_screening_table import (
    initial_screening_table_markdown,
)
from quant.infrastructure.research.reporting import build_research_full_report_html, build_research_stage_report_html


class FileResearchStore(ResearchStore):
    def __init__(self, root_dir: Path | str):
        self.root_dir = Path(root_dir)
        self.state_path = self.root_dir / "research_state.json"

    def upsert_candidate(self, info: Dict[str, Any]) -> None:
        state = self._load_state()
        candidates = state.setdefault("candidates", {})
        sid = str(info["id"])
        existing = candidates.get(sid, {})
        merged = {**existing, **info}
        if existing and existing.get("status") not in (None, "candidate") and info.get("status") == "candidate":
            merged["status"] = existing["status"]
        candidates[sid] = merged
        self._save_state(state)

    def get_candidate(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        return self._load_state().get("candidates", {}).get(strategy_id)

    def list_by_status(self, status: str) -> List[Dict[str, Any]]:
        candidates = self._load_state().get("candidates", {}).values()
        return [dict(info) for info in candidates if info.get("status") == status]

    def update_status(self, strategy_id: str, status: str, reason: str = "") -> bool:
        state = self._load_state()
        candidates = state.setdefault("candidates", {})
        info = candidates.get(strategy_id)
        if info is None:
            return False
        info["status"] = status
        if reason:
            info.setdefault("research_meta", {})["rejection_reason"] = reason
        self._save_state(state)
        return True

    def upsert_hypothesis(self, info: Dict[str, Any]) -> None:
        state = self._load_state()
        hypotheses = state.setdefault("hypotheses", {})
        hid = str(info["hypothesis_id"])
        existing = hypotheses.get(hid, {})
        now = self._now()
        merged = {**existing, **info}
        merged["hypothesis_id"] = hid
        merged["strategy_id"] = merged.get("strategy_id", "")
        merged["metrics"] = {**(existing.get("metrics") or {}), **(info.get("metrics") or {})}
        merged["evidence"] = {**(existing.get("evidence") or {}), **(info.get("evidence") or {})}
        merged["created_at"] = existing.get("created_at") or info.get("created_at") or now
        merged["updated_at"] = now
        hypotheses[hid] = merged
        self._save_state(state)

    def get_hypothesis(self, hypothesis_id: str) -> Optional[Dict[str, Any]]:
        info = self._load_state().get("hypotheses", {}).get(hypothesis_id)
        return dict(info) if info is not None else None

    def list_hypotheses(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        hypotheses = self._load_state().get("hypotheses", {}).values()
        rows = [dict(info) for info in hypotheses]
        if status is None:
            return rows
        return [info for info in rows if info.get("status") == status]

    def upsert_idea(self, raw: Any, status: str = "discovered", run_id: str = "", reason: str = "") -> None:
        state = self._load_state()
        ideas = state.setdefault("ideas", {})
        idea_id = self._idea_id(raw)
        existing = ideas.get(idea_id, {})
        now = self._now()
        final_status = self._merged_idea_status(existing.get("status", ""), status)
        record = {
            **existing,
            "idea_id": idea_id,
            "title": self._raw_field(raw, "title"),
            "description": self._raw_field(raw, "description"),
            "source": self._raw_field(raw, "source"),
            "source_url": self._raw_field(raw, "source_url"),
            "authors": self._raw_field(raw, "authors"),
            "published_date": self._raw_field(raw, "published_date"),
            "metadata": dict(self._raw_field(raw, "metadata") or {}),
            "status": final_status,
            "reason": reason if final_status == status else existing.get("reason", ""),
            "run_id": run_id or existing.get("run_id", ""),
            "discovered_at": existing.get("discovered_at") or now,
            "updated_at": now,
        }
        ideas[idea_id] = record
        self._save_state(state)
        self._write_idea_bank_artifacts(ideas.values())

    def list_ideas(self, status: Optional[Any] = None) -> List[Dict[str, Any]]:
        rows = [dict(info) for info in self._load_state().get("ideas", {}).values()]
        if status is None:
            return rows
        if isinstance(status, str):
            statuses = {status}
        else:
            statuses = set(status or [])
        return [info for info in rows if info.get("status") in statuses]

    def has_seen(self, strategy_hash: str) -> bool:
        return strategy_hash in self._load_state().get("seen_hashes", {})

    def mark_seen(self, strategy_hash: str, raw: Any) -> None:
        state = self._load_state()
        state.setdefault("seen_hashes", {})[strategy_hash] = {
            "title": getattr(raw, "title", ""),
            "source": getattr(raw, "source", ""),
            "source_url": getattr(raw, "source_url", ""),
            "seen_at": self._now(),
        }
        self._save_state(state)

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

    def write_initial_screening_table(self, rows: Iterable[Dict[str, Any]]) -> None:
        data = list(rows)
        self._write_text(LATEST_REPORT_DIR / INITIAL_SCREENING_TABLE_MD, initial_screening_table_markdown(data))

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
        self._delete_file(report_root / "full_research_report.md")
        self._delete_file(LATEST_REPORT_DIR / "full_research_report.md")
        full_metadata = {"available": False}
        full_report_allowed = bool(data.get("full_report_allowed", True))
        if hypotheses and full_report_allowed:
            full_report = build_research_full_report_html(data, hypotheses, generated_at=data["saved_at"])
            self._write_text(report_root / FULL_REPORT_HTML, full_report)
            self._write_text(latest_full_report_html_path(), full_report)
            self._write_text(report_root / "runs" / f"{run_name}_{FULL_REPORT_HTML.name}", full_report)
            full_metadata = {
                "available": True,
                "path": str(report_root / FULL_REPORT_HTML),
                "latest_path": str(latest_full_report_html_path()),
                "filename": FULL_REPORT_HTML.as_posix(),
            }
        else:
            self._delete_file(report_root / FULL_REPORT_HTML)
            self._delete_file(latest_full_report_html_path())
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
                "full_report": full_metadata,
                "stage_reports": stage_metadata,
            },
        )

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return {"candidates": {}, "seen_hashes": {}, "hypotheses": {}, "ideas": {}}
        try:
            with self.state_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return {"candidates": {}, "seen_hashes": {}, "hypotheses": {}, "ideas": {}}
        data.setdefault("candidates", {})
        data.setdefault("seen_hashes", {})
        data.setdefault("hypotheses", {})
        data.setdefault("ideas", {})
        return data

    def _save_state(self, state: Dict[str, Any]) -> None:
        self._write_json(self.state_path.name, state)

    def _write_json(self, relative_path: Path | str, data: Dict[str, Any]) -> None:
        path = self.root_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        tmp_path.replace(path)

    def _write_text(self, relative_path: Path | str, text: str) -> None:
        path = self.root_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _delete_file(self, relative_path: Path | str) -> None:
        path = self.root_dir / relative_path
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass

    def _delete_matching(self, relative_dir: Path | str, pattern: str) -> None:
        path = self.root_dir / relative_dir
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
        ignored_phases = {"stage1_queue", "local_idea_bank", "strict_backtest_stage", "walkforward_audit_stage"}
        titles = set()
        strategy_ids = set()
        for entry in getattr(result, "log", []) or []:
            phase = getattr(entry, "phase", "")
            if phase in ignored_phases:
                continue
            title = str(getattr(entry, "title", "") or "").strip()
            if title:
                titles.add(title)
                strategy_ids.add(title)
            scores = getattr(entry, "scores", {}) or {}
            strategy_id = str(scores.get("strategy_id", "") or "").strip() if isinstance(scores, dict) else ""
            if strategy_id:
                strategy_ids.add(strategy_id)
        if not titles and not strategy_ids:
            return rows
        selected = [
            row for row in rows
            if row.get("title") in titles or str(row.get("strategy_id", "") or "").strip() in strategy_ids
        ]
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
        terminal = {"candidate", "validated", "validation_failed", "rejected", "stage1_rejected", "needs_manual_spec", "error"}
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
