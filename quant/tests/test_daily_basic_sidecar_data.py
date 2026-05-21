from datetime import datetime

import pytest

from quant.infrastructure.data.storage_duckdb import DuckDBStorage
from quant.infrastructure.research.market_data.duckdb_research_market_data import DuckDBResearchMarketData


def _create_market_db(duckdb, path):
    conn = duckdb.connect(str(path))
    conn.execute(
        """
        CREATE TABLE daily_cn_ochl (
            timestamp TIMESTAMP,
            symbol VARCHAR,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume BIGINT,
            turnover DOUBLE,
            adj_open DOUBLE,
            adj_high DOUBLE,
            adj_low DOUBLE,
            adj_close DOUBLE,
            adj_factor DOUBLE
        )
        """
    )
    conn.execute(
        """
        INSERT INTO daily_cn_ochl VALUES
        ('2024-01-02', '600001', 10, 11, 9, 10.5, 1000, 10500, 10, 11, 9, 10.5, 1)
        """
    )
    conn.close()


def _create_basic_db(duckdb, path):
    conn = duckdb.connect(str(path))
    conn.execute(
        """
        CREATE TABLE cn_daily_basic (
            trade_date DATE,
            symbol VARCHAR,
            turnover_rate DOUBLE,
            total_mv DOUBLE,
            circ_mv DOUBLE,
            turnover_rate_f DOUBLE,
            volume_ratio DOUBLE,
            pe DOUBLE,
            pe_ttm DOUBLE,
            pb DOUBLE,
            ps DOUBLE,
            ps_ttm DOUBLE,
            dv_ratio DOUBLE,
            dv_ttm DOUBLE,
            total_share DOUBLE,
            float_share DOUBLE,
            free_share DOUBLE
        )
        """
    )
    conn.execute(
        """
        INSERT INTO cn_daily_basic VALUES
        ('2024-01-02', '600001', 1.2, 12345, 6789, 2.5, 1.1, 10, 11, 1.2, 2.1, 2.2, 0.3, 0.4, 1000, 800, 700)
        """
    )
    conn.close()


def _create_financial_db(duckdb, path):
    conn = duckdb.connect(str(path))
    conn.execute(
        """
        CREATE TABLE cn_financial_indicators (
            symbol VARCHAR,
            ts_code VARCHAR,
            ann_date DATE,
            end_date DATE,
            roe DOUBLE,
            netprofit_yoy DOUBLE,
            updated_at TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        INSERT INTO cn_financial_indicators VALUES
        ('600001', '600001.SH', '2024-03-01', '2023-12-31', 5.0, 4.0, '2024-03-01'),
        ('600001', '600001.SH', '2024-04-30', '2024-03-31', 10.0, 12.0, '2024-04-30')
        """
    )
    conn.close()


def _create_status_db(duckdb, path):
    conn = duckdb.connect(str(path))
    conn.execute(
        """
        CREATE TABLE cn_security_status_daily (
            symbol VARCHAR,
            trade_date DATE,
            is_trade_day BOOLEAN,
            is_listed BOOLEAN,
            list_status VARCHAR,
            is_st BOOLEAN,
            st_type VARCHAR,
            is_suspended BOOLEAN,
            suspend_type VARCHAR,
            suspend_timing VARCHAR,
            has_daily_bar BOOLEAN,
            pre_close DOUBLE,
            up_limit DOUBLE,
            down_limit DOUBLE,
            tradable BOOLEAN,
            source VARCHAR,
            updated_at TIMESTAMP WITH TIME ZONE
        )
        """
    )
    conn.executemany(
        "INSERT INTO cn_security_status_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("600001", "2024-01-02", True, True, "L", False, "", False, "", "", True, 10.0, 11.0, 9.0, True, "fixture", "2024-01-03 00:00:00+00"),
            ("600002", "2024-01-02", True, True, "L", False, "", True, "S", "D", False, 8.0, 8.8, 7.2, False, "fixture", "2024-01-03 00:00:00+00"),
        ],
    )
    conn.close()


