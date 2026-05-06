import json
import shutil
import sys
import types
import uuid
from pathlib import Path

import pytest

from quant.features.research.models import EvaluationReport, RawStrategy, ResearchConfig, ResearchResult
from quant.features.research.research_engine import ResearchEngine
from quant.features.research.tracking.comparison import StrategyComparator
from quant.features.research.tracking.run_recorder import RunRecorder
from quant.infrastructure.research.duckdb_experiment_store import DuckDBExperimentStore
from quant.infrastructure.research.file_artifact_store import FileArtifactStore


def _test_root() -> Path:
    root = Path(__file__).resolve().parents[1] / "infrastructure" / "var" / "test_research_tracking" / str(uuid.uuid4())
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_duckdb_experiment_store_records_run_lifecycle_and_queries(monkeypatch):
    root = _test_root()

    class FixedUuid:
        def __init__(self, value):
            self.hex = value

    uuids = iter(
        [
            FixedUuid("z-first-run"),
            FixedUuid("a-second-run"),
            FixedUuid("metric-1"),
            FixedUuid("metric-2"),
        ]
    )
    monkeypatch.setattr("quant.infrastructure.research.duckdb_experiment_store.uuid.uuid4", lambda: next(uuids))
    monkeypatch.setattr(DuckDBExperimentStore, "_now", staticmethod(lambda: "2026-05-06T00:00:00+00:00"))

    store = DuckDBExperimentStore(root / "research")
    try:
        first_run_id = store.start_run(
            "daily_momentum",
            {"config_hash": "abc123", "nested": {"threshold": 0.5}},
        )
        second_run_id = store.start_run("mean_reversion", {"config_hash": "def456"})

        assert first_run_id
        first_run = store.get_run(first_run_id)
        assert first_run["run_id"] == first_run_id
        assert first_run["strategy_id"] == "daily_momentum"
        assert first_run["status"] == "running"
        assert first_run["metadata"]["nested"]["threshold"] == pytest.approx(0.5)
        assert first_run["started_at"]

        store.record_metrics(
            first_run_id,
            [
                {
                    "strategy_id": "daily_momentum",
                    "metric_name": "sharpe",
                    "metric_value": 1.25,
                    "window_type": "oos",
                    "window_label": "2024",
                },
                {
                    "strategy_id": "daily_momentum",
                    "metric_name": "max_drawdown",
                    "metric_value": -0.08,
                },
            ],
        )

        metrics = store.list_metrics(first_run_id)
        assert [metric["run_id"] for metric in metrics] == [first_run_id, first_run_id]
        by_name = {metric["metric_name"]: metric for metric in metrics}
        assert by_name["sharpe"]["strategy_id"] == "daily_momentum"
        assert by_name["sharpe"]["metric_value"] == pytest.approx(1.25)
        assert by_name["sharpe"]["window_type"] == "oos"
        assert by_name["sharpe"]["window_label"] == "2024"
        assert by_name["max_drawdown"]["window_type"] == ""
        assert by_name["max_drawdown"]["window_label"] == ""

        store.complete_run(first_run_id, "completed")
        completed_run = store.get_run(first_run_id)
        assert completed_run["status"] == "completed"
        assert completed_run["completed_at"]
        assert completed_run["error"] == ""

        store.complete_run(second_run_id, "failed", error="scout unavailable")
        failed_run = store.get_run(second_run_id)
        assert failed_run["status"] == "failed"
        assert failed_run["error"] == "scout unavailable"

        assert [run["run_id"] for run in store.list_runs()] == [second_run_id, first_run_id]
        assert [run["run_id"] for run in store.list_runs(strategy_id="daily_momentum")] == [first_run_id]
        assert StrategyComparator(store).best_metric("sharpe")["run_id"] == first_run_id
    finally:
        store.close()
        shutil.rmtree(root, ignore_errors=True)


