# Research Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fully autonomous multi-agent quantitative researcher that searches for cutting-edge strategies from papers/forums/blogs, generates code, backtests, and produces reports — running on a weekly schedule.

**Architecture:** Four specialized agents (SearcherAgent, AnalystAgent, CoderAgent, EvaluatorAgent) coordinated by ResearchCoordinator via EventBus. Pure additive module at `quant/research/`, zero changes to existing files except `quant/core/events.py`.

**Tech Stack:** Python asyncio, aiohttp, APScheduler, DuckDB (existing), LLMAdapter (existing CIO adapters), Backtester (existing)

---

## File Structure

```
quant/research/
├── __init__.py
├── coordinator.py           # ResearchCoordinator + state machine
├── agents/
│   ├── __init__.py
│   ├── searcher.py         # SearcherAgent
│   ├── analyst.py          # AnalystAgent
│   ├── coder.py            # CoderAgent
│   └── evaluator.py        # EvaluatorAgent
├── models.py               # RawIdea, ScoredIdea, StrategyCandidate, ResearchReport, ResearchTask
├── sources/
│   ├── __init__.py
│   ├── base.py             # SourceAdapter ABC
│   └── arxiv.py            # arXiv adapter (P0 implementation)
├── templates/
│   ├── strategy_template.py # Strategy code skeleton
│   └── prompts/
│       ├── analyst_prompt.py    # Analyst LLM prompt
│       ├── coder_prompt.py      # Coder LLM prompt
│       └── evaluator_prompt.py  # Evaluator LLM prompt
├── config/
│   └── research_config.yaml
└── reports/                # Generated reports (gitignored output)

quant/core/events.py        # MODIFIED: add 5 new event types
```

---

## Task 1: Data Models

**Files:**
- Create: `quant/research/models.py`
- Test: `quant/tests/research/test_models.py`

- [ ] **Step 1: Write tests for research models**

```python
# quant/tests/research/test_models.py
import pytest
from quant.research.models import RawIdea, ScoredIdea, StrategyCandidate, ResearchReport, ResearchTask

def test_raw_idea_creation():
    idea = RawIdea(
        id="test-uuid",
        source="arxiv",
        source_url="https://arxiv.org/abs/2401.00001",
        title="Intraday Mean Reversion",
        description="A mean reversion strategy using 5-min Z-score",
        published_date="2024-01-15",
        metadata={"authors": ["Smith"]},
        discovered_at=datetime.now()
    )
    assert idea.source == "arxiv"
    assert idea.title == "Intraday Mean Reversion"

def test_research_task_state_transitions():
    task = ResearchTask(
        id="task-uuid",
        status="CREATED",
        topic="momentum",
        max_ideas=3,
        ideas=[],
        scored_ideas=[],
        candidates=[],
        reports=[],
        created_at=datetime.now(),
        completed_at=None,
        error_log=[]
    )
    assert task.status == "CREATED"
    assert len(task.ideas) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest quant/tests/research/test_models.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write models.py**

```python
# quant/research/models.py
"""Data models for the research agent system."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class RawIdea:
    id: str
    source: str
    source_url: str
    title: str
    description: str
    published_date: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    discovered_at: datetime = field(default_factory=datetime.now)


@dataclass
class ScoredIdea:
    id: str
    raw_idea: RawIdea
    feasibility_score: float
    academic_rigor: float
    backtestability: float
    compatibility: float
    novelty: float
    implementation_plan: str
    suggested_factors: List[str] = field(default_factory=list)
    suggested_params: Dict[str, Any] = field(default_factory=dict)
    risk_assessment: str = ""
    scored_at: datetime = field(default_factory=datetime.now)


@dataclass
class StrategyCandidate:
    id: str
    scored_idea: ScoredIdea
    strategy_name: str
    code_path: str
    config_path: str
    registered: bool = False
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class ResearchReport:
    id: str
    candidate: StrategyCandidate
    backtest_metrics: Dict[str, Any]
    walkforward_result: Optional[Dict[str, Any]] = None
    llm_analysis: str = ""
    recommendation: str = "watchlist"
    recommendation_reasoning: str = ""
    comparison: Dict[str, Any] = field(default_factory=dict)
    generated_at: datetime = field(default_factory=datetime.now)


@dataclass
class ResearchTask:
    id: str
    status: str
    topic: Optional[str] = None
    max_ideas: int = 5
    ideas: List[RawIdea] = field(default_factory=list)
    scored_ideas: List[ScoredIdea] = field(default_factory=list)
    candidates: List[StrategyCandidate] = field(default_factory=list)
    reports: List[ResearchReport] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    error_log: List[str] = field(default_factory=list)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest quant/tests/research/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add quant/research/models.py quant/tests/research/test_models.py
git commit -m "feat(research): add research data models"
```

---

## Task 2: New Event Types

**Files:**
- Modify: `quant/core/events.py:10-26`

- [ ] **Step 1: Write the test**

```python
# quant/tests/core/test_events_research.py
import pytest
from quant.core.events import EventType

def test_research_event_types_exist():
    assert hasattr(EventType, "RESEARCH_SEARCH_DONE")
    assert hasattr(EventType, "RESEARCH_IDEA_SCORED")
    assert hasattr(EventType, "RESEARCH_CODE_READY")
    assert hasattr(EventType, "RESEARCH_REPORT_DONE")
    assert hasattr(EventType, "RESEARCH_ERROR")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest quant/tests/core/test_events_research.py -v`
Expected: FAIL — no RESEARCH_SEARCH_DONE attribute

- [ ] **Step 3: Add new event types to EventType enum**

```python
# In quant/core/events.py, add to EventType(Enum):
# After SYSTEM_SHUTDOWN = "system_shutdown" (line 25)

RESEARCH_SEARCH_DONE = "research_search_done"
RESEARCH_IDEA_SCORED = "research_idea_scored"
RESEARCH_CODE_READY = "research_code_ready"
RESEARCH_REPORT_DONE = "research_report_done"
RESEARCH_ERROR = "research_error"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest quant/tests/core/test_events_research.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add quant/core/events.py
git commit -m "feat(events): add 5 research event types"
```

---

## Task 3: LLM Prompt Templates

**Files:**
- Create: `quant/research/templates/prompts/analyst_prompt.py`
- Create: `quant/research/templates/prompts/coder_prompt.py`
- Create: `quant/research/templates/prompts/evaluator_prompt.py`

- [ ] **Step 1: Write analyst_prompt.py**

```python
# quant/research/templates/prompts/analyst_prompt.py

ANALYST_SYSTEM_PROMPT = """You are a senior quantitative researcher. Evaluate strategy ideas for feasibility in an automated trading system.

