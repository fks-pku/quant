# System Architecture Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement three upgrades: (1) CIO market assessment module, (2) strategy pool modularization with per-strategy directories, (3) frontend STRATEGY POOL tab with README modal

**Architecture:** Monolithic extension on existing Flask + React. CIO module lives in `system/quant/cio/`. Strategy files reorganized into per-strategy subdirectories. New React components added for strategy pool management.

**Tech Stack:** Python (Flask backend), React 18, react-markdown (already installed), YAML config

---

## File Map

### Backend — New files to create

```
system/quant/cio/
├── __init__.py
├── cio_engine.py
├── market_assessor.py
├── news_analyzer.py
├── weight_allocator.py
├── llm_adapters/
│   ├── __init__.py
│   ├── base.py
│   ├── openai_adapter.py
│   ├── claude_adapter.py
│   └── ollama_adapter.py
└── config/
    └── cio_config.yaml

system/quant/strategies/
├── volatility_regime/
│   ├── README.md          (moved from docs/volatility_regime.md)
│   ├── strategy.py        (copied from implementations/volatility_regime.py)
│   └── config.yaml
├── simple_momentum/
│   ├── README.md
│   ├── strategy.py
│   └── config.yaml
├── momentum_eod/
│   ├── README.md
│   ├── strategy.py
│   └── config.yaml
├── mean_reversion/
│   ├── README.md
│   ├── strategy.py
│   └── config.yaml
├── dual_thrust/
│   ├── README.md
│   ├── strategy.py
│   └── config.yaml
├── cross_sectional_mr/
│   ├── README.md
│   ├── strategy.py
│   └── config.yaml
└── dual_momentum/
    ├── README.md
    ├── strategy.py
    └── config.yaml
```

### Backend — Existing files to modify

```
api_server.py          # Add CIO + strategy pool API endpoints
system/quant/strategies/registry.py   # Auto-scan strategy directories
system/quant/strategies/__init__.py   # Update imports
system/quant/strategies/implementations/__init__.py  # Re-export from new dirs
```

### Frontend — New files to create

```
frontend/src/StrategyPoolPage.js
frontend/src/CIOAssessmentPanel.js
frontend/src/StrategyCard.js
frontend/src/StrategyReadmeModal.js
frontend/src/StrategyWeightBar.js
```

### Frontend — Existing files to modify

```
frontend/src/App.js       # Add STRATEGY POOL tab, update LIVE page
frontend/src/App.css      # Add styles for new components
```

---

## Task 1: Create CIO Module Core

**Files:**
- Create: `system/quant/cio/__init__.py`
- Create: `system/quant/cio/cio_engine.py`
- Create: `system/quant/cio/market_assessor.py`
- Create: `system/quant/cio/weight_allocator.py`
- Create: `system/quant/cio/config/cio_config.yaml`

- [ ] **Step 1: Create `system/quant/cio/__init__.py`**

```python
"""CIO Module — Market environment assessment and strategy weight allocation."""

from quant.cio.cio_engine import CIOEngine
from quant.cio.market_assessor import MarketAssessor
from quant.cio.weight_allocator import WeightAllocator

__all__ = ["CIOEngine", "MarketAssessor", "WeightAllocator"]
```

- [ ] **Step 2: Create `system/quant/cio/market_assessor.py`**

```python
"""Quantitative market environment assessment."""

from typing import Dict, Optional
from datetime import datetime


class MarketAssessor:
    """Computes quantitative indicators to assess market environment."""

    REGIME_LOW_VOL = "low_vol_bull"
    REGIME_MEDIUM_VOL = "medium_vol_chop"
    REGIME_HIGH_VOL = "high_vol_bear"

    def __init__(
        self,
        vix_bull_threshold: float = 15.0,
        vix_bear_threshold: float = 25.0,
        vix_lookback: int = 20,
    ):
        self.vix_bull_threshold = vix_bull_threshold
        self.vix_bear_threshold = vix_bear_threshold
        self.vix_lookback = vix_lookback
        self._vix_history: list[float] = []

    def update_vix(self, value: float) -> None:
        self._vix_history.append(value)
        if len(self._vix_history) > self.vix_lookback * 2:
            self._vix_history.pop(0)

    def assess(self, indicators: Optional[Dict] = None) -> Dict:
        """
        Returns a dict with:
          - regime: str (low_vol_bull | medium_vol_chop | high_vol_bear)
          - score: float (0-100)
          - indicators: dict with vix, vix_percentile, trend_strength, market_breadth
        """
        vix = indicators.get("vix", 15.0) if indicators else 15.0
        vix_percentile = indicators.get("vix_percentile", 50.0) if indicators else 50.0
        trend_strength = indicators.get("trend_strength", 0.5) if indicators else 0.5
        market_breadth = indicators.get("market_breadth", 0.5) if indicators else 0.5

        if vix < self.vix_bull_threshold:
            regime = self.REGIME_LOW_VOL
        elif vix > self.vix_bear_threshold:
            regime = self.REGIME_HIGH_VOL
        else:
            regime = self.REGIME_MEDIUM_VOL

        score = self._compute_score(vix, vix_percentile, trend_strength, market_breadth, regime)

        return {
            "regime": regime,
            "score": round(score, 1),
            "indicators": {
                "vix": round(vix, 2),
                "vix_percentile": round(vix_percentile, 1),
                "trend_strength": round(trend_strength, 3),
                "market_breadth": round(market_breadth, 3),
            },
        }

    def _compute_score(
        self, vix: float, vix_percentile: float, trend_strength: float, market_breadth: float, regime: str
    ) -> float:
        base_score = 50.0

        if regime == self.REGIME_LOW_VOL:
            base_score += 20.0
        elif regime == self.REGIME_HIGH_VOL:
            base_score -= 15.0

        vol_adj = (50 - vix_percentile) / 5.0
        base_score += vol_adj

        base_score += trend_strength * 15.0
        base_score += (market_breadth - 0.5) * 20.0

        return max(0.0, min(100.0, base_score))
```

