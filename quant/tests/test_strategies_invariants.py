"""Invariant tests for strategies module — Registry, Strategy base, _adj."""
from __future__ import annotations

from datetime import date, timedelta
import importlib
import json
import math
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from quant.features.strategies.base import Strategy
from quant.features.strategies.reject.ashare_alpha158_factor_composite.strategy import (
    AShareAlpha158FactorCompositeStrategy,
)
from quant.features.strategies.reject.ashare_davis_double_click.strategy import (
    AShareDavisDoubleClickStrategy,
)
from quant.features.strategies.reject.ashare_csi1000_strict_index_enhanced.strategy import (
    AShareCsi1000StrictIndexEnhancedStrategy,
)
from quant.features.strategies.reject.joinquant_value_rsrs_timing.strategy import JoinquantValueRsrsTimingStrategy
from quant.features.strategies.registry import StrategyRegistry, strategy
from quant.features.strategies.ashare_gold_equity_barbell_timing.strategy import (
    AShareGoldEquityBarbellTimingStrategy,
)
from quant.features.strategies.xueqiu_small_cap_financial_filter.strategy import (
    XueqiuSmallCapFinancialFilterStrategy,
)


# ---------------------------------------------------------------------------
# CASE-1: Registry CRUD
# ---------------------------------------------------------------------------

class TestCase1RegistryCRUD:
    def test_s1_01_registered(self):
        @strategy("TestInvS1")
        class Dummy:
            pass
        assert StrategyRegistry.is_registered("TestInvS1") is True

    def test_s1_02_create_instance(self):
        @strategy("TestInvS1b")
        class DummyB:
            def __init__(self, val=0):
                self.val = val
        inst = StrategyRegistry.create("TestInvS1b", val=42)
        assert isinstance(inst, DummyB)
        assert inst.val == 42

    def test_s1_03_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown strategy"):
            StrategyRegistry.create("NonExistentStrategy999")

    def test_s1_04_list_contains(self):
        @strategy("TestInvS1c")
        class DummyC:
            pass
        assert "TestInvS1c" in StrategyRegistry.list_strategies()

    def test_s1_05_manual_register_sets_registry_metadata(self):
        class ManualRegistered:
            pass

        StrategyRegistry.register("TestInvS1d", ManualRegistered)

        assert ManualRegistered._registry_name == "TestInvS1d"
        assert "TestInvS1d" in StrategyRegistry.list_strategies()


# ---------------------------------------------------------------------------
# CASE-2: _adj helper priority
# ---------------------------------------------------------------------------

class TestCase2AdjHelper:
    """_adj() is for signals/indicators — returns backward-adjusted price as-is."""

    def test_s2_01_prefers_adj_close(self):
        bar = {"close": 100.0, "adj_close": 105.0}
        assert Strategy._adj(bar, "close") == pytest.approx(105.0)

    def test_s2_02_falls_back_to_close(self):
        bar = {"close": 100.0}
        assert Strategy._adj(bar, "close") == pytest.approx(100.0)

    def test_s2_03_nan_fallback(self):
        bar = {"close": 100.0, "adj_close": float("nan")}
        assert Strategy._adj(bar, "close") == pytest.approx(100.0)

    def test_s2_04_none_fallback(self):
        bar = {"close": 100.0, "adj_close": None}
        assert Strategy._adj(bar, "close") == pytest.approx(100.0)

    def test_s2_05_cn_backward_adjusted_preserved_for_signals(self):
        """CN adj_close = close * adj_factor. _adj keeps it for MA continuity."""
        bar = {"close": 10.0, "adj_close": 1160.0, "adj_factor": 116.0}
        assert Strategy._adj(bar, "close") == pytest.approx(1160.0)


# ---------------------------------------------------------------------------
# CASE-2b: _price helper — real market price for quantity/order sizing
# ---------------------------------------------------------------------------

