import math
import shutil
import uuid
from pathlib import Path

import pandas as pd
import pytest

from quant.features.research.models import EvaluationReport, PurgedWalkForwardResult, RawStrategy, ResearchConfig
from quant.features.research.research_engine import ResearchEngine
from quant.features.research.rigor import CostModel, RigorHub, generate_purged_walk_forward_splits
from quant.infrastructure.research.repository import FileResearchStore


def _raw_strategy() -> RawStrategy:
    return RawStrategy(
        title="Daily Momentum",
        description="Ranks liquid stocks by close-to-close momentum.",
        source="test",
        source_url="https://example.test/research",
    )


def _evaluation_report() -> EvaluationReport:
    return EvaluationReport(
        suitability_score=8.0,
        complexity_score=3.0,
        data_requirement="low",
        daily_adaptable=True,
        estimated_edge=0.08,
        recommended_symbols=["SPY", "QQQ"],
        strategy_type="momentum",
        summary="Daily momentum signal.",
    )


def _test_root() -> Path:
    root = Path(__file__).resolve().parents[1] / "infrastructure" / "var" / "test_research_rigor" / str(uuid.uuid4())
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_purged_cv_splits_have_non_leaking_gap():
    dates = pd.date_range("2020-01-01", periods=80, freq="D")

    splits = generate_purged_walk_forward_splits(
        dates,
        train_window_days=20,
        test_window_days=5,
        step_days=10,
        purge_days=3,
        embargo_days=0,
        min_train_observations=20,
    )

    assert splits
    for split in splits:
        train_end = pd.Timestamp(split["train_end"])
        test_start = pd.Timestamp(split["test_start"])
        assert train_end < test_start
        assert (test_start - train_end).days >= 3


def test_purged_cv_empty_when_data_below_minimum():
    splits = generate_purged_walk_forward_splits(
        pd.date_range("2020-01-01", periods=20, freq="D"),
        train_window_days=30,
        test_window_days=5,
        step_days=5,
        purge_days=2,
        embargo_days=0,
        min_train_observations=30,
    )

    assert splits == []


def test_purged_cv_zero_purge_still_starts_test_after_train():
    splits = generate_purged_walk_forward_splits(
        pd.date_range("2020-01-01", periods=40, freq="D"),
        train_window_days=10,
        test_window_days=5,
        step_days=5,
        purge_days=0,
        embargo_days=0,
        min_train_observations=10,
    )

    assert splits
    assert pd.Timestamp(splits[0]["train_end"]) < pd.Timestamp(splits[0]["test_start"])


def test_purged_cv_embargo_shifts_next_split():
    dates = pd.date_range("2020-01-01", periods=90, freq="D")
    without_embargo = generate_purged_walk_forward_splits(
        dates,
        train_window_days=20,
        test_window_days=5,
        step_days=10,
        purge_days=2,
        embargo_days=0,
        min_train_observations=20,
    )
    with_embargo = generate_purged_walk_forward_splits(
        dates,
        train_window_days=20,
        test_window_days=5,
        step_days=10,
        purge_days=2,
        embargo_days=4,
        min_train_observations=20,
    )

    assert len(without_embargo) >= 2
    assert len(with_embargo) >= 2
    assert with_embargo[1]["train_start_index"] == without_embargo[1]["train_start_index"] + 4


def test_cost_model_capacity_and_zero_volume():
    model = CostModel({"spread_bps": 2.0, "commission_bps": 1.0, "max_adv_pct": 0.05})

    over_capacity = model.estimate_trade(
        trade_value=200_000,
        average_daily_volume=2_000_000,
        price=100,
        volatility=0.02,
    )
    zero_volume = model.estimate_trade(
        trade_value=1_000,
        average_daily_volume=0,
        price=100,
        volatility=0.02,
    )

    assert over_capacity.capacity_ok is False
    assert over_capacity.capacity_adv_pct == pytest.approx(0.10)
    assert zero_volume.capacity_ok is False
    assert math.isfinite(zero_volume.capacity_adv_pct)


def test_cost_model_total_bps_increases_with_volatility_and_participation():
    model = CostModel({"spread_bps": 2.0, "commission_bps": 1.0, "impact_coefficient": 20.0})

    low = model.estimate_trade(10_000, average_daily_volume=1_000_000, price=100, volatility=0.01)
    high = model.estimate_trade(100_000, average_daily_volume=1_000_000, price=100, volatility=0.04)

    assert high.total_bps > low.total_bps
    assert high.market_impact > low.market_impact


