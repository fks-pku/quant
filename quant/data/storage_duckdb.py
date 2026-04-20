"""DuckDB-based storage for historical market data.

Replaces SQLite+Parquet with a single DuckDB columnar database.
Tables are organized by market and frequency:
  - daily_hk, daily_us, minute_hk, minute_us
  - orders, trades, portfolio_snapshots

Supports ALTER TABLE ADD COLUMN for schema evolution without rewriting data.
Sparse columns (NULL for most rows) have near-zero storage overhead due to
DuckDB's validity bitmap + columnar compression.
"""

import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import duckdb
import pandas as pd

from quant.utils.logger import setup_logger

_PKG_DIR = Path(__file__).resolve().parent.parent  # quant/
_DEFAULT_DB = str(_PKG_DIR / "var" / "duckdb" / "quant.duckdb")

BAR_COLUMNS = "timestamp TIMESTAMP, symbol VARCHAR, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT, turnover DOUBLE"
BAR_INDEX = "timestamp, symbol"


class DuckDBStorage:
    def __init__(self, db_path: str = _DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.logger = setup_logger("DuckDBStorage")
        self._lock = threading.RLock()
        self._conn: Optional[duckdb.DuckDBPyConnection] = None
        self._init_database()

    def _init_database(self) -> None:
        self._conn = duckdb.connect(str(self.db_path))
        self._conn.execute("SET threads=4")
        for table in ("orders", "trades", "portfolio_snapshots", "strategy_snapshots", "instrument_meta"):
            self._ensure_table(table)
        self.logger.info(f"DuckDB initialized at {self.db_path}")

    def _ensure_table(self, table_name: str) -> None:
        if table_name.startswith(("daily_", "minute_")):
            self._conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    {BAR_COLUMNS}
                )
            """)
            try:
                self._conn.execute(f"""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_{table_name}_ts_sym
                    ON {table_name}({BAR_INDEX})
                """)
            except duckdb.CatalogException:
                pass
        elif table_name == "orders":
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    order_id VARCHAR PRIMARY KEY,
                    timestamp TIMESTAMP,
                    symbol VARCHAR,
                    quantity DOUBLE,
                    side VARCHAR,
                    order_type VARCHAR,
                    price DOUBLE,
                    status VARCHAR,
                    filled_quantity DOUBLE DEFAULT 0,
                    avg_fill_price DOUBLE,
                    broker VARCHAR
                )
            """)
        elif table_name == "trades":
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    timestamp TIMESTAMP,
                    symbol VARCHAR,
                    price DOUBLE,
                    size DOUBLE,
                    side VARCHAR,
                    order_id VARCHAR
                )
            """)
        elif table_name == "portfolio_snapshots":
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                    timestamp TIMESTAMP,
                    total_value DOUBLE,
                    cash DOUBLE,
                    positions_value DOUBLE,
                    unrealized_pnl DOUBLE,
                    realized_pnl DOUBLE,
                    margin_used DOUBLE
                )
            """)
        elif table_name == "instrument_meta":
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS instrument_meta (
                    symbol VARCHAR PRIMARY KEY,
                    lot_size INTEGER DEFAULT 100,
                    market VARCHAR DEFAULT 'HK',
                    name VARCHAR DEFAULT ''
                )
            """)
        elif table_name == "strategy_snapshots":
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS strategy_snapshots (
                    date VARCHAR,
                    strategy_name VARCHAR,
                    nav DOUBLE,
                    market_value DOUBLE,
                    cash DOUBLE,
                    unrealized_pnl DOUBLE,
                    realized_pnl DOUBLE
                )
            """)

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self._conn = duckdb.connect(str(self.db_path))
        return self._conn

    def _resolve_table(self, symbol: str, timeframe: str) -> str:
        freq = "daily" if timeframe in ("1d", "day", "daily") else "minute"
        market = "hk" if (symbol.startswith("HK.") or (symbol.isdigit() and len(symbol) >= 5)) else "us"
        return f"{freq}_{market}"

    def save_bars(self, df: pd.DataFrame, timeframe: str = "1d") -> int:
        if df is None or df.empty:
            return 0

        df = df.copy()
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
        if "turnover" not in df.columns:
            df["turnover"] = pd.NA

        symbol = df["symbol"].iloc[0] if "symbol" in df.columns else ""
        table_name = self._resolve_table(symbol, timeframe)
        self._ensure_table(table_name)

        cols = [c for c in ["timestamp", "symbol", "open", "high", "low", "close", "volume", "turnover"] if c in df.columns]
        df = df[cols]

        with self._lock:
            self.conn.execute(f"DELETE FROM {table_name} WHERE symbol = ? AND timestamp IN (SELECT timestamp FROM df WHERE symbol = ?)", [symbol, symbol])
            self.conn.execute(f"INSERT INTO {table_name} SELECT * FROM df")
            row_count = len(df)
        self.logger.info(f"Saved {row_count} bars to {table_name} for {symbol}")
        return row_count

    def get_bars(
        self,
        symbol: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        timeframe: str = "1d",
    ) -> pd.DataFrame:
        table_name = self._resolve_table(symbol, timeframe)

        try:
            tables = self.conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()
            if (table_name,) not in tables:
                return pd.DataFrame()
        except Exception:
            return pd.DataFrame()

        query = f"SELECT timestamp, symbol, open, high, low, close, volume FROM {table_name} WHERE symbol = ?"
        params: list = [symbol]

        if start is not None:
            query += " AND timestamp >= ?"
            params.append(start)
        if end is not None:
            query += " AND timestamp <= ?"
            params.append(end)

        query += " ORDER BY timestamp ASC"
        with self._lock:
            return self.conn.execute(query, params).fetchdf()

    def get_symbols(self, timeframe: str = "1d", market: str = "hk") -> List[str]:
        table_name = f"{timeframe if timeframe in ('daily', 'minute') else 'daily'}_{market}"
        try:
            df = self.conn.execute(f"SELECT DISTINCT symbol FROM {table_name}").fetchdf()
            return df["symbol"].tolist()
        except Exception:
            return []

    def get_date_range(self, symbol: str, timeframe: str = "1d") -> Optional[Dict[str, datetime]]:
        table_name = self._resolve_table(symbol, timeframe)
        try:
            df = self.conn.execute(
                f"SELECT MIN(timestamp) as min_ts, MAX(timestamp) as max_ts FROM {table_name} WHERE symbol = ?",
                [symbol],
            ).fetchdf()
            if df.empty or pd.isna(df["min_ts"].iloc[0]):
                return None
            return {
                "start": pd.Timestamp(df["min_ts"].iloc[0]).to_pydatetime(),
                "end": pd.Timestamp(df["max_ts"].iloc[0]).to_pydatetime(),
            }
        except Exception:
            return None

    def delete_bars(self, symbol: str, timeframe: str = "1d", start: Optional[datetime] = None, end: Optional[datetime] = None) -> int:
        table_name = self._resolve_table(symbol, timeframe)
        query = f"DELETE FROM {table_name} WHERE symbol = ?"
        params: list = [symbol]
        if start is not None:
            query += " AND timestamp >= ?"
            params.append(start)
        if end is not None:
            query += " AND timestamp <= ?"
            params.append(end)
        with self._lock:
            result = self.conn.execute(query, params)
            return result.fetchone()[0] if result.fetchone() else 0

    def add_column(self, table_name: str, column_name: str, column_type: str = "DOUBLE", default: str = "NULL") -> None:
        self._ensure_table(table_name)
        with self._lock:
            self.conn.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {column_name} {column_type} DEFAULT {default}")
        self.logger.info(f"Added column {column_name} ({column_type}) to {table_name}")

    def save_order(self, order: Dict[str, Any]) -> None:
        self._ensure_table("orders")
        with self._lock:
            self.conn.execute("""
                INSERT OR REPLACE INTO orders
                (order_id, timestamp, symbol, quantity, side, order_type, price, status, filled_quantity, avg_fill_price, broker)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                order.get("order_id"),
                order.get("timestamp", datetime.now()),
                order.get("symbol"),
                order.get("quantity"),
                order.get("side"),
                order.get("order_type"),
                order.get("price"),
                order.get("status"),
                order.get("filled_quantity", 0),
                order.get("avg_fill_price"),
                order.get("broker"),
            ])

    def update_order_status(self, order_id: str, status: str, filled_quantity: Optional[float] = None, avg_fill_price: Optional[float] = None) -> None:
        with self._lock:
            if filled_quantity is not None:
                self.conn.execute("UPDATE orders SET status=?, filled_quantity=?, avg_fill_price=? WHERE order_id=?", [status, filled_quantity, avg_fill_price, order_id])
            else:
                self.conn.execute("UPDATE orders SET status=? WHERE order_id=?", [status, order_id])

    def get_orders(self, symbol: Optional[str] = None, status: Optional[str] = None) -> pd.DataFrame:
        query = "SELECT * FROM orders WHERE 1=1"
        params: list = []
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if status:
            query += " AND status = ?"
            params.append(status)
        with self._lock:
            return self.conn.execute(query, params).fetchdf()

    def save_portfolio_snapshot(self, snapshot: Dict[str, Any]) -> None:
        self._ensure_table("portfolio_snapshots")
        with self._lock:
            self.conn.execute("""
                INSERT INTO portfolio_snapshots
                (timestamp, total_value, cash, positions_value, unrealized_pnl, realized_pnl, margin_used)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [
                snapshot.get("timestamp", datetime.now()),
                snapshot.get("total_value"),
                snapshot.get("cash"),
                snapshot.get("positions_value"),
                snapshot.get("unrealized_pnl"),
                snapshot.get("realized_pnl"),
                snapshot.get("margin_used"),
            ])

    def get_portfolio_snapshots(self, start: Optional[datetime] = None, end: Optional[datetime] = None) -> pd.DataFrame:
        query = "SELECT * FROM portfolio_snapshots WHERE 1=1"
        params: list = []
        if start:
            query += " AND timestamp >= ?"
            params.append(start)
        if end:
            query += " AND timestamp <= ?"
            params.append(end)
        with self._lock:
            return self.conn.execute(query, params).fetchdf()

    def list_tables(self) -> List[str]:
        df = self.conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main' ORDER BY table_name").fetchdf()
        return df["table_name"].tolist()

    def table_info(self, table_name: str) -> pd.DataFrame:
        return self.conn.execute(f"DESCRIBE {table_name}").fetchdf()

    def table_row_count(self, table_name: str) -> int:
        try:
            return self.conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        except Exception:
            return 0

    def save_instrument_meta(self, symbol: str, lot_size: int = 100, market: str = "HK", name: str = "") -> None:
        self._ensure_table("instrument_meta")
        with self._lock:
            self.conn.execute("""
                INSERT OR REPLACE INTO instrument_meta (symbol, lot_size, market, name)
                VALUES (?, ?, ?, ?)
            """, [symbol, lot_size, market, name])

    def get_lot_size(self, symbol: str) -> int:
        self._ensure_table("instrument_meta")
        try:
            result = self.conn.execute(
                "SELECT lot_size FROM instrument_meta WHERE symbol = ?", [symbol]
            ).fetchone()
            return result[0] if result else 100
        except Exception:
            return 100

    def get_all_instrument_meta(self) -> pd.DataFrame:
        self._ensure_table("instrument_meta")
        try:
            return self.conn.execute("SELECT * FROM instrument_meta").fetchdf()
        except Exception:
            return pd.DataFrame()

    def save_strategy_snapshot(self, snapshot: Dict[str, Any]) -> None:
        self._ensure_table("strategy_snapshots")
        with self._lock:
            self.conn.execute("""
                INSERT INTO strategy_snapshots
                (date, strategy_name, nav, market_value, cash, unrealized_pnl, realized_pnl)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [
                snapshot.get("date"),
                snapshot.get("strategy_name"),
                snapshot.get("nav"),
                snapshot.get("market_value"),
                snapshot.get("cash"),
                snapshot.get("unrealized_pnl"),
                snapshot.get("realized_pnl"),
            ])

    def get_strategy_snapshots(self, strategy_name: Optional[str] = None) -> List[Dict[str, Any]]:
        self._ensure_table("strategy_snapshots")
        with self._lock:
            if strategy_name:
                df = self.conn.execute(
                    "SELECT * FROM strategy_snapshots WHERE strategy_name = ? ORDER BY date ASC",
                    [strategy_name],
                ).fetchdf()
            else:
                df = self.conn.execute(
                    "SELECT * FROM strategy_snapshots ORDER BY date ASC"
                ).fetchdf()
        if df.empty:
            return []
        return df.to_dict(orient="records")

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