class TestCase2bPriceHelper:
    """_price() is for order sizing — returns actual market close."""

    def test_s2b_01_returns_close(self):
        bar = {"close": 100.0, "adj_close": 105.0}
        assert Strategy._price(bar) == pytest.approx(100.0)

    def test_s2b_02_cn_real_price(self):
        """Even with high adj_close, _price returns actual market price."""
        bar = {"close": 10.0, "adj_close": 1160.0, "adj_factor": 116.0}
        assert Strategy._price(bar) == pytest.approx(10.0)

    def test_s2b_03_zero_close(self):
        bar = {"close": 0.0}
        assert Strategy._price(bar) == pytest.approx(0.0)

    def test_s2b_04_missing_close(self):
        bar = {}
        assert Strategy._price(bar) == pytest.approx(0.0)

    def test_s2b_05_object_bar(self):
        bar = type("Bar", (), {"close": 55.5, "adj_close": 999.0})()
        assert Strategy._price(bar) == pytest.approx(55.5)


# ---------------------------------------------------------------------------
# CASE-3: buy/sell no-context silent failure
# ---------------------------------------------------------------------------

class TestCase3NoContext:
    def test_s3_01_buy_returns_none(self):
        class TestStrat(Strategy):
            def __init__(self):
                super().__init__("test_no_ctx")

        s = TestStrat()
        assert s.buy("AAPL", 100) is None

    def test_s3_02_sell_returns_none(self):
        class TestStrat(Strategy):
            def __init__(self):
                super().__init__("test_no_ctx2")

        s = TestStrat()
        assert s.sell("AAPL", 100) is None


# ---------------------------------------------------------------------------
# CASE-4: on_fill updates internal positions
# ---------------------------------------------------------------------------

class TestCase4OnFill:
    def test_s4_01_buy_updates_position(self):
        class TestStrat(Strategy):
            def __init__(self):
                super().__init__("test_fill1")

        s = TestStrat()

        class Fill:
            symbol = "AAPL"
            quantity = 100
            side = "BUY"

        s.on_fill(None, Fill())
        assert s.get_position("AAPL") == 100

    def test_s4_02_sell_updates_position(self):
        class TestStrat(Strategy):
            def __init__(self):
                super().__init__("test_fill2")

        s = TestStrat()

        class FillBuy:
            symbol = "AAPL"
            quantity = 100
            side = "BUY"

        class FillSell:
            symbol = "AAPL"
            quantity = 40
            side = "SELL"

        s.on_fill(None, FillBuy())
        s.on_fill(None, FillSell())
        assert s.get_position("AAPL") == 60


# ---------------------------------------------------------------------------
# CASE-5: Daily strategy risk-exit/rebalance state machine
# ---------------------------------------------------------------------------


def _value_rsrs_bar(symbol: str, close: float, **overrides):
    bar = {
        "timestamp": date(2024, 1, 2),
        "symbol": symbol,
        "open": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "turnover": 100000.0,
        "pe_ttm": 8.0,
        "pb": 1.0,
        "ps_ttm": 1.0,
        "dv_ttm": 1.0,
        "total_mv": 1000.0,
        "circ_mv": 1000.0,
        "is_st": False,
        "tradable": True,
        "has_daily_bar": True,
        "is_listed": True,
        "list_status": "L",
    }
    bar.update(overrides)
    return bar


class _InvariantPortfolio:
    nav = 100000.0

    def get_position(self, symbol):
        return None


class _InvariantContext:
    portfolio = _InvariantPortfolio()


