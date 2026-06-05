from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from quant.infrastructure.data.fund_classification import classify_cn_fund
from quant.infrastructure.data.storage_duckdb import (
    _DEFAULT_ETF_DB,
    _DEFAULT_FUND_META_DB,
    _DEFAULT_FUND_NAV_DB,
)


REGISTERED_ETF_UNIVERSE_VERSION = "audited_stable_etf_registry_v1"
_RISK_CATEGORY_ORDER = ("sse50", "csi300", "chinext", "chinext50", "dividend")
_DEFENSIVE_CATEGORY_ORDER = ("gold",)
_BARBELL_CATEGORY_GROUPS = set(_RISK_CATEGORY_ORDER + _DEFENSIVE_CATEGORY_ORDER)
_BROAD_ASSET_CATEGORY_ORDER = ("sse50", "csi300", "csi1000", "chinext", "chinext50", "dividend", "gold", "cash", "bond_rate")


@dataclass(frozen=True)
class RegisteredETF:
    symbol: str
    name: str
    category_group: str
    fund_category: str
    role: str
    list_date: str
    index_code: str
    index_name: str
    priority: int
    audit_status: str = "user_approved"
    audit_note: str = "Stable representative ETF category approved by user audit."

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


_REGISTERED_ETFS: tuple[RegisteredETF, ...] = (
    RegisteredETF("510050", "上证50ETF华夏", "sse50", "equity_cn_broad_sse50", "risk", "20050223", "000016.SH", "上证50指数", 10),
    RegisteredETF("510300", "沪深300ETF华泰柏瑞", "csi300", "equity_cn_broad_csi300", "risk", "20120528", "000300.SH", "沪深300指数", 10),
    RegisteredETF("159915", "创业板ETF易方达", "chinext", "equity_cn_broad_chinext", "risk", "20111209", "399006.SZ", "创业板指数", 10),
    RegisteredETF("159949", "创业板50ETF华安", "chinext50", "equity_cn_broad_chinext50", "risk", "20160722", "399673.SZ", "创业板50指数", 10),
    RegisteredETF("510880", "红利ETF华泰柏瑞", "dividend", "equity_cn_strategy_dividend", "risk", "20070118", "000015.SH", "上证红利指数", 10),
    RegisteredETF("518880", "黄金ETF华安", "gold", "commodity_gold", "defensive", "20130729", "Au99.99.SGE", "黄金9999", 10),
    RegisteredETF("510500", "中证500ETF南方", "csi500", "equity_cn_broad_csi500", "registered_only", "20130315", "000905.SH", "中证小盘500指数", 20),
    RegisteredETF("512100", "中证1000ETF南方", "csi1000", "equity_cn_broad_csi1000", "registered_only", "20161104", "000852.SH", "中证1000指数", 20),
    RegisteredETF("511990", "华宝添益ETF", "cash", "cash_money_market", "registered_only", "20130128", "", "货币ETF", 20),
    RegisteredETF("511010", "国债ETF国泰", "bond_rate", "bond_rate", "registered_only", "20130325", "000140.CSI", "上证5年期国债指数", 20),
)


