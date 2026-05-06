import importlib
import sys
import types

import pytest

from quant.features.research.models import ResearchConfig, ResearchResult
from quant.features.research.research_engine import ResearchEngine
from quant.features.research.ensemble import ResearchEnsembleBuilder


class MemoryExperimentStore:
    def __init__(self):
        self.runs = []
        self.metrics = {}
        self.artifacts = {}

    def start_run(self, strategy_id, metadata):
        return "run-started"

    def record_metrics(self, run_id, metrics):
        self.metrics.setdefault(run_id, []).extend(metrics)

    def complete_run(self, run_id, status, error=""):
        pass

    def get_run(self, run_id):
        for run in self.runs:
            if run["run_id"] == run_id:
                return run
        return None

    def list_runs(self, strategy_id=None, limit=50):
        rows = self.runs
        if strategy_id is not None:
            rows = [run for run in rows if run["strategy_id"] == strategy_id]
        return rows[:limit]

    def list_metrics(self, run_id):
        return self.metrics.get(run_id, [])

    def get_artifacts(self, run_id):
        return self.artifacts.get(run_id, [])


class MemoryArtifactStore:
    def __init__(self, payloads):
        self.payloads = payloads
        self.saved = []

    def save_json(self, run_id, name, data):
        artifact_id = f"{run_id}/{name}.json"
        self.payloads[artifact_id] = data
        self.saved.append((run_id, name, data))
        return {"artifact_id": artifact_id, "run_id": run_id, "name": name}

    def save_table(self, run_id, name, table):
        self.saved.append((run_id, name, table))
        return {"artifact_id": f"{run_id}/{name}.json", "run_id": run_id, "name": name}

    def load_artifact(self, artifact_id):
        return self.payloads[artifact_id]


def _stores():
    experiment_store = MemoryExperimentStore()
    experiment_store.runs = [
        {"run_id": "run-a", "strategy_id": "strategy_a", "status": "completed"},
        {"run_id": "run-b", "strategy_id": "strategy_b", "status": "completed"},
        {"run_id": "run-c", "strategy_id": "strategy_c", "status": "completed"},
    ]
    experiment_store.artifacts = {
        "run-a": [{"artifact_id": "curve-a", "name": "equity_curve"}],
        "run-b": [{"artifact_id": "curve-b", "name": "equity_curve"}],
        "run-c": [{"artifact_id": "curve-c", "name": "equity_curve"}],
    }
    artifact_store = MemoryArtifactStore(
        {
            "curve-a": [
                {"date": "2024-01-01", "equity": 100.0},
                {"date": "2024-01-02", "equity": 101.0},
                {"date": "2024-01-03", "equity": 100.0},
                {"date": "2024-01-04", "equity": 102.0},
            ],
            "curve-b": [
                {"date": "2024-01-01", "equity": 100.0},
                {"date": "2024-01-02", "equity": 99.0},
                {"date": "2024-01-03", "equity": 100.0},
                {"date": "2024-01-04", "equity": 101.0},
            ],
            "curve-c": [
                {"date": "2024-01-01", "equity": 100.0},
                {"date": "2024-01-02", "equity": 102.0},
                {"date": "2024-01-03", "equity": 104.0},
                {"date": "2024-01-04", "equity": 103.0},
            ],
        }
    )
    return experiment_store, artifact_store


def test_ensemble_returns_no_op_for_fewer_than_two_strategies():
    experiment_store, artifact_store = _stores()
    builder = ResearchEnsembleBuilder(experiment_store, artifact_store)

    result = builder.build(["strategy_a"])

    assert result["no_op"] is True
    assert result["strategy_ids"] == ["strategy_a"]
    assert result["weights"] == {}


def test_ensemble_correlation_matrix_is_symmetric():
    experiment_store, artifact_store = _stores()
    builder = ResearchEnsembleBuilder(experiment_store, artifact_store)

    result = builder.build(["strategy_a", "strategy_b", "strategy_c"])
    matrix = result["correlation_matrix"]["matrix"]

    assert result["no_op"] is False
    for i, row in enumerate(matrix):
        for j, value in enumerate(row):
            assert value == pytest.approx(matrix[j][i])


def test_equal_risk_weights_sum_to_one():
    experiment_store, artifact_store = _stores()
    builder = ResearchEnsembleBuilder(experiment_store, artifact_store)

    result = builder.build(["strategy_a", "strategy_b", "strategy_c"])
    weights = result["weights"]["equal_risk"]

    assert sum(weights.values()) == pytest.approx(1.0)


