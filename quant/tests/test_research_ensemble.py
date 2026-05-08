from __future__ import annotations

import json
import math
import sys
import types
from typing import Any, Dict, List, Optional

import numpy as np
import pytest

from quant.features.research.ensemble.correlation_matrix import compute_correlation_matrix, compute_effective_n
from quant.features.research.ensemble.optimizer import equal_weight, inverse_vol, equal_risk
from quant.features.research.ensemble.ensemble import StrategyEnsemble
from quant.features.research.models import EnsembleResult


def _risk_contribution_shares(corr_matrix: List[List[float]], volatilities: List[float], weights: List[float]) -> np.ndarray:
    corr = np.asarray(corr_matrix, dtype=float)
    vols = np.asarray(volatilities, dtype=float)
    w = np.asarray(weights, dtype=float)
    cov = corr * np.outer(vols, vols)
    contributions = w * (cov @ w)
    return contributions / contributions.sum()


def _install_grid_scipy_minimize(monkeypatch):
    scipy_module = types.ModuleType("scipy")
    optimize_module = types.ModuleType("scipy.optimize")

    def minimize(objective, x0, method=None, bounds=None, constraints=(), options=None):
        assert method == "SLSQP"
        cap = bounds[0][1]
        step = 0.005
        best_x = np.asarray(x0, dtype=float)
        best_fun = float(objective(best_x))
        grid = np.arange(0.0, cap + step / 2, step)
        for w0 in grid:
            for w1 in grid:
                w2 = 1.0 - w0 - w1
                if w2 < -1e-12 or w2 > cap + 1e-12:
                    continue
                candidate = np.array([w0, w1, w2], dtype=float)
                value = float(objective(candidate))
                if value < best_fun:
                    best_x = candidate
                    best_fun = value
        return types.SimpleNamespace(success=True, x=best_x, fun=best_fun)

    optimize_module.minimize = minimize
    scipy_module.optimize = optimize_module
    monkeypatch.setitem(sys.modules, "scipy", scipy_module)
    monkeypatch.setitem(sys.modules, "scipy.optimize", optimize_module)


def _install_failing_scipy_minimize(monkeypatch):
    scipy_module = types.ModuleType("scipy")
    optimize_module = types.ModuleType("scipy.optimize")

    def minimize(objective, x0, method=None, bounds=None, constraints=(), options=None):
        return types.SimpleNamespace(success=False, x=np.asarray(x0, dtype=float), fun=float("inf"))

    optimize_module.minimize = minimize
    scipy_module.optimize = optimize_module
    monkeypatch.setitem(sys.modules, "scipy", scipy_module)
    monkeypatch.setitem(sys.modules, "scipy.optimize", optimize_module)


class _FakeExperimentStore:
    def __init__(self, metrics_by_strategy: Optional[Dict[str, List[Dict]]] = None):
        self._metrics_by_strategy = metrics_by_strategy or {}
        self._runs: Dict[str, Dict] = {}

    def list_runs(self, strategy_id=None, limit=50):
        runs = [r for r in self._runs.values() if strategy_id is None or r["strategy_id"] == strategy_id]
        return runs[:limit]

    def list_metrics(self, run_id):
        run = self._runs.get(run_id)
        if run is None:
            return []
        return self._metrics_by_strategy.get(run["strategy_id"], [])

    def start_run(self, strategy_id, metadata):
        import uuid
        run_id = str(uuid.uuid4())[:8]
        self._runs[run_id] = {"run_id": run_id, "strategy_id": strategy_id, "status": "running"}
        return run_id

    def get_run(self, run_id):
        return self._runs.get(run_id)

    def complete_run(self, run_id, status, error=""):
        if run_id in self._runs:
            self._runs[run_id]["status"] = status


class TestCorrelationMatrix:
    def test_symmetric_matrix(self):
        curves = {
            "a": [100.0, 102.0, 101.0, 103.0, 105.0],
            "b": [100.0, 101.0, 100.0, 102.0, 104.0],
            "c": [100.0, 103.0, 102.0, 104.0, 106.0],
        }
        result = compute_correlation_matrix(curves)
        matrix = result["matrix"]
        n = len(curves)
        assert len(matrix) == n
        for i in range(n):
            for j in range(n):
                assert matrix[i][j] == pytest.approx(matrix[j][i], abs=1e-10)

    def test_diagonal_is_one(self):
        curves = {"x": [1, 2, 3], "y": [1, 3, 2]}
        result = compute_correlation_matrix(curves)
        for i in range(2):
            assert result["matrix"][i][i] == pytest.approx(1.0, abs=1e-10)

    def test_single_strategy_returns_identity(self):
        result = compute_correlation_matrix({"a": [100, 105, 110]})
        assert result["matrix"] == [[1.0]]
        assert result["mean_correlation"] == 0.0

    def test_empty_returns_empty(self):
        result = compute_correlation_matrix({})
        assert result["ids"] == []
        assert result["matrix"] == []
        assert result["mean_correlation"] == 0.0