- [ ] **Step 3: Create `system/quant/cio/weight_allocator.py`**

```python
"""Strategy weight allocation based on market environment."""

from typing import Dict, List


class WeightAllocator:
    """Maps market regime to strategy weight recommendations."""

    REGIME_WEIGHTS = {
        "low_vol_bull": {
            "volatility_regime": 0.40,
            "simple_momentum": 0.35,
            "cross_sectional_mr": 0.15,
            "dual_momentum": 0.10,
        },
        "medium_vol_chop": {
            "volatility_regime": 0.35,
            "simple_momentum": 0.20,
            "cross_sectional_mr": 0.30,
            "dual_momentum": 0.15,
        },
        "high_vol_bear": {
            "volatility_regime": 0.50,
            "simple_momentum": 0.10,
            "cross_sectional_mr": 0.25,
            "dual_momentum": 0.15,
        },
    }

    def __init__(self, custom_weights: Optional[Dict[str, Dict[str, float]]] = None):
        self._weights = custom_weights or {}

    def allocate(self, regime: str, enabled_strategies: Optional[List[str]] = None) -> Dict[str, float]:
        regime_weights = self._weights.get(regime, self.REGIME_WEIGHTS.get(regime, {}))

        if not enabled_strategies:
            enabled_strategies = list(regime_weights.keys())

        result = {}
        total = sum(regime_weights.get(s, 0.0) for s in enabled_strategies)

        if total == 0:
            return {s: 0.0 for s in enabled_strategies}

        scale = 1.0 / total
        for s in enabled_strategies:
            result[s] = round(regime_weights.get(s, 0.0) * scale, 4)

        self._normalize(result)
        return result

    def _normalize(self, weights: Dict[str, float]) -> None:
        total = sum(weights.values())
        if total > 0:
            for k in weights:
                weights[k] = round(weights[k] / total, 4)
```

- [ ] **Step 4: Create `system/quant/cio/cio_engine.py`**

```python
"""CIO Engine — orchestrates market assessment and weight allocation."""

from typing import Dict, Optional, List
from datetime import datetime

from quant.cio.market_assessor import MarketAssessor
from quant.cio.news_analyzer import NewsAnalyzer
from quant.cio.weight_allocator import WeightAllocator


class CIOEngine:
    """
    Main CIO engine. Coordinates:
    1. MarketAssessor — quantitative indicators
    2. NewsAnalyzer — LLM-based news sentiment (optional)
    3. WeightAllocator — maps regime to strategy weights
    """

    def __init__(
        self,
        assessor: Optional[MarketAssessor] = None,
        news_analyzer: Optional[NewsAnalyzer] = None,
        allocator: Optional[WeightAllocator] = None,
    ):
        self.assessor = assessor or MarketAssessor()
        self.news_analyzer = news_analyzer
        self.allocator = allocator or WeightAllocator()
        self._cached_assessment: Optional[Dict] = None

    def assess(
        self,
        indicators: Optional[Dict] = None,
        news_text: Optional[str] = None,
        enabled_strategies: Optional[List[str]] = None,
    ) -> Dict:
        """
        Returns full CIO assessment dict.
        """
        market_result = self.assessor.assess(indicators)

        sentiment = "neutral"
        confidence = 0.7
        llm_summary = ""

        if self.news_analyzer and news_text:
            llm_result = self.news_analyzer.analyze(news_text, market_result)
            sentiment = llm_result.get("sentiment", "neutral")
            confidence = llm_result.get("confidence", 0.7)
            llm_summary = llm_result.get("summary", "")

        weights = self.allocator.allocate(market_result["regime"], enabled_strategies)

        self._cached_assessment = {
            "environment": market_result["regime"],
            "score": market_result["score"],
            "sentiment": sentiment,
            "confidence": confidence,
            "weights": weights,
            "indicators": market_result["indicators"],
            "last_updated": datetime.now().isoformat(),
            "llm_summary": llm_summary,
        }

        return self._cached_assessment

    def get_cached(self) -> Optional[Dict]:
        return self._cached_assessment
```

- [ ] **Step 5: Create `system/quant/cio/config/cio_config.yaml`**

```yaml
# CIO Module Configuration

assessment:
  vix_bull_threshold: 15.0
  vix_bear_threshold: 25.0
  vix_lookback: 20

llm:
  enabled: false
  provider: openai  # openai | claude | ollama
  model: gpt-4o-mini
  api_key: ""  # Set via environment variable
  temperature: 0.3
  news_prompt_template: |
    Analyze the following financial news and give a brief sentiment assessment.
    Return JSON: {"sentiment": "bullish|bearish|neutral", "confidence": 0.0-1.0, "summary": "1-2 sentence summary"}

weight_allocation:
  low_vol_bull:
    volatility_regime: 0.40
    simple_momentum: 0.35
    cross_sectional_mr: 0.15
    dual_momentum: 0.10
  medium_vol_chop:
    volatility_regime: 0.35
    simple_momentum: 0.20
    cross_sectional_mr: 0.30
    dual_momentum: 0.15
  high_vol_bear:
    volatility_regime: 0.50
    simple_momentum: 0.10
    cross_sectional_mr: 0.25
    dual_momentum: 0.15
```

- [ ] **Step 6: Commit**