def test_file_artifact_store_saves_and_loads_json_and_table_artifacts():
    root = _test_root()
    store = FileArtifactStore(root / "artifacts")
    try:
        json_meta = store.save_json("run-1", "summary", {"status": "ok", "score": 1.5})
        table_meta = store.save_table("run-1", "metrics", [{"name": "sharpe", "value": 1.25}])

        assert json_meta["artifact_id"]
        assert json_meta["artifact_type"] == "json"
        assert json_meta["name"] == "summary"
        assert json_meta["run_id"] == "run-1"
        assert Path(json_meta["path"]).name == "summary.json"
        assert json.loads(Path(json_meta["path"]).read_text(encoding="utf-8")) == {"status": "ok", "score": 1.5}
        assert store.load_artifact(json_meta["artifact_id"]) == {"status": "ok", "score": 1.5}

        assert table_meta["artifact_type"] == "table"
        assert table_meta["name"] == "metrics"
        assert Path(table_meta["path"]).name == "metrics.json"
        assert store.load_artifact(table_meta["artifact_id"]) == [{"name": "sharpe", "value": 1.25}]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_file_artifact_store_rejects_artifact_paths_outside_root():
    root = _test_root()
    outside = root / "outside.json"
    outside.write_text(json.dumps({"secret": True}), encoding="utf-8")
    store = FileArtifactStore(root / "artifacts")
    try:
        with pytest.raises(ValueError, match="outside artifact root"):
            store.load_artifact(str(outside))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_research_api_background_run_closes_artifact_store(monkeypatch):
    class FakeBlueprint:
        def __init__(self, *args, **kwargs):
            pass

        def route(self, *args, **kwargs):
            def decorator(func):
                return func
            return decorator

    fake_request = types.SimpleNamespace(get_json=lambda: {})
    fake_flask = types.SimpleNamespace(
        Blueprint=FakeBlueprint,
        jsonify=lambda data: data,
        request=fake_request,
    )
    monkeypatch.setitem(sys.modules, "flask", fake_flask)

    import importlib

    research_module = importlib.import_module("quant.api.research_bp")

    class ClosableStore:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class ImmediateThread:
        def __init__(self, target, daemon=True):
            self.target = target
            self.daemon = daemon

        def start(self):
            self.target()

    class FakeEngine:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run_full_pipeline(self, sources=None, result=None):
            return result

    stores = {
        "research": ClosableStore(),
        "experiment": ClosableStore(),
        "artifact": ClosableStore(),
    }
    monkeypatch.setattr(research_module, "_load_research_config", lambda: ResearchConfig(auto_backtest=False, tracking_enabled=True))
    monkeypatch.setattr(research_module, "_create_llm_adapter", lambda cfg: None)
    monkeypatch.setattr(research_module, "_make_backtest_fn", lambda: None)
    monkeypatch.setattr(research_module, "_make_research_store", lambda cfg: stores["research"])
    monkeypatch.setattr(research_module, "_make_experiment_store", lambda cfg: stores["experiment"])
    monkeypatch.setattr(research_module, "_make_artifact_store", lambda cfg: stores["artifact"])
    monkeypatch.setattr(research_module, "ResearchEngine", FakeEngine)
    monkeypatch.setattr(research_module.threading, "Thread", ImmediateThread)

    research_module.run_research()

    assert stores["research"].closed is True
    assert stores["experiment"].closed is True
    assert stores["artifact"].closed is True


def test_scheduler_rigor_hub_reuses_active_experiment_store(monkeypatch):
    class FakeBlueprint:
        def __init__(self, *args, **kwargs):
            pass

        def route(self, *args, **kwargs):
            def decorator(func):
                return func
            return decorator

    fake_request = types.SimpleNamespace(get_json=lambda: {})
    fake_flask = types.SimpleNamespace(
        Blueprint=FakeBlueprint,
        jsonify=lambda data: data,
        request=fake_request,
    )
    monkeypatch.setitem(sys.modules, "flask", fake_flask)

    import importlib

    research_module = importlib.import_module("quant.api.research_bp")

    class FakeEngine:
        last_kwargs = None

        def __init__(self, **kwargs):
            FakeEngine.last_kwargs = kwargs

    class FakeScheduler:
        def __init__(self, engine, config):
            self.engine = engine
            self.config = config

    experiment_store = object()
    captured = {}
    cfg = ResearchConfig(auto_backtest=True, rigor_enabled=True, tracking_enabled=True)

    def make_rigor_hub(config, experiment_store=None):
        captured["experiment_store"] = experiment_store
        return "rigor-hub"

    monkeypatch.setattr(research_module, "_research_scheduler", None)
    monkeypatch.setattr(research_module, "_load_research_config", lambda: cfg)
    monkeypatch.setattr(research_module, "_make_research_store", lambda config: object())
    monkeypatch.setattr(research_module, "_create_llm_adapter", lambda config: None)
    monkeypatch.setattr(research_module, "_make_research_scout", lambda config: object())
    monkeypatch.setattr(research_module, "_make_backtest_fn", lambda: None)
    monkeypatch.setattr(research_module, "_make_experiment_store", lambda config: experiment_store)
    monkeypatch.setattr(research_module, "_make_artifact_store", lambda config: object())
    monkeypatch.setattr(research_module, "_make_rigor_hub", make_rigor_hub)
    monkeypatch.setattr(research_module, "ResearchEngine", FakeEngine)
    monkeypatch.setattr(research_module, "ResearchScheduler", FakeScheduler)

    scheduler = research_module._get_scheduler()

    assert scheduler.engine is not None
    assert FakeEngine.last_kwargs["experiment_store"] is experiment_store
    assert captured["experiment_store"] is experiment_store
    assert FakeEngine.last_kwargs["rigor_hub"] == "rigor-hub"


