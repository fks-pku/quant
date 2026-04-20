"""Claude LLM adapter."""

import os
from typing import Dict, Any

from quant.cio.llm_adapters.base import LLMAdapter


class ClaudeAdapter(LLMAdapter):
    """Anthropic Claude adapter for news analysis."""

    def __init__(self, model="claude-3-haiku-20240307", api_key="", temperature=0.3):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.temperature = temperature

    def analyze(self, prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from anthropic import Anthropic
        except ImportError:
            return {"sentiment": "neutral", "confidence": 0.5, "summary": "LLM analysis unavailable"}

        try:
            client = Anthropic(api_key=self.api_key)
            response = client.messages.create(
                model=self.model,
                max_tokens=1024,
                temperature=self.temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            content = response.content[0].text
            import json
            return json.loads(content)
        except Exception:
            return {"sentiment": "neutral", "confidence": 0.5, "summary": "LLM analysis unavailable"}
