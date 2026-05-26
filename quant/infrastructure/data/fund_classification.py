"""Deterministic CN fund classification for point-in-time universe building."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


CLASSIFICATION_VERSION = "cn_fund_taxonomy_v1"


@dataclass(frozen=True)
class FundClassification:
    classification_version: str
    asset_class: str
    market_region: str
    fund_strategy: str
    fund_category: str
    category_group: str
    classification_source: str
    classification_confidence: float
    classification_reason: str
    classification_excluded: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_INDEX_CATEGORY_MAP: dict[str, tuple[str, str, str, str, str, str, float]] = {
    "000016.SH": ("equity", "cn", "broad", "equity_cn_broad_sse50", "sse50", "上证50 index_code", 1.0),
    "000300.SH": ("equity", "cn", "broad", "equity_cn_broad_csi300", "csi300", "沪深300 index_code", 1.0),
    "399300.SZ": ("equity", "cn", "broad", "equity_cn_broad_csi300", "csi300", "沪深300 index_code", 1.0),
    "000905.SH": ("equity", "cn", "broad", "equity_cn_broad_csi500", "csi500", "中证500 index_code", 1.0),
    "000852.SH": ("equity", "cn", "broad", "equity_cn_broad_csi1000", "csi1000", "中证1000 index_code", 1.0),
    "000510.SH": ("equity", "cn", "broad", "equity_cn_broad_csia500", "csia500", "中证A500 index_code", 1.0),
    "399006.SZ": ("equity", "cn", "broad", "equity_cn_broad_chinext", "chinext", "创业板 index_code", 1.0),
    "399673.SZ": ("equity", "cn", "broad", "equity_cn_broad_chinext50", "chinext50", "创业板50 index_code", 1.0),
    "000015.SH": ("equity", "cn", "strategy", "equity_cn_strategy_dividend", "dividend", "红利 index_code", 1.0),
    "399324.SZ": ("equity", "cn", "strategy", "equity_cn_strategy_dividend", "dividend", "红利 index_code", 1.0),
    "000922.CSI": ("equity", "cn", "strategy", "equity_cn_strategy_dividend", "dividend", "红利 index_code", 1.0),
    "000821.CSI": ("equity", "cn", "strategy", "equity_cn_strategy_dividend", "dividend", "红利 index_code", 1.0),
    "H30269.CSI": (
        "equity",
        "cn",
        "strategy",
        "equity_cn_strategy_dividend_low_volatility",
        "dividend",
        "红利低波 index_code",
        1.0,
    ),
    "930740.CSI": (
        "equity",
        "cn",
        "strategy",
        "equity_cn_strategy_dividend_low_volatility",
        "dividend",
        "沪深300红利低波 index_code",
        1.0,
    ),
}

_GOLD_TOKENS = ("黄金9999", "黄金ETF", "金ETF", "上海金", "黄金现货")
_GOLD_EQUITY_TOKENS = ("黄金股", "金矿", "有色")
_MONEY_TOKENS = ("货币", "现金", "保证金", "添利", "货基")
_BOND_TOKENS = ("债", "国债", "政金", "信用", "短融", "固收", "固定收益")
_CONVERTIBLE_TOKENS = ("可转债", "转债")
_HK_TOKENS = ("港", "恒生", "H股", "港股", "香港")
_US_TOKENS = ("纳指", "纳斯达克", "标普", "美国", "道琼斯")
_GLOBAL_TOKENS = ("QDII", "全球", "海外", "德国", "法国", "日本", "日经")
_FEEDER_TOKENS = ("联接", "连接")
_ENHANCED_TOKENS = ("增强",)
_LOW_VOL_TOKENS = ("低波", "低波动")
_DIVIDEND_EXCLUDE_TOKENS = ("港", "国企", "消费", "央企", "增强")
_SECTOR_THEME_TOKENS = (
    "行业",
    "主题",
    "消费",
    "医药",
    "科技",
    "芯片",
    "半导体",
    "新能源",
    "军工",
    "银行",
    "证券",
    "金融",
    "地产",
    "酒",
    "传媒",
    "煤炭",
    "钢铁",
    "有色",
    "红利消费",
)


def classify_cn_fund(row: Mapping[str, Any]) -> FundClassification:
    fields = _fields(row)
    text = _joined_text(fields)
    index_code = _norm(fields.get("index_code")).upper()
    excluded = _has_any(text, _FEEDER_TOKENS)
    enhanced = _has_any(text, _ENHANCED_TOKENS)
    region = _region(text, fields)
    if index_code in _INDEX_CATEGORY_MAP and not _has_any(text, _ENHANCED_TOKENS + _FEEDER_TOKENS):
        asset_class, region, strategy, category, group, reason, confidence = _INDEX_CATEGORY_MAP[index_code]
        return _classification(asset_class, region, strategy, category, group, reason, confidence)

    if _has_any(text, _GOLD_TOKENS) and not _has_any(text, _GOLD_EQUITY_TOKENS):
        return _classification(
            "commodity",
            "cn",
            "physical",
            "commodity_gold",
            "gold",
            "gold metadata tokens",
            0.95,
            excluded,
        )
    if _has_any(text, _MONEY_TOKENS):
        return _classification("cash", "cn", "money_market", "cash_money_market", "cash", "money-market tokens", 0.9, excluded)
    if _has_any(text, _CONVERTIBLE_TOKENS):
        return _classification("bond", "cn", "convertible", "bond_convertible", "convertible_bond", "convertible bond tokens", 0.9, excluded)
    if _has_any(text, _BOND_TOKENS):
        category = "bond_rate" if _has_any(text, ("国债", "政金", "利率")) else "bond_credit_or_aggregate"
        group = "bond_rate" if category == "bond_rate" else "bond"
        return _classification("bond", "cn", "fixed_income", category, group, "bond tokens", 0.85, excluded)

    if enhanced:
        return _classification("equity", region, "enhanced_index", "equity_enhanced_index", "enhanced", "enhanced token", 0.85, True)
    if _has_any(text, ("创业板50",)):
        return _classification("equity", "cn", "broad", "equity_cn_broad_chinext50", "chinext50", "创业板50 tokens", 0.9, excluded)
    if _has_any(text, ("创业板指数", "创业板ETF", "创业板指")):
        return _classification("equity", "cn", "broad", "equity_cn_broad_chinext", "chinext", "创业板 tokens", 0.85, excluded)
    if _has_any(text, ("上证50",)) and not _has_any(text, ("50AH", "AH", "红利")):
        return _classification("equity", "cn", "broad", "equity_cn_broad_sse50", "sse50", "上证50 tokens", 0.85, excluded)
    if _has_any(text, ("沪深300",)) and not _has_any(text, ("红利", "低波", "价值", "成长", "行业", "策略")):
        return _classification("equity", "cn", "broad", "equity_cn_broad_csi300", "csi300", "沪深300 tokens", 0.85, excluded)
    if _has_any(text, ("中证500",)):
        return _classification("equity", "cn", "broad", "equity_cn_broad_csi500", "csi500", "中证500 tokens", 0.85, excluded)
    if _has_any(text, ("中证1000",)):
        return _classification("equity", "cn", "broad", "equity_cn_broad_csi1000", "csi1000", "中证1000 tokens", 0.85, excluded)
    if _has_any(text, ("中证A500", "A500")):
        return _classification("equity", "cn", "broad", "equity_cn_broad_csia500", "csia500", "中证A500 tokens", 0.85, excluded)
    if _has_any(text, ("红利",)) and not _has_any(text, _DIVIDEND_EXCLUDE_TOKENS):
        category = "equity_cn_strategy_dividend_low_volatility" if _has_any(text, _LOW_VOL_TOKENS) else "equity_cn_strategy_dividend"
        return _classification("equity", "cn", "strategy", category, "dividend", "dividend tokens", 0.8, excluded)
    if _has_any(text, _LOW_VOL_TOKENS):
        return _classification("equity", "cn", "strategy", "equity_cn_strategy_low_volatility", "low_volatility", "low-volatility tokens", 0.75, excluded)
    if _has_any(text, _SECTOR_THEME_TOKENS):
        return _classification("equity", region, "sector_theme", f"equity_{region}_sector_theme", "sector_theme", "sector/theme tokens", 0.65, excluded)

    if region in {"hk", "us", "global"}:
        return _classification("equity", region, "broad_or_theme", f"equity_{region}_broad_or_theme", f"equity_{region}", "cross-border equity tokens", 0.65, excluded)
    if _has_any(text, ("股票", "指数", "ETF", "LOF")):
        return _classification("equity", "cn", "unknown_equity", "equity_cn_other", "equity_cn_other", "equity fallback", 0.5, excluded)
    return _classification("unknown", region, "unknown", "unknown", "unknown", "no stable rule matched", 0.0, excluded)


def _classification(
    asset_class: str,
    market_region: str,
    fund_strategy: str,
    fund_category: str,
    category_group: str,
    reason: str,
    confidence: float,
    excluded: bool = False,
    source: str = "metadata_rules",
) -> FundClassification:
    return FundClassification(
        classification_version=CLASSIFICATION_VERSION,
        asset_class=asset_class,
        market_region=market_region,
        fund_strategy=fund_strategy,
        fund_category=fund_category,
        category_group=category_group,
        classification_source=source,
        classification_confidence=float(confidence),
        classification_reason=reason,
        classification_excluded=bool(excluded),
    )


def _fields(row: Mapping[str, Any]) -> dict[str, str]:
    return {str(key): _norm(value) for key, value in row.items()}


def _joined_text(fields: Mapping[str, str]) -> str:
    keys = (
        "name",
        "fund_type",
        "invest_type",
        "type",
        "benchmark",
        "index_name",
        "etf_type",
        "csname",
        "extname",
        "cname",
    )
    return " ".join(fields.get(key, "") for key in keys)


def _region(text: str, fields: Mapping[str, str]) -> str:
    etf_type = fields.get("etf_type", "")
    if _has_any(text, _HK_TOKENS):
        return "hk"
    if _has_any(text, _US_TOKENS):
        return "us"
    if "QDII" in etf_type.upper() or _has_any(text, _GLOBAL_TOKENS):
        return "global"
    return "cn"


def _has_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token and token in text for token in tokens)


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
