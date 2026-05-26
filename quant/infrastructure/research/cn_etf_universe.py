from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from quant.infrastructure.data.fund_classification import classify_cn_fund
from quant.infrastructure.data.storage_duckdb import (
    _DEFAULT_ETF_DB,
    _DEFAULT_FUND_META_DB,
    _DEFAULT_FUND_NAV_DB,
    _FUND_ETF_BENCHMARK_TABLE,
    _FUND_ETF_SHARE_SIZE_TABLE,
)


_RISK_CATEGORY_ORDER = ("sse50", "csi300", "chinext", "chinext50", "dividend")
_DEFENSIVE_CATEGORY_ORDER = ("gold",)
_BARBELL_CATEGORY_GROUPS = set(_RISK_CATEGORY_ORDER + _DEFENSIVE_CATEGORY_ORDER)
_CLASSIFICATION_METADATA_FIELDS = (
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
    "benchmark",
    "invest_type",
    "type",
    "etf_type",
    "csname",
    "extname",
    "cname",
)
_BENCHMARK_METADATA_FIELDS = ("bmk_level", "bmk_type", "bmk_src", "idx_type", "bmk_name", "bmk_fullname")


def build_gold_equity_barbell_pit_universe(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    fund_meta_db_path: str = _DEFAULT_FUND_META_DB,
    etf_db_path: str = _DEFAULT_ETF_DB,
    fund_nav_db_path: str = _DEFAULT_FUND_NAV_DB,
    universe_as_of: Optional[Any] = None,
    min_history_days_as_of: int = 0,
    max_symbols_per_category: int = 0,
    universe_start: Optional[Any] = None,
    universe_end: Optional[Any] = None,
) -> Dict[str, Any]:
    as_of = _parse_datetime(universe_as_of)
    window_start = _parse_datetime(universe_start) or start
    window_end = _parse_datetime(universe_end) or end
    rows = _load_etf_metadata_rows(
        None if as_of else window_start,
        as_of or window_end,
        fund_meta_db_path,
        etf_db_path,
        fund_nav_db_path,
    )
    risk = {category: [] for category in _RISK_CATEGORY_ORDER}
    defensive = {category: [] for category in _DEFENSIVE_CATEGORY_ORDER}
    audit_rows = []
    min_history = max(0, int(min_history_days_as_of or 0))
    category_cap = max(0, int(max_symbols_per_category or 0))
    for row in rows:
        category = _barbell_category_from_row(row)
        if not category:
            continue
        item = {
            "symbol": str(row.get("symbol")),
            "name": row.get("name") or "",
            "index_name": row.get("index_name") or "",
            "list_date": row.get("list_date") or "",
            "delist_date": row.get("delist_date") or "",
            "bar_rows": int(row.get("bar_rows") or 0),
            "size_rows": int(row.get("size_rows") or 0),
            "as_of_size": float(row.get("as_of_size") or 0.0),
            "category": category,
            "fund_category": row.get("fund_category") or "",
            "category_group": row.get("category_group") or category,
            "classification_confidence": float(row.get("classification_confidence") or 0.0),
            "classification_reason": row.get("classification_reason") or "",
        }
        if item["bar_rows"] <= 0 or item["size_rows"] <= 0:
            continue
        if min_history and item["bar_rows"] < min_history:
            continue
        if category in risk:
            risk[category].append(item)
        elif category in defensive:
            defensive[category].append(item)
        audit_rows.append(item)
    risk_symbols = _category_symbol_map(risk, category_cap)
    defensive_symbols = _category_symbol_map(defensive, category_cap)
    return {
        "risk_category_symbols": risk_symbols,
        "defensive_category_symbols": defensive_symbols,
        "symbols": flatten_category_symbols(risk_symbols, defensive_symbols),
        "audit": audit_rows,
        "universe_as_of": as_of.date().isoformat() if as_of else "",
        "universe_start": window_start.date().isoformat() if window_start else "",
        "universe_end": window_end.date().isoformat() if window_end else "",
        "universe_min_history_days_as_of": min_history,
        "universe_max_symbols_per_category": category_cap,
        "universe_selection_policy": _universe_selection_policy(as_of, category_cap),
        "classification_version": "cn_fund_taxonomy_v1",
    }


