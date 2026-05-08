import pandas as pd
import pytest

from quant.features.research.models import StrategySpec


def test_sensitivity_report_flags_unstable_params():
    from quant.features.research.validation.sensitivity import SensitivityReport

    report = SensitivityReport(
        strategy_id="s",
        base_ic=0.04,
        base_params={"lookback_days": 20, "horizon_days": 5},
        parameter_combinations=[
            {"lookback_days": 10, "horizon_days": 1},
            {"lookback_days": 30, "horizon_days": 21},
        ],
        ic_surface=[0.04, 0.01],
        is_stable=False,
        max_degradation_pct=75.0,
        optimal_params={"lookback_days": 10, "horizon_days": 1},
    )

    assert report.is_stable is False


def test_sensitivity_sweep_handles_sparse_data_without_false_stable_pass():
    from quant.features.research.validation.sensitivity import run_sensitivity_sweep

    class SparseMarketData:
        def get_universe_symbols(self, market):
            return ["AAPL", "MSFT"]

        def get_daily_bars(self, symbols, start, end):
            return pd.DataFrame(columns=["date", "symbol", "close", "open", "high", "low", "volume"])

    spec = StrategySpec(
        strategy_id="sparse",
        strategy_type="momentum",
        signal_formula_key="momentum_close_return",
        universe=["AAPL"],
        horizon_days=5,
        lookback_days=20,
        execution_lag_days=1,
        required_fields=["close"],
        status="ready",
    )

    report = run_sensitivity_sweep(
        spec,
        SparseMarketData(),
        {"lookback_days": 20, "horizon_days": 5},
        {"sensitivity_lookback_grid": [10], "sensitivity_horizon_grid": [1]},
    )

    assert report.strategy_id == "sparse"
    assert report.is_stable is False
    assert report.max_degradation_pct == pytest.approx(100.0)
    assert report.ic_surface == [0.0, 0.0]


def test_sensitivity_sweep_includes_base_params_when_grid_omits_them(monkeypatch):
    import quant.features.research.validation.sensitivity as module
    from quant.features.research.validation.sensitivity import run_sensitivity_sweep

    spec = StrategySpec(
        strategy_id="base",
        strategy_type="momentum",
        signal_formula_key="momentum_close_return",
        universe=["AAPL"],
        horizon_days=5,
        lookback_days=20,
        execution_lag_days=1,
        required_fields=["close"],
        status="ready",
    )

    def fake_compute_ic(spec, data, params, config):
        if params["lookback_days"] == 20 and params["horizon_days"] == 5:
            return 0.01
        return 0.05

    monkeypatch.setattr(module, "_load_market_frame", lambda spec, market_data_port, config: pd.DataFrame({"x": [1]}))
    monkeypatch.setattr(module, "_compute_ic", fake_compute_ic)

    report = run_sensitivity_sweep(
        spec,
        object(),
        {"lookback_days": 20, "horizon_days": 5},
        {"sensitivity_lookback_grid": [10], "sensitivity_horizon_grid": [1], "sensitivity_max_degradation_pct": 30},
    )

    assert {"lookback_days": 20, "horizon_days": 5} in report.parameter_combinations
    assert report.base_ic == pytest.approx(0.01)
    assert report.max_degradation_pct == pytest.approx(80.0)
    assert report.is_stable is False


def test_sensitivity_sweep_uses_absolute_ic_for_negative_surfaces(monkeypatch):
    import quant.features.research.validation.sensitivity as module
    from quant.features.research.validation.sensitivity import run_sensitivity_sweep

    spec = StrategySpec(
        strategy_id="negative",
        strategy_type="mean_reversion",
        signal_formula_key="momentum_close_return",
        universe=["AAPL"],
        horizon_days=5,
        lookback_days=20,
        execution_lag_days=1,
        required_fields=["close"],
        status="ready",
    )

    def fake_compute_ic(spec, data, params, config):
        if params["lookback_days"] == 20 and params["horizon_days"] == 5:
            return -0.01
        return -0.05

    monkeypatch.setattr(module, "_load_market_frame", lambda spec, market_data_port, config: pd.DataFrame({"x": [1]}))
    monkeypatch.setattr(module, "_compute_ic", fake_compute_ic)

    report = run_sensitivity_sweep(
        spec,
        object(),
        {"lookback_days": 20, "horizon_days": 5},
        {"sensitivity_lookback_grid": [10], "sensitivity_horizon_grid": [1], "sensitivity_max_degradation_pct": 30},
    )

    assert report.optimal_params == {"lookback_days": 10, "horizon_days": 1}
    assert report.max_degradation_pct == pytest.approx(80.0)
    assert report.is_stable is False


