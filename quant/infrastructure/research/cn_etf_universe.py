from __future__ import annotations

from datetime import datetime
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
) -> Dict[str, Any]:
    rows = _load_etf_metadata_rows(start, end, fund_meta_db_path, etf_db_path, fund_nav_db_path)
    risk = {category: [] for category in _RISK_CATEGORY_ORDER}
    defensive = {category: [] for category in _DEFENSIVE_CATEGORY_ORDER}
    audit_rows = []
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
            "category": category,
        }
        if item["bar_rows"] <= 0 or item["size_rows"] <= 0:
            continue
        if category in risk:
            risk[category].append(item["symbol"])
        elif category in defensive:
            defensive[category].append(item["symbol"])
        audit_rows.append(item)
    return {
        "risk_category_symbols": {key: sorted(set(values)) for key, values in risk.items()},
        "defensive_category_symbols": {key: sorted(set(values)) for key, values in defensive.items()},
        "symbols": flatten_category_symbols(risk, defensive),
        "audit": audit_rows,
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
        conn.execute(f"ATTACH IF NOT EXISTS '{str(etf_path).replace("'", "''")}' AS etf (READ_ONLY)")
        conn.execute(f"ATTACH IF NOT EXISTS '{str(nav_path).replace("'", "''")}' AS nav (READ_ONLY)")
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
                SUM(CASE WHEN COALESCE(n.total_netasset, n.net_asset) > 0 THEN 1 ELSE 0 END) AS size_rows
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
