import json
import shutil
import sys
import types
import uuid
from pathlib import Path

import pytest

flask_stub = types.ModuleType("flask")
flask_stub.Blueprint = lambda *args, **kwargs: types.SimpleNamespace(route=lambda *a, **k: lambda fn: fn)
flask_stub.jsonify = lambda value=None, **kwargs: value if value is not None else kwargs
flask_stub.request = types.SimpleNamespace(get_json=lambda: {})
sys.modules.setdefault("flask", flask_stub)

import quant.api.research_bp as research_bp
from quant.api.research_bp import _make_research_store
from quant.features.research.models import EvaluationReport, RawStrategy, ResearchConfig, ResearchResult
from quant.infrastructure.research.duckdb_research_store import DuckDBResearchStore
from quant.infrastructure.research.migration import migrate_file_research_store
from quant.infrastructure.research.repository import FileResearchStore


def _raw_strategy() -> RawStrategy:
    return RawStrategy(
        title="Daily Momentum Breakout",
        description="Ranks liquid stocks by 20 day momentum and buys breakouts using daily OHLCV.",
        source="arxiv",
        source_url="https://example.test/paper",
        authors="Researcher",
        published_date="2026-04-01",
    )


def _evaluation_report() -> EvaluationReport:
    return EvaluationReport(
        suitability_score=7.5,
        complexity_score=3.0,
        data_requirement="low",
        daily_adaptable=True,
        estimated_edge=0.08,
        recommended_symbols=["SPY", "QQQ"],
        strategy_type="momentum",
        summary="Daily OHLCV momentum breakout with clear behavioral rationale.",
        economic_rationale_score=2.0,
        factor_uniqueness_score=1.0,
        data_availability_score=2.0,
        implementation_score=2.0,
        overfit_risk_score=1.0,
        cost_capacity_score=1.0,
        regime_robustness_score=1.0,
        risk_flags=["survivorship_bias"],
        rejection_reason="",
    )


def _candidate(status: str = "candidate") -> dict:
    return {
        "id": "daily_momentum_breakout",
        "name": "Daily Momentum Breakout",
        "status": status,
        "research_meta": {
            "suitability_score": 7.5,
            "recommended_symbols": ["SPY", "QQQ"],
            "nested": {"edge": 0.08},
        },
    }


def _test_root() -> Path:
    root = Path(__file__).resolve().parents[1] / "infrastructure" / "var" / "test_research_duckdb_store" / str(uuid.uuid4())
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture(params=[FileResearchStore, DuckDBResearchStore])
def store(request):
    root = _test_root()
    try:
        yield request.param(root / "research")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_upsert_candidate_preserves_non_candidate_status(store):
    store.upsert_candidate(_candidate())
    store.update_status("daily_momentum_breakout", "paused")

    incoming = _candidate()
    incoming["name"] = "Renamed"
    store.upsert_candidate(incoming)

    candidate = store.get_candidate("daily_momentum_breakout")
    assert candidate["name"] == "Renamed"
    assert candidate["status"] == "paused"


def test_get_candidate_returns_candidate_dict(store):
    store.upsert_candidate(_candidate())

    candidate = store.get_candidate("daily_momentum_breakout")

    assert candidate["id"] == "daily_momentum_breakout"
    assert candidate["research_meta"]["recommended_symbols"] == ["SPY", "QQQ"]
    assert candidate["research_meta"]["nested"]["edge"] == pytest.approx(0.08)


def test_list_by_status_filters_status(store):
    store.upsert_candidate(_candidate("candidate"))
    rejected = _candidate("rejected")
    rejected["id"] = "rejected_strategy"
    store.upsert_candidate(rejected)

    candidates = store.list_by_status("candidate")

    assert [candidate["id"] for candidate in candidates] == ["daily_momentum_breakout"]


def test_update_status_stores_rejection_reason(store):
    store.upsert_candidate(_candidate())

    assert store.update_status("daily_momentum_breakout", "rejected", reason="Low Sharpe") is True

    candidate = store.get_candidate("daily_momentum_breakout")
    assert candidate["status"] == "rejected"
    assert candidate["research_meta"]["rejection_reason"] == "Low Sharpe"


def test_has_seen_and_mark_seen_deduplicate_discoveries(store):
    raw = _raw_strategy()

    assert store.has_seen("hash-1") is False

    store.mark_seen("hash-1", raw)

    assert store.has_seen("hash-1") is True