def test_factor_validator_appends_sensitivity_status_when_enabled(monkeypatch):
    import quant.features.research.validation.factor_validator as module
    from quant.features.research.validation.factor_validator import FactorValidator
    from quant.features.research.validation.sensitivity import SensitivityReport

    dates = pd.date_range("2022-01-03", periods=300, freq="B")
    data = pd.DataFrame(
        {
            "close": [100.0 + i for i in range(len(dates))],
            "high": [101.0 + i for i in range(len(dates))],
            "low": [99.0 + i for i in range(len(dates))],
            "volume": [1000000] * len(dates),
        },
        index=dates,
    )

    class MarketData:
        def get_daily_bars(self, symbols, start, end):
            return data

    def fake_sweep(spec, market_data_port, base_params, config):
        return SensitivityReport(
            strategy_id=spec.strategy_id,
            base_ic=0.02,
            base_params=base_params,
            parameter_combinations=[base_params],
            ic_surface=[0.02],
            is_stable=False,
            max_degradation_pct=42.5,
            optimal_params=base_params,
        )

    monkeypatch.setattr(module, "run_sensitivity_sweep", fake_sweep)
    validator = FactorValidator(
        MarketData(),
        config={"min_observations": 50, "sensitivity_enabled": True},
    )
    spec = StrategySpec(
        strategy_id="s",
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
    assert "sensitivity: unstable (max_degradation=42.5%)" in report.errors


def test_factor_validator_preserves_empty_pit_universe():
    from quant.features.research.validation.factor_validator import FactorValidator

    class MarketData:
        def get_universe_symbols(self, market):
            return []

    spec = StrategySpec(
        strategy_id="empty_pit",
        strategy_type="momentum",
        signal_formula_key="momentum_close_return",
        universe=["AAPL"],
        horizon_days=5,
        lookback_days=20,
        execution_lag_days=1,
        required_fields=["close"],
        status="ready",
    )

    assert FactorValidator(MarketData())._resolve_universe(spec) == []


def test_sensitivity_loader_preserves_empty_pit_universe():
    import quant.features.research.validation.sensitivity as module

    class MarketData:
        def __init__(self):
            self.bar_calls = []

        def get_universe_symbols(self, market):
            return []

        def get_daily_bars(self, symbols, start, end):
            self.bar_calls.append(symbols)
            return pd.DataFrame()

    spec = StrategySpec(
        strategy_id="empty_pit",
        strategy_type="momentum",
        signal_formula_key="momentum_close_return",
        universe=["AAPL"],
        horizon_days=5,
        lookback_days=20,
        execution_lag_days=1,
        required_fields=["close"],
        status="ready",
    )
    market_data = MarketData()

    frame = module._load_market_frame(spec, market_data, {})

    assert frame.empty
    assert market_data.bar_calls == []


def test_pit_port_signature_imports_without_external_deps():
    from quant.domain.ports import PITData
    from quant.domain.ports.pit_data import PITData as DirectPITData

    assert PITData is DirectPITData


def test_pit_duckdb_data_infers_universe_without_listing_columns(tmp_path, caplog):
    duckdb = pytest.importorskip("duckdb")
    from quant.infrastructure.research.pit_duckdb import PITDuckDBData

    db_path = tmp_path / "pit.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE daily_us (symbol VARCHAR, date DATE, close DOUBLE, adj_close DOUBLE, volume BIGINT)")
    conn.execute("INSERT INTO daily_us VALUES ('OLD', '2023-01-02', 10.0, 9.5, 100)")
    conn.execute("INSERT INTO daily_us VALUES ('AAPL', '2024-01-03', 20.0, 19.5, 200)")
    conn.execute("INSERT INTO daily_us VALUES ('AAPL', '2024-02-01', 21.0, 20.5, 210)")
    conn.execute("INSERT INTO daily_us VALUES ('FUTURE', '2025-01-02', 30.0, 29.5, 300)")
    conn.close()

    adapter = PITDuckDBData(str(db_path))

    with caplog.at_level("WARNING"):
        universe = adapter.get_universe("2024-01-31", "us")

    assert universe == ["AAPL"]
    assert "inferring universe" in caplog.text