def registered_etf_categories() -> Dict[str, List[Dict[str, Any]]]:
    categories: Dict[str, List[Dict[str, Any]]] = {}
    for entry in _REGISTERED_ETFS:
        categories.setdefault(entry.category_group, []).append(entry.as_dict())
    return categories


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
    entries = [
        entry
        for entry in _REGISTERED_ETFS
        if entry.category_group in _BARBELL_CATEGORY_GROUPS and entry.role in {"risk", "defensive"}
    ]
    rows = _load_registered_etf_rows(
        entries,
        start=start,
        end=end,
        fund_meta_db_path=fund_meta_db_path,
        etf_db_path=etf_db_path,
        fund_nav_db_path=fund_nav_db_path,
        universe_as_of=universe_as_of,
        min_history_days_as_of=min_history_days_as_of,
        universe_start=universe_start,
        universe_end=universe_end,
    )
    risk = {category: [] for category in _RISK_CATEGORY_ORDER}
    defensive = {category: [] for category in _DEFENSIVE_CATEGORY_ORDER}
    for row in rows:
        category = str(row.get("category_group") or "")
        if category in risk:
            risk[category].append(row)
        elif category in defensive:
            defensive[category].append(row)
    category_cap = max(0, int(max_symbols_per_category or 0))
    risk_symbols = _category_symbol_map(risk, category_cap)
    defensive_symbols = _category_symbol_map(defensive, category_cap)
    as_of = _parse_datetime(universe_as_of)
    window_start = _parse_datetime(universe_start) or start
    window_end = _parse_datetime(universe_end) or end
    return {
        "risk_category_symbols": risk_symbols,
        "defensive_category_symbols": defensive_symbols,
        "symbols": flatten_category_symbols(risk_symbols, defensive_symbols),
        "audit": rows,
        "universe_as_of": as_of.date().isoformat() if as_of else "",
        "universe_start": window_start.date().isoformat() if window_start else "",
        "universe_end": window_end.date().isoformat() if window_end else "",
        "universe_min_history_days_as_of": max(0, int(min_history_days_as_of or 0)),
        "universe_max_symbols_per_category": category_cap,
        "universe_selection_policy": "audited_stable_etf_registry",
        "universe_registry_version": REGISTERED_ETF_UNIVERSE_VERSION,
        "registered_universe_counts": _registered_universe_counts(entries, rows),
    }


