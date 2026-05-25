import time

import pytest
import pandas as pd

from quant.features.research.rigor.purged_cv import generate_purged_walkforward_splits
from quant.features.research.rigor.cost_model import estimate_costs
from quant.features.research.rigor.backtest_hub import RigorHub
from quant.features.research.rigor.dsr import compute_dsr
from quant.features.research.rigor.regime_detector import label_split_regime, compute_regime_breakdown


def test_label_split_regime_bull_with_sma_data():
    idx = pd.date_range("2020-01-01", periods=260, freq="B")
    data = pd.DataFrame({"close": range(100, 360)}, index=idx)
    split = {"test_start": 220, "test_end": 250}

    assert label_split_regime(split, data) == "bull"


def test_label_split_regime_supports_date_string_slicing():
    idx = pd.date_range("2020-01-01", periods=260, freq="B")
    data = pd.DataFrame({"close": list(range(360, 100, -1))}, index=idx)
    split = {"test_start": "2020-11-04", "test_end": "2020-12-16"}

    assert label_split_regime(split, data) == "bear"


def test_label_split_regime_falls_back_to_rolling_return_when_sma_insufficient():
    idx = pd.date_range("2020-01-01", periods=90, freq="B")
    data = pd.DataFrame({"close": range(100, 190)}, index=idx)
    split = {"test_start": 70, "test_end": 89}

    assert label_split_regime(split, data) == "bull"


def test_compute_regime_breakdown_groups_split_sharpes():
    splits = [
        {"regime": "bull", "test_sharpe": 1.2},
        {"regime": "bear", "test_sharpe": -0.8},
    ]

    breakdown = compute_regime_breakdown(splits)

    assert breakdown["bull"]["n_splits"] == 1
    assert breakdown["bull"]["sharpe"] == pytest.approx(1.2)
    assert breakdown["bear"]["sharpe"] == pytest.approx(-0.8)


class TestPurgedWalkForwardCV:
    def test_train_end_before_test_start_in_every_split(self):
        splits = generate_purged_walkforward_splits(
            n_observations=1000,
            train_window=252,
            test_window=63,
            step_days=63,
            purge_days=5,
            embargo_days=21,
            min_train_observations=126,
        )
        assert len(splits) > 0
        for s in splits:
            assert s["train_end"] < s["test_start"]

    def test_gap_at_least_purge_days(self):
        purge = 5
        splits = generate_purged_walkforward_splits(
            n_observations=1000, purge_days=purge, embargo_days=21,
        )
        for s in splits:
            assert s["test_start"] - s["train_end"] >= purge

    def test_empty_when_data_too_short(self):
        splits = generate_purged_walkforward_splits(n_observations=50)
        assert splits == []

    def test_embargo_shifts_splits(self):
        splits_small_embargo = generate_purged_walkforward_splits(
            n_observations=800, embargo_days=21, purge_days=5,
        )
        splits_large_embargo = generate_purged_walkforward_splits(
            n_observations=800, embargo_days=42, purge_days=5,
        )
        if splits_large_embargo:
            assert splits_large_embargo[0]["test_start"] >= 126 + 42
        assert len(splits_large_embargo) <= len(splits_small_embargo)


class TestCostModel:
    def test_capacity_ok_false_when_exceeds_adv(self):
        result = estimate_costs(
            trade_value=1_000_000,
            avg_daily_volume=100_000,
            price=100.0,
            volatility=0.2,
            config={"max_adv_pct": 0.05},
        )
        assert result.capacity_ok is False

    def test_total_bps_increases_with_volatility_and_participation(self):
        low_vol = estimate_costs(
            trade_value=10_000, avg_daily_volume=100_000,
            price=100.0, volatility=0.1,
        )
        high_vol = estimate_costs(
            trade_value=10_000, avg_daily_volume=100_000,
            price=100.0, volatility=0.5,
        )
        assert high_vol.total_bps > low_vol.total_bps

    def test_zero_volume_fails_capacity_without_division_by_zero(self):
        result = estimate_costs(
            trade_value=10_000,
            avg_daily_volume=0,
            price=100.0,
        )
        assert result.capacity_ok is False
        assert result.total_bps == result.total_bps

    def test_capacity_ok_true_within_limits(self):
        result = estimate_costs(
            trade_value=100,
            avg_daily_volume=1_000_000,
            price=100.0,
            config={"max_adv_pct": 0.05},
        )
        assert result.capacity_ok is True