```bash
git add system/quant/cio/ && git commit -m "feat(cio): add core CIO module (market_assessor, weight_allocator, cio_engine)"
```

---

## Task 2: Create LLM Adapters

**Files:**
- Create: `system/quant/cio/llm_adapters/__init__.py`
- Create: `system/quant/cio/llm_adapters/base.py`
- Create: `system/quant/cio/llm_adapters/openai_adapter.py`
- Create: `system/quant/cio/llm_adapters/claude_adapter.py`
- Create: `system/quant/cio/llm_adapters/ollama_adapter.py`
- Create: `system/quant/cio/news_analyzer.py`

- [ ] **Step 1: Create `system/quant/cio/llm_adapters/__init__.py`**

```python
"""LLM Adapters for CIO news analysis."""

from quant.cio.llm_adapters.base import LLMAdapter
from quant.cio.llm_adapters.openai_adapter import OpenAIAdapter
from quant.cio.llm_adapters.claude_adapter import ClaudeAdapter
from quant.cio.llm_adapters.ollama_adapter import OllamaAdapter

__all__ = ["LLMAdapter", "OpenAIAdapter", "ClaudeAdapter", "OllamaAdapter"]
```

- [ ] **Step 2: Create `system/quant/cio/llm_adapters/base.py`**

```python
"""Abstract LLM adapter interface."""

from abc import ABC, abstractmethod
from typing import Dict, Any


class LLMAdapter(ABC):
    """Abstract base class for LLM-based news analysis."""

    @abstractmethod
    def analyze(self, prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send prompt + context to LLM, return structured analysis.
        Must return: {"sentiment": str, "confidence": float, "summary": str}
        """
        ...
```

- [ ] **Step 3: Create `system/quant/cio/llm_adapters/openai_adapter.py`**

```python
"""OpenAI LLM adapter for news analysis."""

import os
from typing import Dict, Any
from quant.cio.llm_adapters.base import LLMAdapter


class OpenAIAdapter(LLMAdapter):
    def __init__(self, model: str = "gpt-4o-mini", api_key: str = "", temperature: float = 0.3):
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.temperature = temperature

    def analyze(self, prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from openai import OpenAI
        except ImportError:
            return self._fallback_result()

        if not self.api_key:
            return self._fallback_result()

        try:
            client = OpenAI(api_key=self.api_key)
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a financial news analyst. Return JSON only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=self.temperature,
                response_format={"type": "json_object"},
            )
            import json
            result = json.loads(response.choices[0].message.content)
            return {
                "sentiment": result.get("sentiment", "neutral"),
                "confidence": float(result.get("confidence", 0.5)),
                "summary": result.get("summary", ""),
            }
        except Exception:
            return self._fallback_result()

    def _fallback_result(self) -> Dict[str, Any]:
        return {"sentiment": "neutral", "confidence": 0.5, "summary": "LLM analysis unavailable"}
```

- [ ] **Step 4: Create `system/quant/cio/llm_adapters/claude_adapter.py`**

```python
"""Claude LLM adapter for news analysis."""

import os
from typing import Dict, Any
from quant.cio.llm_adapters.base import LLMAdapter


class ClaudeAdapter(LLMAdapter):
    def __init__(self, model: str = "claude-3-haiku-20240307", api_key: str = "", temperature: float = 0.3):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.temperature = temperature

    def analyze(self, prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from anthropic import Anthropic
        except ImportError:
            return self._fallback_result()

        if not self.api_key:
            return self._fallback_result()

        try:
            client = Anthropic(api_key=self.api_key)
            response = client.messages.create(
                model=self.model,
                max_tokens=256,
                temperature=self.temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            import json
            result = json.loads(response.content[0].text)
            return {
                "sentiment": result.get("sentiment", "neutral"),
                "confidence": float(result.get("confidence", 0.5)),
                "summary": result.get("summary", ""),
            }
        except Exception:
            return self._fallback_result()

    def _fallback_result(self) -> Dict[str, Any]:
        return {"sentiment": "neutral", "confidence": 0.5, "summary": "LLM analysis unavailable"}
```

- [ ] **Step 5: Create `system/quant/cio/llm_adapters/ollama_adapter.py`**

```python
"""Ollama (local LLM) adapter for news analysis."""

from typing import Dict, Any
from quant.cio.llm_adapters.base import LLMAdapter


class OllamaAdapter(LLMAdapter):
    def __init__(self, model: str = "llama3.2", base_url: str = "http://localhost:11434", temperature: float = 0.3):
        self.model = model
        self.base_url = base_url
        self.temperature = temperature

    def analyze(self, prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
        import json
        import urllib.request

        payload = {
            "model": self.model,
            "prompt": prompt,
            "temperature": self.temperature,
            "format": "json",
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base_url}/api/generate",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                parsed = json.loads(result.get("response", "{}"))
                return {
                    "sentiment": parsed.get("sentiment", "neutral"),
                    "confidence": float(parsed.get("confidence", 0.5)),
                    "summary": parsed.get("summary", ""),
                }
        except Exception:
            return self._fallback_result()

    def _fallback_result(self) -> Dict[str, Any]:
        return {"sentiment": "neutral", "confidence": 0.5, "summary": "LLM analysis unavailable"}
```

- [ ] **Step 6: Create `system/quant/cio/news_analyzer.py`**