def test_pit_duckdb_bars_respect_as_of_and_adjust_ohlc(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from quant.infrastructure.research.pit_duckdb import PITDuckDBData

    db_path = tmp_path / "pit_bars.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        "CREATE TABLE daily_us (symbol VARCHAR, date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, adj_close DOUBLE, volume BIGINT)"
    )
    conn.execute("INSERT INTO daily_us VALUES ('AAPL', '2024-01-02', 18.0, 21.0, 17.0, 20.0, 19.5, 200)")
    conn.execute("INSERT INTO daily_us VALUES ('AAPL', '2024-02-01', 28.0, 31.0, 27.0, 30.0, 29.5, 300)")
    conn.close()

    adapter = PITDuckDBData(str(db_path))
    bars = adapter.get_bars_pit(["AAPL"], "2024-01-01", "2024-12-31", "2024-01-31")

    assert list(bars["symbol"]) == ["AAPL"]
    assert list(bars["close"]) == [19.5]
    assert bars["open"].iloc[0] == pytest.approx(17.55)
    assert bars["high"].iloc[0] == pytest.approx(20.475)
    assert bars["low"].iloc[0] == pytest.approx(16.575)


def test_pit_duckdb_bars_preserve_raw_prices_when_adj_close_missing(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from quant.infrastructure.research.pit_duckdb import PITDuckDBData

    db_path = tmp_path / "pit_bars_missing_adj.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        "CREATE TABLE daily_us (symbol VARCHAR, date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, adj_close DOUBLE, volume BIGINT)"
    )
    conn.execute("INSERT INTO daily_us VALUES ('AAPL', '2024-01-02', 18.0, 21.0, 17.0, 20.0, NULL, 200)")
    conn.close()

    adapter = PITDuckDBData(str(db_path))
    bars = adapter.get_bars_pit(["AAPL"], "2024-01-01", "2024-12-31", "2024-01-31")

    assert bars["close"].iloc[0] == pytest.approx(20.0)
    assert bars["open"].iloc[0] == pytest.approx(18.0)


def test_pit_duckdb_bars_exclude_delisted_symbols(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from quant.infrastructure.research.pit_duckdb import PITDuckDBData

    db_path = tmp_path / "pit_active_filter.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        "CREATE TABLE daily_us (symbol VARCHAR, date DATE, close DOUBLE, listing_date DATE, delisting_date DATE)"
    )
    conn.execute("INSERT INTO daily_us VALUES ('OLD', '2024-01-02', 10.0, '2020-01-01', '2024-01-15')")
    conn.execute("INSERT INTO daily_us VALUES ('AAPL', '2024-01-02', 20.0, '2020-01-01', NULL)")
    conn.close()

    adapter = PITDuckDBData(str(db_path))
    bars = adapter.get_bars_pit(["OLD", "AAPL"], "2024-01-01", "2024-12-31", "2024-01-31")

    assert list(bars["symbol"]) == ["AAPL"]


def test_duckdb_market_data_uses_pit_universe_when_configured():
    from quant.infrastructure.research.market_data.duckdb_research_market_data import DuckDBResearchMarketData

    class PIT:
        def __init__(self):
            self.calls = []

        def get_universe(self, as_of_date, market):
            self.calls.append((as_of_date, market))
            return ["AAPL"]

    pit = PIT()
    market_data = DuckDBResearchMarketData("missing.duckdb", pit_data=pit, pit_as_of_date="2024-01-31")

    assert market_data.get_universe_symbols("us") == ["AAPL"]
    assert pit.calls == [("2024-01-31", "us")]


