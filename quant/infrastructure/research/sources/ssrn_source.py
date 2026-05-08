import logging
import random
import time
from html.parser import HTMLParser
from typing import Any, Dict, List
from urllib.parse import urljoin

from quant.domain.ports.research_source import ResearchSource

logger = logging.getLogger(__name__)

_USER_AGENT = "QuantResearchBot/1.0 (academic use)"
_BLOCKED_MARKERS = (
    "access denied",
    "captcha",
    "robot check",
    "unusual traffic",
    "verify you are human",
    "temporarily blocked",
    "cloudflare",
    "forbidden",
)
_PAPER_URL_MARKERS = ("abstract_id=", "/sol3/papers.cfm")
_POLITE_DELAY_SECONDS = (3.0, 5.0)


def _clean_text(value: str) -> str:
    return " ".join(str(value).split())


def _contains_any(value: str, markers) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in markers)


class _SSRNHTMLParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self._base_url = base_url
        self._stack = []
        self._active_link = None
        self._position = 0
        self._tokens = []
        self._links = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs_dict = {str(k).lower(): v or "" for k, v in attrs}
        context = {
            "tag": tag.lower(),
            "class": attrs_dict.get("class", "").lower(),
            "href": attrs_dict.get("href", ""),
        }
        self._stack.append(context)
        href = context["href"]
        if context["tag"] == "a" and href and self._is_paper_url(href):
            self._active_link = {
                "href": href,
                "text": [],
                "start": self._position,
                "end": self._position,
            }

    def handle_data(self, data: str) -> None:
        text = _clean_text(data)
        if not text:
            return
        self._position += 1
        classes = " ".join(item["class"] for item in self._stack if item["class"])
        tag = self._stack[-1]["tag"] if self._stack else ""
        self._tokens.append({
            "position": self._position,
            "text": text,
            "classes": classes,
            "tag": tag,
        })
        if self._active_link is not None:
            self._active_link["text"].append(text)
            self._active_link["end"] = self._position

    def handle_endtag(self, tag: str) -> None:
        lowered_tag = tag.lower()
        if lowered_tag == "a" and self._active_link is not None:
            title = _clean_text(" ".join(self._active_link["text"]))
            if title:
                self._links.append({
                    "title": title,
                    "source_url": urljoin(self._base_url, self._active_link["href"]),
                    "start": self._active_link["start"],
                    "end": self._active_link["end"],
                })
            self._active_link = None
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index]["tag"] == lowered_tag:
                del self._stack[index:]
                break

    def results(self, max_results: int) -> List[Dict[str, str]]:
        results = []
        for index, link in enumerate(self._links):
            next_start = self._links[index + 1]["start"] if index + 1 < len(self._links) else None
            tokens = [
                token for token in self._tokens
                if token["position"] > link["end"]
                and (next_start is None or token["position"] <= next_start)
            ]
            description = self._collect(tokens, ("abstract", "description", "summary", "desc"))
            if not description:
                description = self._first_paragraph(tokens)
            results.append({
                "title": link["title"],
                "description": description[:500],
                "source": "ssrn",
                "source_url": link["source_url"],
                "authors": self._collect(tokens, ("author",)),
                "published_date": self._collect(tokens, ("date", "posted", "published")),
            })
            if len(results) >= max_results:
                break
        return results

    def _collect(self, tokens, markers) -> str:
        values = []
        for token in tokens:
            context = f"{token['classes']} {token['tag']}"
            if _contains_any(context, markers):
                values.append(token["text"])
        return _clean_text(" ".join(values))

    def _first_paragraph(self, tokens) -> str:
        values = []
        for token in tokens:
            context = f"{token['classes']} {token['tag']}"
            if token["tag"] == "p" and not _contains_any(context, ("author", "date", "posted", "published")):
                values.append(token["text"])
        return _clean_text(" ".join(values))

    def _is_paper_url(self, href: str) -> bool:
        lowered = href.lower()
        return any(marker in lowered for marker in _PAPER_URL_MARKERS)


class SSRNSource(ResearchSource):
    def __init__(
        self,
        rate_limiter: Any = None,
        _base_url: str = "https://papers.ssrn.com/sol3/results.cfm",
        _timeout: float = 30.0,
        _max_retries: int = 2,
        _retry_backoff: float = 1.0,
    ):
        self._rate_limiter = rate_limiter
        self._base_url = _base_url
        self._timeout = _timeout
        self._max_retries = max(0, int(_max_retries))
        self._retry_backoff = max(0.0, float(_retry_backoff))

    @property
    def source_name(self) -> str:
        return "ssrn"

    def search(self, query: Dict[str, Any], max_results: int = 10) -> List[Dict[str, Any]]:
        if max_results <= 0:
            return []
        params = {"txtKeywords": self._query_text(query), "npage": "1"}
        headers = {"User-Agent": _USER_AGENT}
        for attempt in range(self._max_retries + 1):
            try:
                self._wait()
                import requests
                response = requests.get(
                    self._base_url,
                    params=params,
                    headers=headers,
                    timeout=self._timeout,
                )
                status_code = getattr(response, "status_code", 200)
                if status_code == 403:
                    logger.warning(f"SSRN search blocked: HTTP {status_code}")
                    return []
                if self._is_retryable_status(status_code):
                    if attempt < self._max_retries:
                        self._sleep(attempt)
                        continue
                    logger.warning(f"SSRN search failed: HTTP {status_code}")
                    return []
                if status_code >= 400:
                    logger.warning(f"SSRN search failed: HTTP {status_code}")
                    return []
                if hasattr(response, "raise_for_status"):
                    response.raise_for_status()
                html = getattr(response, "text", "") or ""
                if self._is_blocked(html):
                    logger.warning("SSRN search blocked by remote page")
                    return []
                return self._parse_html(html, max_results)
            except Exception as e:
                if attempt < self._max_retries:
                    self._sleep(attempt)
                    continue
                logger.warning(f"SSRN search failed: {e}")
                return []
        return []

    def _wait(self) -> None:
        if self._rate_limiter is not None:
            self._rate_limiter.wait(self.source_name)
            return
        time.sleep(random.uniform(*_POLITE_DELAY_SECONDS))

    def _sleep(self, attempt: int) -> None:
        if self._retry_backoff > 0:
            time.sleep(self._retry_backoff * (2 ** attempt))

    def _is_retryable_status(self, status_code: int) -> bool:
        return status_code == 429 or 500 <= status_code <= 599

    def _query_text(self, query: Dict[str, Any]) -> str:
        for key in ("query", "q", "keywords", "keyword", "text", "term"):
            value = query.get(key) if isinstance(query, dict) else None
            if isinstance(value, (list, tuple)):
                return " ".join(str(item) for item in value if item)
            if value:
                return str(value)
        return "quantitative trading"

    def _parse_html(self, html: str, max_results: int) -> List[Dict[str, Any]]:
        parser = _SSRNHTMLParser(self._base_url)
        parser.feed(html)
        parser.close()
        return parser.results(max_results)

    def _is_blocked(self, html: str) -> bool:
        return _contains_any(html, _BLOCKED_MARKERS)