class TestEffectiveN:
    def test_uncorrelated_gives_full_n(self):
        identity = [[1.0, 0.0], [0.0, 1.0]]
        eff_n = compute_effective_n(identity)
        assert eff_n == pytest.approx(2.0, abs=0.01)

    def test_perfectly_correlated_gives_one(self):
        perfect = [[1.0, 1.0], [1.0, 1.0]]
        eff_n = compute_effective_n(perfect)
        assert eff_n == pytest.approx(1.0, abs=0.01)

    def test_single_returns_one(self):
        assert compute_effective_n([[1.0]]) == 1.0

    def test_empty_returns_zero(self):
        assert compute_effective_n([]) == 0.0


class TestOptimizer:
    def test_equal_weight_sums_to_one(self):
        w = equal_weight(5)
        assert len(w) == 5
        assert sum(w) == pytest.approx(1.0)

    def test_equal_weight_empty(self):
        assert equal_weight(0) == []

    def test_inverse_vol_sums_to_one(self):
        w = inverse_vol([0.1, 0.2, 0.3])
        assert len(w) == 3
        assert sum(w) == pytest.approx(1.0)

    def test_inverse_vol_lower_vol_gets_higher_weight(self):
        w = inverse_vol([0.1, 0.5])
        assert w[0] > w[1]

    def test_inverse_vol_empty(self):
        assert inverse_vol([]) == []

    def test_equal_risk_max_weight_cap(self):
        w = equal_risk([[1, 0], [0, 1]], [0.1, 0.1], max_weight=0.6)
        assert max(w) <= 0.6 + 1e-10

    def test_equal_risk_sums_to_one(self):
        w = equal_risk([[1, 0.5], [0.5, 1]], [0.2, 0.3])
        assert sum(w) == pytest.approx(1.0)

    def test_equal_risk_contributions_are_close_for_identity_covariance(self):
        corr = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        volatilities = [0.2, 0.4, 0.8]
        weights = equal_risk(corr, volatilities, max_weight=0.8)
        shares = _risk_contribution_shares(corr, volatilities, weights)
        assert sum(weights) == pytest.approx(1.0)
        assert all(weight >= 0.0 for weight in weights)
        assert max(shares) - min(shares) <= 0.02

    def test_equal_risk_uses_covariance_to_equalize_risk_contributions(self, monkeypatch):
        _install_grid_scipy_minimize(monkeypatch)
        corr = [[1.0, 0.8, 0.0], [0.8, 1.0, 0.0], [0.0, 0.0, 1.0]]
        volatilities = [0.2, 0.2, 0.2]
        weights = equal_risk(corr, volatilities, max_weight=0.8)
        shares = _risk_contribution_shares(corr, volatilities, weights)
        assert sum(weights) == pytest.approx(1.0)
        assert all(0.0 <= weight <= 0.8 for weight in weights)
        assert weights[2] > weights[0]
        assert max(shares) - min(shares) <= 0.03

    def test_equal_risk_falls_back_to_capped_inverse_vol_when_optimizer_fails(self, monkeypatch):
        _install_failing_scipy_minimize(monkeypatch)
        corr = [[1.0, 0.2, 0.1], [0.2, 1.0, 0.3], [0.1, 0.3, 1.0]]
        weights = equal_risk(corr, [0.01, 0.5, 0.6], max_weight=0.55)
        assert sum(weights) == pytest.approx(1.0)
        assert all(0.0 <= weight <= 0.55 + 1e-10 for weight in weights)
        assert weights[0] == pytest.approx(0.55, abs=1e-6)

    def test_equal_risk_empty(self):
        assert equal_risk([], []) == []

    def test_equal_risk_single(self):
        assert equal_risk([[1.0]], [0.2]) == [1.0]


