import importlib.util
from datetime import date
from pathlib import Path

from quant.features.research.integrator import StrategyIntegrator
from quant.features.research.models import EvaluationReport, RawStrategy, StrategySpec


def _raw(title="Daily Momentum Breakout"):
    return RawStrategy(
        title=title,
        description="Ranks daily bars by signal and trades liquid symbols.",
        source="arxiv",
        source_url="https://example.test/paper",
    )


def _report(strategy_type="momentum"):
    return EvaluationReport(
        suitability_score=8.0,
        complexity_score=3.0,
        data_requirement="low",
        daily_adaptable=True,
        estimated_edge=0.08,
        recommended_symbols=["AAPL"],
        strategy_type=strategy_type,
        summary="Daily strategy candidate.",
        required_data_fields=["date", "symbol", "close"],
        validation_tests=["rank_ic", "fdr_control"],
    )


def _spec(strategy_type="momentum", formula="momentum_close_return", strategy_id="daily_momentum_breakout"):
    return StrategySpec(
        strategy_id=strategy_id,
        strategy_type=strategy_type,
        signal_formula_key=formula,
        universe=["AAPL"],
        horizon_days=5,
        lookback_days=3,
        execution_lag_days=1,
        required_fields=["close"],
        status="ready",
    )


def _load_generated_class(strategy_file: Path, class_name: str):
    spec = importlib.util.spec_from_file_location("generated_strategy_under_test", strategy_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, class_name)


class _Portfolio:
    nav = 100000.0


class _Context:
    def __init__(self):
        self.portfolio = _Portfolio()
        self.orders = []

    def submit_order(self, symbol, quantity, side, order_type, price, strategy_name):
        self.orders.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "side": side,
                "order_type": order_type,
                "price": price,
                "strategy_name": strategy_name,
            }
        )
        return f"order-{len(self.orders)}"


def test_integrator_generates_executable_momentum_strategy_from_spec(tmp_path):
    integrator = StrategyIntegrator(tmp_path)
    raw = _raw()
    report = _report("momentum")
    spec = _spec("momentum", "momentum_close_return")

    strategy_id = integrator.integrate(raw, report, spec=spec)
    strategy_file = tmp_path / strategy_id / "strategy.py"
    code = strategy_file.read_text(encoding="utf-8")

    assert "TODO" not in code
    assert "DailyBarStrategy" in code
    assert f'@strategy("{strategy_id}")' in code
    assert "momentum_close_return" in code

    cls = _load_generated_class(strategy_file, "DailyMomentumBreakoutStrategy")
    strategy = cls(symbols=["AAPL"], lookback=3, max_position_pct=0.5)
    context = _Context()
    strategy.on_start(context)
    for close in [100.0, 101.0, 102.0, 106.0]:
        strategy.on_data(context, {"symbol": "AAPL", "close": close, "high": close, "low": close, "volume": 1000000})

    strategy.on_after_trading(context, date(2026, 5, 9))

    assert context.orders
    assert context.orders[-1]["side"] == "BUY"
    assert context.orders[-1]["symbol"] == "AAPL"
    assert context.orders[-1]["strategy_name"] == strategy_id


def test_integrator_generates_mean_reversion_formula_logic(tmp_path):
    integrator = StrategyIntegrator(tmp_path)
    raw = _raw("Daily Mean Reversion")
    report = _report("mean_reversion")
    spec = _spec("mean_reversion", "mean_reversion_close_to_ma")

    strategy_id = integrator.integrate(raw, report, spec=spec)
    code = (tmp_path / strategy_id / "strategy.py").read_text(encoding="utf-8")

    assert "mean_reversion_close_to_ma" in code
    assert "moving_average" in code
    assert "TODO" not in code


def test_integrator_uses_ready_spec_strategy_id_for_generated_candidate(tmp_path):
    integrator = StrategyIntegrator(tmp_path)
    raw = _raw("Paper Title With Punctuation!")
    report = _report("momentum")
    spec = _spec("momentum", "momentum_close_return", strategy_id="paper_alpha_v2")

    strategy_id = integrator.integrate(raw, report, spec=spec)
    code = (tmp_path / "paper_alpha_v2" / "strategy.py").read_text(encoding="utf-8")
    config = (tmp_path / "paper_alpha_v2" / "config.yaml").read_text(encoding="utf-8")
    entry = integrator.get_registry_entry("paper_alpha_v2")

    assert strategy_id == "paper_alpha_v2"
    assert '@strategy("paper_alpha_v2")' in code
    assert 'super().__init__("paper_alpha_v2"' in code
    assert "name: paper_alpha_v2" in config
    assert entry["id"] == "paper_alpha_v2"
    assert entry["research_meta"]["strategy_spec"]["strategy_id"] == "paper_alpha_v2"


def test_integrator_reuses_existing_generated_strategy(tmp_path):
    integrator = StrategyIntegrator(tmp_path)
    raw = _raw()
    report = _report("momentum")
    spec = _spec("momentum", "momentum_close_return")

    first_id = integrator.integrate(raw, report, spec=spec)
    second_id = integrator.integrate(raw, report, spec=spec)

    assert first_id == "daily_momentum_breakout"
    assert second_id == "daily_momentum_breakout"
    assert integrator.get_registry_entry("daily_momentum_breakout")["status"] == "candidate"


def test_integrator_registers_generated_strategy_for_backtest_lookup(tmp_path):
    from quant.features.strategies.registry import StrategyRegistry

    strategy_id = "registry_smoke_alpha"
    integrator = StrategyIntegrator(tmp_path)
    raw = _raw("Registry Smoke Alpha")
    report = _report("momentum")
    spec = _spec("momentum", "momentum_close_return", strategy_id=strategy_id)

    assert StrategyRegistry.get(strategy_id) is None

    integrated_id = integrator.integrate(raw, report, spec=spec)

    cls = StrategyRegistry.get(strategy_id)
    assert integrated_id == strategy_id
    assert cls is not None
    assert cls(symbols=["AAPL"]).name == strategy_id


def test_integrator_preserves_manual_review_for_unknown_formula(tmp_path):
    integrator = StrategyIntegrator(tmp_path)
    raw = _raw("Unmapped Signal")
    report = _report("unknown")
    spec = _spec("unknown", "paper_specific_formula")

    strategy_id = integrator.integrate(raw, report, spec=spec)
    code = (tmp_path / strategy_id / "strategy.py").read_text(encoding="utf-8")

    assert "Manual implementation required" in code
    assert "TODO" not in code