def classify_gold_equity_barbell_category(name: Any, index_name: Any) -> Optional[str]:
    return _barbell_category_from_row({"name": name or "", "index_name": index_name or ""})


def build_pit_fund_category_universe(
    categories: List[str],
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    fund_meta_db_path: str = _DEFAULT_FUND_META_DB,
    etf_db_path: str = _DEFAULT_ETF_DB,
    fund_nav_db_path: str = _DEFAULT_FUND_NAV_DB,
    universe_as_of: Optional[Any] = None,
    min_history_days_as_of: int = 0,
    max_symbols_per_category: int = 0,
    universe_start: Optional[Any] = None,
    universe_end: Optional[Any] = None,
    category_field: str = "fund_category",
    instrument_types: Optional[List[str]] = None,
) -> Dict[str, Any]:
    requested = [str(category) for category in categories if str(category)]
    as_of = _parse_datetime(universe_as_of)
    window_start = _parse_datetime(universe_start) or start
    window_end = _parse_datetime(universe_end) or end
    rows = _load_fund_metadata_rows(
        None if as_of else window_start,
        as_of or window_end,
        fund_meta_db_path,
        etf_db_path,
        fund_nav_db_path,
        instrument_types or ["ETF"],
    )
    buckets = {category: [] for category in requested}
    audit_rows = []
    min_history = max(0, int(min_history_days_as_of or 0))
    category_cap = max(0, int(max_symbols_per_category or 0))
    for row in rows:
        classification = classify_cn_fund(row)
        classified = classification.as_dict()
        key = str(classified.get(category_field) or "")
        if key not in buckets:
            continue
        item = {
            "symbol": str(row.get("symbol")),
            "name": row.get("name") or "",
            "index_name": row.get("index_name") or "",
            "list_date": row.get("list_date") or "",
            "delist_date": row.get("delist_date") or "",
            "bar_rows": int(row.get("bar_rows") or 0),
            "size_rows": int(row.get("size_rows") or 0),
            "as_of_size": float(row.get("as_of_size") or 0.0),
            **classified,
        }
        if item["bar_rows"] <= 0 or item["size_rows"] <= 0:
            continue
        if item.get("classification_excluded"):
            continue
        if min_history and item["bar_rows"] < min_history:
            continue
        buckets[key].append(item)
        audit_rows.append(item)
    category_symbols = _category_symbol_map(buckets, category_cap)
    return {
        "category_symbols": category_symbols,
        "symbols": flatten_category_symbols(category_symbols),
        "audit": audit_rows,
        "categories": requested,
        "category_field": category_field,
        "instrument_types": instrument_types or ["ETF"],
        "classification_version": "cn_fund_taxonomy_v1",
        "universe_as_of": as_of.date().isoformat() if as_of else "",
        "universe_start": window_start.date().isoformat() if window_start else "",
        "universe_end": window_end.date().isoformat() if window_end else "",
        "universe_min_history_days_as_of": min_history,
        "universe_max_symbols_per_category": category_cap,
        "universe_selection_policy": _universe_selection_policy(as_of, category_cap),
    }


def flatten_category_symbols(*category_maps: Dict[str, List[str]]) -> List[str]:
    values = []
    for category_map in category_maps:
        for symbols in category_map.values():
            values.extend(str(symbol) for symbol in symbols)
    return sorted(set(values))


