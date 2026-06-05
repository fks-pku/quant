import json
from datetime import date, datetime
from pathlib import Path

import duckdb

from quant.infrastructure.execution.cn_trading_calendar import (
    expected_market_data_date,
    is_open_trading_day,
    latest_two_data_dates,
    next_trading_date_after,
)


def test_cn_trading_calendar_uses_cached_real_holiday_window(tmp_path):
    cache_path = tmp_path / "cn_trade_calendar_sse.json"
    _write_calendar_cache(cache_path, {
        "2024-09-30": True,
        "2024-10-01": False,
        "2024-10-02": False,
        "2024-10-03": False,
        "2024-10-04": False,
        "2024-10-05": False,
        "2024-10-06": False,
        "2024-10-07": False,
        "2024-10-08": True,
    })

    assert is_open_trading_day(
        date(2024, 10, 1),
        cache_path=cache_path,
        duckdb_dir=tmp_path,
        allow_refresh=False,
    ) is False
    assert next_trading_date_after(
        date(2024, 9, 30),
        cache_path=cache_path,
        duckdb_dir=tmp_path,
        allow_refresh=False,
    ) == date(2024, 10, 8)
    assert expected_market_data_date(
        datetime(2024, 10, 7, 23, 0, 0),
        cache_path=cache_path,
        duckdb_dir=tmp_path,
        allow_refresh=False,
    ) == date(2024, 9, 30)


def test_cn_trading_calendar_latest_two_data_dates_use_common_available_sources(tmp_path):
    duckdb_dir = tmp_path / "duckdb" / "live"
    _write_daily_dates(duckdb_dir / "cn_ohlcv.duckdb", ["2026-06-03", "2026-06-04", "2026-06-05"])
    _write_daily_dates(duckdb_dir / "cn_etf_ohlcv.duckdb", ["2026-06-03", "2026-06-04"])
    _write_daily_dates(duckdb_dir / "cn_index_ohlcv.duckdb", ["2026-06-03", "2026-06-04", "2026-06-05"])

    signal_date, execution_date = latest_two_data_dates(duckdb_dir=duckdb_dir)

    assert signal_date == date(2026, 6, 3)
    assert execution_date == date(2026, 6, 4)


def _write_calendar_cache(path: Path, rows: dict[str, bool]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"exchange": "SSE", "rows": rows}, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_daily_dates(path: Path, dates: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            CREATE TABLE daily_cn_ochl (
                timestamp TIMESTAMP,
                symbol VARCHAR,
                open DOUBLE,
                close DOUBLE
            )
            """
        )
        con.executemany(
            "INSERT INTO daily_cn_ochl VALUES (?, ?, ?, ?)",
            [(value, "000001", 1.0, 1.0) for value in dates],
        )
    finally:
        con.close()
