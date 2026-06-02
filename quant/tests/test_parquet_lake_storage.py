from datetime import datetime, date
import json

import pandas as pd
import pytest

from quant.infrastructure.data.parquet_lake_storage import ParquetLakeStorage
from quant.infrastructure.data.storage_duckdb import DuckDBStorage
from quant.scripts.update_cn_live_data import _copy_duckdb_range_to_lake


def test_parquet_lake_storage_upserts_stock_bars_and_duckdb_reads_views(tmp_path):
    pytest.importorskip("duckdb")
    lake = tmp_path / "lake"
    storage = ParquetLakeStorage(lake)
    frame = pd.DataFrame(
        [
            {
                "timestamp": datetime(2024, 1, 2),
                "symbol": "600519",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 1000,
            }
        ]
    )
    storage.save_bars(frame, "1d")
    replacement = frame.copy()
    replacement["close"] = 12.5
    storage.save_bars(replacement, "1d")
    storage.close()

    assert (lake / "stock_ohlcv" / "year=2024" / "month=01" / "day=02" / "data.parquet").exists()
    manifest = json.loads((lake / "_manifest.json").read_text(encoding="utf-8"))
    stock = next(item for item in manifest["datasets"] if item["name"] == "stock_ohlcv")
    assert stock["rows"] == 1

    reader = DuckDBStorage(
        str(tmp_path / "missing.duckdb"),
        read_only=True,
        parquet_lake_root=str(lake),
        prefer_parquet_lake=True,
    )
    try:
        bars = reader.get_bars("600519", datetime(2024, 1, 2), datetime(2024, 1, 2), "1d")
    finally:
        reader.close()

    assert len(bars) == 1
    assert bars["close"].iloc[0] == pytest.approx(12.5)


def test_parquet_lake_storage_routes_etf_and_index_bars(tmp_path):
    pytest.importorskip("duckdb")
    lake = tmp_path / "lake"
    storage = ParquetLakeStorage(lake)
    common = {
        "timestamp": datetime(2024, 1, 2),
        "open": 1.0,
        "high": 1.1,
        "low": 0.9,
        "close": 1.0,
        "volume": 1000,
    }
    storage.save_bars(pd.DataFrame([{**common, "symbol": "510300"}]), "1d")
    storage.save_cn_index_bars(pd.DataFrame([{**common, "symbol": "000300"}]), "1d")
    storage.close()

    reader = DuckDBStorage(
        str(tmp_path / "missing.duckdb"),
        read_only=True,
        parquet_lake_root=str(lake),
        prefer_parquet_lake=True,
    )
    try:
        bars = reader.get_bars_for_symbols(["510300", "000300"], datetime(2024, 1, 2), datetime(2024, 1, 2), "1d")
    finally:
        reader.close()

    assert set(bars["symbol"]) == {"510300", "000300"}


def test_parquet_lake_dividends_replace_symbol_history(tmp_path):
    pytest.importorskip("duckdb")
    lake = tmp_path / "lake"
    storage = ParquetLakeStorage(lake)
    storage.save_cn_dividends(
        pd.DataFrame(
            [
                {"symbol": "000002", "ex_date": date(2024, 6, 3), "cash_dividend": 0.1},
                {"symbol": "000002", "ex_date": date(2024, 7, 1), "cash_dividend": 0.2},
            ]
        )
    )
    storage.save_cn_dividends(pd.DataFrame([{"symbol": "000002", "ex_date": date(2024, 6, 3), "cash_dividend": 0.3}]))
    storage.close()

    reader = DuckDBStorage(
        str(tmp_path / "missing.duckdb"),
        read_only=True,
        parquet_lake_root=str(lake),
        prefer_parquet_lake=True,
    )
    try:
        dividends = reader.get_cn_dividends("000002")
    finally:
        reader.close()

    assert len(dividends) == 1
    assert dividends["cash_dividend"].iloc[0] == pytest.approx(0.3)


def test_parquet_lake_sync_touched_partitions(monkeypatch, tmp_path):
    pytest.importorskip("duckdb")
    commands = []

    def fake_run(command, check):
        commands.append(command)
        assert check is True

    monkeypatch.setattr("quant.infrastructure.data.parquet_lake_storage.subprocess.run", fake_run)
    lake = tmp_path / "lake"
    storage = ParquetLakeStorage(lake)
    storage.save_bars(
        pd.DataFrame(
            [
                {
                    "timestamp": datetime(2024, 1, 2),
                    "symbol": "600519",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "volume": 1000,
                }
            ]
        ),
        "1d",
    )

    storage.sync_touched("oss:bucket/prefix")
    storage.close()

    partition = lake / "stock_ohlcv" / "year=2024" / "month=01" / "day=02"
    assert commands == [
        [
            "rclone",
            "sync",
            str(partition),
            "oss:bucket/prefix/stock_ohlcv/year=2024/month=01/day=02",
            "--progress",
            "--transfers",
            "16",
            "--checkers",
            "32",
        ],
        ["rclone", "copyto", "--s3-no-check-bucket", str(lake / "_manifest.json"), "oss:bucket/prefix/_manifest.json"],
    ]


def test_copy_duckdb_sidecar_range_to_lake(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    db_path = tmp_path / "daily_basic.duckdb"
    conn = duckdb.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE cn_daily_basic(symbol VARCHAR, trade_date DATE, total_mv DOUBLE)")
        conn.execute(
            """
            INSERT INTO cn_daily_basic VALUES
            ('600519', '2024-01-02', 100.0),
            ('600519', '2024-01-03', 110.0)
            """
        )
    finally:
        conn.close()

    storage = ParquetLakeStorage(tmp_path / "lake")
    rows = _copy_duckdb_range_to_lake(
        storage,
        db_path,
        "cn_daily_basic",
        "daily_basic",
        "trade_date",
        date(2024, 1, 3),
        date(2024, 1, 3),
    )
    storage.close()

    assert rows == 1
    assert (tmp_path / "lake" / "daily_basic" / "year=2024" / "month=01" / "day=03" / "data.parquet").exists()
