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
        ('2024-01-02', '600001', 10, 11, 9, 10.5, 1000, 10, 11, 9, 10.5, 1)
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
            total_mv DOUBLE,
            circ_mv DOUBLE,
            turnover_rate_f DOUBLE
        )
        """
    )
    conn.execute(
        """
        INSERT INTO cn_daily_basic VALUES
        ('2024-01-02', '600001', 12345, 6789, 2.5)
        """
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
