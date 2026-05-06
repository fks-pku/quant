import math
import shutil
import uuid
from pathlib import Path

import pandas as pd
import pytest

from quant.domain.models.trade import Trade
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
    assert result.splits[0]["regime"] == "unknown"
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


def test_rigor_hub_records_metrics_with_pipeline_run_id():
    class RecordingExperimentStore:
        def __init__(self):
            self.calls = []

        def record_metrics(self, run_id, metrics):
            self.calls.append((run_id, list(metrics)))

    def runner(strategy_id, request):
        return {"metrics": {"sharpe": 0.70, "train_sharpe": 0.90}, "equity_curve": [], "trades": [], "errors": []}

    store = RecordingExperimentStore()
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
            }
        },
        experiment_store=store,
    )

    hub.evaluate("daily_momentum", symbols=["SPY"], start="2020-01-01", end="2020-02-28", run_id="run-123")

    assert store.calls
    assert {call[0] for call in store.calls} == {"run-123"}


def test_rigor_hub_records_capacity_metrics_when_trade_volume_available():
    class RecordingExperimentStore:
        def __init__(self):
            self.calls = []

        def record_metrics(self, run_id, metrics):
            self.calls.extend(list(metrics))

    def runner(strategy_id, request):
        return {
            "metrics": {"sharpe": 0.70, "train_sharpe": 0.90},
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

    store = RecordingExperimentStore()
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
        },
        experiment_store=store,
    )

    hub.evaluate("daily_momentum", symbols=["SPY"], start="2020-01-01", end="2020-02-28", run_id="run-123")

    metrics = {metric["metric_name"]: metric["metric_value"] for metric in store.calls if metric["window_label"] == "walkforward_1"}
    assert metrics["capacity_ok"] == 0.0
    assert metrics["capacity_adv_pct"] == pytest.approx(0.10)
    assert metrics["cost_bps"] > 0.0


def test_rigor_trade_serializer_includes_trade_value_and_adv():
    from quant.features.research.rigor.backtest_hub import serialize_backtest_trades

    trade = Trade(
        symbol="SPY",
        quantity=10,
        entry_price=100,
        exit_price=110,
        entry_time=pd.Timestamp("2020-01-01").to_pydatetime(),
        exit_time=pd.Timestamp("2020-01-02").to_pydatetime(),
        side="SELL",
        fill_price=110,
        intended_qty=10,
    )
    data = pd.DataFrame(
        [
            {"date": "2020-01-01", "symbol": "SPY", "close": 100, "volume": 1_000},
            {"date": "2020-01-02", "symbol": "SPY", "close": 110, "volume": 2_000},
        ]
    )

    rows = serialize_backtest_trades([trade], data)

    assert rows == [
        {
            "symbol": "SPY",
            "side": "SELL",
            "quantity": 10.0,
            "price": 110.0,
            "trade_value": 1100.0,
            "average_daily_volume": 160000.0,
            "volatility": 0.0,
        }
    ]


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

        def evaluate(self, strategy_id, symbols, start, end, run_id=None):
            self.calls.append((strategy_id, symbols, start, end, run_id))
            return PurgedWalkForwardResult(
                splits=[{"test_sharpe": 0.50}],
                aggregate_oos_sharpe=0.50,
                worst_oos_sharpe=0.50,
                deflated_sharpe_ratio=None,
                sharpe_degradation=0.10,
                pct_profitable_splits=1.0,
                is_viable=True,
            )

    class TrackingStore:
        def __init__(self):
            self.completed = []

        def start_run(self, name, metadata):
            return "pipeline-run-1"

        def complete_run(self, run_id, status, error=None):
            self.completed.append((run_id, status, error))

    try:
        rigor_hub = PassingRigorHub()
        experiment_store = TrackingStore()
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
            experiment_store=experiment_store,
            strategies_dir=str(tmp_path / "strategies"),
            rigor_hub=rigor_hub,
        )

        result = engine.run_full_pipeline()

        assert result.integrated == 1
        assert result.walkforward_passed == 1
        assert result.run_id == "pipeline-run-1"
        assert rigor_hub.calls == [("daily_momentum", ["SPY", "QQQ"], "2020-01-01", "2024-12-31", "pipeline-run-1")]
        assert experiment_store.completed == [("pipeline-run-1", "completed", None)]
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