class TestCase5DailyRiskExitStateMachine:
    def test_s5_01_pending_risk_exit_is_not_sold_again_by_same_day_rebalance(self, monkeypatch):
        strategy = JoinquantValueRsrsTimingStrategy(
            symbols=["000001", "000002"],
            holding_days=1,
            min_turnover=0.0,
            stop_loss_pct=0.10,
        )
        strategy._positions["000001"] = 100
        strategy._entry_prices["000001"] = 10.0
        strategy._risk_on = True
        monkeypatch.setattr(strategy, "_update_rsrs_state", lambda: True)
        monkeypatch.setattr(strategy, "_check_rebalance_gate", lambda trading_date: True)
        strategy.on_data(None, _value_rsrs_bar("000001", 8.9))
        strategy.on_data(None, _value_rsrs_bar("000002", 10.0, pb=0.8))
        sells = []
        monkeypatch.setattr(
            strategy,
            "sell",
            lambda symbol, quantity, order_type="MARKET", price=None: sells.append((symbol, quantity, price)),
        )
        monkeypatch.setattr(strategy, "buy", lambda *args, **kwargs: "order-buy")

        strategy.on_after_trading(_InvariantContext(), date(2024, 1, 2))

        assert sells == [("000001", 100, 8.9)]

    def test_s5_02_risk_on_reentry_bypasses_stale_rebalance_gate(self, monkeypatch):
        strategy = JoinquantValueRsrsTimingStrategy(symbols=["000001"], holding_days=20)
        strategy._last_rebalance_date = date(2024, 1, 1)
        strategy._days_since_rebalance = 0
        strategy._risk_on = False
        monkeypatch.setattr(strategy, "_update_rsrs_state", lambda: True)
        called = []
        monkeypatch.setattr(
            strategy,
            "_execute_rebalance",
            lambda context, trading_date, pending_exit_symbols=None: called.append(trading_date) or True,
        )

        strategy.on_after_trading(_InvariantContext(), date(2024, 2, 1))

        assert called == [date(2024, 2, 1)]

    def test_s5_03_empty_candidate_pool_does_not_refresh_rebalance_gate(self, monkeypatch):
        strategy = JoinquantValueRsrsTimingStrategy(symbols=["000001"], holding_days=20)
        monkeypatch.setattr(strategy, "_update_rsrs_state", lambda: True)

        strategy.on_after_trading(_InvariantContext(), date(2024, 1, 2))

        assert strategy._last_rebalance_date is None
        assert strategy._days_since_rebalance == 0

    def test_s5_04_candidate_filter_does_not_use_position_profit_stops(self):
        strategy = JoinquantValueRsrsTimingStrategy(symbols=["000001"], stop_loss_pct=0.10)
        strategy._positions["000001"] = 100
        strategy._entry_prices["000001"] = 10.0

        reason = strategy._candidate_rejection("000001", _value_rsrs_bar("000001", 8.9))

        assert reason == ""

        small_cap = XueqiuSmallCapFinancialFilterStrategy(
            symbols=["002475"],
            min_adv_value=0.0,
            stop_loss_pct=0.10,
            min_stop_loss_pct=0.0,
            max_stop_loss_pct=0.0,
            stop_volatility_multiplier=0.0,
        )
        small_cap._positions["002475"] = 100
        small_cap._entry_prices["002475"] = 10.0

        rejected = small_cap._entry_risk(
            "002475",
            _value_rsrs_bar("002475", 8.9, total_mv=120000.0, circ_mv=110000.0, pe_ttm=20.0, ps_ttm=8.0),
        )

        assert rejected is False

        large_cap = AShareAlpha158FactorCompositeStrategy(
            symbols=["002475", "000300"],
            min_turnover=0.0,
            stop_loss_pct=0.10,
            take_profit_pct=0.25,
            trailing_stop_pct=0.10,
        )
        large_cap._positions["002475"] = 100
        large_cap._entry_prices["002475"] = 10.0

        reason = large_cap._candidate_rejection("002475", _value_rsrs_bar("002475", 8.9))

        assert reason == ""

    def test_s5_05_zero_price_stock_dividend_fill_keeps_internal_cost_in_sync(self):
        strategy = JoinquantValueRsrsTimingStrategy(symbols=["000001"])
        strategy.on_fill(
            None,
            SimpleNamespace(symbol="000001", quantity=100, side="BUY", fill_price=10.0, price=10.0),
        )

        strategy.on_fill(
            None,
            SimpleNamespace(symbol="000001", quantity=10, side="BUY", fill_price=0.0, price=0.0),
        )

        assert strategy._positions["000001"] == 110
        assert strategy._entry_prices["000001"] == pytest.approx(1000.0 / 110.0)

        small_cap = XueqiuSmallCapFinancialFilterStrategy(symbols=["002475"])
        small_cap.on_fill(
            None,
            SimpleNamespace(symbol="002475", quantity=100, side="BUY", fill_price=10.0, price=10.0),
        )

        small_cap.on_fill(
            None,
            SimpleNamespace(symbol="002475", quantity=10, side="BUY", fill_price=0.0, price=0.0),
        )

        assert small_cap._positions["002475"] == 110
        assert small_cap._entry_prices["002475"] == pytest.approx(1000.0 / 110.0)

    def test_s5_06_dust_position_does_not_submit_zero_quantity_sell(self, monkeypatch):
        strategy = AShareAlpha158FactorCompositeStrategy(symbols=["002475", "000300"])
        strategy._positions["002475"] = 0.5
        monkeypatch.setattr(strategy, "_get_last_bar", lambda symbol: _value_rsrs_bar(symbol, 10.0))
        monkeypatch.setattr(strategy, "_position_exit_reason", lambda symbol, bar: "dust_exit")
        sells = []
        monkeypatch.setattr(
            strategy,
            "sell",
            lambda symbol, quantity, order_type="MARKET", price=None: sells.append((symbol, quantity, price)),
        )

        exited = strategy._exit_risk_positions()

        assert sells == []
        assert exited == set()
        assert strategy.get_guard_diagnostics()["exit_triggers"]["dust_position"] == 1

        small_cap = XueqiuSmallCapFinancialFilterStrategy(symbols=["002475"])
        small_cap._positions["002475"] = 0.5
        small_cap.on_data(None, _value_rsrs_bar("002475", 1.8, total_mv=100.0))
        monkeypatch.setattr(
            small_cap,
            "sell",
            lambda symbol, quantity, order_type="MARKET", price=None: sells.append((symbol, quantity, price)),
        )

        exited = small_cap._exit_risk_positions()

        assert exited == set()
        assert sells == []
        assert small_cap.get_guard_diagnostics()["exit_triggers"]["dust_position"] == 1

        mid_cap = AShareDavisDoubleClickStrategy(symbols=["002475"])
        mid_cap._positions["002475"] = 0.5
        monkeypatch.setattr(mid_cap, "_get_last_bar", lambda symbol: _value_rsrs_bar(symbol, 10.0))
        monkeypatch.setattr(mid_cap, "_position_exit_reason", lambda symbol, bar: "dust_exit")
        monkeypatch.setattr(
            mid_cap,
            "sell",
            lambda symbol, quantity, order_type="MARKET", price=None: sells.append((symbol, quantity, price)),
        )

        exited = mid_cap._exit_risk_positions()

        assert exited == set()
        assert sells == []
        assert mid_cap.get_guard_diagnostics()["exit_triggers"]["dust_position"] == 1


