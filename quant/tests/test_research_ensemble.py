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
        self.saved.append((run_id, name, data))
        return {"artifact_id": f"{run_id}/{name}.json", "run_id": run_id, "name": name}

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
    builder = ResearchEnsembleBuilder(experiment_store, artifact_store, {"max_weight": 0.34})

    result = builder.build(["strategy_a", "strategy_b", "strategy_c"])

    for method_weights in result["weights"].values():
        assert max(method_weights.values()) <= 0.34 + 1e-12


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