def build_broad_asset_etf_pit_universe(
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
    entries = [
        entry
        for entry in _REGISTERED_ETFS
        if entry.category_group in _BROAD_ASSET_CATEGORY_ORDER
    ]
    rows = _load_registered_etf_rows(
        entries,
        start=start,
        end=end,
        fund_meta_db_path=fund_meta_db_path,
        etf_db_path=etf_db_path,
        fund_nav_db_path=fund_nav_db_path,
        universe_as_of=universe_as_of,
        min_history_days_as_of=min_history_days_as_of,
        universe_start=universe_start,
        universe_end=universe_end,
    )
    buckets = {category: [] for category in _BROAD_ASSET_CATEGORY_ORDER}
    for row in rows:
        category = str(row.get("category_group") or "")
        if category in buckets:
            buckets[category].append(row)
    category_cap = max(0, int(max_symbols_per_category or 0))
    category_symbols = _category_symbol_map(buckets, category_cap)
    as_of = _parse_datetime(universe_as_of)
    window_start = _parse_datetime(universe_start) or start
    window_end = _parse_datetime(universe_end) or end
    return {
        "category_symbols": category_symbols,
        "symbols": flatten_category_symbols(category_symbols),
        "audit": rows,
        "universe_as_of": as_of.date().isoformat() if as_of else "",
        "universe_start": window_start.date().isoformat() if window_start else "",
        "universe_end": window_end.date().isoformat() if window_end else "",
        "universe_min_history_days_as_of": max(0, int(min_history_days_as_of or 0)),
        "universe_max_symbols_per_category": category_cap,
        "universe_selection_policy": "audited_stable_etf_registry",
        "universe_registry_version": REGISTERED_ETF_UNIVERSE_VERSION,
        "registered_universe_counts": _registered_universe_counts(entries, rows),
    }


def classify_gold_equity_barbell_category(name: Any, index_name: Any) -> Optional[str]:
    row = {"name": name or "", "index_name": index_name or ""}
    classification = classify_cn_fund(row).as_dict()
    if classification.get("classification_excluded"):
        return None
    category_group = str(classification.get("category_group") or "")
    if category_group in _BARBELL_CATEGORY_GROUPS:
        return category_group
    return None


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
    field = "category_group" if category_field == "category_group" else "fund_category"
    entries = [entry for entry in _REGISTERED_ETFS if getattr(entry, field) in requested]
    rows = _load_registered_etf_rows(
        entries,
        start=start,
        end=end,
        fund_meta_db_path=fund_meta_db_path,
        etf_db_path=etf_db_path,
        fund_nav_db_path=fund_nav_db_path,
        universe_as_of=universe_as_of,
        min_history_days_as_of=min_history_days_as_of,
        universe_start=universe_start,
        universe_end=universe_end,
    )
    buckets = {category: [] for category in requested}
    for row in rows:
        key = str(row.get(field) or "")
        if key in buckets:
            buckets[key].append(row)
    category_cap = max(0, int(max_symbols_per_category or 0))
    category_symbols = _category_symbol_map(buckets, category_cap)
    as_of = _parse_datetime(universe_as_of)
    window_start = _parse_datetime(universe_start) or start
    window_end = _parse_datetime(universe_end) or end
    return {
        "category_symbols": category_symbols,
        "symbols": flatten_category_symbols(category_symbols),
        "audit": rows,
        "categories": requested,
        "category_field": category_field,
        "instrument_types": instrument_types or ["ETF"],
        "universe_registry_version": REGISTERED_ETF_UNIVERSE_VERSION,
        "universe_as_of": as_of.date().isoformat() if as_of else "",
        "universe_start": window_start.date().isoformat() if window_start else "",
        "universe_end": window_end.date().isoformat() if window_end else "",
        "universe_min_history_days_as_of": max(0, int(min_history_days_as_of or 0)),
        "universe_max_symbols_per_category": category_cap,
        "universe_selection_policy": "audited_stable_etf_registry",
        "registered_universe_counts": _registered_universe_counts(entries, rows),
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
        "universe_registry_version": REGISTERED_ETF_UNIVERSE_VERSION,
    }
    fund_meta_path = Path(fund_meta_db_path)
    etf_path = Path(etf_db_path)
    if not etf_path.exists():
        audit["reason"] = "ETF DuckDB file unavailable; ETF survivorship audit not run."
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
        meta_available = fund_meta_path.exists()
        if meta_available:
            meta_sql_path = str(fund_meta_path).replace("'", "''")
            conn.execute(f"ATTACH IF NOT EXISTS '{meta_sql_path}' AS meta (READ_ONLY)")
            meta_available = _attached_table_exists(conn, "meta", "cn_fund_instruments")
        coverage = _metadata_coverage(conn, date_clause, meta_available)
        audit.update(coverage)
        audit["bar_symbols_missing_fund_meta_sample"] = _missing_meta_sample(conn, date_clause, meta_available)
        audit.update(_registered_bar_coverage(conn, date_clause))
    except Exception as exc:
        audit["reason"] = f"ETF survivorship audit failed: {exc}"
        return audit
    finally:
        conn.close()
    missing_registered = int(audit.get("registered_universe_missing_bar_count") or 0)
    audit["material"] = missing_registered > 0
    if missing_registered:
        audit["reason"] = (
            f"Audited ETF registry has {missing_registered} symbols without bars in the requested window; "
            "strict strategy universe is incomplete."
        )
    else:
        broad_missing = int(audit.get("bar_symbols_missing_fund_meta") or 0)
        delisted_meta = int(audit.get("fund_meta_delisted_symbols") or 0)
        audit["reason"] = (
            "Audited stable ETF registry symbols have bar coverage. Broad ETF metadata gaps are reported for context "
            f"only and do not auto-expand strategy candidates: missing_meta={broad_missing}, delist_markers={delisted_meta}."
        )
    return audit


def _load_registered_etf_rows(
    entries: List[RegisteredETF],
    start: Optional[datetime],
    end: Optional[datetime],
    fund_meta_db_path: str,
    etf_db_path: str,
    fund_nav_db_path: str,
    universe_as_of: Optional[Any],
    min_history_days_as_of: int,
    universe_start: Optional[Any],
    universe_end: Optional[Any],
) -> List[Dict[str, Any]]:
    import duckdb

    as_of = _parse_datetime(universe_as_of)
    window_start = _parse_datetime(universe_start) or start
    window_end = _parse_datetime(universe_end) or end
    query_start = None if as_of else window_start
    query_end = as_of or window_end
    eligible_entries = _eligible_registered_entries(entries, query_end)
    if not eligible_entries:
        return []
    etf_path = Path(etf_db_path)
    nav_path = Path(fund_nav_db_path)
    fund_meta_path = Path(fund_meta_db_path)
    if not etf_path.exists() or not nav_path.exists():
        return []
    conn = duckdb.connect(str(etf_path), read_only=True)
    try:
        nav_sql_path = str(nav_path).replace("'", "''")
        conn.execute(f"ATTACH IF NOT EXISTS '{nav_sql_path}' AS nav (READ_ONLY)")
        nav_available = _attached_table_exists(conn, "nav", "cn_fund_nav")
        meta_available = False
        if fund_meta_path.exists():
            meta_sql_path = str(fund_meta_path).replace("'", "''")
            conn.execute(f"ATTACH IF NOT EXISTS '{meta_sql_path}' AS meta (READ_ONLY)")
            meta_available = _attached_table_exists(conn, "meta", "cn_fund_instruments")
        values_sql = _registered_values_sql(eligible_entries)
        date_filters = []
        if query_start is not None:
            date_filters.append(f"CAST(b.timestamp AS DATE) >= DATE '{query_start.date().isoformat()}'")
        if query_end is not None:
            date_filters.append(f"CAST(b.timestamp AS DATE) <= DATE '{query_end.date().isoformat()}'")
        date_join = " AND " + " AND ".join(date_filters) if date_filters else ""
        nav_join = ""
        size_expr = "0.0"
        size_count_expr = "0"
        if nav_available:
            nav_join = """
            LEFT JOIN nav.cn_fund_nav n
              ON r.symbol = n.symbol
             AND CAST(b.timestamp AS DATE) = n.nav_date
            """
            size_expr = "COALESCE(n.total_netasset, n.net_asset, 0.0)"
            size_count_expr = f"CASE WHEN {size_expr} > 0 THEN 1 ELSE 0 END"
        meta_name = "r.name"
        meta_index_name = "r.index_name"
        meta_list_date = "r.list_date"
        meta_delist_date = "''"
        meta_join = ""
        if meta_available:
            meta_join = """
            LEFT JOIN meta.cn_fund_instruments m
              ON r.symbol = m.symbol
            """
            meta_name = "COALESCE(NULLIF(m.name, ''), r.name)"
            meta_index_name = "COALESCE(NULLIF(m.index_name, ''), r.index_name)"
            meta_list_date = "COALESCE(NULLIF(CAST(m.list_date AS VARCHAR), ''), r.list_date)"
            meta_delist_date = "COALESCE(CAST(m.delist_date AS VARCHAR), '')"
        frame = conn.execute(
            f"""
            WITH registered(symbol, name, category_group, fund_category, role, list_date, index_code, index_name, priority, audit_status, audit_note) AS (
                VALUES {values_sql}
            )
            SELECT
                r.symbol,
                {meta_name} AS name,
                {meta_index_name} AS index_name,
                {meta_list_date} AS list_date,
                {meta_delist_date} AS delist_date,
                r.index_code,
                r.category_group,
                r.fund_category,
                r.role,
                r.priority,
                r.audit_status,
                r.audit_note,
                COUNT(DISTINCT CAST(b.timestamp AS DATE)) AS bar_rows,
                SUM({size_count_expr}) AS size_rows,
                MAX({size_expr}) AS as_of_size,
                MIN(CAST(b.timestamp AS DATE)) AS first_bar_date,
                MAX(CAST(b.timestamp AS DATE)) AS last_bar_date
            FROM registered r
            LEFT JOIN daily_cn_ochl b
              ON r.symbol = b.symbol
             {date_join}
            {nav_join}
            {meta_join}
            GROUP BY
                r.symbol, r.name, r.category_group, r.fund_category, r.role, r.list_date,
                r.index_code, r.index_name, r.priority, r.audit_status, r.audit_note
                {', m.name, m.index_name, m.list_date, m.delist_date' if meta_available else ''}
            ORDER BY r.priority, r.symbol
            """
        ).fetchdf()
    finally:
        conn.close()
    rows: List[Dict[str, Any]] = []
    min_history = max(0, int(min_history_days_as_of or 0))
    for record in frame.to_dict("records") if frame is not None and not frame.empty else []:
        item = {
            **record,
            "symbol": str(record.get("symbol") or ""),
            "category": str(record.get("category_group") or ""),
            "bar_rows": int(record.get("bar_rows") or 0),
            "size_rows": int(record.get("size_rows") or 0),
            "as_of_size": float(record.get("as_of_size") or 0.0),
            "classification_version": REGISTERED_ETF_UNIVERSE_VERSION,
            "asset_class": _asset_class_from_category(str(record.get("category_group") or "")),
            "market_region": "cn",
            "fund_strategy": _fund_strategy_from_category(str(record.get("category_group") or "")),
            "classification_source": "audited_stable_etf_registry",
            "classification_confidence": 1.0,
            "classification_reason": str(record.get("audit_note") or ""),
            "classification_excluded": False,
        }
        if item["bar_rows"] <= 0 or item["size_rows"] <= 0:
            continue
        if min_history and item["bar_rows"] < min_history:
            continue
        rows.append(item)
    return rows


def _registered_values_sql(entries: List[RegisteredETF]) -> str:
    return ", ".join(
        "("
        + ", ".join(
            _sql_literal(value)
            for value in (
                entry.symbol,
                entry.name,
                entry.category_group,
                entry.fund_category,
                entry.role,
                entry.list_date,
                entry.index_code,
                entry.index_name,
                entry.priority,
                entry.audit_status,
                entry.audit_note,
            )
        )
        + ")"
        for entry in entries
    )


def _eligible_registered_entries(entries: List[RegisteredETF], query_end: Optional[datetime]) -> List[RegisteredETF]:
    if query_end is None:
        return entries
    cutoff = query_end.date()
    return [entry for entry in entries if (_parse_datetime(entry.list_date) or datetime.min).date() <= cutoff]


def _category_symbol_map(category_items: Dict[str, List[Dict[str, Any]]], max_symbols_per_category: int) -> Dict[str, List[str]]:
    result = {}
    for category, items in category_items.items():
        unique = {str(item["symbol"]): item for item in items}
        ranked = sorted(
            unique.values(),
            key=lambda item: (int(item.get("priority") or 9999), str(item.get("symbol"))),
        )
        if max_symbols_per_category > 0:
            ranked = ranked[:max_symbols_per_category]
        result[category] = [str(item["symbol"]) for item in ranked]
    return result


def _registered_universe_counts(entries: List[RegisteredETF], rows: List[Dict[str, Any]]) -> Dict[str, int]:
    active = {str(row.get("symbol")) for row in rows}
    registered = {entry.symbol for entry in entries}
    return {
        "registered_symbol_count": len(registered),
        "active_symbol_count": len(active),
        "missing_data_count": len(registered - active),
        "user_approved_count": sum(1 for entry in entries if entry.audit_status == "user_approved"),
    }


def _asset_class_from_category(category_group: str) -> str:
    if category_group == "gold":
        return "commodity"
    if category_group in {"cash"}:
        return "cash"
    if category_group in {"bond", "bond_rate"}:
        return "bond"
    return "equity"


def _fund_strategy_from_category(category_group: str) -> str:
    if category_group == "gold":
        return "physical"
    if category_group == "cash":
        return "money_market"
    if category_group in {"bond", "bond_rate"}:
        return "fixed_income"
    if category_group == "dividend":
        return "strategy"
    return "broad"


def _metadata_coverage(conn: Any, date_clause: str, meta_available: bool) -> Dict[str, int]:
    if not meta_available:
        row = conn.execute(
            f"""
            WITH bar_symbols AS (
                SELECT DISTINCT symbol
                FROM daily_cn_ochl
                {date_clause}
            )
            SELECT COUNT(*) FROM bar_symbols
            """
        ).fetchone()
        count = int(row[0] or 0)
        return {
            "etf_bar_symbols": count,
            "fund_bar_symbols": count,
            "fund_meta_etf_symbols": 0,
            "bar_symbols_missing_fund_meta": count,
            "fund_meta_delisted_symbols": 0,
            "fund_bar_symbols_with_non_etf_metadata": 0,
        }
    row = conn.execute(
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
    return {
        "etf_bar_symbols": int(row[0] or 0),
        "fund_bar_symbols": int(row[0] or 0),
        "fund_meta_etf_symbols": int(row[1] or 0),
        "bar_symbols_missing_fund_meta": int(row[2] or 0),
        "fund_meta_delisted_symbols": int(row[3] or 0),
        "fund_bar_symbols_with_non_etf_metadata": int(row[4] or 0),
    }


def _missing_meta_sample(conn: Any, date_clause: str, meta_available: bool) -> List[Dict[str, Any]]:
    if not meta_available:
        sample = conn.execute(
            f"""
            SELECT symbol, MIN(CAST(timestamp AS DATE)) AS first_date, MAX(CAST(timestamp AS DATE)) AS last_date, COUNT(*) AS bar_rows
            FROM daily_cn_ochl
            {date_clause}
            GROUP BY symbol
            ORDER BY symbol
            LIMIT 12
            """
        ).fetchdf()
        return sample.to_dict("records") if sample is not None else []
    sample = conn.execute(
        f"""
        WITH bar_symbols AS (
            SELECT symbol, MIN(CAST(timestamp AS DATE)) AS first_date, MAX(CAST(timestamp AS DATE)) AS last_date, COUNT(*) AS bar_rows
            FROM daily_cn_ochl
            {date_clause}
            GROUP BY symbol
        ),
        meta_fund AS (
            SELECT symbol
            FROM meta.cn_fund_instruments
        )
        SELECT b.symbol, b.first_date, b.last_date, b.bar_rows
        FROM bar_symbols b
        LEFT JOIN meta_fund m USING(symbol)
        WHERE m.symbol IS NULL
        ORDER BY b.symbol
        LIMIT 12
        """
    ).fetchdf()
    return sample.to_dict("records") if sample is not None else []


def _registered_bar_coverage(conn: Any, date_clause: str) -> Dict[str, Any]:
    entries = [
        entry
        for entry in _REGISTERED_ETFS
        if entry.category_group in _BARBELL_CATEGORY_GROUPS and entry.role in {"risk", "defensive"}
    ]
    values_sql = _registered_values_sql(entries)
    rows = conn.execute(
        f"""
        WITH registered(symbol, name, category_group, fund_category, role, list_date, index_code, index_name, priority, audit_status, audit_note) AS (
            VALUES {values_sql}
        ),
        bar_symbols AS (
            SELECT DISTINCT symbol
            FROM daily_cn_ochl
            {date_clause}
        )
        SELECT r.symbol, r.category_group, CASE WHEN b.symbol IS NULL THEN FALSE ELSE TRUE END AS has_bar
        FROM registered r
        LEFT JOIN bar_symbols b USING(symbol)
        ORDER BY r.priority, r.symbol
        """
    ).fetchall()
    missing = [str(row[0]) for row in rows if not bool(row[2])]
    return {
        "registered_universe_symbol_count": len(rows),
        "registered_universe_symbols_with_bars": len(rows) - len(missing),
        "registered_universe_missing_bar_count": len(missing),
        "registered_universe_missing_bar_symbols": missing,
    }


def _attached_table_exists(conn: Any, schema: str, table_name: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {schema}.{table_name} LIMIT 0")
    except Exception:
        return False
    return True


def _sql_literal(value: Any) -> str:
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value or "").replace("'", "''") + "'"


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
