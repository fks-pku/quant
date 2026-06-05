"""CN trading-calendar resolver used by live scheduling scripts."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import duckdb
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DUCKDB_DIR = ROOT / "quant" / "infrastructure" / "var" / "duckdb" / "live"
DEFAULT_CACHE_PATH = ROOT / "quant" / "infrastructure" / "var" / "calendar" / "cn_trade_calendar_sse.json"


class _NoopProviderStorage:
    def close(self) -> None:
        return None


def parse_date(value: str) -> date:
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def is_open_trading_day(
    value: date,
    *,
    cache_path: Path = DEFAULT_CACHE_PATH,
    duckdb_dir: Path = DEFAULT_DUCKDB_DIR,
    allow_refresh: bool = True,
) -> bool:
    calendar = load_or_refresh_calendar(value, value, cache_path=cache_path, allow_refresh=allow_refresh)
    if value in calendar:
        return calendar[value]
    return value in set(_status_trading_dates(duckdb_dir)) or value in set(_market_data_dates(duckdb_dir))


def next_trading_date_after(
    value: date,
    *,
    cache_path: Path = DEFAULT_CACHE_PATH,
    duckdb_dir: Path = DEFAULT_DUCKDB_DIR,
    allow_refresh: bool = True,
    horizon_days: int = 45,
) -> date:
    start = value + timedelta(days=1)
    end = value + timedelta(days=horizon_days)
    calendar = load_or_refresh_calendar(start, end, cache_path=cache_path, allow_refresh=allow_refresh)
    for day in _date_range(start, end):
        if calendar.get(day):
            return day
    known = [day for day in _status_trading_dates(duckdb_dir) if day > value]
    if known:
        return min(known)
    return _next_weekday_after(value)


def previous_trading_date_before(
    value: date,
    *,
    cache_path: Path = DEFAULT_CACHE_PATH,
    duckdb_dir: Path = DEFAULT_DUCKDB_DIR,
    allow_refresh: bool = True,
    horizon_days: int = 45,
) -> date:
    start = value - timedelta(days=horizon_days)
    end = value - timedelta(days=1)
    calendar = load_or_refresh_calendar(start, end, cache_path=cache_path, allow_refresh=allow_refresh)
    for day in reversed(list(_date_range(start, end))):
        if calendar.get(day):
            return day
    known = [day for day in _status_trading_dates(duckdb_dir) if day < value]
    if known:
        return max(known)
    return _previous_weekday_before(value)


def expected_market_data_date(
    now: Optional[datetime] = None,
    *,
    cache_path: Path = DEFAULT_CACHE_PATH,
    duckdb_dir: Path = DEFAULT_DUCKDB_DIR,
    allow_refresh: bool = False,
) -> date:
    value = now or datetime.now()
    today = value.date()
    if is_open_trading_day(today, cache_path=cache_path, duckdb_dir=duckdb_dir, allow_refresh=allow_refresh):
        if value.hour >= 16:
            return today
        return previous_trading_date_before(
            today,
            cache_path=cache_path,
            duckdb_dir=duckdb_dir,
            allow_refresh=allow_refresh,
        )
    return previous_trading_date_before(
        today,
        cache_path=cache_path,
        duckdb_dir=duckdb_dir,
        allow_refresh=allow_refresh,
    )


def latest_data_date(
    *,
    duckdb_dir: Path = DEFAULT_DUCKDB_DIR,
    on_or_before: Optional[date] = None,
) -> date:
    dates = latest_data_dates(limit=1, duckdb_dir=duckdb_dir, on_or_before=on_or_before)
    if not dates:
        raise RuntimeError("no CN market data dates found")
    return dates[0]


def latest_two_data_dates(
    *,
    duckdb_dir: Path = DEFAULT_DUCKDB_DIR,
    on_or_before: Optional[date] = None,
) -> tuple[date, date]:
    dates = latest_data_dates(limit=2, duckdb_dir=duckdb_dir, on_or_before=on_or_before)
    if len(dates) < 2:
        raise RuntimeError("need at least two CN market data dates")
    return dates[1], dates[0]


def latest_data_dates(
    *,
    limit: int,
    duckdb_dir: Path = DEFAULT_DUCKDB_DIR,
    on_or_before: Optional[date] = None,
) -> List[date]:
    source_sets = _market_data_date_sets(duckdb_dir)
    if not source_sets:
        return []
    common = set.intersection(*source_sets) if len(source_sets) > 1 else set(source_sets[0])
    if not common:
        common = set.union(*source_sets)
    values = sorted(common, reverse=True)
    if on_or_before is not None:
        values = [day for day in values if day <= on_or_before]
    return values[:limit]


def load_or_refresh_calendar(
    start: date,
    end: date,
    *,
    cache_path: Path = DEFAULT_CACHE_PATH,
    allow_refresh: bool = True,
) -> Dict[date, bool]:
    calendar = _read_calendar_cache(cache_path)
    if allow_refresh and _calendar_missing_range(calendar, start, end):
        fetched = _fetch_tushare_calendar(start, end)
        if fetched:
            calendar.update(fetched)
            _write_calendar_cache(cache_path, calendar)
    return calendar


def _date_range(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _calendar_missing_range(calendar: Dict[date, bool], start: date, end: date) -> bool:
    return any(day not in calendar for day in _date_range(start, end))


def _read_calendar_cache(path: Path) -> Dict[date, bool]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = payload.get("rows", {}) if isinstance(payload, dict) else {}
    result: Dict[date, bool] = {}
    for key, value in rows.items():
        try:
            result[parse_date(key)] = bool(value)
        except ValueError:
            continue
    return result


def _write_calendar_cache(path: Path, calendar: Dict[date, bool]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "exchange": "SSE",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "rows": {day.isoformat(): bool(calendar[day]) for day in sorted(calendar)},
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _fetch_tushare_calendar(start: date, end: date) -> Dict[date, bool]:
    provider = None
    try:
        from quant.infrastructure.data.providers.tushare import TushareProvider

        provider = TushareProvider(storage=_NoopProviderStorage(), min_interval=0.12)
        provider.logger.disabled = True
        provider.connect()
        api = getattr(provider, "_api", None)
        if api is None:
            return {}
        provider._rate_limit()
        frame = api.trade_cal(
            exchange="SSE",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            fields="cal_date,is_open",
        )
    except Exception:
        return {}
    finally:
        if provider is not None:
            try:
                provider.disconnect()
            except Exception:
                pass
    if frame is None or frame.empty:
        return {}
    frame = frame.copy()
    frame["trade_date"] = pd.to_datetime(frame["cal_date"], format="%Y%m%d", errors="coerce").dt.date
    frame["is_open"] = pd.to_numeric(frame["is_open"], errors="coerce").fillna(0).astype(int) == 1
    return {
        row.trade_date: bool(row.is_open)
        for row in frame[["trade_date", "is_open"]].dropna(subset=["trade_date"]).itertuples(index=False)
        if start <= row.trade_date <= end
    }


def _market_data_dates(duckdb_dir: Path) -> List[date]:
    values: set[date] = set()
    for source in _market_data_date_sets(duckdb_dir):
        values.update(source)
    return sorted(values)


def _market_data_date_sets(duckdb_dir: Path) -> List[set[date]]:
    specs = (
        ("cn_ohlcv.duckdb", "daily_cn_ochl", "timestamp"),
        ("cn_etf_ohlcv.duckdb", "daily_cn_ochl", "timestamp"),
        ("cn_index_ohlcv.duckdb", "daily_cn_ochl", "timestamp"),
    )
    result: List[set[date]] = []
    for db_name, table, column in specs:
        values = _distinct_dates_from_table(duckdb_dir / db_name, table, column)
        if values:
            result.append(set(values))
    return result


def _status_trading_dates(duckdb_dir: Path) -> List[date]:
    return _distinct_dates_from_table(
        duckdb_dir / "cn_status.duckdb",
        "cn_security_status_daily",
        "trade_date",
        where="COALESCE(is_trade_day, TRUE) = TRUE",
    )


def _distinct_dates_from_table(
    db_path: Path,
    table: str,
    column: str,
    *,
    where: str = "TRUE",
) -> List[date]:
    if not db_path.exists():
        return []
    try:
        with duckdb.connect(str(db_path), read_only=True) as con:
            exists = con.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.tables
                WHERE table_name = ?
                """,
                [table],
            ).fetchone()[0]
            if not exists:
                return []
            rows = con.execute(
                f"""
                SELECT DISTINCT CAST({column} AS DATE) AS d
                FROM {table}
                WHERE {where}
                ORDER BY d
                """
            ).fetchall()
    except Exception:
        return []
    return [row[0] for row in rows if row and row[0] is not None]


def _next_weekday_after(value: date) -> date:
    day = value + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _previous_weekday_before(value: date) -> date:
    day = value - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day