def test_max_weight_cap_is_enforced():
    experiment_store, artifact_store = _stores()
    builder = ResearchEnsembleBuilder(experiment_store, artifact_store, {"max_weight_per_strategy": 0.34})

    result = builder.build(["strategy_a", "strategy_b", "strategy_c"])

    for method_weights in result["weights"].values():
        assert max(method_weights.values()) <= 0.34 + 1e-12


def test_infeasible_max_weight_cap_returns_no_op():
    experiment_store, artifact_store = _stores()
    builder = ResearchEnsembleBuilder(experiment_store, artifact_store, {"max_weight_per_strategy": 0.25})

    result = builder.build(["strategy_a", "strategy_b", "strategy_c"])

    assert result["no_op"] is True
    assert "max_weight_per_strategy" in result["reason"]
    assert result["weights"] == {}


def test_ensemble_result_can_be_serialized_by_research_result_to_dict():
    experiment_store, artifact_store = _stores()
    engine = ResearchEngine(
        config=ResearchConfig(
            auto_backtest=False,
            tracking_enabled=False,
            ensemble_enabled=True,
            ensemble_config={"strategy_ids": ["strategy_a", "strategy_b"]},
        ),
        scout=type("EmptyScout", (), {"search": lambda self, sources=None, max_results=10: []})(),
        experiment_store=experiment_store,
        artifact_store=artifact_store,
    )

    result = engine.run_full_pipeline(result=ResearchResult())
    data = result.to_dict()

    assert data["ensemble_built"] is True


def test_research_api_get_ensemble_loads_latest_saved_recommendation(monkeypatch):
    research_module = _research_module(monkeypatch)
    experiment_store, artifact_store = _stores()
    experiment_store.runs.insert(0, {"run_id": "ensemble-run", "strategy_id": "research_ensemble", "status": "completed"})
    experiment_store.artifacts["ensemble-run"] = [{"artifact_id": "ensemble-artifact", "name": "ensemble"}]
    artifact_store.payloads["ensemble-artifact"] = {
        "no_op": False,
        "strategy_ids": ["strategy_a", "strategy_b"],
        "weights": {"equal_risk": {"strategy_a": 0.5, "strategy_b": 0.5}},
    }
    monkeypatch.setattr(research_module, "_load_research_config", lambda: ResearchConfig(tracking_enabled=True))
    monkeypatch.setattr(research_module, "_make_experiment_store", lambda cfg: experiment_store)
    monkeypatch.setattr(research_module, "_make_artifact_store", lambda cfg: artifact_store)
    monkeypatch.setattr(research_module, "request", types.SimpleNamespace(args=_Args({}), get_json=lambda: {}))

    response = research_module.get_research_ensemble()

    assert response["strategy_ids"] == ["strategy_a", "strategy_b"]
    assert response["weights"]["equal_risk"]["strategy_a"] == pytest.approx(0.5)


def test_research_api_get_ensemble_loads_pipeline_saved_recommendation(monkeypatch):
    research_module = _research_module(monkeypatch)
    experiment_store, artifact_store = _stores()
    experiment_store.runs.insert(0, {"run_id": "pipeline-run", "strategy_id": "research_pipeline", "status": "completed"})
    experiment_store.artifacts["pipeline-run"] = [{"artifact_id": "pipeline-ensemble", "name": "ensemble"}]
    artifact_store.payloads["pipeline-ensemble"] = {
        "no_op": False,
        "strategy_ids": ["pipeline_a", "pipeline_b"],
        "weights": {"equal_risk": {"pipeline_a": 0.6, "pipeline_b": 0.4}},
    }
    monkeypatch.setattr(research_module, "_load_research_config", lambda: ResearchConfig(tracking_enabled=True))
    monkeypatch.setattr(research_module, "_make_experiment_store", lambda cfg: experiment_store)
    monkeypatch.setattr(research_module, "_make_artifact_store", lambda cfg: artifact_store)
    monkeypatch.setattr(research_module, "request", types.SimpleNamespace(args=_Args({}), get_json=lambda: {}))

    response = research_module.get_research_ensemble()

    assert response["strategy_ids"] == ["pipeline_a", "pipeline_b"]
    assert response["weights"]["equal_risk"]["pipeline_a"] == pytest.approx(0.6)