def test_rigor_hub_calls_runner_once_per_split_and_aggregates_oos_metrics():
    calls = []
    sharpes = [0.60, 0.40, 0.80, 0.50]

    def runner(strategy_id, request):
        calls.append((strategy_id, request))
        sharpe = sharpes[len(calls) - 1]
        return {"metrics": {"sharpe": sharpe, "train_sharpe": 0.90}, "equity_curve": [], "trades": [], "errors": []}

    hub = RigorHub(
        runner,
        config={
            "purged_walkforward": {
                "train_window_days": 20,
                "test_window_days": 5,
                "step_days": 10,
                "purge_days": 2,
                "embargo_days": 0,
                "min_train_observations": 20,
            },
            "thresholds": {"min_worst_oos_sharpe": 0.30, "min_profitable_splits_pct": 0.50},
        },
    )

    result = hub.evaluate("daily_momentum", symbols=["SPY"], start="2020-01-01", end="2020-02-28")

    assert len(calls) == len(result.splits)
    assert result.worst_oos_sharpe == pytest.approx(min(split["test_sharpe"] for split in result.splits))
    assert result.deflated_sharpe_ratio is None
    assert result.is_viable is True
    assert calls[0][0] == "daily_momentum"
    assert {"start", "end", "symbols", "initial_cash", "cost_config", "run_label"}.issubset(calls[0][1])


def test_rigor_hub_marks_candidate_not_viable_when_worst_oos_below_threshold():
    def runner(strategy_id, request):
        return {"metrics": {"sharpe": 0.10, "train_sharpe": 0.80}, "equity_curve": [], "trades": [], "errors": []}

    hub = RigorHub(
        runner,
        config={
            "purged_walkforward": {
                "train_window_days": 20,
                "test_window_days": 5,
                "step_days": 10,
                "purge_days": 2,
                "embargo_days": 0,
                "min_train_observations": 20,
            },
            "thresholds": {"min_worst_oos_sharpe": 0.30, "min_profitable_splits_pct": 0.50},
        },
    )

    result = hub.evaluate("daily_momentum", symbols=["SPY"], start="2020-01-01", end="2020-02-28")

    assert isinstance(result, PurgedWalkForwardResult)
    assert result.worst_oos_sharpe == pytest.approx(0.10)
    assert result.is_viable is False


def test_rigor_hub_fails_capacity_when_trades_exceed_adv_cap():
    def runner(strategy_id, request):
        return {
            "metrics": {"sharpe": 0.80, "train_sharpe": 0.90},
            "equity_curve": [],
            "trades": [
                {
                    "trade_value": 100_000,
                    "average_daily_volume": 1_000_000,
                    "price": 100,
                    "volatility": 0.02,
                }
            ],
            "errors": [],
        }

    hub = RigorHub(
        runner,
        config={
            "purged_walkforward": {
                "train_window_days": 20,
                "test_window_days": 5,
                "step_days": 10,
                "purge_days": 2,
                "embargo_days": 0,
                "min_train_observations": 20,
            },
            "cost_model": {"max_adv_pct": 0.05},
            "thresholds": {"min_worst_oos_sharpe": 0.30, "min_profitable_splits_pct": 0.50},
        },
    )

    result = hub.evaluate("daily_momentum", symbols=["SPY"], start="2020-01-01", end="2020-02-28")

    assert result.is_viable is False
    assert result.splits[0]["capacity_ok"] is False


def test_research_engine_runs_optional_rigor_gate_for_integrated_candidates():
    tmp_path = _test_root()

    class FixedScout:
        def search(self, sources=None, max_results=10):
            return [_raw_strategy()]

    class FixedEvaluator:
        def evaluate(self, raw):
            return _evaluation_report()

    class PassingRigorHub:
        def __init__(self):
            self.calls = []

        def evaluate(self, strategy_id, symbols, start, end):
            self.calls.append((strategy_id, symbols, start, end))
            return PurgedWalkForwardResult(
                splits=[{"test_sharpe": 0.50}],
                aggregate_oos_sharpe=0.50,
                worst_oos_sharpe=0.50,
                deflated_sharpe_ratio=None,
                sharpe_degradation=0.10,
                pct_profitable_splits=1.0,
                is_viable=True,
            )

    try:
        rigor_hub = PassingRigorHub()
        research_store = FileResearchStore(tmp_path / "research")
        engine = ResearchEngine(
            config=ResearchConfig(
                auto_backtest=False,
                validation_enabled=False,
                rigor_enabled=True,
                default_symbols=["SPY", "QQQ"],
            ),
            scout=FixedScout(),
            evaluator=FixedEvaluator(),
            research_store=research_store,
            strategies_dir=str(tmp_path / "strategies"),
            rigor_hub=rigor_hub,
        )

        result = engine.run_full_pipeline()

        assert result.integrated == 1
        assert result.walkforward_passed == 1
        assert rigor_hub.calls == [("daily_momentum", ["SPY", "QQQ"], "2020-01-01", "2024-12-31")]
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
