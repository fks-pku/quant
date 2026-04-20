"""OpenAI LLM adapter."""

import os
from typing import Dict, Any

from quant.cio.llm_adapters.base import LLMAdapter


class OpenAIAdapter(LLMAdapter):
    """OpenAI GPT adapter for news analysis."""

    def __init__(self, model="gpt-4o-mini", api_key="", temperature=0.3):
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.temperature = temperature

    def analyze(self, prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from openai import OpenAI
        except ImportError:
            return {"sentiment": "neutral", "confidence": 0.5, "summary": "LLM analysis unavailable"}

        try:
            client = OpenAI(api_key=self.api_key)
            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            import json
            return json.loads(content)
        except Exception:
            return {"sentiment": "neutral", "confidence": 0.5, "summary": "LLM analysis unavailable"}