def test_research_api_run_response_includes_pipeline_result_fields(monkeypatch):
    research_module = _research_module(monkeypatch)

    class ImmediateThread:
        def __init__(self, target, daemon=True):
            self.target = target
            self.daemon = daemon

        def start(self):
            self.target()

    class FakeEngine:
        def __init__(self, **kwargs):
            pass

        def run_full_pipeline(self, sources=None, result=None):
            result.run_id = "run-123"
            result.specified = 2
            result.needs_manual_spec = 1
            result.validated = 2
            result.walkforward_passed = 1
            result.ensemble_built = True
            return result

    monkeypatch.setattr(research_module, "_load_research_config", lambda: ResearchConfig(auto_backtest=False, tracking_enabled=True))
    monkeypatch.setattr(research_module, "_create_llm_adapter", lambda cfg: None)
    monkeypatch.setattr(research_module, "_make_backtest_fn", lambda: None)
    monkeypatch.setattr(research_module, "_make_research_store", lambda cfg: object())
    monkeypatch.setattr(research_module, "_make_experiment_store", lambda cfg: object())
    monkeypatch.setattr(research_module, "_make_artifact_store", lambda cfg: object())
    monkeypatch.setattr(research_module, "_make_rigor_hub", lambda cfg, experiment_store=None: None)
    monkeypatch.setattr(research_module, "ResearchEngine", FakeEngine)
    monkeypatch.setattr(research_module.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(research_module, "request", types.SimpleNamespace(args=_Args({}), get_json=lambda: {}))

    response = research_module.run_research()

    assert response["run_id"] == "run-123"
    assert response["specified"] == 2
    assert response["needs_manual_spec"] == 1
    assert response["validated"] == 2
    assert response["walkforward_passed"] == 1
    assert response["ensemble_built"] is True


def test_research_api_run_response_preallocates_tracking_run_before_async_thread(monkeypatch):
    research_module = _research_module(monkeypatch)

    class DeferredThread:
        def __init__(self, target, daemon=True):
            self.target = target
            self.daemon = daemon

        def start(self):
            pass

    class RecordingExperimentStore:
        def __init__(self):
            self.started = []

        def start_run(self, strategy_id, metadata):
            self.started.append((strategy_id, metadata))
            return "prestarted-run"

    class FakeEngine:
        def __init__(self, **kwargs):
            pass

        def run_full_pipeline(self, sources=None, result=None):
            raise AssertionError("thread should not run synchronously")

    experiment_store = RecordingExperimentStore()
    monkeypatch.setattr(research_module, "_load_research_config", lambda: ResearchConfig(auto_backtest=False, tracking_enabled=True))
    monkeypatch.setattr(research_module, "_create_llm_adapter", lambda cfg: None)
    monkeypatch.setattr(research_module, "_make_backtest_fn", lambda: None)
    monkeypatch.setattr(research_module, "_make_research_store", lambda cfg: object())
    monkeypatch.setattr(research_module, "_make_experiment_store", lambda cfg: experiment_store)
    monkeypatch.setattr(research_module, "_make_artifact_store", lambda cfg: object())
    monkeypatch.setattr(research_module, "_make_rigor_hub", lambda cfg, experiment_store=None: None)
    monkeypatch.setattr(research_module, "ResearchEngine", FakeEngine)
    monkeypatch.setattr(research_module.threading, "Thread", DeferredThread)
    monkeypatch.setattr(research_module, "request", types.SimpleNamespace(args=_Args({}), get_json=lambda: {}))

    response = research_module.run_research()

    assert response["run_id"] == "prestarted-run"
    assert experiment_store.started[0][0] == "research_pipeline"


class _Args:
    def __init__(self, values):
        self.values = values

    def get(self, name, default=None):
        return self.values.get(name, default)


def _research_module(monkeypatch):
    flask_stub = types.ModuleType("flask")
    flask_stub.Blueprint = lambda *args, **kwargs: types.SimpleNamespace(route=lambda *a, **k: lambda fn: fn)
    flask_stub.jsonify = lambda value=None, **kwargs: value if value is not None else kwargs
    flask_stub.request = types.SimpleNamespace(args=_Args({}), get_json=lambda: {})
    monkeypatch.setitem(sys.modules, "flask", flask_stub)
    module = importlib.import_module("quant.api.research_bp")
    monkeypatch.setattr(module, "jsonify", flask_stub.jsonify)
    return module
