"""News analyzer using LLM adapters."""

from typing import Dict, Any, Optional

from quant.features.cio.llm_adapters import OpenAIAdapter, ClaudeAdapter, OllamaAdapter
from quant.features.cio.llm_adapters.base import LLMAdapter


DEFAULT_PROMPT = (
    "Analyze the following financial news and return a JSON object with:\n"
    '- "sentiment": one of "bullish", "bearish", or "neutral"\n'
    '- "confidence": a float between 0.0 and 1.0\n'
    '- "summary": a 1-2 sentence summary\n\n'
    "News:\n{news_text}\n\nJSON response:"
)


class NewsAnalyzer:
    """Analyze financial news using LLM adapters."""

    def __init__(
        self,
        provider="openai",
        model="gpt-4o-mini",
        api_key="",
        base_url="http://localhost:11434",
        temperature=0.3,
        prompt_template=None,
    ):
        self.prompt_template = prompt_template or DEFAULT_PROMPT

        if provider == "openai":
            self.adapter: LLMAdapter = OpenAIAdapter(
                model=model, api_key=api_key, temperature=temperature
            )
        elif provider == "claude":
            self.adapter = ClaudeAdapter(
                model=model, api_key=api_key, temperature=temperature
            )
        elif provider == "ollama":
            self.adapter = OllamaAdapter(
                model=model, base_url=base_url, temperature=temperature
            )
        else:
            raise ValueError(f"Unknown provider: {provider}")
        self._adapter = self.adapter

    def analyze(self, news_text: str, market_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not news_text:
            return {"sentiment": "neutral", "confidence": 0.5, "summary": ""}
        prompt = self.prompt_template.format(news_text=news_text)
        context = market_context or {}
        result = self._adapter.analyze(prompt, context)
        return {
            "sentiment": result.get("sentiment", "neutral"),
            "confidence": float(result.get("confidence", 0.5)) if result.get("confidence") is not None else 0.5,
            "summary": result.get("summary", ""),
        }
