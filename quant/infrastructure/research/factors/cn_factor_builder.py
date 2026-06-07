import logging
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)
CACHE_TTL_DAYS = 30
CACHE_FACTOR_VERSION = "cn_style_7_v2"
CN_FACTOR_CACHE_FILE = "cn7_daily.parquet"
REQUIRED_CN_FACTOR_COLUMNS = {"date", "MKT", "SIZE", "VALUE", "MOM", "REV", "VOL", "LIQ", "RF"}
LOOKBACK_BUFFER_DAYS = 140


def build_cn3_factors(
    db_path: str,
    start: str,
    end: str,
    cache_dir: Any = None,
    daily_basic_db_path: Any = None,
) -> Optional[pd.DataFrame]:
    path = _cache_dir(cache_dir) / CN_FACTOR_CACHE_FILE
    metadata_path = path.with_suffix(path.suffix + ".meta.json")
    if _cache_usable(path, metadata_path, db_path, start, end):
        return _read_cached(path, start, end)

    try:
        import duckdb

        conn = duckdb.connect(str(db_path), read_only=True)
        try:
            date_column = _date_column(conn)
            if date_column is None:
                return None
            frame = _load_factor_frame(conn, date_column, start, end, daily_basic_db_path)
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"CN factor data unavailable: {e}")
        return None

    factors = _build_factors(frame)
    if factors is None:
        return None
    factors = _filter_factors(factors, start, end)
    if factors.empty:
        return None

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        factors.to_parquet(path)
        _write_metadata(metadata_path, db_path, factors)
    except Exception as e:
        logger.warning(f"CN factor cache write failed: {e}")
    return factors


def _date_column(conn: Any) -> Optional[str]:
    try:
        columns = [row[1] for row in conn.execute("PRAGMA table_info('daily_cn_ochl')").fetchall()]
    except Exception:
        return None
    if "timestamp" in columns:
        return "timestamp"
    if "date" in columns:
        return "date"
    return None


def _load_factor_frame(conn: Any, date_column: str, start: str, end: str, daily_basic_db_path: Any = None) -> pd.DataFrame:
    query_start = _history_start(start)
    market_columns = _market_columns(conn)
    market_select = _market_select_columns(market_columns, date_column)
    basic_path = Path(daily_basic_db_path) if daily_basic_db_path is not None else _default_daily_basic_db_path()
    schema = "daily_basic_factor"
    if basic_path.exists() and _attach_daily_basic(conn, basic_path, schema):
        columns = _daily_basic_columns(conn, schema)
        selected = _daily_basic_select_columns(columns)
        if selected:
            return conn.execute(
                f"""
                SELECT {", ".join(market_select)}, {", ".join(selected)}
                FROM daily_cn_ochl m
                LEFT JOIN {schema}.cn_daily_basic db
                  ON m.symbol = db.symbol
                 AND CAST(m.{date_column} AS DATE) = db.trade_date
                WHERE CAST(m.{date_column} AS DATE) BETWEEN ? AND ?
                ORDER BY CAST(m.{date_column} AS DATE), m.symbol
                """,
                [query_start, end],
            ).fetchdf()
    return conn.execute(
        f"""
        SELECT {", ".join(market_select)}
        FROM daily_cn_ochl m
        WHERE CAST(m.{date_column} AS DATE) BETWEEN ? AND ?
        ORDER BY CAST(m.{date_column} AS DATE), m.symbol
        """,
        [query_start, end],
    ).fetchdf()


def _market_columns(conn: Any) -> set[str]:
    try:
        rows = conn.execute("PRAGMA table_info('daily_cn_ochl')").fetchall()
    except Exception:
        return set()
    return {str(row[1]) for row in rows}


def _market_select_columns(columns: set[str], date_column: str) -> list[str]:
    selected = ["m.symbol AS symbol", f"CAST(m.{date_column} AS DATE) AS date", "m.close AS close"]
    for column in ("adj_close", "volume", "turnover"):
        if column in columns:
            selected.append(f"m.{column} AS {column}")
    return selected


def _attach_daily_basic(conn: Any, path: Path, schema: str) -> bool:
    try:
        escaped = str(path).replace("'", "''")
        conn.execute(f"ATTACH '{escaped}' AS {schema} (READ_ONLY)")
        return True
    except Exception as e:
        logger.warning(f"CN daily_basic sidecar unavailable for factor build: {e}")
        return False


