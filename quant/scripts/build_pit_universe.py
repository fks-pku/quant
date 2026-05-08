import argparse
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TABLE_MARKETS = {
    "daily_cn": "cn",
    "daily_us": "us",
    "daily_hk": "hk",
}


def build_pit_universe(
    db_path: str,
    output_dir: Optional[str] = None,
    today: Optional[str] = None,
) -> List[Path]:
    import duckdb

    target = Path(output_dir) if output_dir is not None else _default_snapshot_dir()
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        lifetimes = _load_symbol_lifetimes(conn, today=today)
    finally:
        conn.close()
    if lifetimes.empty:
        return []
    return write_monthly_snapshots(lifetimes, target)


def _load_symbol_lifetimes(conn: Any, today: Optional[str] = None) -> Any:
    import pandas as pd

    frames = []
    for table, market in TABLE_MARKETS.items():
        columns = _table_columns(conn, table)
        if not columns or "symbol" not in columns:
            continue
        date_col = _first_column(columns, ["date", "timestamp"])
        if date_col is None:
            continue
        query = f"""
            SELECT
                symbol,
                MIN(TRY_CAST({date_col} AS DATE)) AS listing_date,
                MAX(TRY_CAST({date_col} AS DATE)) AS last_bar_date
            FROM {table}
            GROUP BY symbol
        """
        try:
            frame = conn.execute(query).fetchdf()
        except Exception as e:
            logger.warning(f"Unable to read {table}: {e}")
            continue
        if frame.empty:
            continue
        frame["listing_date"] = pd.to_datetime(frame["listing_date"], errors="coerce")
        frame["last_bar_date"] = pd.to_datetime(frame["last_bar_date"], errors="coerce")
        market_last_bar = frame["last_bar_date"].dropna().max()
        frame["delisting_date"] = frame["last_bar_date"].where(frame["last_bar_date"] < market_last_bar)
        frame["market"] = market
        frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["symbol", "market", "listing_date", "delisting_date"])
    result = pd.concat(frames, ignore_index=True)
    if today is not None:
        today_ts = pd.Timestamp(today).normalize()
        result = result[result["listing_date"] <= today_ts]
    return result.dropna(subset=["symbol", "market", "listing_date", "last_bar_date"])


def write_monthly_snapshots(lifetimes: Any, output_dir: Path) -> List[Path]:
    import pandas as pd

    output_dir.mkdir(parents=True, exist_ok=True)
    frame = lifetimes.copy()
    frame["listing_date"] = pd.to_datetime(frame["listing_date"], errors="coerce")
    frame["delisting_date"] = pd.to_datetime(frame["delisting_date"], errors="coerce")
    frame = frame.dropna(subset=["listing_date"])
    if frame.empty:
        return []

    start = frame["listing_date"].min()
    end_candidates = [frame["listing_date"].max()]
    if "last_bar_date" in frame.columns:
        frame["last_bar_date"] = pd.to_datetime(frame["last_bar_date"], errors="coerce")
        end_candidates.append(frame["last_bar_date"].dropna().max())
    end_candidates.append(frame["delisting_date"].dropna().max())
    end = max(value for value in end_candidates if not pd.isna(value))

    paths = []
    for as_of in _snapshot_month_ends(start, end):
        active = frame[
            (frame["listing_date"] <= as_of)
            & (frame["delisting_date"].isna() | (frame["delisting_date"] > as_of))
        ].copy()
        snapshot = active[["symbol", "market", "listing_date", "delisting_date"]].copy()
        snapshot["listing_date"] = snapshot["listing_date"].dt.strftime("%Y-%m-%d")
        snapshot["delisting_date"] = None
        path = output_dir / f"{as_of.year}_{as_of.month:02d}_universe.parquet"
        snapshot.to_parquet(path, index=False)
        paths.append(path)
    return paths


def _snapshot_month_ends(start: Any, end: Any) -> List[Any]:
    import pandas as pd

    start_period = pd.Timestamp(start).to_period("M")
    end_period = pd.Timestamp(end).to_period("M")
    return [period.to_timestamp("M") for period in pd.period_range(start_period, end_period, freq="M")]


def _table_columns(conn: Any, table: str) -> Dict[str, str]:
    try:
        rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    except Exception:
        return {}
    return {str(row[1]).lower(): str(row[1]) for row in rows}


def _first_column(columns: Dict[str, str], names: List[str]) -> Optional[str]:
    for name in names:
        column = columns.get(name)
        if column is not None:
            return column
    return None


def _default_snapshot_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "infrastructure" / "var" / "research" / "universe_snapshots"


def _default_db_path() -> Path:
    return Path(__file__).resolve().parents[1] / "infrastructure" / "var" / "market.duckdb"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default=str(_default_db_path()))
    parser.add_argument("--output-dir", default=str(_default_snapshot_dir()))
    parser.add_argument("--today", default=None)
    args = parser.parse_args()
    paths = build_pit_universe(args.db_path, args.output_dir, today=args.today)
    print(f"Wrote {len(paths)} PIT universe snapshots to {args.output_dir}")


if __name__ == "__main__":
    main()