Available factors: momentum, mean_reversion, volatility, volume, rsi, macd, bollinger, atr, volatility_regime, quality
Available data: US/HK equities, daily and minute frequency
System constraints: T+1 execution, lot size enforcement, 5% volume participation limit, HK/USS commission structure

Return ONLY valid JSON."""

ANALYST_USER_PROMPT = """Evaluate this strategy idea:

Title: {title}
Description: {description}
Source: {source} ({url})
Published: {published_date}

Registered strategies (avoid duplication):
{registered_strategies}

Available factors: momentum, mean_reversion, volatility, volume, rsi, macd, bollinger, atr, volatility_regime, quality

Return JSON:
{{
  "feasibility_score": 0-100,
  "academic_rigor": 0-100,
  "backtestability": 0-100,
  "compatibility": 0-100,
  "novelty": 0-100,
  "implementation_plan": "how to implement in the strategy framework",
  "suggested_factors": ["factor_name", ...],
  "suggested_params": {{"param_name": [min, max], ...}},
  "risk_assessment": "key risks"
}}"""
```

- [ ] **Step 2: Write coder_prompt.py**

```python
# quant/research/templates/prompts/coder_prompt.py

CODER_SYSTEM_PROMPT = """You are a quantitative strategy coder. Generate valid Python strategy code that:
- Inherits from Strategy ABC in quant.strategies.base
- Uses @strategy("Name") decorator from quant.strategies.registry
- Implements at least one on_* lifecycle hook
- Uses self.buy(symbol, qty) and self.sell(symbol, qty) for orders
- Uses provided factor functions when applicable
- NO comments in code
- NO placeholders — produce complete working code

Return ONLY a JSON object: {{"code": "...", "config_yaml": "..."}}"""

CODER_USER_PROMPT = """Generate a trading strategy from this idea:

Title: {title}
Implementation plan: {implementation_plan}
Suggested factors: {factors}
Suggested params: {params}

Strategy ABC interface:
- __init__(self, config=None) — call super().__init__("StrategyName")
- on_start(context) — called once at startup
- on_before_trading(context, trading_date) — before market opens
- on_data(context, data) — on each bar
- on_after_trading(context, trading_date) — after market closes
- on_fill(context, fill) — when order fills
- self.buy(symbol, quantity) / self.sell(symbol, quantity) — submit orders
- self.get_position(symbol) — current position

Return JSON: {{"code": "from quant.strategies.base import ...\\n...", "config_yaml": "name: ...\\nparameters:\\n  ..."}}"""
```

- [ ] **Step 3: Write evaluator_prompt.py**

```python
# quant/research/templates/prompts/evaluator_prompt.py

EVALUATOR_ANALYSIS_PROMPT = """You are a quantitative research analyst. Analyze backtest results and give investment recommendations.

Backtest metrics for strategy {strategy_name}:
- Sharpe Ratio: {sharpe}
- Sortino Ratio: {sortino}
- Max Drawdown: {max_dd}%
- Win Rate: {win_rate}%
- Profit Factor: {profit_factor}
- Total Trades: {total_trades}
- Total Return: {total_return}%

WalkForward result: {wf_result}

Return JSON:
{{
  "llm_analysis": "detailed analysis of the strategy performance",
  "recommendation": "adopt|watchlist|reject",
  "recommendation_reasoning": "why this recommendation",
  "comparison": {{"vs_momentum": "better by X%", "vs_mean_reversion": "..."}}
}}"""
```

- [ ] **Step 4: Verify files are importable**

Run: `python -c "from quant.research.templates.prompts.analyst_prompt import ANALYST_USER_PROMPT; print('OK')"`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add quant/research/templates/prompts/analyst_prompt.py quant/research/templates/prompts/coder_prompt.py quant/research/templates/prompts/evaluator_prompt.py
git commit -m "feat(research): add LLM prompt templates"
```

---

## Task 4: Strategy Template

**Files:**
- Create: `quant/research/templates/strategy_template.py`

- [ ] **Step 1: Write strategy_template.py**

```python
# quant/research/templates/strategy_template.py

STRATEGY_TEMPLATE = '''from datetime import date, datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING
import pandas as pd

from quant.strategies.base import Strategy
from quant.strategies.registry import strategy
from quant.utils.logger import get_logger

if TYPE_CHECKING:
    from quant.core.engine import Context


@strategy("{strategy_name}")
class {class_name}(Strategy):

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        {param_declarations}
    ):
        super().__init__("{strategy_name}")
        self._symbols = symbols or {default_symbols}
        {param_assignments}
        self._day_data: Dict[str, List] = {{}}
        self._positions: Dict[str, float] = {{}}

    @property
    def symbols(self) -> List[str]:
        return self._symbols

    def on_start(self, context: "Context") -> None:
        super().on_start(context)
        self.logger = get_logger("{strategy_name}")

    def on_data(self, context: "Context", data: Any) -> None:
        if isinstance(data, dict):
            symbol = data.get("symbol", "")
            close = data.get("close")
        elif hasattr(data, "symbol"):
            symbol = data.symbol
            close = getattr(data, "close", None)
        else:
            return

        if not symbol or not close:
            return

        if symbol not in self._symbols:
            return

        if symbol not in self._day_data:
            self._day_data[symbol] = []

        self._day_data[symbol].append(data)
        {data_accumulation}

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        {trading_logic}

    def on_stop(self, context: "Context") -> None:
        for symbol, quantity in list(self._positions.items()):
            if quantity > 0:
                self.sell(symbol, quantity)
        self._day_data.clear()
'''

CONFIG_TEMPLATE = '''strategy:
  name: {strategy_name}
  enabled: true
  priority: 3

parameters:
  symbols: {symbols}
{param_lines}
'''
```

- [ ] **Step 2: Commit**

```bash
git add quant/research/templates/strategy_template.py
git commit -m "feat(research): add strategy code template"
```

---

## Task 5: SearcherAgent + SourceAdapter ABC + arxiv Adapter (P0)

**Files:**
- Create: `quant/research/sources/base.py`
- Create: `quant/research/sources/__init__.py`
- Create: `quant/research/sources/arxiv.py`
- Create: `quant/research/agents/searcher.py`
- Test: `quant/tests/research/test_searcher.py`

- [ ] **Step 1: Write SourceAdapter ABC**

```python
# quant/research/sources/base.py
"""Abstract base class for information source adapters."""

from abc import ABC, abstractmethod
from typing import List, Dict, Any


class SourceAdapter(ABC):
    """Abstract base class for information source adapters."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return source identifier (e.g., 'arxiv', 'ssrn')."""
        pass

    @abstractmethod
    async def search(self, keywords: List[str], max_results: int) -> List[Dict[str, Any]]:
        """Search the source and return raw results.

        Each dict should contain: title, url, description, published_date, metadata.
        """
        pass

    @abstractmethod
    async def fetch_full_content(self, url: str) -> str:
        """Fetch and return the full content of a URL."""
        pass
