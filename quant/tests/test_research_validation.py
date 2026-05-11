import numpy as np
import pandas as pd
import pytest

from quant.features.research.models import (
    EvaluationReport,
    RawStrategy,
    StrategySpec,
    ValidationReport,
)


def _raw(title="Test Strategy") -> RawStrategy:
    return RawStrategy(
        title=title,
        description="desc",
        source="test",
        source_url="https://example.test",
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
        for table in ("daily_cn", "daily_hk", "daily_us"):
            conn.execute(f"CREATE TABLE {table} {schema}")
        conn.execute("INSERT INTO daily_cn VALUES ('600519', '2024-01-02 09:30:00', 1, 2, 0.5, 1.5, 100)")
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
        assert market_data._table_for_symbol("600519") == "daily_cn"
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
        conn.execute("CREATE TABLE daily_cn AS SELECT * FROM daily_us WHERE 1 = 0")
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
        for table in ("daily_cn", "daily_hk", "daily_us"):
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
