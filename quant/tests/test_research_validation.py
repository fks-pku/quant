import numpy as np
import pandas as pd
import pytest

from quant.features.research.models import (
    EvaluationReport,
    RawStrategy,
    StrategySpec,
    ValidationReport,
)


def _raw(title="Test Strategy", metadata=None) -> RawStrategy:
    return RawStrategy(
        title=title,
        description="desc",
        source="test",
        source_url="https://example.test",
        metadata=metadata or {},
    )


def _report(strategy_type="momentum", symbols=None) -> EvaluationReport:
    return EvaluationReport(
        suitability_score=7.5,
        complexity_score=3.0,
        data_requirement="low",
        daily_adaptable=True,
        estimated_edge=0.08,
        recommended_symbols=symbols or ["SPY"],
        strategy_type=strategy_type,
        summary="test",
    )


class TestStrategySpecBuilder:
    def test_momentum_maps_to_close_return(self):
        from quant.features.research.validation.strategy_spec_builder import (
            StrategySpecBuilder,
        )

        builder = StrategySpecBuilder()
        spec = builder.build(_raw("Momentum Test"), _report("momentum"))
        assert spec.signal_formula_key == "momentum_close_return"
        assert spec.status == "ready"

    def test_strategy_id_is_filesystem_and_registry_safe(self):
        from quant.features.research.validation.strategy_spec_builder import (
            StrategySpecBuilder,
        )

        builder = StrategySpecBuilder()
        spec = builder.build(_raw("Momentum: Test-v2!"), _report("momentum"))
        assert spec.strategy_id == "momentum_test_v2"

    def test_strategy_id_truncation_does_not_leave_trailing_separator(self):
        from quant.features.research.validation.strategy_spec_builder import (
            StrategySpecBuilder,
        )

        builder = StrategySpecBuilder()
        spec = builder.build(
            _raw("Discovery of a 13-Sharpe OOS Factor: Drift Regimes Unlock Hidden Cross-Sectional Predictability"),
            _report("mean_reversion"),
        )
        assert spec.strategy_id == "discovery_of_a_13_sharpe_oos_factor_drift_regimes"

    def test_mean_reversion_maps_to_close_to_ma(self):
        from quant.features.research.validation.strategy_spec_builder import (
            StrategySpecBuilder,
        )

        builder = StrategySpecBuilder()
        spec = builder.build(_raw("MR Test"), _report("mean_reversion"))
        assert spec.signal_formula_key == "mean_reversion_close_to_ma"
        assert spec.status == "ready"

    def test_breakout_maps_to_atr(self):
        from quant.features.research.validation.strategy_spec_builder import (
            StrategySpecBuilder,
        )

        builder = StrategySpecBuilder()
        spec = builder.build(_raw("Breakout Test"), _report("breakout"))
        assert spec.signal_formula_key == "volatility_breakout_atr"
        assert spec.status == "ready"

    def test_worldquant_alpha_001_maps_to_exact_formula(self):
        from quant.features.research.discovery.worldquant101 import build_worldquant101_raw_strategies
        from quant.features.research.validation.strategy_spec_builder import (
            StrategySpecBuilder,
        )

        raw = build_worldquant101_raw_strategies(alpha_numbers=[1])[0]
        builder = StrategySpecBuilder()
        spec = builder.build(raw, _report("momentum", symbols=["600519"]))

        assert spec.strategy_id == "worldquant_101_alpha_001"
        assert spec.strategy_type == "worldquant_factor"
        assert spec.signal_formula_key == "worldquant_alpha_001"
        assert spec.required_fields == ["close"]
        assert spec.status == "ready"

    def test_worldquant_alpha_002_maps_to_exact_formula(self):
        from quant.features.research.discovery.worldquant101 import build_worldquant101_raw_strategies
        from quant.features.research.validation.strategy_spec_builder import (
            StrategySpecBuilder,
        )

        raw = build_worldquant101_raw_strategies(alpha_numbers=[2])[0]
        builder = StrategySpecBuilder()
        spec = builder.build(raw, _report("momentum", symbols=["600519"]))

        assert spec.strategy_id == "worldquant_101_alpha_002"
        assert spec.strategy_type == "worldquant_factor"
        assert spec.signal_formula_key == "worldquant_alpha_002"
        assert spec.required_fields == ["volume", "open", "close"]
        assert spec.lookback_days == 6
        assert spec.status == "ready"

    def test_worldquant_alpha_003_maps_to_exact_formula(self):
        from quant.features.research.discovery.worldquant101 import build_worldquant101_raw_strategies
        from quant.features.research.validation.strategy_spec_builder import (
            StrategySpecBuilder,
        )

        raw = build_worldquant101_raw_strategies(alpha_numbers=[3])[0]
        builder = StrategySpecBuilder()
        spec = builder.build(raw, _report("momentum", symbols=["600519"]))

        assert spec.strategy_id == "worldquant_101_alpha_003"
        assert spec.strategy_type == "worldquant_factor"
        assert spec.signal_formula_key == "worldquant_alpha_003"
        assert spec.required_fields == ["open", "volume"]
        assert spec.lookback_days == 10
        assert spec.status == "ready"

    def test_worldquant_alpha_004_maps_to_exact_formula(self):
        from quant.features.research.discovery.worldquant101 import build_worldquant101_raw_strategies
        from quant.features.research.validation.strategy_spec_builder import (
            StrategySpecBuilder,
        )

        raw = build_worldquant101_raw_strategies(alpha_numbers=[4])[0]
        builder = StrategySpecBuilder()
        spec = builder.build(raw, _report("momentum", symbols=["600519"]))

        assert spec.strategy_id == "worldquant_101_alpha_004"
        assert spec.strategy_type == "worldquant_factor"
        assert spec.signal_formula_key == "worldquant_alpha_004"
        assert spec.required_fields == ["low"]
        assert spec.lookback_days == 9
        assert spec.status == "ready"

    def test_worldquant_alpha_006_maps_to_exact_formula(self):
        from quant.features.research.discovery.worldquant101 import build_worldquant101_raw_strategies
        from quant.features.research.validation.strategy_spec_builder import (
            StrategySpecBuilder,
        )

        raw = build_worldquant101_raw_strategies(alpha_numbers=[6])[0]
        builder = StrategySpecBuilder()
        spec = builder.build(raw, _report("momentum", symbols=["600519"]))

        assert spec.strategy_id == "worldquant_101_alpha_006"
        assert spec.strategy_type == "worldquant_factor"
        assert spec.signal_formula_key == "worldquant_alpha_006"
        assert spec.required_fields == ["open", "volume"]
        assert spec.lookback_days == 10
        assert spec.status == "ready"

    def test_worldquant_alpha_010_maps_to_exact_formula(self):
        from quant.features.research.discovery.worldquant101 import build_worldquant101_raw_strategies
        from quant.features.research.validation.strategy_spec_builder import (
            StrategySpecBuilder,
        )

        raw = build_worldquant101_raw_strategies(alpha_numbers=[10])[0]
        builder = StrategySpecBuilder()
        spec = builder.build(raw, _report("momentum", symbols=["600519"]))

        assert spec.strategy_id == "worldquant_101_alpha_010"
        assert spec.strategy_type == "worldquant_factor"
        assert spec.signal_formula_key == "worldquant_alpha_010"
        assert spec.required_fields == ["close"]
        assert spec.lookback_days == 4
        assert spec.status == "ready"

    def test_worldquant_alpha_without_local_formula_does_not_fallback(self):
        from quant.features.research.discovery.worldquant101 import build_worldquant101_raw_strategies
        from quant.features.research.validation.strategy_spec_builder import (
            StrategySpecBuilder,
        )

        raw = build_worldquant101_raw_strategies(alpha_numbers=[5])[0]
        builder = StrategySpecBuilder()
        spec = builder.build(raw, _report("momentum", symbols=["600519"]))

        assert spec.strategy_id == "worldquant_101_alpha_005"
        assert spec.strategy_type == "worldquant_factor"
        assert spec.signal_formula_key == ""
        assert spec.status == "missing_formula"
        assert "worldquant_alpha_005" in spec.reason

    def test_a_share_structural_metadata_formula_maps_to_ready_spec(self):
        from quant.features.research.discovery.ashare_structural import (
            build_ashare_structural_raw_strategies,
        )
        from quant.features.research.validation.strategy_spec_builder import (
            StrategySpecBuilder,
        )

        raw = build_ashare_structural_raw_strategies(["ashare_short_reversal_5d"])[0]
        builder = StrategySpecBuilder()
        spec = builder.build(raw, _report("factor", symbols=["600519"]))

        assert spec.strategy_id == "a_share_short_term_reversal_5d"
        assert spec.strategy_type == "mean_reversion"
        assert spec.signal_formula_key == "ashare_short_reversal_5d"
        assert spec.required_fields == ["close"]
        assert spec.lookback_days == 5
        assert spec.horizon_days == 5
        assert spec.universe == ["600519"]
        assert spec.status == "ready"

    def test_a_share_structural_extended_formula_maps_to_ready_spec(self):
        from quant.features.research.discovery.ashare_structural import (
            build_ashare_structural_raw_strategies,
        )
        from quant.features.research.validation.strategy_spec_builder import (
            StrategySpecBuilder,
        )

        raw = build_ashare_structural_raw_strategies(["ashare_liquidity_weighted_low_volatility"])[0]
        builder = StrategySpecBuilder()
        spec = builder.build(raw, _report("factor", symbols=["600519"]))

        assert spec.strategy_type == "factor"
        assert spec.signal_formula_key == "ashare_liquidity_weighted_low_volatility"
        assert spec.required_fields == ["close", "turnover"]
        assert spec.lookback_days == 20
        assert spec.horizon_days == 10
        assert spec.status == "ready"

    def test_joinquant_small_cap_formula_maps_to_ready_spec(self):
        from quant.features.research.validation.strategy_spec_builder import (
            StrategySpecBuilder,
        )

        raw = _raw(
            "JoinQuant Small Cap MA Stop",
            metadata={"formula_key": "joinquant_small_cap_ma_stop"},
        )
        builder = StrategySpecBuilder()
        spec = builder.build(raw, _report("factor", symbols=["600519"]))

        assert spec.strategy_type == "factor"
        assert spec.signal_formula_key == "joinquant_small_cap_size_factor"
        assert spec.required_fields == ["close", "market_cap"]
        assert spec.lookback_days == 50
        assert spec.horizon_days == 5
        assert spec.status == "ready"

    def test_unknown_type_returns_unsupported(self):
        from quant.features.research.validation.strategy_spec_builder import (
            StrategySpecBuilder,
        )

        builder = StrategySpecBuilder()
        spec = builder.build(_raw("Unknown"), _report("stat_arb"))
        assert spec.status == "unsupported_type"

    def test_missing_formula_returns_missing_formula(self):
        from quant.features.research.validation.strategy_spec_builder import (
            StrategySpecBuilder,
        )

        builder = StrategySpecBuilder(config={"formulas": {}})
        spec = builder.build(_raw("Momentum"), _report("momentum"))
        assert spec.status == "missing_formula"