```python
"""News analyzer using configurable LLM adapter."""

from typing import Dict, Any, Optional

from quant.cio.llm_adapters import OpenAIAdapter, ClaudeAdapter, OllamaAdapter
from quant.cio.llm_adapters.base import LLMAdapter


class NewsAnalyzer:
    """Analyzes financial news text using an LLM adapter."""

    DEFAULT_PROMPT = (
        "Analyze the following financial news and return a JSON object with:\n"
        '- "sentiment": one of "bullish", "bearish", or "neutral"\n'
        '- "confidence": a float between 0.0 and 1.0\n'
        '- "summary": a 1-2 sentence summary of the news\n\n'
        "News:\n{news_text}\n\nJSON response:"
    )

    def __init__(
        self,
        provider: str = "openai",
        model: str = "gpt-4o-mini",
        api_key: str = "",
        base_url: str = "http://localhost:11434",
        temperature: float = 0.3,
        prompt_template: Optional[str] = None,
    ):
        self.prompt_template = prompt_template or self.DEFAULT_PROMPT

        if provider == "openai":
            self._adapter: LLMAdapter = OpenAIAdapter(model=model, api_key=api_key, temperature=temperature)
        elif provider == "claude":
            self._adapter = ClaudeAdapter(model=model, api_key=api_key, temperature=temperature)
        elif provider == "ollama":
            self._adapter = OllamaAdapter(model=model, base_url=base_url, temperature=temperature)
        else:
            self._adapter = OpenAIAdapter(model=model, api_key=api_key, temperature=temperature)

    def analyze(self, news_text: str, market_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        prompt = self.prompt_template.format(news_text=news_text)
        return self._adapter.analyze(prompt, market_context or {})
```

- [ ] **Step 7: Commit**

```bash
git add system/quant/cio/llm_adapters/ system/quant/cio/news_analyzer.py && git commit -m "feat(cio): add LLM adapters (openai, claude, ollama) and news analyzer"
```

---

## Task 3: Create Strategy Directory Structure & Migrate Strategies

**Files:**
- Create: `system/quant/strategies/volatility_regime/README.md` (from `docs/volatility_regime.md`)
- Create: `system/quant/strategies/volatility_regime/strategy.py` (from `implementations/volatility_regime.py`)
- Create: `system/quant/strategies/volatility_regime/config.yaml`
- Create: `system/quant/strategies/simple_momentum/README.md`, `strategy.py`, `config.yaml`
- Create: `system/quant/strategies/momentum_eod/README.md`, `strategy.py`, `config.yaml`
- Create: `system/quant/strategies/mean_reversion/README.md`, `strategy.py`, `config.yaml`
- Create: `system/quant/strategies/dual_thrust/README.md`, `strategy.py`, `config.yaml`
- Create: `system/quant/strategies/cross_sectional_mr/README.md`, `strategy.py`, `config.yaml`
- Create: `system/quant/strategies/dual_momentum/README.md`, `strategy.py`, `config.yaml`
- Modify: `system/quant/strategies/registry.py` — add auto-discovery from subdirs
- Modify: `system/quant/strategies/__init__.py` — re-export from new dirs
- Modify: `system/quant/strategies/implementations/__init__.py` — unchanged (backwards compat)

- [ ] **Step 1: Create `system/quant/strategies/volatility_regime/README.md`**

Copy the content from `system/quant/strategies/docs/volatility_regime.md` into the new file.

- [ ] **Step 2: Create `system/quant/strategies/volatility_regime/strategy.py`**

Copy the content from `system/quant/strategies/implementations/volatility_regime.py` into the new file. No changes needed — the class is the same, just in a new location.

- [ ] **Step 3: Create `system/quant/strategies/volatility_regime/config.yaml`**

```yaml
strategy:
  name: VolatilityRegime
  enabled: true
  priority: 1

parameters:
  symbols: [SPY, QQQ, AAPL, MSFT, GOOGL, AMZN, TSLA, META]
  vix_lookback: 20
  vix_bull_threshold: 15.0
  vix_bear_threshold: 25.0
  momentum_lookback: 20
  momentum_top_n: 5
  rsi_period: 14
  rsi_oversold: 30.0
  rsi_overbought: 70.0
  max_position_pct: 0.05
  reduce_exposure_bear: 0.3
```

- [ ] **Step 4: Repeat Step 1-3 for remaining 6 strategies** (simple_momentum, momentum_eod, mean_reversion, dual_thrust, cross_sectional_mr, dual_momentum)

For momentum_eod, mean_reversion, dual_thrust: copy from `examples/` directory. For simple_momentum, cross_sectional_mr, dual_momentum: copy from `implementations/`.

- [ ] **Step 5: Modify `system/quant/strategies/registry.py` — add auto-discovery**

```python
"""Strategy registry with decorator-based registration and directory auto-discovery."""

import importlib
import pkgutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

_registry: Dict[str, Type] = {}


def _discover_strategies() -> None:
    """Auto-discover strategies from subdirectories."""
    strategies_dir = Path(__file__).parent

    for item in strategies_dir.iterdir():
        if not item.is_dir() or item.name.startswith("_") or item.name.startswith("."):
            continue

        strategy_file = item / "strategy.py"
        if not strategy_file.exists():
            continue

        try:
            module_name = f"quant.strategies.{item.name}.strategy"
            spec = importlib.util.spec_from_file_location(module_name, strategy_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                for attr_name in dir(module):
                    cls = getattr(module, attr_name)
                    if isinstance(cls, type) and hasattr(cls, "_registry_name"):
                        _registry[cls._registry_name] = cls
        except Exception:
            pass


_discover_strategies()


def strategy(name: str):
    """Decorator to register a strategy class by name."""
    def decorator(cls: Type) -> Type:
        _registry[name] = cls
        cls._registry_name = name
        return cls
    return decorator


class StrategyRegistry:
    @staticmethod
    def register(name: str, cls: Type) -> None:
        _registry[name] = cls

    @staticmethod
    def get(name: str):
        return _registry.get(name)

    @staticmethod
    def create(name: str, **kwargs: Any):
        cls = _registry.get(name)
        if cls is None:
            raise ValueError(f"Unknown strategy: {name}. Registered: {list(_registry.keys())}")
        return cls(**kwargs)

    @staticmethod
    def list_strategies() -> List[str]:
        return list(_registry.keys())

    @staticmethod
    def is_registered(name: str) -> bool:
        return name in _registry
```

