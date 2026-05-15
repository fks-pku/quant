from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from quant.domain.ports.research_store import ResearchStore
from quant.infrastructure.research.repository import FileResearchStore


@dataclass
class _FakeRaw:
    title: str = "Test Strategy"
    description: str = "A test strategy description for unit testing."
    source: str = "test"
    source_url: str = "https://example.com"
    authors: str = "Author"
    published_date: str = "2025-01-01"


@dataclass
class _FakeReport:
    suitability_score: float = 7.0
    complexity_score: float = 3.0
    economic_rationale_score: float = 5.0
    factor_uniqueness_score: float = 4.0
    data_availability_score: float = 6.0
    implementation_score: float = 5.0
    overfit_risk_score: float = 2.0
    cost_capacity_score: float = 4.0
    regime_robustness_score: float = 3.0
    risk_flags: list = None
    summary: str = "Good strategy"

    def __post_init__(self):
        if self.risk_flags is None:
            self.risk_flags = []


@dataclass
class _FakeResult:
    discovered: int = 5
    evaluated: int = 3

    def to_dict(self):
        return {"discovered": self.discovered, "evaluated": self.evaluated}


def _sample_candidate(strategy_id="strat-001", status="candidate", **extra):
    return {
        "id": strategy_id,
        "name": f"Strategy {strategy_id}",
        "description": "A strategy",
        "status": status,
        "priority": 500,
        "source": "test",
        "source_url": "https://example.com",
        **extra,
    }


def _make_file_store(tmp_path: Path) -> FileResearchStore:
    store_dir = tmp_path / "file_store"
    store_dir.mkdir()
    return FileResearchStore(str(store_dir))


def _make_duckdb_store(tmp_path: Path):
    from quant.infrastructure.research.duckdb_research_store import DuckDBResearchStore

    db_path = str(tmp_path / "test.duckdb")
    artifact_root = str(tmp_path / "artifacts")
    return DuckDBResearchStore(db_path=db_path, artifact_root=artifact_root)


def _store_factories(tmp_path: Path):
    return [
        ("FileResearchStore", _make_file_store(tmp_path)),
        ("DuckDBResearchStore", _make_duckdb_store(tmp_path)),
    ]


# ── Tests ──────────────────────────────────────────────────────────────────


class TestUpsertCandidate:
    def test_preserves_non_candidate_status(self, tmp_path):
        for name, store in _store_factories(tmp_path):
            info = _sample_candidate("s1", status="rejected", rejection_reason="bad")
            store.upsert_candidate(info)

            update = _sample_candidate("s1", status="candidate")
            store.upsert_candidate(update)

            got = store.get_candidate("s1")
            assert got["status"] == "rejected", f"{name}: status should stay rejected"

    def test_inserts_new_candidate(self, tmp_path):
        for name, store in _store_factories(tmp_path):
            info = _sample_candidate("s2", status="candidate")
            store.upsert_candidate(info)
            got = store.get_candidate("s2")
            assert got is not None, f"{name}: candidate should exist"
            assert got["name"] == "Strategy s2"

    def test_merges_fields(self, tmp_path):
        for name, store in _store_factories(tmp_path):
            store.upsert_candidate(_sample_candidate("s3", status="candidate"))
            store.upsert_candidate({**_sample_candidate("s3"), "description": "updated"})
            got = store.get_candidate("s3")
            assert got["description"] == "updated", f"{name}: should merge"


class TestGetCandidate:
    def test_returns_none_for_missing(self, tmp_path):
        for name, store in _store_factories(tmp_path):
            assert store.get_candidate("missing") is None

    def test_returns_candidate_dict(self, tmp_path):
        for name, store in _store_factories(tmp_path):
            info = _sample_candidate("s4", status="candidate", research_meta={"score": 7.5})
            store.upsert_candidate(info)
            got = store.get_candidate("s4")
            assert got["id"] == "s4"
            assert got["research_meta"]["score"] == 7.5


