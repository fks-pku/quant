"""ZhipuAI GLM LLM adapter."""

import os
from typing import Dict, Any

from quant.features.cio.llm_adapters.base import LLMAdapter


class GLMAdapter(LLMAdapter):
    """ZhipuAI GLM adapter for strategy evaluation."""

    def __init__(
        self,
        model: str = "glm-4-plus",
        api_key: str = "",
        base_url: str = "https://open.bigmodel.cn/api/paas/v4/",
        temperature: float = 0.3,
    ):
        self.model = model
        self.api_key = api_key or os.environ.get("GLM_API_KEY", "")
        self.base_url = base_url
        self.temperature = temperature

    def analyze(self, prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from openai import OpenAI
        except ImportError:
            return {"sentiment": "neutral", "confidence": 0.5, "summary": "LLM analysis unavailable"}

        if not self.api_key:
            return {"sentiment": "neutral", "confidence": 0.5, "summary": "GLM API key not configured"}

        try:
            client = OpenAI(api_key=self.api_key, base_url=self.base_url)
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
