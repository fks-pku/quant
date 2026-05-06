from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from quant.domain.ports.research_store import ResearchStore


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

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return {"candidates": {}, "seen_hashes": {}}
        try:
            with self.state_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return {"candidates": {}, "seen_hashes": {}}
        data.setdefault("candidates", {})
        data.setdefault("seen_hashes", {})
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