class TestRigorHub:
    def _make_hub(self, runner, config=None):
        return RigorHub(backtest_runner=runner, config=config)

    def test_runner_called_once_per_split(self):
        call_count = 0

        def fake_runner(strategy_id, request):
            nonlocal call_count
            call_count += 1
            return {"metrics": {"sharpe": 0.5}}

        hub = self._make_hub(
            fake_runner,
            config={
                "purged_walkforward": {
                    "train_window_days": 100,
                    "test_window_days": 30,
                    "step_days": 30,
                    "purge_days": 5,
                    "embargo_days": 10,
                    "min_train_observations": 50,
                },
            },
        )
        result = hub.run_walkforward(
            strategy_id="test_strat",
            symbols=["SPY"],
            start="2020-01-01",
            end="2021-01-01",
        )
        assert call_count == len(result.splits)
        assert call_count > 0

    def test_worst_oos_sharpe_is_minimum(self):
        sharpes = [1.0, 0.5, -0.2, 0.8]
        idx = 0

        def fake_runner(strategy_id, request):
            nonlocal idx
            s = sharpes[idx]
            idx += 1
            return {"metrics": {"sharpe": s}}

        hub = self._make_hub(
            fake_runner,
            config={
                "purged_walkforward": {
                    "train_window_days": 100,
                    "test_window_days": 30,
                    "step_days": 30,
                    "purge_days": 5,
                    "embargo_days": 10,
                    "min_train_observations": 50,
                },
            },
        )
        result = hub.run_walkforward(
            strategy_id="test_strat",
            symbols=["SPY"],
            start="2020-01-01",
            end="2021-01-01",
        )
        assert result.worst_oos_sharpe == pytest.approx(min(sharpes[:len(result.splits)]))

    def test_walkforward_oos_stats_exclude_explicit_no_trade_splits(self):
        call_idx = 0

        def fake_runner(strategy_id, request):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return {"metrics": {"sharpe": -9.0, "total_trades": 0}, "trades": []}
            return {
                "metrics": {"sharpe": 0.6, "total_trades": 1},
                "trades": [{"trade_value": 100.0, "avg_daily_volume": 100_000.0, "price": 10.0}],
            }

        hub = self._make_hub(
            fake_runner,
            config={
                "purged_walkforward": {
                    "train_window_days": 100,
                    "test_window_days": 30,
                    "step_days": 30,
                    "purge_days": 5,
                    "embargo_days": 10,
                    "min_train_observations": 50,
                },
                "thresholds": {
                    "min_worst_oos_sharpe": 0.3,
                    "min_profitable_splits_pct": 0.5,
                },
            },
        )

        result = hub.run_walkforward(
            strategy_id="test_strat",
            symbols=["SPY"],
            start="2020-01-01",
            end="2021-01-01",
        )

        assert result.total_splits == len(result.splits)
        assert result.no_trade_splits == 1
        assert result.evaluated_splits == len(result.splits) - 1
        assert result.splits[0]["has_trades"] is False
        assert result.splits[0]["trade_count"] == 0
        assert result.aggregate_oos_sharpe == pytest.approx(0.6)
        assert result.worst_oos_sharpe == pytest.approx(0.6)
        assert result.pct_profitable_splits == pytest.approx(1.0)
        assert result.is_viable is True

    def test_walkforward_all_no_trade_splits_are_not_viable(self):
        def fake_runner(strategy_id, request):
            return {"metrics": {"sharpe": 9.0, "total_trades": 0}, "trades": []}

        hub = self._make_hub(
            fake_runner,
            config={
                "purged_walkforward": {
                    "train_window_days": 100,
                    "test_window_days": 30,
                    "step_days": 30,
                    "purge_days": 5,
                    "embargo_days": 10,
                    "min_train_observations": 50,
                },
                "thresholds": {
                    "min_worst_oos_sharpe": 0.3,
                    "min_profitable_splits_pct": 0.5,
                },
            },
        )

        result = hub.run_walkforward(
            strategy_id="test_strat",
            symbols=["SPY"],
            start="2020-01-01",
            end="2021-01-01",
        )

        assert result.evaluated_splits == 0
        assert result.no_trade_splits == len(result.splits)
        assert result.aggregate_oos_sharpe == 0.0
        assert result.worst_oos_sharpe == 0.0
        assert result.pct_profitable_splits == 0.0
        assert result.is_viable is False

    def test_deflated_sharpe_ratio_is_none_in_mvp(self):
        def fake_runner(strategy_id, request):
            return {"metrics": {"sharpe": 0.5}}

        hub = self._make_hub(
            fake_runner,
            config={
                "purged_walkforward": {
                    "train_window_days": 100,
                    "test_window_days": 30,
                    "step_days": 30,
                    "purge_days": 5,
                    "embargo_days": 10,
                    "min_train_observations": 50,
                },
            },
        )
        result = hub.run_walkforward(
            strategy_id="test_strat",
            symbols=["SPY"],
            start="2020-01-01",
            end="2021-01-01",
        )
        assert result.deflated_sharpe_ratio is None

    def test_walkforward_populates_deflated_sharpe_ratio_when_returns_exist(self):
        class FakeExperimentStore:
            def list_runs(self, limit=100):
                return [{"run_id": str(i)} for i in range(3)]

        def fake_runner(strategy_id, request):
            dates = pd.bdate_range(request["test_start_date"], request["test_end_date"])
            returns = pd.Series([0.001 + (i % 5) * 0.0001 for i in range(len(dates))], index=dates)
            return {"metrics": {"sharpe": 1.0}, "returns": returns}

        hub = RigorHub(
            backtest_runner=fake_runner,
            experiment_store=FakeExperimentStore(),
            config={
                "purged_walkforward": {
                    "train_window_days": 100,
                    "test_window_days": 30,
                    "step_days": 30,
                    "purge_days": 5,
                    "embargo_days": 10,
                    "min_train_observations": 50,
                },
            },
        )

        result = hub.run_walkforward(
            strategy_id="test_strat",
            symbols=["SPY"],
            start="2020-01-01",
            end="2021-01-01",
        )

        assert result.deflated_sharpe_ratio is not None
        assert 0.0 <= result.deflated_sharpe_ratio <= 1.0
        assert "response" in result.splits[0]

    def test_walkforward_requests_oos_dates_and_slices_return_series(self):
        requests = []
        seen_lengths = []

        def fake_runner(strategy_id, request):
            requests.append(request)
            dates = pd.bdate_range(request["train_start_date"], request["test_end_date"])
            duplicated = dates.append(pd.DatetimeIndex([dates[-1]]))
            values = [0.001 + (i % 5) * 0.0001 for i in range(len(duplicated))]
            returns = pd.Series(values, index=duplicated)
            return {"metrics": {"sharpe": 1.0}, "returns": returns}

        hub = self._make_hub(
            fake_runner,
            config={
                "purged_walkforward": {
                    "train_window_days": 100,
                    "test_window_days": 30,
                    "step_days": 30,
                    "purge_days": 5,
                    "embargo_days": 10,
                    "min_train_observations": 50,
                },
            },
        )

        result = hub.run_walkforward(
            strategy_id="test_strat",
            symbols=["SPY"],
            start="2020-01-01",
            end="2021-01-01",
        )

        first_request = requests[0]
        first_split_returns = result.splits[0]["response"]["returns"]
        seen_lengths.append(len(first_split_returns))
        assert first_request["start"] == first_request["test_start_date"]
        assert first_request["end"] == first_request["test_end_date"]
        assert first_request["walkforward_start_date"] == "2020-01-01"
        assert first_request["walkforward_end_date"] == "2021-01-01"
        assert first_request["walkforward_prefetch_data"] is False
        assert first_request["train_start_date"] < first_request["test_start_date"]
        assert first_split_returns.index.min() >= pd.Timestamp(first_request["test_start_date"])
        assert first_split_returns.index.max() <= pd.Timestamp(first_request["test_end_date"])
        assert first_split_returns.index.is_unique
        assert seen_lengths[0] == 30
        assert result.deflated_sharpe_ratio is not None

    def test_parallel_walkforward_preserves_split_order(self):
        def fake_runner(strategy_id, request):
            time.sleep(0.01 * (3 - request["test_start"] % 3))
            return {"metrics": {"sharpe": float(request["test_start"])}}

        hub = self._make_hub(
            fake_runner,
            config={
                "purged_walkforward": {
                    "train_window_days": 100,
                    "test_window_days": 30,
                    "step_days": 30,
                    "purge_days": 5,
                    "embargo_days": 10,
                    "min_train_observations": 50,
                    "parallel_workers": 3,
                },
            },
        )

        result = hub.run_walkforward(
            strategy_id="test_strat",
            symbols=["SPY"],
            start="2020-01-01",
            end="2021-01-01",
        )

        test_starts = [split["test_start"] for split in result.splits]
        assert len(test_starts) > 1
        assert test_starts == sorted(test_starts)
        assert [split["test_sharpe"] for split in result.splits] == [float(start) for start in test_starts]

    def test_walkforward_runner_type_error_is_not_swallowed(self):
        def mismatched_runner(strategy_id, result, config, integrator, pool):
            return None

        hub = self._make_hub(
            mismatched_runner,
            config={
                "purged_walkforward": {
                    "train_window_days": 100,
                    "test_window_days": 30,
                    "step_days": 30,
                    "purge_days": 5,
                    "embargo_days": 10,
                    "min_train_observations": 50,
                },
            },
        )

        with pytest.raises(TypeError):
            hub.run_walkforward(
                strategy_id="test_strat",
                symbols=["SPY"],
                start="2020-01-01",
                end="2021-01-01",
            )

    def test_candidate_fails_below_threshold(self):
        def fake_runner(strategy_id, request):
            return {"metrics": {"sharpe": 0.1}}

        hub = self._make_hub(
            fake_runner,
            config={
                "purged_walkforward": {
                    "train_window_days": 100,
                    "test_window_days": 30,
                    "step_days": 30,
                    "purge_days": 5,
                    "embargo_days": 10,
                    "min_train_observations": 50,
                },
                "thresholds": {
                    "min_worst_oos_sharpe": 0.3,
                    "min_profitable_splits_pct": 0.5,
                },
            },
        )
        result = hub.run_walkforward(
            strategy_id="test_strat",
            symbols=["SPY"],
            start="2020-01-01",
            end="2021-01-01",
        )
        assert result.is_viable is False

    def test_capacity_gate_fails_when_trade_exceeds_adv(self):
        def fake_runner(strategy_id, request):
            return {
                "metrics": {"sharpe": 1.0},
                "trades": [{"trade_value": 1_000_000, "avg_daily_volume": 100_000, "price": 100.0}],
            }

        hub = self._make_hub(
            fake_runner,
            config={
                "purged_walkforward": {
                    "train_window_days": 100,
                    "test_window_days": 30,
                    "step_days": 30,
                    "purge_days": 5,
                    "embargo_days": 10,
                    "min_train_observations": 50,
                },
                "thresholds": {
                    "min_worst_oos_sharpe": 0.3,
                    "min_profitable_splits_pct": 0.5,
                },
                "cost_model": {"max_adv_pct": 0.05},
            },
        )

        result = hub.run_walkforward(
            strategy_id="test_strat",
            symbols=["SPY"],
            start="2020-01-01",
            end="2021-01-01",
        )

        assert result.is_viable is False
        assert result.capacity_ok is False

    def test_capacity_gate_logs_result(self, caplog):
        def fake_runner(strategy_id, request):
            return {
                "metrics": {"sharpe": 1.0},
                "trades": [{"trade_value": 1_000_000, "avg_daily_volume": 100_000, "price": 100.0}],
            }

        hub = self._make_hub(
            fake_runner,
            config={
                "purged_walkforward": {
                    "train_window_days": 100,
                    "test_window_days": 30,
                    "step_days": 30,
                    "purge_days": 5,
                    "embargo_days": 10,
                    "min_train_observations": 50,
                },
                "thresholds": {
                    "min_worst_oos_sharpe": 0.3,
                    "min_profitable_splits_pct": 0.5,
                },
                "cost_model": {"max_adv_pct": 0.05},
            },
        )

        with caplog.at_level("INFO"):
            hub.run_walkforward(
                strategy_id="test_strat",
                symbols=["SPY"],
                start="2020-01-01",
                end="2021-01-01",
        )

        assert any("Capacity gate fail" in message and "trades_present=True" in message for message in caplog.messages)

    def test_walkforward_labels_regimes_from_benchmark_data(self):
        def fake_runner(strategy_id, request):
            return {"metrics": {"sharpe": 1.0}}

        idx = pd.date_range("2020-01-01", periods=262, freq="B")
        benchmark_data = pd.DataFrame({"close": range(100, 362)}, index=idx)
        hub = self._make_hub(
            fake_runner,
            config={
                "purged_walkforward": {
                    "train_window_days": 100,
                    "test_window_days": 30,
                    "step_days": 30,
                    "purge_days": 5,
                    "embargo_days": 10,
                    "min_train_observations": 50,
                },
                "thresholds": {
                    "min_worst_oos_sharpe": 0.3,
                    "min_profitable_splits_pct": 0.5,
                },
            },
        )

        result = hub.run_walkforward(
            strategy_id="test_strat",
            symbols=["SPY"],
            start="2020-01-01",
            end="2021-01-01",
            benchmark_data=benchmark_data,
        )

        assert result.regime_breakdown["bull"]["n_splits"] > 0
        assert result.bull_only_warning is False

    def test_bull_only_warning_false_for_bull_plus_unknown(self):
        def fake_runner(strategy_id, request):
            return {"metrics": {"sharpe": 1.0}}

        idx = pd.date_range("2020-03-25", periods=45, freq="B")
        benchmark_data = pd.DataFrame({"close": range(100, 145)}, index=idx)
        hub = self._make_hub(
            fake_runner,
            config={
                "purged_walkforward": {
                    "train_window_days": 100,
                    "test_window_days": 30,
                    "step_days": 30,
                    "purge_days": 5,
                    "embargo_days": 10,
                    "min_train_observations": 50,
                },
            },
        )

        result = hub.run_walkforward(
            strategy_id="test_strat",
            symbols=["SPY"],
            start="2020-01-01",
            end="2021-01-01",
            benchmark_data=benchmark_data,
        )

        assert set(result.regime_breakdown) == {"bull", "unknown"}
        assert result.bull_only_warning is False

    def test_bull_only_warning_true_when_bear_regime_sharpe_is_bad(self):
        def fake_runner(strategy_id, request):
            return {"metrics": {"sharpe": -0.8}}

        idx = pd.date_range("2020-01-01", periods=262, freq="B")
        benchmark_data = pd.DataFrame({"close": list(range(362, 100, -1))}, index=idx)
        hub = self._make_hub(
            fake_runner,
            config={
                "purged_walkforward": {
                    "train_window_days": 210,
                    "test_window_days": 30,
                    "step_days": 30,
                    "purge_days": 5,
                    "embargo_days": 5,
                    "min_train_observations": 210,
                },
            },
        )

        result = hub.run_walkforward(
            strategy_id="test_strat",
            symbols=["SPY"],
            start="2020-01-01",
            end="2021-01-01",
            benchmark_data=benchmark_data,
        )

        assert result.regime_breakdown["bear"]["sharpe"] < -0.5
        assert result.bull_only_warning is True


class TestDeflatedSharpeRatio:
    def test_compute_dsr_returns_probability_for_positive_returns(self):
        returns = pd.Series([0.001 + (i % 5) * 0.0001 for i in range(80)])

        dsr = compute_dsr(returns, n_trials=2)

        assert dsr is not None
        assert 0.0 <= dsr <= 1.0

    def test_compute_dsr_returns_none_for_short_history(self):
        assert compute_dsr(pd.Series([0.01] * 29)) is None

    def test_compute_dsr_penalizes_multiple_trials(self):
        returns = pd.Series([0.0002 + ((i % 5) - 2) * 0.001 for i in range(120)])

        single_trial = compute_dsr(returns, n_trials=1)
        many_trials = compute_dsr(returns, n_trials=100)

        assert many_trials < single_trial