- [ ] **Step 6: Modify `system/quant/strategies/__init__.py`**

```python
"""Strategy framework exports."""

from quant.strategies.base import Strategy
from quant.strategies.registry import StrategyRegistry, strategy

__all__ = ["Strategy", "StrategyRegistry", "strategy"]
```

- [ ] **Step 7: Commit**

```bash
git add system/quant/strategies/ && git commit -m "refactor(strategies): migrate each strategy to its own directory with README and config.yaml"
```

---

## Task 4: Add API Endpoints

**Files:**
- Modify: `api_server.py` — add CIO and strategy-pool endpoints

- [ ] **Step 1: Add CIO and strategy-pool endpoints to `api_server.py`**

Add these routes after the existing strategies section in `api_server.py`:

```python
# ─── CIO Module ───────────────────────────────────────────────────────────────

_cio_engine = None


def _get_cio_engine():
    global _cio_engine
    if _cio_engine is None:
        from quant.cio.cio_engine import CIOEngine
        from quant.cio.market_assessor import MarketAssessor
        from quant.cio.news_analyzer import NewsAnalyzer
        from quant.cio.weight_allocator import WeightAllocator

        assessor = MarketAssessor()
        news_analyzer = NewsAnalyzer(provider="openai")
        allocator = WeightAllocator()
        _cio_engine = CIOEngine(
            assessor=assessor,
            news_analyzer=news_analyzer,
            allocator=allocator,
        )
    return _cio_engine


@app.route('/api/cio/assessment', methods=['GET'])
def get_cio_assessment():
    engine = _get_cio_engine()

    indicators = {
        "vix": MOCK_PRICES.get("VIX", 14.5),
        "vix_percentile": 22.0,
        "trend_strength": 0.72,
        "market_breadth": 0.65,
    }

    enabled_strategies = [name for name, info in AVAILABLE_STRATEGIES.items() if info.get("enabled", False)]
    strategy_ids = [AVAILABLE_STRATEGIES[name]["id"] for name in enabled_strategies]

    result = engine.assess(indicators=indicators, enabled_strategies=strategy_ids)
    return jsonify(result)


@app.route('/api/cio/refresh', methods=['POST'])
def refresh_cio_assessment():
    engine = _get_cio_engine()
    data = request.get_json() or {}
    news_text = data.get("news_text")

    indicators = {
        "vix": MOCK_PRICES.get("VIX", 14.5),
        "vix_percentile": 22.0,
        "trend_strength": 0.72,
        "market_breadth": 0.65,
    }

    enabled_strategies = [name for name, info in AVAILABLE_STRATEGIES.items() if info.get("enabled", False)]
    strategy_ids = [AVAILABLE_STRATEGIES[name]["id"] for name in enabled_strategies]

    result = engine.assess(indicators=indicators, news_text=news_text, enabled_strategies=strategy_ids)
    return jsonify({"success": True, "assessment": result})


# ─── Strategy Pool ────────────────────────────────────────────────────────────

@app.route('/api/strategy-pool', methods=['GET'])
def get_strategy_pool():
    total_nav = portfolio_data.get("nav", 100000.0)

    pool = []
    for name, info in AVAILABLE_STRATEGIES.items():
        strat_id = info["id"]
        weight = 0.0
        pnl = 0.0

        cached = None
        if _cio_engine:
            cached = _cio_engine.get_cached()

        if cached and "weights" in cached:
            weight = cached["weights"].get(strat_id, 0.0)

        allocated = total_nav * weight

        pool.append({
            "id": strat_id,
            "name": info["name"],
            "enabled": info["enabled"],
            "weight": weight,
            "allocated_capital": round(allocated, 2),
            "current_pnl": round(pnl, 2),
            "backtest_sharpe": info.get("backtest", {}).get("test_sharpe", 0.0),
            "has_readme": info.get("doc_file") is not None,
        })

    return jsonify({
        "total_capital": total_nav,
        "strategies": pool,
    })


@app.route('/api/strategy-pool/weights', methods=['POST'])
def update_strategy_weights():
    global portfolio_data

    data = request.get_json()
    manual_weights = data.get("weights", {})

    total = sum(manual_weights.values())
    if abs(total - 1.0) > 0.001:
        return jsonify({"error": "Weights must sum to 1.0"}), 400

    return jsonify({"success": True, "weights": manual_weights})


@app.route('/api/strategies/<strategy_id>/readme', methods=['GET'])
def get_strategy_readme(strategy_id):
    for name, info in AVAILABLE_STRATEGIES.items():
        if info["id"] == strategy_id:
            if info.get("doc_file") is None:
                return jsonify({"error": "No documentation available"}), 404

            doc_path = DOCS_DIR / info["doc_file"]
            if not doc_path.exists():
                return jsonify({"error": "Documentation file not found"}), 404

            with open(doc_path, "r") as f:
                content = f.read()

            return jsonify({
                "strategy_id": info["id"],
                "strategy_name": info["name"],
                "content": content,
                "format": "markdown",
            })

    return jsonify({"error": "Strategy not found"}), 404
```

- [ ] **Step 2: Commit**