def build_gold_equity_barbell_survivorship_audit(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    fund_meta_db_path: str = _DEFAULT_FUND_META_DB,
    etf_db_path: str = _DEFAULT_ETF_DB,
) -> Dict[str, Any]:
    import duckdb

    audit: Dict[str, Any] = {
        "kind": "etf_metadata_survivorship_audit",
        "material": False,
    }
    fund_meta_path = Path(fund_meta_db_path)
    etf_path = Path(etf_db_path)
    if not fund_meta_path.exists() or not etf_path.exists():
        audit["reason"] = "ETF or fund metadata DuckDB file unavailable; ETF survivorship audit not run."
        return audit
    start_dt = _parse_datetime(start)
    end_dt = _parse_datetime(end)
    date_filters = []
    if start_dt:
        date_filters.append(f"CAST(timestamp AS DATE) >= DATE '{start_dt.date().isoformat()}'")
    if end_dt:
        date_filters.append(f"CAST(timestamp AS DATE) <= DATE '{end_dt.date().isoformat()}'")
    date_clause = "WHERE " + " AND ".join(date_filters) if date_filters else ""
    conn = duckdb.connect(str(etf_path), read_only=True)
    try:
        meta_sql_path = str(fund_meta_path).replace("'", "''")
        conn.execute(f"ATTACH IF NOT EXISTS '{meta_sql_path}' AS meta (READ_ONLY)")
        coverage = conn.execute(
            f"""
            WITH bar_symbols AS (
                SELECT DISTINCT symbol
                FROM daily_cn_ochl
                {date_clause}
            ),
            meta_etf AS (
                SELECT symbol, delist_date
                FROM meta.cn_fund_instruments
                WHERE instrument_type = 'ETF'
            ),
            meta_fund AS (
                SELECT symbol, instrument_type, delist_date
                FROM meta.cn_fund_instruments
            )
            SELECT
                (SELECT COUNT(*) FROM bar_symbols) AS etf_bar_symbols,
                (SELECT COUNT(*) FROM meta_etf) AS fund_meta_etf_symbols,
                (SELECT COUNT(*) FROM bar_symbols WHERE symbol NOT IN (SELECT symbol FROM meta_fund)) AS bar_symbols_missing_fund_meta,
                (SELECT COUNT(*) FROM meta_etf WHERE COALESCE(CAST(delist_date AS VARCHAR), '') <> '') AS fund_meta_delisted_symbols,
                (SELECT COUNT(*) FROM bar_symbols b JOIN meta_fund m USING(symbol) WHERE m.instrument_type <> 'ETF') AS fund_bar_symbols_with_non_etf_metadata
            """
        ).fetchone()
        audit.update(
            {
                "etf_bar_symbols": int(coverage[0] or 0),
                "fund_bar_symbols": int(coverage[0] or 0),
                "fund_meta_etf_symbols": int(coverage[1] or 0),
                "bar_symbols_missing_fund_meta": int(coverage[2] or 0),
                "fund_meta_delisted_symbols": int(coverage[3] or 0),
                "fund_bar_symbols_with_non_etf_metadata": int(coverage[4] or 0),
            }
        )
        sample = conn.execute(
            f"""
            WITH bar_symbols AS (
                SELECT symbol, MIN(CAST(timestamp AS DATE)) AS first_date, MAX(CAST(timestamp AS DATE)) AS last_date, COUNT(*) AS bar_rows
                FROM daily_cn_ochl
                {date_clause}
                GROUP BY symbol
            ),
            meta_etf AS (
                SELECT symbol
                FROM meta.cn_fund_instruments
            )
            SELECT b.symbol, b.first_date, b.last_date, b.bar_rows
            FROM bar_symbols b
            LEFT JOIN meta_etf m USING(symbol)
            WHERE m.symbol IS NULL
            ORDER BY b.symbol
            LIMIT 12
            """
        ).fetchdf()
        audit["bar_symbols_missing_fund_meta_sample"] = sample.to_dict("records") if sample is not None else []
    except Exception as exc:
        audit["reason"] = f"ETF survivorship audit failed: {exc}"
        return audit
    finally:
        conn.close()
    missing_meta = int(audit.get("bar_symbols_missing_fund_meta") or 0)
    delisted_meta = int(audit.get("fund_meta_delisted_symbols") or 0)
    audit["material"] = missing_meta > 0 or delisted_meta == 0
    if audit["material"]:
        audit["reason"] = (
            f"ETF bar data has {missing_meta} symbols missing fund metadata and fund metadata has "
            f"{delisted_meta} delist markers; residual ETF survivorship bias cannot be fully ruled out."
        )
    else:
        non_etf = int(audit.get("fund_bar_symbols_with_non_etf_metadata") or 0)
        audit["reason"] = (
            "ETF/LOF bar symbols are covered by fund metadata and ETF delist markers are present."
            if non_etf == 0
            else f"ETF/LOF bar symbols are covered by fund metadata, including {non_etf} LOF/non-ETF bar symbols; ETF delist markers are present."
        )
    return audit


