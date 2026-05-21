from datetime import date

import pandas as pd
import pytest

from quant.scripts.ingest_tushare_financial_indicators import (
    _ensure_financial_schema,
    _normalize_financial_indicators,
    _upsert_financial_indicators,
    ingest_tushare_financial_indicators,
)


class _FakeClient:
    def call(self, name, **kwargs):
        assert name == "fina_indicator"
        assert kwargs["period"] == "20240331"
        return pd.DataFrame(
            {
                "ts_code": ["600001.SH", "000001.SZ"],
                "ann_date": ["20240430", "20240425"],
                "end_date": ["20240331", "20240331"],
                "roe": ["10.5", "bad"],
                "netprofit_yoy": ["12.0", "8.0"],
                "debt_to_assets": ["40.0", "50.0"],
            }
        )


def test_normalize_financial_indicators_maps_dates_symbols_and_numbers():
    raw = pd.DataFrame(
        {
            "ts_code": ["600001.SH", "000001.SZ"],
            "ann_date": ["20240430", "20240425"],
            "end_date": ["20240331", "20240331"],
            "roe": ["10.5", "bad"],
            "netprofit_yoy": ["12.0", "8.0"],
        }
    )

    frame = _normalize_financial_indicators(raw)

    assert frame["symbol"].tolist() == ["000001", "600001"]
    assert frame["ann_date"].tolist() == [date(2024, 4, 25), date(2024, 4, 30)]
    assert frame["end_date"].tolist() == [date(2024, 3, 31), date(2024, 3, 31)]
    assert pd.isna(frame.loc[0, "roe"])
    assert frame.loc[1, "roe"] == pytest.approx(10.5)


def test_ensure_financial_schema_creates_pit_table(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    financial_db = tmp_path / "cn_financial_indicators.duckdb"
    conn = duckdb.connect(str(financial_db))
    try:
        _ensure_financial_schema(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info('cn_financial_indicators')").fetchall()}
    finally:
        conn.close()

    assert {"symbol", "ann_date", "end_date", "roe", "netprofit_yoy", "debt_to_assets"}.issubset(columns)


def test_upsert_financial_indicators_writes_rows(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    financial_db = tmp_path / "cn_financial_indicators.duckdb"
    conn = duckdb.connect(str(financial_db))
    try:
        _ensure_financial_schema(conn)
        frame = _normalize_financial_indicators(
            pd.DataFrame(
                {
                    "ts_code": ["600001.SH"],
                    "ann_date": ["20240430"],
                    "end_date": ["20240331"],
                    "roe": [10.5],
                    "netprofit_yoy": [12.0],
                    "debt_to_assets": [40.0],
                }
            )
        )
        inserted = _upsert_financial_indicators(conn, frame, end_date=date(2024, 3, 31))
        rows = conn.execute(
            """
            SELECT symbol, ann_date, end_date, roe, netprofit_yoy, debt_to_assets
            FROM cn_financial_indicators
            ORDER BY symbol
            """
        ).fetchall()
    finally:
        conn.close()

    assert inserted == 1
    assert rows == [("600001", date(2024, 4, 30), date(2024, 3, 31), 10.5, 12.0, 40.0)]


def test_ingest_financial_indicators_period_mode_with_fake_client(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    market_db = tmp_path / "cn_ohlcv.duckdb"
    financial_db = tmp_path / "cn_financial_indicators.duckdb"
    market_conn = duckdb.connect(str(market_db))
    try:
        market_conn.execute(
            """
            CREATE TABLE daily_cn_ochl (
                timestamp TIMESTAMP,
                symbol VARCHAR,
                close DOUBLE
            )
            """
        )
        market_conn.execute("INSERT INTO daily_cn_ochl VALUES ('2024-03-31', '600001', 10.0)")
    finally:
        market_conn.close()

    summary = ingest_tushare_financial_indicators(
        market_db_path=market_db,
        financial_db_path=financial_db,
        start_date="2024-03-31",
        end_date="2024-03-31",
        client=_FakeClient(),
    )

    conn = duckdb.connect(str(financial_db), read_only=True)
    try:
        rows = conn.execute("SELECT symbol, roe FROM cn_financial_indicators ORDER BY symbol").fetchall()
    finally:
        conn.close()

    assert summary.fetched_items == 1
    assert summary.fetched_rows == 1
    assert rows == [("600001", 10.5)]