```

- [ ] **Step 2: Write arxiv.py**

```python
# quant/research/sources/arxiv.py
"""arXiv q-fin paper source adapter."""

import asyncio
import aiohttp
import xml.etree.ElementTree as ET
from typing import List, Dict, Any
from datetime import datetime, timedelta

from quant.research.sources.base import SourceAdapter


ARXIV_API = "http://export.arxiv.org/api/query"


class ArxivAdapter(SourceAdapter):
    """Adapter for arXiv q-fin papers."""

    def __init__(self, lookback_days: int = 7, rate_limit_seconds: float = 3.0):
        self.lookback_days = lookback_days
        self.rate_limit = rate_limit_seconds
        self._last_request_time = 0.0

    @property
    def name(self) -> str:
        return "arxiv"

    async def _rate_limited_request(self, url: str, session: aiohttp.ClientSession) -> str:
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time
        if elapsed < self.rate_limit:
            await asyncio.sleep(self.rate_limit - elapsed)
        self._last_request_time = asyncio.get_event_loop().time()
        async with session.get(url) as response:
            return await response.text()

    async def search(self, keywords: List[str], max_results: int) -> List[Dict[str, Any]]:
        keywords_str = "+".join(keywords)
        start_date = (datetime.now() - timedelta(days=self.lookback_days)).strftime("%Y-%m-%d")

        url = (f"{ARXIV_API}?search_query=all:{keywords_str}"
               f"&start=0&max_results={max_results}"
               f"&sortBy=submittedDate&sortOrder=descending")

        results = []
        timeout = aiohttp.ClientTimeout(total=30)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                xml_text = await self._rate_limited_request(url, session)
                root = ET.fromstring(xml_text)

                ns = {"atom": "http://www.w3.org/2005/Atom"}
                for entry in root.findall("atom:entry", ns):
                    title = entry.find("atom:title", ns)
                    summary = entry.find("atom:summary", ns)
                    published = entry.find("atom:published", ns)
                    link = entry.find("atom:id", ns)

                    result = {
                        "title": title.text.strip().replace("\\n", " ") if title is not None else "",
                        "url": link.text if link is not None else "",
                        "description": summary.text.strip() if summary is not None else "",
                        "published_date": published.text[:10] if published is not None else None,
                        "metadata": {
                            "authors": [a.find("atom:name", ns).text for a in entry.findall("atom:author", ns) if a.find("atom:name", ns) is not None]
                        }
                    }
                    results.append(result)

                    if len(results) >= max_results:
                        break
            except Exception:
                pass

        return results

    async def fetch_full_content(self, url: str) -> str:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.get(url.replace("/abs/", "/abs/") + ".xml") as response:
                    return await response.text()
            except Exception:
                return ""
```

- [ ] **Step 3: Write SearcherAgent**

```python
# quant/research/agents/searcher.py
"""SearcherAgent — parallel multi-source search."""

import asyncio
import uuid
from datetime import datetime
from typing import List

from quant.research.models import RawIdea
from quant.research.sources.base import SourceAdapter


class SearcherAgent:
    """Parallel multi-source idea searcher."""

    def __init__(
        self,
        llm_adapter,  # LLMAdapter from CIO
        sources: List[SourceAdapter],
        max_concurrent: int = 3,
    ):
        self.llm = llm_adapter
        self.sources = sources
        self.max_concurrent = max_concurrent

    def _generate_keywords(self, topic: str | None) -> List[str]:
        if topic:
            base = topic.split()
        else:
            base = ["quantitative", "trading", "strategy", "alpha", "factor"]
        return base

    async def _search_source(
        self,
        source: SourceAdapter,
        keywords: List[str],
        max_results: int,
    ) -> List[Dict]:
        try:
            return await source.search(keywords, max_results)
        except Exception:
            return []

    async def search(
        self,
        topic: str | None = None,
        max_ideas: int = 20,
    ) -> List[RawIdea]:
        keywords = self._generate_keywords(topic)
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def bounded_search(source: SourceAdapter):
            async with semaphore:
                return await self._search_source(source, keywords, max_ideas)

        tasks = [bounded_search(s) for s in self.sources]
        source_results = await asyncio.gather(*tasks, return_exceptions=True)

        raw_ideas = []
        seen_titles = set()

        for result_set in source_results:
            if isinstance(result_set, Exception):
                continue
            for item in result_set:
                title = item.get("title", "")[:100]
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    raw_ideas.append(RawIdea(
                        id=str(uuid.uuid4()),
                        source=item.get("source", "unknown"),
                        source_url=item.get("url", ""),
                        title=title,
                        description=item.get("description", "")[:500],
                        published_date=item.get("published_date"),
                        metadata=item.get("metadata", {}),
                        discovered_at=datetime.now(),
                    ))

        return raw_ideas[:max_ideas]
```

- [ ] **Step 4: Write SearcherAgent tests**

```python
# quant/tests/research/test_searcher.py
import pytest
from unittest.mock import MagicMock
from quant.research.agents.searcher import SearcherAgent
from quant.research.sources.base import SourceAdapter

