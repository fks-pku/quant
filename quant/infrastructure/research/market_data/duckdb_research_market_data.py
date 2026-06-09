import logging
from typing import Any, Dict, List

from quant.domain.ports.research_market_data import ResearchMarketData
from quant.infrastructure.data.storage_duckdb import (
    _DEFAULT_DAILY_BASIC_DB,
    _DEFAULT_DB,
    _DEFAULT_ETF_DB,
    _DEFAULT_FINANCIAL_INDICATOR_DB,
    _DEFAULT_INDUSTRY_MEMBERSHIP_DB,
    _DEFAULT_INDEX_DB,
    _FINANCIAL_INDICATOR_SCHEMA,
    _FINANCIAL_INDICATOR_TABLE,
    DuckDBStorage,
)

logger = logging.getLogger(__name__)


class DuckDBResearchMarketData(ResearchMarketData):
    def __init__(
        self,
        db_path: str = _DEFAULT_DB,
        pit_data: Any = None,
        pit_as_of_date: str = None,
        daily_basic_db_path: str = _DEFAULT_DAILY_BASIC_DB,
        financial_indicator_db_path: str = _DEFAULT_FINANCIAL_INDICATOR_DB,
        industry_membership_db_path: str = _DEFAULT_INDUSTRY_MEMBERSHIP_DB,
        etf_db_path: str = _DEFAULT_ETF_DB,
        index_db_path: str = _DEFAULT_INDEX_DB,
    ):
        self._db_path = db_path
        self._pit_data = pit_data
        self._pit_as_of_date = pit_as_of_date
        self._daily_basic_db_path = daily_basic_db_path
        self._financial_indicator_db_path = financial_indicator_db_path
        self._industry_membership_db_path = industry_membership_db_path
        self._etf_db_path = etf_db_path
        self._index_db_path = index_db_path

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
            self._attach_sidecars(conn)
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
            self._attach_sidecars(conn)
            fields = [str(row[1]) for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()]
            if table == "daily_cn_ochl":
                fields.extend([field for field in self._daily_basic_fields(conn) if field not in fields])
                fields.extend([field for field in self._financial_indicator_fields(conn) if field not in fields])
                fields.extend([field for field in self._industry_membership_fields(conn) if field not in fields])
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
            self._attach_sidecars(conn)
            frames = []
            requested_fields = {str(field) for field in fields} if fields else None
            for table, table_symbols in self._symbols_by_table(symbols).items():
                if not table_symbols:
                    continue
                placeholders = ",".join(["?"] * len(table_symbols))
                date_select, start_filter, end_filter = self._date_expressions(conn, table)
                price_select = self._price_select_columns(conn, table, requested_fields)
                sidecar_select = self._daily_basic_select_columns(conn, table, requested_fields)
                industry_select = self._industry_membership_select_columns(conn, table, requested_fields)
                if not price_select:
                    logger.warning(f"Market data table has no price columns: {table}")
                    continue
                select_parts = [price_select]
                join_clause = ""
                if sidecar_select:
                    select_parts.append(sidecar_select)
                    join_clause += """
                    LEFT JOIN daily_basic.cn_daily_basic db
                      ON b.symbol = db.symbol
                     AND CAST(b.timestamp AS DATE) = db.trade_date
                    """
                if industry_select:
                    select_parts.append(industry_select)
                    trade_date_expr = self._trade_date_expression(conn, table)
                    join_clause += f"""
                    LEFT JOIN industry_membership.cn_industry_membership im
                      ON b.symbol = im.symbol
                     AND {trade_date_expr} >= im.start_date
                     AND (im.end_date IS NULL OR {trade_date_expr} <= im.end_date)
                     AND COALESCE(im.industry_system, '') = 'SW'
                     AND COALESCE(im.classification_version, '') = 'SW2021'
                     AND COALESCE(im.industry_level, '') = 'L3'
                    """
                query = f"""
                    SELECT b.symbol, {date_select}, {", ".join(select_parts)}
                    FROM {table} b
                    {join_clause}
                    WHERE b.symbol IN ({placeholders})
                      AND {start_filter}
                      AND {end_filter}
                    ORDER BY date, b.symbol
                """
                params = table_symbols + [start, end]
                try:
                    frame = conn.execute(query, params).fetchdf()
                    if table == "daily_cn_ochl":
                        frame = self._add_financial_indicators(conn, frame, requested_fields)
                    frames.append(frame)
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
        grouped: Dict[str, List[str]] = {
            "daily_cn_ochl": [],
            "cn_etf.daily_cn_ochl": [],
            "cn_index.daily_cn_ochl": [],
            "daily_hk": [],
            "daily_us": [],
        }
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
        if DuckDBStorage.is_cn_etf_symbol(bare):
            return "cn_etf.daily_cn_ochl"
        if DuckDBStorage.is_cn_index_symbol(bare):
            return "cn_index.daily_cn_ochl"
        if bare.isdigit() and len(bare) == 5:
            return "daily_hk"
        if bare.isdigit() and len(bare) == 6:
            return "daily_cn_ochl"
        return "daily_us"

    def _table_for_market(self, market: str) -> Any:
        return {
            "cn": "daily_cn_ochl",
            "ashare": "daily_cn_ochl",
            "cn_stock": "daily_cn_ochl",
            "cn_etf": "cn_etf.daily_cn_ochl",
            "etf": "cn_etf.daily_cn_ochl",
            "cn_index": "cn_index.daily_cn_ochl",
            "index": "cn_index.daily_cn_ochl",
            "hk": "daily_hk",
            "us": "daily_us",
        }.get(str(market).lower())

    def _attach_sidecars(self, conn: Any) -> None:
        from pathlib import Path

        for schema, path_text in (("cn_etf", self._etf_db_path), ("cn_index", self._index_db_path)):
            path = Path(path_text)
            if not path.exists():
                continue
            try:
                attached = {
                    row[1]
                    for row in conn.execute("PRAGMA database_list").fetchall()
                    if len(row) > 1
                }
                if schema not in attached:
                    escaped = str(path).replace("'", "''")
                    conn.execute(f"ATTACH IF NOT EXISTS '{escaped}' AS {schema} (READ_ONLY)")
            except Exception as e:
                logger.warning(f"{schema} sidecar unavailable: {e}")

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

    def _trade_date_expression(self, conn: Any, table: str) -> str:
        columns = {row[1].lower() for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}
        if "timestamp" in columns:
            return "CAST(b.timestamp AS DATE)"
        if "date" in columns:
            return "CAST(b.date AS DATE)"
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

    def _financial_indicator_available(self, conn: Any) -> bool:
        try:
            from pathlib import Path

            path = Path(self._financial_indicator_db_path)
            if not path.exists():
                return False
            attached = {
                row[1]
                for row in conn.execute("PRAGMA database_list").fetchall()
                if len(row) > 1
            }
            if _FINANCIAL_INDICATOR_SCHEMA not in attached:
                escaped = str(path).replace("'", "''")
                conn.execute(f"ATTACH IF NOT EXISTS '{escaped}' AS {_FINANCIAL_INDICATOR_SCHEMA} (READ_ONLY)")
            exists = conn.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.tables
                WHERE table_catalog = ?
                  AND table_name = ?
                """,
                [_FINANCIAL_INDICATOR_SCHEMA, _FINANCIAL_INDICATOR_TABLE],
            ).fetchone()[0]
            return bool(exists)
        except Exception as e:
            logger.warning(f"Financial indicator sidecar unavailable: {e}")
            return False

    def _financial_indicator_fields(self, conn: Any) -> List[str]:
        if not self._financial_indicator_available(conn):
            return []
        rows = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_catalog = ?
              AND table_name = ?
            ORDER BY ordinal_position
            """,
            [_FINANCIAL_INDICATOR_SCHEMA, _FINANCIAL_INDICATOR_TABLE],
        ).fetchall()
        keys = {"symbol", "ts_code", "ann_date", "end_date", "updated_at"}
        return [str(row[0]) for row in rows if str(row[0]) not in keys]

    def _financial_indicator_select_columns(self, conn: Any, table: str, requested_fields: set = None) -> str:
        if table != "daily_cn_ochl" or not requested_fields:
            return ""
        fields = [
            field
            for field in self._financial_indicator_fields(conn)
            if field in requested_fields
        ]
        return ", ".join(f"fi.{field}" for field in fields)

    def _industry_membership_available(self, conn: Any) -> bool:
        try:
            from pathlib import Path

            path = Path(self._industry_membership_db_path)
            if not path.exists():
                return False
            attached = {
                row[1]
                for row in conn.execute("PRAGMA database_list").fetchall()
                if len(row) > 1
            }
            if "industry_membership" not in attached:
                escaped = str(path).replace("'", "''")
                conn.execute(f"ATTACH IF NOT EXISTS '{escaped}' AS industry_membership (READ_ONLY)")
            exists = conn.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.tables
                WHERE table_catalog = 'industry_membership'
                  AND table_name = 'cn_industry_membership'
                """
            ).fetchone()[0]
            return bool(exists)
        except Exception as e:
            logger.warning(f"Industry membership sidecar unavailable: {e}")
            return False

    def _industry_membership_fields(self, conn: Any) -> List[str]:
        if not self._industry_membership_available(conn):
            return []
        rows = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_catalog = 'industry_membership'
              AND table_name = 'cn_industry_membership'
            ORDER BY ordinal_position
            """
        ).fetchall()
        keys = {"symbol", "ts_code", "name", "start_date", "end_date", "is_current", "source", "updated_at"}
        return [str(row[0]) for row in rows if str(row[0]) not in keys]

    def _industry_membership_select_columns(self, conn: Any, table: str, requested_fields: set = None) -> str:
        if table != "daily_cn_ochl" or not requested_fields:
            return ""
        fields = [
            field
            for field in self._industry_membership_fields(conn)
            if field in requested_fields
        ]
        return ", ".join(f"im.{field}" for field in fields)

    def _add_financial_indicators(self, conn: Any, frame: Any, requested_fields: set = None) -> Any:
        if frame is None or frame.empty or not requested_fields:
            return frame
        try:
            import pandas as pd
        except Exception:
            return frame
        fields = [field for field in self._financial_indicator_fields(conn) if field in requested_fields]
        if not fields:
            return frame
        result = frame.copy()
        result["_finance_order"] = range(len(result))
        result["_trade_ts"] = pd.to_datetime(result["date"], errors="coerce")
        symbols = [str(symbol) for symbol in result["symbol"].dropna().astype(str).unique().tolist()]
        if not symbols:
            return result.drop(columns=["_finance_order", "_trade_ts"])
        end_day = result["_trade_ts"].dt.date.max()
        placeholders = ", ".join("?" for _ in symbols)
        select_cols = ", ".join(["symbol", "ann_date", "end_date", *fields])
        financial = conn.execute(
            f"""
            SELECT {select_cols}
            FROM {_FINANCIAL_INDICATOR_SCHEMA}.{_FINANCIAL_INDICATOR_TABLE}
            WHERE symbol IN ({placeholders})
              AND ann_date <= ?
            ORDER BY symbol, ann_date, end_date
            """,
            [*symbols, end_day],
        ).fetchdf()
        if financial.empty:
            for field in fields:
                if field not in result.columns:
                    result[field] = pd.NA
            return result.drop(columns=["_finance_order", "_trade_ts"])
        financial["_ann_ts"] = pd.to_datetime(financial["ann_date"], errors="coerce")
        pieces = []
        finance_columns = ["_ann_ts", *fields]
        for symbol, group in result.groupby("symbol", sort=False):
            symbol_financial = financial[financial["symbol"].astype(str) == str(symbol)].sort_values(["_ann_ts", "end_date"])
            ordered_group = group.sort_values("_trade_ts")
            if symbol_financial.empty:
                merged = ordered_group.copy()
                for field in fields:
                    if field not in merged.columns:
                        merged[field] = pd.NA
            else:
                merged = pd.merge_asof(
                    ordered_group,
                    symbol_financial[finance_columns],
                    left_on="_trade_ts",
                    right_on="_ann_ts",
                    direction="backward",
                )
            pieces.append(merged)
        merged_frame = pd.concat(pieces, ignore_index=True).sort_values("_finance_order")
        return merged_frame.drop(columns=[column for column in ("_finance_order", "_trade_ts", "_ann_ts") if column in merged_frame.columns])