class TestListByStatus:
    def test_filters_by_status(self, tmp_path):
        for name, store in _store_factories(tmp_path):
            store.upsert_candidate(_sample_candidate("s5", status="candidate"))
            store.upsert_candidate(_sample_candidate("s6", status="rejected"))
            store.upsert_candidate(_sample_candidate("s7", status="candidate"))

            candidates = store.list_by_status("candidate")
            ids = {c["id"] for c in candidates}
            assert "s5" in ids and "s7" in ids, f"{name}: should list candidates"
            assert "s6" not in ids, f"{name}: should exclude rejected"

    def test_returns_empty_for_no_match(self, tmp_path):
        for name, store in _store_factories(tmp_path):
            assert store.list_by_status("nonexistent") == []


class TestUpdateStatus:
    def test_stores_rejection_reason(self, tmp_path):
        for name, store in _store_factories(tmp_path):
            store.upsert_candidate(_sample_candidate("s8", status="candidate"))
            ok = store.update_status("s8", "rejected", reason="low sharpe")
            assert ok, f"{name}: update should succeed"
            got = store.get_candidate("s8")
            assert got["status"] == "rejected"
            assert got["research_meta"]["rejection_reason"] == "low sharpe"

    def test_returns_false_for_missing(self, tmp_path):
        for name, store in _store_factories(tmp_path):
            assert store.update_status("ghost", "rejected") is False


class TestSeenHashes:
    def test_has_seen_false_initially(self, tmp_path):
        for name, store in _store_factories(tmp_path):
            assert store.has_seen("abc123") is False

    def test_mark_seen_deduplicates(self, tmp_path):
        for name, store in _store_factories(tmp_path):
            raw = _FakeRaw(title="Momentum Alpha", source="arxiv", source_url="https://arxiv.org/abs/1234")
            store.mark_seen("hash1", raw)
            assert store.has_seen("hash1"), f"{name}: should be seen"

            store.mark_seen("hash1", raw)
            assert store.has_seen("hash1")

    def test_mark_seen_stores_metadata(self, tmp_path):
        for name, store in _store_factories(tmp_path):
            raw = _FakeRaw(title="Test", source="ssrn", source_url="https://ssrn.com/1")
            store.mark_seen("h2", raw)
            assert store.has_seen("h2")


class TestWriteDiscoveries:
    def test_writes_markdown(self, tmp_path):
        for name, store in _store_factories(tmp_path):
            strategies = [
                _FakeRaw(title="Strategy A", description="Desc A"),
                _FakeRaw(title="Strategy B", description="Desc B"),
            ]
            store.write_discoveries(strategies)


class TestWriteEvaluations:
    def test_writes_evaluation_markdown(self, tmp_path):
        for name, store in _store_factories(tmp_path):
            evaluations = [
                (_FakeRaw(title="Eval A"), _FakeReport(), "pass", "good fit"),
            ]
            store.write_evaluations(evaluations)


class TestSaveRunResult:
    def test_saves_json(self, tmp_path):
        for name, store in _store_factories(tmp_path):
            result = _FakeResult(discovered=10, evaluated=5)
            store.save_run_result(result)


class TestHypothesisLedger:
    def test_upserts_and_lists_hypotheses(self, tmp_path):
        for name, store in _store_factories(tmp_path):
            store.upsert_hypothesis(
                {
                    "hypothesis_id": "hyp-001",
                    "title": "Momentum Hypothesis",
                    "source": "arxiv",
                    "source_url": "https://example.com/paper",
                    "thesis": "Winners keep winning over short horizons.",
                    "status": "evaluating",
                    "stage": "evaluate",
                    "decision_reason": "queued",
                    "metrics": {"suitability_score": 7.2},
                    "evidence": {"published_date": "2026-01-01"},
                }
            )
            store.upsert_hypothesis(
                {
                    "hypothesis_id": "hyp-001",
                    "strategy_id": "momentum_hypothesis",
                    "status": "candidate",
                    "stage": "stage2_integrate",
                    "decision_reason": "Integrated as momentum_hypothesis",
                }
            )

            got = store.get_hypothesis("hyp-001")
            assert got["title"] == "Momentum Hypothesis", f"{name}: title should be preserved"
            assert got["strategy_id"] == "momentum_hypothesis", f"{name}: strategy id should merge"
            assert got["status"] == "candidate", f"{name}: status should update"
            assert got["stage"] == "stage2_integrate", f"{name}: stage should update"
            assert got["metrics"]["suitability_score"] == pytest.approx(7.2)
            assert got["evidence"]["published_date"] == "2026-01-01"
            assert store.list_hypotheses("candidate")[0]["hypothesis_id"] == "hyp-001"
            assert store.list_hypotheses("rejected") == []