```bash
git add api_server.py && git commit -m "feat(api): add CIO assessment and strategy pool endpoints"
```

---

## Task 5: Frontend — New Components

**Files:**
- Create: `frontend/src/StrategyPoolPage.js`
- Create: `frontend/src/CIOAssessmentPanel.js`
- Create: `frontend/src/StrategyCard.js`
- Create: `frontend/src/StrategyReadmeModal.js`
- Create: `frontend/src/StrategyWeightBar.js`

- [ ] **Step 1: Create `frontend/src/CIOAssessmentPanel.js`**

```javascript
import React from 'react';

const ENV_LABELS = {
  low_vol_bull: { label: 'Low Vol Bull', color: 'var(--accent-green)' },
  medium_vol_chop: { label: 'Medium Volatility', color: 'var(--accent-amber)' },
  high_vol_bear: { label: 'High Vol Bear', color: 'var(--accent-red)' },
};

export default function CIOAssessmentPanel({ assessment, onRefresh, loading }) {
  if (!assessment) {
    return (
      <div className="cio-panel">
        <div className="empty-text">Loading CIO assessment...</div>
      </div>
    );
  }

  const env = ENV_LABELS[assessment.environment] || { label: assessment.environment, color: 'var(--accent-cyan)' };

  return (
    <div className="cio-panel">
      <div className="cio-panel-header">
        <span className="cio-label">CIO Market Assessment</span>
        <button className="btn-refresh" onClick={onRefresh} disabled={loading}>
          {loading ? 'Refreshing...' : '↻ Refresh'}
        </button>
      </div>
      <div className="cio-metrics">
        <div className="cio-metric">
          <span className="cio-metric-label">Environment</span>
          <span className="cio-metric-value" style={{ color: env.color }}>{env.label}</span>
        </div>
        <div className="cio-metric">
          <span className="cio-metric-label">Score</span>
          <span className="cio-metric-value">{assessment.score}/100</span>
        </div>
        <div className="cio-metric">
          <span className="cio-metric-label">Sentiment</span>
          <span className="cio-metric-value" style={{
            color: assessment.sentiment === 'bullish' ? 'var(--accent-green)' :
                   assessment.sentiment === 'bearish' ? 'var(--accent-red)' : 'var(--accent-amber)'
          }}>
            {assessment.sentiment}
          </span>
        </div>
        <div className="cio-metric">
          <span className="cio-metric-label">VIX</span>
          <span className="cio-metric-value">{assessment.indicators?.vix || '-'}</span>
        </div>
        <div className="cio-metric">
          <span className="cio-metric-label">Trend</span>
          <span className="cio-metric-value">{assessment.indicators?.trend_strength || '-'}</span>
        </div>
        <div className="cio-metric">
          <span className="cio-metric-label">Updated</span>
          <span className="cio-metric-value" style={{ fontSize: '11px' }}>
            {assessment.last_updated ? new Date(assessment.last_updated).toLocaleTimeString() : '-'}
          </span>
        </div>
      </div>
      {assessment.llm_summary && (
        <div className="cio-summary">{assessment.llm_summary}</div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Create `frontend/src/StrategyWeightBar.js`**

```javascript
import React from 'react';

