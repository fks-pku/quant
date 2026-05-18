import logging
from typing import Any, Dict, List

from quant.domain.ports.research_market_data import ResearchMarketData
from quant.infrastructure.data.storage_duckdb import _DEFAULT_DAILY_BASIC_DB, _DEFAULT_DB

logger = logging.getLogger(__name__)


class DuckDBResearchMarketData(ResearchMarketData):
    def __init__(
        self,
        db_path: str = _DEFAULT_DB,
        pit_data: Any = None,
        pit_as_of_date: str = None,
        daily_basic_db_path: str = _DEFAULT_DAILY_BASIC_DB,
    ):
        self._db_path = db_path
        self._pit_data = pit_data
        self._pit_as_of_date = pit_as_of_date
        self._daily_basic_db_path = daily_basic_db_path

    def get_universe_symbols(self, market: str) -> List[str]:
        if self._pit_data is not None and self._pit_as_of_date:
            try:
                universe = self._pit_data.get_universe(self._pit_as_of_date, market)
                if universe is not None:
                    return list(universe)
            except Exception as e:
                logger.warning(f"PIT universe fetch failed: {e}")
            return []
        table = self._table_for_market(market)
        if table is None:
            return []
        conn = None
        try:
            import duckdb
            conn = duckdb.connect(self._db_path, read_only=True)
            rows = conn.execute(f"SELECT DISTINCT symbol FROM {table} ORDER BY symbol").fetchall()
            return [row[0] for row in rows]
        except Exception as e:
            logger.warning(f"Universe fetch failed: {e}")
            return []
        finally:
            if conn is not None:
                conn.close()

    def available_fields(self, market: str) -> List[str]:
        table = self._table_for_market(market)
        if table is None:
            return []
        conn = None
        try:
            import duckdb
            conn = duckdb.connect(self._db_path, read_only=True)
            fields = [str(row[1]) for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()]
            if table == "daily_cn_ochl":
                fields.extend([field for field in self._daily_basic_fields(conn) if field not in fields])
            return fields
        except Exception as e:
            logger.warning(f"Field introspection failed for {table}: {e}")
            return []
        finally:
            if conn is not None:
                conn.close()

    def get_daily_bars(self, symbols: List[str], start: str, end: str, fields: List[str] = None) -> Any:
        if not symbols:
            return None
        if self._pit_data is not None and self._pit_as_of_date:
            try:
                return self._pit_data.get_bars_pit(symbols, start, end, self._pit_as_of_date)
            except Exception as e:
                logger.warning(f"PIT market data fetch failed: {e}")
                try:
                    import pandas as pd
                    return pd.DataFrame()
                except Exception:
                    return None
        conn = None
        try:
            import duckdb
            import pandas as pd
            conn = duckdb.connect(self._db_path, read_only=True)
            frames = []
            requested_fields = {str(field) for field in fields} if fields else None
            for table, table_symbols in self._symbols_by_table(symbols).items():
                if not table_symbols:
                    continue
                placeholders = ",".join(["?"] * len(table_symbols))
                date_select, start_filter, end_filter = self._date_expressions(conn, table)
                price_select = self._price_select_columns(conn, table, requested_fields)
                sidecar_select = self._daily_basic_select_columns(conn, table, requested_fields)
                if not price_select:
                    logger.warning(f"Market data table has no price columns: {table}")
                    continue
                select_parts = [price_select]
                join_clause = ""
                if sidecar_select:
                    select_parts.append(sidecar_select)
                    join_clause = """
                    LEFT JOIN daily_basic.cn_daily_basic db
                      ON b.symbol = db.symbol
                     AND CAST(b.timestamp AS DATE) = db.trade_date
                    """
                query = f"""
                    SELECT b.symbol, {date_select}, {", ".join(select_parts)}
                    FROM {table} b
                    {join_clause}
                    WHERE b.symbol IN ({placeholders})
                      AND {start_filter}
                      AND {end_filter}
                    ORDER BY date, symbol
                """
                params = table_symbols + [start, end]
                try:
                    frames.append(conn.execute(query, params).fetchdf())
                except Exception as e:
                    logger.warning(f"Market data fetch failed for {table}: {e}")
            if not frames:
                return pd.DataFrame()
            return pd.concat(frames, ignore_index=True).sort_values(["date", "symbol"])
        except Exception as e:
            logger.warning(f"Market data fetch failed: {e}")
            return None
        finally:
            if conn is not None:
                conn.close()

    def _symbols_by_table(self, symbols: List[str]) -> Dict[str, List[str]]:
        grouped: Dict[str, List[str]] = {"daily_cn_ochl": [], "daily_hk": [], "daily_us": []}
        for symbol in symbols:
            grouped[self._table_for_symbol(symbol)].append(symbol)
        return grouped

    def _table_for_symbol(self, symbol: str) -> str:
        value = str(symbol).strip().upper()
        if value.endswith((".SS", ".SZ")):
            return "daily_cn_ochl"
        if value == "HSI":
            return "daily_hk"
        if value.startswith("HK."):
            return "daily_hk"
        if value.endswith(".HK"):
            return "daily_hk"
        bare = value.split(".")[0]
        if bare.isdigit() and len(bare) == 5:
            return "daily_hk"
        if bare.isdigit() and len(bare) == 6:
            return "daily_cn_ochl"
        return "daily_us"

    def _table_for_market(self, market: str) -> Any:
        return {
            "cn": "daily_cn_ochl",
            "hk": "daily_hk",
            "us": "daily_us",
        }.get(str(market).lower())

    def _date_expressions(self, conn: Any, table: str) -> Any:
        columns = {row[1].lower() for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}
        if "timestamp" in columns:
            return (
                "b.timestamp AS date",
                "b.timestamp >= CAST(? AS TIMESTAMP)",
                "b.timestamp < CAST(? AS TIMESTAMP) + INTERVAL 1 DAY",
            )
        if "date" in columns:
            return (
                "b.date",
                "b.date >= CAST(? AS DATE)",
                "b.date <= CAST(? AS DATE)",
            )
        raise ValueError(f"{table} has neither timestamp nor date column")

    def _price_select_columns(self, conn: Any, table: str, requested_fields: set = None) -> str:
        columns = {str(row[1]).lower(): str(row[1]) for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}
        selects = []
        for column in ("open", "high", "low", "close", "volume"):
            if requested_fields is not None and column not in requested_fields:
                continue
            if column in columns:
                selects.append(f"b.{columns[column]}")
            elif column == "close" and "adj_close" in columns:
                selects.append(f"b.{columns['adj_close']} AS close")
        for column in ("adj_open", "adj_high", "adj_low", "adj_close", "adj_factor"):
            if requested_fields is not None and column not in requested_fields:
                continue
            if column in columns:
                selects.append(f"b.{columns[column]}")
        for column in (
            "turnover",
            "turnover_rate",
            "turnover_rate_f",
            "market_cap",
            "total_market_cap",
            "total_mv",
            "circ_mv",
            "float_market_cap",
            "circulating_market_cap",
            "total_share",
            "float_share",
            "free_share",
        ):
            if requested_fields is not None and column not in requested_fields:
                continue
            if column in columns:
                selects.append(f"b.{columns[column]}")
        return ", ".join(selects)

    def _daily_basic_available(self, conn: Any) -> bool:
        try:
            from pathlib import Path

            path = Path(self._daily_basic_db_path)
            if not path.exists():
                return False
            attached = {
                row[1]
                for row in conn.execute("PRAGMA database_list").fetchall()
                if len(row) > 1
            }
            if "daily_basic" not in attached:
                escaped = str(path).replace("'", "''")
                conn.execute(f"ATTACH IF NOT EXISTS '{escaped}' AS daily_basic (READ_ONLY)")
            exists = conn.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.tables
                WHERE table_catalog = 'daily_basic'
                  AND table_name = 'cn_daily_basic'
                """
            ).fetchone()[0]
            return bool(exists)
        except Exception as e:
            logger.warning(f"Daily basic sidecar unavailable: {e}")
            return False

    def _daily_basic_fields(self, conn: Any) -> List[str]:
        if not self._daily_basic_available(conn):
            return []
        rows = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_catalog = 'daily_basic'
              AND table_name = 'cn_daily_basic'
            ORDER BY ordinal_position
            """
        ).fetchall()
        return [str(row[0]) for row in rows if str(row[0]) not in {"trade_date", "symbol", "ts_code", "updated_at"}]

    def _daily_basic_select_columns(self, conn: Any, table: str, requested_fields: set = None) -> str:
        if table != "daily_cn_ochl":
            return ""
        table_columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}
        sidecar_fields = [
            field
            for field in self._daily_basic_fields(conn)
            if field not in table_columns and (requested_fields is None or field in requested_fields)
        ]
        return ", ".join(f"db.{field}" for field in sidecar_fields)
