"""DuckDB-based storage for historical market data.

Tables are organized by market, frequency, and CN instrument class:
  - daily_cn_ochl in the main CN stock DB
  - cn_etf.daily_cn_ochl and cn_index.daily_cn_ochl sidecars
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

from quant.domain.models.order import Order
from quant.domain.ports.storage import Storage
from quant.infrastructure.data.fund_classification import classify_cn_fund
from quant.shared.utils.logger import setup_logger
from quant.shared.utils.symbol_utils import detect_market as _detect_market

_PKG_DIR = Path(__file__).resolve().parent.parent  # infrastructure/
_DEFAULT_DUCKDB_DIR = _PKG_DIR / "var" / "duckdb" / "live"
_DEFAULT_DB = str(_DEFAULT_DUCKDB_DIR / "cn_ohlcv.duckdb")
_DEFAULT_ETF_DB = str(_DEFAULT_DUCKDB_DIR / "cn_etf_ohlcv.duckdb")
_DEFAULT_INDEX_DB = str(_DEFAULT_DUCKDB_DIR / "cn_index_ohlcv.duckdb")
_DEFAULT_STATUS_DB = str(_DEFAULT_DUCKDB_DIR / "cn_status.duckdb")
_DEFAULT_DAILY_BASIC_DB = str(_DEFAULT_DUCKDB_DIR / "cn_daily_basic.duckdb")
_DEFAULT_FINANCIAL_INDICATOR_DB = str(_DEFAULT_DUCKDB_DIR / "cn_financial_indicators.duckdb")
_DEFAULT_CORPORATE_ACTIONS_DB = str(_DEFAULT_DUCKDB_DIR / "cn_corporate_actions.duckdb")
_DEFAULT_FUND_META_DB = str(_DEFAULT_DUCKDB_DIR / "cn_fund_meta.duckdb")
_DEFAULT_FUND_NAV_DB = str(_DEFAULT_DUCKDB_DIR / "cn_fund_nav.duckdb")
_STATUS_TABLE = "cn_security_status_daily"
_DAILY_BASIC_TABLE = "cn_daily_basic"
_FINANCIAL_INDICATOR_SCHEMA = "financial_indicator"
_FINANCIAL_INDICATOR_TABLE = "cn_financial_indicators"
_ETF_SCHEMA = "cn_etf"
_INDEX_SCHEMA = "cn_index"
_CORPORATE_ACTIONS_SCHEMA = "corp_actions"
_FUND_META_SCHEMA = "fund_meta"
_FUND_NAV_SCHEMA = "fund_nav"
_CN_DAILY_TABLE = "daily_cn_ochl"
_FUND_INSTRUMENTS_TABLE = "cn_fund_instruments"
_FUND_NAV_TABLE = "cn_fund_nav"
_FUND_CLASSIFICATION_COLUMNS = (
    "classification_version",
    "asset_class",
    "market_region",
    "fund_strategy",
    "fund_category",
    "category_group",
    "classification_source",
    "classification_confidence",
    "classification_reason",
    "classification_excluded",
)
_CN_ETF_PREFIXES = ("15", "16", "50", "51", "52", "56", "58")
_CN_INDEX_SYMBOLS = {"000300", "399001", "399006", "399673"}

BAR_COLUMNS = "timestamp TIMESTAMP, symbol VARCHAR, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT, turnover DOUBLE, adj_open DOUBLE, adj_high DOUBLE, adj_low DOUBLE, adj_close DOUBLE, adj_factor DOUBLE"
BAR_INDEX = "timestamp, symbol"
_BASE_READ_BAR_COLUMNS = (
    "timestamp",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adj_open",
    "adj_high",
    "adj_low",
    "adj_close",
    "adj_factor",
)
_OPTIONAL_READ_BAR_COLUMNS = (
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
)


class DuckDBStorage(Storage):
    def __init__(
        self,
        db_path: str = _DEFAULT_DB,
        read_only: bool = False,
        use_security_status: bool = False,
        status_db_path: str = _DEFAULT_STATUS_DB,
        daily_basic_db_path: str = _DEFAULT_DAILY_BASIC_DB,
        financial_indicator_db_path: str = _DEFAULT_FINANCIAL_INDICATOR_DB,
        etf_db_path: str = _DEFAULT_ETF_DB,
        index_db_path: str = _DEFAULT_INDEX_DB,
        corporate_actions_db_path: str = _DEFAULT_CORPORATE_ACTIONS_DB,
        fund_meta_db_path: str = _DEFAULT_FUND_META_DB,
        fund_nav_db_path: str = _DEFAULT_FUND_NAV_DB,
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
        self._daily_basic_db_path = Path(daily_basic_db_path)
        self._daily_basic_attach_failed = False
        self._financial_indicator_db_path = Path(financial_indicator_db_path)
        self._etf_db_path = Path(etf_db_path)
        self._index_db_path = Path(index_db_path)
        self._corporate_actions_db_path = Path(corporate_actions_db_path)
        self._fund_meta_db_path = Path(fund_meta_db_path)
        self._fund_nav_db_path = Path(fund_nav_db_path)
        self._sidecar_attach_failed: set[str] = set()
        self._init_database()

    def _init_database(self) -> None:
        self._conn = duckdb.connect(str(self.db_path), read_only=self._read_only)
        if not self._read_only:
            self._conn.execute("SET threads=4")
            self._ensure_table(_CN_DAILY_TABLE)
            for table in ("orders", "trades", "portfolio_snapshots", "strategy_snapshots", "instrument_meta"):
                self._ensure_table(table)
            self._ensure_sidecar_attached(_CORPORATE_ACTIONS_SCHEMA, self._corporate_actions_db_path)
            self._ensure_table(f"{_CORPORATE_ACTIONS_SCHEMA}.cn_dividends")
            self._ensure_sidecar_attached(_FUND_META_SCHEMA, self._fund_meta_db_path)
            self._ensure_sidecar_attached(_FUND_NAV_SCHEMA, self._fund_nav_db_path)
        else:
            self._ensure_sidecar_attached(_ETF_SCHEMA, self._etf_db_path)
            self._ensure_sidecar_attached(_INDEX_SCHEMA, self._index_db_path)
            self._ensure_sidecar_attached(_CORPORATE_ACTIONS_SCHEMA, self._corporate_actions_db_path)
            self._ensure_sidecar_attached(_FUND_META_SCHEMA, self._fund_meta_db_path)
            self._ensure_sidecar_attached(_FUND_NAV_SCHEMA, self._fund_nav_db_path)
        self.logger.info(f"DuckDB initialized at {self.db_path} (read_only={self._read_only})")

    @staticmethod
    def is_cn_etf_symbol(symbol: str) -> bool:
        value = str(symbol).strip()
        return value.isdigit() and len(value) == 6 and value.startswith(_CN_ETF_PREFIXES)

    @staticmethod
    def is_cn_index_symbol(symbol: str) -> bool:
        return str(symbol).strip() in _CN_INDEX_SYMBOLS

    @staticmethod
    def _base_table_name(table_name: str) -> str:
        return str(table_name).split(".")[-1]

    def _ensure_sidecar_attached(self, schema: str, path: Path) -> bool:
        if schema in self._sidecar_attach_failed:
            return False
        if self._read_only and not path.exists():
            return False
        if not self._read_only:
            path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            try:
                attached = {
                    row[1]
                    for row in self.conn.execute("PRAGMA database_list").fetchall()
                    if len(row) > 1
                }
                if schema in attached:
                    return True
                escaped = str(path).replace("'", "''")
                read_only_suffix = " (READ_ONLY)" if self._read_only else ""
                self.conn.execute(f"ATTACH IF NOT EXISTS '{escaped}' AS {schema}{read_only_suffix}")
                return True
            except Exception as e:
                self._sidecar_attach_failed.add(schema)
                self.logger.warning(f"DuckDB sidecar {schema} unavailable: {e}")
                return False

    def _table_exists(self, table_name: str) -> bool:
        try:
            self.conn.execute(f"SELECT 1 FROM {table_name} LIMIT 0")
            return True
        except Exception:
            return False

    def _ensure_columns(self, table_name: str, columns: Dict[str, str]) -> None:
        if self._read_only:
            return
        try:
            rows = self.conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        except Exception:
            return
        existing = {str(row[1]) for row in rows}
        for column_name, column_type in columns.items():
            if column_name in existing:
                continue
            self.conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

    def _ensure_table(self, table_name: str) -> None:
        if self._read_only:
            return
        base_table = self._base_table_name(table_name)
        if base_table.startswith(("daily_", "minute_")):
            self._conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    {BAR_COLUMNS}
                )
            """)
            try:
                index_name = f"idx_{str(table_name).replace('.', '_')}_ts_sym"
                self._conn.execute(f"""
                    CREATE UNIQUE INDEX IF NOT EXISTS {index_name}
                    ON {table_name}({BAR_INDEX})
                """)
            except Exception:
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
        elif table_name == f"{_CORPORATE_ACTIONS_SCHEMA}.cn_dividends":
            self._conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
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
        elif table_name == f"{_FUND_META_SCHEMA}.{_FUND_INSTRUMENTS_TABLE}":
            self._conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    symbol VARCHAR PRIMARY KEY,
                    ts_code VARCHAR DEFAULT '',
                    name VARCHAR DEFAULT '',
                    fund_type VARCHAR DEFAULT '',
                    instrument_type VARCHAR DEFAULT '',
                    status VARCHAR DEFAULT '',
                    market VARCHAR DEFAULT '',
                    list_date VARCHAR DEFAULT '',
                    delist_date VARCHAR DEFAULT '',
                    index_code VARCHAR DEFAULT '',
                    index_name VARCHAR DEFAULT '',
                    exchange VARCHAR DEFAULT '',
                    management VARCHAR DEFAULT '',
                    custodian VARCHAR DEFAULT '',
                    found_date VARCHAR DEFAULT '',
                    due_date VARCHAR DEFAULT '',
                    issue_date VARCHAR DEFAULT '',
                    issue_amount DOUBLE,
                    m_fee DOUBLE,
                    c_fee DOUBLE,
                    duration_year DOUBLE,
                    p_value DOUBLE,
                    min_amount DOUBLE,
                    exp_return DOUBLE,
                    benchmark VARCHAR DEFAULT '',
                    invest_type VARCHAR DEFAULT '',
                    type VARCHAR DEFAULT '',
                    trustee VARCHAR DEFAULT '',
                    purc_startdate VARCHAR DEFAULT '',
                    redm_startdate VARCHAR DEFAULT '',
                    csname VARCHAR DEFAULT '',
                    extname VARCHAR DEFAULT '',
                    cname VARCHAR DEFAULT '',
                    setup_date VARCHAR DEFAULT '',
                    mgr_name VARCHAR DEFAULT '',
                    custod_name VARCHAR DEFAULT '',
                    mgt_fee DOUBLE,
                    etf_type VARCHAR DEFAULT '',
                    classification_version VARCHAR DEFAULT '',
                    asset_class VARCHAR DEFAULT '',
                    market_region VARCHAR DEFAULT '',
                    fund_strategy VARCHAR DEFAULT '',
                    fund_category VARCHAR DEFAULT '',
                    category_group VARCHAR DEFAULT '',
                    classification_source VARCHAR DEFAULT '',
                    classification_confidence DOUBLE,
                    classification_reason VARCHAR DEFAULT '',
                    classification_excluded BOOLEAN DEFAULT FALSE,
                    updated_at TIMESTAMP
                )
            """)
            self._ensure_columns(
                table_name,
                {
                    "management": "VARCHAR DEFAULT ''",
                    "custodian": "VARCHAR DEFAULT ''",
                    "found_date": "VARCHAR DEFAULT ''",
                    "due_date": "VARCHAR DEFAULT ''",
                    "issue_date": "VARCHAR DEFAULT ''",
                    "issue_amount": "DOUBLE",
                    "m_fee": "DOUBLE",
                    "c_fee": "DOUBLE",
                    "duration_year": "DOUBLE",
                    "p_value": "DOUBLE",
                    "min_amount": "DOUBLE",
                    "exp_return": "DOUBLE",
                    "benchmark": "VARCHAR DEFAULT ''",
                    "invest_type": "VARCHAR DEFAULT ''",
                    "type": "VARCHAR DEFAULT ''",
                    "trustee": "VARCHAR DEFAULT ''",
                    "purc_startdate": "VARCHAR DEFAULT ''",
                    "redm_startdate": "VARCHAR DEFAULT ''",
                    "csname": "VARCHAR DEFAULT ''",
                    "extname": "VARCHAR DEFAULT ''",
                    "cname": "VARCHAR DEFAULT ''",
                    "setup_date": "VARCHAR DEFAULT ''",
                    "mgr_name": "VARCHAR DEFAULT ''",
                    "custod_name": "VARCHAR DEFAULT ''",
                    "mgt_fee": "DOUBLE",
                    "etf_type": "VARCHAR DEFAULT ''",
                    "classification_version": "VARCHAR DEFAULT ''",
                    "asset_class": "VARCHAR DEFAULT ''",
                    "market_region": "VARCHAR DEFAULT ''",
                    "fund_strategy": "VARCHAR DEFAULT ''",
                    "fund_category": "VARCHAR DEFAULT ''",
                    "category_group": "VARCHAR DEFAULT ''",
                    "classification_source": "VARCHAR DEFAULT ''",
                    "classification_confidence": "DOUBLE",
                    "classification_reason": "VARCHAR DEFAULT ''",
                    "classification_excluded": "BOOLEAN DEFAULT FALSE",
                },
            )
        elif table_name == f"{_FUND_NAV_SCHEMA}.{_FUND_NAV_TABLE}":
            self._conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    symbol VARCHAR,
                    nav_date DATE,
                    ann_date VARCHAR DEFAULT '',
                    unit_nav DOUBLE,
                    accum_nav DOUBLE,
                    accum_div DOUBLE,
                    adj_nav DOUBLE,
                    net_asset DOUBLE,
                    total_netasset DOUBLE,
                    PRIMARY KEY (symbol, nav_date)
                )
            """)
            self._ensure_columns(
                table_name,
                {
                    "ann_date": "VARCHAR DEFAULT ''",
                    "accum_div": "DOUBLE",
                },
            )

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
            if self.is_cn_etf_symbol(symbol):
                self._ensure_sidecar_attached(_ETF_SCHEMA, self._etf_db_path)
                return f"{_ETF_SCHEMA}.{_CN_DAILY_TABLE}"
            if self.is_cn_index_symbol(symbol):
                self._ensure_sidecar_attached(_INDEX_SCHEMA, self._index_db_path)
                return f"{_INDEX_SCHEMA}.{_CN_DAILY_TABLE}"
            return _CN_DAILY_TABLE
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
        if table_name == _CN_DAILY_TABLE and self._is_daily_timeframe(timeframe):
            status_frame = self._get_status_enriched_cn_bars([symbol], start, end)
            if status_frame is not None:
                return status_frame
            sidecar_frame = self._get_daily_basic_enriched_cn_bars([symbol], start, end)
            if sidecar_frame is not None:
                return sidecar_frame
        if table_name == f"{_ETF_SCHEMA}.{_CN_DAILY_TABLE}" and self._is_daily_timeframe(timeframe):
            fund_frame = self._get_fund_enriched_bars([symbol], start, end)
            if fund_frame is not None:
                return fund_frame

        try:
            if not self._table_exists(table_name):
                return pd.DataFrame()
        except Exception:
            return pd.DataFrame()

        select_cols = ", ".join(self._read_bar_columns(table_name))
        query = f"SELECT {select_cols} FROM {table_name} WHERE symbol = ?"
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
            self.conn.execute("SELECT 1")
        except Exception:
            return pd.DataFrame()

        frames = []
        with self._lock:
            for table_name, table_symbols in symbols_by_table.items():
                if not self._table_exists(table_name):
                    continue
                if table_name == _CN_DAILY_TABLE and self._is_daily_timeframe(timeframe):
                    frame = self._get_status_enriched_cn_bars(table_symbols, start, end)
                    if frame is not None:
                        if not frame.empty:
                            frames.append(frame)
                        continue
                    frame = self._get_daily_basic_enriched_cn_bars(table_symbols, start, end)
                    if frame is not None:
                        if not frame.empty:
                            frames.append(frame)
                        continue
                if table_name == f"{_ETF_SCHEMA}.{_CN_DAILY_TABLE}" and self._is_daily_timeframe(timeframe):
                    frame = self._get_fund_enriched_bars(table_symbols, start, end)
                    if frame is not None:
                        if not frame.empty:
                            frames.append(frame)
                        continue
                placeholders = ", ".join("?" for _ in table_symbols)
                select_cols = ", ".join(self._read_bar_columns(table_name))
                query = f"SELECT {select_cols} FROM {table_name} WHERE symbol IN ({placeholders})"
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

    def _read_bar_columns(self, table_name: str) -> List[str]:
        cols = list(_BASE_READ_BAR_COLUMNS)
        cols.extend(self._available_columns(table_name, _OPTIONAL_READ_BAR_COLUMNS))
        return cols

    def _available_columns(self, table_name: str, candidates: tuple[str, ...]) -> List[str]:
        try:
            rows = self.conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        except Exception:
            return []
        existing = {str(row[1]) for row in rows}
        return [col for col in candidates if col in existing and col not in _BASE_READ_BAR_COLUMNS]

    def _daily_basic_available(self) -> bool:
        if self._daily_basic_attach_failed:
            return False
        if not self._daily_basic_db_path.exists():
            return False
        with self._lock:
            try:
                attached = {
                    row[1]
                    for row in self.conn.execute("PRAGMA database_list").fetchall()
                    if len(row) > 1
                }
                if "daily_basic" not in attached:
                    path = str(self._daily_basic_db_path).replace("'", "''")
                    self.conn.execute(f"ATTACH IF NOT EXISTS '{path}' AS daily_basic (READ_ONLY)")
                exists = self.conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.tables
                    WHERE table_catalog = 'daily_basic'
                      AND table_name = ?
                    """,
                    [_DAILY_BASIC_TABLE],
                ).fetchone()[0]
                return bool(exists)
            except Exception as e:
                self._daily_basic_attach_failed = True
                self.logger.warning(f"Daily basic sidecar unavailable: {e}")
                return False

    def _fund_meta_available(self) -> bool:
        if not self._fund_meta_db_path.exists():
            return False
        if not self._ensure_sidecar_attached(_FUND_META_SCHEMA, self._fund_meta_db_path):
            return False
        return self._table_exists(f"{_FUND_META_SCHEMA}.{_FUND_INSTRUMENTS_TABLE}")

    def _fund_nav_available(self) -> bool:
        if not self._fund_nav_db_path.exists():
            return False
        if not self._ensure_sidecar_attached(_FUND_NAV_SCHEMA, self._fund_nav_db_path):
            return False
        return self._table_exists(f"{_FUND_NAV_SCHEMA}.{_FUND_NAV_TABLE}")

    def _get_fund_enriched_bars(
        self,
        symbols: List[str],
        start: Optional[datetime],
        end: Optional[datetime],
    ) -> Optional[pd.DataFrame]:
        meta_available = self._fund_meta_available()
        nav_available = self._fund_nav_available()
        if not meta_available and not nav_available:
            return None
        table_symbols = list(dict.fromkeys(symbols))
        placeholders = ", ".join("?" for _ in table_symbols)
        bar_columns = self._read_bar_columns(f"{_ETF_SCHEMA}.{_CN_DAILY_TABLE}")
        select_columns = [f"b.{col}" for col in bar_columns]
        joins = []
        if meta_available:
            select_columns.extend(
                [
                    "m.name AS fund_name",
                    "m.fund_type",
                    "m.instrument_type",
                    "m.status AS fund_status",
                    "m.market AS fund_market",
                    "m.list_date AS fund_list_date",
                    "m.delist_date AS fund_delist_date",
                    "m.index_code",
                    "m.index_name",
                    "m.exchange AS fund_exchange",
                    "m.management",
                    "m.custodian",
                    "m.found_date",
                    "m.due_date",
                    "m.issue_date",
                    "m.benchmark",
                    "m.invest_type",
                    "m.type AS fund_contract_type",
                    "m.purc_startdate",
                    "m.redm_startdate",
                    "m.setup_date",
                    "m.mgr_name",
                    "m.custod_name",
                    "m.etf_type",
                    "m.classification_version",
                    "m.asset_class",
                    "m.market_region",
                    "m.fund_strategy",
                    "m.fund_category",
                    "m.category_group",
                    "m.classification_source",
                    "m.classification_confidence",
                    "m.classification_reason",
                    "m.classification_excluded",
                ]
            )
            joins.append(
                f"""
                LEFT JOIN {_FUND_META_SCHEMA}.{_FUND_INSTRUMENTS_TABLE} m
                  ON b.symbol = m.symbol
                """
            )
        if nav_available:
            select_columns.extend(
                [
                    "n.unit_nav",
                    "n.accum_nav",
                    "n.adj_nav",
                    "n.net_asset",
                    "n.total_netasset",
                ]
            )
            joins.append(
                f"""
                LEFT JOIN {_FUND_NAV_SCHEMA}.{_FUND_NAV_TABLE} n
                  ON b.symbol = n.symbol
                 AND CAST(b.timestamp AS DATE) = n.nav_date
                """
            )
        nav_value_expr = None
        size_expr = None
        if nav_available:
            nav_value_expr = "n.unit_nav"
            size_expr = "COALESCE(n.total_netasset, n.net_asset)"
        if nav_value_expr:
            select_columns.append(f"CASE WHEN {nav_value_expr} > 0 THEN b.close / {nav_value_expr} - 1 ELSE NULL END AS premium_rate")
        if size_expr:
            select_columns.append(f"{size_expr} AS fund_size")
        query = f"""
            SELECT {", ".join(select_columns)}
            FROM {_ETF_SCHEMA}.{_CN_DAILY_TABLE} b
            {" ".join(joins)}
            WHERE b.symbol IN ({placeholders})
        """
        params: list = list(table_symbols)
        if start is not None:
            query += " AND b.timestamp >= ?"
            params.append(start)
        if end is not None:
            query += " AND b.timestamp <= ?"
            params.append(end)
        query += " ORDER BY b.symbol ASC, b.timestamp ASC"
        try:
            with self._lock:
                return self.conn.execute(query, params).fetchdf()
        except Exception as e:
            self.logger.warning(f"Fund sidecar join failed, falling back to OHLC bars: {e}")
            return None

    def _daily_basic_columns(self) -> set:
        if not self._daily_basic_available():
            return set()
        try:
            rows = self.conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_catalog = 'daily_basic'
                  AND table_name = ?
                """,
                [_DAILY_BASIC_TABLE],
            ).fetchall()
        except Exception:
            return set()
        return {str(row[0]) for row in rows}

    def _daily_basic_sidecar_columns(self, existing_bar_columns: List[str]) -> List[str]:
        existing = set(existing_bar_columns)
        daily_basic_columns = self._daily_basic_columns()
        return [
            col
            for col in _OPTIONAL_READ_BAR_COLUMNS
            if col in daily_basic_columns and col not in existing
        ]

    def _get_daily_basic_enriched_cn_bars(
        self,
        symbols: List[str],
        start: Optional[datetime],
        end: Optional[datetime],
    ) -> Optional[pd.DataFrame]:
        if not symbols or not self._daily_basic_available():
            return None
        table_symbols = list(dict.fromkeys(symbols))
        placeholders = ", ".join("?" for _ in table_symbols)
        bar_columns = list(_BASE_READ_BAR_COLUMNS[2:]) + self._available_columns(_CN_DAILY_TABLE, _OPTIONAL_READ_BAR_COLUMNS)
        sidecar_columns = self._daily_basic_sidecar_columns(bar_columns)
        if not sidecar_columns:
            return None
        select_columns = [
            "b.timestamp",
            "b.symbol",
            *[f"b.{col}" for col in bar_columns],
            *[f"db.{col}" for col in sidecar_columns],
        ]
        query = f"""
            SELECT {", ".join(select_columns)}
            FROM {_CN_DAILY_TABLE} b
            LEFT JOIN daily_basic.{_DAILY_BASIC_TABLE} db
              ON b.symbol = db.symbol
             AND CAST(b.timestamp AS DATE) = db.trade_date
            WHERE b.symbol IN ({placeholders})
        """
        params: list = list(table_symbols)
        if start is not None:
            query += " AND b.timestamp >= ?"
            params.append(start)
        if end is not None:
            query += " AND b.timestamp <= ?"
            params.append(end)
        query += " ORDER BY b.symbol ASC, b.timestamp ASC"
        try:
            with self._lock:
                return self.conn.execute(query, params).fetchdf()
        except Exception as e:
            self.logger.warning(f"Daily basic sidecar join failed, falling back to OHLC bars: {e}")
            return None

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
        bar_columns = list(_BASE_READ_BAR_COLUMNS[2:]) + self._available_columns(_CN_DAILY_TABLE, _OPTIONAL_READ_BAR_COLUMNS)
        bar_select = ",\n                ".join(f"b.{col}" for col in bar_columns)
        sidecar_columns = self._daily_basic_sidecar_columns(bar_columns)
        sidecar_select = ""
        sidecar_join = ""
        if sidecar_columns:
            sidecar_select = ",\n                " + ",\n                ".join(f"db.{col}" for col in sidecar_columns)
            sidecar_join = f"""
            LEFT JOIN daily_basic.{_DAILY_BASIC_TABLE} db
              ON s.symbol = db.symbol
             AND s.trade_date = db.trade_date
            """
        query = f"""
            SELECT
                CAST(s.trade_date AS TIMESTAMP) AS timestamp,
                s.symbol,
                {bar_select}{sidecar_select},
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
            LEFT JOIN {_CN_DAILY_TABLE} b
              ON s.symbol = b.symbol
             AND s.trade_date = CAST(b.timestamp AS DATE)
            {sidecar_join}
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

        numeric_cols = (
            "open",
            "high",
            "low",
            "close",
            "volume",
            "adj_open",
            "adj_high",
            "adj_low",
            "adj_close",
            "adj_factor",
            "up_limit",
            "down_limit",
            "status_pre_close",
            *_OPTIONAL_READ_BAR_COLUMNS,
        )
        for col in numeric_cols:
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
        table_name = _CN_DAILY_TABLE if freq == "daily" and market == "cn" else f"{freq}_{market}"
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
        main_catalog = self.conn.execute("SELECT current_database()").fetchone()[0]
        rows = self.conn.execute(
            """
            SELECT table_catalog, table_name
            FROM information_schema.tables
            WHERE table_schema = 'main'
            ORDER BY table_catalog, table_name
            """
        ).fetchall()
        return [
            str(table) if str(catalog) == str(main_catalog) else f"{catalog}.{table}"
            for catalog, table in rows
        ]

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
        table_name = f"{_CORPORATE_ACTIONS_SCHEMA}.cn_dividends"
        self._ensure_sidecar_attached(_CORPORATE_ACTIONS_SCHEMA, self._corporate_actions_db_path)
        self._ensure_table(table_name)
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
            self.conn.execute(f"DELETE FROM {table_name} WHERE symbol = ?", [symbol])
            self.conn.execute(f"INSERT INTO {table_name} SELECT * FROM df")
        self.logger.info(f"Saved {len(df)} dividend records for {symbol}")
        return len(df)

    def save_cn_fund_instruments(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        table_name = f"{_FUND_META_SCHEMA}.{_FUND_INSTRUMENTS_TABLE}"
        self._ensure_sidecar_attached(_FUND_META_SCHEMA, self._fund_meta_db_path)
        self._ensure_table(table_name)
        frame = df.copy()
        if "symbol" not in frame.columns and "ts_code" in frame.columns:
            frame["symbol"] = frame["ts_code"].astype(str).str.split(".").str[0]
        text_cols = (
            "ts_code",
            "name",
            "fund_type",
            "instrument_type",
            "status",
            "market",
            "list_date",
            "delist_date",
            "index_code",
            "index_name",
            "exchange",
            "management",
            "custodian",
            "found_date",
            "due_date",
            "issue_date",
            "benchmark",
            "invest_type",
            "type",
            "trustee",
            "purc_startdate",
            "redm_startdate",
            "csname",
            "extname",
            "cname",
            "setup_date",
            "mgr_name",
            "custod_name",
            "etf_type",
            "classification_version",
            "asset_class",
            "market_region",
            "fund_strategy",
            "fund_category",
            "category_group",
            "classification_source",
            "classification_reason",
        )
        numeric_cols = (
            "issue_amount",
            "m_fee",
            "c_fee",
            "duration_year",
            "p_value",
            "min_amount",
            "exp_return",
            "mgt_fee",
            "classification_confidence",
        )
        for col in text_cols:
            if col not in frame.columns:
                frame[col] = ""
            frame[col] = frame[col].fillna("").astype(str)
        for col in numeric_cols:
            if col not in frame.columns:
                frame[col] = pd.NA
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        if "classification_excluded" not in frame.columns:
            frame["classification_excluded"] = False
        classifications = frame.apply(lambda row: classify_cn_fund(row.to_dict()).as_dict(), axis=1)
        for idx, classification in classifications.items():
            for col, value in classification.items():
                frame.at[idx, col] = value
        frame["updated_at"] = pd.Timestamp.now("UTC").tz_localize(None)
        cols = [
            "symbol",
            "ts_code",
            "name",
            "fund_type",
            "instrument_type",
            "status",
            "market",
            "list_date",
            "delist_date",
            "index_code",
            "index_name",
            "exchange",
            "management",
            "custodian",
            "found_date",
            "due_date",
            "issue_date",
            "issue_amount",
            "m_fee",
            "c_fee",
            "duration_year",
            "p_value",
            "min_amount",
            "exp_return",
            "benchmark",
            "invest_type",
            "type",
            "trustee",
            "purc_startdate",
            "redm_startdate",
            "csname",
            "extname",
            "cname",
            "setup_date",
            "mgr_name",
            "custod_name",
            "mgt_fee",
            "etf_type",
            "classification_version",
            "asset_class",
            "market_region",
            "fund_strategy",
            "fund_category",
            "category_group",
            "classification_source",
            "classification_confidence",
            "classification_reason",
            "classification_excluded",
            "updated_at",
        ]
        frame = frame[cols].dropna(subset=["symbol"]).drop_duplicates(subset=["symbol"], keep="last")
        col_sql = ", ".join(cols)
        with self._lock:
            self.conn.execute(f"INSERT OR REPLACE INTO {table_name} ({col_sql}) SELECT {col_sql} FROM frame")
        self.logger.info(f"Saved {len(frame)} CN fund instruments")
        return len(frame)

    def refresh_cn_fund_classifications(self) -> int:
        table_name = f"{_FUND_META_SCHEMA}.{_FUND_INSTRUMENTS_TABLE}"
        self._ensure_sidecar_attached(_FUND_META_SCHEMA, self._fund_meta_db_path)
        if not self._table_exists(table_name):
            return 0
        frame = self.conn.execute(
            f"""
            SELECT *
            FROM {table_name}
            """
        ).fetchdf()
        if frame.empty:
            return 0
        classifications = frame.apply(lambda row: classify_cn_fund(row.to_dict()).as_dict(), axis=1)
        update_frame = pd.DataFrame([{"symbol": frame.loc[idx, "symbol"], **classification} for idx, classification in classifications.items()])
        cols = list(_FUND_CLASSIFICATION_COLUMNS)
        set_sql = ", ".join(f"{col} = c.{col}" for col in cols)
        select_sql = ", ".join(["symbol", *cols])
        with self._lock:
            self.conn.execute(
                f"""
                UPDATE {table_name} AS t
                SET {set_sql}
                FROM (SELECT {select_sql} FROM update_frame) AS c
                WHERE t.symbol = c.symbol
                """
            )
        self.logger.info(f"Refreshed CN fund classifications for {len(update_frame)} instruments")
        return len(update_frame)

    def save_cn_fund_nav(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        table_name = f"{_FUND_NAV_SCHEMA}.{_FUND_NAV_TABLE}"
        self._ensure_sidecar_attached(_FUND_NAV_SCHEMA, self._fund_nav_db_path)
        self._ensure_table(table_name)
        frame = df.copy()
        if "symbol" not in frame.columns and "ts_code" in frame.columns:
            frame["symbol"] = frame["ts_code"].astype(str).str.split(".").str[0]
        if "nav_date" not in frame.columns and "trade_date" in frame.columns:
            frame["nav_date"] = frame["trade_date"]
        frame["nav_date"] = pd.to_datetime(frame["nav_date"], errors="coerce").dt.date
        if "ann_date" not in frame.columns:
            frame["ann_date"] = ""
        frame["ann_date"] = frame["ann_date"].fillna("").astype(str)
        for col in ("unit_nav", "accum_nav", "accum_div", "adj_nav", "net_asset", "total_netasset"):
            if col not in frame.columns:
                frame[col] = pd.NA
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        cols = ["symbol", "nav_date", "ann_date", "unit_nav", "accum_nav", "accum_div", "adj_nav", "net_asset", "total_netasset"]
        frame = frame[cols].dropna(subset=["symbol", "nav_date"]).drop_duplicates(subset=["symbol", "nav_date"], keep="last")
        with self._lock:
            self.conn.execute(
                f"DELETE FROM {table_name} WHERE (symbol, nav_date) IN (SELECT symbol, nav_date FROM frame)"
            )
            col_sql = ", ".join(cols)
            self.conn.execute(f"INSERT INTO {table_name} ({col_sql}) SELECT {col_sql} FROM frame")
        self.logger.info(f"Saved {len(frame)} CN fund NAV rows")
        return len(frame)

    def get_cn_dividends(
        self,
        symbol: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> pd.DataFrame:
        table_name = f"{_CORPORATE_ACTIONS_SCHEMA}.cn_dividends"
        if not self._ensure_sidecar_attached(_CORPORATE_ACTIONS_SCHEMA, self._corporate_actions_db_path):
            return pd.DataFrame()
        self._ensure_table(table_name)
        query = f"SELECT * FROM {table_name} WHERE 1=1"
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
