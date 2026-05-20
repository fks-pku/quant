"""Split legacy DuckDB market data into live sidecar databases."""

import argparse
import shutil
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[2]
LEGACY_DUCKDB_DIR = ROOT / "quant" / "infrastructure" / "var" / "duckdb"
LIVE_DUCKDB_DIR = LEGACY_DUCKDB_DIR / "live"

LEGACY_MARKET_DB = LEGACY_DUCKDB_DIR / "quant.duckdb"
LEGACY_DAILY_BASIC_DB = LEGACY_DUCKDB_DIR / "cn_daily_basic.duckdb"
LEGACY_STATUS_DB = LEGACY_DUCKDB_DIR / "security_status.duckdb"

STOCK_DB = LIVE_DUCKDB_DIR / "cn_ohlcv.duckdb"
ETF_DB = LIVE_DUCKDB_DIR / "cn_etf_ohlcv.duckdb"
INDEX_DB = LIVE_DUCKDB_DIR / "cn_index_ohlcv.duckdb"
DAILY_BASIC_DB = LIVE_DUCKDB_DIR / "cn_daily_basic.duckdb"
STATUS_DB = LIVE_DUCKDB_DIR / "cn_status.duckdb"
CORPORATE_ACTIONS_DB = LIVE_DUCKDB_DIR / "cn_corporate_actions.duckdb"

ETF_PREFIX_REGEX = "^(15|51|56|58)"
DAILY_TABLE = "daily_cn_ochl"


def main() -> None:
    parser = argparse.ArgumentParser(description="Split legacy quant.duckdb data into live DuckDB sidecars")
    parser.add_argument("--source-market-db", default=str(LEGACY_MARKET_DB))
    parser.add_argument("--source-daily-basic-db", default=str(LEGACY_DAILY_BASIC_DB))
    parser.add_argument("--source-status-db", default=str(LEGACY_STATUS_DB))
    parser.add_argument("--output-dir", default=str(LIVE_DUCKDB_DIR))
    parser.add_argument("--force", action="store_true", help="Overwrite existing output DuckDB files")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "stock": output_dir / STOCK_DB.name,
        "etf": output_dir / ETF_DB.name,
        "index": output_dir / INDEX_DB.name,
        "daily_basic": output_dir / DAILY_BASIC_DB.name,
        "status": output_dir / STATUS_DB.name,
        "corporate_actions": output_dir / CORPORATE_ACTIONS_DB.name,
    }
    source_market = Path(args.source_market_db).resolve()
    source_basic = Path(args.source_daily_basic_db).resolve()
    source_status = Path(args.source_status_db).resolve()

    _prepare_outputs(paths.values(), (source_market, source_basic, source_status), args.force)
    _build_market_split(source_market, source_basic, paths)
    _copy_sidecar(source_basic, paths["daily_basic"], args.force)
    _copy_sidecar(source_status, paths["status"], args.force)
    _print_summary(paths)


def _prepare_outputs(paths, sources, force: bool) -> None:
    source_set = {path.resolve() for path in sources if path.exists()}
    for path in paths:
        resolved = path.resolve()
        if resolved in source_set:
            raise RuntimeError(f"Refusing to overwrite source DuckDB: {path}")
        if path.exists():
            if not force:
                raise RuntimeError(f"{path} exists; pass --force to overwrite")
            path.unlink()


def _build_market_split(source_market: Path, source_basic: Path, paths: dict) -> None:
    if not source_market.exists():
        raise FileNotFoundError(source_market)
    if not source_basic.exists():
        raise FileNotFoundError(source_basic)

    _create_filtered_market_db(
        paths["stock"],
        source_market,
        source_basic,
        f"b.symbol IN (SELECT symbol FROM daily_basic.cn_daily_basic)",
        copy_utility_tables=True,
    )
    _create_filtered_market_db(
        paths["etf"],
        source_market,
        source_basic,
        f"regexp_matches(b.symbol, '{ETF_PREFIX_REGEX}')",
        copy_utility_tables=False,
    )
    _create_filtered_market_db(
        paths["index"],
        source_market,
        source_basic,
        f"b.symbol NOT IN (SELECT symbol FROM daily_basic.cn_daily_basic) "
        f"AND NOT regexp_matches(b.symbol, '{ETF_PREFIX_REGEX}')",
        copy_utility_tables=False,
    )
    _create_corporate_actions_db(paths["corporate_actions"], source_market)


def _create_filtered_market_db(
    target: Path,
    source_market: Path,
    source_basic: Path,
    predicate: str,
    copy_utility_tables: bool,
) -> None:
    conn = duckdb.connect(str(target))
    try:
        _attach(conn, "source", source_market, read_only=True)
        _attach(conn, "daily_basic", source_basic, read_only=True)
        conn.execute(
            f"""
            CREATE TABLE {DAILY_TABLE} AS
            SELECT b.*
            FROM source.{DAILY_TABLE} b
            WHERE {predicate}
            ORDER BY b.symbol, b.timestamp
            """
        )
        conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{DAILY_TABLE}_ts_sym ON {DAILY_TABLE}(timestamp, symbol)")
        if copy_utility_tables:
            for table in (
                "daily_cn_temp",
                "daily_us",
                "instrument_meta",
                "orders",
                "portfolio_snapshots",
                "strategy_snapshots",
                "trades",
            ):
                if _source_table_exists(conn, table):
                    conn.execute(f"CREATE TABLE {table} AS SELECT * FROM source.{table}")
    finally:
        conn.close()


def _create_corporate_actions_db(target: Path, source_market: Path) -> None:
    conn = duckdb.connect(str(target))
    try:
        _attach(conn, "source", source_market, read_only=True)
        if _source_table_exists(conn, "cn_dividends"):
            conn.execute("CREATE TABLE cn_dividends AS SELECT * FROM source.cn_dividends ORDER BY symbol, ex_date")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cn_dividends_sym_ex_date ON cn_dividends(symbol, ex_date)")
    finally:
        conn.close()


def _copy_sidecar(source: Path, target: Path, force: bool) -> None:
    if not source.exists():
        raise FileNotFoundError(source)
    if target.exists():
        if not force:
            raise RuntimeError(f"{target} exists; pass --force to overwrite")
        target.unlink()
    shutil.copy2(source, target)


def _attach(conn: duckdb.DuckDBPyConnection, schema: str, path: Path, read_only: bool) -> None:
    escaped = str(path).replace("'", "''")
    suffix = " (READ_ONLY)" if read_only else ""
    conn.execute(f"ATTACH '{escaped}' AS {schema}{suffix}")


def _source_table_exists(conn: duckdb.DuckDBPyConnection, table: str) -> bool:
    return bool(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_catalog = 'source'
              AND table_name = ?
            """,
            [table],
        ).fetchone()[0]
    )


def _print_summary(paths: dict) -> None:
    for label, path in paths.items():
        if not path.exists():
            print(f"{label}\tmissing\t{path}")
            continue
        size = path.stat().st_size
        tables = []
        conn = duckdb.connect(str(path), read_only=True)
        try:
            rows = conn.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main'
                ORDER BY table_name
                """
            ).fetchall()
            for (table,) in rows:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                tables.append(f"{table}:{count}")
        finally:
            conn.close()
        print(f"{label}\t{size}\t{path}\t{', '.join(tables)}")


if __name__ == "__main__":
    main()