# ---------------------------------------------------------------------------
# CASE-8: Promoted strategy risk-exit toggle
# ---------------------------------------------------------------------------


class TestCase8PromotedStrategyRiskExitToggle:
    def test_s8_01_promoted_strategies_default_to_enabled_risk_exit_package(self):
        small_cap = XueqiuSmallCapFinancialFilterStrategy(symbols=["002475"])
        barbell = AShareGoldEquityBarbellTimingStrategy(
            risk_symbols=["510300"],
            defensive_symbols=["518880"],
            timing_symbol="510300",
        )

        assert small_cap.get_state()["parameters"]["risk_exit"]["enabled"] is True
        assert barbell.get_state()["parameters"]["risk_exit"]["enabled"] is True

    def test_s8_02_risk_exit_disabled_suppresses_pnl_exit_package(self):
        small_cap = XueqiuSmallCapFinancialFilterStrategy(
            symbols=["002475"],
            risk_exit={"enabled": False, "stop_loss_pct": 0.10},
            min_stop_loss_pct=0.0,
            max_stop_loss_pct=0.0,
            stop_volatility_multiplier=0.0,
        )
        small_cap._positions["002475"] = 100
        small_cap._entry_prices["002475"] = 10.0
        small_cap.on_data(None, _value_rsrs_bar("002475", 8.9, total_mv=120000.0, circ_mv=110000.0, pe_ttm=20.0, ps_ttm=8.0))

        assert small_cap._exit_risk_positions() == set()

        barbell = AShareGoldEquityBarbellTimingStrategy(
            risk_symbols=["510300"],
            defensive_symbols=["518880"],
            timing_symbol="510300",
            risk_exit={"enabled": False, "stop_loss_pct": 0.08},
        )
        barbell._positions["510300"] = 100
        barbell._entry_prices["510300"] = 10.0
        _feed_pit_barbell(barbell, "510300", [10.0, 9.1])

        assert barbell._exit_risk_positions() == set()


# ---------------------------------------------------------------------------
# CASE-9: Retail-permission stock universe
# ---------------------------------------------------------------------------


