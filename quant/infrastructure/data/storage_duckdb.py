"""DuckDB-based storage for historical market data.

Replaces SQLite+Parquet with a single DuckDB columnar database.
Tables are organized by market and frequency:
  - daily_cn_ochl, daily_hk, daily_us, minute_hk, minute_us
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

from quant.domain.models.order import Order
from quant.domain.ports.storage import Storage
from quant.shared.utils.logger import setup_logger
from quant.shared.utils.symbol_utils import detect_market as _detect_market

_PKG_DIR = Path(__file__).resolve().parent.parent  # infrastructure/
_DEFAULT_DB = str(_PKG_DIR / "var" / "duckdb" / "quant.duckdb")
_DEFAULT_STATUS_DB = str(_PKG_DIR / "var" / "duckdb" / "security_status.duckdb")
_STATUS_TABLE = "cn_security_status_daily"

BAR_COLUMNS = "timestamp TIMESTAMP, symbol VARCHAR, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT, turnover DOUBLE, adj_open DOUBLE, adj_high DOUBLE, adj_low DOUBLE, adj_close DOUBLE, adj_factor DOUBLE"
BAR_INDEX = "timestamp, symbol"


class DuckDBStorage(Storage):
    def __init__(
        self,
        db_path: str = _DEFAULT_DB,
        read_only: bool = False,
        use_security_status: bool = False,
        status_db_path: str = _DEFAULT_STATUS_DB,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.logger = setup_logger("DuckDBStorage")
        self._lock = threading.RLock()
        self._conn: Optional[duckdb.DuckDBPyConnection] = None
        self._read_only = read_only
        self._use_security_status = use_security_status
        self._status_db_path = Path(status_db_path)
        self._status_attach_failed = False
        self._init_database()

    def _init_database(self) -> None:
        self._conn = duckdb.connect(str(self.db_path), read_only=self._read_only)
        if not self._read_only:
            self._conn.execute("SET threads=4")
            for table in ("orders", "trades", "portfolio_snapshots", "strategy_snapshots", "instrument_meta", "cn_dividends"):
                self._ensure_table(table)
        self.logger.info(f"DuckDB initialized at {self.db_path} (read_only={self._read_only})")

    def _ensure_table(self, table_name: str) -> None:
        if self._read_only:
            return
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
        elif table_name == "cn_dividends":
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS cn_dividends (
                    symbol VARCHAR,
                    ex_date TIMESTAMP,
                    cash_dividend DOUBLE DEFAULT 0,
                    stock_dividend DOUBLE DEFAULT 0,
                    allotment_ratio DOUBLE DEFAULT 0,
                    allotment_price DOUBLE DEFAULT 0,
                    record_date VARCHAR DEFAULT '',
                    pay_date VARCHAR DEFAULT '',
                    ann_date VARCHAR DEFAULT '',
                    PRIMARY KEY (symbol, ex_date)
                )
            """)

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            with self._lock:
                if self._conn is None:
                    self._conn = duckdb.connect(str(self.db_path), read_only=self._read_only)
        return self._conn

    def _resolve_table(self, symbol: str, timeframe: str) -> str:
        freq = "daily" if timeframe in ("1d", "day", "daily") else "minute"
        market = _detect_market(symbol).lower()
        if freq == "daily" and market == "cn":
            return "daily_cn_ochl"
        return f"{freq}_{market}"

    def save_bars(self, df: pd.DataFrame, timeframe: str = "1d") -> int:
        if df is None or df.empty:
            return 0

        df = df.copy()
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
        if "turnover" not in df.columns:
            df["turnover"] = pd.NA
        for adj_col in ("adj_open", "adj_high", "adj_low", "adj_close", "adj_factor"):
            if adj_col not in df.columns:
                df[adj_col] = pd.NA

        symbol = df["symbol"].iloc[0] if "symbol" in df.columns else ""
        table_name = self._resolve_table(symbol, timeframe)
        self._ensure_table(table_name)

        cols = [c for c in [
            "timestamp", "symbol", "open", "high", "low", "close", "volume", "turnover",
            "adj_open", "adj_high", "adj_low", "adj_close", "adj_factor",
        ] if c in df.columns]
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
        if table_name == "daily_cn_ochl" and self._is_daily_timeframe(timeframe):
            status_frame = self._get_status_enriched_cn_bars([symbol], start, end)
            if status_frame is not None:
                return status_frame

        try:
            tables = self.conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()
            if (table_name,) not in tables:
                return pd.DataFrame()
        except Exception:
            return pd.DataFrame()

        query = f"SELECT timestamp, symbol, open, high, low, close, volume, adj_open, adj_high, adj_low, adj_close, adj_factor FROM {table_name} WHERE symbol = ?"
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

    def get_bars_for_symbols(
        self,
        symbols: List[str],
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        timeframe: str = "1d",
    ) -> pd.DataFrame:
        if not symbols:
            return pd.DataFrame()

        unique_symbols = list(dict.fromkeys(symbols))
        symbols_by_table: Dict[str, List[str]] = {}
        for symbol in unique_symbols:
            table_name = self._resolve_table(symbol, timeframe)
            symbols_by_table.setdefault(table_name, []).append(symbol)

        try:
            existing_tables = {
                row[0]
                for row in self.conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
                ).fetchall()
            }
        except Exception:
            return pd.DataFrame()

        frames = []
        with self._lock:
            for table_name, table_symbols in symbols_by_table.items():
                if table_name not in existing_tables:
                    continue
                if table_name == "daily_cn_ochl" and self._is_daily_timeframe(timeframe):
                    frame = self._get_status_enriched_cn_bars(table_symbols, start, end)
                    if frame is not None:
                        if not frame.empty:
                            frames.append(frame)
                        continue
                placeholders = ", ".join("?" for _ in table_symbols)
                query = (
                    "SELECT timestamp, symbol, open, high, low, close, volume, "
                    f"adj_open, adj_high, adj_low, adj_close, adj_factor FROM {table_name} "
                    f"WHERE symbol IN ({placeholders})"
                )
                params: list = list(table_symbols)
                if start is not None:
                    query += " AND timestamp >= ?"
                    params.append(start)
                if end is not None:
                    query += " AND timestamp <= ?"
                    params.append(end)
                query += " ORDER BY symbol ASC, timestamp ASC"
                frame = self.conn.execute(query, params).fetchdf()
                if not frame.empty:
                    frames.append(frame)

        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    @staticmethod
    def _is_daily_timeframe(timeframe: str) -> bool:
        return timeframe in ("1d", "day", "daily")

    def _status_available(self) -> bool:
        if not self._use_security_status or self._status_attach_failed:
            return False
        if not self._status_db_path.exists():
            return False
        with self._lock:
            try:
                attached = {
                    row[1]
                    for row in self.conn.execute("PRAGMA database_list").fetchall()
                    if len(row) > 1
                }
                if "security_status" not in attached:
                    path = str(self._status_db_path).replace("'", "''")
                    self.conn.execute(f"ATTACH IF NOT EXISTS '{path}' AS security_status (READ_ONLY)")
                exists = self.conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.tables
                    WHERE table_catalog = 'security_status'
                      AND table_name = ?
                    """,
                    [_STATUS_TABLE],
                ).fetchone()[0]
                return bool(exists)
            except Exception as e:
                self._status_attach_failed = True
                self.logger.warning(f"Security status table unavailable: {e}")
                return False

    def _get_status_enriched_cn_bars(
        self,
        symbols: List[str],
        start: Optional[datetime],
        end: Optional[datetime],
    ) -> Optional[pd.DataFrame]:
        if not self._status_available():
            return None
        if not symbols:
            return pd.DataFrame()

        table_symbols = list(dict.fromkeys(symbols))
        placeholders = ", ".join("?" for _ in table_symbols)
        query = f"""
            SELECT
                CAST(s.trade_date AS TIMESTAMP) AS timestamp,
                s.symbol,
                b.open,
                b.high,
                b.low,
                b.close,
                b.volume,
                b.adj_open,
                b.adj_high,
                b.adj_low,
                b.adj_close,
                b.adj_factor,
                s.is_st,
                s.st_type,
                s.is_suspended AS status_is_suspended,
                s.has_daily_bar,
                s.tradable,
                s.up_limit,
                s.down_limit,
                s.pre_close AS status_pre_close,
                s.is_listed,
                s.list_status,
                s.suspend_type,
                s.suspend_timing
            FROM security_status.{_STATUS_TABLE} s
            LEFT JOIN daily_cn_ochl b
              ON s.symbol = b.symbol
             AND s.trade_date = CAST(b.timestamp AS DATE)
            WHERE s.symbol IN ({placeholders})
        """
        params: list = list(table_symbols)
        if start is not None:
            query += " AND s.trade_date >= CAST(? AS DATE)"
            params.append(start)
        if end is not None:
            query += " AND s.trade_date <= CAST(? AS DATE)"
            params.append(end)
        query += " ORDER BY s.symbol ASC, s.trade_date ASC"

        try:
            with self._lock:
                frame = self.conn.execute(query, params).fetchdf()
        except Exception as e:
            self.logger.warning(f"Security status join failed, falling back to OHLC bars: {e}")
            return None
        return self._normalize_status_enriched_bars(frame)

    @staticmethod
    def _normalize_status_enriched_bars(frame: pd.DataFrame) -> pd.DataFrame:
        if frame is None or frame.empty:
            return pd.DataFrame()

        result = frame.copy()
        result["timestamp"] = pd.to_datetime(result["timestamp"])
        result = result.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
        has_daily_bar = result["has_daily_bar"].fillna(True).astype(bool)
        tradable = result["tradable"].fillna(has_daily_bar).astype(bool)
        status_suspended = result["status_is_suspended"].fillna(False).astype(bool)
        synthetic = ~has_daily_bar

        for col in ("open", "high", "low", "close", "volume", "adj_open", "adj_high", "adj_low", "adj_close", "adj_factor", "up_limit", "down_limit", "status_pre_close"):
            if col in result.columns:
                result[col] = pd.to_numeric(result[col], errors="coerce")

        price = result["close"].where(result["close"].notna(), result["status_pre_close"])
        price = price.groupby(result["symbol"]).ffill()
        limit_mid = (result["up_limit"] + result["down_limit"]) / 2
        price = price.where(price.notna(), limit_mid)

        for col in ("open", "high", "low", "close"):
            result[col] = result[col].where(result[col].notna(), price)
        result["volume"] = result["volume"].fillna(0)

        adj_pairs = {
            "adj_open": "open",
            "adj_high": "high",
            "adj_low": "low",
            "adj_close": "close",
        }
        for adj_col, price_col in adj_pairs.items():
            result.loc[synthetic & result[adj_col].isna(), adj_col] = result.loc[synthetic, price_col]
        result.loc[synthetic & result["adj_factor"].isna(), "adj_factor"] = 1.0

        result["_suspended"] = status_suspended | (~tradable)
        result["_has_daily_bar"] = has_daily_bar
        result["is_st"] = result["is_st"].fillna(False).astype(bool)
        result["tradable"] = tradable
        result["has_daily_bar"] = has_daily_bar
        result["st_type"] = result["st_type"].fillna("")
        result["list_status"] = result["list_status"].fillna("")
        result["suspend_type"] = result["suspend_type"].fillna("")
        result["suspend_timing"] = result["suspend_timing"].fillna("")
        result = result.drop(columns=["status_is_suspended", "status_pre_close"])
        return result

    def get_symbols(self, timeframe: str = "1d", market: str = "hk") -> List[str]:
        freq = timeframe if timeframe in ("daily", "minute") else "daily"
        market = str(market).lower()
        table_name = "daily_cn_ochl" if freq == "daily" and market == "cn" else f"{freq}_{market}"
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
            row = result.fetchone()
            return row[0] if row else 0

    def add_column(self, table_name: str, column_name: str, column_type: str = "DOUBLE", default: str = "NULL") -> None:
        self._ensure_table(table_name)
        with self._lock:
            self.conn.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {column_name} {column_type} DEFAULT {default}")
        self.logger.info(f"Added column {column_name} ({column_type}) to {table_name}")

    def save_order(self, order: "Order") -> None:  # type: ignore[override]
        self._ensure_table("orders")
        with self._lock:
            self.conn.execute("""
                INSERT OR REPLACE INTO orders
                (order_id, timestamp, symbol, quantity, side, order_type, price, status, filled_quantity, avg_fill_price, broker)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                order.order_id,
                order.timestamp,
                order.symbol,
                order.quantity,
                order.side.value if hasattr(order.side, 'value') else order.side,
                order.order_type.value if hasattr(order.order_type, 'value') else order.order_type,
                order.price,
                order.status.value if hasattr(order.status, 'value') else order.status,
                getattr(order, 'filled_quantity', 0) or 0,
                getattr(order, 'avg_fill_price', None),
                getattr(order, 'broker', None),
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

    def save_cn_dividends(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        self._ensure_table("cn_dividends")
        df = df.copy()
        if "ex_date" in df.columns:
            df["ex_date"] = pd.to_datetime(df["ex_date"])
        cols = [c for c in [
            "symbol", "ex_date", "cash_dividend", "stock_dividend",
            "allotment_ratio", "allotment_price", "record_date", "pay_date", "ann_date",
        ] if c in df.columns]
        df = df[cols]
        df = df.drop_duplicates(subset=["symbol", "ex_date"], keep="last")
        with self._lock:
            symbol = df["symbol"].iloc[0] if "symbol" in df.columns else ""
            self.conn.execute("DELETE FROM cn_dividends WHERE symbol = ?", [symbol])
            self.conn.execute("INSERT INTO cn_dividends SELECT * FROM df")
        self.logger.info(f"Saved {len(df)} dividend records for {symbol}")
        return len(df)

    def get_cn_dividends(
        self,
        symbol: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> pd.DataFrame:
        self._ensure_table("cn_dividends")
        query = "SELECT * FROM cn_dividends WHERE 1=1"
        params: list = []
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if start:
            query += " AND ex_date >= ?"
            params.append(start)
        if end:
            query += " AND ex_date <= ?"
            params.append(end)
        query += " ORDER BY ex_date ASC"
        with self._lock:
            return self.conn.execute(query, params).fetchdf()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