class TestFDR:
    def test_benjamini_hochberg_deterministic(self):
        from quant.features.research.validation.fdr import benjamini_hochberg

        p_values = [0.001, 0.02, 0.03, 0.20]
        result = benjamini_hochberg(p_values, alpha=0.05)
        assert result[0] == True
        assert result[1] == True
        assert result[2] == True
        assert result[3] == False

    def test_empty_p_values(self):
        from quant.features.research.validation.fdr import benjamini_hochberg

        assert benjamini_hochberg([], alpha=0.05) == []


class TestCrossSectionalValidation:
    def test_detect_market_patterns(self):
        from quant.features.research.validation.cross_sectional import detect_market

        assert detect_market("600519") == "cn"
        assert detect_market("00700") == "hk"
        assert detect_market("12345") == "hk"
        assert detect_market("HK.00700") == "hk"
        assert detect_market("AAPL") == "us"

    def test_cross_sectional_ic_uses_full_universe(self):
        from quant.features.research.validation.cross_sectional import compute_cross_sectional_ic

        dates = pd.date_range("2022-01-01", periods=120, freq="B")
        symbols = [f"S{i:02d}" for i in range(30)]
        rng = np.random.default_rng(7)
        signals = pd.DataFrame(rng.normal(size=(120, 30)), index=dates, columns=symbols)
        forward_returns = signals.copy()

        daily_ic = compute_cross_sectional_ic(signals, forward_returns)

        assert daily_ic.mean() > 0.99

    def test_icir_is_mean_over_std(self):
        from quant.features.research.validation.cross_sectional import compute_icir

        daily_ic = pd.Series([0.01, 0.02, 0.03, 0.04])

        assert compute_icir(daily_ic) == pytest.approx(daily_ic.mean() / daily_ic.std())

    def test_ic_decay_returns_four_horizons(self):
        from quant.features.research.validation.cross_sectional import compute_ic_decay

        dates = pd.date_range("2022-01-01", periods=120, freq="B")
        symbols = [f"S{i:02d}" for i in range(30)]
        base = np.arange(120, dtype=float).reshape(-1, 1)
        slopes = np.linspace(0.01, 0.03, 30).reshape(1, -1)
        prices = pd.DataFrame(100.0 + base * slopes, index=dates, columns=symbols)
        signals = prices.pct_change(20)

        decay = compute_ic_decay(signals, prices, horizons=[1, 5, 10, 21])

        assert [horizon for horizon, _ in decay] == [1, 5, 10, 21]
        assert all(isinstance(ic, float) for _, ic in decay)

    def test_fama_macbeth_tstat_positive_for_linear_relation(self):
        from quant.features.research.validation.cross_sectional import compute_fama_macbeth_tstat

        dates = pd.date_range("2022-01-01", periods=120, freq="B")
        symbols = [f"S{i:02d}" for i in range(30)]
        rng = np.random.default_rng(11)
        signals = pd.DataFrame(rng.normal(size=(120, 30)), index=dates, columns=symbols)
        forward_returns = 0.02 * signals + 0.001 * pd.DataFrame(
            rng.normal(size=(120, 30)),
            index=dates,
            columns=symbols,
        )

        assert compute_fama_macbeth_tstat(signals, forward_returns) > 0


