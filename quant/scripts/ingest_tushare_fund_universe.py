"""Ingest listed CN ETF/LOF data from Tushare into DuckDB sidecars."""

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import pandas as pd

_pkg_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_pkg_dir.parent))

from quant.infrastructure.data.providers.tushare import TushareProvider
from quant.infrastructure.data.storage_duckdb import (
    DuckDBStorage,
    _CN_DAILY_TABLE,
    _ETF_SCHEMA,
    _FUND_NAV_SCHEMA,
    _FUND_NAV_TABLE,
)
from quant.infrastructure.research.repository import FileResearchStore
from quant.shared.utils.logger import setup_logger

logger = setup_logger("ingest_tushare_fund_universe")

STRATEGY_ID = "joinquant_wufu_daily_etf_lof"
DEFAULT_SOURCE_URL = "https://www.joinquant.com/view/community/detail/cc21565a660487b31666dc40a6aa9ecd?type=1"
DEFAULT_CASH_SYMBOL = "511880"
DEFAULT_ALWAYS_SYMBOLS = (
    "159915",
    "510050",
    "510300",
    "510500",
    "511880",
    "512100",
    "512880",
    "513100",
    "518880",
)
EXCLUDE_KEYWORDS = (
    "货币",
    "现金",
    "添利",
    "收益",
    "债",
    "国债",
    "政金",
    "信用",
    "可转债",
    "短融",
    "货基",
    "保证金",
)


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def _parse_symbols(value: str) -> List[str]:
    return [item.strip() for item in str(value or "").replace(";", ",").split(",") if item.strip()]


def _load_symbol_file(path: Optional[str]) -> List[str]:
    if not path:
        return []
    symbols = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            value = line.strip()
            if value:
                symbols.append(value)
    return symbols


def _normalize_symbols(symbols: Iterable[str]) -> List[str]:
    result = []
    seen = set()
    for symbol in symbols:
        value = str(symbol).strip().split(".")[0]
        if not (value.isdigit() and len(value) == 6):
            continue
        if value in seen:
            continue
        result.append(value)
        seen.add(value)
    return result


def fetch_fund_metadata(provider: TushareProvider, statuses: Sequence[str]) -> pd.DataFrame:
    frames = []
    for status in statuses:
        fund_basic = provider.fetch_fund_basic(status=status)
        if not fund_basic.empty:
            fund_basic = fund_basic.copy()
            fund_basic["_source_priority"] = 1
            frames.append(fund_basic)
        etf_basic = provider.fetch_etf_basic(status=status)
        if not etf_basic.empty:
            etf_basic = etf_basic.copy()
            etf_basic["_source_priority"] = 2
            frames.append(etf_basic)
    if not frames:
        return pd.DataFrame()

    frame = pd.concat(frames, ignore_index=True, sort=False)
    if "symbol" not in frame.columns and "ts_code" in frame.columns:
        frame["symbol"] = frame["ts_code"].astype(str).str.split(".").str[0]
    for col in (
        "symbol",
        "ts_code",
        "name",
        "fund_type",
        "instrument_type",
        "status",
        "market",
        "list_date",
        "delist_date",
        "index_code",
        "index_name",
        "exchange",
    ):
        if col not in frame.columns:
            frame[col] = ""
        frame[col] = frame[col].fillna("").astype(str)
    frame = frame[frame["symbol"].str.match(r"^(15|16|50|51|52|56|58)\d{4}$")]
    frame = frame.sort_values(["symbol", "_source_priority"]).drop_duplicates("symbol", keep="last")
    return frame.reset_index(drop=True)


def select_universe(
    metadata: pd.DataFrame,
    statuses: Sequence[str],
    listed_before: datetime,
    listed_after: Optional[datetime],
    limit: int,
    always_symbols: Sequence[str],
    cash_symbol: str,
) -> List[str]:
    if metadata.empty:
        return _normalize_symbols([*always_symbols, cash_symbol])

    frame = metadata.copy()
    frame["list_dt"] = pd.to_datetime(frame["list_date"], format="%Y%m%d", errors="coerce")
    frame = frame[frame["list_dt"].notna()]
    frame = frame[frame["list_dt"] <= pd.Timestamp(listed_before)]
    if listed_after is not None:
        frame = frame[frame["list_dt"] >= pd.Timestamp(listed_after)]
    if statuses:
        allowed = {str(item) for item in statuses}
        frame = frame[frame["status"].isin(allowed)]

    def keep_row(row: pd.Series) -> bool:
        text = " ".join(
            str(row.get(col) or "")
            for col in ("name", "fund_type", "index_name")
        )
        return not any(keyword in text for keyword in EXCLUDE_KEYWORDS)

    frame = frame[frame.apply(keep_row, axis=1)]
    frame = frame.sort_values(["list_dt", "symbol"])
    selected = frame["symbol"].tolist()
    if limit > 0:
        selected = selected[:limit]
    selected.extend(always_symbols)
    selected.append(cash_symbol)
    return _normalize_symbols(selected)


