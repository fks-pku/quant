from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict

import pytest

from quant.infrastructure.research.duckdb_experiment_store import DuckDBExperimentStore
from quant.infrastructure.research.file_artifact_store import FileArtifactStore
from quant.features.research.tracking.run_recorder import RunRecorder
from quant.features.research.tracking.comparison import StrategyComparator


@pytest.fixture
def tmp_dir():
    d = Path(tempfile.mkdtemp())
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def db_path(tmp_dir):
    return str(tmp_dir / "test_experiments.duckdb")


@pytest.fixture
def store(db_path):
    return DuckDBExperimentStore(db_path)


@pytest.fixture
def artifact_store(tmp_dir):
    return FileArtifactStore(str(tmp_dir / "artifacts"))


class TestDuckDBExperimentStore:
    def test_start_run_returns_non_empty_run_id(self, store):
        run_id = store.start_run("test_strategy", {"key": "value"})
        assert isinstance(run_id, str)
        assert len(run_id) > 0

    def test_record_metrics_inserts_metrics(self, store):
        run_id = store.start_run("strat_a", {})
        store.record_metrics(run_id, [
            {"strategy_id": "strat_a", "metric_name": "sharpe", "metric_value": 1.5,
             "window_type": "full", "window_label": "2020-2024"},
        ])
        metrics = store.list_metrics(run_id)
        assert len(metrics) == 1
        assert metrics[0]["metric_name"] == "sharpe"
        assert metrics[0]["metric_value"] == pytest.approx(1.5)
        assert metrics[0]["window_type"] == "full"
        assert metrics[0]["window_label"] == "2020-2024"

    def test_complete_run_updates_status_and_timestamp(self, store):
        run_id = store.start_run("strat_b", {})
        store.complete_run(run_id, "completed")
        run = store.get_run(run_id)
        assert run["status"] == "completed"
        assert run["completed_at"] is not None

    def test_complete_run_with_error(self, store):
        run_id = store.start_run("strat_c", {})
        store.complete_run(run_id, "failed", error="something broke")
        run = store.get_run(run_id)
        assert run["status"] == "failed"
        assert run["error"] == "something broke"

    def test_list_runs_orders_newest_first(self, store):
        id_a = store.start_run("first", {})
        id_b = store.start_run("second", {})
        runs = store.list_runs()
        assert len(runs) >= 2
        assert runs[0]["run_id"] == id_b
        assert runs[1]["run_id"] == id_a

    def test_list_runs_filters_by_strategy_id(self, store):
        store.start_run("alpha", {})
        store.start_run("beta", {})
        runs = store.list_runs(strategy_id="alpha")
        assert all(r["strategy_id"] == "alpha" for r in runs)

    def test_list_metrics_returns_stored_metrics(self, store):
        run_id = store.start_run("strat_d", {})
        store.record_metrics(run_id, [
            {"strategy_id": "strat_d", "metric_name": "sharpe", "metric_value": 2.0,
             "window_type": "oos", "window_label": "split_1"},
            {"strategy_id": "strat_d", "metric_name": "max_dd", "metric_value": -0.15,
             "window_type": "oos", "window_label": "split_1"},
        ])
        metrics = store.list_metrics(run_id)
        assert len(metrics) == 2
        names = {m["metric_name"] for m in metrics}
        assert names == {"sharpe", "max_dd"}

    def test_get_run_returns_run_dict(self, store):
        run_id = store.start_run("strat_e", {"config": "test"})
        run = store.get_run(run_id)
        assert run["run_id"] == run_id
        assert run["strategy_id"] == "strat_e"
        assert run["status"] == "running"
        assert run["started_at"] is not None

    def test_get_run_returns_none_for_unknown(self, store):
        assert store.get_run("nonexistent") is None

    def test_get_artifacts_returns_empty_for_run(self, store):
        run_id = store.start_run("strat_f", {})
        artifacts = store.get_artifacts(run_id)
        assert artifacts == []


class TestFileArtifactStore:
    def test_save_json_stores_file_and_returns_metadata(self, artifact_store, tmp_dir):
        meta = artifact_store.save_json("run_abc", "metrics", {"sharpe": 1.5})
        assert meta["artifact_type"] == "json"
        assert meta["name"] == "metrics"
        assert meta["run_id"] == "run_abc"

        path = tmp_dir / "artifacts" / "run_abc" / "metrics.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["sharpe"] == 1.5

    def test_save_table_stores_json_and_returns_metadata(self, artifact_store, tmp_dir):
        table = [{"col_a": 1, "col_b": 2}]
        meta = artifact_store.save_table("run_xyz", "trades", table)
        assert meta["artifact_type"] == "table"
        assert meta["name"] == "trades"

        path = tmp_dir / "artifacts" / "run_xyz" / "trades.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data[0]["col_a"] == 1

    def test_load_artifact_loads_saved_file(self, artifact_store):
        meta = artifact_store.save_json("run_123", "config", {"param": 42})
        loaded = artifact_store.load_artifact(meta["artifact_id"])
        assert loaded["param"] == 42


class TestRunRecorder:
    def test_hash_config_returns_consistent_hash(self):
        config = {"a": 1, "b": [2, 3]}
        h1 = RunRecorder.hash_config(config)
        h2 = RunRecorder.hash_config(config)
        assert h1 == h2
        assert len(h1) == 16

    def test_hash_config_different_for_different_configs(self):
        h1 = RunRecorder.hash_config({"a": 1})
        h2 = RunRecorder.hash_config({"a": 2})
        assert h1 != h2

    def test_hash_data_returns_consistent_hash(self):
        summary = {"rows": 1000, "symbols": ["AAPL", "MSFT"]}
        h1 = RunRecorder.hash_data(summary)
        h2 = RunRecorder.hash_data(summary)
        assert h1 == h2
        assert len(h1) == 16

    def test_get_code_version_returns_unknown_when_git_unavailable(self):
        version = RunRecorder.get_code_version()
        assert isinstance(version, str)
        assert len(version) > 0


class TestStrategyComparator:
    def test_compare_returns_matching_metrics(self, store):
        run_id = store.start_run("strat_compare", {})
        store.record_metrics(run_id, [
            {"strategy_id": "strat_compare", "metric_name": "sharpe", "metric_value": 1.2,
             "window_type": "full", "window_label": "2020-2024"},
        ])
        comparator = StrategyComparator(store)
        results = comparator.compare(["strat_compare"], metric_name="sharpe")
        assert len(results) == 1
        assert results[0]["strategy_id"] == "strat_compare"
        assert results[0]["metric_value"] == pytest.approx(1.2)

    def test_compare_returns_empty_for_no_match(self, store):
        comparator = StrategyComparator(store)
        results = comparator.compare(["nonexistent"], metric_name="sharpe")
        assert results == []