def _category_symbol_map(category_items: Dict[str, List[Dict[str, Any]]], max_symbols_per_category: int) -> Dict[str, List[str]]:
    result = {}
    for category, items in category_items.items():
        unique = {str(item["symbol"]): item for item in items}
        ranked = sorted(
            unique.values(),
            key=lambda item: (-float(item.get("as_of_size") or 0.0), -int(item.get("bar_rows") or 0), str(item.get("symbol"))),
        )
        if max_symbols_per_category > 0:
            ranked = ranked[:max_symbols_per_category]
        result[category] = sorted(str(item["symbol"]) for item in ranked)
    return result


def _universe_selection_policy(as_of: Optional[datetime], max_symbols_per_category: int) -> str:
    if as_of and max_symbols_per_category == 1:
        return "inception_locked_primary_per_category"
    if as_of:
        return "inception_locked_pit_category"
    return "dynamic_pit_category_wide"


def _load_etf_metadata_rows(
    start: Optional[datetime],
    end: Optional[datetime],
    fund_meta_db_path: str,
    etf_db_path: str,
    fund_nav_db_path: str,
) -> List[Dict[str, Any]]:
    return _load_fund_metadata_rows(
        start,
        end,
        fund_meta_db_path,
        etf_db_path,
        fund_nav_db_path,
        ["ETF"],
    )