class TestResearchAdjustedPrices:
    def test_signal_library_prefers_adjusted_close_for_momentum(self):
        from quant.features.research.validation.signal_library import compute_signal

        frame = pd.DataFrame(
            {
                "close": [100.0, 50.0, 25.0],
                "adj_close": [100.0, 100.0, 100.0],
            }
        )

        signal = compute_signal("momentum_close_return", frame, lookback=1)

        assert signal.dropna().abs().max() == pytest.approx(0.0)

    def test_signal_library_builds_adjusted_close_from_adj_factor(self):
        from quant.features.research.validation.signal_library import compute_signal

        frame = pd.DataFrame(
            {
                "close": [100.0, 50.0, 25.0],
                "adj_factor": [1.0, 2.0, 4.0],
            }
        )

        signal = compute_signal("momentum_close_return", frame, lookback=1)

        assert signal.dropna().abs().max() == pytest.approx(0.0)

    def test_panel_momentum_signal_matches_adjusted_price_matrix(self):
        from quant.features.research.validation.signal_library import compute_signal

        dates = pd.date_range("2022-01-03", periods=3, freq="B")
        frame = pd.DataFrame(
            {
                "date": list(dates) * 2,
                "symbol": ["600001"] * 3 + ["600002"] * 3,
                "close": [100.0, 50.0, 25.0, 10.0, 11.0, 12.1],
                "adj_close": [100.0, 100.0, 100.0, 10.0, 11.0, 12.1],
            }
        )

        signal = compute_signal("momentum_close_return", frame, lookback=1)

        assert signal.loc[dates[1], "600001"] == pytest.approx(0.0)
        assert signal.loc[dates[2], "600002"] == pytest.approx(0.1)

    def test_mean_reversion_signal_is_positive_when_adjusted_close_is_below_ma(self):
        from quant.features.research.validation.signal_library import compute_signal

        frame = pd.DataFrame(
            {
                "close": [100.0, 100.0, 50.0],
                "adj_close": [100.0, 100.0, 90.0],
            }
        )

        signal = compute_signal("mean_reversion_close_to_ma", frame, lookback=2)

        assert signal.iloc[-1] > 0

    def test_breakout_signal_uses_adjusted_breakout_orientation(self):
        from quant.features.research.validation.signal_library import compute_signal

        frame = pd.DataFrame(
            {
                "high": [100.0, 100.0, 100.0, 100.0],
                "low": [90.0, 90.0, 90.0, 90.0],
                "close": [95.0, 95.0, 95.0, 90.0],
                "adj_high": [10.0, 10.0, 10.0, 12.0],
                "adj_low": [9.0, 9.0, 9.0, 11.0],
                "adj_close": [9.5, 9.5, 9.5, 12.0],
            }
        )

        signal = compute_signal("volatility_breakout_atr", frame, lookback=2)

        assert signal.iloc[-1] > 0

    def test_a_share_short_reversal_is_positive_for_recent_loser(self):
        from quant.features.research.validation.signal_library import compute_signal

        frame = pd.DataFrame({"close": [100.0, 96.0, 90.0], "adj_close": [100.0, 96.0, 90.0]})

        signal = compute_signal("ashare_short_reversal_5d", frame, lookback=2)

        assert signal.iloc[-1] == pytest.approx(0.10)

    def test_a_share_volume_exhaustion_requires_down_move_and_high_volume(self):
        from quant.features.research.validation.signal_library import compute_signal

        close = [100.0] * 15 + [98.0, 96.0, 94.0, 92.0, 90.0]
        volume = [100.0] * 19 + [500.0]
        frame = pd.DataFrame({"close": close, "adj_close": close, "volume": volume})

        signal = compute_signal("ashare_volume_exhaustion_reversal", frame, lookback=10)

        assert signal.iloc[-1] > 0

    def test_a_share_lottery_avoidance_prefers_lower_volatility_panel(self):
        from quant.features.research.validation.signal_library import compute_signal

        dates = pd.date_range("2022-01-03", periods=8, freq="B")
        frame = pd.DataFrame(
            {
                "date": list(dates) * 2,
                "symbol": ["600001"] * 8 + ["600002"] * 8,
                "close": [100.0, 100.2, 100.1, 100.3, 100.2, 100.4, 100.3, 100.5, 100.0, 106.0, 94.0, 110.0, 90.0, 112.0, 88.0, 115.0],
                "adj_close": [100.0, 100.2, 100.1, 100.3, 100.2, 100.4, 100.3, 100.5, 100.0, 106.0, 94.0, 110.0, 90.0, 112.0, 88.0, 115.0],
            }
        )

        signal = compute_signal("ashare_lottery_demand_avoidance", frame, lookback=5)

        assert signal.loc[dates[-1], "600001"] > signal.loc[dates[-1], "600002"]

    def test_a_share_gap_down_reversal_is_positive_for_negative_gap(self):
        from quant.features.research.validation.signal_library import compute_signal

        frame = pd.DataFrame(
            {
                "open": [100.0, 95.0],
                "close": [100.0, 96.0],
                "adj_open": [100.0, 95.0],
                "adj_close": [100.0, 96.0],
            }
        )

        signal = compute_signal("ashare_gap_down_reversal", frame, lookback=2)

        assert signal.iloc[-1] == pytest.approx(0.05)

    def test_a_share_low_volatility_momentum_prefers_smoother_trend(self):
        from quant.features.research.validation.signal_library import compute_signal

        dates = pd.date_range("2022-01-03", periods=25, freq="B")
        smooth = np.linspace(100.0, 112.0, len(dates))
        choppy = np.array([100, 110, 95, 115, 96, 116, 98, 118, 99, 120, 100, 122, 101, 124, 102, 126, 103, 128, 104, 130, 105, 131, 106, 132, 112], dtype=float)
        frame = pd.DataFrame(
            {
                "date": list(dates) * 2,
                "symbol": ["600001"] * len(dates) + ["600002"] * len(dates),
                "close": list(smooth) + list(choppy),
                "adj_close": list(smooth) + list(choppy),
            }
        )

        signal = compute_signal("ashare_low_volatility_momentum", frame, lookback=20)

        assert signal.loc[dates[-1], "600001"] > signal.loc[dates[-1], "600002"]

    def test_a_share_liquidity_weighted_low_volatility_uses_turnover(self):
        from quant.features.research.validation.signal_library import compute_signal

        close = np.linspace(100.0, 101.0, 25)
        high_turnover = pd.DataFrame({"close": close, "adj_close": close, "turnover": [1000000.0] * 25})
        low_turnover = pd.DataFrame({"close": close, "adj_close": close, "turnover": [1000.0] * 25})

        high_signal = compute_signal("ashare_liquidity_weighted_low_volatility", high_turnover, lookback=20)
        low_signal = compute_signal("ashare_liquidity_weighted_low_volatility", low_turnover, lookback=20)

        assert high_signal.iloc[-1] > low_signal.iloc[-1]

    def test_joinquant_small_cap_size_factor_prefers_lower_market_cap(self):
        from quant.features.research.validation.signal_library import compute_signal

        dates = pd.date_range("2022-01-03", periods=3, freq="B")
        frame = pd.DataFrame(
            {
                "date": list(dates) * 2,
                "symbol": ["600001"] * 3 + ["600002"] * 3,
                "close": [10.0, 10.0, 10.0, 20.0, 20.0, 20.0],
                "adj_close": [10.0, 10.0, 10.0, 20.0, 20.0, 20.0],
                "total_mv": [100.0, 100.0, 100.0, 500.0, 500.0, 500.0],
            }
        )

        signal = compute_signal("joinquant_small_cap_size_factor", frame, lookback=1)

        assert signal.loc[dates[-1], "600001"] > signal.loc[dates[-1], "600002"]

    def test_worldquant_alpha_001_returns_cross_sectional_rank_signal(self):
        from quant.features.research.validation.signal_library import compute_signal

        dates = pd.date_range("2022-01-03", periods=40, freq="B")
        symbols = ["600001", "600002", "600003", "600004"]
        rng = np.random.default_rng(17)
        frames = []
        for idx, symbol in enumerate(symbols):
            returns = rng.normal(0.0005 * (idx + 1), 0.018 + idx * 0.002, len(dates))
            close = 100.0 * np.cumprod(1.0 + returns)
            frames.append(
                pd.DataFrame(
                    {
                        "date": dates,
                        "symbol": symbol,
                        "close": close,
                        "adj_close": close,
                        "volume": 1000000,
                    }
                )
            )
        frame = pd.concat(frames, ignore_index=True)

        signal = compute_signal("worldquant_alpha_001", frame, lookback=20)
        last = signal.dropna(how="all").iloc[-1].dropna()

        assert list(signal.columns) == symbols
        assert not last.empty
        assert last.max() <= 0.5
        assert last.min() >= -0.5
        assert last.nunique() > 1

    def test_worldquant_alpha_002_matches_ranked_volume_price_correlation(self):
        from quant.features.research.validation.signal_library import (
            adjusted_price_matrix,
            compute_signal,
            field_matrix,
        )

        dates = pd.date_range("2022-01-03", periods=14, freq="B")
        symbols = ["600001", "600002", "600003", "600004"]
        rng = np.random.default_rng(23)
        frames = []
        for symbol_index, symbol in enumerate(symbols):
            day_index = np.arange(len(dates), dtype=float)
            open_price = 10.0 + symbol_index + day_index * (0.05 + 0.01 * symbol_index)
            intraday_move = rng.normal(0.0, 0.004, len(dates))
            close = open_price * (1.0 + intraday_move)
            volume = rng.integers(800000, 1600000, len(dates)).astype(float)
            frames.append(
                pd.DataFrame(
                    {
                        "date": dates,
                        "symbol": symbol,
                        "open": open_price,
                        "close": close,
                        "adj_open": open_price,
                        "adj_close": close,
                        "volume": volume,
                    }
                )
            )
        frame = pd.concat(frames, ignore_index=True)

        signal = compute_signal("worldquant_alpha_002", frame, lookback=6)
        open_matrix = adjusted_price_matrix(frame, "open")
        close_matrix = adjusted_price_matrix(frame, "close")
        volume_matrix = field_matrix(frame, "volume").astype(float)
        delta_log_volume = np.log(volume_matrix).diff(2)
        intraday_return = (close_matrix - open_matrix) / open_matrix
        expected = -delta_log_volume.rank(axis=1, pct=True).rolling(6, min_periods=6).corr(
            intraday_return.rank(axis=1, pct=True)
        )

        pd.testing.assert_frame_equal(signal, expected)
        assert not signal.dropna(how="all").empty

    def test_worldquant_alpha_003_matches_ranked_open_volume_correlation(self):
        from quant.features.research.validation.signal_library import (
            adjusted_price_matrix,
            compute_signal,
            field_matrix,
        )

        dates = pd.date_range("2022-01-03", periods=16, freq="B")
        symbols = ["600001", "600002", "600003", "600004"]
        rng = np.random.default_rng(31)
        frames = []
        for symbol_index, symbol in enumerate(symbols):
            day_index = np.arange(len(dates), dtype=float)
            adjusted_open = 10.0 + symbol_index * 0.2 + day_index * 0.02 + rng.normal(0.0, 0.18, len(dates))
            volume = 900000.0 + symbol_index * 30000.0 + rng.normal(0.0, 60000.0, len(dates))
            frames.append(
                pd.DataFrame(
                    {
                        "date": dates,
                        "symbol": symbol,
                        "open": adjusted_open / 2.0,
                        "adj_open": adjusted_open,
                        "volume": volume,
                    }
                )
            )
        frame = pd.concat(frames, ignore_index=True)

        signal = compute_signal("worldquant_alpha_003", frame, lookback=10)
        open_matrix = adjusted_price_matrix(frame, "open")
        volume_matrix = field_matrix(frame, "volume").astype(float)
        expected = -open_matrix.rank(axis=1, pct=True).rolling(10, min_periods=10).corr(
            volume_matrix.rank(axis=1, pct=True)
        )

        pd.testing.assert_frame_equal(signal, expected)
        assert not signal.dropna(how="all").empty

    def test_worldquant_alpha_004_matches_low_rank_ts_rank(self):
        from quant.features.research.validation.signal_library import adjusted_price_matrix, compute_signal

        dates = pd.date_range("2022-01-03", periods=16, freq="B")
        symbols = ["600001", "600002", "600003", "600004"]
        rng = np.random.default_rng(37)
        frames = []
        for symbol_index, symbol in enumerate(symbols):
            day_index = np.arange(len(dates), dtype=float)
            adjusted_low = 8.0 + symbol_index * 0.4 + day_index * (0.03 + symbol_index * 0.01)
            adjusted_low = adjusted_low + rng.normal(0.0, 0.08, len(dates))
            frames.append(
                pd.DataFrame(
                    {
                        "date": dates,
                        "symbol": symbol,
                        "low": adjusted_low / 2.0,
                        "adj_low": adjusted_low,
                    }
                )
            )
        frame = pd.concat(frames, ignore_index=True)

        signal = compute_signal("worldquant_alpha_004", frame, lookback=9)
        low_matrix = adjusted_price_matrix(frame, "low")
        ranked_low = low_matrix.rank(axis=1, pct=True)

        def ts_rank_last(values):
            return pd.Series(values).rank(method="average", pct=True).iloc[-1]

        expected = -ranked_low.rolling(9, min_periods=9).apply(ts_rank_last, raw=False)

        pd.testing.assert_frame_equal(signal, expected)
        assert not signal.dropna(how="all").empty
        assert signal.dropna(how="all").max().max() <= 0

    def test_worldquant_alpha_006_matches_open_volume_correlation(self):
        from quant.features.research.validation.signal_library import adjusted_price_matrix, compute_signal, field_matrix

        dates = pd.date_range("2022-01-03", periods=16, freq="B")
        symbols = ["600001", "600002", "600003", "600004"]
        rng = np.random.default_rng(41)
        frames = []
        for symbol_index, symbol in enumerate(symbols):
            day_index = np.arange(len(dates), dtype=float)
            adjusted_open = 10.0 + symbol_index * 0.3 + day_index * (0.01 + symbol_index * 0.003)
            adjusted_open = adjusted_open + rng.normal(0.0, 0.1, len(dates))
            volume = 800000.0 + symbol_index * 50000.0 + rng.normal(0.0, 50000.0, len(dates))
            frames.append(
                pd.DataFrame(
                    {
                        "date": dates,
                        "symbol": symbol,
                        "open": adjusted_open / 2.0,
                        "adj_open": adjusted_open,
                        "volume": volume,
                    }
                )
            )
        frame = pd.concat(frames, ignore_index=True)

        signal = compute_signal("worldquant_alpha_006", frame, lookback=10)
        open_matrix = adjusted_price_matrix(frame, "open")
        volume_matrix = field_matrix(frame, "volume").astype(float)
        expected = -open_matrix.rolling(10, min_periods=10).corr(volume_matrix)

        pd.testing.assert_frame_equal(signal, expected)
        assert not signal.dropna(how="all").empty

    def test_worldquant_alpha_010_matches_conditional_delta_rank(self):
        from quant.features.research.validation.signal_library import adjusted_price_matrix, compute_signal

        dates = pd.date_range("2022-01-03", periods=5, freq="B")
        frame = pd.DataFrame(
            {
                "date": list(dates) * 3,
                "symbol": ["600001"] * 5 + ["600002"] * 5 + ["600003"] * 5,
                "close": [5.0, 5.5, 6.0, 6.5, 7.0, 5.0, 4.5, 4.0, 3.5, 3.0, 5.0, 5.5, 5.0, 5.5, 5.0],
                "adj_close": [10.0, 11.0, 12.0, 13.0, 14.0, 10.0, 9.0, 8.0, 7.0, 6.0, 10.0, 11.0, 10.0, 11.0, 10.0],
            }
        )

        signal = compute_signal("worldquant_alpha_010", frame, lookback=4)
        close = adjusted_price_matrix(frame, "close")
        delta = close.diff(1)
        ts_min = delta.rolling(4, min_periods=4).min()
        ts_max = delta.rolling(4, min_periods=4).max()
        expected = delta.where((ts_min > 0) | (ts_max < 0), -delta).rank(axis=1, pct=True)

        pd.testing.assert_frame_equal(signal, expected)
        assert signal.loc[dates[-1], "600002"] == pytest.approx(1.0 / 3.0)
        assert signal.loc[dates[-1], "600001"] == pytest.approx(5.0 / 6.0)
        assert signal.loc[dates[-1], "600003"] == pytest.approx(5.0 / 6.0)

    def test_factor_validator_uses_adjusted_close_for_forward_returns(self):
        from quant.features.research.validation.factor_validator import FactorValidator

        dates = pd.date_range("2022-01-03", periods=140, freq="B")
        symbols = [f"A{i:02d}" for i in range(30)]
        frames = []
        for i, symbol in enumerate(symbols):
            growth = 0.0002 + i * 0.00005
            adjusted = 100.0 * (1.0 + growth) ** np.arange(len(dates))
            raw = 100.0 * (1.0 - growth) ** np.arange(len(dates))
            frames.append(
                pd.DataFrame(
                    {
                        "date": dates,
                        "symbol": symbol,
                        "open": raw,
                        "high": raw * 1.01,
                        "low": raw * 0.99,
                        "close": raw,
                        "adj_open": adjusted,
                        "adj_high": adjusted * 1.01,
                        "adj_low": adjusted * 0.99,
                        "adj_close": adjusted,
                        "volume": 1000000,
                    }
                )
            )
        bars = pd.concat(frames, ignore_index=True)

        class FakeMarketData:
            def get_universe_symbols(self, market):
                return symbols

            def get_daily_bars(self, symbols, start, end):
                return bars[bars["symbol"].isin(symbols)]

        validator = FactorValidator(FakeMarketData(), config={"min_observations": 50, "min_stocks": 20})
        spec = StrategySpec(
            strategy_id="adjusted_forward_returns",
            strategy_type="momentum",
            signal_formula_key="momentum_close_return",
            universe=["AAPL"],
            horizon_days=1,
            lookback_days=1,
            execution_lag_days=1,
            required_fields=["close"],
            status="ready",
        )

        report = validator.validate(spec)

        assert report.status == "validated"
        assert report.rank_ic > 0.95