class TestStrategyEnsemble:
    def test_fewer_than_two_returns_noop(self):
        store = _FakeExperimentStore()
        ensemble = StrategyEnsemble(store)
        result = ensemble.build(["only_one"])
        assert result.strategy_ids == []
        assert result.weights == []
        assert result.portfolio_sharpe == 0.0

    def test_fewer_than_two_empty_list(self):
        store = _FakeExperimentStore()
        ensemble = StrategyEnsemble(store)
        result = ensemble.build([])
        assert result.strategy_ids == []

    def test_build_with_data_returns_result(self):
        store = _FakeExperimentStore({
            "s1": [
                {"metric_name": "sharpe", "metric_value": 1.2},
                {"metric_name": "volatility", "metric_value": 0.15},
                {"metric_name": "equity_curve", "metric_value": [100, 105, 110, 108, 112]},
            ],
            "s2": [
                {"metric_name": "sharpe", "metric_value": 0.8},
                {"metric_name": "volatility", "metric_value": 0.25},
                {"metric_name": "equity_curve", "metric_value": [100, 102, 104, 106, 108]},
            ],
        })
        store.start_run("s1", {})
        store.start_run("s2", {})
        ensemble = StrategyEnsemble(store)
        result = ensemble.build(["s1", "s2"])
        assert len(result.strategy_ids) == 2
        assert len(result.weights) == 2
        assert sum(result.weights) == pytest.approx(1.0)

    def test_max_weight_cap_enforced(self):
        store = _FakeExperimentStore({
            "s1": [
                {"metric_name": "sharpe", "metric_value": 1.0},
                {"metric_name": "volatility", "metric_value": 0.01},
                {"metric_name": "equity_curve", "metric_value": [100, 101, 102, 103, 104]},
            ],
            "s2": [
                {"metric_name": "sharpe", "metric_value": 1.0},
                {"metric_name": "volatility", "metric_value": 0.5},
                {"metric_name": "equity_curve", "metric_value": [100, 95, 90, 85, 80]},
            ],
            "s3": [
                {"metric_name": "sharpe", "metric_value": 0.9},
                {"metric_name": "volatility", "metric_value": 0.2},
                {"metric_name": "equity_curve", "metric_value": [100, 102, 101, 103, 105]},
            ],
        })
        store.start_run("s1", {})
        store.start_run("s2", {})
        store.start_run("s3", {})
        ensemble = StrategyEnsemble(store, config={"max_weight_per_strategy": 0.6})
        result = ensemble.build(["s1", "s2", "s3"])
        assert max(result.weights) <= 0.6 + 1e-10
        assert sum(result.weights) == pytest.approx(1.0)

    def test_ensemble_result_is_serializable(self):
        store = _FakeExperimentStore({
            "s1": [{"metric_name": "sharpe", "metric_value": 1.0}],
            "s2": [{"metric_name": "sharpe", "metric_value": 0.9}],
        })
        store.start_run("s1", {})
        store.start_run("s2", {})
        ensemble = StrategyEnsemble(store)
        result = ensemble.build(["s1", "s2"])
        serialized = json.dumps({
            "strategy_ids": result.strategy_ids,
            "weights": result.weights,
            "portfolio_sharpe": result.portfolio_sharpe,
            "diversification_ratio": result.diversification_ratio,
            "mean_correlation": result.mean_correlation,
            "effective_n": result.effective_n,
        })
        assert isinstance(serialized, str)
        parsed = json.loads(serialized)
        assert parsed["strategy_ids"] == ["s1", "s2"]

    def test_equal_weight_method(self):
        store = _FakeExperimentStore({
            "s1": [{"metric_name": "sharpe", "metric_value": 1.0}],
            "s2": [{"metric_name": "sharpe", "metric_value": 0.5}],
            "s3": [{"metric_name": "sharpe", "metric_value": 0.8}],
        })
        store.start_run("s1", {})
        store.start_run("s2", {})
        store.start_run("s3", {})
        ensemble = StrategyEnsemble(store, config={"default_method": "equal_weight"})
        result = ensemble.build(["s1", "s2", "s3"])
        assert result.weights == pytest.approx([1/3, 1/3, 1/3])

    def test_inverse_vol_method(self):
        store = _FakeExperimentStore({
            "s1": [
                {"metric_name": "sharpe", "metric_value": 1.0},
                {"metric_name": "volatility", "metric_value": 0.1},
            ],
            "s2": [
                {"metric_name": "sharpe", "metric_value": 0.5},
                {"metric_name": "volatility", "metric_value": 0.3},
            ],
        })
        store.start_run("s1", {})
        store.start_run("s2", {})
        ensemble = StrategyEnsemble(store, config={"default_method": "inverse_vol"})
        result = ensemble.build(["s1", "s2"])
        assert result.weights[0] > result.weights[1]
        assert sum(result.weights) == pytest.approx(1.0)
