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


def test_generic_generated_rebalance_uses_top_max_positions(tmp_path):
    integrator = StrategyIntegrator(tmp_path)
    raw = _raw("Top Capped Momentum")
    report = _report("momentum")
    spec = _spec("momentum", "momentum_close_return", strategy_id="top_capped_momentum")

    strategy_id = integrator.integrate(raw, report, spec=spec)
    strategy_file = tmp_path / strategy_id / "strategy.py"
    cls = _load_generated_class(strategy_file, "TopCappedMomentumStrategy")
    strategy = cls(symbols=["600001", "600002", "600003"], lookback=2, max_positions=1)
    context = _Context()
    strategy.on_start(context)
    for symbol, closes in {
        "600001": [100.0, 101.0, 102.0],
        "600002": [100.0, 101.0, 104.0],
        "600003": [100.0, 101.0, 103.0],
    }.items():
        for close in closes:
            strategy.on_data(context, {"symbol": symbol, "close": close, "high": close, "low": close, "volume": 1000000})

    strategy.on_after_trading(context, date(2026, 5, 9))

    buy_orders = [order for order in context.orders if order["side"] == "BUY"]
    assert len(buy_orders) == 1
    assert buy_orders[0]["symbol"] == "600002"


def test_integrator_prefixes_generated_class_name_when_title_starts_with_number(tmp_path):
    integrator = StrategyIntegrator(tmp_path)
    raw = _raw("2026 Trend Following Momentum")
    report = _report("momentum")
    spec = _spec("momentum", "momentum_close_return", strategy_id="2026_trend_following_momentum")

    strategy_id = integrator.integrate(raw, report, spec=spec)
    strategy_file = tmp_path / strategy_id / "strategy.py"

    assert "class Strategy2026TrendFollowingMomentumStrategy" in strategy_file.read_text(encoding="utf-8")
    _load_generated_class(strategy_file, "Strategy2026TrendFollowingMomentumStrategy")


def test_integrator_generates_mean_reversion_formula_logic(tmp_path):
    integrator = StrategyIntegrator(tmp_path)
    raw = _raw("Daily Mean Reversion")
    report = _report("mean_reversion")
    spec = _spec("mean_reversion", "mean_reversion_close_to_ma")

    strategy_id = integrator.integrate(raw, report, spec=spec)
    code = (tmp_path / strategy_id / "strategy.py").read_text(encoding="utf-8")

    assert "mean_reversion_close_to_ma" in code
    assert "from quant.analytics.signal_kernels import compute_signal" in code
    assert 'compute_signal("mean_reversion_close_to_ma", frame, self.lookback)' in code
    assert "TODO" not in code


def test_integrator_generates_a_share_structural_formula_logic(tmp_path):
    integrator = StrategyIntegrator(tmp_path)
    raw = _raw("A-Share Short-Term Reversal 5D")
    report = _report("mean_reversion")
    spec = _spec("mean_reversion", "ashare_short_reversal_5d", strategy_id="a_share_short_term_reversal_5d")

    strategy_id = integrator.integrate(raw, report, spec=spec)
    strategy_file = tmp_path / strategy_id / "strategy.py"
    code = strategy_file.read_text(encoding="utf-8")

    assert "ashare_short_reversal_5d" in code
    assert 'compute_signal("ashare_short_reversal_5d", frame, self.lookback)' in code
    assert "Manual implementation required" not in code
    assert "TODO" not in code

    cls = _load_generated_class(strategy_file, "AshareShorttermReversal5dStrategy")
    strategy = cls(symbols=["600001"], lookback=2)
    assert strategy.name == "a_share_short_term_reversal_5d"


