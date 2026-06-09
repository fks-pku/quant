import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Set

from quant.domain.ports.research_source import ResearchSource

logger = logging.getLogger(__name__)


_SEED_IDEAS = (
    {
        "title": "BigQuant 行业轮动策略",
        "source": "bigquant",
        "source_url": "https://bigquant.com/wiki/doc/DlXVSO3ZVu",
        "authors": "BigQuant Community",
        "published_date": "2021-12-14",
        "tags": ["行业轮动", "动量", "A股", "低频"],
        "description": (
            "公开论坛策略种子：使用日线 OHLCV、收盘价、成交量和申万行业分类，"
            "按行业 2个月、4个月、半年动量加权得分选择强势行业，约20个交易日换仓。"
            "适合 A股股票或 ETF 低频研究，必须重新验证交易成本、换手率、容量、"
            "点位可交易性和样本外稳定性。"
        ),
    },
    {
        "title": "BigQuant WorldQuant Alpha101 因子复现",
        "source": "bigquant",
        "source_url": "https://bigquant.com/wiki/doc/niKLVYPIRg",
        "authors": "BigQuant Community",
        "published_date": "2021-05-01",
        "tags": ["Alpha101", "量价因子", "A股", "因子库"],
        "description": (
            "公开论坛因子库种子：基于 WorldQuant 101 Formulaic Alphas 的日线 OHLCV "
            "和成交量量价表达式，适合作为 A股低频因子研究的表达式模板。"
            "需要在本地 daily_cn_ochl/HFQ 价格上重新做 Rank IC、FDR、多重检验、"
            "交易成本和容量审计。"
        ),
    },
    {
        "title": "BigQuant 行业轮动：景气度、趋势与拥挤度",
        "source": "bigquant",
        "source_url": "https://bigquant.com/square/paper/a7300085-2b6e-46dc-a0bd-0a56bd11ff58",
        "authors": "BigQuant AI Quant",
        "published_date": "2022-03-01",
        "tags": ["行业轮动", "景气度", "趋势", "拥挤度"],
        "description": (
            "公开研报摘要种子：把行业配置拆成景气度、趋势和拥挤度三个标尺，"
            "可转化为 A股日线行业合成指数或行业 ETF 轮动研究。"
            "本地实现应避免使用未来行业成分、未来基金规模或当期不可获得的宏观指标，"
            "并单独检验交易成本、容量、风格暴露和样本外稳定性。"
        ),
    },
    {
        "title": "JoinQuant 小市值低价股社区策略",
        "source": "joinquant",
        "source_url": "https://www.joinquant.com/community/post/detailMobile?postId=59884",
        "authors": "JoinQuant Community",
        "published_date": "2026-05-01",
        "tags": ["小市值", "低价股", "A股", "因子"],
        "description": (
            "公开社区策略种子：在可交易非 ST A股股票中，结合日线收盘价、成交量、"
            "换手率和 point-in-time 市值，偏向小市值低价股票。"
            "适合转化为低频多因子或指数增强候选，但必须使用状态表、停牌/ST/退市过滤、"
            "T+1、涨跌停、100股手数、交易成本和容量约束重做严格回测。"
        ),
    },
    {
        "title": "JoinQuant 多因子选股模型研究",
        "source": "joinquant",
        "source_url": "https://www.joinquant.com/index.php",
        "authors": "JoinQuant",
        "published_date": "2026-01-01",
        "tags": ["多因子", "机器学习", "A股", "低频"],
        "description": (
            "公开平台研究种子：多因子选股模型使用 A股股票日线、基本面和常用因子，"
            "可拆分为价值、质量、动量、低波、流动性等低频因子族。"
            "机器学习版本需要额外防止过拟合，先以简单线性/分位数组合、Rank IC、"
            "行业市值中性和交易成本审计作为准入门槛。"
        ),
    },
)


class ASharePublicForumSource(ResearchSource):
    def __init__(
        self,
        fetch_page_excerpt: bool = False,
        timeout: float = 10.0,
        source_name: str = "ashare_public_forum",
        source_filter: Optional[Iterable[str]] = None,
    ):
        self._fetch_page_excerpt = bool(fetch_page_excerpt)
        self._timeout = timeout
        self._source_name = source_name
        self._source_filter = {str(item).lower() for item in source_filter or []} or None

    @property
    def source_name(self) -> str:
        return self._source_name

    def search(self, query: Dict[str, Any], max_results: int = 10) -> List[Dict[str, Any]]:
        tokens = self._query_tokens(query)
        rows = []
        for seed in _SEED_IDEAS:
            seed_source = str(seed.get("source", "")).lower()
            if self._source_filter is not None and seed_source not in self._source_filter:
                continue
            if tokens and not self._matches(seed, tokens):
                continue
            row = dict(seed)
            original_source = str(row.get("source", ""))
            if self._source_filter is not None:
                row["source"] = self._source_name
            row["description"] = self._description(seed)
            row["metadata"] = {
                "source_family": self._source_name,
                "original_source": original_source,
                "tags": list(seed.get("tags") or []),
                "requires_manual_replication": True,
                "needs_bias_audit": True,
                "query": dict(query or {}),
            }
            rows.append(row)
            if len(rows) >= max_results:
                break
        return rows

    def _description(self, seed: Dict[str, Any]) -> str:
        description = str(seed.get("description", ""))
        if not self._fetch_page_excerpt:
            return description
        excerpt = self._fetch_excerpt(str(seed.get("source_url", "")))
        return f"{description} 页面摘录：{excerpt}" if excerpt else description

    def _fetch_excerpt(self, url: str) -> str:
        if not url:
            return ""
        try:
            import requests

            response = requests.get(url, timeout=self._timeout, headers={"User-Agent": "QuantResearchBot/1.0"})
            response.raise_for_status()
            return self._clean_html(getattr(response, "text", "") or "")[:500]
        except Exception as exc:
            logger.warning(f"A-share public forum page fetch failed: {exc}")
            return ""

    @staticmethod
    def _query_tokens(query: Dict[str, Any]) -> List[str]:
        if not isinstance(query, dict):
            return []
        value = query.get("query") or query.get("q") or query.get("keywords") or query.get("text") or ""
        if isinstance(value, (list, tuple)):
            text = " ".join(str(item) for item in value if item)
        else:
            text = str(value)
        return [token.lower() for token in re.findall(r"[\w\u4e00-\u9fff]+", text) if token.strip()]

    @staticmethod
    def _matches(seed: Dict[str, Any], tokens: List[str]) -> bool:
        haystack = " ".join(
            [
                str(seed.get("title", "")),
                str(seed.get("description", "")),
                " ".join(str(tag) for tag in seed.get("tags", []) or []),
            ]
        ).lower()
        return all(token in haystack for token in tokens)

    @staticmethod
    def _clean_html(value: str) -> str:
        return " ".join(re.sub(r"<[^>]+>", " ", value or "").split())


class BigQuantSource(ASharePublicForumSource):
    def __init__(self, fetch_page_excerpt: bool = False, timeout: float = 10.0):
        super().__init__(
            fetch_page_excerpt=fetch_page_excerpt,
            timeout=timeout,
            source_name="bigquant",
            source_filter={"bigquant"},
        )


class JoinQuantSource(ASharePublicForumSource):
    def __init__(
        self,
        fetch_page_excerpt: bool = False,
        timeout: float = 10.0,
        source_name: str = "joinquant",
        source_filter: Optional[Set[str]] = None,
    ):
        super().__init__(
            fetch_page_excerpt=fetch_page_excerpt,
            timeout=timeout,
            source_name=source_name,
            source_filter=source_filter or {"joinquant"},
        )