def _load_fund_metadata_rows(
    start: Optional[datetime],
    end: Optional[datetime],
    fund_meta_db_path: str,
    etf_db_path: str,
    fund_nav_db_path: str,
    instrument_types: List[str],
) -> List[Dict[str, Any]]:
    import duckdb

    fund_meta_path = Path(fund_meta_db_path)
    etf_path = Path(etf_db_path)
    nav_path = Path(fund_nav_db_path)
    if not fund_meta_path.exists() or not etf_path.exists() or not nav_path.exists():
        return []
    conn = duckdb.connect(str(fund_meta_path), read_only=True)
    try:
        etf_sql_path = str(etf_path).replace("'", "''")
        nav_sql_path = str(nav_path).replace("'", "''")
        conn.execute(f"ATTACH IF NOT EXISTS '{etf_sql_path}' AS etf (READ_ONLY)")
        conn.execute(f"ATTACH IF NOT EXISTS '{nav_sql_path}' AS nav (READ_ONLY)")
        size_available = _attached_table_exists(conn, "nav", _FUND_ETF_SHARE_SIZE_TABLE)
        benchmark_available = _main_table_exists(conn, _FUND_ETF_BENCHMARK_TABLE)
        meta_columns = _table_columns(conn, "cn_fund_instruments")
        filters = []
        params: List[Any] = []
        normalized_types = [str(item) for item in instrument_types if str(item)]
        if normalized_types:
            placeholders = ", ".join("?" for _ in normalized_types)
            filters.append(f"{_select_expr(meta_columns, 'instrument_type')} IN ({placeholders})")
            params.extend(normalized_types)
        if start is not None:
            filters.append("CAST(b.timestamp AS DATE) >= CAST(? AS DATE)")
            params.append(start)
        if end is not None:
            filters.append("CAST(b.timestamp AS DATE) <= CAST(? AS DATE)")
            params.append(end)
        if not filters:
            filters.append("1 = 1")
        select_metadata = ",\n                ".join(
            f"{_select_expr(meta_columns, field)} AS {field}"
            for field in _CLASSIFICATION_METADATA_FIELDS
        )
        select_benchmark = ",\n                ".join(_benchmark_select_expr(field, benchmark_available) for field in _BENCHMARK_METADATA_FIELDS)
        benchmark_join = ""
        benchmark_group = ""
        if benchmark_available:
            benchmark_join = f"""
            LEFT JOIN {_FUND_ETF_BENCHMARK_TABLE} bm
              ON UPPER(CAST({_select_expr(meta_columns, 'index_code')} AS VARCHAR)) = UPPER(CAST(bm.ts_code AS VARCHAR))
            """
            benchmark_group = ", bm.bmk_level, bm.bmk_type, bm.bmk_src, bm.idx_type, bm.name, bm.fullname"
        size_join = ""
        size_expr = "COALESCE(n.total_netasset, n.net_asset)"
        if size_available:
            size_join = f"""
            LEFT JOIN nav.{_FUND_ETF_SHARE_SIZE_TABLE} s
              ON m.symbol = s.symbol
             AND CAST(b.timestamp AS DATE) = s.trade_date
            """
            size_expr = "COALESCE(s.total_size, n.total_netasset, n.net_asset)"
        query = f"""
            SELECT
                m.symbol,
                {select_metadata},
                {select_benchmark},
                COUNT(DISTINCT CAST(b.timestamp AS DATE)) AS bar_rows,
                SUM(CASE WHEN {size_expr} > 0 THEN 1 ELSE 0 END) AS size_rows,
                MAX({size_expr}) AS as_of_size
            FROM cn_fund_instruments m
            JOIN etf.daily_cn_ochl b
              ON m.symbol = b.symbol
            LEFT JOIN nav.cn_fund_nav n
              ON m.symbol = n.symbol
             AND CAST(b.timestamp AS DATE) = n.nav_date
            {benchmark_join}
            {size_join}
            WHERE {" AND ".join(filters)}
            GROUP BY m.symbol, {", ".join(_select_expr(meta_columns, field) for field in _CLASSIFICATION_METADATA_FIELDS)}{benchmark_group}
            ORDER BY {_select_expr(meta_columns, 'list_date')}, m.symbol
        """
        frame = conn.execute(query, params).fetchdf()
        return frame.to_dict("records") if frame is not None and not frame.empty else []
    finally:
        conn.close()


def _barbell_category_from_row(row: Dict[str, Any]) -> Optional[str]:
    classification = classify_cn_fund(row)
    if classification.classification_excluded:
        return None
    if classification.category_group in _BARBELL_CATEGORY_GROUPS:
        return classification.category_group
    return None


def _table_columns(conn: Any, table_name: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()}
    except Exception:
        return set()


def _main_table_exists(conn: Any, table_name: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {table_name} LIMIT 0")
    except Exception:
        return False
    return True


def _select_expr(columns: set[str], field: str) -> str:
    if field in columns:
        return f"m.{field}"
    return "''"


def _benchmark_select_expr(field: str, benchmark_available: bool) -> str:
    if not benchmark_available:
        return f"'' AS {field}"
    mapping = {
        "bmk_level": "bm.bmk_level",
        "bmk_type": "bm.bmk_type",
        "bmk_src": "bm.bmk_src",
        "idx_type": "bm.idx_type",
        "bmk_name": "bm.name",
        "bmk_fullname": "bm.fullname",
    }
    return f"{mapping[field]} AS {field}"


def _attached_table_exists(conn: Any, schema: str, table_name: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {schema}.{table_name} LIMIT 0")
    except Exception:
        return False
    return True


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value)
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text[:8], fmt)
        except ValueError:
            continue
    return None