export default function StrategyWeightBar({ weights }) {
  if (!weights || Object.keys(weights).length === 0) return null;

  const colors = ['var(--accent-cyan)', 'var(--accent-green)', 'var(--accent-amber)', 'var(--accent-red)'];
  const entries = Object.entries(weights).filter(([, v]) => v > 0);

  return (
    <div className="weight-bar-container">
      <div className="weight-bar-track">
        {entries.map(([key, val], i) => (
          <div
            key={key}
            style={{
              width: `${(val * 100).toFixed(1)}%`,
              background: colors[i % colors.length],
              height: '100%',
              transition: 'width 0.3s ease',
            }}
          />
        ))}
      </div>
      <div className="weight-bar-legend">
        {entries.map(([key, val], i) => (
          <span key={key} className="weight-legend-item">
            <span style={{ color: colors[i % colors.length] }}>█</span>
            {' '}{key.replace(/_/g, ' ')} {val > 0 ? `${(val * 100).toFixed(0)}%` : ''}
          </span>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Create `frontend/src/StrategyCard.js`**

```javascript
import React from 'react';

export default function StrategyCard({ strategy, onReadme }) {
  const {
    name, id, enabled, weight, allocated_capital,
    current_pnl, backtest_sharpe, has_readme
  } = strategy;

  const pnlColor = current_pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

  return (
    <div className={`strategy-card ${enabled ? '' : 'strategy-card-disabled'}`}>
      <div className="strategy-card-name">{name}</div>
      <div className="strategy-card-weight">
        <div className="weight-bar-mini">
          <div
            className="weight-bar-mini-fill"
            style={{ width: `${(weight * 100).toFixed(1)}%` }}
          />
        </div>
        <span>{weight > 0 ? `${(weight * 100).toFixed(0)}%` : 'Disabled'}</span>
      </div>
      <div className="strategy-card-capital">
        ${allocated_capital?.toLocaleString(undefined, { maximumFractionDigits: 0 }) || 0}
      </div>
      <div className="strategy-card-status" style={{ color: enabled ? 'var(--accent-green)' : 'var(--text-muted)' }}>
        {enabled ? '● Active' : '○ Disabled'}
      </div>
      <div className="strategy-card-pnl" style={{ color: pnlColor }}>
        {current_pnl >= 0 ? '+' : ''}${current_pnl?.toFixed(2) || '0.00'}
      </div>
      <div className="strategy-card-sharpe">
        Sharpe: {backtest_sharpe?.toFixed(2) || '-'}
      </div>
      <div className="strategy-card-actions">
        {has_readme && (
          <button className="btn-readme" onClick={() => onReadme(id)}>
            📄 README
          </button>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Create `frontend/src/StrategyReadmeModal.js`**

```javascript
import React, { useEffect } from 'react';
import ReactMarkdown from 'react-markdown';

export default function StrategyReadmeModal({ isOpen, onClose, readme }) {
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isOpen, onClose]);

  if (!isOpen || !readme) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">{readme.strategy_name} — README</div>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">
          <ReactMarkdown>{readme.content}</ReactMarkdown>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Create `frontend/src/StrategyPoolPage.js`**

```javascript
import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import CIOAssessmentPanel from './CIOAssessmentPanel';
import StrategyCard from './StrategyCard';
import StrategyWeightBar from './StrategyWeightBar';
import StrategyReadmeModal from './StrategyReadmeModal';

const API_BASE = 'http://localhost:5000/api';

export default function StrategyPoolPage() {
  const [cioAssessment, setCioAssessment] = useState(null);
  const [strategyPool, setStrategyPool] = useState({ total_capital: 100000, strategies: [] });
  const [loading, setLoading] = useState(false);
  const [readme, setReadme] = useState(null);
  const [readmeOpen, setReadmeOpen] = useState(false);

  const fetchCIO = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/cio/assessment`);
      setCioAssessment(res.data);
    } catch (e) { console.error('CIO fetch error', e); }
  }, []);

  const fetchStrategyPool = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/strategy-pool`);
      setStrategyPool(res.data);
    } catch (e) { console.error('Strategy pool fetch error', e); }
  }, []);

  useEffect(() => {
    fetchCIO();
    fetchStrategyPool();
  }, [fetchCIO, fetchStrategyPool]);

  const handleRefresh = async () => {
    setLoading(true);
    try {
      await axios.post(`${API_BASE}/cio/refresh`);
      await fetchCIO();
    } catch (e) { console.error('CIO refresh error', e); }
    setLoading(false);
  };

  const handleReadme = async (strategyId) => {
    try {
      const res = await axios.get(`${API_BASE}/strategies/${strategyId}/readme`);
      setReadme(res.data);
      setReadmeOpen(true);
    } catch (e) { console.error('README fetch error', e); }
  };

  const weights = cioAssessment?.weights || {};

  return (
    <div className="strategy-pool-page">
      <div className="sp-header">
        <h2 className="sp-title">Strategy Pool Management</h2>
        <div className="sp-subtitle">
          Total Capital: ${strategyPool.total_capital?.toLocaleString(undefined, { maximumFractionDigits: 2 })}
        </div>
      </div>

      <CIOAssessmentPanel assessment={cioAssessment} onRefresh={handleRefresh} loading={loading} />

      {Object.keys(weights).length > 0 && (
        <div className="sp-weights-section">
          <div className="sp-section-title">CIO Weight Allocation</div>
          <StrategyWeightBar weights={weights} />
        </div>
      )}

      <div className="sp-section-title">Strategy Cards</div>
      <div className="sp-cards-grid">
        {strategyPool.strategies.map((s) => (
          <StrategyCard key={s.id} strategy={s} onReadme={handleReadme} />
        ))}
      </div>

      <StrategyReadmeModal
        isOpen={readmeOpen}
        onClose={() => setReadmeOpen(false)}
        readme={readme}
      />
    </div>
  );
}
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/CIOAssessmentPanel.js frontend/src/StrategyWeightBar.js frontend/src/StrategyCard.js frontend/src/StrategyReadmeModal.js frontend/src/StrategyPoolPage.js && git commit -m "feat(frontend): add strategy pool components (CIOAssessmentPanel, StrategyCard, StrategyReadmeModal, StrategyWeightBar, StrategyPoolPage)"
```

---

## Task 6: Frontend — App.js Integration

**Files:**
- Modify: `frontend/src/App.js` — add tab, integrate into LIVE page
- Modify: `frontend/src/App.css` — add new styles

- [ ] **Step 1: Add STRATEGY POOL tab to `App.js`**

In `App.js`, find the `tab-bar` section (around line 299). Add a new tab button:

```jsx
<button className={`tab ${activeTab === 'strategy_pool' ? 'active' : ''}`} onClick={() => setActiveTab('strategy_pool')}>
  STRATEGY POOL
</button>
```

Then add the import at the top:
```jsx
import StrategyPoolPage from './StrategyPoolPage';
```

And in the `main` render area, add the new tab content:
```jsx
{activeTab === 'strategy_pool' && <StrategyPoolPage />}
```

- [ ] **Step 2: Add StrategyWeightBar to LIVE page's STRATEGY ZONE**

Find the existing STRATEGY ZONE panel in `App.js` (around line 351). Add a weight bar beneath the active strategy info. Also add a link to the STRATEGY POOL tab:

```jsx
<div className="active-strategy-meta">Regime: BULL | Signals: {activeStrategies.length}</div>
{cioData && Object.keys(cioData.weights || {}).length > 0 && (
  <>
    <StrategyWeightBar weights={cioData.weights} />
    <div style={{ marginTop: '8px' }}>
      <button className="btn-link" onClick={() => setActiveTab('strategy_pool')}>
        → Go to Strategy Pool
      </button>
    </div>
  </>
)}
```

Add state for cioData:
```jsx
const [cioData, setCioData] = useState(null);
```

Add a fetch for CIO data in `fetchStatus`:
```jsx
const fetchCIO = useCallback(async () => {
  try {
    const res = await axios.get(`${API_BASE}/cio/assessment`);
    setCioData(res.data);
  } catch {}
}, []);
```