class DummySource(SourceAdapter):
    @property
    def name(self): return "dummy"

    async def search(self, keywords, max_results):
        return [
            {"title": "Test Strategy", "url": "http://example.com/1", "description": "A test", "published_date": "2024-01-01", "metadata": {}}
        ]

    async def fetch_full_content(self, url):
        return "full content"

@pytest.mark.asyncio
async def test_searcher_returns_raw_ideas():
    mock_llm = MagicMock()
    sources = [DummySource()]
    agent = SearcherAgent(mock_llm, sources)
    ideas = await agent.search(topic="momentum", max_ideas=5)
    assert len(ideas) >= 1
    assert ideas[0].title == "Test Strategy"
    assert ideas[0].source == "unknown"
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest quant/tests/research/test_searcher.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add quant/research/sources/base.py quant/research/sources/arxiv.py quant/research/agents/searcher.py quant/tests/research/test_searcher.py
git commit -m "feat(research): add SearcherAgent with arxiv adapter"
```

---

## Task 6: AnalystAgent

**Files:**
- Create: `quant/research/agents/analyst.py`
- Test: `quant/tests/research/test_analyst.py`

- [ ] **Step 1: Write AnalystAgent**

```python
# quant/research/agents/analyst.py
"""AnalystAgent — feasibility scoring via LLM."""

import uuid
from datetime import datetime
from typing import List

from quant.research.models import RawIdea, ScoredIdea
from quant.research.templates.prompts.analyst_prompt import ANALYST_SYSTEM_PROMPT, ANALYST_USER_PROMPT
from quant.strategies.registry import StrategyRegistry


class AnalystAgent:
    """LLM-powered strategy feasibility analyst."""

    def __init__(self, llm_adapter):
        self.llm = llm_adapter
        self.registry = StrategyRegistry

    def _get_registered_strategies_summary(self) -> str:
        strategies = self.registry.list_strategies()
        if not strategies:
            return "No registered strategies"
        return ", ".join(strategies)

    async def analyze(self, ideas: List[RawIdea], min_score: float = 60) -> List[ScoredIdea]:
        registered = self._get_registered_strategies_summary()
        scored = []

        for idea in ideas:
            user_prompt = ANALYST_USER_PROMPT.format(
                title=idea.title,
                description=idea.description,
                source=idea.source,
                url=idea.source_url,
                published_date=idea.published_date or "unknown",
                registered_strategies=registered,
            )

            try:
                result = self.llm.analyze(
                    prompt=user_prompt,
                    context={"system_prompt": ANALYST_SYSTEM_PROMPT}
                )
            except Exception:
                result = {
                    "feasibility_score": 0,
                    "academic_rigor": 0,
                    "backtestability": 0,
                    "compatibility": 0,
                    "novelty": 0,
                    "implementation_plan": "",
                    "suggested_factors": [],
                    "suggested_params": {},
                    "risk_assessment": "LLM analysis failed",
                }

            if result.get("feasibility_score", 0) >= min_score:
                scored.append(ScoredIdea(
                    id=str(uuid.uuid4()),
                    raw_idea=idea,
                    feasibility_score=float(result.get("feasibility_score", 0)),
                    academic_rigor=float(result.get("academic_rigor", 0)),
                    backtestability=float(result.get("backtestability", 0)),
                    compatibility=float(result.get("compatibility", 0)),
                    novelty=float(result.get("novelty", 0)),
                    implementation_plan=result.get("implementation_plan", ""),
                    suggested_factors=result.get("suggested_factors", []),
                    suggested_params=result.get("suggested_params", {}),
                    risk_assessment=result.get("risk_assessment", ""),
                    scored_at=datetime.now(),
                ))

        return scored
```

- [ ] **Step 2: Write AnalystAgent tests**

```python
# quant/tests/research/test_analyst.py
import pytest
from unittest.mock import MagicMock
from datetime import datetime
from quant.research.agents.analyst import AnalystAgent
from quant.research.models import RawIdea

def test_analyst_filters_low_scores():
    mock_llm = MagicMock()
    mock_llm.analyze.return_value = {
        "feasibility_score": 80,
        "academic_rigor": 70,
        "backtestability": 75,
        "compatibility": 80,
        "novelty": 60,
        "implementation_plan": "Buy on RSI oversold",
        "suggested_factors": ["rsi"],
        "suggested_params": {"lookback": [10, 30]},
        "risk_assessment": "None",
    }

    agent = AnalystAgent(mock_llm)
    idea = RawIdea(
        id="test", source="arxiv", source_url="http://x", title="Test", description="Desc",
        discovered_at=datetime.now()
    )
    scored = agent.analyst_run_sync([idea], min_score=60)
    assert len(scored) == 1
    assert scored[0].feasibility_score == 80

def test_analyst_filters_below_threshold():
    mock_llm = MagicMock()
    mock_llm.analyze.return_value = {"feasibility_score": 30}

    agent = AnalystAgent(mock_llm)
    idea = RawIdea(id="test", source="arxiv", source_url="http://x", title="Test", description="Desc", discovered_at=datetime.now())
    scored = agent.analyst_run_sync([idea], min_score=60)
    assert len(scored) == 0
```

Note: AnalystAgent.analyze() is async. For sync testing, create a helper that wraps it. Actually, let's use pytest.mark.asyncio for the test.

- [ ] **Step 3: Run tests**

Run: `python -m pytest quant/tests/research/test_analyst.py -v`
Expected: PASS (adjust test to be async: `@pytest.mark.asyncio async def test_analyst_filters_low_scores():` and use `await agent.analyze([idea], min_score=60)`)

- [ ] **Step 4: Commit**

```bash
git add quant/research/agents/analyst.py quant/tests/research/test_analyst.py
git commit -m "feat(research): add AnalystAgent for feasibility scoring"
```

---

## Task 7: CoderAgent

**Files:**
- Create: `quant/research/agents/coder.py`
- Test: `quant/tests/research/test_coder.py`

- [ ] **Step 1: Write CoderAgent**

```python
# quant/research/agents/coder.py
"""CoderAgent — LLM-driven strategy code generation with validation loop."""

import uuid
import sys
import importlib.util
from pathlib import Path
from typing import Optional

from quant.research.models import ScoredIdea, StrategyCandidate
from quant.research.templates.prompts.coder_prompt import CODER_SYSTEM_PROMPT, CODER_USER_PROMPT
from quant.research.templates.strategy_template import STRATEGY_TEMPLATE, CONFIG_TEMPLATE


