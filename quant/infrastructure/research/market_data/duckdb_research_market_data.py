import logging
from typing import Any, List

from quant.domain.ports.research_market_data import ResearchMarketData

logger = logging.getLogger(__name__)


class DuckDBResearchMarketData(ResearchMarketData):
    def __init__(self, db_path: str = "quant/infrastructure/var/market.duckdb"):
        self._db_path = db_path

    def get_daily_bars(self, symbols: List[str], start: str, end: str) -> Any:
        try:
            import duckdb
            import pandas as pd
            conn = duckdb.connect(self._db_path, read_only=True)
            placeholders = ",".join(["?"] * len(symbols))
            query = f"""
                SELECT symbol, date, open, high, low, close, volume
                FROM bars
                WHERE symbol IN ({placeholders})
                  AND date >= ?
                  AND date <= ?
                ORDER BY date
            """
            params = symbols + [start, end]
            df = conn.execute(query, params).fetchdf()
            conn.close()
            return df
        except Exception as e:
            logger.warning(f"Market data fetch failed: {e}")
            return None