def test_artifact_writes_create_compatible_files(store):
    root = _test_root() / "research"
    store = type(store)(root)
    try:
        raw = _raw_strategy()
        report = _evaluation_report()
        result = ResearchResult(discovered=1, evaluated=1, integrated=1, run_id="run-1")

        store.write_discoveries([raw])
        store.write_evaluations([(raw, report, "pass", "Integrated as daily_momentum_breakout")])
        store.save_run_result(result)

        assert "Daily Momentum Breakout" in (root / "discovered_strategies.md").read_text(encoding="utf-8")
        evaluation_text = (root / "strategy_evaluation.md").read_text(encoding="utf-8")
        assert "economic_rationale" in evaluation_text
        last_result = json.loads((root / "last_result.json").read_text(encoding="utf-8"))
        assert last_result["run_id"] == "run-1"
        assert "saved_at" in last_result
        run_results = list((root / "runs").glob("*_result.json"))
        assert len(run_results) == 1
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_migration_copies_candidates_and_seen_hashes():
    root = _test_root()
    json_path = root / "file_store" / "research_state.json"
    json_path.parent.mkdir(parents=True)
    try:
        json_path.write_text(
            json.dumps(
                {
                    "candidates": {
                        "daily_momentum_breakout": _candidate("paused"),
                        "rejected_strategy": {**_candidate("rejected"), "id": "rejected_strategy"},
                    },
                    "seen_hashes": {
                        "hash-1": {
                            "title": "Daily Momentum Breakout",
                            "source": "arxiv",
                            "source_url": "https://example.test/paper",
                            "seen_at": "2026-05-01T00:00:00+00:00",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        duckdb_store = DuckDBResearchStore(root / "duckdb_store")

        counts = migrate_file_research_store(json_path, duckdb_store)

        assert counts == {"candidates": 2, "seen_hashes": 1}
        assert duckdb_store.get_candidate("daily_momentum_breakout")["status"] == "paused"
        assert duckdb_store.list_by_status("rejected")[0]["id"] == "rejected_strategy"
        assert duckdb_store.has_seen("hash-1") is True
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_api_store_selection_requires_explicit_duckdb_backend():
    root = _test_root()
    try:
        tracking_db_path = root / "tracking.duckdb"
        default_store = _make_research_store(
            ResearchConfig(research_dir=str(root / "research"), tracking_db_path=str(tracking_db_path))
        )
        explicit_store = _make_research_store(
            ResearchConfig(
                research_dir=str(root / "research"),
                tracking_db_path=str(tracking_db_path),
                research_store_backend="duckdb",
            )
        )

        assert isinstance(default_store, FileResearchStore)
        assert isinstance(explicit_store, DuckDBResearchStore)
        explicit_store.close()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_duckdb_store_close_is_idempotent_and_supports_context_manager():
    root = _test_root()
    try:
        with DuckDBResearchStore(root / "research") as store:
            store.upsert_candidate(_candidate())
            assert store.get_candidate("daily_momentum_breakout")["id"] == "daily_momentum_breakout"

        store.close()
        store.close()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_migration_returns_empty_counts_for_missing_or_corrupt_json():
    root = _test_root()
    try:
        duckdb_store = DuckDBResearchStore(root / "duckdb_store")
        missing_counts = migrate_file_research_store(root / "missing.json", duckdb_store)

        corrupt_path = root / "corrupt.json"
        corrupt_path.write_text("{not-json", encoding="utf-8")
        corrupt_counts = migrate_file_research_store(corrupt_path, duckdb_store)

        assert missing_counts == {"candidates": 0, "seen_hashes": 0}
        assert corrupt_counts == {"candidates": 0, "seen_hashes": 0}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_run_research_closes_background_store(monkeypatch):
    class ClosingStore:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class FakeEngine:
        def __init__(self, research_store=None, **kwargs):
            self.research_store = research_store

        def run_full_pipeline(self, sources=None, result=None):
            result.discovered = 1

    class ImmediateThread:
        def __init__(self, target, daemon=False):
            self.target = target
            self.daemon = daemon

        def start(self):
            self.target()

    store = ClosingStore()
    monkeypatch.setattr(research_bp.request, "get_json", lambda: {})
    monkeypatch.setattr(research_bp, "_load_research_config", ResearchConfig)
    monkeypatch.setattr(research_bp, "_create_llm_adapter", lambda cfg: None)
    monkeypatch.setattr(research_bp, "_make_backtest_fn", lambda: None)
    monkeypatch.setattr(research_bp, "_make_research_store", lambda cfg: store)
    monkeypatch.setattr(research_bp, "ResearchEngine", FakeEngine)
    monkeypatch.setattr(research_bp.threading, "Thread", ImmediateThread)

    response = research_bp.run_research()

    assert response["status"] == "running"
    assert store.closed is True