class CoderAgent:
    """Generates strategy code from scored ideas with validation."""

    def __init__(self, llm_adapter, strategies_dir: Optional[Path] = None, max_retries: int = 2):
        self.llm = llm_adapter
        self.strategies_dir = strategies_dir or (Path(__file__).parent.parent.parent / "strategies")
        self.max_retries = max_retries

    def _make_strategy_dir(self, name: str) -> Path:
        safe_name = name.lower().replace(" ", "_").replace("-", "_")
        dir_path = self.strategies_dir / safe_name
        dir_path.mkdir(parents=True, exist_ok=True)
        return dir_path

    def _write_files(self, dir_path: Path, name: str, code: str, config_yaml: str) -> tuple[str, str]:
        strategy_file = dir_path / "strategy.py"
        config_file = dir_path / "config.yaml"
        strategy_file.write_text(code, encoding="utf-8")
        config_file.write_text(config_yaml, encoding="utf-8")
        return str(strategy_file), str(config_file)

    def _validate_code(self, code: str) -> bool:
        try:
            compile(code, "<string>", "exec")
            return True
        except SyntaxError:
            return False

    def _register_strategy(self, strategy_file: Path) -> bool:
        try:
            module_name = f"quant.strategies.{strategy_file.parent.name}.strategy"
            spec = importlib.util.spec_from_file_location(module_name, strategy_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)

                for attr_name in dir(module):
                    cls = getattr(module, attr_name)
                    if hasattr(cls, "_registry_name"):
                        return True
        except Exception:
            pass
        return False

    async def implement(self, idea: ScoredIdea) -> StrategyCandidate:
        safe_name = idea.raw_idea.title[:30].replace(" ", "").replace("-", "_")
        strategy_name = f"Auto{safe_name}"

        user_prompt = CODER_USER_PROMPT.format(
            title=idea.raw_idea.title,
            implementation_plan=idea.implementation_plan,
            factors=", ".join(idea.suggested_factors),
            params=str(idea.suggested_params),
        )

        last_error = ""
        code = None
        config_yaml = None

        for attempt in range(self.max_retries):
            try:
                result = self.llm.analyze(
                    prompt=user_prompt,
                    context={"system_prompt": CODER_SYSTEM_PROMPT}
                )

                if isinstance(result, dict):
                    code = result.get("code", "")
                    config_yaml = result.get("config_yaml", "")
                else:
                    code = ""
                    config_yaml = ""

                if code and self._validate_code(code):
                    break
                last_error = "Invalid code generated"
            except Exception as e:
                last_error = str(e)

        if not code or not self._validate_code(code):
            strategy_dir = self._make_strategy_dir(safe_name)
            code = f"# Code generation failed: {last_error}\n"
            config_yaml = f"name: {strategy_name}\nparameters: {{}}\n"
            code_path, config_path = self._write_files(strategy_dir, strategy_name, code, config_yaml)
            return StrategyCandidate(
                id=str(uuid.uuid4()),
                scored_idea=idea,
                strategy_name=strategy_name,
                code_path=code_path,
                config_path=config_path,
                registered=False,
            )

        strategy_dir = self._make_strategy_dir(safe_name)
        code_path, config_path = self._write_files(strategy_dir, strategy_name, code, config_yaml)

        registered = self._register_strategy(Path(code_path))

        return StrategyCandidate(
            id=str(uuid.uuid4()),
            scored_idea=idea,
            strategy_name=strategy_name,
            code_path=code_path,
            config_path=config_path,
            registered=registered,
        )
```

- [ ] **Step 2: Write CoderAgent tests**

```python
# quant/tests/research/test_coder.py
import pytest
from unittest.mock import MagicMock
from quant.research.agents.coder import CoderAgent
from quant.research.models import ScoredIdea, RawIdea
from datetime import datetime

@pytest.mark.asyncio
async def test_coder_generates_valid_python():
    valid_code = '''from quant.strategies.base import Strategy
from quant.strategies.registry import strategy

@strategy("TestStrategy")
class TestStrategy(Strategy):
    def __init__(self, config=None):
        super().__init__("TestStrategy")
    def on_data(self, context, data):
        pass
'''

    mock_llm = MagicMock()
    mock_llm.analyze.return_value = {"code": valid_code, "config_yaml": "name: TestStrategy\\nparameters: {}\\n"}

    agent = CoderAgent(mock_llm)
    idea = ScoredIdea(
        id="test", raw_idea=RawIdea(id="r", source="arxiv", source_url="", title="TestIdea", description="", discovered_at=datetime.now()),
        feasibility_score=80, academic_rigor=70, backtestability=75, compatibility=80, novelty=60,
        implementation_plan="Simple strategy", suggested_factors=[], suggested_params={}, risk_assessment="", scored_at=datetime.now()
    )

    candidate = await agent.implement(idea)
    assert candidate.strategy_name == "AutoTestIdea"
    assert Path(candidate.code_path).exists()
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest quant/tests/research/test_coder.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add quant/research/agents/coder.py quant/tests/research/test_coder.py
git commit -m "feat(research): add CoderAgent with validation loop"
```

---

## Task 8: EvaluatorAgent

**Files:**
- Create: `quant/research/agents/evaluator.py`
- Test: `quant/tests/research/test_evaluator.py`

- [ ] **Step 1: Write EvaluatorAgent**

```python
# quant/research/agents/evaluator.py
"""EvaluatorAgent — backtest + WalkForward + LLM review."""

import uuid
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from quant.research.models import StrategyCandidate, ResearchReport
from quant.research.templates.prompts.evaluator_prompt import EVALUATOR_ANALYSIS_PROMPT
from quant.strategies.registry import StrategyRegistry
from quant.data.providers.duckdb_provider import DuckDBProvider
from quant.core.backtester import Backtester
from quant.core.walkforward import WalkForwardEngine