def test_duckdb_market_data_preserves_empty_pit_universe():
    from quant.infrastructure.research.market_data.duckdb_research_market_data import DuckDBResearchMarketData

    class PIT:
        def get_universe(self, as_of_date, market):
            return []

    market_data = DuckDBResearchMarketData("missing.duckdb", pit_data=PIT(), pit_as_of_date="1900-01-01")

    assert market_data.get_universe_symbols("us") == []


def test_duckdb_market_data_fails_closed_when_pit_universe_errors():
    from quant.infrastructure.research.market_data.duckdb_research_market_data import DuckDBResearchMarketData

    class PIT:
        def get_universe(self, as_of_date, market):
            raise RuntimeError("pit unavailable")

    market_data = DuckDBResearchMarketData("missing.duckdb", pit_data=PIT(), pit_as_of_date="2024-01-31")

    assert market_data.get_universe_symbols("us") == []


def test_duckdb_market_data_delegates_bars_to_pit_adapter():
    from quant.infrastructure.research.market_data.duckdb_research_market_data import DuckDBResearchMarketData

    class PIT:
        def __init__(self):
            self.calls = []

        def get_universe(self, as_of_date, market):
            return ["AAPL"]

        def get_bars_pit(self, symbols, start, end, as_of_date):
            self.calls.append((symbols, start, end, as_of_date))
            return pd.DataFrame({"symbol": ["AAPL"], "date": ["2024-01-02"], "close": [10.0]})

    pit = PIT()
    market_data = DuckDBResearchMarketData("missing.duckdb", pit_data=pit, pit_as_of_date="2024-01-31")
    bars = market_data.get_daily_bars(["AAPL"], "2024-01-01", "2024-12-31")

    assert list(bars["symbol"]) == ["AAPL"]
    assert pit.calls == [(["AAPL"], "2024-01-01", "2024-12-31", "2024-01-31")]


def test_api_market_data_factory_consumes_pit_enabled():
    from quant.api import research_bp as research_module
    from quant.features.research.models import ResearchConfig

    market_data = research_module._make_research_market_data(
        ResearchConfig(pit_enabled=True, default_backtest_end="2024-01-31")
    )

    assert market_data._pit_data is not None
    assert market_data._pit_as_of_date == "2024-01-31"


def test_build_pit_universe_keeps_market_last_bar_symbols_active(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from quant.scripts.build_pit_universe import _load_symbol_lifetimes

    db_path = tmp_path / "lifetimes.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE daily_us (symbol VARCHAR, date DATE, close DOUBLE)")
    conn.execute("INSERT INTO daily_us VALUES ('OLD', '2024-01-02', 10.0)")
    conn.execute("INSERT INTO daily_us VALUES ('AAPL', '2024-01-02', 20.0)")
    conn.execute("INSERT INTO daily_us VALUES ('AAPL', '2024-03-01', 21.0)")

    lifetimes = _load_symbol_lifetimes(conn, today="2024-12-31")
    conn.close()

    rows = {row["symbol"]: row for row in lifetimes.to_dict("records")}
    assert pd.isna(rows["AAPL"]["delisting_date"])
    assert str(pd.Timestamp(rows["OLD"]["delisting_date"]).date()) == "2024-01-02"


def test_write_monthly_snapshots_masks_future_delisting_dates(tmp_path):
    pytest.importorskip("pyarrow")
    from quant.scripts.build_pit_universe import write_monthly_snapshots

    lifetimes = pd.DataFrame(
        [
            {
                "symbol": "OLD",
                "market": "us",
                "listing_date": "2024-01-02",
                "last_bar_date": "2024-03-15",
                "delisting_date": "2024-03-15",
            }
        ]
    )

    paths = write_monthly_snapshots(lifetimes, tmp_path)
    january = pd.read_parquet(paths[0])

    assert january["symbol"].tolist() == ["OLD"]
    assert pd.isna(january["delisting_date"].iloc[0])


def test_build_pit_universe_month_end_helper():
    from quant.scripts.build_pit_universe import _snapshot_month_ends

    month_ends = _snapshot_month_ends("2024-01-02", "2024-03-04")

    assert [str(value.date()) for value in month_ends] == ["2024-01-31", "2024-02-29", "2024-03-31"]