class TestCase9RetailPermissionStockUniverse:
    def test_s9_01_small_cap_strategy_excludes_special_permission_stock_boards_by_default(self):
        strategy = XueqiuSmallCapFinancialFilterStrategy(
            symbols=["000001", "002475", "300001", "301001", "688001", "689001"],
            risk_index_symbol="399001",
        )

        assert strategy.symbols == ["000001", "002475", "399001"]
        assert strategy.get_state()["parameters"]["excluded_board_prefixes"] == ["300", "301", "688", "689"]
        for symbol in ["300001", "301001", "688001", "689001"]:
            assert strategy._entry_risk(
                symbol,
                _value_rsrs_bar(symbol, 10.0, total_mv=120000.0, circ_mv=110000.0, pe_ttm=20.0, ps_ttm=8.0),
            )

    def test_s9_02_etf_strategy_keeps_chinext_etfs_under_its_own_universe_rules(self):
        strategy = AShareGoldEquityBarbellTimingStrategy(
            risk_symbols=["159915", "159949"],
            defensive_symbols=["518880"],
            timing_symbol="510300",
        )

        assert "159915" in strategy.symbols
        assert "159949" in strategy.symbols

    def test_s9_03_dynamic_stock_universe_exposes_only_static_snapshot_dependencies(self):
        strategy = XueqiuSmallCapFinancialFilterStrategy(symbols=["000001", "002475", "600099"])

        assert strategy.symbols == ["000001", "002475", "600099"]
        assert strategy.required_snapshot_symbols() == []

        strategy = AShareCsi1000StrictIndexEnhancedStrategy(
            symbols=["600001", "600002", "000852"],
            index_weights=[{"trade_date": "2026-05-31", "symbol": "600001", "weight": 100.0}],
        )

        assert strategy.required_snapshot_symbols() == []

        strategy = XueqiuSmallCapFinancialFilterStrategy(
            symbols=["000001", "002475", "600099"],
            risk_index_symbol="399001",
        )

        assert strategy.required_snapshot_symbols() == ["399001"]


# ---------------------------------------------------------------------------
# CASE-6: Top-level strategy promotion gate
# ---------------------------------------------------------------------------