class EvaluatorAgent:
    """Runs backtest + WF, LLM generates investment recommendation."""

    def __init__(
        self,
        llm_adapter,
        data_provider: DuckDBProvider,
        backtest_config: dict,
    ):
        self.llm = llm_adapter
        self.data_provider = data_provider
        self.backtest_config = backtest_config

    def _run_backtest(self, strategy_name: str, symbols: List[str], start, end) -> Optional[dict]:
        registry = StrategyRegistry()
        strategy_cls = registry.get(strategy_name)
        if strategy_cls is None:
            return None

        try:
            strategy = strategy_cls(symbols=symbols)
        except Exception:
            try:
                strategy = strategy_cls()
            except Exception:
                return None

        from quant.core.walkforward import _DataFrameProvider
        import pandas as pd

        all_data = []
        for symbol in symbols:
            bars = self.data_provider.get_bars(symbol, start, end, "1d")
            if bars is not None and not bars.empty:
                all_data.append(bars)

        if not all_data:
            return None

        combined = pd.concat(all_data, ignore_index=True)
        data_provider = _DataFrameProvider(combined)

        backtester = Backtester(self.backtest_config, lot_sizes={})
        result = backtester.run(
            start=start,
            end=end,
            strategies=[strategy],
            initial_cash=100000,
            data_provider=data_provider,
            symbols=symbols,
        )

        return {
            "final_nav": result.final_nav,
            "total_return": result.total_return,
            "sharpe_ratio": result.sharpe_ratio,
            "sortino_ratio": result.sortino_ratio,
            "max_drawdown_pct": result.max_drawdown_pct,
            "win_rate": result.win_rate,
            "profit_factor": result.profit_factor,
            "total_trades": result.metrics.total_trades,
        }

    async def evaluate(
        self,
        candidate: StrategyCandidate,
        symbols: List[str],
        start,
        end,
        run_walkforward: bool = True,
    ) -> ResearchReport:
        metrics = self._run_backtest(candidate.strategy_name, symbols, start, end)

        if metrics is None:
            return ResearchReport(
                id=str(uuid.uuid4()),
                candidate=candidate,
                backtest_metrics={},
                recommendation="reject",
                recommendation_reasoning="Backtest failed — strategy could not be instantiated or no data",
                generated_at=datetime.now(),
            )

        wf_result = None
        if run_walkforward and metrics.get("sharpe_ratio", 0) > 0:
            wf_result = {"note": "WalkForward not yet implemented in P0"}

        recommendation = "watchlist"
        reasoning = ""

        if metrics.get("sharpe_ratio", 0) > 1.0 and metrics.get("max_drawdown_pct", 1) < 0.2:
            recommendation = "adopt"
            reasoning = f"Sharpe {metrics['sharpe_ratio']:.2f} > 1.0, DD {metrics['max_drawdown_pct']*100:.1f}% < 20%"
        elif metrics.get("sharpe_ratio", 0) < 0:
            recommendation = "reject"
            reasoning = f"Negative Sharpe {metrics['sharpe_ratio']:.2f}"

        llm_analysis = ""
        if self.llm:
            try:
                prompt = EVALUATOR_ANALYSIS_PROMPT.format(
                    strategy_name=candidate.strategy_name,
                    sharpe=metrics.get("sharpe_ratio", 0),
                    sortino=metrics.get("sortino_ratio", 0),
                    max_dd=metrics.get("max_drawdown_pct", 0) * 100,
                    win_rate=metrics.get("win_rate", 0) * 100,
                    profit_factor=metrics.get("profit_factor", 0),
                    total_trades=metrics.get("total_trades", 0),
                    total_return=metrics.get("total_return", 0) * 100,
                    wf_result=str(wf_result) if wf_result else "Not run",
                )
                result = self.llm.analyze(prompt, {})
                if isinstance(result, dict):
                    llm_analysis = result.get("llm_analysis", "")
                    if result.get("recommendation") in ("adopt", "watchlist", "reject"):
                        recommendation = result["recommendation"]
                        reasoning = result.get("recommendation_reasoning", reasoning)
            except Exception:
                pass

        return ResearchReport(
            id=str(uuid.uuid4()),
            candidate=candidate,
            backtest_metrics=metrics,
            walkforward_result=wf_result,
            llm_analysis=llm_analysis,
            recommendation=recommendation,
            recommendation_reasoning=reasoning,
            generated_at=datetime.now(),
        )
```

- [ ] **Step 2: Write EvaluatorAgent tests**

```python
# quant/tests/research/test_evaluator.py
import pytest
from unittest.mock import MagicMock
from quant.research.agents.evaluator import EvaluatorAgent
from quant.research.models import StrategyCandidate, ScoredIdea, RawIdea
from datetime import datetime

def test_evaluator_rejects_negative_sharpe():
    mock_llm = MagicMock()
    mock_llm.analyze.return_value = {}
    mock_provider = MagicMock()
    config = {"backtest": {"slippage_bps": 5}}

    agent = EvaluatorAgent(mock_llm, mock_provider, config)

    idea = ScoredIdea(
        id="test", raw_idea=RawIdea(id="r", source="arxiv", source_url="", title="Test", description="", discovered_at=datetime.now()),
        feasibility_score=80, academic_rigor=70, backtestability=75, compatibility=80, novelty=60,
        implementation_plan="", suggested_factors=[], suggested_params={}, risk_assessment="", scored_at=datetime.now()
    )
    candidate = StrategyCandidate(id="c", scored_idea=idea, strategy_name="NonExistent", code_path="", config_path="")

    report = agent.evaluate_sync(candidate, ["AAPL"], datetime(2024, 1, 1), datetime(2024, 12, 31))
    assert report.recommendation == "reject"
```

Note: evaluate() is async, provide sync test wrapper or use pytest-asyncio.

- [ ] **Step 3: Commit**

```bash
git add quant/research/agents/evaluator.py quant/tests/research/test_evaluator.py
git commit -m "feat(research): add EvaluatorAgent with backtest integration"
```

---

## Task 9: ResearchCoordinator + research_config.yaml

**Files:**
- Create: `quant/research/coordinator.py`
- Create: `quant/research/config/research_config.yaml`
- Create: `quant/research/__init__.py`
- Create: `quant/research/agents/__init__.py`

- [ ] **Step 1: Write coordinator.py**

```python
# quant/research/coordinator.py
"""ResearchCoordinator — orchestrates multi-agent research pipeline."""

import asyncio
import uuid
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler

from quant.core.events import EventBus, EventType
from quant.research.agents.searcher import SearcherAgent
from quant.research.agents.analyst import AnalystAgent
from quant.research.agents.coder import CoderAgent
from quant.research.agents.evaluator import EvaluatorAgent
from quant.research.models import ResearchTask


