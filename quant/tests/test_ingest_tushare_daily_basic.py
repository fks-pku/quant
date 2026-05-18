from datetime import date

import pandas as pd
import pytest

from quant.scripts.ingest_tushare_daily_basic import (
    _ensure_basic_schema,
    _normalize_daily_basic,
    _upsert_daily_basic,
    ingest_tushare_daily_basic,
)
from quant.scripts.backfill_missing_cn_ohlc import missing_ohlc_tasks


def test_normalize_daily_basic_maps_symbol_dates_and_numbers():
    raw = pd.DataFrame(
        {
            "ts_code": ["600001.SH", "000001.SZ"],
            "trade_date": ["20240102", "20240102"],
            "total_mv": ["12345.5", "bad"],
            "circ_mv": ["6789.0", "2222.0"],
            "turnover_rate": ["1.2", "3.4"],
        }
    )

    frame = _normalize_daily_basic(raw)

    assert frame["symbol"].tolist() == ["000001", "600001"]
    assert frame["trade_date"].tolist() == [date(2024, 1, 2), date(2024, 1, 2)]
    assert pd.isna(frame.loc[0, "total_mv"])
    assert frame.loc[1, "total_mv"] == pytest.approx(12345.5)
    assert frame.loc[1, "circ_mv"] == pytest.approx(6789.0)


def test_ensure_basic_schema_creates_sidecar_table(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    basic_db = tmp_path / "cn_daily_basic.duckdb"
    conn = duckdb.connect(str(basic_db))
    try:
        _ensure_basic_schema(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info('cn_daily_basic')").fetchall()}
    finally:
        conn.close()

    assert {"trade_date", "symbol", "total_mv", "circ_mv", "turnover_rate_f", "free_share"}.issubset(columns)


def test_upsert_daily_basic_writes_sidecar_rows(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    basic_db = tmp_path / "cn_daily_basic.duckdb"
    conn = duckdb.connect(str(basic_db))
    try:
        _ensure_basic_schema(conn)
        frame = _normalize_daily_basic(
            pd.DataFrame(
                {
                    "ts_code": ["600001.SH"],
                    "trade_date": ["20240102"],
                    "total_mv": [1000.0],
                    "circ_mv": [800.0],
                    "turnover_rate_f": [2.5],
                    "free_share": [300.0],
                }
            )
        )

        inserted = _upsert_daily_basic(conn, frame, date(2024, 1, 2))
        rows = conn.execute(
            """
            SELECT symbol, total_mv, circ_mv, turnover_rate_f, free_share
            FROM cn_daily_basic
            ORDER BY symbol
            """
        ).fetchall()
    finally:
        conn.close()

    assert inserted == 1
    assert rows == [("600001", 1000.0, 800.0, 2.5, 300.0)]


def test_ingest_dry_run_does_not_add_columns_to_market_db(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    market_db = tmp_path / "quant.duckdb"
    basic_db = tmp_path / "cn_daily_basic.duckdb"
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
        market_conn.execute("INSERT INTO daily_cn_ochl VALUES ('2024-01-02', '600001', 10.0)")
    finally:
        market_conn.close()

    summary = ingest_tushare_daily_basic(
        market_db_path=market_db,
        basic_db_path=basic_db,
        start_date="2024-01-02",
        end_date="2024-01-02",
        dry_run=True,
    )

    market_conn = duckdb.connect(str(market_db), read_only=True)
    basic_conn = duckdb.connect(str(basic_db), read_only=True)
    try:
        market_columns = {row[1] for row in market_conn.execute("PRAGMA table_info('daily_cn_ochl')").fetchall()}
        basic_exists = basic_conn.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_name = 'cn_daily_basic'
            """
        ).fetchone()[0]
    finally:
        basic_conn.close()
        market_conn.close()

    assert "total_mv" not in market_columns
    assert "circ_mv" not in market_columns
    assert basic_exists == 1
    assert summary.coverage["bar_rows"] == 1


def test_missing_ohlc_tasks_uses_daily_basic_ranges(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    market_db = tmp_path / "quant.duckdb"
    basic_db = tmp_path / "cn_daily_basic.duckdb"

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
        market_conn.execute("INSERT INTO daily_cn_ochl VALUES ('2024-01-02', '600001', 10.0)")
    finally:
        market_conn.close()

    basic_conn = duckdb.connect(str(basic_db))
    try:
        basic_conn.execute(
            """
            CREATE TABLE cn_daily_basic (
                trade_date DATE,
                symbol VARCHAR,
                ts_code VARCHAR
            )
            """
        )
        basic_conn.executemany(
            "INSERT INTO cn_daily_basic VALUES (?, ?, ?)",
            [
                ("2024-01-02", "600001", "600001.SH"),
                ("2024-01-02", "600002", "600002.SH"),
                ("2024-01-03", "600002", "600002.SH"),
                ("2024-01-02", "920000", "920000.BJ"),
            ],
        )
    finally:
        basic_conn.close()

    tasks = missing_ohlc_tasks(market_db_path=market_db, basic_db_path=basic_db)

    assert len(tasks) == 1
    assert tasks[0].symbol == "600002"
    assert tasks[0].start == date(2024, 1, 2)
    assert tasks[0].end == date(2024, 1, 3)
    assert tasks[0].daily_basic_rows == 2