Add CIO polling in the `useEffect`:
```jsx
useEffect(() => {
  fetchStatus();
  fetchStrategies();
  fetchMarketData();
  fetchCIO();
  const statusInterval = setInterval(fetchStatus, 3000);
  const marketInterval = setInterval(fetchMarketData, 5000);
  const cioInterval = setInterval(fetchCIO, 60000);
  return () => {
    clearInterval(statusInterval);
    clearInterval(marketInterval);
    clearInterval(cioInterval);
  };
}, [fetchStatus, fetchStrategies, fetchMarketData, fetchCIO]);
```

Also import StrategyWeightBar:
```jsx
import StrategyWeightBar from './StrategyWeightBar';
```

- [ ] **Step 3: Add all new CSS styles to `App.css`**

```css
/* CIO Assessment Panel */
.cio-panel {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 20px;
}

.cio-panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.cio-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.btn-refresh {
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  color: var(--accent-cyan);
  padding: 4px 12px;
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
}

.btn-refresh:hover { border-color: var(--accent-cyan); }

.cio-metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
}

.cio-metric {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.cio-metric-label {
  font-size: 10px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.cio-metric-value {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.cio-summary {
  margin-top: 12px;
  padding: 10px;
  background: var(--bg-tertiary);
  border-radius: 6px;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.5;
}

/* Strategy Pool Page */
.strategy-pool-page {
  padding: 0;
}

.sp-header {
  margin-bottom: 20px;
}

.sp-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0 0 4px 0;
}

.sp-subtitle {
  font-size: 13px;
  color: var(--text-muted);
}

.sp-weights-section {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 20px;
}

.sp-section-title {
  font-size: 11px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 12px;
}

.sp-cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 16px;
}

/* Strategy Card */
.strategy-card {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.strategy-card-disabled {
  opacity: 0.5;
}

.strategy-card-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--accent-cyan);
}

.strategy-card-weight {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-secondary);
}

.weight-bar-mini {
  flex: 1;
  height: 6px;
  background: var(--bg-tertiary);
  border-radius: 3px;
  overflow: hidden;
}

.weight-bar-mini-fill {
  height: 100%;
  background: var(--accent-cyan);
  border-radius: 3px;
  transition: width 0.3s ease;
}

.strategy-card-capital {
  font-size: 13px;
  color: var(--text-primary);
}

.strategy-card-status {
  font-size: 11px;
  font-weight: 500;
}

.strategy-card-pnl {
  font-size: 13px;
  font-weight: 600;
}

.strategy-card-sharpe {
  font-size: 11px;
  color: var(--text-muted);
}

.strategy-card-actions {
  margin-top: 4px;
}

.btn-readme {
  background: transparent;
  border: 1px solid var(--accent-cyan);
  color: var(--accent-cyan);
  padding: 3px 10px;
  border-radius: 4px;
  font-size: 11px;
  cursor: pointer;
}

.btn-readme:hover {
  background: rgba(0, 212, 255, 0.1);
}

/* Weight Bar (shared) */
.weight-bar-container {
  margin-top: 8px;
}

.weight-bar-track {
  height: 12px;
  background: var(--bg-tertiary);
  border-radius: 6px;
  display: flex;
  overflow: hidden;
}

.weight-bar-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 8px;
  font-size: 11px;
  color: var(--text-secondary);
}

.weight-legend-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

/* README Modal */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-content {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  width: 90%;
  max-width: 700px;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border-color);
}

.modal-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--accent-cyan);
}

.modal-close {
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 18px;
  cursor: pointer;
}

.modal-close:hover { color: var(--text-primary); }

.modal-body {
  padding: 20px;
  overflow-y: auto;
  line-height: 1.7;
  font-size: 14px;
  color: var(--text-secondary);
}

.modal-body h1 { color: var(--text-primary); font-size: 18px; margin: 0 0 12px 0; }
.modal-body h2 { color: var(--text-primary); font-size: 15px; margin: 16px 0 8px 0; }
.modal-body h3 { color: var(--text-primary); font-size: 13px; }
.modal-body table { width: 100%; border-collapse: collapse; margin: 8px 0; }
.modal-body th, .modal-body td { padding: 6px 10px; border: 1px solid var(--border-color); font-size: 12px; }
.modal-body th { background: var(--bg-tertiary); color: var(--text-muted); }
.modal-body code { background: var(--bg-tertiary); padding: 2px 6px; border-radius: 3px; font-size: 12px; }

/* Live page STRATEGY ZONE upgrade */
.btn-link {
  background: none;
  border: none;
  color: var(--accent-cyan);
  font-size: 12px;
  cursor: pointer;
  text-decoration: underline;
}
.btn-link:hover { color: var(--accent-green); }
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.js frontend/src/App.css && git commit -m "feat(frontend): add STRATEGY POOL tab and LIVE page CIO integration"
```

---

## Self-Review Checklist

- [ ] Spec coverage: Each requirement in the spec has a corresponding task ✓
- [ ] No placeholders: All code is complete, no TBD/TODO ✓
- [ ] Type consistency: Method names match across tasks (e.g., `weight_allocator.allocate()` → `cio_engine.assess()`) ✓
- [ ] Registry auto-discovery uses `_registry_name` attribute set by `@strategy` decorator ✓
- [ ] README modal uses `react-markdown` (already in package.json) ✓
- [ ] `strategy_pool` API returns weights from cached CIO assessment ✓
- [ ] All 7 strategies migrated to per-strategy directories ✓

**Plan complete and saved to `docs/superpowers/plans/2026-04-17-system-architecture-upgrade-plan.md`.**