def test_run_recorder_helpers_are_deterministic_and_safe():
    assert RunRecorder.hash_config({"b": 2, "a": 1}) == RunRecorder.hash_config({"a": 1, "b": 2})
    assert RunRecorder.hash_config({"a": 1}) != RunRecorder.hash_config({"a": 2})
    assert len(RunRecorder.hash_config({"a": 1})) == 12
    assert RunRecorder.hash_data({"symbols": ["SPY", "QQQ"]}) == RunRecorder.hash_data({"symbols": ["SPY", "QQQ"]})
    assert isinstance(RunRecorder.get_code_version(), str)
    assert RunRecorder.get_code_version()


def test_research_engine_tracks_run_when_store_is_injected():
    class FixedScout:
        def search(self, sources=None, max_results=10):
            return []

    class RecordingExperimentStore:
        def __init__(self):
            self.started = []
            self.completed = []

        def start_run(self, strategy_id, metadata):
            self.started.append((strategy_id, metadata))
            return "run-123"

        def complete_run(self, run_id, status, error=""):
            self.completed.append((run_id, status, error))

    experiment_store = RecordingExperimentStore()
    engine = ResearchEngine(
        config=ResearchConfig(auto_backtest=False, tracking_enabled=True),
        scout=FixedScout(),
        experiment_store=experiment_store,
    )

    result = engine.run_full_pipeline()

    assert result.run_id == "run-123"
    assert experiment_store.started[0][0] == "research_pipeline"
    assert experiment_store.started[0][1]["config_hash"]
    assert experiment_store.started[0][1]["code_version"]
    assert experiment_store.completed == [("run-123", "completed", "")]


def test_research_engine_marks_tracked_run_failed_before_reraising():
    class FailingScout:
        def search(self, sources=None, max_results=10):
            raise RuntimeError("scout unavailable")

    class RecordingExperimentStore:
        def __init__(self):
            self.completed = []

        def start_run(self, strategy_id, metadata):
            return "run-failed"

        def complete_run(self, run_id, status, error=""):
            self.completed.append((run_id, status, error))

    experiment_store = RecordingExperimentStore()
    engine = ResearchEngine(
        config=ResearchConfig(auto_backtest=False, tracking_enabled=True),
        scout=FailingScout(),
        experiment_store=experiment_store,
    )

    with pytest.raises(RuntimeError, match="scout unavailable"):
        engine.run_full_pipeline()

    assert experiment_store.completed == [("run-failed", "failed", "scout unavailable")]


def test_research_engine_reuses_prestarted_run_id():
    class EmptyScout:
        def search(self, sources=None, max_results=10):
            return []

    class PrestartedExperimentStore:
        def __init__(self):
            self.completed = []

        def start_run(self, strategy_id, metadata):
            raise AssertionError("prestarted run should be reused")

        def complete_run(self, run_id, status, error=""):
            self.completed.append((run_id, status, error))

    experiment_store = PrestartedExperimentStore()
    engine = ResearchEngine(
        config=ResearchConfig(auto_backtest=False, tracking_enabled=True),
        scout=EmptyScout(),
        experiment_store=experiment_store,
    )

    result = engine.run_full_pipeline(result=ResearchResult(run_id="prestarted-run"))

    assert result.run_id == "prestarted-run"
    assert experiment_store.completed == [("prestarted-run", "completed", "")]


def test_research_engine_still_runs_without_experiment_store():
    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [
                RawStrategy(
                    title="No Tracking Strategy",
                    description="Daily OHLCV signal.",
                    source="arxiv",
                    source_url="https://example.test/no-tracking",
                )
            ]

    class NeutralEvaluator:
        def evaluate(self, raw):
            return EvaluationReport(
                suitability_score=1.0,
                complexity_score=1.0,
                data_requirement="low",
                daily_adaptable=True,
                estimated_edge=0.0,
                recommended_symbols=[],
                strategy_type="momentum",
                summary="Low score.",
            )

    engine = ResearchEngine(
        config=ResearchConfig(auto_backtest=False, tracking_enabled=True),
        scout=FixedScout(),
        evaluator=NeutralEvaluator(),
    )

    result = engine.run_full_pipeline(result=ResearchResult())

    assert result.discovered == 1
    assert result.evaluated == 1
    assert result.rejected == 1
    assert result.errors == []
    assert result.run_id is None
