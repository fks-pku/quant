"""SQLite/Parquet storage for historical data and persistence."""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import sqlite3
import pandas as pd
import threading

from quant.utils.logger import setup_logger


class Storage:
    """SQLite and Parquet persistence layer."""

    def __init__(self, data_dir: str = "./data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logger = setup_logger("Storage")
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()
        self._init_database()

    def _init_database(self) -> None:
        """Initialize SQLite database with schema."""
        db_path = self.data_dir / "quant.db"
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        cursor = self._conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume INTEGER NOT NULL,
                timeframe TEXT NOT NULL,
                provider TEXT NOT NULL,
                UNIQUE(timestamp, symbol, timeframe, provider)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                price REAL NOT NULL,
                size REAL NOT NULL,
                side TEXT NOT NULL,
                order_id TEXT,
                UNIQUE(timestamp, symbol, order_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE NOT NULL,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                quantity REAL NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                price REAL,
                status TEXT NOT NULL,
                filled_quantity REAL DEFAULT 0,
                avg_fill_price REAL,
                broker TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                total_value REAL NOT NULL,
                cash REAL NOT NULL,
                positions_value REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                margin_used REAL NOT NULL
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_bars_symbol_time
            ON bars(symbol, timestamp)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_orders_symbol
            ON orders(symbol)
        """)

        self._conn.commit()

    def save_bars(
        self,
        df: pd.DataFrame,
        timeframe: str,
        provider: str,
    ) -> None:
        """Save bars to SQLite database."""
        if df.empty:
            return

        with self._lock:
            df = df.copy()
            df["timestamp"] = df["timestamp"].astype(str)
            df["timeframe"] = timeframe
            df["provider"] = provider

            df.to_sql("bars", self._conn, if_exists="append", index=False)
            self._conn.commit()

    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str,
        provider: str,
    ) -> pd.DataFrame:
        """Retrieve bars from SQLite database."""
        with self._lock:
            query = """
                SELECT timestamp, symbol, open, high, low, close, volume
                FROM bars
                WHERE symbol = ? AND timestamp >= ? AND timestamp <= ?
                AND timeframe = ? AND provider = ?
                ORDER BY timestamp ASC
            """
            df = pd.read_sql_query(
                query,
                self._conn,
                params=(symbol, start.isoformat(), end.isoformat(), timeframe, provider),
            )
            return df

    def save_order(self, order: Dict[str, Any]) -> None:
        """Save order to SQLite database."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO orders
                (order_id, timestamp, symbol, quantity, side, order_type, price, status,
                 filled_quantity, avg_fill_price, broker)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order.get("order_id"),
                order.get("timestamp", datetime.now().isoformat()),
                order.get("symbol"),
                order.get("quantity"),
                order.get("side"),
                order.get("order_type"),
                order.get("price"),
                order.get("status"),
                order.get("filled_quantity", 0),
                order.get("avg_fill_price"),
                order.get("broker"),
            ))
            self._conn.commit()

    def update_order_status(
        self,
        order_id: str,
        status: str,
        filled_quantity: Optional[float] = None,
        avg_fill_price: Optional[float] = None,
    ) -> None:
        """Update order status."""
        with self._lock:
            cursor = self._conn.cursor()
            if filled_quantity is not None:
                cursor.execute("""
                    UPDATE orders
                    SET status = ?, filled_quantity = ?, avg_fill_price = ?
                    WHERE order_id = ?
                """, (status, filled_quantity, avg_fill_price, order_id))
            else:
                cursor.execute("""
                    UPDATE orders
                    SET status = ?
                    WHERE order_id = ?
                """, (status, order_id))
            self._conn.commit()

    def get_orders(
        self,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
    ) -> pd.DataFrame:
        """Retrieve orders from database."""
        with self._lock:
            query = "SELECT * FROM orders WHERE 1=1"
            params = []

            if symbol:
                query += " AND symbol = ?"
                params.append(symbol)
            if status:
                query += " AND status = ?"
                params.append(status)

            df = pd.read_sql_query(query, self._conn, params=params)
            return df

    def save_portfolio_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """Save portfolio snapshot to database."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("""
                INSERT INTO portfolio_snapshots
                (timestamp, total_value, cash, positions_value, unrealized_pnl,
                 realized_pnl, margin_used)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                snapshot.get("timestamp", datetime.now().isoformat()),
                snapshot.get("total_value"),
                snapshot.get("cash"),
                snapshot.get("positions_value"),
                snapshot.get("unrealized_pnl"),
                snapshot.get("realized_pnl"),
                snapshot.get("margin_used"),
            ))
            self._conn.commit()

    def get_portfolio_snapshots(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """Retrieve portfolio snapshots."""
        with self._lock:
            query = "SELECT * FROM portfolio_snapshots WHERE 1=1"
            params = []

            if start:
                query += " AND timestamp >= ?"
                params.append(start.isoformat())
            if end:
                query += " AND timestamp <= ?"
                params.append(end.isoformat())

            df = pd.read_sql_query(query, self._conn, params=params)
            return df

    def save_parquet(self, df: pd.DataFrame, filename: str) -> None:
        """Save high-frequency data to Parquet file."""
        if df.empty:
            return

        path = self.data_dir / filename
        df.to_parquet(path, engine="pyarrow", compression="snappy")
        self.logger.info(f"Saved {len(df)} rows to {path}")

    def load_parquet(self, filename: str) -> pd.DataFrame:
        """Load high-frequency data from Parquet file."""
        path = self.data_dir / filename
        if not path.exists():
            return pd.DataFrame()
        return pd.read_parquet(path)

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
