import pytest

from quant.features.research.rigor.purged_cv import generate_purged_walkforward_splits
from quant.features.research.rigor.cost_model import estimate_costs
from quant.features.research.rigor.backtest_hub import RigorHub


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