def test_integrator_generates_a_share_extended_liquidity_formula_logic(tmp_path):
    integrator = StrategyIntegrator(tmp_path)
    raw = _raw("A-Share Liquidity-Weighted Low Volatility")
    report = _report("factor")
    spec = _spec("factor", "ashare_liquidity_weighted_low_volatility", strategy_id="a_share_liquidity_weighted_low_volatility")

    strategy_id = integrator.integrate(raw, report, spec=spec)
    strategy_file = tmp_path / strategy_id / "strategy.py"
    code = strategy_file.read_text(encoding="utf-8")

    assert "ashare_liquidity_weighted_low_volatility" in code
    assert "_bar_turnover" in code
    assert 'compute_signal("ashare_liquidity_weighted_low_volatility", frame, self.lookback)' in code
    assert "Manual implementation required" not in code

    cls = _load_generated_class(strategy_file, "AshareLiquidityweightedLowVolatilityStrategy")
    strategy = cls(symbols=["600001"], lookback=20)
    assert strategy.name == "a_share_liquidity_weighted_low_volatility"


def test_integrator_generates_worldquant_alpha_001_formula_logic(tmp_path):
    integrator = StrategyIntegrator(tmp_path)
    raw = _raw("WorldQuant 101 Alpha #001")
    report = _report("worldquant_factor")
    spec = _spec("worldquant_factor", "worldquant_alpha_001", strategy_id="worldquant_101_alpha_001")

    strategy_id = integrator.integrate(raw, report, spec=spec)
    strategy_file = tmp_path / strategy_id / "strategy.py"
    code = strategy_file.read_text(encoding="utf-8")

    assert "worldquant_alpha_001" in code
    assert 'compute_signal("worldquant_alpha_001", frame, self.lookback)' in code
    assert code.count("def _execute_rebalance") == 1
    assert "Manual implementation required" not in code
    assert "TODO" not in code

    cls = _load_generated_class(strategy_file, "Worldquant101Alpha001Strategy")
    strategy = cls(symbols=["600001", "600002"], lookback=20)
    assert strategy.name == "worldquant_101_alpha_001"
    assert strategy.max_position_pct == 1.0


def test_generated_strategy_uses_analytics_signal_kernel_for_screening_source(tmp_path):
    integrator = StrategyIntegrator(tmp_path)
    raw = _raw("BigQuant Industry Rotation")
    report = _report("momentum")
    spec = _spec(
        "momentum",
        "ashare_industry_prosperity_trend_crowding_rotation",
        strategy_id="bigquant_industry_rotation",
    )

    strategy_file = integrator.write_screening_source(raw, report, spec=spec)
    assert strategy_file == tmp_path / "reject" / "bigquant_industry_rotation" / "strategy.py"
    assert strategy_file.exists()
    code = strategy_file.read_text(encoding="utf-8")

    assert "from quant.analytics.signal_kernels import compute_signal" in code
    assert 'compute_signal("ashare_industry_prosperity_trend_crowding_rotation", frame, self.lookback)' in code


def test_integrator_generates_worldquant_alpha_002_formula_logic(tmp_path):
    integrator = StrategyIntegrator(tmp_path)
    raw = _raw("WorldQuant 101 Alpha #002")
    report = _report("worldquant_factor")
    spec = _spec("worldquant_factor", "worldquant_alpha_002", strategy_id="worldquant_101_alpha_002")

    strategy_id = integrator.integrate(raw, report, spec=spec)
    strategy_file = tmp_path / strategy_id / "strategy.py"
    code = strategy_file.read_text(encoding="utf-8")

    assert "worldquant_alpha_002" in code
    assert 'compute_signal("worldquant_alpha_002", frame, self.lookback)' in code
    assert code.count("def _execute_rebalance") == 1
    assert "Manual implementation required" not in code
    assert "TODO" not in code

    cls = _load_generated_class(strategy_file, "Worldquant101Alpha002Strategy")
    strategy = cls(symbols=["600001", "600002"], lookback=6)
    assert strategy.name == "worldquant_101_alpha_002"