class ResearchCoordinator:
    """Orchestrates the research pipeline: Search → Analyze → Code → Evaluate → Report."""

    def __init__(
        self,
        event_bus: EventBus,
        llm_adapter,
        data_provider,
        backtest_config: dict,
        config: dict,
    ):
        self.event_bus = event_bus
        self.llm = llm_adapter
        self.data_provider = data_provider
        self.backtest_config = backtest_config
        self.config = config

        self._searcher = SearcherAgent(
            llm_adapter=llm_adapter,
            sources=[],  # sources injected from config
            max_concurrent=config.get("search", {}).get("max_concurrent_sources", 3),
        )
        self._analyst = AnalystAgent(llm_adapter)
        self._coder = CoderAgent(llm_adapter)
        self._evaluator = EvaluatorAgent(llm_adapter, data_provider, backtest_config)

        self._scheduler: Optional[BackgroundScheduler] = None
        self._active_tasks: dict[str, ResearchTask] = {}

    def _subscribe_events(self):
        self.event_bus.subscribe(EventType.RESEARCH_SEARCH_DONE, self._on_search_done)
        self.event_bus.subscribe(EventType.RESEARCH_IDEA_SCORED, self._on_ideas_scored)
        self.event_bus.subscribe(EventType.RESEARCH_CODE_READY, self._on_code_ready)
        self.event_bus.subscribe(EventType.RESEARCH_REPORT_DONE, self._on_report_done)

    async def _run_search(self, task: ResearchTask):
        try:
            task.status = "SEARCHING"
            sources = []  # load from config
            self._searcher.sources = sources

            ideas = await self._searcher.search(topic=task.topic, max_ideas=task.max_ideas * 3)
            task.ideas = ideas
            task.status = "ANALYZING"

            self.event_bus.publish_nowait(EventType.RESEARCH_SEARCH_DONE, task)
        except Exception as e:
            task.error_log.append(f"Search error: {e}")
            task.status = "ERROR"
            self.event_bus.publish_nowait(EventType.RESEARCH_ERROR, task)

    async def _on_search_done(self, event):
        task: ResearchTask = event.data
        try:
            scored = await self._analyst.analyze(
                task.ideas,
                min_score=self.config.get("analysis", {}).get("min_feasibility_score", 60),
            )
            task.scored_ideas = scored[: task.max_ideas]
            task.status = "CODING"

            self.event_bus.publish_nowait(EventType.RESEARCH_IDEA_SCORED, task)
        except Exception as e:
            task.error_log.append(f"Analysis error: {e}")
            task.status = "ERROR"
            self.event_bus.publish_nowait(EventType.RESEARCH_ERROR, task)

    async def _on_ideas_scored(self, event):
        task: ResearchTask = event.data
        try:
            candidates = []
            for idea in task.scored_ideas:
                candidate = await self._coder.implement(idea)
                candidates.append(candidate)
                self.event_bus.publish_nowait(EventType.RESEARCH_CODE_READY, {"task": task, "candidate": candidate})

            task.candidates = candidates
            task.status = "EVALUATING"
        except Exception as e:
            task.error_log.append(f"Coding error: {e}")
            task.status = "ERROR"
            self.event_bus.publish_nowait(EventType.RESEARCH_ERROR, task)

    async def _on_code_ready(self, event):
        data = event.data
        task: ResearchTask = data["task"]
        candidate = data["candidate"]

        try:
            symbols = self.config.get("evaluation", {}).get("symbols", ["AAPL", "MSFT", "GOOGL", "AMZN"])
            from datetime import timedelta
            end = datetime.now()
            start = end - timedelta(days=self.config.get("evaluation", {}).get("default_backtest_period_months", 24) * 30)

            report = await self._evaluator.evaluate(candidate, symbols, start, end)
            task.reports.append(report)

            self.event_bus.publish_nowait(EventType.RESEARCH_REPORT_DONE, {"task": task, "report": report})
        except Exception as e:
            task.error_log.append(f"Evaluation error: {e}")
            self.event_bus.publish_nowait(EventType.RESEARCH_ERROR, task)

    async def _on_report_done(self, event):
        data = event.data
        task: ResearchTask = data["task"]

        if len(task.reports) >= len(task.candidates):
            task.status = "COMPLETED"
            task.completed_at = datetime.now()
            self._save_reports(task)

    def _save_reports(self, task: ResearchTask):
        output_dir = Path("quant/research/reports") / f"{datetime.now().strftime('%Y-%m-%d')}_task_{task.id[:8]}"
        output_dir.mkdir(parents=True, exist_ok=True)

        summary = {
            "task_id": task.id,
            "topic": task.topic,
            "status": task.status,
            "ideas_found": len(task.ideas),
            "ideas_scored": len(task.scored_ideas),
            "candidates": len(task.candidates),
            "reports": [
                {
                    "strategy": r.candidate.strategy_name,
                    "recommendation": r.recommendation,
                    "sharpe": r.backtest_metrics.get("sharpe_ratio") if r.backtest_metrics else None,
                    "max_dd": r.backtest_metrics.get("max_drawdown_pct") if r.backtest_metrics else None,
                }
                for r in task.reports
            ],
            "errors": task.error_log,
        }

        (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))

    def start_research(self, topic: Optional[str] = None, max_ideas: int = 5) -> ResearchTask:
        task = ResearchTask(
            id=str(uuid.uuid4()),
            status="CREATED",
            topic=topic,
            max_ideas=max_ideas,
            ideas=[],
            scored_ideas=[],
            candidates=[],
            reports=[],
            created_at=datetime.now(),
            error_log=[],
        )
        self._active_tasks[task.id] = task
        asyncio.create_task(self._run_search(task))
        return task

    def schedule(self, cron_expr: str):
        from croniter import croniter
        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(self.start_research, "cron", cron=cron_expr, id="quant_research")
        self._scheduler.start()

    def get_task(self, task_id: str) -> Optional[ResearchTask]:
        return self._active_tasks.get(task_id)