def _bar_coverage(storage: DuckDBStorage, symbol: str) -> Optional[Dict[str, object]]:
    if not storage._ensure_sidecar_attached(_ETF_SCHEMA, storage._etf_db_path):
        return None
    table_name = f"{_ETF_SCHEMA}.{_CN_DAILY_TABLE}"
    if not storage._table_exists(table_name):
        return None
    row = storage.conn.execute(
        f"""
        SELECT MIN(timestamp), MAX(timestamp), COUNT(*)
        FROM {table_name}
        WHERE symbol = ?
        """,
        [symbol],
    ).fetchone()
    if row is None or row[2] == 0:
        return None
    return {"start": row[0], "end": row[1], "rows": int(row[2])}


def _nav_coverage(storage: DuckDBStorage, symbol: str) -> Optional[Dict[str, object]]:
    if not storage._ensure_sidecar_attached(_FUND_NAV_SCHEMA, storage._fund_nav_db_path):
        return None
    table_name = f"{_FUND_NAV_SCHEMA}.{_FUND_NAV_TABLE}"
    if not storage._table_exists(table_name):
        return None
    row = storage.conn.execute(
        f"""
        SELECT MIN(nav_date), MAX(nav_date), COUNT(*)
        FROM {table_name}
        WHERE symbol = ?
        """,
        [symbol],
    ).fetchone()
    if row is None or row[2] == 0:
        return None
    return {"start": row[0], "end": row[1], "rows": int(row[2])}


def _coverage_covers(coverage: Optional[Dict[str, object]], start: datetime, end: datetime) -> bool:
    if not coverage:
        return False
    start_value = coverage.get("start")
    end_value = coverage.get("end")
    if start_value is None or end_value is None:
        return False
    tolerated_start = pd.Timestamp(start) + pd.Timedelta(days=10)
    return (
        pd.Timestamp(start_value) <= tolerated_start
        and pd.Timestamp(end_value).date() >= pd.Timestamp(end).date()
    )


def ingest_symbol(
    provider: TushareProvider,
    storage: DuckDBStorage,
    symbol: str,
    start: datetime,
    end: datetime,
    resume: bool,
    skip_nav: bool,
) -> Dict[str, object]:
    bar_cov = _bar_coverage(storage, symbol)
    nav_cov = None if skip_nav else _nav_coverage(storage, symbol)
    bars_skipped = resume and _coverage_covers(bar_cov, start, end)
    nav_skipped = skip_nav or (resume and _coverage_covers(nav_cov, start, end))
    bars_saved = 0
    nav_saved = 0

    if not bars_skipped:
        bars = provider.fetch_daily_with_hfq(symbol, start, end)
        if not bars.empty:
            bars_saved = storage.save_bars(bars, timeframe="1d")
    if not nav_skipped:
        nav = provider.fetch_fund_nav(symbol, start, end)
        if not nav.empty:
            nav_saved = storage.save_cn_fund_nav(nav)

    return {
        "symbol": symbol,
        "bars_saved": bars_saved,
        "nav_saved": nav_saved,
        "bars_skipped": bars_skipped,
        "nav_skipped": nav_skipped,
    }


