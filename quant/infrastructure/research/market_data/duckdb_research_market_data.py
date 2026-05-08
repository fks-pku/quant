import logging
from typing import Any, Dict, List

from quant.domain.ports.research_market_data import ResearchMarketData

logger = logging.getLogger(__name__)


class DuckDBResearchMarketData(ResearchMarketData):
    def __init__(self, db_path: str = "quant/infrastructure/var/market.duckdb"):
        self._db_path = db_path

    def get_universe_symbols(self, market: str) -> List[str]:
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

    def get_daily_bars(self, symbols: List[str], start: str, end: str) -> Any:
        if not symbols:
            return None
        conn = None
        try:
            import duckdb
            import pandas as pd
            conn = duckdb.connect(self._db_path, read_only=True)
            frames = []
            for table, table_symbols in self._symbols_by_table(symbols).items():
                if not table_symbols:
                    continue
                placeholders = ",".join(["?"] * len(table_symbols))
                date_select, start_filter, end_filter = self._date_expressions(conn, table)
                query = f"""
                    SELECT symbol, {date_select}, open, high, low, close, volume
                    FROM {table}
                    WHERE symbol IN ({placeholders})
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
        grouped: Dict[str, List[str]] = {"daily_cn": [], "daily_hk": [], "daily_us": []}
        for symbol in symbols:
            grouped[self._table_for_symbol(symbol)].append(symbol)
        return grouped

    def _table_for_symbol(self, symbol: str) -> str:
        value = str(symbol).strip().upper()
        if value.endswith((".SS", ".SZ")):
            return "daily_cn"
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
            return "daily_cn"
        return "daily_us"

    def _table_for_market(self, market: str) -> Any:
        return {
            "cn": "daily_cn",
            "hk": "daily_hk",
            "us": "daily_us",
        }.get(str(market).lower())

    def _date_expressions(self, conn: Any, table: str) -> Any:
        columns = {row[1].lower() for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}
        if "timestamp" in columns:
            return (
                "timestamp AS date",
                "timestamp >= CAST(? AS TIMESTAMP)",
                "timestamp < CAST(? AS TIMESTAMP) + INTERVAL 1 DAY",
            )
        if "date" in columns:
            return (
                "date",
                "date >= CAST(? AS DATE)",
                "date <= CAST(? AS DATE)",
            )
        raise ValueError(f"{table} has neither timestamp nor date column")
