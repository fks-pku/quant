import argparse
import logging
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

import duckdb
import pandas as pd

_PKG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG_DIR.parent))

from quant.shared.utils.config_loader import ConfigLoader

try:
    import tushare as ts
except ImportError:
    ts = None


logger = logging.getLogger("build_cn_security_status")

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MARKET_DB = ROOT / "quant" / "infrastructure" / "var" / "duckdb" / "quant.duckdb"
DEFAULT_SECURITY_DB = ROOT / "quant" / "infrastructure" / "var" / "duckdb" / "security_status.duckdb"
DAILY_CN_TABLE = "daily_cn_ochl"
STATUS_TABLE = "cn_security_status_daily"
NON_SH_SZ_PREFIXES = ("920", "8", "4")


class TushareClient:
    def __init__(self, min_interval: float = 0.25, retries: int = 3):
        if ts is None:
            raise RuntimeError("tushare is not installed")
        cfg = ConfigLoader().load("config.yaml")
        token = cfg.get("data", {}).get("tushare", {}).get("token", "")
        api_url = cfg.get("data", {}).get("tushare", {}).get("api_url", "")
        if not token:
            raise RuntimeError("tushare token is not configured")
        self.api = ts.pro_api(token=token)
        if api_url:
            self.api._DataApi__http_url = api_url
        self.min_interval = min_interval
        self.retries = retries
        self._last_request = 0.0

    def call(self, name: str, **kwargs: Any) -> pd.DataFrame:
        last_error: Optional[Exception] = None
        for attempt in range(self.retries):
            self._rate_limit()
            try:
                frame = getattr(self.api, name)(**kwargs)
                if frame is None:
                    return pd.DataFrame()
                return frame
            except Exception as exc:
                last_error = exc
                sleep_s = min(2.0 * (attempt + 1), 10.0)
                logger.warning("Tushare %s failed on attempt %s/%s: %s", name, attempt + 1, self.retries, exc)
                time.sleep(sleep_s)
        raise RuntimeError(f"Tushare {name} failed") from last_error

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request = time.time()


def build_cn_security_status(
    market_db: Path = DEFAULT_MARKET_DB,
    security_db: Path = DEFAULT_SECURITY_DB,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    min_interval: float = 0.25,
    skip_limits: bool = False,
) -> None:
    security_db.parent.mkdir(parents=True, exist_ok=True)
    client = TushareClient(min_interval=min_interval)
    conn = duckdb.connect(str(security_db))
    try:
        _attach_market_db(conn, market_db)
        start, end = _resolve_date_range(conn, start_date, end_date)
        logger.info("Building %s for %s to %s", STATUS_TABLE, start, end)
        _create_stage_tables(conn)
        _load_trade_calendar(conn, client, start, end)
        _load_stock_basic(conn, client)
        _load_namechange(conn, client)
        _load_suspend(conn, client, start, end)
        if not skip_limits:
            _load_stk_limit(conn, client, start, end)
        _build_status_table(conn, start, end, include_limits=not skip_limits)
        _log_summary(conn)
    finally:
        conn.close()