def test_storage_reads_cn_market_cap_from_sidecar_without_ochl_columns(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    market_db = tmp_path / "quant.duckdb"
    basic_db = tmp_path / "cn_daily_basic.duckdb"
    _create_market_db(duckdb, market_db)
    _create_basic_db(duckdb, basic_db)

    storage = DuckDBStorage(str(market_db), read_only=True, daily_basic_db_path=str(basic_db))
    try:
        bars = storage.get_bars("600001", datetime(2024, 1, 2), datetime(2024, 1, 2), "1d")
        ochl_columns = {row[1] for row in storage.conn.execute("PRAGMA table_info('daily_cn_ochl')").fetchall()}
    finally:
        storage.close()

    assert "total_mv" not in ochl_columns
    assert bars["total_mv"].iloc[0] == pytest.approx(12345)
    assert bars["circ_mv"].iloc[0] == pytest.approx(6789)
    assert bars["turnover_rate_f"].iloc[0] == pytest.approx(2.5)


def test_research_market_data_reads_sidecar_fields_without_ochl_pollution(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    market_db = tmp_path / "quant.duckdb"
    basic_db = tmp_path / "cn_daily_basic.duckdb"
    _create_market_db(duckdb, market_db)
    _create_basic_db(duckdb, basic_db)

    market_data = DuckDBResearchMarketData(str(market_db), daily_basic_db_path=str(basic_db))

    fields = set(market_data.available_fields("cn"))
    bars = market_data.get_daily_bars(["600001"], "2024-01-02", "2024-01-02")

    assert "total_mv" in fields
    assert "circ_mv" in fields
    assert bars["total_mv"].iloc[0] == pytest.approx(12345)
    assert bars["circ_mv"].iloc[0] == pytest.approx(6789)


def test_research_market_data_can_limit_sidecar_fields(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    market_db = tmp_path / "quant.duckdb"
    basic_db = tmp_path / "cn_daily_basic.duckdb"
    _create_market_db(duckdb, market_db)
    _create_basic_db(duckdb, basic_db)

    market_data = DuckDBResearchMarketData(str(market_db), daily_basic_db_path=str(basic_db))

    bars = market_data.get_daily_bars(["600001"], "2024-01-02", "2024-01-02", fields=["close", "total_mv"])

    assert "close" in bars.columns
    assert "total_mv" in bars.columns
    assert "circ_mv" not in bars.columns
    assert "turnover_rate_f" not in bars.columns


def test_streaming_research_backtest_provider_reads_status_and_sidecar(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    market_db = tmp_path / "quant.duckdb"
    basic_db = tmp_path / "cn_daily_basic.duckdb"
    status_db = tmp_path / "security_status.duckdb"
    _create_market_db(duckdb, market_db)
    _create_basic_db(duckdb, basic_db)
    _create_status_db(duckdb, status_db)

    from quant.api.research_bp import _DuckDBDailyDateProvider

    provider = _DuckDBDailyDateProvider(
        ["600001", "600002"],
        datetime(2024, 1, 2),
        datetime(2024, 1, 2),
        db_path=str(market_db),
        status_db_path=str(status_db),
        daily_basic_db_path=str(basic_db),
    )
    try:
        bars = provider.get_bars_for_date(datetime(2024, 1, 2))
    finally:
        provider.close()

    assert type(bars).__name__ == "_BarRecordBatch"
    assert len(bars) == 2
    assert list(bars)[0]["symbol"] in {"600001", "600002"}
    by_symbol = {bar["symbol"]: bar for bar in bars}
    assert by_symbol["600001"]["total_mv"] == pytest.approx(12345)
    assert by_symbol["600001"]["tradable"] is True
    assert by_symbol["600001"]["_suspended"] is False
    assert "adv20_value" not in by_symbol["600001"]
    assert by_symbol["600002"]["tradable"] is False
    assert by_symbol["600002"]["_suspended"] is True
    assert by_symbol["600002"]["close"] == pytest.approx(8.0)


def test_streaming_research_backtest_provider_reads_financial_indicators_pit(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    market_db = tmp_path / "quant.duckdb"
    financial_db = tmp_path / "cn_financial_indicators.duckdb"
    conn = duckdb.connect(str(market_db))
    conn.execute(
        """
        CREATE TABLE daily_cn_ochl (
            timestamp TIMESTAMP,
            symbol VARCHAR,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume BIGINT,
            turnover DOUBLE,
            adj_open DOUBLE,
            adj_high DOUBLE,
            adj_low DOUBLE,
            adj_close DOUBLE,
            adj_factor DOUBLE
        )
        """
    )
    conn.execute(
        """
        INSERT INTO daily_cn_ochl VALUES
        ('2024-04-10', '600001', 10, 11, 9, 10.5, 1000, 10500, 10, 11, 9, 10.5, 1),
        ('2024-05-02', '600001', 11, 12, 10, 11.5, 1000, 11500, 11, 12, 10, 11.5, 1)
        """
    )
    conn.close()
    _create_financial_db(duckdb, financial_db)

    from quant.api.research_bp import _DuckDBDailyDateProvider

    provider = _DuckDBDailyDateProvider(
        ["600001"],
        datetime(2024, 4, 10),
        datetime(2024, 5, 2),
        db_path=str(market_db),
        status_db_path=str(tmp_path / "missing_status.duckdb"),
        financial_indicator_db_path=str(financial_db),
        include_daily_basic=False,
        include_financial_indicators=True,
        cache_enabled=False,
    )
    try:
        before = list(provider.get_bars_for_date(datetime(2024, 4, 10)))
        after = list(provider.get_bars_for_date(datetime(2024, 5, 2)))
    finally:
        provider.close()

    assert before[0]["roe"] == pytest.approx(5.0)
    assert before[0]["netprofit_yoy"] == pytest.approx(4.0)
    assert after[0]["roe"] == pytest.approx(10.0)
    assert after[0]["netprofit_yoy"] == pytest.approx(12.0)


def test_research_market_data_reads_financial_indicators_pit(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    market_db = tmp_path / "quant.duckdb"
    financial_db = tmp_path / "cn_financial_indicators.duckdb"
    conn = duckdb.connect(str(market_db))
    conn.execute(
        """
        CREATE TABLE daily_cn_ochl (
            timestamp TIMESTAMP,
            symbol VARCHAR,
            close DOUBLE
        )
        """
    )
    conn.execute(
        """
        INSERT INTO daily_cn_ochl VALUES
        ('2024-04-10', '600001', 10.5),
        ('2024-05-02', '600001', 11.5)
        """
    )
    conn.close()
    _create_financial_db(duckdb, financial_db)

    market_data = DuckDBResearchMarketData(
        str(market_db),
        daily_basic_db_path=str(tmp_path / "missing_basic.duckdb"),
        financial_indicator_db_path=str(financial_db),
    )

    fields = market_data.available_fields("cn")
    frame = market_data.get_daily_bars(["600001"], "2024-04-10", "2024-05-02", fields=["close", "roe", "netprofit_yoy"])

    assert "roe" in fields
    assert frame["roe"].tolist() == pytest.approx([5.0, 10.0])
    assert frame["netprofit_yoy"].tolist() == pytest.approx([4.0, 12.0])


def test_streaming_research_backtest_provider_reuses_disk_cache(tmp_path, monkeypatch):
    duckdb = pytest.importorskip("duckdb")
    market_db = tmp_path / "quant.duckdb"
    basic_db = tmp_path / "cn_daily_basic.duckdb"
    status_db = tmp_path / "security_status.duckdb"
    cache_dir = tmp_path / "provider_cache"
    _create_market_db(duckdb, market_db)
    _create_basic_db(duckdb, basic_db)
    _create_status_db(duckdb, status_db)

    from quant.api import research_bp

    provider = research_bp._DuckDBDailyDateProvider(
        ["600001", "600002"],
        datetime(2024, 1, 2),
        datetime(2024, 1, 2),
        db_path=str(market_db),
        status_db_path=str(status_db),
        daily_basic_db_path=str(basic_db),
        cache_dir=cache_dir,
        cache_enabled=True,
    )
    try:
        first = list(provider.get_bars_for_date(datetime(2024, 1, 2)))
    finally:
        provider.close()

    assert first
    assert list(cache_dir.glob("chunk_*.pkl"))

    def fail_fetch(*args, **kwargs):
        raise AssertionError("_fetch_frame should not run on cache hit")

    monkeypatch.setattr(research_bp._DuckDBDailyDateProvider, "_fetch_frame", fail_fetch)
    cached_provider = research_bp._DuckDBDailyDateProvider(
        ["600002", "600001"],
        datetime(2024, 1, 2),
        datetime(2024, 1, 2),
        db_path=str(market_db),
        status_db_path=str(status_db),
        daily_basic_db_path=str(basic_db),
        cache_dir=cache_dir,
        cache_enabled=True,
    )
    try:
        second = list(cached_provider.get_bars_for_date(datetime(2024, 1, 2)))
    finally:
        cached_provider.close()

    assert {bar["symbol"] for bar in second} == {"600001", "600002"}
    first_by_symbol = {bar["symbol"]: bar for bar in first}
    second_by_symbol = {bar["symbol"]: bar for bar in second}
    assert second_by_symbol["600001"]["total_mv"] == pytest.approx(first_by_symbol["600001"]["total_mv"])
    assert second_by_symbol["600002"]["_suspended"] is first_by_symbol["600002"]["_suspended"]
    assert second_by_symbol["600002"]["close"] == pytest.approx(first_by_symbol["600002"]["close"])


def test_streaming_research_backtest_provider_cache_key_tracks_source_files(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    market_db = tmp_path / "quant.duckdb"
    basic_db = tmp_path / "cn_daily_basic.duckdb"
    status_db = tmp_path / "security_status.duckdb"
    cache_dir = tmp_path / "provider_cache"
    _create_market_db(duckdb, market_db)
    _create_basic_db(duckdb, basic_db)
    _create_status_db(duckdb, status_db)

    from quant.api.research_bp import _DuckDBDailyDateProvider

    provider = _DuckDBDailyDateProvider(
        ["600001", "600002"],
        datetime(2024, 1, 2),
        datetime(2024, 1, 2),
        db_path=str(market_db),
        status_db_path=str(status_db),
        daily_basic_db_path=str(basic_db),
        cache_dir=cache_dir,
        cache_enabled=True,
    )
    try:
        dates = provider._trading_dates_list
        before_path = provider._chunk_cache_path(dates, dates[0], dates[-1])
    finally:
        provider.close()

    conn = duckdb.connect(str(market_db))
    conn.execute("INSERT INTO daily_cn_ochl VALUES ('2024-01-03', '600001', 11, 12, 10, 11.5, 1000, 11500, 11, 12, 10, 11.5, 1)")
    conn.close()

    provider = _DuckDBDailyDateProvider(
        ["600001", "600002"],
        datetime(2024, 1, 2),
        datetime(2024, 1, 2),
        db_path=str(market_db),
        status_db_path=str(status_db),
        daily_basic_db_path=str(basic_db),
        cache_dir=cache_dir,
        cache_enabled=True,
    )
    try:
        dates = provider._trading_dates_list
        after_path = provider._chunk_cache_path(dates, dates[0], dates[-1])
    finally:
        provider.close()

    assert before_path != after_path


def test_cn_survivorship_audit_flags_missing_low_price_ohlc_symbols(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    market_db = tmp_path / "quant.duckdb"
    basic_db = tmp_path / "cn_daily_basic.duckdb"
    status_db = tmp_path / "security_status.duckdb"

    conn = duckdb.connect(str(market_db))
    conn.execute(
        """
        CREATE TABLE daily_cn_ochl (
            timestamp TIMESTAMP,
            symbol VARCHAR,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume BIGINT,
            turnover DOUBLE,
            adj_open DOUBLE,
            adj_high DOUBLE,
            adj_low DOUBLE,
            adj_close DOUBLE,
            adj_factor DOUBLE
        )
        """
    )
    conn.executemany(
        "INSERT INTO daily_cn_ochl VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("2024-01-02", "600001", 10, 10, 10, 10, 1000, 10000, 10, 10, 10, 10, 1),
            ("2024-01-02", "600002", 12, 12, 12, 12, 1000, 12000, 12, 12, 12, 12, 1),
        ],
    )
    conn.close()

    conn = duckdb.connect(str(basic_db))
    conn.execute(
        """
        CREATE TABLE cn_daily_basic (
            trade_date DATE,
            symbol VARCHAR,
            total_mv DOUBLE,
            total_share DOUBLE,
            circ_mv DOUBLE,
            float_share DOUBLE
        )
        """
    )
    conn.executemany(
        "INSERT INTO cn_daily_basic VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("2024-01-02", "600001", 200000, 20000, 200000, 20000),
            ("2024-01-02", "600002", 300000, 25000, 300000, 25000),
            ("2024-01-02", "000005", 50000, 50000, 50000, 50000),
        ],
    )
    conn.close()

    conn = duckdb.connect(str(status_db))
    conn.execute(
        """
        CREATE TABLE cn_security_status_daily (
            symbol VARCHAR,
            trade_date DATE,
            is_trade_day BOOLEAN,
            is_listed BOOLEAN,
            list_status VARCHAR,
            is_st BOOLEAN,
            st_type VARCHAR,
            is_suspended BOOLEAN,
            suspend_type VARCHAR,
            suspend_timing VARCHAR,
            has_daily_bar BOOLEAN,
            pre_close DOUBLE,
            up_limit DOUBLE,
            down_limit DOUBLE,
            tradable BOOLEAN,
            source VARCHAR,
            updated_at TIMESTAMP WITH TIME ZONE
        )
        """
    )
    conn.executemany(
        "INSERT INTO cn_security_status_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("600001", "2024-01-02", True, True, "L", False, "", False, "", "", True, 10.0, 11.0, 9.0, True, "fixture", "2024-01-03 00:00:00+00"),
            ("600002", "2024-01-02", True, True, "L", False, "", False, "", "", True, 12.0, 13.2, 10.8, True, "fixture", "2024-01-03 00:00:00+00"),
        ],
    )
    conn.close()

    from quant.api.research_bp import _cn_survivorship_audit

    storage = DuckDBStorage(
        str(market_db),
        read_only=True,
        use_security_status=True,
        status_db_path=str(status_db),
        daily_basic_db_path=str(basic_db),
    )
    try:
        audit = _cn_survivorship_audit(
            storage,
            datetime(2024, 1, 2),
            datetime(2024, 1, 2),
            formula_key="joinquant_small_cap_low_price_factor",
        )
    finally:
        storage.close()

    assert audit["material"] is True
    assert audit["daily_basic_not_ohlc_symbols"] == 1
    assert audit["missing_low_price_symbols_excluding_920"] == 1
    assert audit["missing_symbols_below_top20_excluding_920"] == 1
    assert audit["sample_missing_symbols"][0]["symbol"] == "000005"


def test_security_status_builder_expands_delisted_stock_basic_symbols(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    market_db = tmp_path / "quant.duckdb"
    status_db = tmp_path / "security_status.duckdb"
    _create_market_db(duckdb, market_db)

    import importlib.util
    from pathlib import Path

    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_cn_security_status.py"
    spec = importlib.util.spec_from_file_location("build_cn_security_status", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _build_status_table = module._build_status_table
    _create_stage_tables = module._create_stage_tables

    conn = duckdb.connect(str(status_db))
    conn.execute(f"ATTACH '{market_db}' AS market (READ_ONLY)")
    _create_stage_tables(conn)
    conn.executemany(
        "INSERT INTO stage_trade_cal VALUES (?, ?)",
        [
            ("2024-03-04", True),
            ("2024-03-05", True),
            ("2024-03-06", True),
        ],
    )
    conn.execute(
        """
        INSERT INTO stage_stock_basic VALUES
        ('000005', '000005.SZ', 'ST星源', '1990-01-01', '2024-03-05', 'D')
        """
    )
    _build_status_table(conn, datetime(2024, 3, 4).date(), datetime(2024, 3, 6).date(), include_limits=False)
    rows = conn.execute(
        """
        SELECT trade_date, is_listed, list_status, is_suspended, has_daily_bar, tradable
        FROM cn_security_status_daily
        WHERE symbol = '000005'
        ORDER BY trade_date
        """
    ).fetchall()
    conn.close()

    assert rows == [
        (datetime(2024, 3, 4).date(), True, "L", True, False, False),
        (datetime(2024, 3, 5).date(), True, "L", True, False, False),
        (datetime(2024, 3, 6).date(), False, "D", True, False, False),
    ]