class TestFactorValidator:
    @staticmethod
    def _make_market_data(n_rows=300):
        dates = pd.date_range("2020-01-01", periods=n_rows, freq="B")
        np.random.seed(42)
        close = 100.0 + np.cumsum(np.random.randn(n_rows) * 0.5)
        high = close + np.abs(np.random.randn(n_rows))
        low = close - np.abs(np.random.randn(n_rows))
        df = pd.DataFrame(
            {"close": close, "high": high, "low": low, "volume": 1000000},
            index=dates,
        )

        class FakeMarketData:
            def get_daily_bars(self, symbols, start, end):
                return df

        return FakeMarketData()

    def test_configured_dates_are_used_for_market_data_request(self):
        from quant.features.research.validation.factor_validator import FactorValidator

        calls = []
        dates = pd.date_range("2020-01-01", periods=300, freq="B")
        frame = pd.DataFrame(
            {"close": np.linspace(100.0, 130.0, len(dates)), "volume": 1000000},
            index=dates,
        )

        class FakeMarketData:
            def get_daily_bars(self, symbols, start, end):
                calls.append((list(symbols), start, end))
                return frame

        validator = FactorValidator(
            FakeMarketData(),
            config={"min_observations": 50, "start_date": "2012-01-01", "end_date": "2025-12-31"},
        )
        spec = StrategySpec(
            strategy_id="test_mom",
            strategy_type="momentum",
            signal_formula_key="momentum_close_return",
            universe=["SPY"],
            horizon_days=5,
            lookback_days=20,
            execution_lag_days=1,
            required_fields=["close"],
            status="ready",
        )

        validator.validate(spec)

        assert calls == [(["SPY"], "2012-01-01", "2025-12-31")]

    def test_close_based_signal_applies_execution_lag(self):
        from quant.features.research.validation.factor_validator import FactorValidator

        md = self._make_market_data(300)
        validator = FactorValidator(md, config={"min_observations": 50, "execution_lag_days": 1})
        spec = StrategySpec(
            strategy_id="test_mom",
            strategy_type="momentum",
            signal_formula_key="momentum_close_return",
            universe=["SPY"],
            horizon_days=5,
            lookback_days=20,
            execution_lag_days=1,
            required_fields=["close"],
            status="ready",
        )
        report = validator.validate(spec)
        assert report.status == "validated"
        assert report.n_observations > 0

    def test_insufficient_observations_returns_error(self):
        from quant.features.research.validation.factor_validator import FactorValidator

        md = self._make_market_data(10)
        validator = FactorValidator(md, config={"min_observations": 252})
        spec = StrategySpec(
            strategy_id="test_short",
            strategy_type="momentum",
            signal_formula_key="momentum_close_return",
            universe=["SPY"],
            horizon_days=5,
            lookback_days=20,
            execution_lag_days=1,
            required_fields=["close"],
            status="ready",
        )
        report = validator.validate(spec)
        assert report.status == "error"

    def test_supported_formula_returns_validation_report(self):
        from quant.features.research.validation.factor_validator import FactorValidator

        md = self._make_market_data(300)
        validator = FactorValidator(md, config={"min_observations": 50})
        spec = StrategySpec(
            strategy_id="test_mr",
            strategy_type="mean_reversion",
            signal_formula_key="mean_reversion_close_to_ma",
            universe=["SPY"],
            horizon_days=5,
            lookback_days=20,
            execution_lag_days=1,
            required_fields=["close"],
            status="ready",
        )
        report = validator.validate(spec)
        assert isinstance(report, ValidationReport)
        assert report.status == "validated"

    def test_unsupported_spec_does_not_call_market_data(self):
        from quant.features.research.validation.factor_validator import FactorValidator

        validator = FactorValidator(None, config={"min_observations": 50})
        spec = StrategySpec(
            strategy_id="test_bad",
            strategy_type="momentum",
            signal_formula_key="momentum_close_return",
            universe=["SPY"],
            horizon_days=5,
            lookback_days=20,
            execution_lag_days=1,
            required_fields=["close"],
            status="unsupported_type",
        )
        report = validator.validate(spec)
        assert report.status == "skipped"

    def test_factor_validator_fetches_full_universe_and_populates_decay(self):
        from quant.features.research.validation.factor_validator import FactorValidator

        dates = pd.date_range("2022-01-01", periods=150, freq="B")
        symbols = [f"A{i:02d}" for i in range(30)]
        frames = []
        for i, symbol in enumerate(symbols):
            growth = 0.001 + i * 0.0001
            close = 100.0 * (1.0 + growth) ** np.arange(len(dates))
            frames.append(
                pd.DataFrame(
                    {
                        "date": dates,
                        "symbol": symbol,
                        "open": close,
                        "high": close * 1.01,
                        "low": close * 0.99,
                        "close": close,
                        "volume": 1000000,
                    }
                )
            )
        bars = pd.concat(frames, ignore_index=True)

        class FakeMarketData:
            def __init__(self):
                self.universe_calls = []
                self.daily_bar_symbols = []

            def get_universe_symbols(self, market):
                self.universe_calls.append(market)
                return symbols

            def get_daily_bars(self, symbols, start, end):
                self.daily_bar_symbols.append(list(symbols))
                return bars[bars["symbol"].isin(symbols)]

        md = FakeMarketData()
        validator = FactorValidator(md, config={"min_observations": 50, "execution_lag_days": 1})
        spec = StrategySpec(
            strategy_id="test_full_universe",
            strategy_type="momentum",
            signal_formula_key="momentum_close_return",
            universe=["AAPL"],
            horizon_days=5,
            lookback_days=20,
            execution_lag_days=1,
            required_fields=["close"],
            status="ready",
        )

        report = validator.validate(spec)

        assert md.universe_calls == ["us"]
        assert len(md.daily_bar_symbols[0]) == 30
        assert report.status == "validated"
        assert len(report.ic_decay) == 4
        assert isinstance(report.fama_macbeth_tstat, float)
        diagnostics = report.portfolio_diagnostics
        assert "top_bucket_after_cost_calmar_ratio" in diagnostics
        assert "top_bucket_after_cost_sharpe" in diagnostics
        assert "top_bucket_turnover" in diagnostics
        assert diagnostics["top_bucket_selection"] == "top_n"
        assert diagnostics["top_bucket_target_count"] == 20
        assert diagnostics["top_bucket_selected_count_min"] == 20
        assert diagnostics["top_bucket_selected_count_max"] == 20
        assert "top1_pct_annualized_return" in diagnostics
        assert "top1_pct_after_cost_calmar_ratio" in diagnostics
        assert "top1_pct_after_cost_sharpe" in diagnostics
        assert "top1_pct_turnover" in diagnostics
        assert "pnl_attribution_bridge" in diagnostics
        assert len(diagnostics["pnl_attribution_bridge"]) == 6
        assert diagnostics["pnl_attribution_bridge"][0]["key"] == "ideal_top20_close_to_close"
        assert diagnostics["pnl_attribution_bridge"][-1]["key"] == "turnover_cost"
        assert np.isfinite(diagnostics["pnl_attribution_bridge"][-1]["annualized_return"])
        assert np.isfinite(diagnostics["pnl_attribution_bridge"][-1]["delta_sharpe"])
        assert np.isfinite(diagnostics["top_bucket_after_cost_calmar_ratio"])
        assert np.isfinite(diagnostics["top_bucket_after_cost_sharpe"])
        assert np.isfinite(diagnostics["top_bucket_turnover"])
        assert np.isfinite(diagnostics["top1_pct_after_cost_calmar_ratio"])
        assert np.isfinite(diagnostics["top1_pct_after_cost_sharpe"])
        assert np.isfinite(diagnostics["top1_pct_turnover"])

    def test_factor_validator_applies_execution_lag_once(self, monkeypatch):
        from quant.features.research.validation import signal_library
        from quant.features.research.validation.factor_validator import FactorValidator

        dates = pd.date_range("2022-01-03", periods=140, freq="B")
        symbols = [f"A{i:02d}" for i in range(30)]
        symbol_scores = np.linspace(-1.0, 1.0, len(symbols))
        signal_values = np.vstack([
            ((-1.0) ** idx) * symbol_scores
            for idx in range(len(dates))
        ])
        signal_frame = pd.DataFrame(signal_values, index=dates, columns=symbols)

        close_values = np.full((len(dates), len(symbols)), 100.0)
        for date_idx in range(2, len(dates)):
            close_values[date_idx] = close_values[date_idx - 1] * (1.0 + 0.001 * signal_values[date_idx - 2])

        frames = []
        for symbol_idx, symbol in enumerate(symbols):
            close = close_values[:, symbol_idx]
            frames.append(
                pd.DataFrame(
                    {
                        "date": dates,
                        "symbol": symbol,
                        "open": close,
                        "high": close * 1.01,
                        "low": close * 0.99,
                        "close": close,
                        "volume": 1000000,
                    }
                )
            )
        bars = pd.concat(frames, ignore_index=True)

        class FakeMarketData:
            def get_universe_symbols(self, market):
                return symbols

            def get_daily_bars(self, symbols, start, end):
                return bars[bars["symbol"].isin(symbols)]

        def fake_compute_signal(formula_key, data, lookback):
            return signal_frame

        monkeypatch.setattr(signal_library, "compute_signal", fake_compute_signal)
        validator = FactorValidator(
            FakeMarketData(),
            config={"min_observations": 50, "execution_lag_days": 1},
        )
        spec = StrategySpec(
            strategy_id="lag_alignment",
            strategy_type="factor",
            signal_formula_key="patched_signal",
            universe=["A00"],
            horizon_days=1,
            lookback_days=1,
            execution_lag_days=1,
            required_fields=["close"],
            status="ready",
        )

        report = validator.validate(spec)

        assert report.status == "validated"
        assert report.rank_ic > 0.95
        assert report.rank_ic_tstat > 100
        assert report.rank_ic_p_value < 1e-20
        assert report.portfolio_diagnostics["return_frequency"] == "daily portfolio returns"

    def test_top_bucket_series_defaults_to_top_20_symbols(self):
        from quant.features.research.validation.factor_validator import FactorValidator

        date = pd.Timestamp("2022-01-03")
        symbols = [f"A{i:02d}" for i in range(30)]
        signals = pd.DataFrame([np.arange(30, dtype=float)], index=[date], columns=symbols)
        forward_returns = pd.DataFrame([[-1.0] * 30], index=[date], columns=symbols)
        forward_returns.loc[date, symbols[10:24]] = 0.0
        forward_returns.loc[date, symbols[24:30]] = 1.0

        validator = FactorValidator(None, config={"min_stocks": 20})
        top_bucket = validator._top_bucket_series(signals, forward_returns)

        assert top_bucket.iloc[0] == pytest.approx(0.30)

    def test_factor_validator_populates_ff_fields_when_factor_port_available(self):
        from quant.features.research.validation.factor_validator import FactorValidator

        dates = pd.date_range("2022-01-03", periods=180, freq="B")
        symbols = [f"A{i:02d}" for i in range(30)]
        frames = []
        for i, symbol in enumerate(symbols):
            growth = 0.0005 + i * 0.0001
            close = 100.0 * (1.0 + growth) ** np.arange(len(dates))
            frames.append(
                pd.DataFrame(
                    {
                        "date": dates,
                        "symbol": symbol,
                        "open": close,
                        "high": close * 1.01,
                        "low": close * 0.99,
                        "close": close,
                        "volume": 1000000,
                    }
                )
            )
        bars = pd.concat(frames, ignore_index=True)

        class FakeMarketData:
            def get_universe_symbols(self, market):
                return symbols

            def get_daily_bars(self, symbols, start, end):
                return bars[bars["symbol"].isin(symbols)]

        class FakeFactorData:
            def __init__(self):
                self.calls = []

            def get_factors(self, names, start, end):
                self.calls.append((names, start, end))
                return pd.DataFrame({"MKT": 0.0, "SMB": 0.0, "HML": 0.0, "RF": 0.0}, index=dates)

        factor_data = FakeFactorData()
        validator = FactorValidator(
            FakeMarketData(),
            factor_data_port=factor_data,
            config={"min_observations": 50, "execution_lag_days": 1},
        )
        spec = StrategySpec(
            strategy_id="test_ff",
            strategy_type="momentum",
            signal_formula_key="momentum_close_return",
            universe=["AAPL"],
            horizon_days=5,
            lookback_days=20,
            execution_lag_days=1,
            required_fields=["close"],
            status="ready",
        )

        report = validator.validate(spec)

        assert report.status == "validated"
        assert factor_data.calls
        assert report.ff_alpha_monthly != 0.0
        assert report.ff_alpha_tstat != 0.0

    def test_factor_validator_flags_unavailable_factor_data_when_enabled(self):
        from quant.features.research.validation.factor_validator import FactorValidator

        dates = pd.date_range("2022-01-03", periods=180, freq="B")
        symbols = [f"A{i:02d}" for i in range(30)]
        frames = []
        for i, symbol in enumerate(symbols):
            growth = 0.0005 + i * 0.0001
            close = 100.0 * (1.0 + growth) ** np.arange(len(dates))
            frames.append(
                pd.DataFrame(
                    {
                        "date": dates,
                        "symbol": symbol,
                        "open": close,
                        "high": close * 1.01,
                        "low": close * 0.99,
                        "close": close,
                        "volume": 1000000,
                    }
                )
            )
        bars = pd.concat(frames, ignore_index=True)

        class FakeMarketData:
            def get_universe_symbols(self, market):
                return symbols

            def get_daily_bars(self, symbols, start, end):
                return bars[bars["symbol"].isin(symbols)]

        class MissingFactorData:
            def get_factors(self, names, start, end):
                return None

        validator = FactorValidator(
            FakeMarketData(),
            factor_data_port=MissingFactorData(),
            config={
                "min_observations": 50,
                "execution_lag_days": 1,
                "factor_validation_enabled": True,
            },
        )
        spec = StrategySpec(
            strategy_id="test_missing_factor_data",
            strategy_type="momentum",
            signal_formula_key="momentum_close_return",
            universe=["AAPL"],
            horizon_days=5,
            lookback_days=20,
            execution_lag_days=1,
            required_fields=["close"],
            status="ready",
        )

        report = validator.validate(spec)

        assert report.status == "validated"
        assert "factor_data_unavailable" in report.errors

    def test_cross_sectional_validation_enforces_100_date_floor(self):
        from quant.features.research.validation.factor_validator import FactorValidator

        dates = pd.date_range("2022-01-01", periods=120, freq="B")
        symbols = [f"A{i:02d}" for i in range(30)]
        frames = []
        for i, symbol in enumerate(symbols):
            growth = 0.001 + i * 0.0001
            close = 100.0 * (1.0 + growth) ** np.arange(len(dates))
            frames.append(
                pd.DataFrame(
                    {
                        "date": dates,
                        "symbol": symbol,
                        "open": close,
                        "high": close * 1.01,
                        "low": close * 0.99,
                        "close": close,
                        "volume": 1000000,
                    }
                )
            )
        bars = pd.concat(frames, ignore_index=True)

        class FakeMarketData:
            def get_universe_symbols(self, market):
                return symbols

            def get_daily_bars(self, symbols, start, end):
                return bars[bars["symbol"].isin(symbols)]

        validator = FactorValidator(FakeMarketData(), config={"min_observations": 50})
        spec = StrategySpec(
            strategy_id="test_cs_floor",
            strategy_type="momentum",
            signal_formula_key="momentum_close_return",
            universe=["AAPL"],
            horizon_days=5,
            lookback_days=20,
            execution_lag_days=1,
            required_fields=["close"],
            status="ready",
        )

        report = validator.validate(spec)

        assert report.status == "error"
        assert "Insufficient valid cross-sectional dates" in report.errors[0]

    def test_duckdb_market_data_routes_timestamp_market_tables_and_universes(self, tmp_path):
        duckdb = pytest.importorskip("duckdb")
        from quant.infrastructure.research.market_data.duckdb_research_market_data import (
            DuckDBResearchMarketData,
        )

        db_path = tmp_path / "research_market.duckdb"
        conn = duckdb.connect(str(db_path))
        schema = "(symbol VARCHAR, timestamp TIMESTAMP, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT)"
        for table in ("daily_cn_ochl", "daily_hk", "daily_us"):
            conn.execute(f"CREATE TABLE {table} {schema}")
        conn.execute("INSERT INTO daily_cn_ochl VALUES ('600519', '2024-01-02 09:30:00', 1, 2, 0.5, 1.5, 100)")
        conn.execute("INSERT INTO daily_hk VALUES ('12345', '2024-01-02 09:30:00', 2, 3, 1.5, 2.5, 200)")
        conn.execute("INSERT INTO daily_hk VALUES ('HK.00700', '2024-01-03 09:30:00', 4, 5, 3.5, 4.5, 250)")
        conn.execute("INSERT INTO daily_us VALUES ('AAPL', '2024-01-02 09:30:00', 3, 4, 2.5, 3.5, 300)")
        conn.close()

        market_data = DuckDBResearchMarketData(str(db_path))

        assert market_data.get_universe_symbols("cn") == ["600519"]
        assert market_data.get_universe_symbols("hk") == ["12345", "HK.00700"]
        assert market_data.get_universe_symbols("us") == ["AAPL"]

        bars = market_data.get_daily_bars(["600519", "12345", "HK.00700", "AAPL"], "2024-01-01", "2024-01-31")

        assert set(bars["symbol"]) == {"600519", "12345", "HK.00700", "AAPL"}
        assert "date" in bars.columns
        assert "timestamp" not in bars.columns
        assert market_data._table_for_symbol("600519") == "daily_cn_ochl"
        assert market_data._table_for_symbol("12345") == "daily_hk"
        assert market_data._table_for_symbol("HK.00700") == "daily_hk"
        assert market_data._table_for_symbol("HSI") == "daily_hk"
        assert market_data._table_for_symbol("AAPL") == "daily_us"

    def test_duckdb_market_data_supports_date_schema_fallback(self, tmp_path):
        duckdb = pytest.importorskip("duckdb")
        from quant.infrastructure.research.market_data.duckdb_research_market_data import (
            DuckDBResearchMarketData,
        )

        db_path = tmp_path / "research_market_date.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute(
            "CREATE TABLE daily_us (symbol VARCHAR, date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT)"
        )
        conn.execute("CREATE TABLE daily_cn_ochl AS SELECT * FROM daily_us WHERE 1 = 0")
        conn.execute("CREATE TABLE daily_hk AS SELECT * FROM daily_us WHERE 1 = 0")
        conn.execute("INSERT INTO daily_us VALUES ('AAPL', '2024-01-02', 3, 4, 2.5, 3.5, 300)")
        conn.close()

        market_data = DuckDBResearchMarketData(str(db_path))
        bars = market_data.get_daily_bars(["AAPL"], "2024-01-01", "2024-01-31")

        assert list(bars["symbol"]) == ["AAPL"]
        assert "date" in bars.columns

    def test_duckdb_market_data_returns_adjusted_price_columns_when_available(self, tmp_path):
        duckdb = pytest.importorskip("duckdb")
        from quant.infrastructure.research.market_data.duckdb_research_market_data import (
            DuckDBResearchMarketData,
        )

        db_path = tmp_path / "research_market_adjusted.duckdb"
        conn = duckdb.connect(str(db_path))
        schema = (
            "(symbol VARCHAR, date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, "
            "volume BIGINT, adj_open DOUBLE, adj_high DOUBLE, adj_low DOUBLE, adj_close DOUBLE, adj_factor DOUBLE)"
        )
        for table in ("daily_cn_ochl", "daily_hk", "daily_us"):
            conn.execute(f"CREATE TABLE {table} {schema}")
        conn.execute(
            "INSERT INTO daily_us VALUES ('AAPL', '2024-01-02', 3, 4, 2.5, 3.5, 300, 6, 8, 5, 7, 2)"
        )
        conn.close()

        market_data = DuckDBResearchMarketData(str(db_path))
        bars = market_data.get_daily_bars(["AAPL"], "2024-01-01", "2024-01-31")

        assert bars["adj_open"].iloc[0] == pytest.approx(6.0)
        assert bars["adj_high"].iloc[0] == pytest.approx(8.0)
        assert bars["adj_low"].iloc[0] == pytest.approx(5.0)
        assert bars["adj_close"].iloc[0] == pytest.approx(7.0)
        assert bars["adj_factor"].iloc[0] == pytest.approx(2.0)
