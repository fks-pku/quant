from pathlib import Path
from typing import List, Optional

import duckdb
import pandas as pd

from quant.domain.ports import ResearchMarketData


class DuckDBResearchMarketData(ResearchMarketData):
    def __init__(self, db_path: str | Path, read_only: bool = True):
        self.db_path = Path(db_path)
        self.read_only = read_only
        self._conn: Optional[duckdb.DuckDBPyConnection] = None

    def get_daily_bars(self, symbols: List[str], start: str, end: str):
        if not symbols:
            return pd.DataFrame()
        conn = self._connect()
        if conn is None:
            return pd.DataFrame()

        frames = []
        symbol_params = list(symbols)
        placeholders = ", ".join("?" for _ in symbol_params)
        for table in self._daily_tables(conn):
            try:
                frames.append(
                    conn.execute(
                        f"""
                        SELECT
                            timestamp AS date,
                            symbol,
                            open,
                            high,
                            low,
                            close,
                            volume
                        FROM {table}
                        WHERE symbol IN ({placeholders})
                          AND timestamp >= ?
                          AND timestamp <= ?
                        ORDER BY timestamp ASC, symbol ASC
                        """,
                        [*symbol_params, start, end],
                    ).fetchdf()
                )
            except Exception:
                continue
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _connect(self) -> Optional[duckdb.DuckDBPyConnection]:
        if self._conn is not None:
            return self._conn
        if self.read_only and not self.db_path.exists():
            return None
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = duckdb.connect(str(self.db_path), read_only=self.read_only)
        except Exception:
            return None
        return self._conn

    @staticmethod
    def _daily_tables(conn: duckdb.DuckDBPyConnection) -> List[str]:
        try:
            rows = conn.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema='main' AND table_name LIKE 'daily_%'
                ORDER BY table_name
                """
            ).fetchall()
        except Exception:
            return []
        return [row[0] for row in rows]
