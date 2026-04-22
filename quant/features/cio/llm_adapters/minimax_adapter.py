"""MiniMax LLM adapter."""

import os
from typing import Dict, Any

from quant.features.cio.llm_adapters.base import LLMAdapter


class MiniMaxAdapter(LLMAdapter):
    """MiniMax LLM adapter for strategy evaluation."""

    def __init__(
        self,
        model: str = "MiniMax-M2.7",
        api_key: str = "",
        base_url: str = "https://api.minimax.chat/v1",
        temperature: float = 0.3,
        group_id: str = "",
    ):
        self.model = model
        self.api_key = api_key or os.environ.get("MINIMAX_API_KEY", "")
        self.base_url = base_url
        self.temperature = temperature
        self.group_id = group_id or os.environ.get("MINIMAX_GROUP_ID", "")

    def analyze(self, prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from openai import OpenAI
        except ImportError:
            return {"sentiment": "neutral", "confidence": 0.5, "summary": "LLM analysis unavailable"}

        if not self.api_key:
            return {"sentiment": "neutral", "confidence": 0.5, "summary": "MiniMax API key not configured"}

        try:
            client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
            headers = {}
            if self.group_id:
                headers["GroupId"] = self.group_id

            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                response_format={"type": "json_object"},
                extra_headers=headers if headers else None,
            )
            content = response.choices[0].message.content
            import json
            return json.loads(content)
        except Exception:
            return {"sentiment": "neutral", "confidence": 0.5, "summary": "LLM analysis unavailable"}