class TestCase6TopLevelStrategyPromotionGate:
    def test_s6_01_top_level_strategies_pass_current_production_checklist(self):
        package_root = Path(__file__).resolve().parents[1]
        strategies_root = package_root / "features" / "strategies"
        reports_root = package_root / "infrastructure" / "var" / "research" / "reports"
        reject_root = strategies_root / "reject"

        assert reject_root.exists()
        violations = []
        for item in sorted(strategies_root.iterdir()):
            if not item.is_dir() or item.name.startswith("_") or item.name.startswith(".") or item.name == "reject":
                continue
            if not (item / "strategy.py").exists():
                continue
            checklist = _strict_report_checklist(reports_root, item.name)
            if not checklist["passed"]:
                violations.append((item.name, checklist["failures"]))

        assert violations == []

    def test_s6_02_top_level_strategies_bundle_full_research_report(self):
        package_root = Path(__file__).resolve().parents[1]
        strategies_root = package_root / "features" / "strategies"
        reports_root = package_root / "infrastructure" / "var" / "research" / "reports"

        violations = []
        for item in sorted(strategies_root.iterdir()):
            if not item.is_dir() or item.name.startswith("_") or item.name.startswith(".") or item.name == "reject":
                continue
            if not (item / "strategy.py").exists():
                continue
            bundled_report = item / "full_research_report.html"
            source_report = reports_root / item.name / "full_research_report.html"
            if not bundled_report.exists():
                violations.append((item.name, "missing bundled full_research_report.html"))
                continue
            if not source_report.exists():
                violations.append((item.name, "missing research full_research_report.html"))
                continue
            bundled_text = bundled_report.read_text(encoding="utf-8")
            source_text = source_report.read_text(encoding="utf-8")
            if bundled_text != source_text:
                violations.append((item.name, "bundled full_research_report.html is not synchronized with research report"))
                continue
            if item.name not in bundled_text or "End-to-End Research Report" not in bundled_text:
                violations.append((item.name, "bundled report is not the strategy full report"))

        assert violations == []

    def test_s6_03_archived_strategy_tests_do_not_import_missing_top_level_modules(self):
        package_root = Path(__file__).resolve().parents[1]
        strategies_root = package_root / "features" / "strategies"
        rejected_strategy_root = package_root / "features" / "rejected_strategy"
        tests_root = package_root / "tests"
        active_strategy_ids = {
            item.name
            for item in strategies_root.iterdir()
            if item.is_dir() and (item / "strategy.py").exists()
        }
        archived_strategy_ids = {
            item.name
            for item in rejected_strategy_root.iterdir()
            if item.is_dir() and (item / "strategy.py").exists()
        }
        direct_import = re.compile(
            r"from\s+quant\.features\.strategies\.([A-Za-z0-9_]+)\.strategy\s+import"
        )

        violations = []
        for test_file in sorted(tests_root.glob("test_*.py")):
            text = test_file.read_text(encoding="utf-8")
            for match in direct_import.finditer(text):
                strategy_id = match.group(1)
                if strategy_id in archived_strategy_ids and strategy_id not in active_strategy_ids:
                    violations.append(f"{test_file.name}:{strategy_id}")

        assert violations == []

    def test_s6_04_promoted_strategy_entrypoints_do_not_fallback_to_rejected_archive(self):
        package_root = Path(__file__).resolve().parents[1]
        strategies_root = package_root / "features" / "strategies"
        scan_roots = [package_root / "tests", package_root / "scripts"]
        active_strategy_ids = {
            item.name
            for item in strategies_root.iterdir()
            if item.is_dir() and (item / "strategy.py").exists()
        }
        rejected_import = re.compile(
            r"quant\.features\.rejected_strategy\.([A-Za-z0-9_]+)\.strategy"
        )

        violations = []
        invariant_test_file = Path(__file__).resolve()
        for root in scan_roots:
            for py_file in sorted(root.rglob("*.py")):
                if py_file.resolve() == invariant_test_file:
                    continue
                text = py_file.read_text(encoding="utf-8")
                for match in rejected_import.finditer(text):
                    strategy_id = match.group(1)
                    if strategy_id in active_strategy_ids:
                        violations.append(f"{py_file.relative_to(package_root)}:{strategy_id}")

        assert violations == []

    def test_s6_05_archive_and_reject_imports_do_not_mutate_active_registry(self):
        active_gold_cls = StrategyRegistry.get("ashare_gold_equity_barbell_timing")
        names_before = set(StrategyRegistry.list_strategies())

        importlib.import_module("quant.features.rejected_strategy.ashare_gold_equity_barbell_timing.strategy")
        importlib.import_module("quant.features.rejected_strategy.joinquant_wufu_daily_etf_lof.strategy")
        importlib.import_module("quant.features.strategies.reject.joinquant_value_rsrs_timing.strategy")

        assert StrategyRegistry.get("ashare_gold_equity_barbell_timing") is active_gold_cls
        assert StrategyRegistry.get("joinquant_wufu_daily_etf_lof") is None
        assert StrategyRegistry.get("joinquant_value_rsrs_timing") is None
        assert set(StrategyRegistry.list_strategies()) == names_before


# ---------------------------------------------------------------------------
# CASE-7: Audited ETF registry universe
# ---------------------------------------------------------------------------