def update_research_state(symbols: Sequence[str], args: argparse.Namespace, metadata_rows: int) -> None:
    root = _pkg_dir / "infrastructure" / "var" / "research"
    store = FileResearchStore(root)
    now = datetime.now(timezone.utc).isoformat()
    meta = {
        "source": "joinquant_community_tushare_daily_impl",
        "source_url": args.source_url,
        "daily_adaptable": True,
        "strategy_type": "etf_momentum_rotation",
        "data_requirement": "tushare fund_daily/fund_adj/fund_nav/fund_basic/etf_basic",
        "required_data_fields": ["close", "adj_close", "turnover", "unit_nav", "premium_rate", "fund_name", "fund_status"],
        "strategy_spec": {
            "strategy_id": STRATEGY_ID,
            "strategy_type": "etf_momentum_rotation",
            "signal_formula_key": STRATEGY_ID,
            "horizon_days": 1,
            "lookback_days": 25,
            "execution_lag_days": 1,
            "universe": list(symbols),
            "required_fields": ["close", "adj_close", "turnover", "unit_nav", "premium_rate", "fund_name", "fund_status"],
            "max_position_pct": 1.0,
            "fallback_symbol": args.cash_symbol,
            "cash_symbol": args.cash_symbol,
            "status": "candidate",
        },
        "data_ingestion": {
            "start": args.start,
            "end": args.end,
            "statuses": list(args.statuses),
            "listed_before": args.listed_before,
            "listed_after": args.listed_after or "",
            "limit": args.limit,
            "universe_size": len(symbols),
            "metadata_rows": metadata_rows,
            "updated_at": now,
        },
    }
    store.upsert_candidate(
        {
            "id": STRATEGY_ID,
            "name": "JoinQuant Wufu daily ETF/LOF momentum",
            "description": (
                "Daily-only JoinQuant Wufu-style ETF/LOF rotation implemented with Tushare "
                "fund daily bars, fund adjustment factors, fund metadata, and NAV premium filters."
            ),
            "status": "candidate",
            "priority": 20,
            "source": "joinquant",
            "source_url": args.source_url,
            "research_meta": meta,
        }
    )
    logger.info(f"Updated research candidate {STRATEGY_ID} with {len(symbols)} symbols")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Tushare CN ETF/LOF daily data into split DuckDB files")
    parser.add_argument("--start", default="2012-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default="2025-12-31", help="End date YYYY-MM-DD")
    parser.add_argument("--statuses", default="L", help="Comma-separated fund statuses, e.g. L or L,D")
    parser.add_argument("--listed-before", default=None, help="Keep funds listed on or before this date; defaults to --end")
    parser.add_argument("--listed-after", default=None, help="Keep funds listed on or after this date")
    parser.add_argument("--symbols", default="", help="Comma-separated explicit symbols; bypasses metadata universe selection")
    parser.add_argument("--symbol-file", default="", help="File with one symbol per line; bypasses metadata universe selection")
    parser.add_argument("--always-symbols", default=",".join(DEFAULT_ALWAYS_SYMBOLS), help="Comma-separated symbols always included")
    parser.add_argument("--cash-symbol", default=DEFAULT_CASH_SYMBOL, help="Defensive cash-like symbol")
    parser.add_argument("--limit", type=int, default=0, help="Limit metadata-selected risk symbols before always-symbols")
    parser.add_argument("--min-interval", type=float, default=0.12, help="Minimum seconds between Tushare calls")
    parser.add_argument("--no-resume", action="store_true", help="Refetch symbols even if local coverage reaches --end")
    parser.add_argument("--metadata-only", action="store_true", help="Only refresh fund metadata and research state")
    parser.add_argument("--skip-nav", action="store_true", help="Do not fetch fund_nav")
    parser.add_argument("--update-research-state", action="store_true", help="Register the selected universe for research reports")
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL, help="Source URL stored in research metadata")
    args = parser.parse_args()

    start = _parse_date(args.start)
    end = _parse_date(args.end)
    listed_before = _parse_date(args.listed_before or args.end)
    listed_after = _parse_date(args.listed_after) if args.listed_after else None
    args.statuses = _parse_symbols(args.statuses)
    always_symbols = _normalize_symbols(_parse_symbols(args.always_symbols))

    storage = DuckDBStorage()
    provider = TushareProvider(storage=storage, min_interval=args.min_interval)
    provider.connect()

    try:
        metadata = fetch_fund_metadata(provider, args.statuses)
        if not metadata.empty:
            storage.save_cn_fund_instruments(metadata)
        explicit = _normalize_symbols([*_parse_symbols(args.symbols), *_load_symbol_file(args.symbol_file)])
        if explicit:
            symbols = _normalize_symbols([*explicit, args.cash_symbol])
        else:
            symbols = select_universe(
                metadata,
                statuses=args.statuses,
                listed_before=listed_before,
                listed_after=listed_after,
                limit=args.limit,
                always_symbols=always_symbols,
                cash_symbol=args.cash_symbol,
            )
        list_dates = {}
        if not metadata.empty:
            meta_dates = metadata[["symbol", "list_date"]].copy()
            meta_dates["list_dt"] = pd.to_datetime(meta_dates["list_date"], format="%Y%m%d", errors="coerce")
            list_dates = {
                str(row.symbol): row.list_dt.to_pydatetime()
                for row in meta_dates.itertuples(index=False)
                if not pd.isna(row.list_dt)
            }
        logger.info(f"Selected {len(symbols)} ETF/LOF symbols")
        if args.update_research_state:
            update_research_state(symbols, args, len(metadata))
        if args.metadata_only:
            return

        started = time.time()
        done = failed = skipped = 0
        for index, symbol in enumerate(symbols, start=1):
            try:
                symbol_start = max(start, list_dates.get(symbol, start))
                result = ingest_symbol(
                    provider,
                    storage,
                    symbol,
                    symbol_start,
                    end,
                    resume=not args.no_resume,
                    skip_nav=args.skip_nav,
                )
                if result["bars_skipped"] and result["nav_skipped"]:
                    skipped += 1
                else:
                    done += 1
                elapsed = max(time.time() - started, 1e-9)
                rate = index / elapsed
                remaining = (len(symbols) - index) / rate if rate > 0 else 0.0
                logger.info(
                    f"[{index}/{len(symbols)}] {symbol}: bars={result['bars_saved']} "
                    f"nav={result['nav_saved']} skipped=({result['bars_skipped']},{result['nav_skipped']}) "
                    f"eta={remaining / 60:.1f}m"
                )
            except Exception as exc:
                failed += 1
                logger.error(f"[{index}/{len(symbols)}] {symbol}: FAILED - {exc}")
        logger.info(f"Done: fetched={done}, skipped={skipped}, failed={failed}, total={len(symbols)}")
    finally:
        provider.disconnect()


if __name__ == "__main__":
    main()
