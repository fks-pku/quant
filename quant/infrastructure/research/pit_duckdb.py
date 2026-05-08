import logging
from typing import Any, Dict, List, Optional

from quant.domain.ports.pit_data import PITData

logger = logging.getLogger(__name__)


class PITDuckDBData(PITData):
    def __init__(self, db_path: str = "quant/infrastructure/var/market.duckdb"):
        self._db_path = db_path

    def get_universe(self, as_of_date: str, market: str) -> List[str]:
        table = self._table_for_market(market)
        if table is None:
            return []
        conn = None
        try:
            import duckdb

            conn = duckdb.connect(self._db_path, read_only=True)
            columns = self._columns(conn, table)
            if not columns:
                return []
            listing_col = self._first_column(columns, ["listing_date", "list_date", "ipo_date"])
            delisting_col = self._first_column(columns, ["delisting_date", "delist_date"])
            if listing_col is None or delisting_col is None:
                logger.warning(f"{table} listing/delisting columns missing; inferring universe from bar lifetimes")
                return self._inferred_universe(conn, table, columns, as_of_date)
            query = f"""
                SELECT DISTINCT symbol
                FROM {table}
                WHERE (TRY_CAST({listing_col} AS DATE) IS NULL OR TRY_CAST({listing_col} AS DATE) <= CAST(? AS DATE))
                  AND (TRY_CAST({delisting_col} AS DATE) IS NULL OR TRY_CAST({delisting_col} AS DATE) > CAST(? AS DATE))
                ORDER BY symbol
            """
            rows = conn.execute(query, [as_of_date, as_of_date]).fetchall()
            return [row[0] for row in rows]
        except Exception as e:
            logger.warning(f"PIT universe fetch failed: {e}")
            return []
        finally:
            if conn is not None:
                conn.close()

    def get_bars_pit(self, symbols: List[str], start: str, end: str, as_of_date: str) -> Any:
        import pandas as pd

        if not symbols:
            return pd.DataFrame()
        conn = None
        try:
            import duckdb

            conn = duckdb.connect(self._db_path, read_only=True)
            frames = []
            for table, table_symbols in self._symbols_by_table(symbols).items():
                if not table_symbols:
                    continue
                columns = self._columns(conn, table)
                if not columns:
                    continue
                table_symbols = self._active_symbols(conn, table, columns, table_symbols, as_of_date)
                if not table_symbols:
                    continue
                date_col = self._date_column(columns)
                raw_close_col = columns.get("close")
                adj_close_col = columns.get("adj_close")
                if date_col is None or (raw_close_col is None and adj_close_col is None):
                    continue
                placeholders = ",".join(["?"] * len(table_symbols))
                date_expr = f"{date_col} AS date"
                date_filter = f"TRY_CAST({date_col} AS DATE)"
                select_columns = ["symbol", date_expr]
                if raw_close_col is not None:
                    select_columns.append(raw_close_col)
                else:
                    select_columns.append(f"{adj_close_col} AS close")
                for name in ("open", "high", "low", "volume", "adj_close"):
                    column = columns.get(name)
                    if column is not None and column not in select_columns:
                        select_columns.append(column)
                query = f"""
                    SELECT {", ".join(select_columns)}
                    FROM {table}
                    WHERE symbol IN ({placeholders})
                      AND {date_filter} >= CAST(? AS DATE)
                      AND {date_filter} <= CAST(? AS DATE)
                      AND {date_filter} <= CAST(? AS DATE)
                    ORDER BY date, symbol
                """
                params = table_symbols + [start, end, as_of_date]
                frames.append(self._adjust_prices(conn.execute(query, params).fetchdf()))
            if not frames:
                return pd.DataFrame()
            return pd.concat(frames, ignore_index=True).sort_values(["date", "symbol"])
        except Exception as e:
            logger.warning(f"PIT bars fetch failed: {e}")
            return pd.DataFrame()
        finally:
            if conn is not None:
                conn.close()

    def _columns(self, conn: Any, table: str) -> Dict[str, str]:
        try:
            rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
        except Exception as e:
            logger.warning(f"PIT table unavailable: {table}: {e}")
            return {}
        return {str(row[1]).lower(): str(row[1]) for row in rows}

    def _all_symbols(self, conn: Any, table: str) -> List[str]:
        try:
            rows = conn.execute(f"SELECT DISTINCT symbol FROM {table} ORDER BY symbol").fetchall()
            return [row[0] for row in rows]
        except Exception as e:
            logger.warning(f"PIT all-symbol fallback failed: {e}")
            return []

    def _inferred_universe(self, conn: Any, table: str, columns: Dict[str, str], as_of_date: str) -> List[str]:
        date_col = self._date_column(columns)
        if date_col is None:
            return self._all_symbols(conn, table)
        date_expr = f"TRY_CAST({date_col} AS DATE)"
        query = f"""
            WITH lifetimes AS (
                SELECT symbol, MIN({date_expr}) AS listing_date, MAX({date_expr}) AS last_bar_date
                FROM {table}
                GROUP BY symbol
            ),
            market AS (
                SELECT MAX(last_bar_date) AS market_last_bar_date FROM lifetimes
            )
            SELECT symbol
            FROM lifetimes, market
            WHERE listing_date <= CAST(? AS DATE)
              AND (last_bar_date >= CAST(? AS DATE) OR last_bar_date = market_last_bar_date)
            ORDER BY symbol
        """
        try:
            rows = conn.execute(query, [as_of_date, as_of_date]).fetchall()
            return [row[0] for row in rows]
        except Exception as e:
            logger.warning(f"PIT inferred universe failed: {e}")
            return self._all_symbols(conn, table)

    def _active_symbols(
        self,
        conn: Any,
        table: str,
        columns: Dict[str, str],
        symbols: List[str],
        as_of_date: str,
    ) -> List[str]:
        listing_col = self._first_column(columns, ["listing_date", "list_date", "ipo_date"])
        delisting_col = self._first_column(columns, ["delisting_date", "delist_date"])
        if listing_col is None or delisting_col is None:
            inferred = set(self._inferred_universe(conn, table, columns, as_of_date))
            return [symbol for symbol in symbols if symbol in inferred]
        placeholders = ",".join(["?"] * len(symbols))
        query = f"""
            SELECT DISTINCT symbol
            FROM {table}
            WHERE symbol IN ({placeholders})
              AND (TRY_CAST({listing_col} AS DATE) IS NULL OR TRY_CAST({listing_col} AS DATE) <= CAST(? AS DATE))
              AND (TRY_CAST({delisting_col} AS DATE) IS NULL OR TRY_CAST({delisting_col} AS DATE) > CAST(? AS DATE))
            ORDER BY symbol
        """
        try:
            rows = conn.execute(query, symbols + [as_of_date, as_of_date]).fetchall()
            active = {row[0] for row in rows}
            return [symbol for symbol in symbols if symbol in active]
        except Exception as e:
            logger.warning(f"PIT active-symbol filter failed: {e}")
            return []

    def _first_column(self, columns: Dict[str, str], names: List[str]) -> Optional[str]:
        for name in names:
            column = columns.get(name)
            if column is not None:
                return column
        return None

    def _date_column(self, columns: Dict[str, str]) -> Optional[str]:
        return self._first_column(columns, ["date", "timestamp"])

    def _adjust_prices(self, frame: Any) -> Any:
        if frame is None or frame.empty or "adj_close" not in frame.columns or "close" not in frame.columns:
            return frame
        try:
            import pandas as pd

            adjusted = frame.copy()
            raw_close = pd.to_numeric(adjusted["close"], errors="coerce")
            adj_close = pd.to_numeric(adjusted["adj_close"], errors="coerce")
            factor = (adj_close / raw_close.replace(0, pd.NA)).fillna(1.0)
            for column in ("open", "high", "low", "close"):
                if column in adjusted.columns:
                    adjusted[column] = pd.to_numeric(adjusted[column], errors="coerce") * factor
            adjusted["close"] = adj_close.where(adj_close.notna(), raw_close)
            return adjusted
        except Exception as e:
            logger.warning(f"PIT adjusted price normalization failed: {e}")
            return frame

    def _symbols_by_table(self, symbols: List[str]) -> Dict[str, List[str]]:
        grouped: Dict[str, List[str]] = {"daily_cn": [], "daily_hk": [], "daily_us": []}
        for symbol in symbols:
            grouped[self._table_for_symbol(symbol)].append(symbol)
        return grouped

    def _table_for_market(self, market: str) -> Optional[str]:
        return {
            "cn": "daily_cn",
            "hk": "daily_hk",
            "us": "daily_us",
        }.get(str(market).lower())

    def _table_for_symbol(self, symbol: str) -> str:
        value = str(symbol).strip().upper()
        if value.endswith((".SS", ".SZ")):
            return "daily_cn"
        if value == "HSI" or value.startswith("HK.") or value.endswith(".HK"):
            return "daily_hk"
        bare = value.split(".")[0]
        if bare.isdigit() and len(bare) == 5:
            return "daily_hk"
        if bare.isdigit() and len(bare) == 6:
            return "daily_cn"
        return "daily_us"