class TestCase7AuditedEtfRegistryUniverse:
    def test_s7_01_visible_candidates_are_ranked_by_signal_not_preselected_by_size(self):
        strategy = AShareGoldEquityBarbellTimingStrategy(
            risk_category_symbols={"csi300": ["510300", "159919"]},
            defensive_category_symbols={"gold": ["518880"]},
            timing_symbol="000300",
            momentum_lookback=6,
            momentum_skip=1,
            trend_window=5,
            volatility_window=5,
            liquidity_window=3,
            min_avg_turnover=1000.0,
            target_exposure=0.98,
            risk_leg_weight=0.50,
            holding_days=1,
            require_pit_size=True,
        )
        context = _InvariantContext()
        strategy.on_start(context)

        _feed_pit_barbell(strategy, "000300", [10.0, 10.2, 10.4, 10.5, 10.7, 10.9, 11.1, 11.3])
        _feed_pit_barbell(strategy, "510300", [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.1, 10.2], total_netasset=100_000_000_000)
        _feed_pit_barbell(strategy, "159919", [10.0, 10.2, 10.4, 10.6, 10.8, 11.0, 11.2, 11.4], total_netasset=50_000_000_000)
        _feed_pit_barbell(strategy, "518880", [5.0, 5.05, 5.1, 5.15, 5.2, 5.25, 5.3, 5.35], total_netasset=70_000_000_000)

        strategy.on_after_trading(context, date(2026, 5, 20))

        ordered = {order["symbol"] for order in context.orders}
        assert "159919" in ordered
        assert "510300" not in ordered

    def test_s7_02_future_unlisted_symbol_is_not_visible_before_current_bar_exists(self):
        strategy = AShareGoldEquityBarbellTimingStrategy(
            risk_category_symbols={"csi300": ["510300", "515300"]},
            defensive_category_symbols={"gold": ["518880"]},
            timing_symbol="000300",
            momentum_lookback=6,
            momentum_skip=1,
            trend_window=5,
            volatility_window=5,
            liquidity_window=3,
            min_avg_turnover=1000.0,
            target_exposure=0.98,
            risk_leg_weight=0.50,
            holding_days=1,
            require_pit_size=True,
        )
        context = _InvariantContext()
        strategy.on_start(context)

        _feed_pit_barbell(strategy, "000300", [10.0, 10.2, 10.4, 10.5, 10.7, 10.9, 11.1, 11.3])
        _feed_pit_barbell(strategy, "510300", [10.0, 10.1, 10.2, 10.4, 10.6, 10.8, 11.0, 11.2], total_netasset=100_000_000_000)
        _feed_pit_barbell(
            strategy,
            "515300",
            [10.0, 10.4, 10.8, 11.2, 11.6, 12.0, 12.4, 12.8],
            last_date=date(2026, 5, 19),
            total_netasset=200_000_000_000,
        )
        _feed_pit_barbell(strategy, "518880", [5.0, 5.05, 5.1, 5.15, 5.2, 5.25, 5.3, 5.35], total_netasset=70_000_000_000)

        strategy.on_after_trading(context, date(2026, 5, 20))

        ordered = {order["symbol"] for order in context.orders}
        assert "510300" in ordered
        assert "515300" not in ordered

    def test_s7_03_registered_etf_categories_are_user_approved(self):
        from quant.infrastructure.research.cn_etf_universe import registered_etf_categories

        registry = registered_etf_categories()
        assert registry
        violations = []
        for category, entries in registry.items():
            for entry in entries:
                if entry.get("audit_status") != "user_approved":
                    violations.append((category, entry.get("symbol"), entry.get("audit_status")))

        assert violations == []


class _InvariantPortfolio:
    nav = 100000.0


class _InvariantContext:
    def __init__(self):
        self.portfolio = _InvariantPortfolio()
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


def _feed_pit_barbell(
    strategy,
    symbol,
    closes,
    *,
    last_date=date(2026, 5, 20),
    turnover=50000000.0,
    volume=100000,
    total_netasset=None,
):
    first_date = last_date - timedelta(days=len(closes) - 1)
    for index, close in enumerate(closes):
        strategy.on_data(
            None,
            {
                "symbol": symbol,
                "timestamp": first_date + timedelta(days=index),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "adj_close": close,
                "adj_factor": 1.0,
                "volume": volume,
                "turnover": turnover,
                "total_netasset": total_netasset,
            },
        )


def _strict_report_cagr(reports_root: Path, strategy_id: str) -> float | None:
    result_path = reports_root / strategy_id / "last_result.json"
    if result_path.exists():
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        value = _payload_cagr(payload)
        if value is not None:
            return value

    grid_path = reports_root / strategy_id / "grid_result.json"
    if grid_path.exists():
        payload = json.loads(grid_path.read_text(encoding="utf-8"))
        best = payload.get("best") or {}
        value = best.get("cagr")
        if value is not None:
            return float(value)

    html_cagr = _strict_report_html_cagr(reports_root / strategy_id / "strict_backtest_report.html")
    if html_cagr is not None:
        return html_cagr

    for batch_path in reports_root.glob("*/batch_result.json"):
        payload = json.loads(batch_path.read_text(encoding="utf-8"))
        for row in payload.get("rows", []):
            if row.get("strategy_id") == strategy_id and row.get("cagr") is not None:
                return float(row["cagr"])
    return None