def test_integrator_generates_worldquant_alpha_003_formula_logic(tmp_path):
    integrator = StrategyIntegrator(tmp_path)
    raw = _raw("WorldQuant 101 Alpha #003")
    report = _report("worldquant_factor")
    spec = _spec("worldquant_factor", "worldquant_alpha_003", strategy_id="worldquant_101_alpha_003")

    strategy_id = integrator.integrate(raw, report, spec=spec)
    strategy_file = tmp_path / strategy_id / "strategy.py"
    code = strategy_file.read_text(encoding="utf-8")

    assert "worldquant_alpha_003" in code
    assert 'compute_signal("worldquant_alpha_003", frame, self.lookback)' in code
    assert code.count("def _execute_rebalance") == 1
    assert "Manual implementation required" not in code
    assert "TODO" not in code

    cls = _load_generated_class(strategy_file, "Worldquant101Alpha003Strategy")
    strategy = cls(symbols=["600001", "600002"], lookback=10)
    assert strategy.name == "worldquant_101_alpha_003"


def test_integrator_generates_worldquant_alpha_004_formula_logic(tmp_path):
    integrator = StrategyIntegrator(tmp_path)
    raw = _raw("WorldQuant 101 Alpha #004")
    report = _report("worldquant_factor")
    spec = _spec("worldquant_factor", "worldquant_alpha_004", strategy_id="worldquant_101_alpha_004")

    strategy_id = integrator.integrate(raw, report, spec=spec)
    strategy_file = tmp_path / strategy_id / "strategy.py"
    code = strategy_file.read_text(encoding="utf-8")

    assert "worldquant_alpha_004" in code
    assert 'compute_signal("worldquant_alpha_004", frame, self.lookback)' in code
    assert code.count("def _execute_rebalance") == 1
    assert "Manual implementation required" not in code
    assert "TODO" not in code

    cls = _load_generated_class(strategy_file, "Worldquant101Alpha004Strategy")
    strategy = cls(symbols=["600001", "600002"], lookback=9)
    assert strategy.name == "worldquant_101_alpha_004"


def test_integrator_generates_worldquant_alpha_006_formula_logic(tmp_path):
    integrator = StrategyIntegrator(tmp_path)
    raw = _raw("WorldQuant 101 Alpha #006")
    report = _report("worldquant_factor")
    spec = _spec("worldquant_factor", "worldquant_alpha_006", strategy_id="worldquant_101_alpha_006")

    strategy_id = integrator.integrate(raw, report, spec=spec)
    strategy_file = tmp_path / strategy_id / "strategy.py"
    code = strategy_file.read_text(encoding="utf-8")

    assert "worldquant_alpha_006" in code
    assert 'compute_signal("worldquant_alpha_006", frame, self.lookback)' in code
    assert code.count("def _execute_rebalance") == 1
    assert "Manual implementation required" not in code
    assert "TODO" not in code

    cls = _load_generated_class(strategy_file, "Worldquant101Alpha006Strategy")
    strategy = cls(symbols=["600001", "600002"], lookback=10)
    assert strategy.name == "worldquant_101_alpha_006"


def test_integrator_generates_worldquant_alpha_010_formula_logic(tmp_path):
    integrator = StrategyIntegrator(tmp_path)
    raw = _raw("WorldQuant 101 Alpha #010")
    report = _report("worldquant_factor")
    spec = _spec("worldquant_factor", "worldquant_alpha_010", strategy_id="worldquant_101_alpha_010")

    strategy_id = integrator.integrate(raw, report, spec=spec)
    strategy_file = tmp_path / strategy_id / "strategy.py"
    code = strategy_file.read_text(encoding="utf-8")

    assert "worldquant_alpha_010" in code
    assert 'compute_signal("worldquant_alpha_010", frame, self.lookback)' in code
    assert code.count("def _execute_rebalance") == 1
    assert "Manual implementation required" not in code
    assert "TODO" not in code

    cls = _load_generated_class(strategy_file, "Worldquant101Alpha010Strategy")
    strategy = cls(symbols=["600001", "600002"], lookback=4)
    assert strategy.name == "worldquant_101_alpha_010"


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
    assert "max_position_pct: 1.0" in config
    assert 'symbols: ["000300", "000905", "600519", "000001", "510300"]' in config
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