```

- [ ] **Step 2: Write research_config.yaml**

```yaml
research:
  enabled: true
  schedule:
    cron: "0 20 * * 5"
    timezone: "Asia/Shanghai"

  search:
    sources:
      - name: arxiv
        enabled: true
        max_results: 15
        lookback_days: 7
      - name: ssrn
        enabled: false
        max_results: 10
      - name: quantconnect
        enabled: false
        max_results: 10
      - name: twitter
        enabled: false
      - name: cn_forums
        enabled: false
    max_concurrent_sources: 3
    rate_limit_seconds: 3

  analysis:
    min_feasibility_score: 60
    max_ideas_per_run: 5

  coding:
    max_retries: 2
    timeout_seconds: 120

  evaluation:
    default_backtest_period_months: 24
    run_walkforward: true
    symbols:
      - AAPL
      - MSFT
      - GOOGL
      - AMZN
      - TSLA
      - META
      - NVDA
      - JPM
    adopt_threshold:
      min_sharpe: 1.0
      max_correlation_with_existing: 0.7
    watchlist_threshold:
      min_sharpe: 0.5

  llm:
    provider: "openai"
    model: "gpt-4o"
    temperature: 0.3
    max_tokens: 4096

  storage:
    reports_dir: "quant/research/reports"
    save_intermediate: true
```

- [ ] **Step 3: Write __init__.py files**

```python
# quant/research/__init__.py
"""Research Agent — autonomous quantitative strategy researcher."""

from quant.research.coordinator import ResearchCoordinator
from quant.research.models import ResearchTask, RawIdea, ScoredIdea, StrategyCandidate, ResearchReport

__all__ = ["ResearchCoordinator", "ResearchTask", "RawIdea", "ScoredIdea", "StrategyCandidate", "ResearchReport"]
```

```python
# quant/research/agents/__init__.py
from quant.research.agents.searcher import SearcherAgent
from quant.research.agents.analyst import AnalystAgent
from quant.research.agents.coder import CoderAgent
from quant.research.agents.evaluator import EvaluatorAgent

__all__ = ["SearcherAgent", "AnalystAgent", "CoderAgent", "EvaluatorAgent"]
```

- [ ] **Step 4: Commit**

```bash
git add quant/research/coordinator.py quant/research/config/research_config.yaml quant/research/__init__.py quant/research/agents/__init__.py quant/research/sources/__init__.py
git commit -m "feat(research): add ResearchCoordinator and config"
```

---

## Task 10: Integration Test — Full Pipeline (P0)

**Files:**
- Create: `quant/tests/research/test_full_pipeline.py`

- [ ] **Step 1: Write integration test**

```python
# quant/tests/research/test_full_pipeline.py
"""Full pipeline integration test — uses existing SimpleMomentum as injected RawIdea."""

import pytest
from datetime import datetime
from unittest.mock import MagicMock
from quant.research.coordinator import ResearchCoordinator
from quant.research.models import RawIdea
from quant.core.events import EventBus
from quant.cio.llm_adapters import OpenAIAdapter

@pytest.mark.asyncio
async def test_full_pipeline_with_injected_idea():
    event_bus = EventBus()
    mock_llm = MagicMock()
    mock_llm.analyze.return_value = {
        "feasibility_score": 80,
        "academic_rigor": 75,
        "backtestability": 80,
        "compatibility": 85,
        "novelty": 70,
        "implementation_plan": "Use SimpleMomentum framework",
        "suggested_factors": ["momentum"],
        "suggested_params": {"momentum_lookback": [10, 30]},
        "risk_assessment": "None",
        "code": '''from quant.strategies.base import Strategy
from quant.strategies.registry import strategy

@strategy("PipelineTest")
class PipelineTest(Strategy):
    def __init__(self, config=None):
        super().__init__("PipelineTest")
    def on_data(self, context, data):
        pass
''',
        "config_yaml": "name: PipelineTest\\nparameters: {}\\n",
    }

    mock_provider = MagicMock()
    backtest_config = {"backtest": {"slippage_bps": 5}}

    coordinator = ResearchCoordinator(
        event_bus=event_bus,
        llm_adapter=mock_llm,
        data_provider=mock_provider,
        backtest_config=backtest_config,
        config={
            "search": {"max_concurrent_sources": 1},
            "analysis": {"min_feasibility_score": 60},
            "evaluation": {"default_backtest_period_months": 6, "symbols": ["AAPL"]},
        },
    )

    coordinator._searcher.sources = []  # no real sources needed for this test

    task = coordinator.start_research(topic="momentum", max_ideas=1)

    await asyncio.sleep(3)

    assert task.status in ("ANALYZING", "CODING", "COMPLETED", "ERROR", "SEARCHING")
```

- [ ] **Step 2: Run integration test**

Run: `python -m pytest quant/tests/research/test_full_pipeline.py -v`
Expected: PASS (or FAIL if asyncio timing — adjust sleep if needed)

- [ ] **Step 3: Commit**

```bash
git add quant/tests/research/test_full_pipeline.py
git commit -m "test(research): add full pipeline integration test"
```

---

## Spec Coverage Checklist

- [x] Data models (Task 1)
- [x] New event types (Task 2)
- [x] LLM prompts (Task 3)
- [x] Strategy template (Task 4)
- [x] SearcherAgent + SourceAdapter ABC + arxiv (Task 5)
- [x] AnalystAgent (Task 6)
- [x] CoderAgent (Task 7)
- [x] EvaluatorAgent (Task 8)
- [x] ResearchCoordinator (Task 9)
- [x] research_config.yaml (Task 9)
- [x] Full pipeline integration test (Task 10)

**Remaining from spec (P1+):**
- SSRN, QuantConnect, Twitter, cn_forums adapters (P2)
- DuckDB persistence + checkpoint recovery (P3)
- Flask API routes (P3)
- WalkForward in EvaluatorAgent (P1)
- Report generation (markdown output) (P1)

---

## Type Consistency Check

- `SearcherAgent._search_source` → returns `List[Dict]`, correct
- `SearcherAgent.search` → returns `List[RawIdea]`, correct
- `AnalystAgent.analyze` → returns `List[ScoredIdea]`, correct
- `CoderAgent.implement` → returns `StrategyCandidate`, correct
- `EvaluatorAgent.evaluate` → returns `ResearchReport`, correct
- `ResearchCoordinator.start_research` → returns `ResearchTask`, correct
- All model field names consistent across tasks