def _daily_basic_columns(conn: Any, schema: str) -> set[str]:
    try:
        rows = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = ? AND table_name = 'cn_daily_basic'
            """,
            [schema],
        ).fetchall()
    except Exception:
        return set()
    return {str(row[0]) for row in rows}


def _daily_basic_select_columns(columns: set[str]) -> list[str]:
    selected = []
    for column in ("pb", "total_mv", "circ_mv", "turnover_rate", "turnover_rate_f"):
        if column in columns:
            selected.append(f"db.{column} AS {column}")
    return selected


def _build_factors(frame: pd.DataFrame) -> Optional[pd.DataFrame]:
    if frame is None or frame.empty or not {"symbol", "close"}.issubset(frame.columns):
        return None
    data = frame.copy()
    if "timestamp" in data.columns:
        data["date"] = pd.to_datetime(data["timestamp"]).dt.normalize()
    elif "date" in data.columns:
        data["date"] = pd.to_datetime(data["date"]).dt.normalize()
    else:
        return None

    data["price"] = _price_series(data)
    data = data.sort_values(["symbol", "date"])
    grouped_price = data.groupby("symbol")["price"]
    data["ret"] = grouped_price.pct_change()
    data["size_proxy"] = _positive(_coalesce_numeric(data, ["circ_mv", "total_mv"], _fallback_size_proxy(data)))
    data["value_proxy"] = _value_proxy(data)
    data["mom_proxy"] = pd.concat(
        [
            grouped_price.transform(lambda series: series.shift(1) / series.shift(21) - 1.0),
            grouped_price.transform(lambda series: series.shift(1) / series.shift(61) - 1.0),
        ],
        axis=1,
    ).mean(axis=1, skipna=True)
    data["rev_proxy"] = -grouped_price.transform(lambda series: series.shift(1) / series.shift(6) - 1.0)
    data["vol_proxy"] = data.groupby("symbol")["ret"].transform(lambda series: series.shift(1).rolling(20, min_periods=10).std())
    data["liq_proxy"] = _liquidity_proxy(data)

    rows = []
    for date, group in data.dropna(subset=["ret"]).groupby("date"):
        if len(group) < 3:
            continue
        rows.append(
            {
                "date": date,
                "MKT": float(group["ret"].mean()),
                "SIZE": _spread(group, "size_proxy", high_minus_low=False),
                "VALUE": _spread(group, "value_proxy", high_minus_low=True),
                "MOM": _spread(group, "mom_proxy", high_minus_low=True),
                "REV": _spread(group, "rev_proxy", high_minus_low=True),
                "VOL": _spread(group, "vol_proxy", high_minus_low=True),
                "LIQ": _spread(group, "liq_proxy", high_minus_low=True),
                "RF": 0.0,
            }
        )
    if not rows:
        return None
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def _price_series(data: pd.DataFrame) -> pd.Series:
    raw = pd.to_numeric(data["close"], errors="coerce")
    if "adj_close" not in data.columns:
        return raw
    adjusted = pd.to_numeric(data["adj_close"], errors="coerce")
    return adjusted.where(adjusted > 0, raw)


def _fallback_size_proxy(data: pd.DataFrame) -> pd.Series:
    price = pd.to_numeric(data["price"], errors="coerce")
    if "turnover" in data.columns:
        return pd.to_numeric(data["turnover"], errors="coerce")
    if "volume" in data.columns:
        return price * pd.to_numeric(data["volume"], errors="coerce")
    return price


def _value_proxy(data: pd.DataFrame) -> pd.Series:
    if "pb" in data.columns:
        pb = _positive(pd.to_numeric(data["pb"], errors="coerce"))
        value = 1.0 / pb
        fallback = 1.0 / _positive(pd.to_numeric(data["price"], errors="coerce"))
        return value.where(value.notna(), fallback)
    return 1.0 / _positive(pd.to_numeric(data["price"], errors="coerce"))


def _liquidity_proxy(data: pd.DataFrame) -> pd.Series:
    base = _positive(_coalesce_numeric(data, ["turnover", "volume", "turnover_rate_f", "turnover_rate"], pd.Series(index=data.index, dtype=float)))
    illiquidity = data["ret"].abs() / base
    return illiquidity.groupby(data["symbol"]).transform(lambda series: series.shift(1).rolling(20, min_periods=10).mean())


def _coalesce_numeric(data: pd.DataFrame, columns: list[str], fallback: pd.Series) -> pd.Series:
    result = None
    for column in columns:
        if column not in data.columns:
            continue
        values = pd.to_numeric(data[column], errors="coerce")
        result = values if result is None else result.where(result.notna(), values)
    if result is None:
        return fallback
    return result.where(result.notna(), fallback)


def _positive(values: pd.Series) -> pd.Series:
    return values.where(values > 0)


def _spread(group: pd.DataFrame, column: str, high_minus_low: bool) -> float:
    valid = group.dropna(subset=["ret", column])
    if len(valid) < 3:
        return 0.0
    low = valid[column].quantile(0.3)
    high = valid[column].quantile(0.7)
    low_ret = valid.loc[valid[column] <= low, "ret"]
    high_ret = valid.loc[valid[column] >= high, "ret"]
    if low_ret.empty or high_ret.empty:
        return 0.0
    if high_minus_low:
        return float(high_ret.mean() - low_ret.mean())
    return float(low_ret.mean() - high_ret.mean())


def _filter_factors(factors: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    dates = pd.to_datetime(factors["date"] if "date" in factors.columns else factors.index)
    mask = (dates >= pd.to_datetime(start)) & (dates <= pd.to_datetime(end))
    return factors.loc[mask].reset_index(drop=True)


def _history_start(start: str) -> str:
    return (pd.to_datetime(start) - pd.Timedelta(days=LOOKBACK_BUFFER_DAYS)).date().isoformat()


def _cache_dir(cache_dir: Any = None) -> Path:
    if cache_dir is not None:
        return Path(cache_dir)
    return Path(__file__).resolve().parents[2] / "var" / "research" / "factor_zoo"


def _default_daily_basic_db_path() -> Path:
    try:
        from quant.infrastructure.data.storage_duckdb import _DEFAULT_DAILY_BASIC_DB

        return Path(_DEFAULT_DAILY_BASIC_DB)
    except Exception:
        return Path(__file__).resolve().parents[2] / "var" / "duckdb" / "live" / "cn_daily_basic.duckdb"


def _is_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    modified = datetime.fromtimestamp(path.stat().st_mtime)
    return datetime.now() - modified <= timedelta(days=CACHE_TTL_DAYS)


def _cache_usable(path: Path, metadata_path: Path, db_path: str, start: str, end: str) -> bool:
    if not _is_fresh(path):
        return False
    metadata = _read_metadata(metadata_path)
    if metadata is not None:
        return (
            metadata.get("factor_version") == CACHE_FACTOR_VERSION
            and _metadata_matches(metadata, db_path)
            and _metadata_covers(metadata, start, end)
            and _cached_columns_cover(path)
        )
    coverage = _cached_coverage(path)
    if coverage is None:
        return False
    min_date, max_date = coverage
    return min_date <= pd.to_datetime(start).normalize() and max_date >= pd.to_datetime(end).normalize() and _cached_columns_cover(path)


def _read_cached(path: Path, start: str, end: str) -> Optional[pd.DataFrame]:
    try:
        frame = pd.read_parquet(path)
        if "date" in frame.columns:
            dates = pd.to_datetime(frame["date"])
            mask = (dates >= pd.to_datetime(start)) & (dates <= pd.to_datetime(end))
            return frame.loc[mask].reset_index(drop=True)
        frame.index = pd.to_datetime(frame.index)
        return frame.loc[(frame.index >= pd.to_datetime(start)) & (frame.index <= pd.to_datetime(end))]
    except Exception as e:
        logger.warning(f"CN factor cache read failed: {e}")
        return None


def _read_metadata(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"CN factor metadata read failed: {e}")
        return None


def _write_metadata(path: Path, db_path: str, factors: pd.DataFrame) -> None:
    dates = pd.to_datetime(factors["date"] if "date" in factors.columns else factors.index)
    source = Path(db_path).resolve()
    metadata = {
        "factor_version": CACHE_FACTOR_VERSION,
        "db_path": str(source),
        "db_mtime": source.stat().st_mtime if source.exists() else None,
        "min_date": dates.min().strftime("%Y-%m-%d"),
        "max_date": dates.max().strftime("%Y-%m-%d"),
        "created_at": datetime.now().isoformat(),
    }
    path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")


def _metadata_matches(metadata: dict, db_path: str) -> bool:
    source = Path(db_path).resolve()
    if metadata.get("db_path") != str(source):
        return False
    db_mtime = metadata.get("db_mtime")
    if db_mtime is None or not source.exists():
        return False
    return float(db_mtime) == source.stat().st_mtime


def _metadata_covers(metadata: dict, start: str, end: str) -> bool:
    try:
        min_date = pd.to_datetime(metadata.get("min_date")).normalize()
        max_date = pd.to_datetime(metadata.get("max_date")).normalize()
        return min_date <= pd.to_datetime(start).normalize() and max_date >= pd.to_datetime(end).normalize()
    except Exception:
        return False


def _cached_coverage(path: Path) -> Optional[tuple[pd.Timestamp, pd.Timestamp]]:
    try:
        frame = pd.read_parquet(path)
        dates = pd.to_datetime(frame["date"] if "date" in frame.columns else frame.index)
        if dates.empty:
            return None
        return dates.min().normalize(), dates.max().normalize()
    except Exception as e:
        logger.warning(f"CN factor cache coverage read failed: {e}")
        return None


def _cached_columns_cover(path: Path) -> bool:
    try:
        frame = pd.read_parquet(path)
        return REQUIRED_CN_FACTOR_COLUMNS.issubset(set(frame.columns))
    except Exception as e:
        logger.warning(f"CN factor cache column read failed: {e}")
        return False