def _attach_market_db(conn: duckdb.DuckDBPyConnection, market_db: Path) -> None:
    path = str(market_db).replace("'", "''")
    conn.execute(f"ATTACH '{path}' AS market (READ_ONLY)")
    exists = conn.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_catalog = 'market'
          AND table_name = ?
        """,
        [DAILY_CN_TABLE],
    ).fetchone()[0]
    if not exists:
        raise RuntimeError(f"market DB does not contain {DAILY_CN_TABLE}")


def _resolve_date_range(
    conn: duckdb.DuckDBPyConnection,
    start_date: Optional[str],
    end_date: Optional[str],
) -> Tuple[date, date]:
    row = conn.execute(
        f"""
        SELECT MIN(CAST(timestamp AS DATE)), MAX(CAST(timestamp AS DATE))
        FROM market.{DAILY_CN_TABLE}
        """
    ).fetchone()
    if row is None or row[0] is None or row[1] is None:
        raise RuntimeError(f"market.{DAILY_CN_TABLE} has no rows")
    start = _parse_date(start_date) if start_date else row[0]
    end = _parse_date(end_date) if end_date else row[1]
    return start, end


def _create_stage_tables(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE stage_trade_cal (
            trade_date DATE,
            is_open BOOLEAN
        )
        """
    )
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE stage_stock_basic (
            symbol VARCHAR,
            ts_code VARCHAR,
            name VARCHAR,
            list_date DATE,
            delist_date DATE,
            list_status VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE stage_namechange (
            symbol VARCHAR,
            ts_code VARCHAR,
            name VARCHAR,
            start_date DATE,
            end_date DATE,
            change_reason VARCHAR,
            is_st BOOLEAN,
            st_type VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE stage_suspend_d (
            symbol VARCHAR,
            ts_code VARCHAR,
            trade_date DATE,
            suspend_timing VARCHAR,
            suspend_type VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE stage_stk_limit (
            symbol VARCHAR,
            ts_code VARCHAR,
            trade_date DATE,
            up_limit DOUBLE,
            down_limit DOUBLE
        )
        """
    )


def _load_trade_calendar(conn: duckdb.DuckDBPyConnection, client: TushareClient, start: date, end: date) -> None:
    frames = []
    for chunk_start, chunk_end in _day_ranges(start, end, 2500):
        chunk = client.call(
            "trade_cal",
            exchange="SSE",
            start_date=_fmt_date(chunk_start),
            end_date=_fmt_date(chunk_end),
            fields="cal_date,is_open",
        )
        if not chunk.empty:
            frames.append(chunk)
    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if frame.empty:
        frame = conn.execute(
            f"""
            SELECT DISTINCT CAST(timestamp AS DATE) AS trade_date, TRUE AS is_open
            FROM market.{DAILY_CN_TABLE}
            WHERE CAST(timestamp AS DATE) BETWEEN ? AND ?
            """,
            [start, end],
        ).fetchdf()
    else:
        frame = frame.rename(columns={"cal_date": "trade_date"})
        frame["trade_date"] = _date_series(frame["trade_date"])
        frame["is_open"] = pd.to_numeric(frame["is_open"], errors="coerce").fillna(0).astype(int) == 1
        frame = frame[["trade_date", "is_open"]].dropna(subset=["trade_date"]).drop_duplicates()
        frame = frame[(frame["trade_date"] >= start) & (frame["trade_date"] <= end)]
    _insert_frame(conn, "stage_trade_cal", frame)
    logger.info("Loaded trade calendar rows: %s", len(frame))


def _load_stock_basic(conn: duckdb.DuckDBPyConnection, client: TushareClient) -> None:
    frames = []
    for status in ("L", "D", "P"):
        frame = client.call(
            "stock_basic",
            exchange="",
            list_status=status,
            fields="ts_code,symbol,name,list_date,delist_date,list_status",
        )
        if not frame.empty:
            frames.append(frame)
    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not frame.empty:
        frame["ts_code"] = frame["ts_code"].astype(str)
        frame["symbol"] = frame["ts_code"].map(_symbol_from_ts_code)
        frame = frame[~frame["symbol"].fillna("").astype(str).str.startswith(NON_SH_SZ_PREFIXES)].copy()
        frame["list_date"] = _date_series(frame.get("list_date"))
        frame["delist_date"] = _date_series(frame.get("delist_date"))
        frame = frame[["symbol", "ts_code", "name", "list_date", "delist_date", "list_status"]]
        frame = frame.dropna(subset=["symbol"]).drop_duplicates(subset=["ts_code", "list_status"], keep="last")
    _insert_frame(conn, "stage_stock_basic", frame)
    logger.info("Loaded stock_basic rows: %s", len(frame))


def _load_namechange(conn: duckdb.DuckDBPyConnection, client: TushareClient) -> None:
    frame = client.call(
        "namechange",
        ts_code="",
        fields="ts_code,name,start_date,end_date,change_reason",
    )
    if not frame.empty:
        frame["ts_code"] = frame["ts_code"].astype(str)
        frame["symbol"] = frame["ts_code"].map(_symbol_from_ts_code)
        frame = frame[~frame["symbol"].fillna("").astype(str).str.startswith(NON_SH_SZ_PREFIXES)].copy()
        frame["start_date"] = _date_series(frame.get("start_date"))
        frame["end_date"] = _date_series(frame.get("end_date"))
        frame["name"] = frame["name"].fillna("").astype(str)
        frame["change_reason"] = frame["change_reason"].fillna("").astype(str)
        frame["st_type"] = frame.apply(lambda row: _st_type(row["name"], row["change_reason"]), axis=1)
        frame["is_st"] = frame["st_type"] != ""
        frame = frame[
            ["symbol", "ts_code", "name", "start_date", "end_date", "change_reason", "is_st", "st_type"]
        ].dropna(subset=["symbol", "start_date"])
        frame = frame.drop_duplicates()
    _insert_frame(conn, "stage_namechange", frame)
    logger.info("Loaded namechange rows: %s", len(frame))


def _load_suspend(conn: duckdb.DuckDBPyConnection, client: TushareClient, start: date, end: date) -> None:
    total = 0
    for chunk_start, chunk_end in _month_ranges(start, end):
        frame = client.call(
            "suspend_d",
            start_date=_fmt_date(chunk_start),
            end_date=_fmt_date(chunk_end),
            fields="ts_code,trade_date,suspend_timing,suspend_type",
        )
        if frame.empty:
            continue
        frame["ts_code"] = frame["ts_code"].astype(str)
        frame["symbol"] = frame["ts_code"].map(_symbol_from_ts_code)
        frame = frame[~frame["symbol"].fillna("").astype(str).str.startswith(NON_SH_SZ_PREFIXES)].copy()
        frame["trade_date"] = _date_series(frame["trade_date"])
        frame["suspend_timing"] = frame["suspend_timing"].fillna("").astype(str)
        frame["suspend_type"] = frame["suspend_type"].fillna("").astype(str)
        frame = frame[["symbol", "ts_code", "trade_date", "suspend_timing", "suspend_type"]].dropna(
            subset=["symbol", "trade_date"]
        )
        frame = frame.drop_duplicates()
        _insert_frame(conn, "stage_suspend_d", frame)
        total += len(frame)
        logger.info("Loaded suspend_d %s-%s rows=%s total=%s", chunk_start, chunk_end, len(frame), total)


def _load_stk_limit(conn: duckdb.DuckDBPyConnection, client: TushareClient, start: date, end: date) -> None:
    total = 0
    for chunk_start, chunk_end in _month_ranges(start, end):
        frame = client.call(
            "stk_limit",
            start_date=_fmt_date(chunk_start),
            end_date=_fmt_date(chunk_end),
            fields="trade_date,ts_code,up_limit,down_limit",
        )
        if frame.empty:
            continue
        frame["ts_code"] = frame["ts_code"].astype(str)
        frame["symbol"] = frame["ts_code"].map(_symbol_from_ts_code)
        frame = frame[~frame["symbol"].fillna("").astype(str).str.startswith(NON_SH_SZ_PREFIXES)].copy()
        frame["trade_date"] = _date_series(frame["trade_date"])
        frame["up_limit"] = pd.to_numeric(frame["up_limit"], errors="coerce")
        frame["down_limit"] = pd.to_numeric(frame["down_limit"], errors="coerce")
        frame = frame[["symbol", "ts_code", "trade_date", "up_limit", "down_limit"]].dropna(
            subset=["symbol", "trade_date"]
        )
        frame = frame.drop_duplicates(subset=["symbol", "trade_date"], keep="last")
        _insert_frame(conn, "stage_stk_limit", frame)
        total += len(frame)
        logger.info("Loaded stk_limit %s-%s rows=%s total=%s", chunk_start, chunk_end, len(frame), total)


def _build_status_table(conn: duckdb.DuckDBPyConnection, start: date, end: date, include_limits: bool) -> None:
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE stage_local_bars AS
        SELECT
            symbol,
            CAST(timestamp AS DATE) AS trade_date,
            close,
            LAG(close) OVER (
                PARTITION BY symbol
                ORDER BY CAST(timestamp AS DATE)
            ) AS pre_close
        FROM market.{DAILY_CN_TABLE}
        WHERE CAST(timestamp AS DATE) BETWEEN ? AND ?
          AND NOT (symbol LIKE '920%' OR symbol LIKE '8%' OR symbol LIKE '4%')
        """,
        [start, end],
    )
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE stage_meta AS
        SELECT symbol, ts_code, name, list_date, delist_date, list_status
        FROM (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY symbol
                    ORDER BY
                        CASE list_status WHEN 'L' THEN 0 WHEN 'P' THEN 1 WHEN 'D' THEN 2 ELSE 3 END,
                        list_date DESC NULLS LAST
                ) AS rn
            FROM stage_stock_basic
        )
        WHERE rn = 1
        """
    )
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE stage_symbol_ranges AS
        WITH local_ranges AS (
            SELECT symbol, MIN(trade_date) AS start_date, MAX(trade_date) AS end_date
            FROM stage_local_bars
            GROUP BY symbol
        ),
        meta_ranges AS (
            SELECT
                symbol,
                GREATEST(COALESCE(list_date, CAST(? AS DATE)), CAST(? AS DATE)) AS start_date,
                CAST(? AS DATE) AS end_date
            FROM stage_meta
            WHERE symbol IS NOT NULL
              AND (list_date IS NULL OR list_date <= CAST(? AS DATE))
              AND (delist_date IS NULL OR delist_date >= CAST(? AS DATE))
        )
        SELECT symbol, MIN(start_date) AS start_date, MAX(end_date) AS end_date
        FROM (
            SELECT * FROM local_ranges
            UNION ALL
            SELECT * FROM meta_ranges
        )
        GROUP BY symbol
        """,
        [start, start, end, end, start],
    )
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE stage_grid AS
        SELECT r.symbol, c.trade_date
        FROM stage_symbol_ranges r
        JOIN (
            SELECT DISTINCT trade_date
            FROM stage_trade_cal
            WHERE is_open = TRUE
        ) c
          ON c.trade_date BETWEEN r.start_date AND r.end_date
        """
    )
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE stage_st_by_day AS
        SELECT
            g.symbol,
            g.trade_date,
            TRUE AS is_st,
            CASE
                WHEN MAX(CASE WHEN n.st_type = '*ST' THEN 2 WHEN n.st_type = 'ST' THEN 1 ELSE 0 END) = 2 THEN '*ST'
                WHEN MAX(CASE WHEN n.st_type = '*ST' THEN 2 WHEN n.st_type = 'ST' THEN 1 ELSE 0 END) = 1 THEN 'ST'
                ELSE ''
            END AS st_type
        FROM stage_grid g
        JOIN stage_namechange n
          ON g.symbol = n.symbol
         AND n.is_st = TRUE
         AND g.trade_date >= n.start_date
         AND g.trade_date <= COALESCE(n.end_date, DATE '9999-12-31')
        GROUP BY g.symbol, g.trade_date
        """
    )
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE stage_suspend_daily AS
        SELECT
            symbol,
            trade_date,
            MAX(suspend_type) AS suspend_type,
            MAX(suspend_timing) AS suspend_timing
        FROM stage_suspend_d
        GROUP BY symbol, trade_date
        """
    )
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE stage_limit_daily AS
        SELECT
            symbol,
            trade_date,
            MAX(up_limit) AS up_limit,
            MAX(down_limit) AS down_limit
        FROM stage_stk_limit
        GROUP BY symbol, trade_date
        """
    )
    limit_select = "l.up_limit, l.down_limit" if include_limits else "CAST(NULL AS DOUBLE) AS up_limit, CAST(NULL AS DOUBLE) AS down_limit"
    limit_join = "LEFT JOIN stage_limit_daily l ON g.symbol = l.symbol AND g.trade_date = l.trade_date" if include_limits else ""
    conn.execute(f"DROP TABLE IF EXISTS {STATUS_TABLE}_new")
    conn.execute(
        f"""
        CREATE TABLE {STATUS_TABLE}_new AS
        WITH joined AS (
            SELECT
                g.symbol,
                g.trade_date,
                TRUE AS is_trade_day,
                (
                    (m.list_date IS NULL OR g.trade_date >= m.list_date)
                    AND (m.delist_date IS NULL OR g.trade_date <= m.delist_date)
                ) AS is_listed,
                CASE
                    WHEN m.delist_date IS NOT NULL AND g.trade_date > m.delist_date THEN 'D'
                    WHEN m.list_date IS NOT NULL AND g.trade_date < m.list_date THEN 'P'
                    ELSE COALESCE(NULLIF(m.list_status, 'D'), 'L')
                END AS list_status,
                COALESCE(st.is_st, FALSE) AS is_st,
                COALESCE(st.st_type, '') AS st_type,
                b.symbol IS NOT NULL AS has_daily_bar,
                b.pre_close,
                {limit_select},
                COALESCE(s.suspend_type, '') AS suspend_type,
                COALESCE(s.suspend_timing, '') AS suspend_timing
            FROM stage_grid g
            LEFT JOIN stage_local_bars b
              ON g.symbol = b.symbol
             AND g.trade_date = b.trade_date
            LEFT JOIN stage_meta m
              ON g.symbol = m.symbol
            LEFT JOIN stage_st_by_day st
              ON g.symbol = st.symbol
             AND g.trade_date = st.trade_date
            LEFT JOIN stage_suspend_daily s
              ON g.symbol = s.symbol
             AND g.trade_date = s.trade_date
            {limit_join}
        )
        SELECT
            symbol,
            trade_date,
            is_trade_day,
            COALESCE(is_listed, TRUE) AS is_listed,
            list_status,
            is_st,
            st_type,
                (NOT has_daily_bar) OR NOT COALESCE(is_listed, TRUE) AS is_suspended,
            suspend_type,
            suspend_timing,
            has_daily_bar,
            pre_close,
            up_limit,
            down_limit,
                has_daily_bar AND COALESCE(is_listed, TRUE) AS tradable,
            'daily_cn_ochl+tushare' AS source,
            CURRENT_TIMESTAMP AS updated_at
        FROM joined
        """
    )
    conn.execute(f"DROP TABLE IF EXISTS {STATUS_TABLE}")
    conn.execute(f"ALTER TABLE {STATUS_TABLE}_new RENAME TO {STATUS_TABLE}")
    conn.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_{STATUS_TABLE}_symbol_date
        ON {STATUS_TABLE}(symbol, trade_date)
        """
    )


def _log_summary(conn: duckdb.DuckDBPyConnection) -> None:
    summary = conn.execute(
        f"""
        SELECT
            COUNT(*) AS row_count,
            COUNT(DISTINCT symbol) AS symbol_count,
            MIN(trade_date) AS start_date,
            MAX(trade_date) AS end_date,
            SUM(CASE WHEN is_st THEN 1 ELSE 0 END) AS st_rows,
            SUM(CASE WHEN is_suspended THEN 1 ELSE 0 END) AS suspended_rows,
            SUM(CASE WHEN tradable THEN 1 ELSE 0 END) AS tradable_rows,
            SUM(CASE WHEN up_limit IS NOT NULL THEN 1 ELSE 0 END) AS limit_rows
        FROM cn_security_status_daily
        """
    ).fetchdf()
    logger.info("\n%s", summary.to_string(index=False))


def _insert_frame(conn: duckdb.DuckDBPyConnection, table: str, frame: pd.DataFrame) -> None:
    if frame is None or frame.empty:
        return
    conn.register("stage_frame", frame)
    try:
        conn.execute(f"INSERT INTO {table} SELECT * FROM stage_frame")
    finally:
        conn.unregister("stage_frame")


def _month_ranges(start: date, end: date) -> Iterable[Tuple[date, date]]:
    periods = pd.period_range(pd.Timestamp(start).to_period("M"), pd.Timestamp(end).to_period("M"), freq="M")
    for period in periods:
        chunk_start = max(period.start_time.date(), start)
        chunk_end = min(period.end_time.date(), end)
        yield chunk_start, chunk_end


def _day_ranges(start: date, end: date, max_days: int) -> Iterable[Tuple[date, date]]:
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min((pd.Timestamp(chunk_start) + pd.Timedelta(days=max_days - 1)).date(), end)
        yield chunk_start, chunk_end
        chunk_start = (pd.Timestamp(chunk_end) + pd.Timedelta(days=1)).date()


def _parse_date(value: str) -> date:
    return pd.Timestamp(value).date()


def _fmt_date(value: date) -> str:
    return pd.Timestamp(value).strftime("%Y%m%d")


def _date_series(values: Any) -> Any:
    if values is None:
        return pd.Series(dtype="datetime64[ns]")
    return pd.to_datetime(values, format="%Y%m%d", errors="coerce").dt.date


def _symbol_from_ts_code(value: Any) -> str:
    return str(value).split(".")[0].zfill(6)


def _st_type(name: Any, change_reason: Any = "") -> str:
    value = str(name)
    if "*ST" in value or "＊ST" in value:
        return "*ST"
    if "ST" in value:
        return "ST"
    reason = str(change_reason).strip()
    if reason in ("*ST", "＊ST"):
        return "*ST"
    if reason == "ST":
        return "ST"
    return ""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-db", type=Path, default=DEFAULT_MARKET_DB)
    parser.add_argument("--security-db", type=Path, default=DEFAULT_SECURITY_DB)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--min-interval", type=float, default=0.25)
    parser.add_argument("--skip-limits", action="store_true")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    args = _parse_args()
    build_cn_security_status(
        market_db=args.market_db,
        security_db=args.security_db,
        start_date=args.start_date,
        end_date=args.end_date,
        min_interval=args.min_interval,
        skip_limits=args.skip_limits,
    )


if __name__ == "__main__":
    main()
