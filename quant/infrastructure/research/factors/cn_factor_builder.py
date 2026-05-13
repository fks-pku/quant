import logging
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)
CACHE_TTL_DAYS = 30


def build_cn3_factors(db_path: str, start: str, end: str, cache_dir: Any = None) -> Optional[pd.DataFrame]:
    path = _cache_dir(cache_dir) / "cn3_daily.parquet"
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
            frame = conn.execute(
                f"""
                SELECT *
                FROM daily_cn_ochl
                WHERE CAST({date_column} AS DATE) BETWEEN ? AND ?
                ORDER BY CAST({date_column} AS DATE), symbol
                """,
                [start, end],
            ).fetchdf()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"CN factor data unavailable: {e}")
        return None

    factors = _build_factors(frame)
    if factors is None:
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

    data = data.sort_values(["symbol", "date"])
    data["ret"] = data.groupby("symbol")["close"].pct_change()
    if "turnover" in data.columns:
        data["size_proxy"] = pd.to_numeric(data["turnover"], errors="coerce")
        data["value_proxy"] = data["size_proxy"] / pd.to_numeric(data["close"], errors="coerce")
    elif "volume" in data.columns:
        data["size_proxy"] = pd.to_numeric(data["close"], errors="coerce") * pd.to_numeric(data["volume"], errors="coerce")
        data["value_proxy"] = 1.0 / pd.to_numeric(data["close"], errors="coerce")
    else:
        data["size_proxy"] = pd.to_numeric(data["close"], errors="coerce")
        data["value_proxy"] = 1.0 / pd.to_numeric(data["close"], errors="coerce")

    rows = []
    for date, group in data.dropna(subset=["ret", "size_proxy", "value_proxy"]).groupby("date"):
        if len(group) < 3:
            continue
        rows.append(
            {
                "date": date,
                "MKT": float(group["ret"].mean()),
                "SMB": _spread(group, "size_proxy", small_minus_large=True),
                "HML": _spread(group, "value_proxy", small_minus_large=False),
                "RF": 0.0,
            }
        )
    if not rows:
        return None
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def _spread(group: pd.DataFrame, column: str, small_minus_large: bool) -> float:
    low = group[column].quantile(0.3)
    high = group[column].quantile(0.7)
    low_ret = group.loc[group[column] <= low, "ret"]
    high_ret = group.loc[group[column] >= high, "ret"]
    if low_ret.empty or high_ret.empty:
        return 0.0
    if small_minus_large:
        return float(low_ret.mean() - high_ret.mean())
    return float(high_ret.mean() - low_ret.mean())


def _cache_dir(cache_dir: Any = None) -> Path:
    if cache_dir is not None:
        return Path(cache_dir)
    return Path(__file__).resolve().parents[2] / "var" / "research" / "factor_zoo"


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
        return _metadata_matches(metadata, db_path) and _metadata_covers(metadata, start, end)
    coverage = _cached_coverage(path)
    if coverage is None:
        return False
    min_date, max_date = coverage
    return min_date <= pd.to_datetime(start).normalize() and max_date >= pd.to_datetime(end).normalize()


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
