from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from quant.infrastructure.data.storage_duckdb import (
    _DEFAULT_ETF_DB,
    _DEFAULT_FUND_META_DB,
    _DEFAULT_FUND_NAV_DB,
)


_RISK_CATEGORY_ORDER = ("sse50", "csi300", "chinext", "chinext50", "dividend")
_DEFENSIVE_CATEGORY_ORDER = ("gold",)


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
        category = classify_gold_equity_barbell_category(row.get("name"), row.get("index_name"))
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
    }


def classify_gold_equity_barbell_category(name: Any, index_name: Any) -> Optional[str]:
    text = f"{name or ''} {index_name or ''}"
    if _contains_any(text, ("黄金9999", "黄金ETF", "金ETF")) and not _contains_any(text, ("黄金股", "金矿", "有色")):
        return "gold"
    if _contains_any(text, ("创业板50",)):
        return "chinext50"
    if _contains_any(text, ("创业板指数", "创业板ETF")) and not _contains_any(text, ("创业板50", "增强")):
        return "chinext"
    if _contains_any(text, ("上证50",)) and not _contains_any(text, ("50AH", "AH", "增强", "红利")):
        return "sse50"
    if _contains_any(text, ("沪深300",)) and not _contains_any(text, ("红利", "低波", "增强", "价值", "成长", "行业", "策略")):
        return "csi300"
    if _contains_any(text, ("红利",)) and not _contains_any(text, ("港", "国企", "消费", "央企", "增强")):
        return "dividend"
    return None


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
            )
            SELECT
                (SELECT COUNT(*) FROM bar_symbols) AS etf_bar_symbols,
                (SELECT COUNT(*) FROM meta_etf) AS fund_meta_etf_symbols,
                (SELECT COUNT(*) FROM bar_symbols WHERE symbol NOT IN (SELECT symbol FROM meta_etf)) AS bar_symbols_missing_fund_meta,
                (SELECT COUNT(*) FROM meta_etf WHERE COALESCE(CAST(delist_date AS VARCHAR), '') <> '') AS fund_meta_delisted_symbols
            """
        ).fetchone()
        audit.update(
            {
                "etf_bar_symbols": int(coverage[0] or 0),
                "fund_meta_etf_symbols": int(coverage[1] or 0),
                "bar_symbols_missing_fund_meta": int(coverage[2] or 0),
                "fund_meta_delisted_symbols": int(coverage[3] or 0),
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
                WHERE instrument_type = 'ETF'
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
        audit["reason"] = "ETF bar symbols are covered by fund metadata and delist markers are present."
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
        filters = ["m.instrument_type = 'ETF'"]
        params: List[Any] = []
        if start is not None:
            filters.append("CAST(b.timestamp AS DATE) >= CAST(? AS DATE)")
            params.append(start)
        if end is not None:
            filters.append("CAST(b.timestamp AS DATE) <= CAST(? AS DATE)")
            params.append(end)
        query = f"""
            SELECT
                m.symbol,
                m.name,
                m.index_name,
                m.list_date,
                m.delist_date,
                COUNT(DISTINCT CAST(b.timestamp AS DATE)) AS bar_rows,
                SUM(CASE WHEN COALESCE(n.total_netasset, n.net_asset) > 0 THEN 1 ELSE 0 END) AS size_rows,
                MAX(COALESCE(n.total_netasset, n.net_asset)) AS as_of_size
            FROM cn_fund_instruments m
            JOIN etf.daily_cn_ochl b
              ON m.symbol = b.symbol
            LEFT JOIN nav.cn_fund_nav n
              ON m.symbol = n.symbol
             AND CAST(b.timestamp AS DATE) = n.nav_date
            WHERE {" AND ".join(filters)}
            GROUP BY m.symbol, m.name, m.index_name, m.list_date, m.delist_date
            ORDER BY m.list_date, m.symbol
        """
        frame = conn.execute(query, params).fetchdf()
        return frame.to_dict("records") if frame is not None and not frame.empty else []
    finally:
        conn.close()


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