def _strict_report_checklist(reports_root: Path, strategy_id: str) -> dict:
    evidence = _strict_report_evidence(reports_root, strategy_id)
    cagr = _optional_float(evidence.get("cagr"))
    max_drawdown = _optional_float(evidence.get("max_drawdown_pct"))
    total_trades = _optional_int(evidence.get("total_trades"))
    max_adv = _optional_float(evidence.get("max_adv_participation"))
    failures = []
    if cagr is None:
        failures.append("cagr=missing")
    elif cagr < 0.05:
        failures.append(f"cagr={cagr:.4f} < 0.05")
    if max_drawdown is None:
        failures.append("max_drawdown_pct=missing")
    elif cagr is not None and cagr >= 0.05 and abs(max_drawdown) > _drawdown_limit_for_cagr(cagr):
        failures.append(f"max_drawdown_pct={max_drawdown:.4f} exceeds tier")
    if total_trades is None:
        failures.append("total_trades=missing")
    elif total_trades <= 50:
        failures.append(f"total_trades={total_trades} <= 50")
    if max_adv is None:
        failures.append("max_adv_participation=missing")
    elif max_adv > 0.05:
        failures.append(f"max_adv_participation={max_adv:.4f} > 0.05")
    return {"passed": not failures, "failures": failures, "evidence": evidence}


def _strict_report_evidence(reports_root: Path, strategy_id: str) -> dict:
    grid_path = reports_root / strategy_id / "grid_result.json"
    if grid_path.exists():
        payload = json.loads(grid_path.read_text(encoding="utf-8"))
        best = payload.get("best") or {}
        strict_reports = payload.get("strict_reports") or {}
        strict = strict_reports.get(str(best.get("scenario"))) or {}
        metrics = strict.get("metrics") or {}
        capacity = strict.get("capacity") or {}
        return {
            "cagr": _first_present(best, metrics, "cagr"),
            "max_drawdown_pct": _first_present(best, metrics, "max_drawdown_pct"),
            "total_trades": _first_present(best, metrics, "total_trades"),
            "max_adv_participation": _first_present(best, capacity, "max_adv_participation"),
        }

    result_path = reports_root / strategy_id / "last_result.json"
    if result_path.exists():
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        metrics = payload.get("metrics") or {}
        capacity = payload.get("capacity") or {}
        return {
            "cagr": metrics.get("cagr"),
            "max_drawdown_pct": metrics.get("max_drawdown_pct"),
            "total_trades": metrics.get("total_trades"),
            "max_adv_participation": capacity.get("max_adv_participation"),
        }
    return {}


def _first_present(primary: dict, secondary: dict, key: str):
    value = primary.get(key)
    if value is not None:
        return value
    return secondary.get(key)


def _optional_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _drawdown_limit_for_cagr(cagr: float) -> float:
    if cagr < 0.10:
        return 0.15
    if cagr < 0.15:
        return 0.25
    if cagr < 0.20:
        return 0.30
    return 0.50


def _payload_cagr(payload: dict) -> float | None:
    metrics = payload.get("metrics") or {}
    value = metrics.get("cagr")
    if value is not None:
        return float(value)
    for entry in payload.get("log", []):
        scores = entry.get("scores") or {}
        value = scores.get("cagr")
        if value is not None:
            return float(value)
    return None


def _strict_report_html_cagr(report_path: Path) -> float | None:
    if not report_path.exists():
        return None
    text = report_path.read_text(encoding="utf-8")
    match = re.search(r"CAGR\s*[=:]\s*([+-]?\d+(?:\.\d+)?)(\s*%)?", text)
    if match:
        return _normalize_cagr(float(match.group(1)), bool(match.group(2)))
    match = re.search(r"<td>\s*CAGR\s*</td>\s*<td>\s*([+-]?\d+(?:\.\d+)?)(\s*%)?\s*</td>", text)
    if match:
        return _normalize_cagr(float(match.group(1)), bool(match.group(2)))
    return None


def _normalize_cagr(value: float, explicit_percent: bool) -> float:
    if explicit_percent or abs(value) > 1:
        return value / 100.0
    return value
