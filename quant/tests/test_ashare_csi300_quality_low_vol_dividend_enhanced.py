from datetime import date

import quant.scripts.run_ashare_csi300_quality_low_vol_dividend_full_research as report_runner
from quant.features.strategies.reject.ashare_csi300_quality_low_vol_dividend_enhanced.strategy import (
    AShareCsi300QualityLowVolDividendEnhancedStrategy,
    STRATEGY_NAME,
)
from quant.features.strategies.registry import StrategyRegistry


class _Portfolio:
    nav = 1_000_000.0


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


def test_csi300_quality_low_vol_dividend_keeps_metadata_outside_active_registry():
    assert AShareCsi300QualityLowVolDividendEnhancedStrategy._registry_name == STRATEGY_NAME
    assert AShareCsi300QualityLowVolDividendEnhancedStrategy._registry_active is False
    assert not StrategyRegistry.is_registered(STRATEGY_NAME)


def test_csi300_quality_low_vol_dividend_filters_permission_boards():
    strategy = AShareCsi300QualityLowVolDividendEnhancedStrategy(symbols=["300001", "688001", "600001", "000300"])

    assert strategy.trade_symbols == ["600001"]
    assert strategy.symbols == ["600001", "000300"]
    assert strategy.get_state()["parameters"]["excluded_board_prefixes"] == ["300", "301", "688", "689"]
    assert strategy.get_state()["parameters"]["max_positions"] == 40
    assert strategy.get_state()["parameters"]["score_profile"] == "csi300_quality_low_vol_dividend_index_enhanced_v2"


def test_csi300_quality_low_vol_dividend_uses_soft_factor_score_profile():
    strategy = AShareCsi300QualityLowVolDividendEnhancedStrategy(symbols=["600001", "000300"])

    assert strategy.score_specs == [
        ("momentum", 0.22, True),
        ("recent_momentum", 0.12, True),
        ("roe", 0.16, True),
        ("volatility", 0.14, False),
        ("pb", 0.12, False),
        ("pe_ttm", 0.10, False),
        ("turnover_rate", 0.08, False),
        ("dv_ttm", 0.06, True),
    ]


def test_csi300_quality_low_vol_dividend_prefers_quality_dividend_low_vol_name():
    strategy = AShareCsi300QualityLowVolDividendEnhancedStrategy(
        symbols=["600001", "600002", "600003", "000300"],
        max_positions=1,
        target_weight_slots=1,
        cap_percentile_low=0.0,
        cap_percentile_high=1.0,
        min_turnover=0.0,
        min_long_momentum=-1.0,
        min_recent_momentum=-1.0,
        max_volatility=0.0,
        use_market_timing=False,
    )
    context = _Context()
    strategy.on_start(context)

    for index in range(260):
        close = 10.0 + index * 0.01
        base = {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "adj_close": close,
            "volume": 1000000,
            "turnover": 1000000,
            "turnover_rate_f": 2.0,
            "pe_ttm": 12.0,
            "pb": 1.2,
            "ps_ttm": 1.5,
            "dv_ttm": 2.0,
            "total_mv": 1000000.0,
            "circ_mv": 800000.0,
            "tradable": True,
            "has_daily_bar": True,
            "is_st": False,
            "is_listed": True,
            "list_status": "L",
        }
        strategy.on_data(None, {"symbol": "600001", **base, "dv_ttm": 4.0, "roe": 18.0, "grossprofit_margin": 40.0, "debt_to_assets": 25.0})
        strategy.on_data(None, {"symbol": "600002", **base, "dv_ttm": 1.2, "roe": 7.0, "grossprofit_margin": 20.0, "debt_to_assets": 70.0})
        strategy.on_data(None, {"symbol": "600003", **base, "dv_ttm": 0.5, "roe": 20.0, "grossprofit_margin": 45.0, "debt_to_assets": 20.0})
        strategy.on_data(None, {"symbol": "000300", **base})

    strategy.on_after_trading(context, date(2026, 6, 1))

    buys = [order for order in context.orders if order["side"] == "BUY"]
    assert [order["symbol"] for order in buys] == ["600001"]
    assert "low_dividend_yield" not in strategy.get_guard_diagnostics()["entry_rejections"]


def test_csi300_quality_low_vol_dividend_risk_exit_can_be_disabled():
    enabled = AShareCsi300QualityLowVolDividendEnhancedStrategy(symbols=["600001", "000300"])
    disabled = AShareCsi300QualityLowVolDividendEnhancedStrategy(
        symbols=["600001", "000300"],
        risk_exit={"enabled": False},
    )

    assert enabled.get_state()["parameters"]["risk_exit"]["enabled"] is True
    assert enabled.stop_loss_pct == 0.20
    assert enabled.take_profit_pct == 0.55
    assert enabled.trailing_stop_pct == 0.16
    assert disabled.get_state()["parameters"]["risk_exit"]["enabled"] is False
    assert disabled.stop_loss_pct == 0.0
    assert disabled.take_profit_pct == 0.0
    assert disabled.trailing_stop_pct == 0.0


def test_csi300_quality_low_vol_dividend_report_uses_low_satellite_allocation():
    params = report_runner._report_parameters()

    assert params["portfolio_construction"] == "csi300_core_plus_small_cap_quality_satellite"
    assert params["allocations"][STRATEGY_NAME] == 0.97
    assert params["allocations"]["xueqiu_small_cap_financial_filter"] == 0.03
    assert params["target_excess_cagr"] == 0.02
    assert params["satellite_strategy"]["excluded_board_prefixes"] == ["300", "301", "688", "689"]
    assert params["execution_cost_model"]["max_participation_rate"] == 0.01


def test_csi300_quality_low_vol_dividend_hybrid_guard_reports_both_sleeves():
    core = AShareCsi300QualityLowVolDividendEnhancedStrategy(symbols=["600001", "000300"])
    satellite = report_runner.XueqiuSmallCapFinancialFilterStrategy(
        symbols=["600001", "399001"],
        **report_runner.SATELLITE_STRATEGY_PARAMS,
    )
    diagnostics = report_runner._HybridGuardDiagnostics(core, satellite)

    guard = diagnostics.get_guard_diagnostics()

    assert diagnostics.max_positions == 43
    assert guard["parameters"]["allocations"][STRATEGY_NAME] == 0.97
    assert guard["parameters"]["allocations"]["xueqiu_small_cap_financial_filter"] == 0.03
    assert guard["core"]["parameters"]["excluded_board_prefixes"] == ["300", "301", "688", "689"]
    assert guard["satellite"]["parameters"]["risk_index_symbol"] == "399001"


def test_csi300_quality_low_vol_dividend_target_excess_uses_benchmark_cagr():
    strict_report = {
        "metrics": {"cagr": 0.055, "total_trades": 60},
        "benchmark": {"benchmark_cagr": 0.029},
        "capacity": {"max_adv_participation": 0.01},
    }

    assert abs(report_runner._excess_cagr(strict_report) - 0.026) < 1e-12
    assert report_runner._target_excess_met(strict_report)