class TestIdeaBank:
    def test_upserts_and_filters_local_ideas(self, tmp_path):
        for name, store in _store_factories(tmp_path):
            raw = _FakeRaw(title="Idea Bank Momentum", source="arxiv", source_url="https://example.com/idea")
            store.upsert_idea(raw, status="discovered", reason="scouted")
            store.upsert_idea(raw, status="research_queue", reason="ready")

            got = store.list_ideas("research_queue")
            assert len(got) == 1, f"{name}: idea should be filterable by status"
            assert got[0]["title"] == "Idea Bank Momentum"
            assert got[0]["reason"] == "ready"
            assert store.list_ideas("discovered") == []

    def test_does_not_downgrade_terminal_idea_status(self, tmp_path):
        for name, store in _store_factories(tmp_path):
            raw = _FakeRaw(title="Terminal Idea", source="ssrn", source_url="https://example.com/terminal")
            store.upsert_idea(raw, status="candidate", reason="integrated")
            store.upsert_idea(raw, status="discovered", reason="rediscovered")

            got = store.list_ideas("candidate")
            assert len(got) == 1, f"{name}: terminal status should be preserved"
            assert got[0]["reason"] == "integrated"

    def test_writes_organized_idea_bank_artifacts(self, tmp_path):
        for name, store in _store_factories(tmp_path):
            raw = _FakeRaw(title="Organized Idea", source="arxiv", source_url="https://example.com/organized")
            store.upsert_idea(raw, status="discovered", reason="scouted")
            root = Path(store.root_dir) if hasattr(store, "root_dir") else Path(store._artifact_root)

            assert (root / "idea_bank" / "idea_bank.json").exists(), f"{name}: organized json should exist"
            assert (root / "idea_bank" / "idea_bank.md").exists(), f"{name}: organized markdown should exist"
            assert not (root / "idea_bank.json").exists(), f"{name}: legacy json mirror should not be created"


class TestDuckDBSpecific:
    def test_creates_tables_on_init(self, tmp_path):
        import duckdb

        store = _make_duckdb_store(tmp_path)
        conn = duckdb.connect(str(tmp_path / "test.duckdb"))
        tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
        conn.close()
        assert "candidates" in tables
        assert "seen_hashes" in tables
        assert "hypotheses" in tables
        assert "idea_bank" in tables

    def test_writes_artifact_files(self, tmp_path):
        store = _make_duckdb_store(tmp_path)
        store.write_discoveries([_FakeRaw()])
        store.save_run_result(_FakeResult())
        artifacts = tmp_path / "artifacts"
        assert (artifacts / "idea_bank" / "discovered_strategies.md").exists()
        assert not (artifacts / "last_result.json").exists()
        assert (artifacts / "reports" / "latest" / "last_result.json").exists()
        assert not (artifacts / "reports" / "research_pipeline" / "full_research_report.html").exists()
        assert (artifacts / "reports" / "research_pipeline" / "fast_research_report.html").exists()
        assert (artifacts / "reports" / "research_pipeline" / "strict_backtest_report.html").exists()
        assert (artifacts / "reports" / "research_pipeline" / "walkforward_audit_report.html").exists()
        assert not (artifacts / "reports" / "latest" / "full_research_report.html").exists()
        assert (artifacts / "reports" / "latest" / "fast_research_report.html").exists()
        assert (artifacts / "reports" / "latest" / "strict_backtest_report.html").exists()
        assert (artifacts / "reports" / "latest" / "walkforward_audit_report.html").exists()
        assert (artifacts / "reports" / "latest" / "metadata.json").exists()
